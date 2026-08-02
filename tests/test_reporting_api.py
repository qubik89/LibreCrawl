import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


def import_main():
    fake_flask = types.ModuleType('flask')

    class FakeFlask:
        def __init__(self, *args, **kwargs):
            self.secret_key = None

        def route(self, *args, **kwargs):
            return lambda func: func

    fake_flask.Flask = FakeFlask
    fake_flask.render_template = mock.Mock()
    fake_flask.request = types.SimpleNamespace(path='', get_json=lambda silent=True: {})
    fake_flask.jsonify = lambda value=None, **kwargs: value if value is not None else kwargs
    fake_flask.session = {}
    fake_flask.redirect = mock.Mock()
    fake_flask.url_for = mock.Mock(return_value='/login')
    fake_flask.send_file = mock.Mock()

    class FakeResponse:
        def __init__(self, response=None, content_type=None, headers=None, **_kwargs):
            self.response = response
            self.content_type = content_type
            self.headers = headers or {}

    fake_flask.Response = FakeResponse
    fake_flask.stream_with_context = lambda iterable: iterable

    fake_flask_compress = types.ModuleType('flask_compress')
    fake_flask_compress.Compress = mock.Mock()

    fake_dotenv = types.ModuleType('dotenv')
    fake_dotenv.load_dotenv = mock.Mock()

    fake_crawler = types.ModuleType('src.crawler')
    fake_crawler.WebCrawler = mock.Mock()

    fake_settings_manager = types.ModuleType('src.settings_manager')
    fake_settings_manager.SettingsManager = mock.Mock()

    fake_auth_db = types.ModuleType('src.auth_db')
    for name in (
        'init_db',
        'create_user',
        'authenticate_user',
        'get_user_by_id',
        'log_guest_crawl',
        'get_guest_crawls_last_24h',
        'verify_user',
        'set_user_tier',
        'create_verification_token',
        'verify_token',
        'get_user_by_email',
    ):
        setattr(fake_auth_db, name, mock.Mock())

    fake_email_service = types.ModuleType('src.email_service')
    fake_email_service.send_verification_email = mock.Mock()
    fake_email_service.send_welcome_email = mock.Mock()

    with mock.patch.object(sys, 'argv', ['main.py']):
        with mock.patch.dict(os.environ, {'SECRET_KEY': 'test-secret'}):
            with mock.patch.dict(sys.modules, {
                'flask': fake_flask,
                'flask_compress': fake_flask_compress,
                'dotenv': fake_dotenv,
                'src.crawler': fake_crawler,
                'src.settings_manager': fake_settings_manager,
                'src.auth_db': fake_auth_db,
                'src.email_service': fake_email_service,
            }):
                return importlib.import_module('main')


main = import_main()


