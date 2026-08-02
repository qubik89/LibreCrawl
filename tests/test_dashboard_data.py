import unittest
from unittest import mock

from src import dashboard_data


class DashboardDataTest(unittest.TestCase):
    def setUp(self):
        self.urls = [
            {'id': 1, 'url': 'https://example.test/', 'is_internal': True, 'status_code': 200, 'content_type': 'text/html', 'depth': 0, 'response_time': 120, 'robots': ''},
            {'id': 2, 'url': 'https://example.test/a', 'is_internal': True, 'status_code': 404, 'content_type': 'text/html', 'depth': 1, 'response_time': 220, 'robots': ''},
            {'id': 3, 'url': 'https://example.test/b', 'is_internal': True, 'status_code': 500, 'content_type': 'text/html', 'depth': 1, 'response_time': 3200, 'robots': ''},
            {'id': 4, 'url': 'https://cdn.example.test/app.js', 'is_internal': False, 'status_code': 200, 'content_type': 'application/javascript', 'depth': 1, 'response_time': 80, 'robots': ''},
        ]
        self.issues = [
            {'id': 1, 'url': 'https://example.test/a', 'category': 'Technical', 'issue': '404 Client Error', 'type': 'error'},
            {'id': 2, 'url': 'https://example.test/b', 'category': 'Technical', 'issue': '500 Server Error', 'type': 'error'},
            {'id': 3, 'url': 'https://example.test/', 'category': 'SEO', 'issue': 'Missing Title Tag', 'type': 'error'},
            {'id': 4, 'url': 'https://cdn.example.test/app.js', 'category': 'SEO', 'issue': 'Missing Title Tag', 'type': 'error'},
        ]

    def test_fallback_scopes_on_page_issues_to_internal_html(self):
        with mock.patch.object(dashboard_data.crawl_clickhouse, 'get_dashboard_facts', return_value=None):
            with mock.patch.object(dashboard_data.crawl_db, 'load_crawled_urls', return_value=self.urls):
                with mock.patch.object(dashboard_data.crawl_db, 'load_crawl_links', return_value=[]):
                    with mock.patch.object(dashboard_data.crawl_db, 'load_crawl_issues', return_value=self.issues):
                        facts = dashboard_data._facts_from_database(7, [])

        self.assertEqual(facts['coverage']['internal_urls'], 3)
        self.assertEqual(facts['http']['4xx'], 1)
        self.assertEqual(facts['http']['5xx'], 1)
        title = next(row for row in facts['on_page'] if row['key'] == 'title')
        self.assertEqual(title['affected'], 1)
        self.assertEqual(facts['issues']['top'][0]['priority'], 'critical')

    def test_exclusions_remove_affected_issue_groups(self):
        with mock.patch.object(dashboard_data.crawl_db, 'load_crawled_urls', return_value=self.urls):
            with mock.patch.object(dashboard_data.crawl_db, 'load_crawl_links', return_value=[]):
                with mock.patch.object(dashboard_data.crawl_db, 'load_crawl_issues', return_value=self.issues):
                    facts = dashboard_data._facts_from_database(7, ['/a'])

        names = {row['issue'] for row in facts['issues']['groups']}
        self.assertNotIn('404 Client Error', names)

    def test_enrichments_are_projected_without_connection_metadata(self):
        result = dashboard_data._safe_enrichments({
            'gsc': {'status': 'available', 'connection_id': 'secret', 'data': {'clicks': 12, 'token': 'nope'}},
            'pagespeed': {'status': 'available', 'data': {'pages': [{'url': 'https://example.test/'}]}},
        })
        self.assertEqual(result['gsc']['data'], {'clicks': 12})
        self.assertNotIn('connection_id', result['gsc'])
        self.assertEqual(result['pagespeed']['status'], 'available')

    def test_latest_rows_and_zero_denominators_do_not_inflate_metrics(self):
        duplicate = {
            'id': 10, 'url': 'https://example.test/a/', 'is_internal': True,
            'status_code': 200, 'content_type': 'text/html', 'depth': 1,
        }
        with mock.patch.object(dashboard_data.crawl_db, 'load_crawled_urls', return_value=[self.urls[1], duplicate]):
            with mock.patch.object(dashboard_data.crawl_db, 'load_crawl_links', return_value=[]):
                with mock.patch.object(dashboard_data.crawl_db, 'load_crawl_issues', return_value=[]):
                    facts = dashboard_data._facts_from_database(7, [])

        self.assertEqual(facts['coverage']['internal_urls'], 1)
        self.assertEqual(facts['http']['2xx'], 1)
        self.assertIsNone(dashboard_data._percentage(1, 0))

    def test_priority_rules_cover_critical_high_medium_and_opportunity(self):
        self.assertEqual(dashboard_data._issue_presentation('Technical', '500 Server Error', 'error')['priority'], 'critical')
        self.assertEqual(dashboard_data._issue_presentation('SEO', 'Missing H1 Tag', 'warning')['priority'], 'high')
        self.assertEqual(dashboard_data._issue_presentation('SEO', 'Missing Canonical URL', 'warning')['priority'], 'medium')
        self.assertEqual(dashboard_data._issue_presentation('Structured Data', 'No Structured Data', 'info')['priority'], 'opportunity')

    def test_empty_aggregate_recovers_from_clickhouse_row_reader(self):
        empty_aggregate = {'coverage': {'unique_urls': 0}}
        url_page = {'rows': self.urls}
        with mock.patch.object(dashboard_data.crawl_clickhouse, 'get_dashboard_facts', return_value=empty_aggregate):
            with mock.patch.object(dashboard_data.crawl_clickhouse, 'load_urls', return_value=url_page):
                with mock.patch.object(dashboard_data.crawl_clickhouse, 'load_links', return_value={'rows': []}):
                    with mock.patch.object(dashboard_data.crawl_clickhouse, 'load_issues', return_value={'rows': self.issues}):
                        with mock.patch.object(dashboard_data.crawl_db, 'get_crawl_by_id', return_value={'base_url': 'https://example.test'}):
                            with mock.patch.object(dashboard_data.crawl_db, 'load_crawl_enrichments', return_value={}):
                                facts = dashboard_data.build_dashboard_snapshot(7)

        self.assertEqual(facts['source'], 'clickhouse_rows')
        self.assertEqual(facts['coverage']['internal_urls'], 3)
        self.assertEqual(facts['http']['4xx'], 1)


if __name__ == '__main__':
    unittest.main()
