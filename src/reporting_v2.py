"""Evidence-first data contracts for Report Suite 2.0.

The report renderer and the LLM only consume this module's bounded, versioned
snapshot.  It keeps raw crawler records out of prompts and makes every metric
carry a clear denominator.
"""

import copy
import hashlib
import math
from collections import Counter, defaultdict
from urllib.parse import urlsplit, urlunsplit

from src import crawl_clickhouse, crawl_db


AUDIT_FACTS_VERSION = '2.0'
FALLBACK_SCAN_LIMIT = 5000
DEFAULT_SAMPLE_LIMIT = 30
DEFAULT_ISSUE_LIMIT = 60

MODEL_URL_FIELDS = (
    'id', 'url', 'requested_url', 'final_url', 'status_code', 'error_type',
    'content_type', 'size', 'is_internal', 'depth', 'title', 'meta_description',
    'h1', 'h2', 'h3', 'word_count', 'canonical_url', 'lang', 'robots',
    'in_sitemap', 'response_time', 'response_time_ms', 'internal_links',
    'external_links', 'javascript_rendered', 'redirect_chain', 'hreflang',
)
MODEL_LINK_FIELDS = (
    'id', 'source_url', 'source_final_url', 'target_url', 'target_final_url',
    'anchor_text', 'placement', 'is_internal', 'target_status', 'target_domain',
)
MODEL_ISSUE_FIELDS = ('id', 'url', 'final_url', 'category', 'issue', 'type', 'details')


def build_audit_facts(crawl_id, crawl_metadata=None, baseline_crawl_id=None, enrichments=None,
                      sample_limit=DEFAULT_SAMPLE_LIMIT, issue_limit=DEFAULT_ISSUE_LIMIT):
    """Return a JSON-safe ``AuditFactsV2`` snapshot for one completed crawl.

    ClickHouse supplies server-side de-duplicated aggregates when available.
    The SQLite fallback is deliberately capped and declares that limitation so
    an AI writer cannot generalise incomplete evidence.
    """
    metadata = crawl_metadata or crawl_db.get_crawl_by_id(crawl_id) or {}
    sample_limit = _bounded_limit(sample_limit, DEFAULT_SAMPLE_LIMIT, 100)
    issue_limit = _bounded_limit(issue_limit, DEFAULT_ISSUE_LIMIT, 200)
    source_facts = _clickhouse_facts(crawl_id, sample_limit, issue_limit)
    if source_facts is None:
        source_facts = _fallback_facts(crawl_id, sample_limit, issue_limit)

    coverage = dict(source_facts.get('coverage') or {})
    denominators = dict(coverage.get('denominators') or {})
    discovered = _int(metadata.get('urls_discovered'))
    if discovered is not None:
        denominators['discovered_urls'] = discovered
    coverage['denominators'] = denominators
    unique_urls = denominators.get('unique_urls')
    inconsistent_discovery = (
        isinstance(unique_urls, (int, float)) and isinstance(discovered, (int, float))
        and discovered >= 0 and unique_urls > discovered
    )
    coverage['coverage_ratio'] = None if inconsistent_discovery else _ratio(unique_urls, discovered)

    stored_enrichments = _stored_enrichments(crawl_id)
    merged_enrichments = _merge_enrichment_references(stored_enrichments, enrichments)
    comparison = _comparison(crawl_id, baseline_crawl_id, source_facts, sample_limit, issue_limit)
    limitations = list(source_facts.get('limitations') or [])
    if inconsistent_discovery:
        limitations.append(
            'The discovered URL denominator is lower than the final unique URL count; '
            'crawl coverage is not reported until both lifecycle counters are reconciled.'
        )
    if denominators.get('sitemap_urls') is None:
        limitations.append('Sitemap membership and sitemap/crawl overlap were not persisted for this crawl; no sitemap conclusion is available.')
    snapshot = {
        'schema_version': AUDIT_FACTS_VERSION,
        'crawl': {
            'id': crawl_id,
            'base_url': metadata.get('base_url'),
            'base_domain': metadata.get('base_domain'),
            'final_url': metadata.get('final_url'),
            'final_domain': metadata.get('final_domain'),
            'started_at': metadata.get('started_at'),
            'completed_at': metadata.get('completed_at'),
        },
        'coverage': coverage,
        'thematic_metrics': source_facts.get('thematic_metrics') or {},
        'distributions': source_facts.get('distributions') or {},
        'evidence': _evidence_with_ids(source_facts.get('evidence') or {}),
        'comparison': comparison,
        'enrichments': _normalise_enrichments(merged_enrichments),
        'limitations': limitations,
    }
    snapshot['metric_catalog'] = _build_metric_catalog(snapshot)
    return _jsonable(snapshot)


