"""HTML and PDF rendering helpers for generated reports."""

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright
from markupsafe import escape


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORTS_BASE_DIR = PROJECT_ROOT / 'data' / 'reports'
TEMPLATE_DIR = PROJECT_ROOT / 'web' / 'templates'
DEFAULT_PRIMARY_COLOR = '#2563eb'
HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$')
REPORT_VARIANT_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,40}$')
STATUS_GROUPS = (
    ('Correctas', lambda status: 200 <= status < 300),
    ('Redirecciones', lambda status: 300 <= status < 400),
    ('Errores', lambda status: status == 0 or status >= 400),
)


def build_report_view_model(audit_packet):
    """Return deterministic report display data derived from crawl evidence."""
    packet = audit_packet or {}
    metadata = packet.get('crawl_metadata') or {}
    analytics = packet.get('analytics_summary') or {}
    counts = _first_dict((packet.get('crawl_summary') or {}).get('counts'), analytics.get('counts'))
    samples = packet.get('samples') or {}
    return {
        'domain': metadata.get('base_domain') or metadata.get('base_url') or 'Sitio analizado',
        'base_url': metadata.get('base_url'),
        'crawl_id': metadata.get('id') or packet.get('crawl_id'),
        'stats': _report_stats(counts),
        'status_distribution': _status_distribution(analytics.get('status_counts')),
        'issue_distribution': _issue_distribution(analytics.get('issue_type_counts')),
        'priority_rows': _priority_rows(packet.get('top_status_issues'), packet.get('top_issue_groups')),
        'sample_issues': (samples.get('issues') or {}).get('rows') or [],
    }


def report_output_dir(crawl_id, base_dir=None):
    """Return the rooted report directory for one crawl."""
    base_path = Path(base_dir or DEFAULT_REPORTS_BASE_DIR).resolve()
    output_dir = base_path / str(_safe_int(crawl_id, 'crawl_id'))
    _ensure_under_base(output_dir, base_path)
    return output_dir


def report_output_paths(crawl_id, report_id=None, base_dir=None, language=None, tone=None):
    """Return standard markdown, HTML, and PDF paths for one report."""
    output_dir = report_output_dir(crawl_id, base_dir=base_dir)
    for segment in (_safe_report_variant(language), _safe_report_variant(tone)):
        if segment:
            output_dir = output_dir / segment
    filename = 'report'
    if report_id is not None:
        filename = f'report-{_safe_int(report_id, "report_id")}'

    return {
        'dir': output_dir,
        'markdown': output_dir / f'{filename}.md',
        'html': output_dir / f'{filename}.html',
        'pdf': output_dir / f'{filename}.pdf',
    }