class ReportingApiHelperTest(unittest.TestCase):
    def setUp(self):
        main.session.clear()
        main.request.path = ''
        main.get_user_by_id.reset_mock()
        main.get_user_by_id.return_value = {'id': 5, 'username': 'test'}

    def test_public_report_settings_hides_openrouter_key(self):
        settings = {
            'openrouter_api_key': 'sk-live-1234567890',
            'default_model': 'openai/gpt-4.1',
        }

        public = main.public_report_settings(settings)

        self.assertEqual(public['openrouter_api_key'], '')
        self.assertTrue(public['has_openrouter_api_key'])
        self.assertEqual(public['masked_openrouter_api_key'], 'sk-l...7890')
        self.assertEqual(public['default_model'], 'openai/gpt-4.1')

    def test_login_required_rejects_stale_user_session_for_api(self):
        main.session.update({'user_id': 999, 'username': 'stale', 'tier': 'admin'})
        main.request.path = '/api/start_crawl'
        main.get_user_by_id.return_value = None

        response = main.login_required(lambda: {'ok': True})()

        self.assertEqual(response, ({'success': False, 'error': 'Autenticación requerida'}, 401))
        self.assertEqual(main.session, {})

    def test_page_limit_and_offset_are_clamped(self):
        self.assertEqual(main._page_limit(-1), 1)
        self.assertEqual(main._page_limit(5000), 1000)
        self.assertEqual(main._page_limit('bad'), 500)
        self.assertEqual(main._page_offset(-10), 0)
        self.assertEqual(main._page_offset('bad'), 0)

    def test_visualization_reads_clickhouse_rows_and_builds_edges(self):
        main.session.update({'user_id': 5, 'username': 'test', 'tier': 'admin', 'session_id': 'session-a', 'current_crawl_id': 42})
        main.request.path = '/api/visualization_data'
        crawl = {'id': 42, 'user_id': 5, 'session_id': 'session-a'}
        urls = [
            {'url': 'https://example.test/', 'status_code': 200, 'title': 'Home', 'depth': 0},
            {'url': 'https://example.test/about', 'status_code': 200, 'title': 'About', 'depth': 1},
        ]
        links = [{
            'source_url': 'https://example.test/',
            'target_url': 'https://example.test/about',
            'is_internal': True,
        }]

        with mock.patch('src.crawl_db.get_crawl_by_id', return_value=crawl):
            with mock.patch('src.crawl_clickhouse.load_urls', return_value={'rows': urls, 'total': 2}):
                with mock.patch('src.crawl_clickhouse.load_links', return_value={'rows': links, 'total': 1}):
                    with mock.patch('src.crawl_db.load_crawled_urls') as sqlite_urls:
                        with mock.patch('src.crawl_db.load_crawl_links') as sqlite_links:
                            response = main.visualization_data()

        self.assertTrue(response['success'])
        self.assertEqual(response['total_pages'], 2)
        self.assertEqual(response['visualized_pages'], 2)
        self.assertEqual(len(response['nodes']), 2)
        self.assertEqual(len(response['edges']), 1)
        sqlite_urls.assert_not_called()
        sqlite_links.assert_not_called()

    def test_visualization_falls_back_to_sqlite_per_collection(self):
        main.session.update({'user_id': 5, 'username': 'test', 'tier': 'admin', 'session_id': 'session-a', 'current_crawl_id': 42})
        main.request.path = '/api/visualization_data'
        crawl = {'id': 42, 'user_id': 5, 'session_id': 'session-a'}
        urls = [
            {'url': 'https://example.test/', 'status_code': 200, 'title': 'Home', 'depth': 0},
            {'url': 'https://example.test/about', 'status_code': 200, 'title': 'About', 'depth': 1},
        ]
        links = [{
            'source_url': 'https://example.test/',
            'target_url': 'https://example.test/about',
            'is_internal': True,
        }]

        with mock.patch('src.crawl_db.get_crawl_by_id', return_value=crawl):
            with mock.patch('src.crawl_clickhouse.load_urls', return_value=None):
                with mock.patch('src.crawl_clickhouse.load_links', return_value=None):
                    with mock.patch('src.crawl_db.get_crawl_counts', return_value={'urls': 2}):
                        with mock.patch('src.crawl_db.load_crawled_urls', return_value=urls):
                            with mock.patch('src.crawl_db.load_crawl_links', return_value=links):
                                response = main.visualization_data()

        self.assertTrue(response['success'])
        self.assertEqual(response['total_pages'], 2)
        self.assertEqual(len(response['edges']), 1)

    def test_visualization_rejects_crawl_owned_by_another_user(self):
        main.session.update({'user_id': 5, 'username': 'test', 'tier': 'admin', 'session_id': 'session-a', 'current_crawl_id': 42})
        main.request.path = '/api/visualization_data'

        with mock.patch('src.crawl_db.get_crawl_by_id', return_value={
            'id': 42,
            'user_id': 99,
            'session_id': 'session-b',
        }):
            with mock.patch('src.crawl_clickhouse.load_urls') as load_urls:
                response, status = main.visualization_data()

        self.assertEqual(status, 403)
        self.assertFalse(response['success'])
        load_urls.assert_not_called()

    def test_export_data_rejects_unauthorized_crawl_id(self):
        main.session.update({'user_id': 5, 'username': 'test', 'tier': 'admin', 'session_id': 'session-a'})
        main.request.path = '/api/export_data'

        with mock.patch.object(main.request, 'get_json', return_value={'crawlId': 42, 'format': 'csv', 'fields': ['url']}):
            with mock.patch('src.crawl_db.get_crawl_by_id', return_value={
                'id': 42,
                'user_id': 99,
                'session_id': 'session-b',
            }):
                with mock.patch('src.crawl_db.load_crawled_urls') as load_urls:
                    response, status = main.export_data()

        self.assertEqual(status, 403)
        self.assertFalse(response['success'])
        load_urls.assert_not_called()

    def test_export_data_for_stored_crawl_returns_streaming_download(self):
        main.session.update({'user_id': 5, 'username': 'test', 'tier': 'admin', 'session_id': 'session-a'})
        main.request.path = '/api/export_data'
        payload = {
            'crawlId': 42,
            'format': 'csv',
            'fields': ['url', 'status_code', 'title'],
        }

        with mock.patch.object(main.request, 'get_json', return_value=payload):
            with mock.patch('src.crawl_db.get_crawl_by_id', return_value={
                'id': 42,
                'user_id': 5,
                'session_id': 'session-a',
            }):
                with mock.patch('src.crawl_db.load_crawled_urls') as load_urls:
                    response = main.export_data()

        self.assertTrue(response['success'])
        self.assertEqual(response['downloads'], [{
            'url': '/api/crawls/42/export/urls?format=csv&fields=url%2Cstatus_code%2Ctitle',
            'dataset': 'urls',
        }])
        load_urls.assert_not_called()

    def test_download_crawl_export_streams_selected_clickhouse_fields(self):
        main.session.update({'user_id': 5, 'username': 'test', 'tier': 'admin', 'session_id': 'session-a'})
        main.request.path = '/api/crawls/42/export/urls'
        args = {
            'format': 'csv',
            'fields': 'url,status_code',
        }
        main.request.args = types.SimpleNamespace(
            get=lambda key, default=None, type=None: args.get(key, default),
        )
        source = types.SimpleNamespace(
            rows=iter([{'url': 'https://example.com', 'status_code': 200}]),
            source='clickhouse',
        )

        with mock.patch('src.crawl_db.get_crawl_by_id', return_value={
            'id': 42,
            'user_id': 5,
            'session_id': 'session-a',
        }):
            with mock.patch('src.crawl_export.open_crawl_rows', return_value=source):
                response = main.download_crawl_export(42, 'urls')

        content = b''.join(response.response).decode('utf-8-sig')
        self.assertEqual(content, 'url,status_code\r\nhttps://example.com,200\r\n')
        self.assertEqual(response.content_type, 'text/csv; charset=utf-8')
        self.assertIn('attachment;', response.headers['Content-Disposition'])
        self.assertEqual(response.headers['X-Accel-Buffering'], 'no')

    def test_report_settings_update_preserves_blank_or_missing_key(self):
        self.assertEqual(
            main.report_settings_update_from_payload({
                'openrouter_api_key': '',
                'default_model': 'anthropic/claude-sonnet-4',
                'footer_text': '',
                'ignored': 'nope',
            }),
            {
                'default_model': 'anthropic/claude-sonnet-4',
                'footer_text': '',
            },
        )
        self.assertEqual(
            main.report_settings_update_from_payload({'openrouter_api_key': ' sk-test '}),
            {'openrouter_api_key': 'sk-test'},
        )

    def test_resolve_report_request_options_uses_overrides_then_settings(self):
        options = main.resolve_report_request_options(
            {
                'manual_model': 'deepseek/deepseek-chat',
                'tone': 'technical',
                'primary_color': '#111111',
            },
            {
                'openrouter_api_key': 'sk-test',
                'default_model': 'openai/gpt-4.1',
                'manual_model': 'anthropic/claude',
                'default_language': 'en',
                'default_tone': 'executive',
                'agency_name': 'Agency',
                'primary_color': '#2563eb',
                'footer_text': 'Footer',
            },
        )

        self.assertEqual(options['model'], 'deepseek/deepseek-chat')
        self.assertEqual(options['language'], 'en')
        self.assertEqual(options['tone'], 'technical')
        self.assertEqual(options['openrouter_api_key'], 'sk-test')
        self.assertEqual(options['branding']['agency_name'], 'Agency')
        self.assertEqual(options['branding']['primary_color'], '#111111')

    def test_resolve_report_request_options_requires_no_defaults_beyond_language_and_tone(self):
        options = main.resolve_report_request_options({}, {})

        self.assertIsNone(options['model'])
        self.assertEqual(options['language'], 'es-ES')
        self.assertEqual(options['tone'], 'executive')

    def test_report_options_validate_language_tone_and_model(self):
        with self.assertRaises(ValueError):
            main.resolve_report_request_options({'language': 'fr'}, {'default_model': 'openai/gpt-4.1'})
        with self.assertRaises(ValueError):
            main.resolve_report_request_options({'tone': 'casual'}, {'default_model': 'openai/gpt-4.1'})
        with self.assertRaises(ValueError):
            main.resolve_report_request_options({'model': 'google/gemini'}, {})

    def test_reports_require_admin_session(self):
        main.session.update({'user_id': 5, 'tier': 'user'})
        self.assertFalse(main.current_user_can_use_reports())

        main.session.update({'user_id': 5, 'tier': 'admin'})
        self.assertTrue(main.current_user_can_use_reports())

    def test_save_report_settings_returns_400_for_invalid_payload(self):
        main.session.update({'user_id': 5, 'tier': 'admin'})
        with mock.patch.object(main.request, 'get_json', return_value={'default_language': 'fr'}):
            response, status = main.save_report_settings()

        self.assertEqual(status, 400)
        self.assertFalse(response['success'])

        with mock.patch.object(main.request, 'get_json', return_value=['bad']):
            response, status = main.save_report_settings()

        self.assertEqual(status, 400)
        self.assertFalse(response['success'])

    def test_create_crawl_report_returns_400_for_invalid_payload(self):
        main.session.update({'user_id': 5, 'tier': 'admin', 'session_id': 'session-a'})

        with mock.patch.object(main.request, 'get_json', return_value={'language': 'fr'}):
            with mock.patch('src.crawl_db.get_crawl_by_id', return_value={
                'id': 42,
                'user_id': 5,
                'session_id': 'session-a',
                'status': 'completed',
            }):
                with mock.patch('src.reporting_settings.get_reporting_settings', return_value={
                    'openrouter_api_key': 'sk-test',
                    'default_model': 'openai/gpt-4.1',
                }):
                    response, status = main.create_crawl_report(42)

        self.assertEqual(status, 400)
        self.assertFalse(response['success'])

        with mock.patch.object(main.request, 'get_json', return_value=['bad']):
            with mock.patch('src.crawl_db.get_crawl_by_id', return_value={
                'id': 42,
                'user_id': 5,
                'session_id': 'session-a',
                'status': 'completed',
            }):
                response, status = main.create_crawl_report(42)

        self.assertEqual(status, 400)
        self.assertFalse(response['success'])

    def test_combine_report_usage_keeps_calls_and_sums_numeric_fields(self):
        usage = main.combine_report_usage(
            {'prompt_tokens': 10, 'completion_tokens': 5, 'cached': True},
            {'prompt_tokens': 3, 'total_tokens': 20},
        )

        self.assertEqual(usage['calls'][0]['prompt_tokens'], 10)
        self.assertEqual(usage['total'], {
            'prompt_tokens': 13,
            'completion_tokens': 5,
            'total_tokens': 20,
        })

    def test_report_access_context_reuses_crawl_ownership(self):
        with mock.patch('src.reporting_jobs.get_report_job', return_value={'id': 7, 'crawl_id': 42}):
            with mock.patch('src.crawl_db.get_crawl_by_id', return_value={'id': 42, 'user_id': 5, 'session_id': 'guest'}):
                job, crawl, allowed = main.report_access_context(7, user_id=5, session_id='other')

        self.assertEqual(job['id'], 7)
        self.assertEqual(crawl['id'], 42)
        self.assertTrue(allowed)

    def test_report_access_context_denies_wrong_guest_session(self):
        with mock.patch('src.reporting_jobs.get_report_job', return_value={'id': 7, 'crawl_id': 42}):
            with mock.patch('src.crawl_db.get_crawl_by_id', return_value={'id': 42, 'user_id': None, 'session_id': 'guest-a'}):
                _, _, allowed = main.report_access_context(7, user_id=None, session_id='guest-b')

        self.assertFalse(allowed)

    def test_public_report_job_redacts_server_paths(self):
        public = main.public_report_job({
            'id': 3,
            'crawl_id': 42,
            'status': 'completed',
            'language': 'en',
            'tone': 'technical',
            'model': 'openai/gpt-4.1',
            'markdown_path': '/app/data/reports/42/report-3.md',
            'html_path': '/app/data/reports/42/report-3.html',
            'pdf_path': '/app/data/reports/42/report-3.pdf',
            'error': 'secret path /tmp/x',
            'usage': {'total_tokens': 5},
        })

        self.assertNotIn('markdown_path', public)
        self.assertNotIn('html_path', public)
        self.assertNotIn('pdf_path', public)
        self.assertIsNone(public['error'])
        self.assertTrue(public['has_pdf'])
        self.assertEqual(public['download_url'], '/api/reports/3/download')

    def test_list_crawl_reports_returns_authorized_public_jobs(self):
        main.session.update({'user_id': 5, 'tier': 'admin', 'session_id': 'session-a'})

        with mock.patch('src.crawl_db.get_crawl_by_id', return_value={
            'id': 42,
            'user_id': 5,
            'session_id': 'session-a',
        }):
            with mock.patch('src.reporting_jobs.list_report_jobs_for_crawl', return_value=[{
                'id': 3,
                'crawl_id': 42,
                'status': 'completed',
                'language': 'es-ES',
                'tone': 'commercial',
                'model': 'openai/gpt-4.1',
                'pdf_path': '/app/data/reports/42/es-es/commercial/report-3.pdf',
                'error': None,
                'usage': None,
            }]):
                response = main.list_crawl_reports(42)

        self.assertTrue(response['success'])
        self.assertEqual(response['reports'][0]['tone'], 'commercial')
        self.assertEqual(response['reports'][0]['download_url'], '/api/reports/3/download')

    def test_safe_report_pdf_path_rejects_paths_outside_reports_base(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('src.reporting_pdf.DEFAULT_REPORTS_BASE_DIR', Path(tmpdir)):
                safe = Path(tmpdir) / '42' / 'report.pdf'
                unsafe = Path(tmpdir).parent / 'secret.pdf'

                self.assertEqual(main.safe_report_pdf_path({'pdf_path': str(safe)}), safe.resolve())
                self.assertIsNone(main.safe_report_pdf_path({'pdf_path': str(unsafe)}))

    def test_run_report_job_generates_files_and_marks_completed_with_mocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            paths = {
                'dir': output_dir,
                'markdown': output_dir / 'report-3.md',
                'html': output_dir / 'report-3.html',
                'pdf': output_dir / 'report-3.pdf',
            }
            audit_packet = {'crawl_metadata': {'base_url': 'https://example.test'}}

            with mock.patch('src.reporting_jobs.update_report_job') as update_job:
                with mock.patch('src.openrouter_client.OpenRouterClient') as client:
                    with mock.patch('src.openrouter_client.generate_structured_findings', return_value=('findings', {'prompt_tokens': 10})) as findings:
                        with mock.patch('src.openrouter_client.generate_report_markdown', return_value=('# Report', {'completion_tokens': 8})) as markdown:
                            with mock.patch('src.reporting_data.build_audit_packet', return_value=audit_packet):
                                with mock.patch('src.reporting_pdf.report_output_paths', return_value=paths) as output_paths:
                                    with mock.patch('src.reporting_pdf.render_report_html', return_value='<h1>Report</h1>') as html:
                                        with mock.patch('src.reporting_pdf.render_report_pdf') as pdf:
                                            with mock.patch('src.reporting_settings.get_openrouter_model', return_value={'supported_parameters': []}):
                                                main.run_report_job(3, 42, {
                                                    'model': 'openai/gpt-4.1',
                                                    'language': 'en',
                                                    'tone': 'technical',
                                                    'branding': {'agency_name': 'Agency'},
                                                    'openrouter_api_key': 'sk-test',
                                                })

            self.assertEqual(paths['markdown'].read_text(encoding='utf-8'), '# Report')
            self.assertEqual(paths['html'].read_text(encoding='utf-8'), '<h1>Report</h1>')
            client.assert_called_once_with('sk-test')
            findings.assert_called_once()
            markdown.assert_called_once()
            html.assert_called_once_with(
                '# Report',
                {'agency_name': 'Agency'},
                {'base_url': 'https://example.test'},
                audit_packet=audit_packet,
            )
            pdf.assert_called_once_with('<h1>Report</h1>', paths['pdf'])
            output_paths.assert_called_once_with(42, 3, language='en', tone='technical')
            update_job.assert_any_call(3, status='running')
            completed_call = update_job.call_args_list[-1]
            self.assertEqual(completed_call.kwargs['status'], 'completed')
            self.assertEqual(completed_call.kwargs['usage']['total'], {
                'prompt_tokens': 10,
                'completion_tokens': 8,
            })


if __name__ == '__main__':
    unittest.main()
