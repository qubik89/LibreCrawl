import os
import unittest

from src.crawl_storage import result_storage_mode, should_save_sqlite_rows


class CrawlStorageTest(unittest.TestCase):
    def test_result_storage_mode_defaults_and_validates(self):
        old = os.environ.pop('CRAWL_RESULT_STORAGE', None)
        try:
            self.assertEqual(result_storage_mode(), 'both')
            os.environ['CRAWL_RESULT_STORAGE'] = 'clickhouse'
            self.assertEqual(result_storage_mode(), 'clickhouse')
            os.environ['CRAWL_RESULT_STORAGE'] = 'sqlite'
            self.assertEqual(result_storage_mode(), 'sqlite')
            os.environ['CRAWL_RESULT_STORAGE'] = 'bad'
            self.assertEqual(result_storage_mode(), 'both')
        finally:
            if old is not None:
                os.environ['CRAWL_RESULT_STORAGE'] = old
            else:
                os.environ.pop('CRAWL_RESULT_STORAGE', None)

    def test_clickhouse_mode_never_falls_back_to_sqlite_rows(self):
        self.assertFalse(should_save_sqlite_rows('clickhouse', True, True))
        self.assertFalse(should_save_sqlite_rows('clickhouse', True, False))
        self.assertTrue(should_save_sqlite_rows('both', True, True))
        self.assertTrue(should_save_sqlite_rows('sqlite', True, False))


if __name__ == '__main__':
    unittest.main()
