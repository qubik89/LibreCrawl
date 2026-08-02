"""Server-side facts for the technical SEO overview dashboard."""
from collections import Counter
from fnmatch import fnmatch
from urllib.parse import urlparse, urlsplit, urlunsplit

from src import crawl_clickhouse, crawl_db


PRIORITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'opportunity': 3}


def normalise_url(value):
    """Use one stable key for URL-level dashboard deduplication."""
    raw = str(value or '').strip()
    if not raw:
        return ''
    parsed = urlsplit(raw)
    path = parsed.path or '/'
    if path != '/':
        path = path.rstrip('/')
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ''))


def build_dashboard_snapshot(crawl_id, exclusion_patterns=None):
    """Return an evidence-only dashboard projection for one crawl."""
    exclusion_patterns = [pattern for pattern in (exclusion_patterns or []) if pattern]
    aggregate_facts = crawl_clickhouse.get_dashboard_facts(crawl_id, exclusion_patterns)
    if _has_url_evidence(aggregate_facts):
        facts = _facts_from_aggregates(aggregate_facts)
    else:
        clickhouse_rows = _facts_from_clickhouse_rows(crawl_id, exclusion_patterns)
        facts = clickhouse_rows or _facts_from_database(crawl_id, exclusion_patterns)

    crawl = crawl_db.get_crawl_by_id(crawl_id) or {}
    facts['schema_version'] = 'dashboard.v1'
    facts['crawl'] = {
        'id': int(crawl_id),
        'base_url': crawl.get('base_url') or '',
        'status': crawl.get('status') or 'unknown',
        'discovered': _number(crawl.get('urls_discovered')),
        'crawled': _number(crawl.get('urls_crawled')),
        'started_at': crawl.get('started_at'),
        'completed_at': crawl.get('completed_at'),
    }
    facts['enrichments'] = _safe_enrichments(crawl_db.load_crawl_enrichments(crawl_id))
    return facts


def _has_url_evidence(facts):
    coverage = (facts or {}).get('coverage') or {}
    return _number(coverage.get('unique_urls')) > 0


def _facts_from_clickhouse_rows(crawl_id, exclusion_patterns):
    """Recover from an aggregate-query mismatch using the proven row readers.

    The paged readers power the existing tables. Using them here prevents the
    overview from showing an empty audit when a legacy ClickHouse build rejects
    an aggregate expression but can still return crawl rows.
    """
    urls = _load_clickhouse_rows(crawl_clickhouse.load_urls, crawl_id)
    if not urls:
        return None
    links = _load_clickhouse_rows(crawl_clickhouse.load_links, crawl_id)
    issues = _load_clickhouse_rows(crawl_clickhouse.load_issues, crawl_id)
    return _facts_from_rows(
        _latest_by_key(urls, lambda row: normalise_url(row.get('url'))),
        _latest_by_key(links, lambda row: '|'.join((
            normalise_url(row.get('source_url')), normalise_url(row.get('target_url')),
            str(row.get('anchor_text') or ''), str(row.get('placement') or 'body'),
        ))),
        _latest_by_key(issues, lambda row: '|'.join((
            normalise_url(row.get('url')), str(row.get('category') or ''), str(row.get('issue') or ''),
        ))),
        exclusion_patterns,
        source='clickhouse_rows',
    )


def _load_clickhouse_rows(loader, crawl_id, page_size=1000, max_pages=100):
    rows = []
    after = None
    for _ in range(max_pages):
        page = loader(crawl_id, limit=page_size, after=after)
        if not page:
            break
        batch = page.get('rows') or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        next_after = batch[-1].get('_row_order')
        if next_after in (None, after):
            break
        after = next_after
    return rows


