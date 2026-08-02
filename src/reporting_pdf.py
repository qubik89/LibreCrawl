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
SAFE_FONT_RE = re.compile(r'^[A-Za-z0-9 ,."\'-]{1,120}$')
SAFE_LOGO_DATA_RE = re.compile(r'^data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+$')
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
        'facts': output_dir / f'{filename}.facts.json',
        'analysis': output_dir / f'{filename}.analysis.json',
        'document': output_dir / f'{filename}.document.json',
        'quality': output_dir / f'{filename}.quality.json',
        'manifest': output_dir / f'{filename}.manifest.json',
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


def render_report_document_html(document, analysis, audit_facts, branding=None):
    """Render Report Suite 2.0 from validated JSON, never model-authored HTML."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(['html', 'xml']),
    )
    template = env.get_template('report_v2.html')
    return template.render(report=build_report_suite_view_model(document, analysis, audit_facts, branding))


def build_report_suite_view_model(document, analysis, audit_facts, branding=None):
    """Build all visual data from the immutable V2 facts snapshot."""
    document = document or {}
    analysis = analysis or {}
    facts = audit_facts or {}
    coverage = facts.get('coverage') or {}
    coverage = dict(coverage)
    denominators = coverage.get('denominators') or {}
    distributions = facts.get('distributions') or {}
    thematic = facts.get('thematic_metrics') or {}
    findings = {row.get('id'): row for row in analysis.get('findings') or []}
    recommendations = analysis.get('recommendations') or []
    report_type = document.get('report_type') or 'executive'
    language = document.get('language') or 'es-ES'
    coverage['coverage_ratio_display'] = _format_percent(coverage.get('coverage_ratio'), language)
    labels = _suite_labels(language)
    commercial_context = document.get('commercial_context') or 'prospect'
    copy = _suite_product_copy(language, report_type, commercial_context)
    status_rows = _suite_distribution(distributions.get('status_codes'), 'label', language)
    issue_rows = _suite_distribution(distributions.get('issue_groups'), 'issue', language)
    depth_rows = _suite_distribution(distributions.get('depth'), 'label', language)
    performance = thematic.get('performance') or {}
    crawl = dict(facts.get('crawl') or {})
    crawl['display_domain'] = _display_domain(
        crawl.get('final_domain') or crawl.get('final_url')
        or crawl.get('base_domain') or crawl.get('base_url')
    )
    client_context = document.get('client_context') or {}
    brand = _suite_branding(branding, client_context)
    display_findings = [_display_finding(item, language) for item in findings.values()]
    display_recommendations = [
        _display_recommendation(item, index, language)
        for index, item in enumerate(recommendations, start=1)
    ]

    sections = []
    for section in document.get('sections') or []:
        section_findings = [_display_finding(findings[item], language) for item in section.get('finding_ids') or [] if item in findings]
        sections.append({
            **section,
            'findings': section_findings,
            'chart_rows': _chart_rows(section.get('chart'), status_rows, issue_rows, depth_rows, performance, recommendations, language),
        })
    return {
        'brand': brand,
        'language': language,
        'labels': labels,
        'copy': copy,
        'report_type': report_type,
        'title': document.get('title') or labels['default_title'],
        'subtitle': document.get('subtitle'),
        'closing': document.get('closing'),
        'crawl': crawl,
        'coverage': coverage,
        'limitations': list(facts.get('limitations') or []) + list(analysis.get('limitations') or []),
        'stats': _suite_cover_stats(report_type, denominators, thematic, coverage, labels, language),
        'coverage_rows': _suite_coverage_rows(denominators, language),
        'comparison_rows': _suite_comparison_rows(facts.get('comparison') or {}, language),
        'scorecard': _suite_scorecard(denominators, thematic, language),
        'status_rows': status_rows,
        'issue_rows': issue_rows,
        'depth_rows': depth_rows,
        'performance_rows': _suite_performance_rows(performance, language),
        'funnel_rows': _suite_funnel_rows(denominators, thematic, language),
        'performance': performance,
        'findings': display_findings,
        'recommendations': display_recommendations,
        'roadmap': _suite_roadmap(display_recommendations, language),
        'impact_effort': _suite_impact_effort(display_recommendations, language),
        'sections': sections,
        'evidence': facts.get('evidence') or {},
        'evidence_tables': _suite_evidence_tables(facts.get('evidence') or {}, denominators, language),
        'comparison': facts.get('comparison') or {},
        'enrichments': facts.get('enrichments') or {},
        'enrichment_rows': _suite_enrichment_rows(facts.get('enrichments') or {}, language),
        'commercial_context': commercial_context,
    }


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
            options = {
                'path': str(output_path),
                'format': 'A4',
                'print_background': True,
                'prefer_css_page_size': True,
                'display_header_footer': True,
                'header_template': '<span></span>',
                'footer_template': '<div style="font-size:8px;width:100%;padding:0 14mm;color:#6b7280;text-align:right"><span class="pageNumber"></span> / <span class="totalPages"></span></div>',
                'margin': {'top': '16mm', 'right': '16mm', 'bottom': '18mm', 'left': '16mm'},
                'tagged': True,
                'outline': True,
            }
            try:
                page.pdf(**options)
            except TypeError:
                # Older local Playwright builds still produce a well-formed PDF.
                options.pop('tagged', None)
                options.pop('outline', None)
                page.pdf(**options)
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


def _display_domain(value):
    """Keep the operational host in facts but present the registered domain cleanly."""
    value = str(value or '').strip()
    if '://' in value:
        value = urlparse(value).netloc
    value = value.split('@')[-1].split(':')[0].lower()
    return value[4:] if value.startswith('www.') else value


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


def _suite_branding(branding, client_context):
    branding = branding or {}
    primary = _accessible_suite_color(
        branding.get('primary_color') or branding.get('brand_primary'), '#1d4ed8'
    )
    secondary = _accessible_suite_color(branding.get('secondary_color'), '#334155')
    accent = _accessible_suite_color(branding.get('accent_color'), '#b45309')
    name = client_context.get('client_name') or branding.get('client_name') or ''
    display_name = _display_domain(name) if '.' in name and ' ' not in name else name
    reporter = branding.get('agency_name') or client_context.get('author') or ''
    if _is_product_brand(reporter):
        reporter = ''
    footer = client_context.get('confidentiality') or branding.get('footer_text') or ''
    if _is_product_brand(footer):
        footer = ''
    author = client_context.get('author') or ''
    contact = client_context.get('contact') or ''
    cta = client_context.get('cta') or branding.get('cta') or ''
    if _is_product_brand(author):
        author = ''
    if _is_product_brand(contact):
        contact = ''
    if _is_product_brand(cta):
        cta = ''
    return {
        'name': name,
        'display_name': display_name,
        'reporter': reporter,
        'logo_path': _safe_suite_logo_path(branding.get('logo_path')),
        'primary_color': primary,
        'secondary_color': secondary,
        'accent_color': accent,
        'footer_text': footer,
        'author': author,
        'contact': contact,
        'cta': cta,
        'font_family': _safe_font_family(branding.get('font_family')),
    }


def _is_product_brand(value):
    compact = ''.join(char for char in str(value or '').lower() if char.isalnum())
    return 'librecrawl' in compact or 'mitmore' in compact


def _safe_font_family(value):
    value = str(value or '').strip()
    if not value or not SAFE_FONT_RE.match(value):
        return 'Inter, Arial, sans-serif'
    return value


def _accessible_suite_color(value, fallback):
    value = str(value or '').strip()
    if not HEX_COLOR_RE.match(value) or _contrast_ratio(value, '#ffffff') < 4.5:
        return fallback
    return value.lower()


def _contrast_ratio(first, second):
    def channel(value):
        value = value.lstrip('#')
        if len(value) == 3:
            value = ''.join(char * 2 for char in value)
        try:
            raw = int(value, 16) / 255
        except ValueError:
            return 0
        return raw / 12.92 if raw <= 0.04045 else ((raw + 0.055) / 1.055) ** 2.4

    def luminance(value):
        value = value.lstrip('#')
        if len(value) == 3:
            value = ''.join(char * 2 for char in value)
        return 0.2126 * channel(value[0:2]) + 0.7152 * channel(value[2:4]) + 0.0722 * channel(value[4:6])

    light = max(luminance(first), luminance(second))
    dark = min(luminance(first), luminance(second))
    return (light + 0.05) / (dark + 0.05)


def _suite_labels(language):
    if language == 'en':
        return {
            'default_title': 'SEO audit report', 'unique_urls': 'Unique URLs', 'html_2xx': 'HTML 2xx',
            'unique_issues': 'Unique issues', 'coverage': 'Evidence coverage', 'limitations': 'Limits and context',
            'evidence': 'Representative evidence', 'recommendations': 'Recommended actions',
            'no_data': 'No data available', 'confidence': 'Confidence', 'denominator': 'Denominator',
        }
    return {
        'default_title': 'Informe de auditoría SEO', 'unique_urls': 'URLs únicas', 'html_2xx': 'HTML 2xx',
        'unique_issues': 'Incidencias únicas', 'coverage': 'Cobertura de evidencia', 'limitations': 'Límites y contexto',
        'evidence': 'Evidencia representativa', 'recommendations': 'Acciones recomendadas',
        'no_data': 'Sin datos disponibles', 'confidence': 'Confianza', 'denominator': 'Denominador',
    }


def _suite_product_copy(language, report_type, commercial_context):
    if language == 'en':
        products = {
            'executive': ('Executive report', 'Leadership and marketing', 'Decide priorities, owners, and sequence.'),
            'commercial': ('Opportunity report', 'Client and commercial team', 'Frame the opportunity without inventing business outcomes.'),
            'technical': ('Technical audit', 'SEO, engineering, and operations', 'Implement, verify, and re-crawl with the same definitions.'),
        }
        label, audience, purpose = products[report_type]
        return {
            'product_label': label, 'audience': audience, 'purpose': purpose,
            'context_label': 'Existing client' if commercial_context == 'existing_client' else 'Prospect',
            'contents': 'Contents', 'decision': 'Decision brief', 'coverage': 'Evidence coverage',
            'scorecard': 'Transparent thematic scorecard', 'distributions': 'Observed distributions',
            'findings': 'Evidence-led findings', 'actions': 'Prioritised action backlog',
            'plan': 'Impact, effort, and sequence', 'method': 'Method, sources, and limits',
            'issues_appendix': 'Issue evidence', 'urls_appendix': 'URL sample', 'links_appendix': 'Link sample',
            'glossary': 'Definitions and glossary', 'validation': 'Post-change validation',
            'contract': 'Data contract', 'runbook': 'Resolution runbook', 'evidence_register': 'Evidence register',
            'formula_note': 'Each state uses an explicit numerator and denominator. There is no opaque global score.',
            'method_text': 'URLs, issues, and links are deduplicated before calculations. Samples are deterministic and stratified; they are not the first rows returned by storage.',
            'claim_limit': 'Crawl evidence can describe technical accessibility and declared indexability, but not actual Google indexing, traffic, conversions, or revenue without an authorised source.',
            'not_connected': 'Not connected', 'connected': 'Connected', 'available': 'Available',
        }
    products = {
        'executive': ('Informe ejecutivo', 'Dirección general y marketing', 'Decidir prioridades, responsables y secuencia.'),
        'commercial': ('Informe comercial', 'Cliente y equipo comercial', 'Enmarcar la oportunidad sin inventar resultados de negocio.'),
        'technical': ('Auditoría técnica', 'SEO, ingeniería y operaciones', 'Implementar, verificar y repetir el rastreo con las mismas definiciones.'),
    }
    label, audience, purpose = products[report_type]
    return {
        'product_label': label, 'audience': audience, 'purpose': purpose,
        'context_label': 'Cliente actual' if commercial_context == 'existing_client' else 'Prospecto',
        'contents': 'Contenido', 'decision': 'Resumen de decisión', 'coverage': 'Cobertura de la evidencia',
        'scorecard': 'Cuadro temático transparente', 'distributions': 'Distribuciones observadas',
        'findings': 'Hallazgos basados en evidencia', 'actions': 'Backlog priorizado de acciones',
        'plan': 'Impacto, esfuerzo y secuencia', 'method': 'Método, fuentes y límites',
        'issues_appendix': 'Evidencia de incidencias', 'urls_appendix': 'Muestra de URLs', 'links_appendix': 'Muestra de enlaces',
        'glossary': 'Definiciones y glosario', 'validation': 'Validación posterior',
        'contract': 'Contrato de datos', 'runbook': 'Runbook de resolución', 'evidence_register': 'Registro de evidencias',
        'formula_note': 'Cada estado utiliza un numerador y un denominador explícitos. No existe una puntuación global opaca.',
        'method_text': 'Las URLs, incidencias y enlaces se deduplican antes de calcular. Las muestras son deterministas y estratificadas; no son las primeras filas devueltas por el almacenamiento.',
        'claim_limit': 'El crawl puede describir accesibilidad técnica e indexabilidad declarada, pero no indexación real en Google, tráfico, conversiones ni ingresos sin una fuente autorizada.',
        'not_connected': 'No conectada', 'connected': 'Conectada', 'available': 'Disponible',
    }


def _suite_cover_stats(report_type, denominators, thematic, coverage, labels, language):
    indexability = thematic.get('indexability') or {}
    indexable = _first_number(
        denominators.get('indexable_html_urls'), denominators.get('indexable_urls'),
        indexability.get('indexable_html_urls'), indexability.get('indexable_urls'),
    )
    html_2xx = _first_number(denominators.get('html_2xx_urls'), indexability.get('denominator'))
    if report_type == 'commercial':
        ratio_label = 'Unique URLs / requests' if language == 'en' else 'URLs únicas / peticiones'
        return [
            _suite_stat(denominators.get('unique_urls'), labels['unique_urls'], language),
            _suite_stat(denominators.get('unique_issues'), labels['unique_issues'], language),
            {'label': ratio_label, 'value': _format_percent(coverage.get('coverage_ratio'), language)},
        ]
    if report_type == 'technical':
        p75 = (thematic.get('performance') or {}).get('p75_ms')
        return [
            _suite_stat(denominators.get('unique_urls'), labels['unique_urls'], language),
            _suite_stat(denominators.get('unique_issues'), labels['unique_issues'], language),
            {'label': 'p75 crawl request duration' if language == 'en' else 'p75 duración de petición', 'value': _format_ms(p75, language)},
        ]
    return [
        _suite_stat(denominators.get('unique_urls'), labels['unique_urls'], language),
        _suite_stat(denominators.get('unique_issues'), labels['unique_issues'], language),
        {
            'label': 'Indexable / HTML 2xx' if language == 'en' else 'Indexables / HTML 2xx',
            'value': f'{_format_number(indexable, language)} / {_format_number(html_2xx, language)}',
        },
    ]


def _suite_coverage_rows(denominators, language):
    labels = {
        'discovered_urls': ('Requests made', 'Peticiones realizadas'),
        'unique_urls': ('Unique URLs after deduplication', 'URLs únicas tras deduplicar'),
        'internal_urls': ('Internal URLs', 'URLs internas'),
        'html_2xx_urls': ('HTML 2xx URLs', 'URLs HTML 2xx'),
        'indexable_html_urls': ('Indexable HTML URLs', 'URLs HTML indexables'),
        'indexable_urls': ('Indexable URLs', 'URLs indexables'),
        'unique_links': ('Unique link edges', 'Aristas de enlace únicas'),
        'unique_issues': ('Unique issues', 'Incidencias únicas'),
        'sitemap_urls': ('Sitemap URLs', 'URLs en sitemap'),
        'sitemap_crawl_overlap_urls': ('Sitemap/crawl overlap', 'Solapamiento sitemap/crawl'),
    }
    rows = []
    for key in labels:
        value = denominators.get(key)
        if value is None and key == 'indexable_html_urls':
            value = denominators.get('indexable_urls')
        if key == 'indexable_urls' and denominators.get('indexable_html_urls') is not None:
            continue
        rows.append({
            'key': key, 'field': f'coverage.denominators.{key}',
            'label': labels[key][0 if language == 'en' else 1],
            'value': value, 'display': _format_number(value, language) if value is not None else ('Not available' if language == 'en' else 'No disponible'),
            'missing': value is None,
        })
    return rows


def _suite_comparison_rows(comparison, language):
    if not comparison.get('available'):
        return []
    deltas = comparison.get('deltas') or {}
    labels = {
        'unique_urls': ('Unique URLs', 'URLs únicas'),
        'unique_issues': ('Unique issues', 'Incidencias únicas'),
    }
    rows = []
    for key in ('unique_urls', 'unique_issues'):
        value = deltas.get(key)
        if value is None:
            continue
        rows.append({
            'key': key,
            'label': labels[key][0 if language == 'en' else 1],
            'value': value,
            'display': _format_signed_number(value, language),
            'shape': '▲' if value > 0 else '▼' if value < 0 else '●',
        })
    return rows


def _suite_scorecard(denominators, thematic, language):
    http = thematic.get('http') or {}
    indexability = thematic.get('indexability') or {}
    content = thematic.get('content') or {}
    links = thematic.get('links') or {}
    unique_urls = _first_number(denominators.get('unique_urls'), 0) or 0
    html_2xx = _first_number(denominators.get('html_2xx_urls'), indexability.get('denominator'), content.get('denominator'), 0) or 0
    indexable = _first_number(indexability.get('indexable_html_urls'), indexability.get('indexable_urls'), denominators.get('indexable_html_urls'), denominators.get('indexable_urls'), 0) or 0
    content_total = html_2xx * 4
    content_failures = sum(_safe_count(content.get(key)) or 0 for key in (
        'missing_title', 'missing_meta_description', 'missing_h1', 'thin_content_under_300_words',
    ))
    names = {
        'http': ('HTTP response', 'Respuesta HTTP'), 'indexability': ('Indexability', 'Indexabilidad'),
        'content': ('Content and metadata', 'Contenido y metadatos'), 'links': ('Internal linking', 'Enlazado interno'),
        'performance': ('Crawl performance', 'Rendimiento de rastreo'),
    }
    definitions = [
        ('http', _first_number(http.get('2xx'), 0) or 0, unique_urls,
         ('2xx URLs / unique URLs', 'URLs 2xx / URLs únicas'),
         'thematic_metrics.http.2xx / coverage.denominators.unique_urls'),
        ('indexability', indexable, html_2xx,
         ('indexable HTML / HTML 2xx', 'HTML indexable / HTML 2xx'),
         'thematic_metrics.indexability.indexable_html_urls / coverage.denominators.html_2xx_urls'),
        ('content', max(0, content_total - content_failures), content_total,
         ('passed checks / 4 checks x HTML 2xx', 'comprobaciones superadas / 4 x HTML 2xx'),
         'thematic_metrics.content.* / coverage.denominators.html_2xx_urls'),
        ('links', _first_number(links.get('internal_edges'), 0) or 0, _first_number(links.get('unique_edges'), denominators.get('unique_links'), 0) or 0,
         ('internal edges / unique edges', 'aristas internas / aristas únicas'),
         'thematic_metrics.links.internal_edges / thematic_metrics.links.unique_edges'),
    ]
    rows = []
    for key, passed, total, formulas, fields in definitions:
        available = total > 0
        value = round((passed / total) * 100, 1) if available else None
        status = 'unavailable' if not available else ('conform' if value >= 90 else 'attention' if value >= 70 else 'critical')
        rows.append({
            'key': key, 'name': names[key][0 if language == 'en' else 1], 'passed': passed, 'total': total,
            'display': _format_percent(value, language) if available else ('Not scored' if language == 'en' else 'No puntuable'),
            'count_display': f'{_format_number(passed, language)} / {_format_number(total, language)}' if available else ('No denominator' if language == 'en' else 'Sin denominador'),
            'formula': formulas[0 if language == 'en' else 1], 'fields': fields, 'available': available, 'bar_width': max(1.5, value or 0),
            'status': status, 'status_label': _status_label(status, language), 'shape': {'conform': '●', 'attention': '◐', 'critical': '▲', 'unavailable': '○'}[status],
        })
    perf = thematic.get('performance') or {}
    rows.append({
        'key': 'performance', 'name': names['performance'][0 if language == 'en' else 1], 'available': False,
        'display': 'Not scored' if language == 'en' else 'No puntuable',
        'count_display': f"n = {_format_number(perf.get('count'), language)}", 'bar_width': 0,
        'formula': 'p50 / p75 / p90 / p95 crawl request duration' if language == 'en' else 'p50 / p75 / p90 / p95 duración de petición',
        'fields': 'thematic_metrics.performance.*', 'status': 'unavailable',
        'status_label': _status_label('unavailable', language), 'shape': '○',
    })
    return rows


def _suite_performance_rows(performance, language='es-ES'):
    values = [(key, performance.get(f'{key}_ms')) for key in ('p50', 'p75', 'p90', 'p95')]
    maximum = max([value or 0 for _, value in values] or [1]) or 1
    return [
        {'label': key, 'count': value, 'display_count': _format_ms(value, language), 'percent': round(((value or 0) / maximum) * 100, 1), 'pattern': index % 3}
        for index, (key, value) in enumerate(values)
    ]


def _suite_funnel_rows(denominators, thematic, language):
    indexability = thematic.get('indexability') or {}
    values = [
        ('Unique URLs' if language == 'en' else 'URLs únicas', denominators.get('unique_urls'), 'coverage.denominators.unique_urls'),
        ('Internal URLs' if language == 'en' else 'URLs internas', denominators.get('internal_urls'), 'coverage.denominators.internal_urls'),
        ('HTML 2xx' if language == 'en' else 'HTML con respuesta 2xx', denominators.get('html_2xx_urls'), 'coverage.denominators.html_2xx_urls'),
        ('Indexable HTML' if language == 'en' else 'HTML sin noindex', _first_number(indexability.get('indexable_html_urls'), denominators.get('indexable_html_urls'), denominators.get('indexable_urls')), 'thematic_metrics.indexability.indexable_html_urls'),
    ]
    base = _safe_count(values[0][1]) or 0
    rows = []
    previous = None
    for label, value, field in values:
        count = _safe_count(value)
        rows.append({
            'label': label, 'value': count, 'display': _format_number(count, language), 'field': field,
            'percent': round((count / base) * 100, 1) if base and count is not None else 0,
            'drop': None if previous is None or count is None else previous - count,
        })
        previous = count
    return rows


def _display_recommendation(item, index, language):
    result = dict(item)
    result['order'] = f'{index:02d}'
    result['impact_label'] = _level_label(result.get('impact'), language)
    result['effort_label'] = _level_label(result.get('effort'), language)
    result['priority_label'] = {
        'now': ('Now', 'Ahora'), 'next': ('Next', 'Después'), 'later': ('Later', 'Más adelante'),
    }.get(result.get('priority'), ('Next', 'Después'))[0 if language == 'en' else 1]
    dependencies = result.get('dependencies') or []
    result['dependency_text'] = ' · '.join(str(value) for value in dependencies) or ('None declared' if language == 'en' else 'Ninguna declarada')
    result['shape'] = {'high': '▲', 'medium': '◐', 'low': '●'}.get(result.get('impact'), '○')
    return result


def _suite_roadmap(recommendations, language):
    definitions = [
        ('now', 'Days 1-30' if language == 'en' else 'Días 1-30'),
        ('next', 'Days 31-60' if language == 'en' else 'Días 31-60'),
        ('later', 'Days 61-90' if language == 'en' else 'Días 61-90'),
    ]
    return [{'key': key, 'label': label, 'items': [item for item in recommendations if item.get('priority') == key]} for key, label in definitions]


def _suite_impact_effort(recommendations, language):
    levels = ('high', 'medium', 'low')
    return [
        {
            'impact': impact, 'impact_label': _level_label(impact, language),
            'cells': [
                {'effort': effort, 'effort_label': _level_label(effort, language), 'items': [item for item in recommendations if item.get('impact') == impact and item.get('effort') == effort]}
                for effort in reversed(levels)
            ],
        }
        for impact in levels
    ]


def _suite_enrichment_rows(enrichments, language):
    names = {'gsc': 'Google Search Console', 'ga4': 'Google Analytics 4', 'crux': 'Chrome UX Report', 'pagespeed': 'PageSpeed / Lighthouse'}
    rows = []
    for key, name in names.items():
        source = enrichments.get(key) or {}
        status = source.get('status') or 'not_connected'
        rows.append({
            'key': key, 'name': name, 'status': status,
            'shape': '●' if status in {'connected', 'available', 'complete'} else '○',
            'status_label': ('Connected' if language == 'en' else 'Conectada') if status in {'connected', 'available', 'complete'} else ('Not connected' if language == 'en' else 'No conectada'),
        })
    return rows


def _suite_evidence_tables(evidence, denominators, language):
    return {
        'issues': {
            'rows': [_issue_evidence_row(row, language) for row in (evidence.get('issues') or [])],
            'denominator': denominators.get('unique_issues'),
        },
        'urls': {
            'rows': [_url_evidence_row(row, language) for row in (evidence.get('urls') or [])],
            'denominator': denominators.get('unique_urls'),
        },
        'links': {
            'rows': [_link_evidence_row(row) for row in (evidence.get('links') or [])],
            'denominator': denominators.get('unique_links'),
        },
        'sample_label': 'sampled from' if language == 'en' else 'muestra de',
    }


def _issue_evidence_row(row, language='es-ES'):
    url = str(row.get('url') or '')
    parsed = urlparse(url)
    clean = dict(row)
    for field in ('issue', 'details', 'category'):
        if field in clean:
            clean[field] = _white_label_copy(clean[field], language)
    return {**clean, 'host': parsed.netloc, 'path': parsed.path or '/', 'shape': '▲' if row.get('type') == 'error' else '●'}


def _white_label_copy(value, language='es-ES'):
    """Remove crawler platform names that may have entered stored evidence."""
    text = str(value or '')
    crawler_label = 'the crawler' if language == 'en' else 'el crawler'
    issuer_label = 'the issuer' if language == 'en' else 'el emisor'
    text = re.sub(r'(?i)\blibrecrawl\b', crawler_label, text)
    text = re.sub(r'(?i)\bmitmore(?:\s+seo\s+crawl)?\b', issuer_label, text)
    return text


def _url_evidence_row(row, language='es-ES'):
    url = str(row.get('url') or '')
    requested = str(row.get('requested_url') or url)
    final = str(row.get('final_url') or url)
    parsed = urlparse(final)
    requested_parsed = urlparse(requested)
    chain = row.get('redirect_chain') or row.get('redirects') or []
    return {
        **row,
        'host': parsed.netloc,
        'path': parsed.path or '/',
        'requested_path': requested_parsed.path or '/',
        'final_path': parsed.path or '/',
        'redirect_count': len(chain) if isinstance(chain, list) else 0,
        'response_display': _format_ms(row.get('response_time'), language),
    }


def _link_evidence_row(row):
    source = str(row.get('source_final_url') or row.get('source_url') or '')
    target = str(row.get('target_final_url') or row.get('target_url') or '')
    requested_source = str(row.get('source_url') or source)
    requested_target = str(row.get('target_url') or target)
    return {
        **row,
        'source_path': urlparse(source).path or '/',
        'target_path': urlparse(target).path or target,
        'requested_source_path': urlparse(requested_source).path or '/',
        'requested_target_path': urlparse(requested_target).path or target,
    }


def _first_number(*values):
    for value in values:
        if value is not None:
            try:
                return float(value) if isinstance(value, float) else int(value)
            except (TypeError, ValueError):
                continue
    return None


def _format_number(value, language):
    if value is None:
        return '—'
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f'{number:,}'.replace(',', '.' if language != 'en' else ',')


def _format_signed_number(value, language):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return '—'
    sign = '+' if number > 0 else ''
    return sign + _format_number(number, language)


def _format_percent(value, language):
    if value is None:
        return '—'
    try:
        display = f'{float(value):.1f}'.rstrip('0').rstrip('.')
    except (TypeError, ValueError):
        return '—'
    return display.replace('.', ',' if language != 'en' else '.') + ' %'


def _format_ms(value, language):
    if value is None:
        return '—'
    try:
        display = f'{float(value):,.0f}'
    except (TypeError, ValueError):
        return '—'
    if language != 'en':
        display = display.replace(',', '.')
    return f'{display} ms'


def _status_label(status, language):
    labels = {
        'conform': ('Conformant', 'Conforme'), 'attention': ('Attention', 'Atención'),
        'critical': ('Critical', 'Crítico'), 'unavailable': ('No data', 'Sin datos'),
    }
    return labels[status][0 if language == 'en' else 1]


def _level_label(value, language):
    labels = {'high': ('High', 'Alto'), 'medium': ('Medium', 'Medio'), 'low': ('Low', 'Bajo')}
    return labels.get(value, (str(value or '—'), str(value or '—')))[0 if language == 'en' else 1]


def _suite_stat(value, label, language='es-ES'):
    value = _safe_count(value)
    return {'label': label, 'value': _format_number(value, language)}


def _suite_distribution(rows, label_key, language='es-ES'):
    rows = rows or []
    total = sum(_safe_count((row or {}).get('count')) or 0 for row in rows)
    result = []
    for row in rows[:20]:
        count = _safe_count((row or {}).get('count')) or 0
        label = (row or {}).get(label_key) or (row or {}).get('category') or '—'
        result.append({'label': str(label), 'count': count, 'display_count': _format_number(count, language), 'percent': _percent(count, total)})
    return result


def _chart_rows(chart, status_rows, issue_rows, depth_rows, performance, recommendations, language='es-ES'):
    if chart == 'status_codes':
        return status_rows
    if chart == 'issue_groups':
        return issue_rows
    if chart == 'coverage':
        return depth_rows
    if chart == 'performance_percentiles':
        return [
            {'label': key.upper(), 'count': value or 0, 'display_count': f'{value:.0f} ms' if value is not None else '—', 'percent': min(100, round((value or 0) / max(1, performance.get('p95_ms') or 1) * 100))}
            for key, value in (('p50', performance.get('p50_ms')), ('p75', performance.get('p75_ms')), ('p90', performance.get('p90_ms')), ('p95', performance.get('p95_ms')))
        ]
    if chart == 'impact_effort':
        return [
            {'label': item.get('title') or ('Action' if language == 'en' else 'Acción'), 'count': index + 1, 'display_count': f"{item.get('impact', 'medium')} / {item.get('effort', 'medium')}", 'percent': {'high': 90, 'medium': 60, 'low': 30}.get(item.get('impact'), 45)}
            for index, item in enumerate(recommendations[:6])
        ]
    if chart == 'roadmap':
        return [
            {'label': item.get('title') or ('Action' if language == 'en' else 'Acción'), 'count': index + 1, 'display_count': item.get('priority') or 'next', 'percent': {'now': 90, 'next': 60, 'later': 30}.get(item.get('priority'), 45)}
            for index, item in enumerate(recommendations[:6])
        ]
    return []


def _display_finding(finding, language):
    result = dict(finding)
    if language != 'en':
        result['severity_label'] = {
            'critical': 'Crítica', 'high': 'Alta', 'medium': 'Media', 'low': 'Baja', 'opportunity': 'Oportunidad',
        }.get(result.get('severity'), result.get('severity'))
        result['confidence_label'] = {'high': 'Confianza alta', 'medium': 'Confianza media', 'low': 'Confianza baja'}.get(
            result.get('confidence'), result.get('confidence')
        )
    else:
        result['severity_label'] = result.get('severity')
        result['confidence_label'] = f"{result.get('confidence')} confidence"
    return result


def _escape_raw_html(text):
    return str(escape(text))


def _safe_logo_path(value):
    if not value:
        return None
    value = str(value).strip()
    parsed = urlparse(value)
    if parsed.scheme:
        if parsed.scheme == 'data' and SAFE_LOGO_DATA_RE.match(value):
            return value
        return None
    if value.startswith('/static/') and '..' not in value:
        return value
    return None


def _safe_suite_logo_path(value):
    """V2 logos are always owned, sanitised data URIs; never server paths."""
    value = str(value or '').strip()
    return value if SAFE_LOGO_DATA_RE.match(value) else None


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
