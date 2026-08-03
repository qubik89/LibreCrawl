"""Thin OpenRouter client for AI report generation."""

import json

import requests

from src import reporting_settings


DEFAULT_BASE_URL = 'https://openrouter.ai/api/v1'


class OpenRouterClient:
    def __init__(self, api_key, base_url=None, timeout=120):
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip('/')
        self.timeout = timeout

    def chat_completion(self, model, messages, params=None):
        """POST a chat completion request and return parsed JSON."""
        params = _safe_payload_params(params or {})
        payload = {
            'model': model,
            'messages': messages,
        }
        payload.update(params)

        try:
            response = requests.post(
                f'{self.base_url}/chat/completions',
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                },
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f'OpenRouter request failed: {exc}') from exc

        data = _response_json(response, allow_invalid=response.status_code >= 400)
        if response.status_code >= 400:
            raise RuntimeError(f'OpenRouter HTTP {response.status_code}: {_error_message(data, response.text)}')
        if isinstance(data, dict) and data.get('error'):
            raise RuntimeError(f"OpenRouter API error: {_error_message(data, response.text)}")
        return data


def refresh_models(api_key, base_url=None, timeout=30):
    """Fetch, filter, cache, and return supported OpenRouter models."""
    base_url = (base_url or DEFAULT_BASE_URL).rstrip('/')
    try:
        response = requests.get(
            f'{base_url}/models',
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f'OpenRouter models request failed: {exc}') from exc

    data = _response_json(response, allow_invalid=response.status_code >= 400)
    if response.status_code >= 400:
        raise RuntimeError(f'OpenRouter HTTP {response.status_code}: {_error_message(data, response.text)}')

    models = data.get('data', data) if isinstance(data, dict) else data
    if not isinstance(models, list):
        raise RuntimeError('OpenRouter models response did not contain a model list')

    filtered = reporting_settings.filter_openrouter_models(models)
    reporting_settings.save_openrouter_models(filtered)
    return filtered


def generate_structured_findings(client, model, audit_packet, prompt_bundle, model_metadata=None):
    """Generate structured findings text from one bounded audit packet."""
    messages = [
        {
            'role': 'system',
            'content': _system_prompt(prompt_bundle, 'audit_analysis_system_prompt'),
        },
        {
            'role': 'user',
            'content': 'Audit packet JSON:\n' + _json_text(audit_packet),
        },
    ]
    return _generate(client, model, messages, model_metadata, {'max_tokens': 2000, 'temperature': 0.1})


def generate_report_markdown(client, model, audit_packet, structured_findings, prompt_bundle, model_metadata=None):
    """Generate client-ready Markdown from structured findings and crawl evidence."""
    messages = [
        {
            'role': 'system',
            'content': _system_prompt(prompt_bundle, 'report_writer_system_prompt'),
        },
        {
            'role': 'user',
            'content': (
                'Structured findings:\n'
                f'{structured_findings}\n\n'
                'Audit packet JSON:\n'
                f'{_json_text(audit_packet)}'
            ),
        },
    ]
    return _generate(client, model, messages, model_metadata, {'max_tokens': 4000, 'temperature': 0.2})


def generate_report_analysis(client, model, audit_facts, prompt_bundle, model_metadata=None):
    """Generate the V2 evidence analysis as a JSON object response."""
    messages = [
        {'role': 'system', 'content': _system_prompt(prompt_bundle, 'audit_analysis_system_prompt')},
        {'role': 'user', 'content': 'AuditFactsV2 JSON:\n' + _json_text(_model_facts(audit_facts))},
    ]
    return _generate_structured(client, model, messages, model_metadata, {
        'max_tokens': 48000,
        'temperature': 0.1,
        'reasoning': {'max_tokens': 8000, 'exclude': True},
    }, 'seo_audit_analysis', _analysis_schema())


def generate_report_document(client, model, audit_facts, analysis, prompt_bundle, client_context=None,
                             model_metadata=None):
    """Generate the V2 editorial composition as JSON, never as presentation HTML."""
    messages = [
        {'role': 'system', 'content': _system_prompt(prompt_bundle, 'report_writer_system_prompt')},
        {
            'role': 'user',
            'content': (
                f"Report type: {prompt_bundle.get('report_type')}\n"
                f"Commercial context: {prompt_bundle.get('commercial_context')}\n"
                'Client context JSON:\n' + _json_text(client_context or {}) + '\n\n'
                'Validated analysis JSON:\n' + _json_text(analysis) + '\n\n'
                'AuditFactsV2 JSON:\n' + _json_text(_model_facts(audit_facts))
            ),
        },
    ]
    token_budget = {'executive': 18000, 'commercial': 26000, 'technical': 45000}.get(
        prompt_bundle.get('report_type'), 18000
    )
    reasoning_budget = {'executive': 4000, 'commercial': 6000, 'technical': 8000}.get(
        prompt_bundle.get('report_type'), 4000
    )
    return _generate_structured(client, model, messages, model_metadata, {
        'max_tokens': token_budget,
        'temperature': 0.15,
        'reasoning': {'max_tokens': reasoning_budget, 'exclude': True},
    }, 'seo_report_document', _document_schema())