def _facts_from_database(crawl_id, exclusion_patterns):
    urls = _latest_by_key(crawl_db.load_crawled_urls(crawl_id) or [], lambda row: normalise_url(row.get('url')))
    links = _latest_by_key(
        crawl_db.load_crawl_links(crawl_id) or [],
        lambda row: '|'.join((
            normalise_url(row.get('source_url')),
            normalise_url(row.get('target_url')),
            str(row.get('anchor_text') or ''),
            str(row.get('placement') or 'body'),
        )),
    )
    issues = _latest_by_key(
        crawl_db.load_crawl_issues(crawl_id) or [],
        lambda row: '|'.join((normalise_url(row.get('url')), str(row.get('category') or ''), str(row.get('issue') or ''))),
    )
    return _facts_from_rows(urls, links, issues, exclusion_patterns, source='sqlite')


def _facts_from_aggregates(facts):
    coverage = dict(facts.get('coverage') or {})
    internal_total = _number(coverage.get('internal_urls'))
    html_total = _number(coverage.get('html_2xx_urls'))
    group_rows = [
        _issue_row(
            str(row.get('category') or 'Other'),
            str(row.get('issue') or row.get('type') or 'Issue'),
            str(row.get('type') or 'info'),
            _number(row.get('count')),
            internal_total,
            html_total,
        )
        for row in (facts.get('issue_groups') or [])
    ]
    group_rows.sort(key=lambda row: (PRIORITY_ORDER[row['priority']], -row['affected'], row['label']))
    severity = Counter()
    categories = Counter()
    for row in group_rows:
        severity[row['type']] += row['affected']
        categories[row['category']] += row['affected']
    return {
        'source': facts.get('source') or 'clickhouse',
        'coverage': coverage,
        'http': facts.get('http') or _http_counts([]),
        'performance': facts.get('performance') or {},
        'links': facts.get('links') or {},
        'depth': facts.get('depth') or [],
        'issues': {
            'total': sum(row['affected'] for row in group_rows),
            'by_severity': [{'type': name, 'count': count} for name, count in sorted(severity.items())],
            'by_category': [{'category': name, 'count': count} for name, count in sorted(categories.items(), key=lambda item: (-item[1], item[0]))],
            'top': group_rows[:8],
            'groups': group_rows,
        },
        'on_page': _on_page_rows(group_rows, html_total),
    }


def _facts_from_rows(urls, links, issues, exclusion_patterns, source):
    urls_by_key = {normalise_url(row.get('url')): row for row in urls if normalise_url(row.get('url'))}
    internal = [row for row in urls if _truthy(row.get('is_internal'))]
    html_2xx = [row for row in internal if _is_html_2xx(row)]
    indexable = [row for row in html_2xx if 'noindex' not in str(row.get('robots') or '').lower()]

    scoped_issues = []
    for issue in issues:
        url_row = urls_by_key.get(normalise_url(issue.get('url')))
        if not url_row or not _truthy(url_row.get('is_internal')):
            continue
        if not _issue_in_scope(issue, url_row) or _is_excluded(issue.get('url'), exclusion_patterns):
            continue
        scoped_issues.append(issue)

    groups = Counter()
    severity = Counter()
    categories = Counter()
    for issue in scoped_issues:
        category = str(issue.get('category') or 'Other')
        name = str(issue.get('issue') or issue.get('type') or 'Issue')
        issue_type = str(issue.get('type') or 'info')
        groups[(category, name, issue_type)] += 1
        severity[issue_type] += 1
        categories[category] += 1

    group_rows = [_issue_row(category, name, issue_type, count, len(internal), len(html_2xx))
                  for (category, name, issue_type), count in groups.items()]
    group_rows.sort(key=lambda row: (PRIORITY_ORDER[row['priority']], -row['affected'], row['label']))

    http = _http_counts(internal)
    response_times = sorted(_float(row.get('response_time')) for row in internal if _float(row.get('response_time')) is not None)
    depths = Counter(str(_number(row.get('depth'))) for row in internal)
    internal_links = [row for row in links if _truthy(row.get('is_internal'))]
    broken_links = [row for row in internal_links if (_number(row.get('target_status')) or 0) >= 400]

    return {
        'source': source,
        'coverage': {
            'internal_urls': len(internal),
            'external_urls': max(0, len(urls) - len(internal)),
            'html_2xx_urls': len(html_2xx),
            'indexable_html_urls': len(indexable),
            'unique_urls': len(urls),
        },
        'http': http,
        'performance': {
            'source': 'crawl_request_duration',
            'p50_ms': _percentile(response_times, 50),
            'p75_ms': _percentile(response_times, 75),
            'p95_ms': _percentile(response_times, 95),
        },
        'links': {
            'unique_edges': len(links),
            'internal_edges': len(internal_links),
            'broken_internal_edges': len(broken_links),
        },
        'depth': [{'depth': int(depth), 'count': count} for depth, count in sorted(depths.items(), key=lambda item: int(item[0]))],
        'issues': {
            'total': len(scoped_issues),
            'by_severity': [{'type': name, 'count': count} for name, count in sorted(severity.items())],
            'by_category': [{'category': name, 'count': count} for name, count in sorted(categories.items(), key=lambda item: (-item[1], item[0]))],
            'top': group_rows[:8],
            'groups': group_rows,
        },
        'on_page': _on_page_rows(group_rows, len(html_2xx)),
    }


