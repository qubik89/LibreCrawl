import unittest
import os
import sys
import threading
import time
from collections import deque
from types import SimpleNamespace
from unittest import mock

sys.modules.setdefault('nest_asyncio', SimpleNamespace(apply=lambda: None))
sys.modules.setdefault(
    'psutil',
    SimpleNamespace(
        Process=lambda: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=0)),
        virtual_memory=lambda: SimpleNamespace(total=0, available=0, used=0, percent=0),
    ),
)

from src.crawler import WebCrawler


class CrawlerCheckpointTests(unittest.TestCase):
    def test_queue_checkpoint_is_throttled_unless_forced(self):
        crawler = WebCrawler()
        crawler.crawl_id = 7
        crawler.db_save_enabled = True
        crawler.queue_checkpoint_interval = 300
        crawler.link_manager = SimpleNamespace(
            discovered_urls=deque([('https://example.com/a', 1)]),
            get_stats=lambda: {'pending': 1},
        )

        with (
            mock.patch('src.crawl_db.replace_crawl_queue', return_value=True) as replace_queue,
            mock.patch('src.crawl_db.save_checkpoint', return_value=True) as save_checkpoint,
            mock.patch('src.crawler.time.time', side_effect=[1000, 1000, 1010, 2000, 2000]),
        ):
            crawler._save_queue_checkpoint()
            crawler._save_queue_checkpoint()
            crawler._save_queue_checkpoint(force=True)

        self.assertEqual(replace_queue.call_count, 2)
        self.assertEqual(save_checkpoint.call_count, 2)

    def test_batch_saves_do_not_overlap(self):
        old_storage = os.environ.get('CRAWL_RESULT_STORAGE')
        crawler = WebCrawler()
        crawler.crawl_id = 9
        crawler.db_save_enabled = True
        crawler.unsaved_urls = [{'url': 'https://example.com'}]
        crawler.unsaved_links = []
        crawler.unsaved_issues = []

        active = 0
        max_active = 0
        active_lock = threading.Lock()

        def save_url_batch(_crawl_id, _urls):
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with active_lock:
                active -= 1

        try:
            os.environ['CRAWL_RESULT_STORAGE'] = 'sqlite'
            with (
                mock.patch('src.crawl_db.save_url_batch', side_effect=save_url_batch),
                mock.patch('src.crawl_db.save_links_batch'),
                mock.patch('src.crawl_db.save_issues_batch'),
                mock.patch('src.crawl_db.update_crawl_stats'),
            ):
                threads = [threading.Thread(target=crawler._save_batch_to_db) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

            self.assertEqual(max_active, 1)
        finally:
            if old_storage is None:
                os.environ.pop('CRAWL_RESULT_STORAGE', None)
            else:
                os.environ['CRAWL_RESULT_STORAGE'] = old_storage


if __name__ == '__main__':
    unittest.main()