def model_audit_facts(facts):
    """Return a semantically complete, prompt-safe projection of AuditFactsV2.

    The immutable artifact retains every crawler field. Model stages receive
    the same metrics and samples without bulky HTML-adjacent payloads such as
    full image arrays, raw social tags, or complete JSON-LD documents.
    """
    projected = copy.deepcopy(facts or {})
    evidence = projected.get('evidence') or {}
    evidence['urls'] = [_model_url_evidence(row) for row in evidence.get('urls') or []]
    evidence['links'] = [_pick_fields(row, MODEL_LINK_FIELDS) for row in evidence.get('links') or []]
    evidence['issues'] = [_pick_fields(row, MODEL_ISSUE_FIELDS) for row in evidence.get('issues') or []]
    projected['evidence'] = evidence
    return _jsonable(projected)


def _model_url_evidence(row):
    result = _pick_fields(row, MODEL_URL_FIELDS)
    images = row.get('images') if isinstance(row.get('images'), list) else []
    broken_images = row.get('broken_images') if isinstance(row.get('broken_images'), list) else []
    if images or broken_images:
        result['image_summary'] = {
            'total': len(images),
            'missing_alt': sum(not str(item.get('alt') or '').strip() for item in images if isinstance(item, dict)),
            'broken': len(broken_images) + sum(
                bool(item.get('broken')) or (_int(item.get('status_code')) or 0) >= 400
                for item in images if isinstance(item, dict)
            ),
        }
    structured_types = sorted(_structured_data_types(row.get('json_ld'), row.get('schema_org')))
    if structured_types:
        result['structured_data_types'] = structured_types
    result['open_graph_present'] = bool(row.get('og_tags'))
    result['twitter_card_present'] = bool(row.get('twitter_tags'))
    return result


def _pick_fields(row, fields):
    if not isinstance(row, dict):
        return {}
    return {key: copy.deepcopy(row[key]) for key in fields if key in row and row[key] not in (None, '', [], {})}


def _structured_data_types(*values):
    result = set()

    def visit(value):
        if isinstance(value, dict):
            type_value = value.get('@type') or value.get('itemtype') or value.get('type')
            candidates = type_value if isinstance(type_value, list) else [type_value]
            for candidate in candidates:
                if candidate:
                    result.add(str(candidate).rstrip('/').rsplit('/', 1)[-1])
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    for value in values:
        visit(value)
    return result


def normalise_url(url):
    """Normalise URL identity without collapsing meaningful non-root slashes."""
    if not url:
        return ''
    try:
        parsed = urlsplit(str(url).strip())
    except ValueError:
        return str(url).strip()
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or '').lower()
    port = parsed.port
    netloc = hostname
    if port and not ((scheme == 'https' and port == 443) or (scheme == 'http' and port == 80)):
        netloc = f'{hostname}:{port}'
    path = parsed.path or '/'
    return urlunsplit((scheme, netloc, path, parsed.query, ''))


def stratified_samples(rows, limit, kind='url'):
    """Select deterministic, diverse evidence rather than the first rows."""
    rows = list(rows or [])
    if len(rows) <= limit:
        return rows
    buckets = defaultdict(list)
    for row in rows:
        if kind == 'issue':
            key = (row.get('category') or 'Other', row.get('issue') or row.get('type') or 'Issue')
        elif kind == 'link':
            key = (str(row.get('placement') or 'body'), str(row.get('is_internal')))
        else:
            key = (str(row.get('status_code') or 0), _url_bucket(row.get('url')))
        buckets[key].append(row)

    selected = []
    for key in sorted(buckets, key=str):
        selected.append(_stable_pick(buckets[key], kind))
        if len(selected) >= limit:
            return selected
    remaining = [row for row in rows if row not in selected]
    remaining.sort(key=lambda row: _stable_key(row, kind))
    return selected + remaining[:max(0, limit - len(selected))]


def _clickhouse_facts(crawl_id, sample_limit, issue_limit):
    try:
        facts = crawl_clickhouse.get_report_facts(crawl_id, sample_limit, issue_limit)
    except AttributeError:
        facts = None
    except Exception:
        facts = None
    return facts or None


