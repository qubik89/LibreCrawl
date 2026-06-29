import os
import sys
import threading
import types
import unittest

from src import crawl_clickhouse


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


if __name__ == '__main__':
    unittest.main()