def _latest_by_key(rows, key_fn):
    latest = {}
    for row in rows:
        key = key_fn(row)
        if not key:
            continue
        previous = latest.get(key)
        if previous is None or _number(row.get('_row_order') or row.get('id')) >= _number(previous.get('_row_order') or previous.get('id')):
            latest[key] = dict(row)
    return list(latest.values())


def _issue_in_scope(issue, url_row):
    if str(issue.get('category') or '') in {'Technical', 'Performance'}:
        return True
    return _is_html_2xx(url_row)


def _is_excluded(url, patterns):
    path = urlparse(str(url or '')).path
    return any(fnmatch(path, pattern) or path.startswith(pattern.rstrip('*')) for pattern in patterns)


def _is_html_2xx(row):
    status = _number(row.get('status_code'))
    return 200 <= status < 300 and 'html' in str(row.get('content_type') or '').lower()


def _http_counts(rows):
    counts = {'2xx': 0, '3xx': 0, '4xx': 0, '5xx': 0, 'no_response': 0}
    for row in rows:
        status = _number(row.get('status_code'))
        if 200 <= status < 300:
            counts['2xx'] += 1
        elif 300 <= status < 400:
            counts['3xx'] += 1
        elif 400 <= status < 500:
            counts['4xx'] += 1
        elif status >= 500:
            counts['5xx'] += 1
        elif str(row.get('error_type') or '') != 'file_too_large':
            counts['no_response'] += 1
    return counts


def _issue_row(category, issue, issue_type, affected, internal_total, html_total):
    presentation = _issue_presentation(category, issue, issue_type)
    denominator = internal_total if presentation['scope'] == 'internal' else html_total
    return {
        'category': category,
        'issue': issue,
        'type': issue_type,
        'label': presentation['label'],
        'impact': presentation['impact'],
        'priority': presentation['priority'],
        'affected': affected,
        'denominator': denominator,
        'percentage': _percentage(affected, denominator),
    }