def _fallback_facts(crawl_id, sample_limit, issue_limit):
    urls, urls_source = _load_all_fallback(crawl_clickhouse.load_urls, crawl_db.load_crawled_urls, crawl_id)
    links, links_source = _load_all_fallback(crawl_clickhouse.load_links, crawl_db.load_crawl_links, crawl_id)
    issues, issues_source = _load_all_fallback(crawl_clickhouse.load_issues, crawl_db.load_crawl_issues, crawl_id)
    latest_urls = _latest_by_url(urls)
    unique_links = _unique_links(links)
    unique_issues = _unique_issues(issues)
    sources = {urls_source, links_source, issues_source}
    source = sources.pop() if len(sources) == 1 else 'mixed_fallback'
    return _facts_from_rows(latest_urls, unique_links, unique_issues, sample_limit, issue_limit, source=source)


def _load_all_fallback(clickhouse_loader, sqlite_loader, crawl_id):
    try:
        page = clickhouse_loader(crawl_id, limit=FALLBACK_SCAN_LIMIT, offset=0)
    except Exception:
        page = None
    if page is not None:
        return list(page.get('rows') or [])[:FALLBACK_SCAN_LIMIT], 'clickhouse_rows'
    try:
        return list(sqlite_loader(crawl_id, limit=FALLBACK_SCAN_LIMIT, offset=0) or []), 'sqlite'
    except Exception:
        return [], 'unavailable'


def _facts_from_rows(urls, links, issues, sample_limit, issue_limit, source):
    status_counts = Counter(str(_int(row.get('status_code')) or 0) for row in urls)
    issue_groups = Counter((row.get('category') or 'Other', row.get('issue') or row.get('type') or 'Issue') for row in issues)
    depth_counts = Counter(str(_int(row.get('depth')) or 0) for row in urls)
    response_times = sorted(float(row.get('response_time') or 0) for row in urls if _number(row.get('response_time')) is not None)
    html_2xx = [row for row in urls if _is_html_2xx(row)]
    indexable = [row for row in html_2xx if 'noindex' not in str(row.get('robots') or '').lower()]
    sitemap_flags = [row.get('in_sitemap') if 'in_sitemap' in row else row.get('sitemap') for row in urls]
    sitemap_count = sum(bool(flag) for flag in sitemap_flags) if any(flag is not None for flag in sitemap_flags) else None
    denominators = {
        'unique_urls': len(urls),
        'internal_urls': sum(bool(row.get('is_internal')) for row in urls),
        'html_2xx_urls': len(html_2xx),
        'indexable_html_urls': len(indexable),
        'unique_links': len(links),
        'unique_issues': len(issues),
        'sitemap_urls': sitemap_count,
        'sitemap_crawl_overlap_urls': sitemap_count,
    }
    return {
        'coverage': {'source': source, 'deduplication': 'latest_url_unique_issue_and_link_keys', 'denominators': denominators},
        'thematic_metrics': {
            'http': _http_metrics(urls),
            'content': _content_metrics(html_2xx),
            'performance': _performance_metrics(response_times, source='crawl_request_duration'),
            'links': {'unique_edges': len(links), 'internal_edges': sum(bool(row.get('is_internal')) for row in links)},
            'indexability': {'indexable_html_urls': len(indexable), 'noindex_html_urls': len(html_2xx) - len(indexable)},
        },
        'distributions': {
            'status_codes': [{'label': key, 'count': value} for key, value in sorted(status_counts.items(), key=lambda item: int(item[0]))],
            'issue_groups': _issue_group_rows(issue_groups),
            'depth': [{'label': key, 'count': value} for key, value in sorted(depth_counts.items(), key=lambda item: int(item[0]))],
        },
        'evidence': {
            'urls': stratified_samples(urls, sample_limit, 'url'),
            'links': stratified_samples(links, sample_limit, 'link'),
            'issues': stratified_samples(issues, issue_limit, 'issue'),
        },
        'limitations': [
            'Fallback evidence from %s is capped at %s records per entity; do not extrapolate beyond the covered rows.'
            % (source, FALLBACK_SCAN_LIMIT),
            'Crawl request duration is not a Core Web Vital or a user-field performance metric.',
            'Crawl evidence does not establish Google indexing, traffic, conversion, or revenue.',
        ],
    }


def _latest_by_url(rows):
    latest = {}
    for row in rows:
        # The final response URL is the canonical identity for report
        # denominators; requested_url remains in the evidence row so a root
        # migration can still be explained without losing provenance.
        key = _identity_url(row.get('final_url') or row.get('url') or row.get('requested_url'))
        if not key:
            continue
        if _order_value(row) >= _order_value(latest.get(key) or {}):
            latest[key] = dict(row)
    return list(latest.values())


