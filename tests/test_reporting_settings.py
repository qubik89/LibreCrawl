import os
import tempfile
import unittest
from io import BytesIO

from PIL import Image

from src import reporting_settings
from src import report_assets


class ReportingSettingsTest(unittest.TestCase):
    def setUp(self):
        old_db_file = reporting_settings.DB_FILE
        self.addCleanup(reporting_settings.set_db_file, old_db_file)
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        reporting_settings.set_db_file(os.path.join(tmpdir.name, 'reporting.db'))
        reporting_settings.init_reporting_tables()
        self.addCleanup(self._cleanup_asset_root)

    def _cleanup_asset_root(self):
        import shutil
        shutil.rmtree(report_assets.ASSET_ROOT, ignore_errors=True)

    def test_filter_openrouter_models_keeps_only_supported_providers(self):
        models = [
            {'id': 'openai/gpt-4.1', 'provider': 'openai', 'architecture': {'input_modalities': ['text']}},
            {'id': 'anthropic/claude-sonnet-4', 'provider': 'anthropic'},
            {'id': 'deepseek/deepseek-r1', 'provider': 'deepseek'},
            {'id': 'openai/image-only', 'provider': 'openai', 'architecture': {'input_modalities': ['image'], 'output_modalities': ['image']}},
            {'id': 'mistral/mistral-small', 'provider': 'mistral'},
            {'id': 'google/gemini-2.5-pro', 'provider': 'google'},
        ]

        filtered = reporting_settings.filter_openrouter_models(models)

        self.assertEqual([model['id'] for model in filtered], [
            'openai/gpt-4.1',
            'anthropic/claude-sonnet-4',
            'deepseek/deepseek-r1',
        ])

    def test_safe_generation_params_keeps_supported_max_tokens(self):
        model_metadata = {
            'supported_parameters': ['max_tokens', 'temperature', 'top_p', 'response_format'],
        }
        desired_params = {
            'max_tokens': 1200,
            'temperature': 0.2,
            'top_p': 0.9,
            'reasoning': {'effort': 'low'},
            'unused': True,
        }

        safe_params = reporting_settings.safe_generation_params(model_metadata, desired_params)

        self.assertEqual(safe_params, {
            'max_tokens': 1200,
            'temperature': 0.2,
            'top_p': 0.9,
        })

    def test_safe_generation_params_drops_unsupported_max_tokens(self):
        model_metadata = {
            'supported_parameters': ['temperature', 'top_p'],
        }
        desired_params = {
            'max_tokens': 1200,
            'temperature': 0.2,
            'top_p': 0.9,
            'reasoning': {'effort': 'low'},
        }

        safe_params = reporting_settings.safe_generation_params(model_metadata, desired_params)

        self.assertEqual(safe_params, {
            'temperature': 0.2,
            'top_p': 0.9,
        })

    def test_safe_generation_params_keeps_only_max_tokens_without_metadata(self):
        self.assertEqual(
            reporting_settings.safe_generation_params(None, {
                'max_tokens': 900,
                'temperature': 0.3,
            }),
            {'max_tokens': 900},
        )

    def test_settings_and_model_cache_roundtrip(self):
        reporting_settings.save_reporting_settings({
            'openrouter_api_key': 'sk-test',
            'default_model': 'openai/gpt-4.1',
            'agency_name': 'Mitmore SEO Crawl',
        })
        with reporting_settings.get_db() as conn:
            conn.execute(
                'INSERT INTO reporting_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)',
                ('unexpected_key', 'should-not-escape', 1234567890),
            )

        reporting_settings.save_openrouter_models([{
            'id': 'openai/gpt-4.1',
            'provider': 'openai',
            'name': 'GPT-4.1',
            'context_length': 128000,
            'pricing': {'prompt': '0.01', 'completion': '0.03'},
            'supported_parameters': ['temperature', 'top_p'],
        }])

        self.assertEqual(reporting_settings.get_reporting_setting('openrouter_api_key'), 'sk-test')
        self.assertEqual(reporting_settings.get_reporting_setting('default_model'), 'openai/gpt-4.1')

        settings = reporting_settings.get_reporting_settings()
        self.assertEqual(settings['agency_name'], 'Mitmore SEO Crawl')
        self.assertEqual(settings['footer_text'], None)
        self.assertNotIn('unexpected_key', settings)

        model = reporting_settings.get_openrouter_model('openai/gpt-4.1')
        self.assertIsNotNone(model)
        self.assertEqual(model['provider'], 'openai')
        self.assertEqual(model['pricing'], {'prompt': '0.01', 'completion': '0.03'})
        self.assertEqual(model['supported_parameters'], ['temperature', 'top_p'])
        self.assertEqual([row['id'] for row in reporting_settings.list_openrouter_models()], ['openai/gpt-4.1'])

    def test_corrupt_cached_json_does_not_crash_model_reads(self):
        with reporting_settings.get_db() as conn:
            conn.execute('''
                INSERT INTO openrouter_models (
                    id, provider, name, context_length,
                    pricing_json, supported_parameters_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                'openai/bad-json',
                'openai',
                'Bad JSON',
                8192,
                '{not-valid-json',
                '["temperature",',
                1234567890,
            ))

        model = reporting_settings.get_openrouter_model('openai/bad-json')
        self.assertIsNotNone(model)
        self.assertIsNone(model['pricing'])
        self.assertEqual(model['supported_parameters'], [])
        self.assertEqual(reporting_settings.list_openrouter_models()[0]['supported_parameters'], [])

    def test_user_profiles_are_isolated_and_default_to_v2_pack(self):
        first = reporting_settings.get_user_reporting_settings(10)
        second = reporting_settings.get_user_reporting_settings(11)
        self.assertEqual(first['default_report_mode'], 'pack')
        self.assertEqual(first['default_model'], 'anthropic/claude-opus-4.7')
        self.assertIsNone(first['openrouter_api_key'])

        reporting_settings.save_user_reporting_settings(10, {
            'openrouter_api_key': 'sk-user-10', 'issuer_name': 'Agency 10',
            'default_report_mode': 'technical', 'primary_color': '#123456',
        })
        self.assertEqual(reporting_settings.get_user_reporting_settings(10)['issuer_name'], 'Agency 10')
        self.assertEqual(reporting_settings.get_user_reporting_settings(10)['default_report_mode'], 'technical')
        self.assertIsNone(reporting_settings.get_user_reporting_settings(11)['openrouter_api_key'])
        self.assertEqual(second['default_report_mode'], 'pack')

    def test_legacy_settings_migrate_only_when_requested(self):
        reporting_settings.save_reporting_settings({
            'openrouter_api_key': 'sk-legacy', 'default_model': 'openai/gpt-4.1',
            'default_tone': 'commercial', 'agency_name': 'Legacy Agency',
            'footer_text': 'Internal',
        })
        profile = reporting_settings.get_user_reporting_settings(10, migrate_legacy=True)
        self.assertEqual(profile['openrouter_api_key'], 'sk-legacy')
        self.assertEqual(profile['default_report_mode'], 'commercial')
        self.assertEqual(profile['issuer_name'], 'Legacy Agency')
        self.assertEqual(profile['default_confidentiality'], 'Internal')
        self.assertIsNone(reporting_settings.get_user_reporting_settings(11)['openrouter_api_key'])

    def test_legacy_product_brand_is_not_migrated_into_white_label_profile(self):
        reporting_settings.save_reporting_settings({'agency_name': 'Mitmore SEO Crawl'})
        profile = reporting_settings.get_user_reporting_settings(10, migrate_legacy=True)
        self.assertIsNone(profile['issuer_name'])

    def test_logo_is_reencoded_and_owned_by_user(self):
        image = Image.new('RGBA', (32, 20), (12, 34, 56, 255))
        source = BytesIO()
        image.save(source, format='PNG')
        source.seek(0)

        class Upload:
            filename = 'logo.png'

            def read(self, size=-1):
                return source.read(size)

        path, mime, name = report_assets.sanitise_logo(Upload(), 10)
        asset_id = reporting_settings.create_report_asset(10, path, mime, name)
        asset = reporting_settings.get_report_asset(asset_id, 10)
        self.assertEqual(asset['mime_type'], 'image/png')
        self.assertIsNone(reporting_settings.get_report_asset(asset_id, 11))
        self.assertTrue(report_assets.asset_data_uri(asset).startswith('data:image/png;base64,'))
        deleted = reporting_settings.delete_report_asset(asset_id, 10)
        report_assets.remove_asset_file(deleted)
        self.assertFalse(path.exists())

    def test_logo_rejects_corrupt_payload_and_dimensions(self):
        class Corrupt:
            filename = 'logo.png'
            def read(self, size=-1):
                return b'not-an-image'

        with self.assertRaises(ValueError):
            report_assets.sanitise_logo(Corrupt(), 10)

        source = BytesIO()
        Image.new('RGB', (4097, 1), (1, 2, 3)).save(source, format='PNG')
        source.seek(0)

        class TooWide:
            filename = 'logo.png'
            def read(self, size=-1):
                return source.read(size)

        with self.assertRaises(ValueError):
            report_assets.sanitise_logo(TooWide(), 10)


if __name__ == '__main__':
    unittest.main()