def generate_report_quality_review(client, model, audit_facts, analysis, document, prompt_bundle,
                                   deterministic_review=None, model_metadata=None):
    """Run an independent JSON-only review after deterministic validation."""
    messages = [
        {'role': 'system', 'content': _system_prompt(prompt_bundle, 'quality_review_system_prompt')},
        {
            'role': 'user',
            'content': (
                'Deterministic review JSON:\n' + _json_text(deterministic_review or {}) + '\n\n'
                'Analysis JSON:\n' + _json_text(analysis) + '\n\n'
                'Document JSON:\n' + _json_text(document) + '\n\n'
                'AuditFactsV2 JSON:\n' + _json_text(_model_facts(audit_facts))
            ),
        },
    ]
    return _generate_structured(client, model, messages, model_metadata, {
        'max_tokens': 12000,
        'temperature': 0,
        'reasoning': {'max_tokens': 5000, 'exclude': True},
    }, 'seo_quality_review', _quality_schema())


def generate_report_repair(client, model, audit_facts, analysis, document, quality_review, prompt_bundle,
                           client_context=None, model_metadata=None):
    """Request the single permitted repair pass for one report package."""
    messages = [
        {'role': 'system', 'content': _system_prompt(prompt_bundle, 'repair_system_prompt')},
        {
            'role': 'user',
            'content': (
                f"Report type: {prompt_bundle.get('report_type')}\n"
                f"Commercial context: {prompt_bundle.get('commercial_context')}\n"
                'Client context JSON:\n' + _json_text(client_context or {}) + '\n\n'
                'Quality issues JSON:\n' + _json_text(quality_review) + '\n\n'
                'Current analysis JSON:\n' + _json_text(analysis) + '\n\n'
                'Current document JSON:\n' + _json_text(document) + '\n\n'
                'Immutable AuditFactsV2 JSON:\n' + _json_text(_model_facts(audit_facts))
            ),
        },
    ]
    token_budget = {'executive': 24000, 'commercial': 34000, 'technical': 56000}.get(
        prompt_bundle.get('report_type'), 24000
    )
    reasoning_budget = {'executive': 6000, 'commercial': 8000, 'technical': 10000}.get(
        prompt_bundle.get('report_type'), 6000
    )
    return _generate_structured(client, model, messages, model_metadata, {
        'max_tokens': token_budget,
        'temperature': 0,
        'reasoning': {'max_tokens': reasoning_budget, 'exclude': True},
    }, 'seo_report_repair', _repair_schema())


def _generate(client, model, messages, model_metadata, desired_params):
    if model_metadata is None:
        model_metadata = reporting_settings.get_openrouter_model(model)
    params = reporting_settings.safe_generation_params(model_metadata, desired_params)
    data = client.chat_completion(model, messages, params)
    return _content(data), data.get('usage')


def _generate_structured(client, model, messages, model_metadata, desired_params, schema_name, schema):
    """Generate schema-bound JSON and retry one malformed or truncated response."""
    if model_metadata is None:
        model_metadata = reporting_settings.get_openrouter_model(model)
    desired = dict(desired_params)
    desired['response_format'] = {
        'type': 'json_schema',
        'json_schema': {'name': schema_name, 'strict': True, 'schema': schema},
    }
    desired['provider'] = {'require_parameters': True}
    attempts = []
    content = ''
    for attempt in range(2):
        params = reporting_settings.safe_generation_params(model_metadata, desired)
        data = client.chat_completion(model, messages, params)
        content = _content(data)
        attempts.append(_generation_usage(data))
        if _valid_structured_content(content, schema) and _finish_reason(data) != 'length':
            return content, _combined_attempt_usage(attempts)
        if attempt == 0 and 'max_tokens' in desired:
            desired['max_tokens'] = min(
                64000,
                max(desired['max_tokens'] + 4000, int(desired['max_tokens'] * 1.5)),
            )
    return content, _combined_attempt_usage(attempts)


def _generation_usage(data):
    usage = dict(data.get('usage') or {})
    response = {
        'id': data.get('id'),
        'provider': data.get('provider'),
        'finish_reason': _finish_reason(data),
        'native_finish_reason': _native_finish_reason(data),
    }
    response = {key: value for key, value in response.items() if value not in (None, '')}
    if response:
        usage['_response'] = response
    return usage


def _combined_attempt_usage(attempts):
    if len(attempts) == 1:
        return attempts[0]
    combined = {}
    for usage in attempts:
        for key, value in usage.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                combined[key] = combined.get(key, 0) + value
    combined['_attempts'] = attempts
    combined['_response'] = dict((attempts[-1] or {}).get('_response') or {})
    return combined


