import unittest
from unittest import mock

from src import reporting_documents, reporting_pdf, reporting_v2


class ReportingV2Test(unittest.TestCase):
    def setUp(self):
        self.urls = [
            {'url': 'https://example.test/', 'status_code': 200, 'content_type': 'text/html', 'is_internal': True, 'depth': 0, 'title': 'Home', 'meta_description': 'Description', 'h1': 'Home', 'word_count': 600, 'response_time': 120, '_row_order': 1},
            {'url': 'https://example.test/', 'status_code': 200, 'content_type': 'text/html', 'is_internal': True, 'depth': 0, 'title': 'Home', 'meta_description': 'Description', 'h1': 'Home', 'word_count': 600, 'response_time': 140, '_row_order': 2},
            {'url': 'https://example.test/blog/a', 'status_code': 404, 'content_type': 'text/html', 'is_internal': True, 'depth': 1, 'title': '', 'meta_description': '', 'h1': '', 'word_count': 0, 'response_time': 500, '_row_order': 3},
        ]
        self.issues = [
            {'url': 'https://example.test/blog/a', 'category': 'Technical', 'issue': '404 Client Error', 'type': 'error', 'details': 'Not found', '_row_order': 1},
            {'url': 'https://example.test/blog/a', 'category': 'Technical', 'issue': '404 Client Error', 'type': 'error', 'details': 'Not found', '_row_order': 2},
        ]

    def test_facts_dedupe_urls_issues_and_include_denominators(self):
        facts = reporting_v2._facts_from_rows(
            reporting_v2._latest_by_url(self.urls), [], reporting_v2._unique_issues(self.issues), 10, 10, 'test'
        )
        denominators = facts['coverage']['denominators']
        self.assertEqual(denominators['unique_urls'], 2)
        self.assertEqual(denominators['html_2xx_urls'], 1)
        self.assertEqual(denominators['unique_issues'], 1)
        self.assertEqual(facts['thematic_metrics']['performance']['p50_ms'], 140.0)

    def test_facts_uses_explicit_sitemap_membership_when_available(self):
        urls = [
            {**self.urls[0], 'in_sitemap': True},
            {**self.urls[2], 'in_sitemap': False},
        ]
        facts = reporting_v2._facts_from_rows(urls, [], [], 10, 10, 'test')
        self.assertEqual(facts['coverage']['denominators']['sitemap_urls'], 1)

    def test_audit_facts_exposes_exact_citable_metric_catalog(self):
        with mock.patch.object(reporting_v2.crawl_clickhouse, 'get_report_facts', return_value={
            'coverage': {'denominators': {'unique_urls': 5, 'html_2xx_urls': 4}},
            'thematic_metrics': {'content': {'missing_title': 2}},
            'distributions': {'status_codes': [{'label': '200', 'count': 4}]},
            'evidence': {}, 'limitations': [],
        }):
            with mock.patch.object(reporting_v2.crawl_db, 'load_crawl_enrichments', return_value={}):
                facts = reporting_v2.build_audit_facts(42, {'id': 42, 'urls_discovered': 5})

        metrics = {item['id']: item for item in facts['metric_catalog']}
        self.assertEqual(metrics['thematic_metrics.content.missing_title'], {
            'id': 'thematic_metrics.content.missing_title', 'value': 2,
            'denominator_id': 'coverage.denominators.html_2xx_urls', 'denominator': 4,
        })
        self.assertEqual(metrics['distributions.status_codes.200']['value'], 4)

    def test_normalise_url_keeps_non_root_trailing_slashes_meaningful(self):
        self.assertEqual(reporting_v2.normalise_url('https://Example.test'), 'https://example.test/')
        self.assertNotEqual(
            reporting_v2.normalise_url('https://example.test/blog'),
            reporting_v2.normalise_url('https://example.test/blog/'),
        )

    def test_report_presentation_strips_www_without_changing_facts(self):
        view = reporting_pdf.build_report_suite_view_model(
            {'report_type': 'commercial', 'language': 'es-ES'}, {},
            {'crawl': {'base_domain': 'www.davidayala.com'}}, {},
        )
        self.assertEqual(view['crawl']['base_domain'], 'www.davidayala.com')
        self.assertEqual(view['crawl']['display_domain'], 'davidayala.com')

    def test_spanish_report_localises_deterministic_limitations(self):
        view = reporting_pdf.build_report_suite_view_model(
            {'report_type': 'executive', 'language': 'es-ES'}, {},
            {
                'crawl': {'base_domain': 'example.test'},
                'coverage': {'denominators': {}},
                'limitations': [
                    'Rows are de-duplicated with the most recent stored crawler record for each logical key.',
                    'Crawl evidence does not establish Google indexing, traffic, conversion, or revenue.',
                ],
            }, {},
        )

        self.assertEqual(len(view['limitations']), 2)
        self.assertTrue(all('Crawl' not in item and 'Rows are' not in item for item in view['limitations']))
        self.assertIn('indexación en Google', view['limitations'][1])

    def test_spanish_report_collapses_semantically_duplicate_limitations(self):
        view = reporting_pdf.build_report_suite_view_model(
            {'report_type': 'executive', 'language': 'es-ES'},
            {
                'limitations': [
                    'El tiempo de respuesta corresponde a crawl_request_duration, no a una Core Web Vital.',
                    'La deduplicación usa argMax(row_order) por URL/issue/enlace.',
                ],
            },
            {
                'crawl': {'base_domain': 'example.test'},
                'coverage': {'denominators': {}},
                'limitations': [
                    'Rows are de-duplicated with the most recent stored crawler record for each logical key.',
                    'Crawl request duration is not a Core Web Vital or user-field performance metric.',
                ],
            }, {},
        )

        self.assertEqual(len(view['limitations']), 2)
        self.assertNotIn('argMax', ' '.join(view['limitations']))
        self.assertNotIn('crawl_request_duration', ' '.join(view['limitations']))

    def test_executive_print_hides_flowing_footer_and_uses_closing_heading(self):
        facts = {
            'crawl': {'base_domain': 'example.test'},
            'coverage': {'denominators': {}},
            'evidence': {},
        }
        analysis = reporting_documents.fallback_analysis(facts, 'es-ES')
        document = reporting_documents.normalise_document(None, facts, analysis, 'executive', 'es-ES')

        html = reporting_pdf.render_report_document_html(document, analysis, facts)

        self.assertIn('.report-footer { display: none; }', html)
        self.assertIn('Conclusiones y límites', html)

    def test_historical_comparison_is_rendered_without_exposing_baseline_id(self):
        view = reporting_pdf.build_report_suite_view_model(
            {'report_type': 'executive', 'language': 'es-ES'}, {},
            {
                'crawl': {'base_domain': 'example.test'},
                'coverage': {'denominators': {}},
                'comparison': {'available': True, 'baseline_crawl_id': 12, 'deltas': {'unique_urls': 3, 'unique_issues': -2}},
            },
        )
        self.assertEqual([row['display'] for row in view['comparison_rows']], ['+3', '-2'])
        self.assertNotIn('baseline_crawl_id', view['comparison_rows'][0])

    def test_transparent_scorecard_keeps_numerators_and_denominators(self):
        view = reporting_pdf.build_report_suite_view_model(
            {'report_type': 'executive', 'language': 'es-ES'}, {}, {
                'coverage': {'denominators': {'unique_urls': 10, 'html_2xx_urls': 8, 'unique_links': 12}},
                'thematic_metrics': {
                    'http': {'2xx': 9},
                    'indexability': {'indexable_html_urls': 6},
                    'content': {'missing_title': 1, 'missing_meta_description': 0, 'missing_h1': 0, 'thin_content_under_300_words': 1},
                    'links': {'internal_edges': 10, 'unique_edges': 12},
                },
            }, {},
        )
        by_key = {row['key']: row for row in view['scorecard']}
        self.assertEqual((by_key['http']['passed'], by_key['http']['total']), (9, 10))
        self.assertEqual((by_key['indexability']['passed'], by_key['indexability']['total']), (6, 8))
        self.assertFalse(by_key['performance']['available'])

    def test_quality_gate_rejects_invalid_counts_brand_leaks_and_promises(self):
        facts = {'evidence': {'issues': [{'id': 'issues-1'}]}}
        analysis = {
            'findings': [{
                'id': 'finding-1', 'numerator': 12, 'denominator': 10,
                'metric_id': '', 'evidence_ids': ['missing'],
                'observation': 'LibreCrawl garantiza que aumentará el ranking.',
            }],
            'recommendations': [{
                'id': 'recommendation-1', 'title': 'Fix it', 'acceptance_criteria': '',
                'validation': '', 'kpi': '',
            }],
        }
        review = reporting_documents.validate_report_package(
            facts, analysis, {'sections': [{'id': 's1', 'finding_ids': ['unknown']}]},
        )
        codes = {item['code'] for item in review['issues']}
        self.assertFalse(review['passed'])
        self.assertIn('invalid_denominator', codes)
        self.assertIn('unknown_evidence', codes)
        self.assertIn('product_brand_leak', codes)
        self.assertIn('unsupported_causal_promise', codes)
        self.assertIn('unknown_finding_reference', codes)

    def test_quality_reviews_both_need_to_pass_the_ninety_point_gate(self):
        deterministic = {'passed': True, 'score': 100, 'issues': []}
        model = reporting_documents.normalise_quality_review({
            'verdict': 'pass', 'score': 89, 'issues': [],
        })
        merged = reporting_documents.merge_quality_reviews(deterministic, model)
        self.assertFalse(model['passed'])
        self.assertFalse(merged['passed'])
        self.assertEqual(merged['score'], 89)

    def test_quality_gate_checks_metric_value_and_denominator_against_catalog(self):
        facts = {'metric_catalog': [{
            'id': 'thematic_metrics.content.missing_title', 'value': 2,
            'denominator_id': 'coverage.denominators.html_2xx_urls', 'denominator': 4,
        }], 'evidence': {}}
        analysis = {
            'findings': [{
                'id': 'finding-1', 'observation': 'Tres URLs no tienen título.',
                'metric_id': 'thematic_metrics.content.missing_title',
                'numerator': 3, 'denominator': 5, 'evidence_ids': [],
            }],
            'recommendations': [],
        }

        review = reporting_documents.validate_report_package(facts, analysis, {'sections': []})
        codes = {item['code'] for item in review['issues']}
        self.assertIn('metric_value_mismatch', codes)
        self.assertIn('metric_denominator_mismatch', codes)

    def test_document_contract_discards_unknown_evidence_and_renders_white_label_html(self):
        facts = reporting_v2.build_audit_facts
        snapshot = {
            'crawl': {'base_domain': 'example.test'},
            'coverage': {'denominators': {'unique_urls': 2, 'html_2xx_urls': 1, 'unique_issues': 1}},
            'distributions': {'status_codes': [{'label': '200', 'count': 1}], 'issue_groups': [], 'depth': []},
            'thematic_metrics': {'performance': {}},
            'evidence': {'issues': [{'id': 'issues-1', 'url': 'https://example.test/a', 'issue': '404', 'details': 'Not found'}]},
            'limitations': ['Crawl evidence is not Google indexing data.'],
        }
        analysis = reporting_documents.normalise_analysis({
            'findings': [{'id': 'f1', 'observation': 'One URL returned 404.', 'evidence_ids': ['issues-1', 'unknown']}],
            'recommendations': [{'id': 'r1', 'title': 'Fix the URL'}],
        }, snapshot)
        self.assertEqual(analysis['findings'][0]['evidence_ids'], ['issues-1'])
        document = reporting_documents.normalise_document(None, snapshot, analysis, 'executive', client_context={'client_name': 'Example client'})
        html = reporting_pdf.render_report_document_html(document, analysis, snapshot, {'primary_color': '#0f766e'})
        self.assertIn('Example client', html)
        self.assertNotIn('Mitmore SEO Crawl', html)
        self.assertNotIn('LibreCrawl', html)
        self.assertNotIn('unpkg.com', html)
        self.assertIn('data-report-type="executive"', html)
        self.assertIn('data-module="scorecard"', html)
        self.assertIn('data-module="impact-effort"', html)

    def test_v2_renderer_does_not_reintroduce_platform_brand_from_legacy_branding(self):
        facts = {'crawl': {'base_domain': 'example.test'}, 'coverage': {'denominators': {}}, 'evidence': {}}
        analysis = reporting_documents.fallback_analysis(facts, 'es-ES')
        document = reporting_documents.normalise_document(None, facts, analysis, 'executive', 'es-ES')
        html = reporting_pdf.render_report_document_html(
            document, analysis, facts,
            {'agency_name': 'Mitmore SEO Crawl', 'footer_text': 'LibreCrawl'},
        )
        self.assertNotIn('Mitmore SEO Crawl', html)
        self.assertNotIn('LibreCrawl', html)

    def test_v2_branding_keeps_extreme_palette_and_logo_inputs_accessible(self):
        facts = {
            'crawl': {'base_domain': 'example.test'},
            'coverage': {'denominators': {'unique_urls': 1, 'html_2xx_urls': 1}},
            'evidence': {},
        }
        analysis = reporting_documents.fallback_analysis(facts, 'es-ES')
        document = reporting_documents.normalise_document(
            None, facts, analysis, 'commercial', 'es-ES',
            client_context={'client_name': 'Example', 'report_title': 'Título elegido', 'cta': 'Revisar el alcance'},
        )
        html = reporting_pdf.render_report_document_html(document, analysis, facts, {
            'primary_color': '#ffffff', 'secondary_color': '#ffffff', 'accent_color': '#ffffff',
            'font_family': 'Inter; color: red',
            'logo_path': 'data:image/svg+xml;base64,PHN2Zy8+',
        })
        self.assertIn('Título elegido', html)
        self.assertIn('Revisar el alcance', html)
        self.assertIn('--brand: #1d4ed8;', html)
        self.assertIn('--brand-2: #334155;', html)
        self.assertIn('--accent: #b45309;', html)
        self.assertNotIn('data:image/svg+xml', html)
        self.assertNotIn('Inter; color: red', html)

    def test_evidence_redacts_platform_name_from_generated_issue_detail(self):
        view = reporting_pdf.build_report_suite_view_model(
            {'report_type': 'technical', 'language': 'es-ES'}, {},
            {
                'crawl': {'base_domain': 'example.test'},
                'coverage': {'denominators': {'unique_issues': 1}},
                'evidence': {'issues': [{'url': 'https://example.test/a', 'issue': 'LibreCrawl timeout', 'details': 'Mitmore SEO Crawl measured 500ms'}]},
            },
        )
        row = view['evidence_tables']['issues']['rows'][0]
        self.assertNotIn('LibreCrawl', row['issue'])
        self.assertNotIn('Mitmore', row['details'])

    def test_build_audit_facts_uses_clickhouse_aggregate_and_assigns_evidence_ids(self):
        source = {
            'coverage': {'denominators': {'unique_urls': 3}},
            'thematic_metrics': {}, 'distributions': {},
            'evidence': {'urls': [{'url': 'https://example.test/'}], 'links': [], 'issues': []},
            'limitations': [],
        }
        with mock.patch.object(reporting_v2.crawl_clickhouse, 'get_report_facts', return_value=source):
            with mock.patch.object(reporting_v2.crawl_db, 'load_crawl_enrichments', return_value={}):
                facts = reporting_v2.build_audit_facts(42, {'id': 42, 'base_domain': 'example.test', 'urls_discovered': 5})
        self.assertEqual(facts['coverage']['denominators']['discovered_urls'], 5)
        self.assertEqual(facts['evidence']['urls'][0]['id'], 'urls-1')
        self.assertEqual(facts['coverage']['coverage_ratio'], 60.0)

    def test_model_facts_keep_seo_evidence_without_raw_page_payloads(self):
        facts = {
            'schema_version': '2.0',
            'coverage': {'denominators': {'unique_urls': 1}},
            'evidence': {'urls': [{
                'id': 'urls-1', 'url': 'https://example.test/', 'status_code': 200,
                'title': 'Home', 'canonical_url': 'https://example.test/',
                'images': [{'src': f'https://cdn.test/{index}.jpg', 'alt': ''} for index in range(100)],
                'json_ld': [{'@context': 'https://schema.org', '@type': 'Organization', 'name': 'Example'}],
                'og_tags': {'og:title': 'Home', 'og:image': 'https://cdn.test/hero.jpg'},
                'twitter_tags': {'twitter:card': 'summary_large_image'},
                'meta_tags': {'robots': 'index,follow'},
            }], 'links': [], 'issues': []},
            'limitations': [],
        }

        projected = reporting_v2.model_audit_facts(facts)
        row = projected['evidence']['urls'][0]

        self.assertEqual(row['id'], 'urls-1')
        self.assertEqual(row['canonical_url'], 'https://example.test/')
        self.assertEqual(row['image_summary'], {'total': 100, 'missing_alt': 100, 'broken': 0})
        self.assertEqual(row['structured_data_types'], ['Organization'])
        self.assertTrue(row['open_graph_present'])
        self.assertTrue(row['twitter_card_present'])
        self.assertNotIn('images', row)
        self.assertNotIn('json_ld', row)
        self.assertNotIn('meta_tags', row)

    def test_inconsistent_discovery_denominator_is_declared_instead_of_showing_over_100_percent(self):
        source = {
            'coverage': {'source': 'clickhouse', 'denominators': {'unique_urls': 8}},
            'thematic_metrics': {}, 'distributions': {}, 'evidence': {}, 'limitations': [],
        }
        with mock.patch.object(reporting_v2.crawl_clickhouse, 'get_report_facts', return_value=source):
            with mock.patch.object(reporting_v2.crawl_db, 'load_crawl_enrichments', return_value={}):
                facts = reporting_v2.build_audit_facts(42, {'id': 42, 'urls_discovered': 5})

        self.assertIsNone(facts['coverage']['coverage_ratio'])
        self.assertTrue(any('discovered URL denominator' in item for item in facts['limitations']))

    def test_row_fallback_reports_the_source_that_actually_supplied_evidence(self):
        clickhouse_urls = {'rows': [self.urls[0]]}
        empty_page = {'rows': []}
        with mock.patch.object(reporting_v2.crawl_clickhouse, 'get_report_facts', return_value=None):
            with mock.patch.object(reporting_v2.crawl_clickhouse, 'load_urls', return_value=clickhouse_urls):
                with mock.patch.object(reporting_v2.crawl_clickhouse, 'load_links', return_value=empty_page):
                    with mock.patch.object(reporting_v2.crawl_clickhouse, 'load_issues', return_value=empty_page):
                        with mock.patch.object(reporting_v2.crawl_db, 'load_crawled_urls') as sqlite_urls:
                            with mock.patch.object(reporting_v2.crawl_db, 'load_crawl_enrichments', return_value={}):
                                facts = reporting_v2.build_audit_facts(42, {'id': 42, 'urls_discovered': 1})

        sqlite_urls.assert_not_called()
        self.assertEqual(facts['coverage']['source'], 'clickhouse_rows')
        self.assertTrue(any('Fallback evidence' in item for item in facts['limitations']))
        self.assertFalse(any('SQLite fallback' in item for item in facts['limitations']))

    def test_stored_enrichment_data_redacts_connection_material(self):
        clean = reporting_v2._normalise_enrichments({
            'gsc': {'status': 'available', 'data': {
                'clicks': 12, 'access_token': 'secret', 'nested': {'api_key': 'hidden', 'impressions': 4},
            }},
        })
        self.assertEqual(clean['gsc']['data'], {'clicks': 12, 'nested': {'impressions': 4}})
        self.assertEqual(reporting_v2._normalise_enrichments({'ga4': {}})['ga4']['status'], 'not_connected')


if __name__ == '__main__':
    unittest.main()
