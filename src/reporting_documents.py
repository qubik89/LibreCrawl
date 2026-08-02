"""Strict JSON contracts and safe fallbacks for generated Report Suite documents."""

import json
import re


REPORT_TYPES = {'executive', 'commercial', 'technical'}
COMMERCIAL_CONTEXTS = {'prospect', 'existing_client'}
MAX_FINDINGS = 20
MAX_RECOMMENDATIONS = 30
MAX_QUALITY_ISSUES = 40

PROHIBITED_PROMISE_PATTERNS = (
    r'\b(?:garantiza|asegura|aumentará|incrementará|generará|mejorará)\b.{0,60}\b(?:ranking|rankings|ventas?|ingresos?|conversiones?|tráfico)\b',
    r'\b(?:guarantees?|will increase|will improve|will generate|ensures?)\b.{0,60}\b(?:rankings?|sales|revenue|conversions?|traffic)\b',
    r'\b(?:roi garantizado|guaranteed roi)\b',
)


def parse_json_response(text):
    """Parse a model response that may be wrapped in a Markdown code fence."""
    text = str(text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*```$', '', text)
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError('The model response was not valid JSON.') from exc
    if not isinstance(value, dict):
        raise ValueError('The model response must be a JSON object.')
    return value


def normalise_analysis(value, facts, language='es-ES'):
    """Validate the analysis shape and remove unsupported references."""
    if not isinstance(value, dict):
        raise ValueError('Analysis must be an object.')
    evidence_ids = _evidence_ids(facts)
    findings = []
    for index, item in enumerate((value.get('findings') or [])[:MAX_FINDINGS], start=1):
        if not isinstance(item, dict):
            continue
        observation = _text(item.get('observation'))
        if not observation:
            continue
        refs = [ref for ref in _string_list(item.get('evidence_ids')) if ref in evidence_ids]
        findings.append({
            'id': _id(item.get('id'), 'finding', index),
            'theme': _text(item.get('theme')) or 'technical_health',
            'severity': _choice(item.get('severity'), {'critical', 'high', 'medium', 'low', 'opportunity'}, 'medium'),
            'confidence': _choice(item.get('confidence'), {'high', 'medium', 'low'}, 'medium'),
            'observation': observation,
            'inference': _text(item.get('inference')),
            'hypothesis': _text(item.get('hypothesis')),
            'scope': _text(item.get('scope')),
            'numerator': _integer(item.get('numerator')),
            'denominator': _integer(item.get('denominator')),
            'metric_id': _text(item.get('metric_id')),
            'evidence_ids': refs,
            'limitation': _text(item.get('limitation')),
            'recommendation_ids': _string_list(item.get('recommendation_ids')),
        })
    recommendations = []
    for index, item in enumerate((value.get('recommendations') or [])[:MAX_RECOMMENDATIONS], start=1):
        if not isinstance(item, dict) or not _text(item.get('title')):
            continue
        recommendations.append({
            'id': _id(item.get('id'), 'recommendation', index),
            'title': _text(item.get('title')),
            'priority': _choice(item.get('priority'), {'now', 'next', 'later'}, 'next'),
            'impact': _choice(item.get('impact'), {'high', 'medium', 'low'}, 'medium'),
            'effort': _choice(item.get('effort'), {'high', 'medium', 'low'}, 'medium'),
            'owner': _text(item.get('owner')) or ('Responsible team' if language == 'en' else 'Equipo responsable'),
            'dependencies': _string_list(item.get('dependencies')),
            'sequence': _text(item.get('sequence')),
            'acceptance_criteria': _text(item.get('acceptance_criteria')),
            'validation': _text(item.get('validation')),
            'kpi': _text(item.get('kpi')),
        })
    if not findings:
        return fallback_analysis(facts, language)
    return {
        'findings': findings,
        'recommendations': recommendations or _fallback_recommendations(findings, language),
        'limitations': _string_list(value.get('limitations')) or list(facts.get('limitations') or []),
    }


def fallback_analysis(facts, language='es-ES'):
    """Create a useful report when a model cannot honour the JSON contract."""
    groups = ((facts.get('distributions') or {}).get('issue_groups') or [])[:5]
    issue_evidence = (facts.get('evidence') or {}).get('issues') or []
    denom = ((facts.get('coverage') or {}).get('denominators') or {}).get('unique_urls')
    findings = []
    for index, group in enumerate(groups, start=1):
        evidence = [row.get('id') for row in issue_evidence if row.get('category') == group.get('category') and row.get('issue') == group.get('issue')]
        count = _integer(group.get('count')) or 0
        findings.append({
            'id': f'finding-{index}',
            'theme': _slug(group.get('category')) or 'technical_health',
            'severity': _severity(group.get('type')),
            'confidence': 'high',
            'observation': (
                f"{count} URLs show: {group.get('issue') or 'detected issue'}."
                if language == 'en' else f"{count} URLs presentan: {group.get('issue') or 'incidencia detectada'}."
            ),
            'inference': '',
            'hypothesis': '',
            'scope': group.get('category') or 'SEO técnico',
            'numerator': count,
            'denominator': denom,
            'metric_id': f"issue.{_slug(group.get('category'))}.{_slug(group.get('issue'))}",
            'evidence_ids': evidence[:3],
            'limitation': (
                'Priority requires checking a representative sample before applying changes at scale.'
                if language == 'en' else 'La prioridad requiere comprobar una muestra representativa antes de aplicar cambios masivos.'
            ),
            'recommendation_ids': [f'recommendation-{index}'],
        })
    if not findings:
        findings.append({
            'id': 'finding-1', 'theme': 'coverage', 'severity': 'low', 'confidence': 'medium',
            'observation': (
                'There are not enough issue groups to establish automatic priorities.'
                if language == 'en' else 'No se han encontrado grupos de incidencias suficientes para establecer prioridades automáticas.'
            ),
            'inference': '', 'hypothesis': '', 'scope': 'Coverage' if language == 'en' else 'Cobertura', 'numerator': None, 'denominator': denom,
            'metric_id': 'coverage.denominators.unique_urls', 'evidence_ids': [],
            'limitation': (
                'Review coverage and connected sources before drawing conclusions.'
                if language == 'en' else 'Revisar cobertura y fuentes conectadas antes de extraer conclusiones.'
            ),
            'recommendation_ids': ['recommendation-1'],
        })
    return {'findings': findings, 'recommendations': _fallback_recommendations(findings, language), 'limitations': list(facts.get('limitations') or [])}


def normalise_document(value, facts, analysis, report_type, language='es-ES', commercial_context='prospect', client_context=None):
    """Validate writer JSON and fall back to a deterministic composition."""
    report_type = report_type if report_type in REPORT_TYPES else 'executive'
    commercial_context = commercial_context if commercial_context in COMMERCIAL_CONTEXTS else 'prospect'
    safe_context = _safe_client_context(client_context)
    if not isinstance(value, dict) or not isinstance(value.get('sections'), list):
        return fallback_document(facts, analysis, report_type, language, commercial_context, safe_context)
    known_findings = {item['id'] for item in analysis.get('findings') or []}
    sections = []
    for index, item in enumerate(value.get('sections')[:_section_limit(report_type)], start=1):
        if not isinstance(item, dict) or not _text(item.get('title')):
            continue
        sections.append({
            'id': _id(item.get('id'), 'section', index),
            'title': _text(item.get('title')),
            'summary': _text(item.get('summary')),
            'finding_ids': [ref for ref in _string_list(item.get('finding_ids')) if ref in known_findings],
            'chart': _choice(item.get('chart'), _chart_names(), None),
            'actions': _string_list(item.get('actions'))[:6],
        })
    if not sections:
        return fallback_document(facts, analysis, report_type, language, commercial_context, safe_context)
    return {
        'report_type': report_type,
        'commercial_context': commercial_context,
        'language': language,
        'title': safe_context.get('report_title') or _text(value.get('title')) or _default_title(report_type, language),
        'subtitle': _text(value.get('subtitle')) or _default_subtitle(report_type, commercial_context, language),
        'sections': sections,
        'closing': _text(value.get('closing')) or _default_closing(language),
        'client_context': safe_context,
    }


def validate_report_package(facts, analysis, document):
    """Return deterministic contract, reference, and claim checks with a score."""
    issues = []
    evidence_ids = _evidence_ids(facts)
    metric_catalog = {
        item.get('id'): item for item in (facts.get('metric_catalog') or [])
        if isinstance(item, dict) and item.get('id')
    }
    findings = analysis.get('findings') or []
    finding_ids = {item.get('id') for item in findings}
    recommendations = analysis.get('recommendations') or []

    for finding in findings:
        finding_id = finding.get('id')
        numerator = finding.get('numerator')
        denominator = finding.get('denominator')
        if numerator is not None and denominator is not None and (numerator < 0 or denominator < 0 or numerator > denominator):
            issues.append(_quality_issue(
                'invalid_denominator', 'critical', 'analysis', finding_id,
                'Finding numerator and denominator are inconsistent.',
                'Use the exact numerator and denominator from AuditFactsV2 or remove the unsupported values.',
            ))
        unknown_refs = [ref for ref in finding.get('evidence_ids') or [] if ref not in evidence_ids]
        if unknown_refs:
            issues.append(_quality_issue(
                'unknown_evidence', 'critical', 'analysis', finding_id,
                f"Unknown evidence references: {', '.join(unknown_refs)}.",
                'Remove unknown references and keep only evidence IDs present in AuditFactsV2.',
            ))
        if not finding.get('metric_id') and not finding.get('evidence_ids'):
            issues.append(_quality_issue(
                'missing_evidence_reference', 'major', 'analysis', finding_id,
                'The observation has no metric_id or evidence_ids.',
                'Add a valid evidence reference or remove the unsupported finding.',
            ))
        metric_id = finding.get('metric_id')
        if metric_catalog and metric_id and metric_id not in metric_catalog:
            issues.append(_quality_issue(
                'unknown_metric', 'critical', 'analysis', finding_id,
                f'Unknown metric reference: {metric_id}.',
                'Use an exact metric_catalog ID from AuditFactsV2 or remove the metric reference.',
            ))
        metric = metric_catalog.get(metric_id)
        if metric and numerator is not None and not _same_number(numerator, metric.get('value')):
            issues.append(_quality_issue(
                'metric_value_mismatch', 'critical', 'analysis', finding_id,
                f"Numerator {numerator} does not match {metric_id} ({metric.get('value')}).",
                'Copy the exact metric_catalog value or remove the unsupported numerator.',
            ))
        if metric and denominator is not None and metric.get('denominator') is not None and not _same_number(denominator, metric.get('denominator')):
            issues.append(_quality_issue(
                'metric_denominator_mismatch', 'critical', 'analysis', finding_id,
                f"Denominator {denominator} does not match {metric.get('denominator_id')} ({metric.get('denominator')}).",
                'Copy the exact metric_catalog denominator or remove the unsupported denominator.',
            ))

    for recommendation in recommendations:
        recommendation_id = recommendation.get('id')
        for field in ('acceptance_criteria', 'validation', 'kpi'):
            if not recommendation.get(field):
                issues.append(_quality_issue(
                    f'missing_{field}', 'major', 'analysis', recommendation_id,
                    f'Recommendation is missing {field}.',
                    f'Add an evidence-backed {field} without inventing a business outcome.',
                ))

    for section in document.get('sections') or []:
        unknown_findings = [ref for ref in section.get('finding_ids') or [] if ref not in finding_ids]
        if unknown_findings:
            issues.append(_quality_issue(
                'unknown_finding_reference', 'critical', 'document', section.get('id'),
                f"Unknown finding references: {', '.join(unknown_findings)}.",
                'Keep only finding IDs present in the validated analysis.',
            ))

    for target, target_id, text in _package_text_nodes(analysis, document):
        lower = text.lower()
        if 'librecrawl' in lower or 'mitmore' in lower:
            issues.append(_quality_issue(
                'product_brand_leak', 'critical', target, target_id,
                'The report contains a prohibited product or vendor brand.',
                'Remove the product/vendor name and retain only supplied white-label identity.',
            ))
        if any(re.search(pattern, lower, flags=re.IGNORECASE | re.DOTALL) for pattern in PROHIBITED_PROMISE_PATTERNS):
            issues.append(_quality_issue(
                'unsupported_causal_promise', 'critical', target, target_id,
                'The report contains an unsupported causal business or ranking promise.',
                'Replace the promise with an explicitly labelled hypothesis and a validation method, or remove it.',
            ))

    issues = _dedupe_quality_issues(issues)
    score = max(0, 100 - sum({'critical': 30, 'major': 10, 'minor': 3}[item['severity']] for item in issues))
    passed = score >= 90 and not any(item['severity'] == 'critical' for item in issues)
    return {'verdict': 'pass' if passed else 'fail', 'passed': passed, 'score': score, 'issues': issues}


def normalise_quality_review(value):
    """Validate the independent reviewer response and enforce the 90-point gate."""
    if not isinstance(value, dict):
        raise ValueError('Quality review must be an object.')
    issues = []
    for index, item in enumerate((value.get('issues') or [])[:MAX_QUALITY_ISSUES], start=1):
        if not isinstance(item, dict):
            continue
        message = _text(item.get('message'))
        if not message:
            continue
        issues.append({
            'code': _slug(item.get('code')) or f'model-review-{index}',
            'severity': _choice(item.get('severity'), {'critical', 'major', 'minor'}, 'major'),
            'target': _choice(item.get('target'), {'analysis', 'document'}, 'document'),
            'target_id': _text(item.get('target_id')) or None,
            'message': message,
            'repair_instruction': _text(item.get('repair_instruction')),
            'source': 'model',
        })
    try:
        score = max(0, min(100, int(value.get('score'))))
    except (TypeError, ValueError):
        score = 0
    passed = (
        str(value.get('verdict') or '').lower() == 'pass'
        and score >= 90
        and not any(item['severity'] == 'critical' for item in issues)
    )
    return {'verdict': 'pass' if passed else 'fail', 'passed': passed, 'score': score, 'issues': issues}


def merge_quality_reviews(deterministic, model_review):
    """Combine both gates; the package passes only when both reviewers pass."""
    issues = _dedupe_quality_issues(
        list((deterministic or {}).get('issues') or []) + list((model_review or {}).get('issues') or [])
    )
    score = min((deterministic or {}).get('score', 0), (model_review or {}).get('score', 0))
    passed = bool((deterministic or {}).get('passed')) and bool((model_review or {}).get('passed')) and score >= 90
    return {'verdict': 'pass' if passed else 'fail', 'passed': passed, 'score': score, 'issues': issues}


def normalise_repair(value, facts, report_type, language, commercial_context, client_context):
    """Apply the existing strict contracts to a repair response."""
    if not isinstance(value, dict):
        raise ValueError('Repair response must be an object.')
    analysis = normalise_analysis(value.get('analysis'), facts, language)
    document = normalise_document(
        value.get('document'), facts, analysis, report_type, language, commercial_context, client_context,
    )
    return analysis, document


def fallback_document(facts, analysis, report_type, language='es-ES', commercial_context='prospect', client_context=None):
    labels = _labels(language)
    safe_context = _safe_client_context(client_context)
    findings = list(analysis.get('findings') or [])
    finding_ids = [item['id'] for item in findings]
    common = [
        ('coverage', labels['coverage'], labels['coverage_summary'], 'coverage'),
        ('priorities', labels['priorities'], labels['priorities_summary'], 'issue_groups'),
    ]
    if report_type == 'executive':
        composition = [
            ('decision-brief', labels['decision_brief'], labels['decision_summary'], 'status_codes'),
            *common,
            ('roadmap', labels['roadmap'], labels['roadmap_summary'], 'roadmap'),
            ('method', labels['method'], labels['method_summary'], None),
        ]
    elif report_type == 'commercial':
        context = labels['prospect'] if commercial_context == 'prospect' else labels['existing_client']
        composition = [
            ('opportunity', labels['opportunity'], context, 'status_codes'),
            *common,
            ('workstreams', labels['workstreams'], labels['workstreams_summary'], 'impact_effort'),
            ('next-step', labels['next_step'], labels['next_step_summary'], 'roadmap'),
            ('method', labels['method'], labels['method_summary'], None),
        ]
    else:
        composition = [
            ('coverage', labels['coverage'], labels['coverage_summary'], 'coverage'),
            ('http', labels['http'], labels['http_summary'], 'status_codes'),
            ('content', labels['content'], labels['content_summary'], 'issue_groups'),
            ('performance', labels['performance'], labels['performance_summary'], 'performance_percentiles'),
            ('backlog', labels['backlog'], labels['backlog_summary'], 'impact_effort'),
            ('validation', labels['validation'], labels['validation_summary'], 'roadmap'),
            ('method', labels['method'], labels['method_summary'], None),
        ]
    return {
        'report_type': report_type,
        'commercial_context': commercial_context,
        'language': language,
        'title': safe_context.get('report_title') or _default_title(report_type, language),
        'subtitle': _default_subtitle(report_type, commercial_context, language),
        'sections': [
            {
                'id': key,
                'title': title,
                'summary': summary,
                'finding_ids': _section_finding_ids(key, findings, finding_ids),
                'chart': chart,
                'actions': [],
            }
            for key, title, summary, chart in composition
        ],
        'closing': _default_closing(language),
        'client_context': safe_context,
    }


def document_markdown(document, analysis):
    """Keep a readable archival representation; it is not used as layout input."""
    findings = {item['id']: item for item in analysis.get('findings') or []}
    lines = [f"# {document.get('title')}", '', document.get('subtitle') or '']
    for section in document.get('sections') or []:
        lines.extend(['', f"## {section.get('title')}", section.get('summary') or ''])
        for finding_id in section.get('finding_ids') or []:
            finding = findings.get(finding_id)
            if finding:
                lines.append(f"- {finding.get('observation')}")
    if document.get('closing'):
        lines.extend(['', document['closing']])
    return '\n'.join(line for line in lines if line is not None).strip() + '\n'


def _fallback_recommendations(findings, language='es-ES'):
    rows = []
    for index, finding in enumerate(findings[:5], start=1):
        rows.append({
            'id': f'recommendation-{index}',
            'title': (
                f"Validate and resolve: {finding.get('scope') or finding.get('theme')}"
                if language == 'en' else f"Validar y resolver: {finding.get('scope') or finding.get('theme')}"
            ),
            'priority': 'now' if finding.get('severity') in {'critical', 'high'} else 'next',
            'impact': 'high' if finding.get('severity') in {'critical', 'high'} else 'medium',
            'effort': 'medium', 'owner': 'Responsible team' if language == 'en' else 'Equipo responsable', 'dependencies': [],
            'sequence': ('Check examples, apply the change by pattern, and re-crawl.' if language == 'en' else 'Comprobar ejemplos, aplicar el cambio por patrón y volver a rastrear.'),
            'acceptance_criteria': ('The affected sample no longer reproduces the issue.' if language == 'en' else 'La muestra afectada deja de reproducir la incidencia.'),
            'validation': ('Run a verification crawl and compare the issue group.' if language == 'en' else 'Ejecutar un crawl de verificación y comparar el grupo de incidencias.'),
            'kpi': 'Affected URLs' if language == 'en' else 'URLs afectadas',
        })
    return rows


def _section_finding_ids(section_id, findings, all_ids):
    if section_id in {'priorities', 'backlog'}:
        return all_ids[:5]
    if section_id in {'decision-brief', 'opportunity'}:
        return all_ids[:3]
    theme_by_section = {
        'http': {'http', 'technical', 'crawlability'},
        'content': {'content', 'seo'},
        'performance': {'performance'},
    }
    themes = theme_by_section.get(section_id)
    if not themes:
        return []
    return [item['id'] for item in findings if item.get('theme') in themes][:5]


def _labels(language):
    if language == 'en':
        return {
            'coverage': 'Evidence coverage', 'coverage_summary': 'What was observed, what the denominators mean, and what remains outside the evidence.',
            'priorities': 'Priorities', 'priorities_summary': 'The most material patterns, ordered by evidence, scope, and practical next step.',
            'decision_brief': 'Decision brief', 'decision_summary': 'A concise view of where attention and ownership are required.',
            'roadmap': '30/60/90 roadmap', 'roadmap_summary': 'Sequence work from validation to repeatable implementation and verification.',
            'method': 'Method and limits', 'method_summary': 'This document reports crawl evidence, not Google index or business-performance claims.',
            'opportunity': 'Opportunity narrative', 'prospect': 'A factual basis for prioritising the first workstreams and deciding whether to proceed.',
            'existing_client': 'A factual basis for the next account decisions and expansion opportunities.',
            'workstreams': 'Recommended workstreams', 'workstreams_summary': 'Package action around evidence and verification rather than generic promises.',
            'next_step': 'Next step', 'next_step_summary': 'Align owners, access, and the first validation cycle.',
            'http': 'HTTP and crawlability', 'http_summary': 'Status distribution is shown against the unique-URL denominator.',
            'content': 'Content and metadata', 'content_summary': 'Recommendations distinguish missing elements from context-dependent optimisation opportunities.',
            'performance': 'Performance signals', 'performance_summary': 'Crawler request duration is separate from Lighthouse or field data.',
            'backlog': 'Implementation backlog', 'backlog_summary': 'Each item should have an owner, acceptance criteria, and a repeatable validation step.',
            'validation': 'Validation plan', 'validation_summary': 'Re-crawl after deployment and compare the same denominator and issue definition.',
        }
    return {
        'coverage': 'Cobertura y confianza', 'coverage_summary': 'Qué se observó, qué significan los denominadores y qué queda fuera de la evidencia.',
        'priorities': 'Prioridades', 'priorities_summary': 'Patrones relevantes ordenados por evidencia, alcance y siguiente paso verificable.',
        'decision_brief': 'Resumen de decisión', 'decision_summary': 'Una vista concisa de dónde hacen falta atención, responsables y decisiones.',
        'roadmap': 'Hoja de ruta 30/60/90', 'roadmap_summary': 'Secuenciar validación, implantación repetible y comprobación posterior.',
        'method': 'Método y límites', 'method_summary': 'Este documento informa evidencia de crawl, no afirma indexación en Google ni resultados de negocio.',
        'opportunity': 'Relato de oportunidad', 'prospect': 'Una base factual para priorizar el primer trabajo y decidir si avanzar.',
        'existing_client': 'Una base factual para las siguientes decisiones de cuenta y oportunidades de ampliación.',
        'workstreams': 'Líneas de trabajo recomendadas', 'workstreams_summary': 'Agrupar acciones alrededor de evidencia y verificación, no de promesas genéricas.',
        'next_step': 'Siguiente paso', 'next_step_summary': 'Alinear responsables, accesos y el primer ciclo de validación.',
        'http': 'HTTP y rastreabilidad', 'http_summary': 'La distribución de estados se presenta sobre el denominador de URLs únicas.',
        'content': 'Contenido y metadatos', 'content_summary': 'Las recomendaciones distinguen ausencias de oportunidades que dependen del contexto.',
        'performance': 'Señales de rendimiento', 'performance_summary': 'La duración del request del crawler es distinta de Lighthouse o datos de campo.',
        'backlog': 'Backlog de implementación', 'backlog_summary': 'Cada tarea debe tener responsable, aceptación y comprobación repetible.',
        'validation': 'Plan de validación', 'validation_summary': 'Volver a rastrear después del despliegue y comparar el mismo denominador y definición.',
    }


def _default_title(report_type, language):
    if language == 'en':
        return {'executive': 'Executive SEO audit', 'commercial': 'SEO opportunity report', 'technical': 'Technical SEO audit'}[report_type]
    return {'executive': 'Informe ejecutivo SEO', 'commercial': 'Informe comercial SEO', 'technical': 'Auditoría técnica SEO'}[report_type]


def _default_subtitle(report_type, commercial_context, language):
    if language == 'en':
        return 'Evidence-led review of crawl findings and prioritised next steps.'
    if report_type == 'commercial' and commercial_context == 'existing_client':
        return 'Revisión de evidencias para orientar las siguientes decisiones de cuenta.'
    return 'Revisión basada en evidencias del crawl y próximos pasos priorizados.'


def _default_closing(language):
    return 'Priorizar validación antes de escalar cambios y repetir el crawl para medir la evolución.' if language != 'en' else 'Validate before scaling changes, then repeat the crawl to measure progress.'


def _safe_client_context(value):
    value = value if isinstance(value, dict) else {}
    allowed = ('client_name', 'report_title', 'author', 'confidentiality', 'contact', 'business_goals', 'cta', 'market')
    return {key: _text(value.get(key)) for key in allowed if _text(value.get(key))}


def _quality_issue(code, severity, target, target_id, message, repair_instruction):
    return {
        'code': code, 'severity': severity, 'target': target, 'target_id': target_id,
        'message': message, 'repair_instruction': repair_instruction, 'source': 'deterministic',
    }


def _package_text_nodes(analysis, document):
    fields = ('observation', 'inference', 'hypothesis', 'limitation', 'title', 'sequence', 'acceptance_criteria', 'validation', 'kpi')
    for item in (analysis.get('findings') or []) + (analysis.get('recommendations') or []):
        for field in fields:
            if item.get(field):
                yield 'analysis', item.get('id'), str(item[field])
    for field in ('title', 'subtitle', 'closing'):
        if document.get(field):
            yield 'document', None, str(document[field])
    for section in document.get('sections') or []:
        for field in ('title', 'summary'):
            if section.get(field):
                yield 'document', section.get('id'), str(section[field])
        for action in section.get('actions') or []:
            yield 'document', section.get('id'), str(action)


def _dedupe_quality_issues(issues):
    result = []
    seen = set()
    for item in issues:
        key = (item.get('code'), item.get('target'), item.get('target_id'), item.get('message'))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _same_number(left, right):
    try:
        return abs(float(left) - float(right)) < 1e-9
    except (TypeError, ValueError):
        return False


def _evidence_ids(facts):
    return {row.get('id') for group in (facts.get('evidence') or {}).values() for row in (group or []) if row.get('id')}


def _section_limit(report_type):
    return {'executive': 6, 'commercial': 9, 'technical': 16}.get(report_type, 6)


def _chart_names():
    return {'coverage', 'status_codes', 'issue_groups', 'performance_percentiles', 'impact_effort', 'roadmap', None}


def _id(value, prefix, index):
    value = _slug(value)
    return value or f'{prefix}-{index}'


def _slug(value):
    value = re.sub(r'[^a-z0-9]+', '-', str(value or '').lower()).strip('-')
    return value[:64]


def _text(value):
    return str(value).strip()[:2000] if value is not None else ''


def _string_list(value):
    if not isinstance(value, (list, tuple)):
        return []
    return [_text(item) for item in value if _text(item)][:20]


def _choice(value, allowed, default):
    value = str(value or '').strip().lower()
    return value if value in allowed else default


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _severity(value):
    value = str(value or '').lower()
    return 'high' if value == 'error' else 'medium' if value == 'warning' else 'low'
