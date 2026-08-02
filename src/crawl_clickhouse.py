"""Optional ClickHouse analytics sink for large crawl results."""
import json
import os
import threading
import time


_client = None
_ready = False
_lock = threading.Lock()
_thread_local = threading.local()


def enabled():
    return os.getenv('CLICKHOUSE_ENABLED', '').lower() in ('1', 'true', 'yes')


def _setting(name, default=''):
    return os.getenv(name, default)


def _db_name():
    return _setting('CLICKHOUSE_DATABASE', 'mitmore_seo_crawl')


def _table(name):
    return f'{_db_name()}.{name}'


def _row_json(data):
    return json.dumps(data or {}, default=str, separators=(',', ':'))


def _status_code(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _bool(value):
    return 1 if value else 0


def _text(value):
    if value is None:
        return ''
    return str(value)


def _order(base, index):
    return base + index


def get_client():
    """Return a initialized ClickHouse client, or None if disabled/unavailable."""
    global _client, _ready
    if not enabled():
        return None

    with _lock:
        client = getattr(_thread_local, 'client', None)
        if client and getattr(_thread_local, 'ready', False):
            return client

        try:
            import clickhouse_connect

            client = clickhouse_connect.get_client(
                host=_setting('CLICKHOUSE_HOST', 'clickhouse'),
                port=int(_setting('CLICKHOUSE_PORT', '8123')),
                username=_setting('CLICKHOUSE_USER', 'default'),
                password=_setting('CLICKHOUSE_PASSWORD', ''),
                secure=_setting('CLICKHOUSE_SECURE', '').lower() in ('1', 'true', 'yes'),
                connect_timeout=int(_setting('CLICKHOUSE_CONNECT_TIMEOUT', '5')),
                send_receive_timeout=int(_setting('CLICKHOUSE_TIMEOUT', '30')),
            )
            ensure_schema(client)
            _thread_local.client = client
            _thread_local.ready = True
            _client = client
            _ready = True
            return client
        except Exception as e:
            print(f'ClickHouse unavailable: {e}')
            _thread_local.client = None
            _thread_local.ready = False
            _client = None
            _ready = False
            return None


def ensure_schema(client=None):
    """Create analytics database/tables if needed."""
    client = client or get_client()
    if not client:
        return False

    db = _db_name()
    client.command(f'CREATE DATABASE IF NOT EXISTS {db}')
    client.command(f'''
        CREATE TABLE IF NOT EXISTS {_table('crawl_urls')} (
            crawl_id UInt64,
            row_order UInt64,
            url String,
            status_code UInt16,
            error_type LowCardinality(String),
            content_type LowCardinality(String),
            is_internal UInt8,
            depth UInt16,
            title String,
            meta_description String,
            h1 String,
            word_count UInt32,
            response_time_ms Float64,
            size UInt64,
            javascript_rendered UInt8,
            row_json String,
            inserted_at DateTime64(3) DEFAULT now64(3)
        )
        ENGINE = MergeTree
        ORDER BY (crawl_id, row_order)
    ''')
    client.command(f'''
        CREATE TABLE IF NOT EXISTS {_table('crawl_links')} (
            crawl_id UInt64,
            row_order UInt64,
            source_url String,
            target_url String,
            is_internal UInt8,
            target_status UInt16,
            target_domain String,
            placement LowCardinality(String),
            anchor_text String,
            row_json String,
            inserted_at DateTime64(3) DEFAULT now64(3)
        )
        ENGINE = MergeTree
        ORDER BY (crawl_id, row_order)
    ''')
    client.command(f'''
        CREATE TABLE IF NOT EXISTS {_table('crawl_issues')} (
            crawl_id UInt64,
            row_order UInt64,
            url String,
            type LowCardinality(String),
            category LowCardinality(String),
            issue String,
            details String,
            row_json String,
            inserted_at DateTime64(3) DEFAULT now64(3)
        )
        ENGINE = MergeTree
        ORDER BY (crawl_id, row_order)
    ''')
    return True


def url_rows(crawl_id, urls, base_order=None):
    base_order = base_order or time.time_ns()
    rows = []
    for i, item in enumerate(urls or []):
        rows.append((
            int(crawl_id),
            _order(base_order, i),
            _text(item.get('url')),
            _status_code(item.get('status_code')),
            _text(item.get('error_type')),
            _text(item.get('content_type')),
            _bool(item.get('is_internal')),
            _status_code(item.get('depth')),
            _text(item.get('title')),
            _text(item.get('meta_description')),
            _text(item.get('h1')),
            _status_code(item.get('word_count')),
            float(item.get('response_time') or 0),
            int(item.get('size') or 0),
            _bool(item.get('javascript_rendered')),
            _row_json(item),
        ))
    return rows


def link_rows(crawl_id, links, base_order=None):
    base_order = base_order or time.time_ns()
    rows = []
    for i, item in enumerate(links or []):
        rows.append((
            int(crawl_id),
            _order(base_order, i),
            _text(item.get('source_url')),
            _text(item.get('target_url')),
            _bool(item.get('is_internal')),
            _status_code(item.get('target_status')),
            _text(item.get('target_domain')),
            _text(item.get('placement') or 'body'),
            _text(item.get('anchor_text')),
            _row_json(item),
        ))
    return rows


def issue_rows(crawl_id, issues, base_order=None):
    base_order = base_order or time.time_ns()
    rows = []
    for i, item in enumerate(issues or []):
        rows.append((
            int(crawl_id),
            _order(base_order, i),
            _text(item.get('url')),
            _text(item.get('type')),
            _text(item.get('category')),
            _text(item.get('issue')),
            _text(item.get('details')),
            _row_json(item),
        ))
    return rows


def save_batches(crawl_id, urls=None, links=None, issues=None):
    """Best-effort mirror of crawl data into ClickHouse."""
    client = get_client()
    if not client:
        return False

    try:
        urls = url_rows(crawl_id, urls)
        links = link_rows(crawl_id, links)
        issues = issue_rows(crawl_id, issues)

        if urls:
            client.insert(
                _table('crawl_urls'),
                urls,
                column_names=[
                    'crawl_id', 'row_order', 'url', 'status_code', 'error_type',
                    'content_type', 'is_internal', 'depth', 'title',
                    'meta_description', 'h1', 'word_count', 'response_time_ms',
                    'size', 'javascript_rendered', 'row_json'
                ],
            )
        if links:
            client.insert(
                _table('crawl_links'),
                links,
                column_names=[
                    'crawl_id', 'row_order', 'source_url', 'target_url',
                    'is_internal', 'target_status', 'target_domain', 'placement',
                    'anchor_text', 'row_json'
                ],
            )
        if issues:
            client.insert(
                _table('crawl_issues'),
                issues,
                column_names=[
                    'crawl_id', 'row_order', 'url', 'type', 'category',
                    'issue', 'details', 'row_json'
                ],
            )
        return True
    except Exception as e:
        print(f'ClickHouse save failed for crawl {crawl_id}: {e}')
        return False


def _filter_conditions(table, filters):
    filters = filters or {}
    conditions = []
    kind = filters.get('kind')
    issue_type = filters.get('issue_type')
    scope = filters.get('scope')
    status_family = filters.get('status_family')
    content_type = filters.get('content_type')
    depth = filters.get('depth')
    category = filters.get('category')
    issues = filters.get('issues') or []

    url_kinds = {
        'internal': 'is_internal = 1',
        'external': 'is_internal = 0',
        '2xx': 'status_code >= 200 AND status_code < 300',
        '3xx': 'status_code >= 300 AND status_code < 400',
        '4xx': 'status_code >= 400 AND status_code < 500',
        '5xx': 'status_code >= 500',
        'no_response': "status_code = 0 AND error_type != 'file_too_large'",
        'html': "positionCaseInsensitive(content_type, 'html') > 0",
        'css': "positionCaseInsensitive(content_type, 'css') > 0",
        'js': "positionCaseInsensitive(content_type, 'javascript') > 0",
        'images': "positionCaseInsensitive(content_type, 'image') > 0",
    }
    link_kinds = {
        'internal': 'is_internal = 1',
        'external': 'is_internal = 0',
        '2xx': 'target_status >= 200 AND target_status < 300',
        '3xx': 'target_status >= 300 AND target_status < 400',
        '4xx': 'target_status >= 400 AND target_status < 500',
        '5xx': 'target_status >= 500',
    }
    if table == 'crawl_urls' and kind in url_kinds:
        conditions.append(url_kinds[kind])
    if table == 'crawl_urls' and scope in ('internal', 'external'):
        conditions.append('is_internal = 1' if scope == 'internal' else 'is_internal = 0')
    if table == 'crawl_urls' and status_family in ('2xx', '3xx', '4xx', '5xx', 'no_response'):
        conditions.append(url_kinds[status_family])
    if table == 'crawl_urls' and content_type in ('html', 'css', 'js', 'images'):
        conditions.append(url_kinds[content_type])
    if table == 'crawl_urls' and depth is not None:
        try:
            conditions.append(f'depth = {max(0, min(int(depth), 1000))}')
        except (TypeError, ValueError):
            pass
    if table == 'crawl_links' and kind in link_kinds:
        conditions.append(link_kinds[kind])
    if table == 'crawl_links' and scope in ('internal', 'external'):
        conditions.append('is_internal = 1' if scope == 'internal' else 'is_internal = 0')
    if table == 'crawl_links' and status_family in ('2xx', '3xx', '4xx', '5xx'):
        conditions.append(link_kinds[status_family])
    if table == 'crawl_issues' and issue_type in ('error', 'warning', 'info'):
        conditions.append(f"type = '{issue_type}'")
    if table == 'crawl_issues' and _safe_filter_text(category):
        conditions.append(f"category = {_sql_text_literal(category)}")
    if table == 'crawl_issues':
        safe_issues = [item for item in issues if _safe_filter_text(item)]
        if safe_issues:
            conditions.append('issue IN (' + ', '.join(_sql_text_literal(item) for item in safe_issues[:20]) + ')')
    return conditions


def _safe_filter_text(value):
    value = str(value or '')
    return bool(value) and len(value) <= 200 and all(ord(char) >= 32 for char in value)


def _sql_text_literal(value):
    return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'") + "'"


def _query_json_rows(table, crawl_id, limit=500, offset=0, after=None, filters=None, descending=False):
    client = get_client()
    if not client:
        return None

    limit = max(0, min(int(limit), 5000))
    offset = max(0, int(offset))
    crawl_id = int(crawl_id)
    filter_conditions = _filter_conditions(table, filters)
    base_conditions = [f'crawl_id = {crawl_id}'] + filter_conditions
    where = ' AND '.join(base_conditions)
    try:
        total = client.query(
            f'SELECT count() FROM {_table(table)} WHERE {where}'
        ).result_rows[0][0]
        if total == 0:
            if filter_conditions or after is not None:
                return {'total': 0, 'rows': []}
            return None
        page_conditions = list(base_conditions)
        if after is not None:
            page_conditions.append(f'row_order > {int(after)}')
        page_where = ' AND '.join(page_conditions)
        order = 'DESC' if descending else 'ASC'
        offset_clause = '' if after is not None else f' OFFSET {offset}'
        result = client.query(f'''
            SELECT row_order, row_json
            FROM {_table(table)}
            WHERE {page_where}
            ORDER BY row_order {order}
            LIMIT {limit}{offset_clause}
        ''')
        rows = []
        for row_order, row_json in result.result_rows:
            row = json.loads(row_json)
            # UInt64 row orders are commonly larger than JavaScript's safe
            # integer range. Keep the cursor exact across the JSON boundary.
            row['_row_order'] = str(int(row_order))
            rows.append(row)
        return {
            'total': int(total),
            'rows': rows,
        }
    except Exception as e:
        print(f'ClickHouse page read failed for crawl {crawl_id}: {e}')
        return None


def load_urls(crawl_id, limit=500, offset=0, after=None, filters=None):
    return _query_json_rows('crawl_urls', crawl_id, limit, offset, after, filters)


def load_links(crawl_id, limit=500, offset=0, after=None, filters=None):
    return _query_json_rows('crawl_links', crawl_id, limit, offset, after, filters)


def load_issues(crawl_id, limit=500, offset=0, after=None, filters=None):
    return _query_json_rows('crawl_issues', crawl_id, limit, offset, after, filters)


def load_recent_urls(crawl_id, limit=20):
    return _query_json_rows('crawl_urls', crawl_id, limit=limit, descending=True)


def load_recent_issues(crawl_id, limit=20):
    return _query_json_rows('crawl_issues', crawl_id, limit=limit, descending=True)


def get_summary(crawl_id):
    client = get_client()
    if not client:
        return None

    crawl_id = int(crawl_id)
    try:
        url_counts = client.query(f'''
            SELECT
                count() AS total,
                countIf(is_internal = 1) AS internal,
                countIf(is_internal = 0) AS external,
                countIf(status_code >= 200 AND status_code < 300) AS status_2xx,
                countIf(status_code >= 300 AND status_code < 400) AS status_3xx,
                countIf(status_code >= 400 AND status_code < 500) AS status_4xx,
                countIf(status_code >= 500) AS status_5xx,
                countIf(status_code = 0 AND error_type != 'file_too_large') AS no_response,
                countIf(positionCaseInsensitive(content_type, 'html') > 0) AS html,
                countIf(positionCaseInsensitive(content_type, 'css') > 0) AS css,
                countIf(positionCaseInsensitive(content_type, 'javascript') > 0) AS js,
                countIf(positionCaseInsensitive(content_type, 'image') > 0) AS images
            FROM {_table('crawl_urls')}
            WHERE crawl_id = {crawl_id}
        ''').result_rows[0]

        status_rows = client.query(f'''
            SELECT status_code, error_type, count()
            FROM {_table('crawl_urls')}
            WHERE crawl_id = {crawl_id}
            GROUP BY status_code, error_type
            ORDER BY status_code, error_type
        ''').result_rows

        issue_rows_result = client.query(f'''
            SELECT type, count()
            FROM {_table('crawl_issues')}
            WHERE crawl_id = {crawl_id}
            GROUP BY type
        ''').result_rows
        link_count = client.query(
            f'SELECT count() FROM {_table("crawl_links")} WHERE crawl_id = {crawl_id}'
        ).result_rows[0][0]
        issue_count = client.query(
            f'SELECT count() FROM {_table("crawl_issues")} WHERE crawl_id = {crawl_id}'
        ).result_rows[0][0]

        return {
            'source': 'clickhouse',
            'counts': {
                'urls': int(url_counts[0]),
                'links': int(link_count),
                'issues': int(issue_count),
                'internal': int(url_counts[1]),
                'external': int(url_counts[2]),
                '2xx': int(url_counts[3]),
                '3xx': int(url_counts[4]),
                '4xx': int(url_counts[5]),
                '5xx': int(url_counts[6]),
                'no_response': int(url_counts[7]),
                'html': int(url_counts[8]),
                'css': int(url_counts[9]),
                'js': int(url_counts[10]),
                'images': int(url_counts[11]),
            },
            'status_counts': [
                {'status_code': int(code), 'error_type': error_type, 'count': int(count)}
                for code, error_type, count in status_rows
            ],
            'issue_type_counts': {str(kind or 'unknown'): int(count) for kind, count in issue_rows_result},
        }
    except Exception as e:
        print(f'ClickHouse summary failed for crawl {crawl_id}: {e}')
        return None


def get_dashboard_facts(crawl_id, exclusion_patterns=None):
    """Return compact, de-duplicated aggregates for the live overview."""
    client = get_client()
    if not client:
        return None

    crawl_id = int(crawl_id)
    urls_table = _table('crawl_urls')
    links_table = _table('crawl_links')
    issues_table = _table('crawl_issues')
    latest_urls = f'''
        SELECT
            coalesce(nullIf(JSONExtractString(row_json, 'final_url'), ''), url) AS url,
            argMax(status_code, row_order) AS status_code,
            argMax(error_type, row_order) AS error_type,
            argMax(content_type, row_order) AS content_type,
            argMax(is_internal, row_order) AS is_internal,
            argMax(depth, row_order) AS depth,
            argMax(response_time_ms, row_order) AS response_time_ms,
            argMax(row_json, row_order) AS row_json
        FROM {urls_table}
        WHERE crawl_id = {crawl_id}
        GROUP BY coalesce(nullIf(JSONExtractString(row_json, 'final_url'), ''), url)
    '''
    latest_issues = f'''
        SELECT
            coalesce(nullIf(JSONExtractString(row_json, 'final_url'), ''), url) AS url, category, issue,
            argMax(type, row_order) AS type
        FROM {issues_table}
        WHERE crawl_id = {crawl_id}
        GROUP BY coalesce(nullIf(JSONExtractString(row_json, 'final_url'), ''), url), category, issue
    '''
    latest_links = f'''
        SELECT
            coalesce(nullIf(JSONExtractString(row_json, 'source_final_url'), ''), source_url) AS source_url,
            coalesce(nullIf(JSONExtractString(row_json, 'target_final_url'), ''), target_url) AS target_url,
            anchor_text, placement,
            argMax(is_internal, row_order) AS is_internal,
            argMax(target_status, row_order) AS target_status
        FROM {links_table}
        WHERE crawl_id = {crawl_id}
        GROUP BY
            coalesce(nullIf(JSONExtractString(row_json, 'source_final_url'), ''), source_url),
            coalesce(nullIf(JSONExtractString(row_json, 'target_final_url'), ''), target_url),
            anchor_text, placement
    '''
    issue_exclusions = _dashboard_exclusion_conditions(exclusion_patterns or [])
    try:
        url_metrics = client.query(f'''
            WITH latest_urls AS ({latest_urls})
            SELECT
                countIf(is_internal = 1),
                countIf(is_internal = 0),
                countIf(is_internal = 1 AND status_code >= 200 AND status_code < 300
                    AND positionCaseInsensitive(content_type, 'html') > 0),
                countIf(is_internal = 1 AND status_code >= 200 AND status_code < 300
                    AND positionCaseInsensitive(content_type, 'html') > 0
                    AND positionCaseInsensitive(JSONExtractString(row_json, 'robots'), 'noindex') = 0),
                countIf(is_internal = 1 AND status_code >= 200 AND status_code < 300),
                countIf(is_internal = 1 AND status_code >= 300 AND status_code < 400),
                countIf(is_internal = 1 AND status_code >= 400 AND status_code < 500),
                countIf(is_internal = 1 AND status_code >= 500),
                countIf(is_internal = 1 AND status_code = 0 AND error_type != 'file_too_large'),
                count(),
                quantileExactIf(0.5)(response_time_ms, is_internal = 1),
                quantileExactIf(0.75)(response_time_ms, is_internal = 1),
                quantileExactIf(0.95)(response_time_ms, is_internal = 1)
            FROM latest_urls
        ''').result_rows[0]
        depth_rows = client.query(f'''
            WITH latest_urls AS ({latest_urls})
            SELECT depth, count()
            FROM latest_urls
            WHERE is_internal = 1
            GROUP BY depth
            ORDER BY depth
        ''').result_rows
        issue_rows = client.query(f'''
            WITH latest_urls AS ({latest_urls}), latest_issues AS ({latest_issues})
            SELECT i.category, i.issue, i.type, count()
            FROM latest_issues AS i
            INNER JOIN latest_urls AS u ON i.url = u.url
            WHERE u.is_internal = 1
              AND (i.category IN ('Technical', 'Performance')
                   OR (u.status_code >= 200 AND u.status_code < 300
                       AND positionCaseInsensitive(u.content_type, 'html') > 0))
              {issue_exclusions}
            GROUP BY i.category, i.issue, i.type
        ''').result_rows
        link_metrics = client.query(f'''
            WITH latest_links AS ({latest_links})
            SELECT
                count(),
                countIf(is_internal = 1),
                countIf(is_internal = 1 AND target_status >= 400)
            FROM latest_links
        ''').result_rows[0]
    except Exception as e:
        print(f'ClickHouse dashboard facts failed for crawl {crawl_id}: {e}')
        return None

    return {
        'source': 'clickhouse',
        'coverage': {
            'internal_urls': int(url_metrics[0]),
            'external_urls': int(url_metrics[1]),
            'html_2xx_urls': int(url_metrics[2]),
            'indexable_html_urls': int(url_metrics[3]),
            'unique_urls': int(url_metrics[9]),
        },
        'http': {
            '2xx': int(url_metrics[4]), '3xx': int(url_metrics[5]),
            '4xx': int(url_metrics[6]), '5xx': int(url_metrics[7]),
            'no_response': int(url_metrics[8]),
        },
        'performance': {
            'source': 'crawl_request_duration',
            'p50_ms': _round_metric(url_metrics[10]),
            'p75_ms': _round_metric(url_metrics[11]),
            'p95_ms': _round_metric(url_metrics[12]),
        },
        'links': {
            'unique_edges': int(link_metrics[0]),
            'internal_edges': int(link_metrics[1]),
            'broken_internal_edges': int(link_metrics[2]),
        },
        'depth': [{'depth': int(depth), 'count': int(count)} for depth, count in depth_rows],
        'issue_groups': [
            {'category': category or 'Other', 'issue': issue or type or 'Issue', 'type': issue_type or 'info', 'count': int(count)}
            for category, issue, issue_type, count in issue_rows
        ],
    }


def _dashboard_exclusion_conditions(patterns):
    values = []
    for pattern in patterns[:30]:
        pattern = str(pattern or '').strip()
        if not pattern or len(pattern) > 300:
            continue
        escaped = pattern.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        values.append(escaped.replace('*', '%').replace('?', '_').replace("'", "\\'"))
    if not values:
        return ''
    clauses = [f"path(i.url) NOT LIKE '{value}' ESCAPE '\\\\'" for value in values]
    return 'AND ' + ' AND '.join(clauses)


def get_report_facts(crawl_id, sample_limit=30, issue_limit=60):
    """Return Report Suite 2.0 aggregates without loading a crawl into Python.

    Crawl rows can contain retries.  Every query therefore starts from the most
    recent ``row_order`` for each URL (or logical issue/link key).
    """
    client = get_client()
    if not client:
        return None

    crawl_id = int(crawl_id)
    sample_limit = max(1, min(int(sample_limit), 100))
    issue_limit = max(1, min(int(issue_limit), 200))
    urls_table = _table('crawl_urls')
    links_table = _table('crawl_links')
    issues_table = _table('crawl_issues')
    latest_urls = f'''
        SELECT
            coalesce(nullIf(JSONExtractString(row_json, 'final_url'), ''), url) AS url,
            argMax(status_code, row_order) AS status_code,
            argMax(error_type, row_order) AS error_type,
            argMax(content_type, row_order) AS content_type,
            argMax(is_internal, row_order) AS is_internal,
            argMax(depth, row_order) AS depth,
            argMax(title, row_order) AS title,
            argMax(meta_description, row_order) AS meta_description,
            argMax(h1, row_order) AS h1,
            argMax(word_count, row_order) AS word_count,
            argMax(response_time_ms, row_order) AS response_time_ms,
            argMax(row_json, row_order) AS row_json
        FROM {urls_table}
        WHERE crawl_id = {crawl_id}
        GROUP BY coalesce(nullIf(JSONExtractString(row_json, 'final_url'), ''), url)
    '''
    latest_issues = f'''
        SELECT
            coalesce(nullIf(JSONExtractString(row_json, 'final_url'), ''), url) AS url, category, issue,
            argMax(type, row_order) AS type,
            argMax(details, row_order) AS details,
            argMax(row_json, row_order) AS row_json
        FROM {issues_table}
        WHERE crawl_id = {crawl_id}
        GROUP BY coalesce(nullIf(JSONExtractString(row_json, 'final_url'), ''), url), category, issue
    '''
    latest_links = f'''
        SELECT
            coalesce(nullIf(JSONExtractString(row_json, 'source_final_url'), ''), source_url) AS source_url,
            coalesce(nullIf(JSONExtractString(row_json, 'target_final_url'), ''), target_url) AS target_url,
            anchor_text, placement,
            argMax(is_internal, row_order) AS is_internal,
            argMax(target_status, row_order) AS target_status,
            argMax(target_domain, row_order) AS target_domain,
            argMax(row_json, row_order) AS row_json
        FROM {links_table}
        WHERE crawl_id = {crawl_id}
        GROUP BY
            coalesce(nullIf(JSONExtractString(row_json, 'source_final_url'), ''), source_url),
            coalesce(nullIf(JSONExtractString(row_json, 'target_final_url'), ''), target_url),
            anchor_text, placement
    '''
    try:
        url_metrics = client.query(f'''
            WITH latest_urls AS ({latest_urls})
            SELECT
                count(),
                countIf(is_internal = 1),
                countIf(status_code >= 200 AND status_code < 300
                    AND positionCaseInsensitive(content_type, 'html') > 0),
                countIf(status_code >= 200 AND status_code < 300),
                countIf(status_code >= 300 AND status_code < 400),
                countIf(status_code >= 400 AND status_code < 500),
                countIf(status_code >= 500),
                countIf(status_code = 0),
                countIf(positionCaseInsensitive(content_type, 'html') > 0),
                countIf(title = ''),
                countIf(meta_description = ''),
                countIf(h1 = ''),
                countIf(word_count < 300),
                quantileExact(0.5)(response_time_ms),
                quantileExact(0.75)(response_time_ms),
                quantileExact(0.9)(response_time_ms),
                quantileExact(0.95)(response_time_ms)
            FROM latest_urls
        ''').result_rows[0]
        status_rows = client.query(f'''
            WITH latest_urls AS ({latest_urls})
            SELECT status_code, error_type, count()
            FROM latest_urls
            GROUP BY status_code, error_type
            ORDER BY status_code, error_type
        ''').result_rows
        depth_rows = client.query(f'''
            WITH latest_urls AS ({latest_urls})
            SELECT depth, count()
            FROM latest_urls
            GROUP BY depth
            ORDER BY depth
        ''').result_rows
        issue_rows_result = client.query(f'''
            WITH latest_issues AS ({latest_issues})
            SELECT category, issue, type, count()
            FROM latest_issues
            GROUP BY category, issue, type
            ORDER BY count() DESC, category, issue
            LIMIT 20
        ''').result_rows
        issue_total = client.query(f'''
            WITH latest_issues AS ({latest_issues})
            SELECT count() FROM latest_issues
        ''').result_rows[0][0]
        link_metrics = client.query(f'''
            WITH latest_links AS ({latest_links})
            SELECT count(), countIf(is_internal = 1)
            FROM latest_links
        ''').result_rows[0]
        url_samples = _report_rows(client.query(f'''
            WITH latest_urls AS ({latest_urls})
            SELECT row_json FROM latest_urls
            ORDER BY cityHash64(concat(url, toString(status_code)))
            LIMIT {sample_limit}
        ''').result_rows)
        link_samples = _report_rows(client.query(f'''
            WITH latest_links AS ({latest_links})
            SELECT row_json FROM latest_links
            ORDER BY cityHash64(concat(source_url, target_url, anchor_text, placement))
            LIMIT {sample_limit}
        ''').result_rows)
        issue_samples = _report_rows(client.query(f'''
            WITH latest_issues AS ({latest_issues})
            SELECT row_json FROM latest_issues
            ORDER BY cityHash64(concat(url, category, issue))
            LIMIT {issue_limit}
        ''').result_rows)
    except Exception as e:
        print(f'ClickHouse report facts failed for crawl {crawl_id}: {e}')
        return None

    html_2xx = int(url_metrics[2])
    noindex = _report_noindex_count(client, latest_urls)
    sitemap_urls = _report_sitemap_count(client, latest_urls)
    return {
        'coverage': {
            'source': 'clickhouse',
            'deduplication': 'argMax(row_order) per URL, issue, and link key',
            'denominators': {
                'unique_urls': int(url_metrics[0]),
                'internal_urls': int(url_metrics[1]),
                'html_2xx_urls': html_2xx,
                'indexable_html_urls': max(0, html_2xx - noindex) if noindex is not None else None,
                'unique_links': int(link_metrics[0]),
                'unique_issues': int(issue_total),
                'sitemap_urls': sitemap_urls,
                'sitemap_crawl_overlap_urls': sitemap_urls,
            },
        },
        'thematic_metrics': {
            'http': {
                '2xx': int(url_metrics[3]), '3xx': int(url_metrics[4]),
                '4xx': int(url_metrics[5]), '5xx': int(url_metrics[6]),
                'no_response': int(url_metrics[7]),
            },
            'content': {
                'missing_title': int(url_metrics[9]),
                'missing_meta_description': int(url_metrics[10]),
                'missing_h1': int(url_metrics[11]),
                'thin_content_under_300_words': int(url_metrics[12]),
            },
            'performance': {
                'source': 'crawl_request_duration', 'count': int(url_metrics[0]),
                'p50_ms': _round_metric(url_metrics[13]), 'p75_ms': _round_metric(url_metrics[14]),
                'p90_ms': _round_metric(url_metrics[15]), 'p95_ms': _round_metric(url_metrics[16]),
            },
            'links': {'unique_edges': int(link_metrics[0]), 'internal_edges': int(link_metrics[1])},
            'indexability': {
                'indexable_html_urls': max(0, html_2xx - noindex) if noindex is not None else None,
                'noindex_html_urls': noindex,
            },
        },
        'distributions': {
            'status_codes': [
                {'label': str(code), 'error_type': error_type or None, 'count': int(count)}
                for code, error_type, count in status_rows
            ],
            'issue_groups': [
                {'category': category or 'Other', 'issue': issue or type or 'Issue', 'type': type or 'info', 'count': int(count)}
                for category, issue, type, count in issue_rows_result
            ],
            'depth': [{'label': str(depth), 'count': int(count)} for depth, count in depth_rows],
        },
        'evidence': {'urls': url_samples, 'links': link_samples, 'issues': issue_samples},
        'limitations': [
            'Rows are de-duplicated with the most recent stored crawler record for each logical key.',
            'Crawl request duration is not a Core Web Vital or user-field performance metric.',
            'Crawl evidence does not establish Google indexing, traffic, conversion, or revenue.',
        ],
    }


def _report_noindex_count(client, latest_urls):
    """Best-effort robots calculation. Older ClickHouse builds may lack JSONExtract."""
    try:
        return int(client.query(f'''
            WITH latest_urls AS ({latest_urls})
            SELECT countIf(positionCaseInsensitive(JSONExtractString(row_json, 'robots'), 'noindex') > 0)
            FROM latest_urls
        ''').result_rows[0][0])
    except Exception:
        return None


def _report_sitemap_count(client, latest_urls):
    """Return persisted sitemap membership without treating missing fields as zero."""
    try:
        row = client.query(f'''
            WITH latest_urls AS ({latest_urls})
            SELECT
                countIf(JSONHas(row_json, 'in_sitemap')),
                countIf(JSONExtractBool(row_json, 'in_sitemap'))
            FROM latest_urls
        ''').result_rows[0]
        return int(row[1]) if int(row[0]) else None
    except Exception:
        return None


def _report_rows(result_rows):
    rows = []
    for row in result_rows or []:
        try:
            rows.append(json.loads(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return rows


def _round_metric(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None
