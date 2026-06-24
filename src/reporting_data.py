"""Bounded crawl evidence packets for AI report generation."""

import math

from src import crawl_clickhouse, crawl_db

MAX_SAMPLE_LIMIT = 25
MAX_ISSUE_LIMIT = 50
MIN_SAMPLE_LIMIT = 1
TOP_LIMIT = 10


def build_audit_packet(crawl_id, sample_limit=MAX_SAMPLE_LIMIT, issue_limit=MAX_ISSUE_LIMIT):
    """Build a compact, JSON-serializable audit packet for one crawl."""
    from main import build_crawl_summary

    sample_limit = _bounded_limit(sample_limit, MAX_SAMPLE_LIMIT)
    issue_limit = _bounded_limit(issue_limit, MAX_ISSUE_LIMIT)

    crawl_summary = _compact_crawl_summary(build_crawl_summary(crawl_id))
    crawl_metadata = _compact_crawl_metadata(crawl_db.get_crawl_by_id(crawl_id))
    counts = crawl_db.get_crawl_counts(crawl_id)
    analytics_summary = _clickhouse_summary(crawl_id)

    top_status_issues = _top_status_issues(crawl_id, analytics_summary)
    top_issue_groups = _top_issue_groups(crawl_id, analytics_summary)

    samples = {
        'urls': _load_sample(
            crawl_id,
            sample_limit,
            crawl_clickhouse.load_urls,
            crawl_db.load_crawled_urls,
            counts.get('urls', 0),
            _compact_url_row,
        ),
        'links': _load_sample(
            crawl_id,
            sample_limit,
            crawl_clickhouse.load_links,
            crawl_db.load_crawl_links,
            counts.get('links', 0),
            _compact_link_row,
        ),
        'issues': _load_sample(
            crawl_id,
            issue_limit,
            crawl_clickhouse.load_issues,
            crawl_db.load_crawl_issues,
            counts.get('issues', 0),
            _compact_issue_row,
        ),
    }

    return _jsonable({
        'crawl_id': crawl_id,
        'crawl_summary': crawl_summary,
        'crawl_metadata': crawl_metadata,
        'analytics_summary': analytics_summary,
        'top_status_issues': top_status_issues,
        'top_issue_groups': top_issue_groups,
        'samples': samples,
        'limits': {
            'sample_limit': sample_limit,
            'issue_limit': issue_limit,
            'top_limit': TOP_LIMIT,
        },
    })


def _bounded_limit(value, maximum):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = maximum
    return max(MIN_SAMPLE_LIMIT, min(value, maximum))


def _compact_crawl_summary(summary):
    if not summary:
        return None
    summary = _row_dict(summary)
    compact = _pick(summary, [
        'success', 'crawl_id', 'status', 'base_url', 'base_domain', 'is_active',
        'is_running', 'is_paused', 'is_stopped', 'can_resume', 'started_at',
        'completed_at', 'last_saved_at', 'elapsed_time', 'speed', 'progress',
        'depth', 'urls_discovered', 'urls_crawled', 'max_depth_reached',
        'counts', 'analytics', 'memory', 'memory_data', 'demo_stopped',
    ])
    if 'crawl' in summary:
        compact['crawl'] = _compact_crawl_metadata(summary.get('crawl'))
    return compact


def _compact_crawl_metadata(crawl):
    if not crawl:
        return None
    return _pick(crawl, [
        'id', 'user_id', 'session_id', 'base_url', 'base_domain', 'status',
        'urls_discovered', 'urls_crawled', 'max_depth_reached', 'started_at',
        'completed_at', 'last_saved_at', 'peak_memory_mb', 'estimated_size_mb',
        'can_resume',
    ])


def _clickhouse_summary(crawl_id):
    try:
        return crawl_clickhouse.get_summary(crawl_id)
    except Exception:
        return None


def _load_sample(crawl_id, limit, clickhouse_loader, sqlite_loader, sqlite_total, compact):
    try:
        page = clickhouse_loader(crawl_id, limit=limit, offset=0)
    except Exception:
        page = None

    if page is not None:
        rows = page.get('rows', [])
        return {
            'source': 'clickhouse',
            'total': page.get('total'),
            'rows': [compact(row) for row in rows[:limit]],
        }

    rows = sqlite_loader(crawl_id, limit=limit, offset=0)
    return {
        'source': 'sqlite',
        'total': sqlite_total,
        'rows': [compact(row) for row in (rows or [])[:limit]],
    }


def _top_status_issues(crawl_id, analytics_summary):
    status_counts = (analytics_summary or {}).get('status_counts')
    if status_counts:
        rows = [row for row in status_counts if _is_problem_status(row)]
        return sorted(
            (_status_row(row) for row in rows),
            key=lambda row: row.get('count') or 0,
            reverse=True,
        )[:TOP_LIMIT]
    return _sqlite_top_status_issues(crawl_id)


def _top_issue_groups(crawl_id, analytics_summary):
    issue_counts = (analytics_summary or {}).get('issue_type_counts')
    if issue_counts:
        rows = [
            {'type': kind, 'count': count}
            for kind, count in issue_counts.items()
        ]
        return sorted(rows, key=lambda row: row.get('count') or 0, reverse=True)[:TOP_LIMIT]
    return _sqlite_top_issue_groups(crawl_id)


def _sqlite_top_status_issues(crawl_id):
    query = '''
        SELECT status_code, error_type, COUNT(*) AS count
        FROM crawled_urls
        WHERE crawl_id = ?
          AND (status_code >= 400 OR status_code = 0 OR error_type IS NOT NULL)
        GROUP BY status_code, error_type
        ORDER BY count DESC
        LIMIT ?
    '''
    return _sqlite_group_query(query, (crawl_id, TOP_LIMIT), _status_row)


def _sqlite_top_issue_groups(crawl_id):
    query = '''
        SELECT type, category, issue, COUNT(*) AS count
        FROM crawl_issues
        WHERE crawl_id = ?
        GROUP BY type, category, issue
        ORDER BY count DESC
        LIMIT ?
    '''
    return _sqlite_group_query(query, (crawl_id, TOP_LIMIT), _row_dict)


def _sqlite_group_query(query, params, mapper):
    try:
        with crawl_db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [mapper(row) for row in cursor.fetchall()]
    except Exception:
        return []


def _is_problem_status(row):
    row = _row_dict(row)
    status_code = _to_int(row.get('status_code'))
    return status_code == 0 or status_code >= 400 or bool(row.get('error_type'))


def _status_row(row):
    row = _row_dict(row)
    return {
        'status_code': _to_int(row.get('status_code')),
        'error_type': row.get('error_type'),
        'count': _to_int(row.get('count')),
    }


def _compact_url_row(row):
    return _pick(row, [
        'url', 'status_code', 'error_type', 'content_type', 'is_internal', 'depth',
        'title', 'meta_description', 'h1', 'word_count', 'canonical_url', 'lang',
        'robots', 'internal_links', 'external_links', 'response_time',
        'javascript_rendered',
    ])


def _compact_link_row(row):
    return _pick(row, [
        'source_url', 'target_url', 'anchor_text', 'is_internal', 'target_domain',
        'target_status', 'placement',
    ])


def _compact_issue_row(row):
    return _pick(row, ['url', 'type', 'category', 'issue', 'details'])


def _pick(row, keys):
    row = _row_dict(row)
    return {key: row[key] for key in keys if key in row and row[key] is not None}


def _row_dict(row):
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
