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
    if table == 'crawl_links' and kind in link_kinds:
        conditions.append(link_kinds[kind])
    if table == 'crawl_issues' and issue_type in ('error', 'warning', 'info'):
        conditions.append(f"type = '{issue_type}'")
    return conditions


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
            row['_row_order'] = int(row_order)
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
