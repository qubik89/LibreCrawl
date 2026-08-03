import importlib
import json
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

        fallback = main.public_report_settings({'primary_color': 'url(javascript:bad)'})
        self.assertEqual(fallback['appearance']['primary_color'], '#1d4ed8')

    def test_get_report_settings_loads_the_authenticated_profile(self):
        main.session.update({'user_id': 5, 'username': 'test', 'tier': 'admin'})
        main.request.path = '/api/report-settings'
        reporting_settings = types.SimpleNamespace(
            get_report_asset=mock.Mock(return_value=None),
        )
        profile = {
            'openrouter_api_key': 'sk-live-1234567890',
            'default_model': 'openai/gpt-4.1',
        }

        with mock.patch.object(main, '_get_report_profile', return_value=profile):
            with mock.patch.dict(sys.modules, {'src.reporting_settings': reporting_settings}):
                response = main.get_report_settings()

        self.assertTrue(response['success'])
        self.assertTrue(response['settings']['has_openrouter_api_key'])
        self.assertEqual(response['settings']['masked_openrouter_api_key'], 'sk-l...7890')
        reporting_settings.get_report_asset.assert_called_once_with(None, 5)

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

    def test_dashboard_fallback_filters_can_be_combined(self):
        row = {
            'url': 'https://example.test/a', 'is_internal': True,
            'status_code': 404, 'content_type': 'text/html', 'depth': 2,
        }
        self.assertTrue(main._row_matches_filters(row, {
            'scope': 'internal', 'status_family': '4xx',
            'content_type': 'html', 'depth': '2',
        }, 'urls'))
        self.assertFalse(main._row_matches_filters(row, {'scope': 'external'}, 'urls'))

    def test_dashboard_endpoint_checks_owner_and_caches_snapshot(self):
        main.session.update({'user_id': 5, 'username': 'test', 'tier': 'admin', 'session_id': 'session-a'})
        main.request.path = '/api/crawls/91/dashboard'
        main.dashboard_cache.clear()
        crawl = {
            'id': 91, 'user_id': 5, 'session_id': 'session-a',
            'status': 'completed', 'base_url': 'https://example.test',
            'urls_discovered': 2, 'urls_crawled': 2,
        }
        snapshot = {'crawl': {'id': 91, 'status': 'completed', 'discovered': 2, 'crawled': 2}, 'coverage': {}, 'enrichments': {}}
        with mock.patch('src.crawl_db.get_crawl_by_id', return_value=crawl):
            with mock.patch('src.dashboard_data.build_dashboard_snapshot', return_value=snapshot) as build:
                with mock.patch.object(main, 'get_session_settings', return_value=mock.Mock(
                    get_settings=lambda: {'issueExclusionPatterns': ''},
                )):
                    first = main.crawl_dashboard_by_id(91)
                    second = main.crawl_dashboard_by_id(91)

        self.assertTrue(first['success'])
        self.assertEqual(first['crawl']['progress'], 100.0)
        self.assertEqual(second['crawl']['id'], 91)
        build.assert_called_once_with(91, ())

        with mock.patch('src.crawl_db.get_crawl_by_id', return_value={**crawl, 'user_id': 7}):
            response, status = main.crawl_dashboard_by_id(91)
        self.assertEqual(status, 403)
        self.assertFalse(response['success'])

    def test_clickhouse_cursor_is_serialized_as_exact_text(self):
        cursor = 1785696868549154891

        self.assertEqual(main._next_cursor([{'_row_order': cursor}], 0), str(cursor))
        self.assertEqual(main._next_cursor([], cursor), str(cursor))

    def test_issue_page_exposes_clickhouse_cursor_as_text(self):
        cursor = 1785696868549154891
        main.session.update({'user_id': 5, 'username': 'test', 'tier': 'admin', 'session_id': 'session-a'})
        main.request.path = '/api/crawls/9/issues'
        main.request.args = types.SimpleNamespace(
            get=lambda _key, default=None, type=None: default,
            getlist=lambda _key: [],
        )

        with mock.patch('src.crawl_db.get_crawl_by_id', return_value={
            'id': 9,
            'user_id': 5,
            'session_id': 'session-a',
        }):
            with mock.patch('src.crawl_clickhouse.load_issues', return_value={
                'rows': [{
                    '_row_order': str(cursor),
                    'url': 'https://example.com/a',
                    'type': 'warning',
                    'category': 'SEO',
                    'issue': 'Example issue',
                    'details': 'Example details',
                }],
                'total': 1,
            }):
                with mock.patch.object(main, 'get_session_settings', return_value=mock.Mock(
                    get_settings=lambda: {'issueExclusionPatterns': ''},
                )):
                    response = main.load_crawl_issues_page(9, limit=1, offset=0)

        self.assertEqual(response['issues'][0]['_row_order'], str(cursor))
        self.assertEqual(response['next_cursor'], str(cursor))

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
        self.assertEqual(response['total_links'], 1)
        self.assertEqual(response['visualized_links'], 1)
        sqlite_urls.assert_not_called()
        sqlite_links.assert_not_called()

    def test_visualization_selects_pages_from_link_sample_for_large_crawl(self):
        main.session.update({'user_id': 5, 'username': 'test', 'tier': 'admin', 'session_id': 'session-a', 'current_crawl_id': 42})
        main.request.path = '/api/visualization_data'
        crawl = {'id': 42, 'user_id': 5, 'session_id': 'session-a', 'base_url': 'https://example.test'}
        first_url_page = [
            {'url': f'https://example.test/page-{idx}', 'status_code': 200, 'title': '', 'depth': 1}
            for idx in range(500)
        ]
        graph_pages = [
            {'url': 'https://example.test/page-550', 'status_code': 200, 'title': 'Source', 'depth': 2},
            {'url': 'https://example.test/page-551', 'status_code': 200, 'title': 'Target', 'depth': 3},
        ]
        links = [{
            'source_url': 'https://example.test/page-550',
            'target_url': 'https://example.test/page-551',
            'is_internal': True,
        }]

        with mock.patch('src.crawl_db.get_crawl_by_id', return_value=crawl):
            with mock.patch('src.crawl_clickhouse.load_urls', return_value={'rows': first_url_page, 'total': 600}):
                with mock.patch('src.crawl_clickhouse.load_links', return_value={'rows': links, 'total': 1}):
                    with mock.patch('src.crawl_clickhouse.load_urls_for_urls', return_value={'rows': graph_pages, 'total': 2}) as load_graph_urls:
                        response = main.visualization_data()

        self.assertTrue(response['success'])
        self.assertEqual(len(response['nodes']), 2)
        self.assertEqual(len(response['edges']), 1)
        self.assertEqual(response['total_links'], 1)
        load_graph_urls.assert_called_once()

    def test_visualization_deduplicates_trailing_slashes_and_marks_actual_root(self):
        nodes, edges = main._build_visualization_graph(
            [
                {'url': 'https://example.test/about/', 'status_code': 200},
                {'url': 'https://example.test/', 'status_code': 200},
                {'url': 'https://example.test/about', 'status_code': 200},
            ],
            [{
                'source_url': 'https://example.test/',
                'target_url': 'https://example.test/about',
                'is_internal': True,
            }],
            base_url='https://example.test',
        )

        self.assertEqual(len(nodes), 2)
        self.assertEqual(len(edges), 1)
        self.assertEqual(nodes[0]['data']['url'], 'https://example.test/')
        self.assertEqual(nodes[0]['data']['size'], 30)

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

    def test_report_profile_payload_uses_v2_fields_and_rejects_invalid_palette(self):
        profile = main.report_profile_update_from_payload({
            'default_report_mode': 'pack',
            'default_commercial_context': 'existing_client',
            'issuer': {'name': 'Agency', 'author': 'SEO Team'},
            'appearance': {'primary_color': '#123456', 'secondary_color': '#334455', 'accent_color': '#aa5500'},
        })
        self.assertEqual(profile['default_report_mode'], 'pack')
        self.assertEqual(profile['default_commercial_context'], 'existing_client')
        self.assertEqual(profile['issuer_name'], 'Agency')
        self.assertEqual(profile['primary_color'], '#123456')
        self.assertNotIn('agency_name', profile)
        self.assertEqual(
            main.report_profile_update_from_payload({'tone': 'technical'})['default_report_mode'],
            'technical',
        )
        with self.assertRaises(ValueError):
            main.report_profile_update_from_payload({'appearance': {'primary_color': 'red'}})

    def test_report_profile_does_not_persist_displayed_key_mask(self):
        self.assertNotIn('openrouter_api_key', main.report_profile_update_from_payload({
            'openrouter_api_key': 'sk-l...7890',
        }))

    def test_legacy_requests_remain_single_product_when_profile_defaults_to_pack(self):
        options = main.resolve_report_request_options(
            {'tone': 'technical', 'use_v2': False},
            {'default_report_mode': 'pack', 'default_tone': 'executive', 'default_model': 'openai/gpt-4.1'},
        )
        self.assertEqual(options['report_types'], ['technical'])
        self.assertFalse(options['use_v2'])

        default_options = main.resolve_report_request_options(
            {'use_v2': False},
            {'default_report_mode': 'pack', 'default_model': 'openai/gpt-4.1'},
        )
        self.assertEqual(default_options['report_types'], ['executive'])

    def test_enrichment_request_keeps_reference_but_drops_inline_credentials(self):
        options = main.resolve_report_request_options(
            {'enrichments': {'gsc': {'status': 'available', 'connection_id': 'gsc-1', 'data': {'access_token': 'secret'}}}},
            {'default_model': 'openai/gpt-4.1'},
        )
        self.assertEqual(options['enrichments'], {'gsc': {'status': 'available', 'connection_id': 'gsc-1'}})

    def test_public_profile_has_no_product_brand_by_default(self):
        public = main.public_report_settings({
            'default_model': 'anthropic/claude-opus-4.7', 'default_report_mode': 'pack',
            'issuer_name': None,
        })
        self.assertEqual(public['default_report_mode'], 'pack')
        self.assertEqual(public['issuer']['name'], '')
        self.assertNotIn('Mitmore', public['issuer']['name'])
        self.assertNotIn('LibreCrawl', public['issuer']['name'])

    def test_public_profile_filters_product_brand_from_legacy_identity_fields(self):
        public = main.public_report_settings({
            'default_confidentiality': 'Mitmore internal',
            'default_author': 'LibreCrawl team',
        })
        self.assertEqual(public['issuer']['confidentiality'], '')
        self.assertEqual(public['issuer']['author'], '')

    def test_public_profile_does_not_echo_unknown_connection_fields(self):
        public = main.public_report_settings({
            'default_model': 'anthropic/claude-opus-4.7',
            'connection_id': 'gsc-private-1',
            'access_token': 'secret',
        })
        self.assertNotIn('connection_id', public)
        self.assertNotIn('access_token', public)

    def test_sole_admin_migration_gate_requires_exactly_one_admin(self):
        fake_users = types.SimpleNamespace(
            get_all_users=mock.Mock(return_value=[{'id': 5, 'tier': 'admin'}]),
        )
        with mock.patch.dict(sys.modules, {'src.auth_db': fake_users}):
            self.assertTrue(main._is_sole_admin(5))
            fake_users.get_all_users.return_value = [
                {'id': 5, 'tier': 'admin'}, {'id': 6, 'tier': 'admin'},
            ]
            self.assertFalse(main._is_sole_admin(5))

    def test_report_preflight_is_token_free_and_rejects_cross_domain_baseline(self):
        main.session.update({'user_id': 5, 'username': 'test', 'tier': 'admin', 'session_id': 'session-a'})
        main.request.args = types.SimpleNamespace(get=lambda key, default=None, type=None: {
            'report_types': 'executive,commercial,technical', 'baseline_crawl_id': '8',
        }.get(key, default))
        current = {'id': 7, 'user_id': 5, 'status': 'completed', 'base_domain': 'example.test'}
        baseline = {'id': 8, 'user_id': 5, 'status': 'completed', 'base_domain': 'other.test'}
        with mock.patch('src.crawl_db.get_crawl_by_id', side_effect=[current, baseline]):
            response, status = main.report_preflight(7)
        self.assertEqual(status, 400)
        self.assertIn('mismo dominio', response['error'])

        main.request.args = types.SimpleNamespace(get=lambda key, default=None, type=None: {
            'report_types': 'executive',
        }.get(key, default))
        facts = {
            'coverage': {'denominators': {'unique_urls': 2, 'html_2xx_urls': 2}, 'coverage_ratio': 100},
            'limitations': [], 'enrichments': {}, 'comparison': {'available': False},
        }
        with mock.patch('src.crawl_db.get_crawl_by_id', return_value=current):
            with mock.patch('src.crawl_db.get_user_crawls', return_value=[]):
                with mock.patch('src.reporting_v2.build_audit_facts', return_value=facts) as build:
                    with mock.patch('src.reporting_settings.get_openrouter_model', return_value=None):
                        result = main.report_preflight(7)
        self.assertTrue(result['success'])
        build.assert_called_once_with(7, crawl_metadata=current, baseline_crawl_id=None)
        self.assertFalse(result['estimate']['pricing_available'])

    def test_report_preflight_only_recommends_an_older_same_domain_crawl(self):
        main.session.update({'user_id': 5, 'tier': 'admin', 'session_id': 'session-a'})
        main.request.args = types.SimpleNamespace(get=lambda key, default=None, type=None: {
            'report_types': 'executive',
        }.get(key, default))
        current = {
            'id': 12, 'user_id': 5, 'status': 'completed', 'base_domain': 'example.test',
            'started_at': '2026-08-02 12:00:00', 'completed_at': '2026-08-02 12:02:00',
        }
        newer = {
            'id': 13, 'user_id': 5, 'status': 'completed', 'base_domain': 'example.test',
            'started_at': '2026-08-02 13:00:00', 'completed_at': '2026-08-02 13:02:00',
        }
        facts = {
            'coverage': {'denominators': {'unique_urls': 1, 'html_2xx_urls': 1}, 'coverage_ratio': 100},
            'limitations': [], 'enrichments': {}, 'comparison': {'available': False},
        }
        with mock.patch('src.crawl_db.get_crawl_by_id', return_value=current):
            with mock.patch('src.crawl_db.get_user_crawls', return_value=[newer]):
                with mock.patch('src.reporting_v2.build_audit_facts', return_value=facts):
                    with mock.patch('src.reporting_settings.get_openrouter_model', return_value=None):
                        result = main.report_preflight(12)
        self.assertTrue(result['success'])
        self.assertIsNone(result['baseline']['selected_id'])
        self.assertEqual(result['baseline']['candidates'], [])

    def test_report_preflight_can_explicitly_disable_automatic_baseline(self):
        main.session.update({'user_id': 5, 'tier': 'admin', 'session_id': 'session-a'})
        main.request.args = types.SimpleNamespace(get=lambda key, default=None, type=None: {
            'report_types': 'executive', 'baseline_crawl_id': 'none',
        }.get(key, default))
        current = {'id': 12, 'user_id': 5, 'status': 'completed', 'base_domain': 'example.test'}
        older = {'id': 11, 'user_id': 5, 'status': 'completed', 'base_domain': 'example.test'}
        facts = {
            'coverage': {'denominators': {'unique_urls': 1, 'html_2xx_urls': 1}, 'coverage_ratio': 100},
            'limitations': [], 'enrichments': {}, 'comparison': {'available': False},
        }
        with mock.patch('src.crawl_db.get_crawl_by_id', return_value=current):
            with mock.patch('src.crawl_db.get_user_crawls', return_value=[older]):
                with mock.patch('src.reporting_v2.build_audit_facts', return_value=facts) as build:
                    with mock.patch('src.reporting_settings.get_openrouter_model', return_value=None):
                        result = main.report_preflight(12)
        self.assertTrue(result['success'])
        self.assertIsNone(result['baseline']['selected_id'])
        build.assert_called_once_with(12, crawl_metadata=current, baseline_crawl_id=None)

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

    def test_report_v2_pack_options_are_normalized_without_credentials(self):
        with mock.patch.object(main, 'REPORTS_V2_ENABLED', True):
            options = main.resolve_report_request_options({
                'report_types': ['executive', 'commercial', 'technical', 'executive'],
                'commercial_context': 'existing_client',
                'baseline_crawl_id': '7',
                'use_v2': True,
                'brand_kit': {'client_name': 'Example', 'primary_color': '#0f766e'},
                'client_context': {'author': 'SEO Team', 'business_goals': 'Improve qualified acquisition'},
                'enrichments': {'gsc': {'connection_id': 'gsc-1'}},
            }, {'default_model': 'anthropic/claude-opus-4.7', 'openrouter_api_key': 'sk-test'})

        self.assertEqual(options['report_types'], ['executive', 'commercial', 'technical'])
        self.assertEqual(options['commercial_context'], 'existing_client')
        self.assertEqual(options['baseline_crawl_id'], 7)
        self.assertTrue(options['use_v2'])
        self.assertEqual(options['client_context']['client_name'], 'Example')
        self.assertNotIn('openrouter_api_key', options['enrichments']['gsc'])

    def test_pack_profile_defaults_expand_to_three_v2_products(self):
        with mock.patch.object(main, 'REPORTS_V2_ENABLED', True):
            options = main.resolve_report_request_options({
                'report_mode': 'pack', 'use_v2': True,
            }, {
                'default_model': 'anthropic/claude-opus-4.7',
                'openrouter_api_key': 'sk-test', 'default_report_mode': 'pack',
            })
        self.assertEqual(options['report_types'], ['executive', 'commercial', 'technical'])
        self.assertTrue(options['use_v2'])

    def test_report_v2_is_rejected_when_the_feature_flag_is_off(self):
        with mock.patch.object(main, 'REPORTS_V2_ENABLED', False):
            with self.assertRaises(ValueError):
                main.resolve_report_request_options({'use_v2': True}, {})

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

    def test_report_usage_cost_is_unknown_without_provider_pricing(self):
        usage = {'total': {'prompt_tokens': 1000, 'completion_tokens': 500}}
        self.assertEqual(
            main.report_usage_cost({'pricing': {'prompt': '0.001', 'completion': '0.002'}}, usage),
            2.0,
        )
        self.assertIsNone(main.report_usage_cost({}, usage))

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

    def test_public_failed_report_error_redacts_secrets_and_paths(self):
        public = main.public_report_job({
            'id': 4, 'crawl_id': 42, 'status': 'failed', 'tone': 'technical',
            'error': 'OpenRouter sk-live-secret failed at /srv/librecrawl/report.json',
        })
        self.assertNotIn('sk-live-secret', public['error'])
        self.assertNotIn('/srv/librecrawl', public['error'])

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

    def test_run_report_job_v2_writes_traceable_artifacts_with_shared_analysis(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            paths = {
                'dir': output_dir,
                'markdown': output_dir / 'report-3.md',
                'html': output_dir / 'report-3.html',
                'pdf': output_dir / 'report-3.pdf',
                'facts': output_dir / 'report-3.facts.json',
                'analysis': output_dir / 'report-3.analysis.json',
                'document': output_dir / 'report-3.document.json',
                'quality': output_dir / 'report-3.quality.json',
                'manifest': output_dir / 'report-3.manifest.json',
            }
            facts = {
                'schema_version': '2.0', 'crawl': {'id': 42, 'base_domain': 'example.test'},
                'coverage': {'denominators': {}}, 'distributions': {}, 'thematic_metrics': {},
                'evidence': {}, 'limitations': [],
            }
            analysis = {'findings': [], 'recommendations': [], 'limitations': []}
            document_json = '{"title":"Audit","subtitle":"","sections":[],"closing":""}'

            with mock.patch('src.reporting_jobs.update_report_job') as update_job:
                with mock.patch('src.openrouter_client.OpenRouterClient'):
                    with mock.patch('src.openrouter_client.generate_report_document', return_value=(document_json, {'completion_tokens': 8})):
                        with mock.patch(
                            'src.openrouter_client.generate_report_quality_review',
                            return_value=('{"verdict":"pass","score":100,"issues":[]}', {'completion_tokens': 2}),
                        ):
                            with mock.patch('src.reporting_pdf.report_output_paths', return_value=paths):
                                with mock.patch('src.reporting_pdf.render_report_document_html', return_value='<h1>Audit</h1>'):
                                    with mock.patch('src.reporting_pdf.render_report_pdf'):
                                        with mock.patch('src.reporting_settings.get_openrouter_model', return_value={'supported_parameters': []}):
                                            main.run_report_job(3, 42, {
                                                'use_v2': True, 'model': 'anthropic/claude-opus-4.7', 'language': 'es-ES',
                                                'tone': 'executive', 'report_type': 'executive', 'commercial_context': 'prospect',
                                                'branding': {'primary_color': '#0f766e'}, 'client_context': {'client_name': 'Example'},
                                                'openrouter_api_key': 'sk-test', 'baseline_crawl_id': None, 'enrichments': {},
                                            }, shared_facts=facts, shared_analysis=analysis, shared_analysis_usage={'prompt_tokens': 4})

            self.assertTrue(paths['facts'].exists())
            self.assertTrue(paths['analysis'].exists())
            self.assertTrue(paths['document'].exists())
            self.assertTrue(paths['quality'].exists())
            self.assertTrue(paths['manifest'].exists())
            completed = update_job.call_args_list[-1].kwargs
            self.assertEqual(completed['status'], 'completed')
            self.assertEqual(completed['template_version'], '2.0')
            self.assertEqual(completed['usage']['total'], {'prompt_tokens': 4, 'completion_tokens': 10})

    def test_v2_invalid_model_analysis_fails_technically_instead_of_using_a_placeholder(self):
        facts = {
            'schema_version': '2.0', 'crawl': {'id': 42, 'base_domain': 'example.test'},
            'coverage': {'denominators': {}}, 'distributions': {}, 'thematic_metrics': {},
            'evidence': {}, 'limitations': [],
        }

        with mock.patch('src.reporting_jobs.update_report_job') as update_job:
            with mock.patch('src.openrouter_client.OpenRouterClient'):
                with mock.patch(
                    'src.openrouter_client.generate_report_analysis',
                    return_value=('{"findings":', {
                        'completion_tokens': 18000,
                        '_response': {'finish_reason': 'length', 'id': 'generation-1'},
                    }),
                ):
                    with mock.patch('src.openrouter_client.generate_report_document') as document:
                        with mock.patch(
                            'src.reporting_settings.get_openrouter_model', return_value={'supported_parameters': []},
                        ):
                            main.run_report_job(3, 42, {
                                'use_v2': True, 'model': 'anthropic/claude-sonnet-5', 'language': 'es-ES',
                                'tone': 'executive', 'report_type': 'executive',
                                'commercial_context': 'existing_client', 'branding': {},
                                'client_context': {}, 'openrouter_api_key': 'sk-test',
                                'baseline_crawl_id': None, 'enrichments': {},
                            }, shared_facts=facts)

        document.assert_not_called()
        failed = update_job.call_args_list[-1].kwargs
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('truncated', failed['error'].lower())
        self.assertNotIn('deterministic', failed['error'].lower())

    def test_quality_cycle_runs_exactly_one_repair_and_rechecks(self):
        facts = {'evidence': {'issues': [{'id': 'issues-1'}]}}
        analysis = {
            'findings': [{
                'id': 'finding-1', 'theme': 'indexability', 'severity': 'high', 'confidence': 'high',
                'observation': 'Una URL indexable devuelve un error.', 'inference': '', 'hypothesis': '',
                'scope': 'HTML 2xx', 'numerator': 1, 'denominator': 1,
                'metric_id': 'issues.indexability', 'evidence_ids': ['issues-1'],
                'limitation': '', 'recommendation_ids': ['recommendation-1'],
            }],
            'recommendations': [{
                'id': 'recommendation-1', 'title': 'Corregir la URL', 'priority': 'now',
                'impact': 'high', 'effort': 'low', 'owner': 'Equipo SEO', 'dependencies': [],
                'sequence': 'Primero', 'acceptance_criteria': 'La URL responde correctamente.',
                'validation': 'Repetir el crawl.', 'kpi': '0 URLs afectadas.',
            }],
            'limitations': [],
        }
        document = {
            'report_type': 'executive', 'commercial_context': 'prospect', 'language': 'es-ES',
            'title': 'Informe ejecutivo', 'subtitle': '',
            'sections': [{
                'id': 'summary', 'title': 'Resumen', 'summary': 'Prioridad validada.',
                'finding_ids': ['finding-1'], 'chart': None, 'actions': ['Corregir la URL'],
            }],
            'closing': 'Siguiente paso', 'client_context': {},
        }
        repaired_payload = json.dumps({'analysis': analysis, 'document': document})
        review_responses = [
            (json.dumps({
                'verdict': 'fail', 'score': 80,
                'issues': [{
                    'code': 'clarity', 'severity': 'major', 'target': 'document',
                    'target_id': 'summary', 'message': 'Falta claridad.',
                    'repair_instruction': 'Aclarar la prioridad.',
                }],
            }), {'completion_tokens': 2}),
            ('{"verdict":"pass","score":95,"issues":[]}', {'completion_tokens': 2}),
        ]

        with mock.patch(
            'src.openrouter_client.generate_report_quality_review', side_effect=review_responses,
        ) as review:
            with mock.patch(
                'src.openrouter_client.generate_report_repair',
                return_value=(repaired_payload, {'completion_tokens': 3}),
            ) as repair:
                final_analysis, final_document, quality, usage = main.run_report_quality_cycle(
                    mock.Mock(), 'anthropic/claude-opus-4.7', {'supported_parameters': []},
                    facts, analysis, document,
                    {
                        'report_type': 'executive', 'language': 'es-ES',
                        'commercial_context': 'prospect', 'quality_review_system_prompt': 'review',
                        'repair_system_prompt': 'repair', 'tone_prompt': 'executive',
                    },
                )

        self.assertEqual(review.call_count, 2)
        repair.assert_called_once()
        self.assertEqual(final_analysis['findings'][0]['id'], 'finding-1')
        self.assertEqual(final_document['sections'][0]['id'], 'summary')
        self.assertTrue(quality['repair']['attempted'])
        self.assertTrue(quality['final']['combined']['passed'])
        self.assertEqual(quality['final']['combined']['score'], 95)
        self.assertEqual(usage, [
            {'completion_tokens': 2}, {'completion_tokens': 3}, {'completion_tokens': 2},
        ])

    def test_invalid_model_review_is_a_technical_failure_and_does_not_repair_content(self):
        facts = {'evidence': {}, 'metric_catalog': []}
        analysis = {'findings': [], 'recommendations': [], 'limitations': []}
        document = {
            'report_type': 'executive', 'commercial_context': 'prospect', 'language': 'es-ES',
            'title': 'Informe', 'subtitle': '', 'sections': [], 'closing': '', 'client_context': {},
        }

        with mock.patch(
            'src.openrouter_client.generate_report_quality_review',
            return_value=('{"verdict":', {
                'completion_tokens': 4000,
                '_response': {'finish_reason': 'length', 'id': 'generation-1'},
            }),
        ) as review:
            with mock.patch('src.openrouter_client.generate_report_repair') as repair:
                _, _, quality, usage = main.run_report_quality_cycle(
                    mock.Mock(), 'anthropic/claude-sonnet-5', {'supported_parameters': []},
                    facts, analysis, document,
                    {
                        'report_type': 'executive', 'language': 'es-ES',
                        'commercial_context': 'prospect', 'quality_review_system_prompt': 'review',
                        'repair_system_prompt': 'repair', 'tone_prompt': 'executive',
                    },
                )

        review.assert_called_once()
        repair.assert_not_called()
        self.assertEqual(quality['final']['combined']['verdict'], 'error')
        self.assertIsNone(quality['final']['combined']['score'])
        self.assertIn('truncated', quality['final']['combined']['technical_error'].lower())
        self.assertFalse(quality['repair']['attempted'])
        self.assertEqual(usage[0]['completion_tokens'], 4000)

    def test_v2_failed_quality_gate_keeps_diagnostic_artifacts_without_rendering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            paths = {
                'dir': output_dir, 'markdown': output_dir / 'report.md',
                'html': output_dir / 'report.html', 'pdf': output_dir / 'report.pdf',
                'facts': output_dir / 'facts.json', 'analysis': output_dir / 'analysis.json',
                'document': output_dir / 'document.json', 'quality': output_dir / 'quality.json',
                'manifest': output_dir / 'manifest.json',
            }
            facts = {
                'schema_version': '2.0', 'crawl': {'id': 42},
                'coverage': {'denominators': {}}, 'distributions': {},
                'thematic_metrics': {}, 'metric_catalog': [], 'evidence': {}, 'limitations': [],
            }
            analysis = {'findings': [], 'recommendations': [], 'limitations': []}
            failed_review = json.dumps({
                'verdict': 'fail', 'score': 70,
                'issues': [{
                    'code': 'audience-fit', 'severity': 'major', 'target': 'document',
                    'target_id': None, 'message': 'No alcanza el nivel requerido.',
                    'repair_instruction': 'Revisar la composición.',
                }],
            })

            with mock.patch('src.reporting_jobs.update_report_job') as update_job:
                with mock.patch('src.openrouter_client.OpenRouterClient'):
                    with mock.patch('src.openrouter_client.generate_report_document', return_value=('{}', {})):
                        with mock.patch(
                            'src.openrouter_client.generate_report_quality_review',
                            side_effect=[(failed_review, {}), (failed_review, {})],
                        ):
                            with mock.patch(
                                'src.openrouter_client.generate_report_repair',
                                return_value=(json.dumps({'analysis': analysis, 'document': {
                                    'title': 'Informe', 'subtitle': '', 'sections': [], 'closing': '',
                                }}), {}),
                            ) as repair:
                                with mock.patch('src.reporting_pdf.report_output_paths', return_value=paths):
                                    with mock.patch('src.reporting_pdf.render_report_document_html') as render_html:
                                        with mock.patch('src.reporting_pdf.render_report_pdf') as render_pdf:
                                            with mock.patch('src.reporting_settings.get_openrouter_model', return_value={'supported_parameters': []}):
                                                main.run_report_job(3, 42, {
                                                    'use_v2': True, 'model': 'anthropic/claude-opus-4.7',
                                                    'language': 'es-ES', 'tone': 'executive',
                                                    'report_type': 'executive', 'commercial_context': 'prospect',
                                                    'branding': {}, 'client_context': {}, 'openrouter_api_key': 'sk-test',
                                                    'baseline_crawl_id': None, 'enrichments': {},
                                                }, shared_facts=facts, shared_analysis=analysis)

            repair.assert_called_once()
            render_html.assert_not_called()
            render_pdf.assert_not_called()
            self.assertTrue(paths['quality'].exists())
            self.assertTrue(paths['manifest'].exists())
            failed = update_job.call_args_list[-1].kwargs
            self.assertEqual(failed['status'], 'failed')
            self.assertEqual(failed['quality_path'], str(paths['quality']))
            self.assertIn('no superó el control de calidad', failed['error'])


if __name__ == '__main__':
    unittest.main()
