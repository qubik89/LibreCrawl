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
        {'role': 'user', 'content': 'AuditFactsV2 JSON:\n' + _json_text(audit_facts)},
    ]
    return _generate(client, model, messages, model_metadata, {
        'max_tokens': 6000,
        'temperature': 0.1,
        'response_format': {'type': 'json_object'},
    })


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
                'AuditFactsV2 JSON:\n' + _json_text(audit_facts)
            ),
        },
    ]
    token_budget = {'executive': 6000, 'commercial': 9000, 'technical': 16000}.get(
        prompt_bundle.get('report_type'), 6000
    )
    return _generate(client, model, messages, model_metadata, {
        'max_tokens': token_budget,
        'temperature': 0.15,
        'response_format': {'type': 'json_object'},
    })


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
                'AuditFactsV2 JSON:\n' + _json_text(audit_facts)
            ),
        },
    ]
    return _generate(client, model, messages, model_metadata, {
        'max_tokens': 4000,
        'temperature': 0,
        'response_format': {'type': 'json_object'},
    })


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
                'Immutable AuditFactsV2 JSON:\n' + _json_text(audit_facts)
            ),
        },
    ]
    token_budget = {'executive': 7000, 'commercial': 10000, 'technical': 18000}.get(
        prompt_bundle.get('report_type'), 7000
    )
    return _generate(client, model, messages, model_metadata, {
        'max_tokens': token_budget,
        'temperature': 0,
        'response_format': {'type': 'json_object'},
    })


def _generate(client, model, messages, model_metadata, desired_params):
    if model_metadata is None:
        model_metadata = reporting_settings.get_openrouter_model(model)
    params = reporting_settings.safe_generation_params(model_metadata, desired_params)
    data = client.chat_completion(model, messages, params)
    return _content(data), data.get('usage')


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