def _unique_links(rows):
    unique = {}
    for row in rows:
        key = (_identity_url(row.get('source_final_url') or row.get('source_url')),
               _identity_url(row.get('target_final_url') or row.get('target_url')),
               str(row.get('anchor_text') or ''), str(row.get('placement') or 'body'))
        if key[0] and key[1] and _order_value(row) >= _order_value(unique.get(key) or {}):
            unique[key] = dict(row)
    return list(unique.values())


def _unique_issues(rows):
    unique = {}
    for row in rows:
        key = (_identity_url(row.get('final_url') or row.get('url')), str(row.get('category') or ''), str(row.get('issue') or ''))
        if key[0] and (key[1] or key[2]) and _order_value(row) >= _order_value(unique.get(key) or {}):
            unique[key] = dict(row)
    return list(unique.values())


def _identity_url(value):
    return normalise_url(value)


def _http_metrics(rows):
    counts = Counter()
    for row in rows:
        status = _int(row.get('status_code')) or 0
        if 200 <= status < 300:
            counts['2xx'] += 1
        elif 300 <= status < 400:
            counts['3xx'] += 1
        elif 400 <= status < 500:
            counts['4xx'] += 1
        elif status >= 500:
            counts['5xx'] += 1
        else:
            counts['no_response'] += 1
    return dict(counts)


def _content_metrics(rows):
    return {
        'missing_title': sum(not row.get('title') for row in rows),
        'missing_meta_description': sum(not row.get('meta_description') for row in rows),
        'missing_h1': sum(not row.get('h1') for row in rows),
        'thin_content_under_300_words': sum((_int(row.get('word_count')) or 0) < 300 for row in rows),
    }


def _performance_metrics(values, source):
    return {
        'source': source,
        'count': len(values),
        'p50_ms': _percentile(values, 50),
        'p75_ms': _percentile(values, 75),
        'p90_ms': _percentile(values, 90),
        'p95_ms': _percentile(values, 95),
    }


def _issue_group_rows(groups):
    return [
        {'category': category, 'issue': issue, 'count': count}
        for (category, issue), count in sorted(groups.items(), key=lambda item: (-item[1], item[0]))[:20]
    ]


def _comparison(crawl_id, baseline_crawl_id, current, sample_limit=DEFAULT_SAMPLE_LIMIT, issue_limit=DEFAULT_ISSUE_LIMIT):
    if not baseline_crawl_id or str(baseline_crawl_id) == str(crawl_id):
        return {'available': False, 'reason': 'No baseline crawl selected.'}
    baseline = _clickhouse_facts(baseline_crawl_id, 1, 1)
    if not baseline:
        try:
            baseline = _fallback_facts(baseline_crawl_id, sample_limit, issue_limit)
        except Exception:
            baseline = None
    if not baseline:
        return {'available': False, 'baseline_crawl_id': baseline_crawl_id, 'reason': 'Baseline facts are unavailable.'}
    current_denoms = (current.get('coverage') or {}).get('denominators') or {}
    baseline_denoms = (baseline.get('coverage') or {}).get('denominators') or {}
    return {
        'available': True,
        'baseline_crawl_id': baseline_crawl_id,
        'deltas': {
            'unique_urls': _delta(current_denoms.get('unique_urls'), baseline_denoms.get('unique_urls')),
            'unique_issues': _delta(current_denoms.get('unique_issues'), baseline_denoms.get('unique_issues')),
        },
    }


def _normalise_enrichments(value):
    value = value or {}
    result = {}
    for key in ('gsc', 'ga4', 'crux', 'pagespeed'):
        source = value.get(key)
        if isinstance(source, dict):
            clean_data = _redact_enrichment_data(source.get('data') or {})
            result[key] = {
                'status': source.get('status') or ('available' if clean_data else 'not_connected'),
                'data': clean_data,
            }
        else:
            result[key] = {'status': 'not_connected', 'data': {}}
    return result


def _merge_enrichment_references(stored, requested):
    """Apply connection references without manufacturing source availability.

    The request may identify an authorised connection, but it cannot turn a
    source with no persisted metrics into an available source by sending a
    client-controlled ``status`` value.
    """
    merged = {key: dict(item) for key, item in (stored or {}).items() if isinstance(item, dict)}
    for key in ('gsc', 'ga4', 'crux', 'pagespeed'):
        reference = (requested or {}).get(key) if isinstance(requested, dict) else None
        if not isinstance(reference, dict) or not reference.get('connection_id'):
            continue
        if key in merged:
            merged[key] = {**merged[key], 'connection_id': reference['connection_id']}
    return merged