def _finish_reason(data):
    try:
        return data['choices'][0].get('finish_reason')
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def _native_finish_reason(data):
    try:
        return data['choices'][0].get('native_finish_reason')
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def _valid_structured_content(content, schema):
    try:
        value = json.loads(str(content or '').strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return _matches_schema(value, schema)


def _matches_schema(value, schema):
    expected = schema.get('type')
    if isinstance(expected, list):
        if value is None and 'null' in expected:
            return True
        expected = next((item for item in expected if item != 'null'), None)
    if expected == 'object':
        if not isinstance(value, dict):
            return False
        required = schema.get('required') or []
        if any(key not in value for key in required):
            return False
        properties = schema.get('properties') or {}
        if schema.get('additionalProperties') is False and any(key not in properties for key in value):
            return False
        return all(_matches_schema(item, properties[key]) for key, item in value.items() if key in properties)
    if expected == 'array':
        if not isinstance(value, list) or len(value) > schema.get('maxItems', len(value)):
            return False
        return all(_matches_schema(item, schema.get('items') or {}) for item in value)
    if expected == 'string' and not isinstance(value, str):
        return False
    if expected == 'integer' and (not isinstance(value, int) or isinstance(value, bool)):
        return False
    if expected == 'number' and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return False
    if expected == 'boolean' and not isinstance(value, bool):
        return False
    if 'enum' in schema and value not in schema['enum']:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 'minimum' in schema and value < schema['minimum']:
            return False
        if 'maximum' in schema and value > schema['maximum']:
            return False
    return True


def _model_facts(audit_facts):
    from src.reporting_v2 import model_audit_facts
    return model_audit_facts(audit_facts)


def _analysis_schema():
    finding = _strict_object({
        'id': {'type': 'string'}, 'theme': {'type': 'string'},
        'severity': {'type': 'string', 'enum': ['critical', 'high', 'medium', 'low', 'opportunity']},
        'confidence': {'type': 'string', 'enum': ['high', 'medium', 'low']},
        'observation': {'type': 'string'}, 'inference': {'type': 'string'},
        'hypothesis': {'type': 'string'}, 'scope': {'type': 'string'},
        'numerator': _nullable('integer'), 'denominator': _nullable('integer'),
        'metric_id': {'type': 'string'}, 'evidence_ids': _string_array(),
        'limitation': {'type': 'string'}, 'recommendation_ids': _string_array(),
    })
    recommendation = _strict_object({
        'id': {'type': 'string'}, 'title': {'type': 'string'},
        'priority': {'type': 'string', 'enum': ['now', 'next', 'later']},
        'impact': {'type': 'string', 'enum': ['high', 'medium', 'low']},
        'effort': {'type': 'string', 'enum': ['high', 'medium', 'low']},
        'owner': {'type': 'string'}, 'dependencies': _string_array(),
        'sequence': {'type': 'string'}, 'acceptance_criteria': {'type': 'string'},
        'validation': {'type': 'string'}, 'kpi': {'type': 'string'},
    })
    return _strict_object({
        'findings': {'type': 'array', 'items': finding},
        'recommendations': {'type': 'array', 'items': recommendation},
        'limitations': _string_array(),
    })


def _document_schema():
    section = _strict_object({
        'id': {'type': 'string'}, 'title': {'type': 'string'}, 'summary': {'type': 'string'},
        'finding_ids': _string_array(), 'chart': _nullable('string'), 'actions': _string_array(),
    })
    return _strict_object({
        'title': {'type': 'string'}, 'subtitle': {'type': 'string'},
        'sections': {'type': 'array', 'items': section},
        'closing': {'type': 'string'},
    })


def _quality_schema():
    issue = _strict_object({
        'code': {'type': 'string'},
        'severity': {'type': 'string', 'enum': ['critical', 'major', 'minor']},
        'target': {'type': 'string', 'enum': ['analysis', 'document']},
        'target_id': _nullable('string'), 'message': {'type': 'string'},
        'repair_instruction': {'type': 'string'},
    })
    return _strict_object({
        'verdict': {'type': 'string', 'enum': ['pass', 'fail']},
        'score': {'type': 'integer'},
        'issues': {'type': 'array', 'items': issue},
    })


def _repair_schema():
    return _strict_object({'analysis': _analysis_schema(), 'document': _document_schema()})


def _strict_object(properties):
    return {
        'type': 'object', 'properties': properties,
        'required': list(properties), 'additionalProperties': False,
    }


def _nullable(value_type):
    return {'type': [value_type, 'null']}


def _string_array():
    return {'type': 'array', 'items': {'type': 'string'}}


def _system_prompt(prompt_bundle, prompt_key):
    return '\n\n'.join([
        prompt_bundle[prompt_key],
        prompt_bundle.get('language_prompt', ''),
        prompt_bundle.get('tone_prompt', ''),
    ]).strip()


def _content(data):
    try:
        return data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError('OpenRouter response did not contain message content') from exc


def _response_json(response, allow_invalid=False):
    try:
        return response.json()
    except ValueError as exc:
        if allow_invalid:
            return None
        raise RuntimeError(f'OpenRouter returned invalid JSON: {response.text}') from exc


def _error_message(data, fallback):
    if isinstance(data, dict):
        error = data.get('error')
        if isinstance(error, dict):
            return error.get('message') or json.dumps(error)
        if error:
            return str(error)
    return fallback or 'unknown error'


def _json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_payload_params(params):
    return {
        key: value for key, value in params.items()
        if key not in {'model', 'messages'}
    }
