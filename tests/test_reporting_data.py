import sys
import types
import unittest
from unittest import mock

from src import reporting_data


class FakeDb:
    def __init__(self, result_sets):
        self.result_sets = list(result_sets)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self

    def execute(self, query, params):
        self.calls.append((query, params))

    def fetchall(self):
        return self.result_sets.pop(0)


class ReportingDataTest(unittest.TestCase):
    def test_build_audit_packet_prefers_clickhouse_samples_and_respects_limits(self):
        fake_main = types.SimpleNamespace(
            build_crawl_summary=mock.Mock(return_value={'counts': {'urls': 99, 'links': 7, 'issues': 3}})
        )
        analytics = {
            'source': 'clickhouse',
            'counts': {'urls': 99, 'links': 7, 'issues': 3},
            'status_counts': [
                {'status_code': 200, 'error_type': None, 'count': 80},
                {'status_code': 404, 'error_type': None, 'count': 9},
                {'status_code': 0, 'error_type': 'timeout', 'count': 2},
            ],
            'issue_type_counts': {'warning': 5, 'error': 2},
        }

        with mock.patch.dict(sys.modules, {'main': fake_main}):
            with mock.patch.object(reporting_data.crawl_clickhouse, 'get_summary', return_value=analytics):
                with mock.patch.object(reporting_data.crawl_clickhouse, 'load_urls', return_value={'total': 99, 'rows': [{'url': 'https://example.com'}]}) as load_urls:
                        with mock.patch.object(reporting_data.crawl_clickhouse, 'load_links', return_value={'total': 7, 'rows': [{'target_url': '/a'}]}) as load_links:
                            with mock.patch.object(reporting_data.crawl_clickhouse, 'load_issues', return_value={'total': 3, 'rows': [{'type': 'warning'}]}) as load_issues:
                                with mock.patch.object(reporting_data.crawl_db, 'get_crawl_by_id', return_value={'id': 42, 'base_url': 'https://example.com'}):
                                    with mock.patch.object(reporting_data.crawl_db, 'get_crawl_counts', return_value={'urls': 99, 'links': 7, 'issues': 3}):
                                        packet = reporting_data.build_audit_packet(42, sample_limit=7, issue_limit=11)

        self.assertEqual(packet['crawl_id'], 42)
        self.assertEqual(packet['crawl_summary']['counts']['urls'], 99)
        self.assertEqual(packet['crawl_metadata']['base_url'], 'https://example.com')
        self.assertEqual(packet['analytics_summary'], analytics)
        self.assertEqual(packet['top_status_issues'], [
            {'status_code': 404, 'error_type': None, 'count': 9},
            {'status_code': 0, 'error_type': 'timeout', 'count': 2},
        ])
        self.assertEqual(packet['top_issue_groups'][0], {'type': 'warning', 'count': 5})
        self.assertEqual(packet['samples']['urls']['source'], 'clickhouse')
        self.assertEqual(packet['samples']['links']['rows'], [{'target_url': '/a'}])
        self.assertEqual(packet['samples']['issues']['total'], 3)
        self.assertEqual(packet['limits'], {'sample_limit': 7, 'issue_limit': 11, 'top_limit': 10})
        load_urls.assert_called_once_with(42, limit=7, offset=0)
        load_links.assert_called_once_with(42, limit=7, offset=0)
        load_issues.assert_called_once_with(42, limit=11, offset=0)

    def test_build_audit_packet_falls_back_to_sqlite(self):
        fake_main = types.SimpleNamespace(build_crawl_summary=mock.Mock(return_value={'counts': {}}))
        fake_db = FakeDb([
            [{'status_code': 500, 'error_type': None, 'count': 4}],
            [{'type': 'error', 'category': 'http', 'issue': 'Server error', 'count': 4}],
        ])

        with mock.patch.dict(sys.modules, {'main': fake_main}):
            with mock.patch.object(reporting_data.crawl_clickhouse, 'get_summary', return_value=None):
                with mock.patch.object(reporting_data.crawl_clickhouse, 'load_urls', return_value=None):
                    with mock.patch.object(reporting_data.crawl_clickhouse, 'load_links', return_value=None):
                        with mock.patch.object(reporting_data.crawl_clickhouse, 'load_issues', return_value=None):
                            with mock.patch.object(reporting_data.crawl_db, 'get_crawl_by_id', return_value={'id': 8}):
                                with mock.patch.object(reporting_data.crawl_db, 'get_crawl_counts', return_value={'urls': 2, 'links': 1, 'issues': 1}):
                                    with mock.patch.object(reporting_data.crawl_db, 'load_crawled_urls', return_value=[{'url': 'u'}]) as load_urls:
                                        with mock.patch.object(reporting_data.crawl_db, 'load_crawl_links', return_value=[{'target_url': 't'}]) as load_links:
                                            with mock.patch.object(reporting_data.crawl_db, 'load_crawl_issues', return_value=[{'issue': 'i'}]) as load_issues:
                                                with mock.patch.object(reporting_data.crawl_db, 'get_db', return_value=fake_db):
                                                    packet = reporting_data.build_audit_packet(8, sample_limit=3, issue_limit=4)

        self.assertIsNone(packet['analytics_summary'])
        self.assertEqual(packet['top_status_issues'], [{'status_code': 500, 'error_type': None, 'count': 4}])
        self.assertEqual(packet['top_issue_groups'], [{'type': 'error', 'category': 'http', 'issue': 'Server error', 'count': 4}])
        self.assertEqual(packet['samples']['urls'], {'source': 'sqlite', 'total': 2, 'rows': [{'url': 'u'}]})
        self.assertEqual(packet['samples']['links']['total'], 1)
        self.assertEqual(packet['samples']['issues']['source'], 'sqlite')
        load_urls.assert_called_once_with(8, limit=3, offset=0)
        load_links.assert_called_once_with(8, limit=3, offset=0)
        load_issues.assert_called_once_with(8, limit=4, offset=0)
        self.assertEqual(fake_db.calls[0][1], (8, 10))
        self.assertEqual(fake_db.calls[1][1], (8, 10))

    def test_build_audit_packet_drops_large_checkpoint_fields(self):
        fake_main = types.SimpleNamespace(build_crawl_summary=mock.Mock(return_value={
            'crawl_id': 9,
            'counts': {'urls': 1},
            'crawl': {
                'id': 9,
                'base_url': 'https://example.com',
                'resume_checkpoint': {'visited_urls': ['u'] * 1000},
                'config_snapshot': {'custom': 'large'},
            },
        }))
        metadata = {
            'id': 9,
            'base_url': 'https://example.com',
            'resume_checkpoint': {'visited_urls': ['u'] * 1000},
            'config_snapshot': {'custom': 'large'},
        }

        with mock.patch.dict(sys.modules, {'main': fake_main}):
            with mock.patch.object(reporting_data.crawl_clickhouse, 'get_summary', return_value=None):
                with mock.patch.object(reporting_data.crawl_clickhouse, 'load_urls', return_value={'total': 1, 'rows': []}):
                    with mock.patch.object(reporting_data.crawl_clickhouse, 'load_links', return_value={'total': 0, 'rows': []}):
                        with mock.patch.object(reporting_data.crawl_clickhouse, 'load_issues', return_value={'total': 0, 'rows': []}):
                            with mock.patch.object(reporting_data.crawl_db, 'get_crawl_by_id', return_value=metadata):
                                with mock.patch.object(reporting_data.crawl_db, 'get_crawl_counts', return_value={'urls': 1, 'links': 0, 'issues': 0}):
                                    packet = reporting_data.build_audit_packet(9)

        self.assertNotIn('resume_checkpoint', packet['crawl_metadata'])
        self.assertNotIn('config_snapshot', packet['crawl_metadata'])
        self.assertNotIn('resume_checkpoint', packet['crawl_summary']['crawl'])

    def test_build_audit_packet_never_passes_zero_limit_to_sqlite(self):
        fake_main = types.SimpleNamespace(build_crawl_summary=mock.Mock(return_value={'counts': {}}))
        fake_db = FakeDb([[], []])

        with mock.patch.dict(sys.modules, {'main': fake_main}):
            with mock.patch.object(reporting_data.crawl_clickhouse, 'get_summary', return_value=None):
                with mock.patch.object(reporting_data.crawl_clickhouse, 'load_urls', return_value=None):
                    with mock.patch.object(reporting_data.crawl_clickhouse, 'load_links', return_value=None):
                        with mock.patch.object(reporting_data.crawl_clickhouse, 'load_issues', return_value=None):
                            with mock.patch.object(reporting_data.crawl_db, 'get_crawl_by_id', return_value={'id': 10}):
                                with mock.patch.object(reporting_data.crawl_db, 'get_crawl_counts', return_value={'urls': 2, 'links': 2, 'issues': 2}):
                                    with mock.patch.object(reporting_data.crawl_db, 'load_crawled_urls', return_value=[]) as load_urls:
                                        with mock.patch.object(reporting_data.crawl_db, 'load_crawl_links', return_value=[]) as load_links:
                                            with mock.patch.object(reporting_data.crawl_db, 'load_crawl_issues', return_value=[]) as load_issues:
                                                with mock.patch.object(reporting_data.crawl_db, 'get_db', return_value=fake_db):
                                                    packet = reporting_data.build_audit_packet(10, sample_limit=0, issue_limit=-5)

        self.assertEqual(packet['limits']['sample_limit'], 1)
        self.assertEqual(packet['limits']['issue_limit'], 1)
        load_urls.assert_called_once_with(10, limit=1, offset=0)
        load_links.assert_called_once_with(10, limit=1, offset=0)
        load_issues.assert_called_once_with(10, limit=1, offset=0)


if __name__ == '__main__':
    unittest.main()
