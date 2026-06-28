import unittest
import sys
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


if __name__ == '__main__':
    unittest.main()
