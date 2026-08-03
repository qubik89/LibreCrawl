import json
import os
import sys
import threading
import types
import unittest
from unittest import mock

from src import crawl_clickhouse


class FakeQueryResult:
    def __init__(self, rows):
        self.result_rows = rows


class FakeClickHouseClient:
    def __init__(self, rows=None, total=1):
        self.rows = rows or [(101, '{"url":"https://example.com/a"}')]
        self.total = total
        self.sql = []

    def query(self, sql):
        self.sql.append(sql)
        if 'count()' in sql:
            return FakeQueryResult([(self.total,)])
        return FakeQueryResult(self.rows)


class CrawlClickHouseTest(unittest.TestCase):
    def test_disabled_without_env(self):
        old = os.environ.pop('CLICKHOUSE_ENABLED', None)
        try:
            self.assertFalse(crawl_clickhouse.enabled())
            self.assertIsNone(crawl_clickhouse.get_client())
        finally:
            if old is not None:
                os.environ['CLICKHOUSE_ENABLED'] = old

    def test_url_row_conversion_preserves_json_payload(self):
        rows = crawl_clickhouse.url_rows(7, [{
            'url': 'https://example.com/a',
            'status_code': 200,
            'content_type': 'text/html',
            'is_internal': True,
            'depth': 2,
            'title': 'Title',
            'meta_description': 'Desc',
            'h1': 'H1',
            'word_count': 123,
            'response_time': 45.6,
            'size': 789,
            'javascript_rendered': False,
        }], base_order=100)

        self.assertEqual(rows[0][0], 7)
        self.assertEqual(rows[0][1], 100)
        self.assertEqual(rows[0][2], 'https://example.com/a')
        self.assertEqual(rows[0][3], 200)
        self.assertEqual(rows[0][6], 1)
        self.assertIn('"url":"https://example.com/a"', rows[0][-1])

    def test_link_and_issue_rows_are_safe_with_missing_values(self):
        self.assertEqual(crawl_clickhouse.link_rows(1, [{'target_url': 'x'}], base_order=1)[0][5], 0)
        issue = crawl_clickhouse.issue_rows(1, [{'url': 'u', 'type': 'warning'}], base_order=1)[0]
        self.assertEqual(issue[2], 'u')
        self.assertEqual(issue[3], 'warning')

    def test_client_is_thread_local(self):
        old_enabled = os.environ.get('CLICKHOUSE_ENABLED')
        old_module = sys.modules.get('clickhouse_connect')
        old_client = crawl_clickhouse._client
        old_ready = crawl_clickhouse._ready
        clients = []

        class FakeClient:
            def command(self, _sql):
                return None

        def get_client(**_kwargs):
            client = FakeClient()
            clients.append(client)
            return client

        try:
            os.environ['CLICKHOUSE_ENABLED'] = 'true'
            sys.modules['clickhouse_connect'] = types.SimpleNamespace(get_client=get_client)
            crawl_clickhouse._client = None
            crawl_clickhouse._ready = False

            seen = []
            threads = [
                threading.Thread(target=lambda: seen.append(crawl_clickhouse.get_client()))
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(len(clients), 2)
            self.assertIsNot(seen[0], seen[1])
        finally:
            if old_enabled is None:
                os.environ.pop('CLICKHOUSE_ENABLED', None)
            else:
                os.environ['CLICKHOUSE_ENABLED'] = old_enabled
            if old_module is None:
                sys.modules.pop('clickhouse_connect', None)
            else:
                sys.modules['clickhouse_connect'] = old_module
            crawl_clickhouse._client = old_client
            crawl_clickhouse._ready = old_ready

    def test_load_urls_uses_cursor_filter_order_and_returns_row_order(self):
        client = FakeClickHouseClient()

        with mock.patch.object(crawl_clickhouse, 'get_client', return_value=client):
            page = crawl_clickhouse.load_urls(
                crawl_id=7,
                limit=50,
                after=100,
                filters={'kind': 'internal'},
            )

        select_sql = client.sql[-1]
        self.assertIn('row_order > 100', select_sql)
        self.assertIn('is_internal = 1', select_sql)
        self.assertIn('ORDER BY row_order ASC', select_sql)
        self.assertNotIn('OFFSET', select_sql)
        self.assertEqual(page['rows'][0]['_row_order'], '101')

    def test_load_rows_preserves_uint64_cursor_precision_for_json_clients(self):
        first_order = 1785696868549154891
        client = FakeClickHouseClient(rows=[
            (first_order, '{"url":"https://example.com/a"}'),
            (first_order + 1, '{"url":"https://example.com/b"}'),
        ], total=2)

        with mock.patch.object(crawl_clickhouse, 'get_client', return_value=client):
            page = crawl_clickhouse.load_issues(
                crawl_id=9,
                limit=2,
                after=str(first_order - 1),
            )

        encoded = json.dumps(page['rows'])
        decoded = json.loads(encoded)
        cursors = [row['_row_order'] for row in decoded]
        self.assertEqual(cursors, [str(first_order), str(first_order + 1)])
        self.assertEqual(len(set(cursors)), 2)
        self.assertIn(f'row_order > {first_order - 1}', client.sql[-1])

    def test_load_issues_allows_issue_type_filter(self):
        for issue_type in ('error', 'warning', 'info'):
            with self.subTest(issue_type=issue_type):
                client = FakeClickHouseClient(rows=[(101, json.dumps({'type': issue_type}))])

                with mock.patch.object(crawl_clickhouse, 'get_client', return_value=client):
                    page = crawl_clickhouse.load_issues(
                        crawl_id=7,
                        limit=10,
                        filters={'issue_type': issue_type},
                    )

                self.assertIn(f"type = '{issue_type}'", client.sql[-1])
                self.assertEqual(page['rows'][0]['_row_order'], '101')

    def test_dashboard_filters_keep_legacy_kind_and_support_exact_combinations(self):
        client = FakeClickHouseClient()
        with mock.patch.object(crawl_clickhouse, 'get_client', return_value=client):
            crawl_clickhouse.load_urls(7, limit=10, filters={
                'kind': 'internal', 'status_family': '4xx',
                'content_type': 'html', 'depth': '2',
            })
        self.assertIn('is_internal = 1', client.sql[-1])
        self.assertIn('status_code >= 400 AND status_code < 500', client.sql[-1])
        self.assertIn("positionCaseInsensitive(content_type, 'html') > 0", client.sql[-1])
        self.assertIn('depth = 2', client.sql[-1])

        client = FakeClickHouseClient()
        with mock.patch.object(crawl_clickhouse, 'get_client', return_value=client):
            crawl_clickhouse.load_issues(7, limit=10, filters={
                'category': 'SEO', 'issues': ['Missing Title Tag', "Bob's issue"],
            })
        self.assertIn("category = 'SEO'", client.sql[-1])
        self.assertIn("issue IN ('Missing Title Tag', 'Bob\\'s issue')", client.sql[-1])

    def test_load_recent_urls_uses_descending_row_order(self):
        client = FakeClickHouseClient()

        with mock.patch.object(crawl_clickhouse, 'get_client', return_value=client):
            crawl_clickhouse.load_recent_urls(crawl_id=7, limit=5)

        self.assertIn('ORDER BY row_order DESC', client.sql[-1])

    def test_dashboard_exclusions_use_clickhouse_native_like_escaping(self):
        sql = crawl_clickhouse._dashboard_exclusion_conditions(['/private/*', '/under_score'])

        self.assertIn("NOT LIKE '/private/%'", sql)
        self.assertIn("NOT LIKE '/under\\_score'", sql)
        self.assertNotIn(' ESCAPE ', sql)

    def test_report_fact_ctes_do_not_reuse_row_json_as_an_aggregate_alias(self):
        class ReportFactsClient:
            def __init__(self):
                self.sql = []

            def query(self, sql):
                self.sql.append(sql)
                if 'quantileExact(0.5)' in sql:
                    return FakeQueryResult([(
                        1, 1, 1, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0,
                        100, 100, 100, 100,
                    )])
                if 'GROUP BY status_code, error_type' in sql:
                    return FakeQueryResult([(200, '', 1)])
                if 'GROUP BY depth' in sql:
                    return FakeQueryResult([(0, 1)])
                if 'GROUP BY category, issue, type' in sql:
                    return FakeQueryResult([])
                if 'SELECT count() FROM latest_issues' in sql:
                    return FakeQueryResult([(0,)])
                if 'countIf(is_internal = 1)' in sql:
                    return FakeQueryResult([(0, 0)])
                if 'SELECT latest_row_json FROM latest_' in sql:
                    return FakeQueryResult([])
                if "positionCaseInsensitive(JSONExtractString(latest_row_json, 'robots')" in sql:
                    return FakeQueryResult([(0,)])
                if "JSONHas(latest_row_json, 'in_sitemap')" in sql:
                    return FakeQueryResult([(0, 0)])
                raise AssertionError(sql)

        client = ReportFactsClient()
        with mock.patch.object(crawl_clickhouse, 'get_client', return_value=client):
            facts = crawl_clickhouse.get_report_facts(7)

        self.assertIsNotNone(facts)
        combined_sql = '\n'.join(client.sql)
        self.assertNotIn('argMax(row_json, row_order) AS row_json', combined_sql)
        self.assertIn('argMax(row_json, row_order) AS latest_row_json', combined_sql)
        self.assertIn("AS identity_url", combined_sql)
        self.assertIn('GROUP BY identity_url', combined_sql)
        self.assertNotIn("GROUP BY coalesce(nullIf(JSONExtractString(row_json", combined_sql)


if __name__ == '__main__':
    unittest.main()