def _redact_enrichment_data(value):
    """Keep stored metrics useful while removing connection material."""
    if not isinstance(value, dict):
        return {}
    secret_markers = ('token', 'secret', 'password', 'credential', 'authorization', 'api_key', 'apikey')
    clean = {}
    for key, item in value.items():
        key_text = str(key).lower()
        if any(marker in key_text for marker in secret_markers):
            continue
        if isinstance(item, dict):
            clean[str(key)] = _redact_enrichment_data(item)
        elif isinstance(item, list):
            clean[str(key)] = [
                _redact_enrichment_data(row) if isinstance(row, dict) else row
                for row in item[:1000]
            ]
        else:
            clean[str(key)] = item
    return clean


def _stored_enrichments(crawl_id):
    try:
        return crawl_db.load_crawl_enrichments(crawl_id) or {}
    except Exception:
        return {}


def _evidence_with_ids(groups):
    result = {}
    for kind, rows in (groups or {}).items():
        result[kind] = []
        for index, row in enumerate(rows or [], start=1):
            item = dict(row or {})
            item.setdefault('id', f'{kind}-{index}')
            result[kind].append(item)
    return result


def _build_metric_catalog(snapshot):
    """Expose stable IDs and exact values for every model-citable metric."""
    coverage = snapshot.get('coverage') or {}
    denominators = coverage.get('denominators') or {}
    entries = []

    def add(metric_id, value, denominator_id=None):
        if isinstance(value, bool) or _number(value) is None:
            return
        item = {'id': metric_id, 'value': value}
        if denominator_id and denominators.get(denominator_id) is not None:
            item['denominator_id'] = f'coverage.denominators.{denominator_id}'
            item['denominator'] = denominators[denominator_id]
        entries.append(item)

    for key, value in sorted(denominators.items()):
        add(f'coverage.denominators.{key}', value)
    add('coverage.coverage_ratio', coverage.get('coverage_ratio'))

    theme_denominators = {
        'http': 'unique_urls', 'content': 'html_2xx_urls',
        'indexability': 'html_2xx_urls', 'links': 'unique_links',
    }
    for theme, metrics in sorted((snapshot.get('thematic_metrics') or {}).items()):
        if not isinstance(metrics, dict):
            continue
        denominator_id = theme_denominators.get(theme)
        for key, value in sorted(metrics.items()):
            add(f'thematic_metrics.{theme}.{key}', value, denominator_id)

    distribution_denominators = {
        'status_codes': 'unique_urls', 'depth': 'unique_urls', 'issue_groups': 'unique_urls',
    }
    for group, rows in sorted((snapshot.get('distributions') or {}).items()):
        for row in rows or []:
            if group == 'issue_groups':
                metric_id = 'issue.%s.%s' % (
                    _metric_slug(row.get('category')), _metric_slug(row.get('issue')),
                )
            else:
                metric_id = f'distributions.{group}.{_metric_slug(row.get("label"))}'
            add(metric_id, row.get('count'), distribution_denominators.get(group))
    return entries


def _url_bucket(url):
    try:
        parts = [part for part in urlsplit(url or '').path.split('/') if part]
    except ValueError:
        parts = []
    return '/' + (parts[0] if parts else '')


def _metric_slug(value):
    result = ''.join(character.lower() if character.isalnum() else '-' for character in str(value or ''))
    return '-'.join(part for part in result.split('-') if part)[:80] or 'unknown'


def _stable_pick(rows, kind):
    return min(rows, key=lambda row: _stable_key(row, kind))


def _stable_key(row, kind):
    value = '|'.join(str(row.get(key) or '') for key in ('url', 'source_url', 'target_url', 'issue', 'category'))
    return hashlib.sha256((kind + '|' + value).encode('utf-8')).hexdigest()


def _is_html_2xx(row):
    status = _int(row.get('status_code')) or 0
    return 200 <= status < 300 and 'html' in str(row.get('content_type') or '').lower()


def _order_value(row):
    return _int((row or {}).get('_row_order')) or _int((row or {}).get('id')) or 0


def _bounded_limit(value, default, maximum):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _percentile(values, percentile):
    if not values:
        return None
    index = max(0, min(len(values) - 1, math.ceil((percentile / 100) * len(values)) - 1))
    return round(values[index], 1)


def _ratio(numerator, denominator):
    if not denominator:
        return None
    return round((float(numerator or 0) / float(denominator)) * 100, 1)


def _delta(current, baseline):
    if current is None or baseline is None:
        return None
    return int(current) - int(baseline)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