def render_report_html(markdown_text, branding, crawl_metadata, audit_packet=None):
    """Render report markdown into branded HTML without importing Flask."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(['html', 'xml']),
    )
    template = env.get_template('report_pdf.html')
    report_html = markdown.markdown(_escape_raw_html(markdown_text or ''), extensions=['extra', 'sane_lists'])
    report_html = _sanitize_report_html(report_html)
    return template.render(
        report_html=report_html,
        branding=_branding(branding),
        crawl=crawl_metadata or {},
        report=build_report_view_model(audit_packet or {'crawl_metadata': crawl_metadata or {}}),
    )


def render_report_pdf(html, output_path):
    """Write an A4 PDF for the supplied report HTML."""
    output_path = Path(output_path).resolve()
    _ensure_under_base(output_path, DEFAULT_REPORTS_BASE_DIR.resolve())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        try:
            page = browser.new_page()
            page.set_content(html, wait_until='networkidle')
            page.pdf(
                path=str(output_path),
                format='A4',
                print_background=True,
                margin={'top': '18mm', 'right': '20mm', 'bottom': '22mm', 'left': '20mm'},
            )
        finally:
            browser.close()
    return output_path


def _safe_int(value, field_name):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} must be an integer') from None
    if value < 1:
        raise ValueError(f'{field_name} must be a positive integer')
    return value


def _safe_report_variant(value):
    if not value:
        return None
    value = str(value).strip().lower()
    if not REPORT_VARIANT_RE.match(value):
        raise ValueError('report variant must be a safe slug')
    return value


def _first_dict(*values):
    for value in values:
        if isinstance(value, dict) and value:
            return value
    return {}


def _report_stats(counts):
    if not isinstance(counts, dict):
        return []
    fields = (
        ('urls', 'URLs rastreadas'),
        ('links', 'Enlaces detectados'),
        ('issues', 'Incidencias'),
    )
    stats = []
    for key, label in fields:
        value = _safe_count(counts.get(key))
        if value is not None:
            stats.append({'label': label, 'value': f'{value:,}'.replace(',', '.')})
    return stats


def _status_distribution(rows):
    rows = rows or []
    grouped = {label: 0 for label, _ in STATUS_GROUPS}
    for row in rows:
        status = _safe_count((row or {}).get('status_code'))
        count = _safe_count((row or {}).get('count')) or 0
        if status is None or count <= 0:
            continue
        for label, matcher in STATUS_GROUPS:
            if matcher(status):
                grouped[label] += count
                break
    total = sum(grouped.values())
    return [
        {'label': label, 'count': count, 'percent': _percent(count, total)}
        for label, count in grouped.items()
        if count > 0
    ]


def _issue_distribution(issue_counts):
    if not isinstance(issue_counts, dict):
        return []
    total = sum(_safe_count(value) or 0 for value in issue_counts.values())
    rows = []
    severity_order = {'error': 0, 'warning': 1, 'info': 2, 'notice': 3}
    ordered = sorted(
        issue_counts.items(),
        key=lambda item: (severity_order.get(str(item[0]).lower(), 99), -(_safe_count(item[1]) or 0)),
    )
    for label, value in ordered:
        count = _safe_count(value)
        if count and count > 0:
            rows.append({'label': str(label), 'count': count, 'percent': _percent(count, total)})
    return rows


def _priority_rows(status_rows, issue_rows):
    rows = []
    for row in (status_rows or [])[:3]:
        count = _safe_count((row or {}).get('count'))
        status = _safe_count((row or {}).get('status_code'))
        if count and status is not None:
            rows.append({'label': f'HTTP {status}', 'count': count})
    for row in (issue_rows or [])[:3]:
        count = _safe_count((row or {}).get('count'))
        label = (row or {}).get('issue') or (row or {}).get('type') or (row or {}).get('category')
        if count and label:
            rows.append({'label': str(label), 'count': count})
    return rows


def _percent(value, total):
    value = _safe_count(value) or 0
    total = _safe_count(total) or 0
    if total <= 0:
        return 0
    return max(0, min(100, round((value / total) * 100)))


def _safe_count(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _ensure_under_base(path, base_path):
    if not path.resolve().is_relative_to(base_path):
        raise ValueError('report path escapes reports directory')


def _branding(branding):
    branding = branding or {}
    primary_color = branding.get('primary_color') or DEFAULT_PRIMARY_COLOR
    if not HEX_COLOR_RE.match(str(primary_color)):
        primary_color = DEFAULT_PRIMARY_COLOR
    return {
        'agency_name': branding.get('agency_name') or 'Mitmore SEO Crawl',
        'primary_color': primary_color,
        'footer_text': branding.get('footer_text'),
        'logo_path': _safe_logo_path(branding.get('logo_path')),
    }


def _escape_raw_html(text):
    return str(escape(text))


def _safe_logo_path(value):
    if not value:
        return None
    value = str(value).strip()
    parsed = urlparse(value)
    if parsed.scheme:
        if parsed.scheme == 'data' and value.startswith('data:image/'):
            return value
        return None
    if value.startswith('/static/') and '..' not in value:
        return value
    return None


def _sanitize_report_html(html):
    return _ReportHtmlSanitizer().sanitize(html)


class _ReportHtmlSanitizer(HTMLParser):
    allowed_tags = {
        'p', 'br', 'strong', 'em', 'b', 'i', 'ul', 'ol', 'li', 'blockquote',
        'code', 'pre', 'h1', 'h2', 'h3', 'h4', 'table', 'thead', 'tbody',
        'tr', 'th', 'td', 'hr', 'a',
    }
    allowed_attrs = {'a': {'href', 'title'}}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []

    def sanitize(self, html):
        self.feed(html or '')
        self.close()
        return ''.join(self.parts)

    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed_tags:
            return
        safe_attrs = self._safe_attrs(tag, attrs)
        attr_text = ''.join(f' {name}="{escape(value)}"' for name, value in safe_attrs)
        self.parts.append(f'<{tag}{attr_text}>')

    def handle_startendtag(self, tag, attrs):
        if tag == 'br':
            self.parts.append('<br>')
        elif tag == 'hr':
            self.parts.append('<hr>')

    def handle_endtag(self, tag):
        if tag in self.allowed_tags and tag not in {'br', 'hr'}:
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        self.parts.append(str(escape(data)))

    def handle_entityref(self, name):
        self.parts.append(f'&{name};')

    def handle_charref(self, name):
        self.parts.append(f'&#{name};')

    def _safe_attrs(self, tag, attrs):
        allowed = self.allowed_attrs.get(tag, set())
        safe = []
        for name, value in attrs:
            if name not in allowed:
                continue
            if name == 'href' and not _safe_link_href(value):
                continue
            safe.append((name, value or ''))
        return safe


def _safe_link_href(value):
    if not value:
        return False
    parsed = urlparse(str(value).strip())
    if not parsed.scheme:
        return True
    return parsed.scheme in {'http', 'https', 'mailto'}
