import tempfile
import unittest
from pathlib import Path

from src import crawl_db


class CrawlDbQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_file = crawl_db.DB_FILE
        self.old_queue_dir = crawl_db.QUEUE_DIR
        crawl_db.DB_FILE = str(Path(self.tmp.name) / 'users.db')
        crawl_db.QUEUE_DIR = str(Path(self.tmp.name) / 'crawl_queues')
        crawl_db.init_crawl_tables()
        self.crawl_id = crawl_db.create_crawl(None, 'session-a', 'https://example.com', 'example.com', {})

    def tearDown(self):
        crawl_db.DB_FILE = self.old_db_file
        crawl_db.QUEUE_DIR = self.old_queue_dir
        self.tmp.cleanup()

    def test_replace_and_load_crawl_queue(self):
        self.assertTrue(crawl_db.replace_crawl_queue(self.crawl_id, [
            ('https://example.com/a', 0),
            ('https://example.com/b', 2),
        ]))

        self.assertEqual(crawl_db.load_crawl_queue(self.crawl_id), [
            ('https://example.com/a', 0),
            ('https://example.com/b', 2),
        ])

        self.assertTrue(crawl_db.replace_crawl_queue(self.crawl_id, [
            ('https://example.com/c', 1),
        ]))
        self.assertEqual(crawl_db.load_crawl_queue(self.crawl_id), [
            ('https://example.com/c', 1),
        ])


if __name__ == '__main__':
    unittest.main()