def _issue_presentation(category, issue, issue_type):
    text = str(issue or '').lower()
    label = {
        'Missing Title Tag': 'Titulo ausente',
        'Missing Meta Description': 'Meta descripcion ausente',
        'Missing H1 Tag': 'H1 ausente',
        'Missing Canonical URL': 'Canonical ausente',
        'Canonical URL Different': 'Canonical distinta',
        'Thin Content': 'Contenido escaso',
        'Duplicate Content Detected': 'Contenido duplicado',
        'No Structured Data': 'Datos estructurados no detectados',
        'Structured Data Opportunity': 'Oportunidad de datos estructurados',
        'Noindex Directive Present': 'Directiva noindex presente',
        'Noindex Tag Present': 'Directiva noindex presente',
    }.get(issue, issue)
    if any(token in text for token in ('server error', 'dns not found', 'ssl/tls', 'connection refused', 'connection error', 'request timeout', 'no response')):
        return _presentation('critical', 'internal', 'Disponibilidad o acceso al sitio comprometidos', label)
    if 'client error' in text or 'broken image' in text:
        return _presentation('high', 'internal', 'Puede romper navegacion, rastreo o recursos visibles', label)
    if 'noindex' in text or 'missing title' in text or 'missing h1' in text:
        return _presentation('high', 'html', 'Afecta directamente a la indexabilidad o relevancia on-page', label)
    if any(token in text for token in ('redirect', 'canonical', 'meta description', 'thin content', 'duplicate content', 'viewport', 'large page', 'crawl request duration')):
        return _presentation('medium', 'html', 'Reduce calidad tecnica, eficiencia o claridad semantica', label)
    if category in {'Structured Data', 'Social', 'Accessibility'} or any(token in text for token in ('language attribute', 'alt text', 'structured data')):
        return _presentation('opportunity', 'html', 'Mejora elegibilidad, comprension o presentacion del resultado', label)
    fallback = {'error': 'high', 'warning': 'medium', 'info': 'opportunity'}.get(str(issue_type or '').lower(), 'medium')
    return _presentation(fallback, 'html', 'Requiere revision tecnica en las URLs afectadas', label)


def _presentation(priority, scope, impact, label):
    return {'priority': priority, 'scope': scope, 'impact': impact, 'label': label}


def _on_page_rows(groups, denominator):
    mapping = [
        ('title', 'Títulos', {'Missing Title Tag', 'Title Too Long', 'Title Too Short'}),
        ('meta', 'Meta descripciones', {'Missing Meta Description', 'Meta Description Too Long', 'Meta Description Too Short'}),
        ('h1', 'H1', {'Missing H1 Tag'}),
        ('canonical', 'Canonical', {'Missing Canonical URL', 'Canonical URL Different'}),
        ('thin_content', 'Contenido >=300 palabras', {'Thin Content'}),
        ('duplicate_content', 'Contenido duplicado', {'Duplicate Content Detected'}),
        ('structured_data', 'Datos estructurados', {'No Structured Data', 'Structured Data Opportunity'}),
    ]
    rows = []
    for key, label, names in mapping:
        affected = sum(row['affected'] for row in groups if row['issue'] in names)
        rows.append({
            'key': key,
            'label': label,
            'issues': sorted(names),
            'affected': affected,
            'denominator': denominator,
            'healthy': max(0, denominator - affected),
            'coverage': _percentage(max(0, denominator - affected), denominator),
        })
    return rows


def _safe_enrichments(enrichments):
    enrichments = enrichments or {}
    result = {}
    allowed = {
        'gsc': ('period', 'clicks', 'impressions', 'ctr', 'avg_position', 'top_queries', 'top_pages'),
        'ga4': ('period', 'organic_sessions', 'engaged_sessions', 'engagement_rate', 'conversions', 'top_landing_pages'),
        'crux': ('period', 'scope', 'lcp', 'inp', 'cls'),
        'pagespeed': ('pages', 'source'),
        'semantic': ('analyzed_urls', 'topics', 'intent_distribution', 'entities', 'clusters'),
    }
    for source, keys in allowed.items():
        raw = enrichments.get(source)
        if source == 'semantic' and raw is None:
            raw = enrichments.get('semantics')
        if not isinstance(raw, dict):
            result[source] = {'status': 'not_connected', 'data': {}}
            continue
        data = raw.get('data') if isinstance(raw.get('data'), dict) else {}
        result[source] = {
            'status': raw.get('status') or ('available' if data else 'not_connected'),
            'data': {key: data[key] for key in keys if key in data},
        }
    return result


def _number(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value):
    return value is True or value == 1 or str(value).lower() in {'1', 'true', 'yes'}


def _percentage(numerator, denominator):
    if not denominator:
        return None
    return round((float(numerator or 0) / denominator) * 100, 1)


def _percentile(values, percentile):
    if not values:
        return None
    index = max(0, min(len(values) - 1, int((percentile / 100) * len(values) + 0.999) - 1))
    return round(values[index], 1)
