import unittest
from unittest import mock

from src import reporting_settings
from src.openrouter_client import (
    OpenRouterClient,
    generate_report_markdown,
    generate_structured_findings,
    refresh_models,
)
from src.reporting_prompts import get_prompt_bundle


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class OpenRouterClientTest(unittest.TestCase):
    @mock.patch('src.openrouter_client.requests.post')
    def test_chat_completion_posts_expected_payload(self, post):
        post.return_value = FakeResponse(payload={
            'choices': [{'message': {'content': 'ok'}}],
            'usage': {'total_tokens': 3},
        })
        client = OpenRouterClient('sk-test', base_url='https://example.test/api/v1', timeout=12)

        result = client.chat_completion(
            'openai/gpt-4.1',
            [{'role': 'user', 'content': 'hello'}],
            {'max_tokens': 100},
        )

        self.assertEqual(result['choices'][0]['message']['content'], 'ok')
        post.assert_called_once_with(
            'https://example.test/api/v1/chat/completions',
            headers={
                'Authorization': 'Bearer sk-test',
                'Content-Type': 'application/json',
            },
            json={
                'model': 'openai/gpt-4.1',
                'messages': [{'role': 'user', 'content': 'hello'}],
                'max_tokens': 100,
            },
            timeout=12,
        )

    @mock.patch('src.openrouter_client.requests.post')
    def test_chat_completion_raises_clear_error_on_http_and_bad_json(self, post):
        client = OpenRouterClient('sk-test', base_url='https://example.test')
        post.return_value = FakeResponse(status_code=401, payload={'error': {'message': 'bad key'}})

        with self.assertRaisesRegex(RuntimeError, 'OpenRouter HTTP 401: bad key'):
            client.chat_completion('openai/gpt-4.1', [])

        post.return_value = FakeResponse(status_code=429, payload=ValueError('no json'), text='rate limited')
        with self.assertRaisesRegex(RuntimeError, 'OpenRouter HTTP 429: rate limited'):
            client.chat_completion('openai/gpt-4.1', [])

        post.return_value = FakeResponse(payload=ValueError('no json'), text='not json')
        with self.assertRaisesRegex(RuntimeError, 'OpenRouter returned invalid JSON'):
            client.chat_completion('openai/gpt-4.1', [])

    @mock.patch('src.openrouter_client.requests.post')
    def test_chat_completion_does_not_allow_params_to_override_reserved_payload(self, post):
        post.return_value = FakeResponse(payload={'choices': [{'message': {'content': 'ok'}}]})
        client = OpenRouterClient('sk-test', base_url='https://example.test')

        client.chat_completion(
            'openai/gpt-4.1',
            [{'role': 'user', 'content': 'hello'}],
            {'model': 'other/model', 'messages': [], 'max_tokens': 10},
        )

        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['model'], 'openai/gpt-4.1')
        self.assertEqual(payload['messages'], [{'role': 'user', 'content': 'hello'}])
        self.assertEqual(payload['max_tokens'], 10)

    @mock.patch('src.openrouter_client.reporting_settings.save_openrouter_models')
    @mock.patch('src.openrouter_client.requests.get')
    def test_refresh_models_filters_and_saves_supported_models(self, get, save_models):
        get.return_value = FakeResponse(payload={'data': [
            {'id': 'openai/gpt-4.1', 'provider': 'openai'},
            {'id': 'deepseek/chat', 'provider': 'deepseek'},
            {'id': 'google/gemini', 'provider': 'google'},
        ]})

        models = refresh_models('sk-test', base_url='https://example.test/api/v1', timeout=3)

        self.assertEqual([model['id'] for model in models], ['openai/gpt-4.1', 'deepseek/chat'])
        save_models.assert_called_once_with(models)
        get.assert_called_once_with(
            'https://example.test/api/v1/models',
            headers={'Authorization': 'Bearer sk-test'},
            timeout=3,
        )

    def test_generation_helpers_filter_params_and_return_content_usage(self):
        client = mock.Mock()
        client.chat_completion.return_value = {
            'choices': [{'message': {'content': 'generated text'}}],
            'usage': {'prompt_tokens': 11},
        }
        prompt_bundle = get_prompt_bundle('en', 'technical')
        metadata = {'supported_parameters': ['max_tokens']}
        packet = {'crawl_id': 5, 'samples': {'urls': {'rows': []}}}

        content, usage = generate_structured_findings(
            client,
            'openai/gpt-4.1',
            packet,
            prompt_bundle,
            metadata,
        )

        self.assertEqual(content, 'generated text')
        self.assertEqual(usage, {'prompt_tokens': 11})
        _, messages, params = client.chat_completion.call_args.args
        self.assertEqual(params, {'max_tokens': 2000})
        self.assertIn('Technical tone', messages[0]['content'])
        self.assertIn('"crawl_id": 5', messages[1]['content'])

        client.reset_mock()
        generate_report_markdown(client, 'openai/gpt-4.1', packet, 'findings', prompt_bundle, metadata)
        _, messages, params = client.chat_completion.call_args.args
        self.assertEqual(params, {'max_tokens': 4000})
        self.assertIn('findings', messages[1]['content'])
        self.assertNotIn('temperature', params)

    def test_generation_helpers_can_load_model_metadata_from_cache(self):
        client = mock.Mock()
        client.chat_completion.return_value = {
            'choices': [{'message': {'content': 'cached'}}],
            'usage': {},
        }

        with mock.patch.object(reporting_settings, 'get_openrouter_model', return_value={
            'supported_parameters': ['max_tokens', 'temperature'],
        }) as get_model:
            content, _ = generate_report_markdown(
                client,
                'anthropic/claude',
                {'crawl_id': 9},
                'findings',
                get_prompt_bundle(),
            )

        self.assertEqual(content, 'cached')
        get_model.assert_called_once_with('anthropic/claude')
        _, _, params = client.chat_completion.call_args.args
        self.assertEqual(params, {'max_tokens': 4000, 'temperature': 0.2})


if __name__ == '__main__':
    unittest.main()
