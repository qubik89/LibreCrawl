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

    def test_crawl_stats_are_throttled_unless_forced(self):
        old_storage = os.environ.get('CRAWL_RESULT_STORAGE')
        crawler = WebCrawler()
        crawler.crawl_id = 10
        crawler.db_save_enabled = True
        crawler.stats_save_interval = 60
        crawler.last_stats_save_time = 1000
        crawler.stats.update({'discovered': 5, 'crawled': 2, 'depth': 1})

        try:
            os.environ['CRAWL_RESULT_STORAGE'] = 'clickhouse'
            with (
                mock.patch('src.crawl_db.update_crawl_stats') as update_stats,
                mock.patch('src.crawler.time.time', side_effect=[1010, 1070, 1070]),
            ):
                crawler._save_batch_to_db()
                crawler._save_batch_to_db()

            self.assertEqual(update_stats.call_count, 1)

            with mock.patch('src.crawl_db.update_crawl_stats') as update_stats:
                crawler._save_batch_to_db(force=True)

            self.assertEqual(update_stats.call_count, 1)
        finally:
            if old_storage is None:
                os.environ.pop('CRAWL_RESULT_STORAGE', None)
            else:
                os.environ['CRAWL_RESULT_STORAGE'] = old_storage

    def test_resume_preserves_persisted_crawled_count_when_rows_are_external(self):
        crawler = WebCrawler()
        crawl_data = {
            'id': 11,
            'user_id': 1,
            'status': 'paused',
            'base_url': 'https://example.com',
            'base_domain': 'example.com',
            'config_snapshot': crawler._get_default_config(),
            'urls_crawled': 1234,
            'urls_discovered': 5000,
            'max_depth_reached': 3,
            'resume_checkpoint': {},
        }

        with (
            mock.patch('src.crawl_db.get_resume_data', return_value=crawl_data),
            mock.patch('src.crawl_db.load_crawled_urls', return_value=[]),
            mock.patch('src.crawl_db.load_crawl_links', return_value=[]),
            mock.patch('src.crawl_db.load_crawl_issues', return_value=[]),
            mock.patch('src.crawl_db.load_crawl_queue', return_value=[('https://example.com/next', 1)]),
            mock.patch('src.crawl_db.set_crawl_status', return_value=True),
            mock.patch.object(WebCrawler, '_start_auto_save_thread'),
            mock.patch('src.crawler.threading.Thread', side_effect=lambda target: SimpleNamespace(start=lambda: None)),
        ):
            success, message = crawler.resume_from_database(11, user_id=1, session_id='s')

        self.assertTrue(success)
        self.assertEqual(crawler.stats['crawled'], 1234)
        self.assertEqual(message, 'Resumed crawl from 1234 URLs')

    def test_runtime_env_overrides_resume_config_and_skips_link_load(self):
        old_env = {
            key: os.environ.get(key)
            for key in (
                'CRAWL_CONCURRENCY',
                'CRAWL_BATCH_SAVE_SIZE',
                'CRAWL_PERSIST_LINKS',
                'CRAWL_ENABLE_DUPLICATION_CHECK',
            )
        }
        crawl_data = {
            'id': 12,
            'user_id': 1,
            'status': 'paused',
            'base_url': 'https://example.com',
            'base_domain': 'example.com',
            'config_snapshot': {
                **WebCrawler()._get_default_config(),
                'concurrency': 20,
                'persist_links': True,
                'enable_duplication_check': True,
            },
            'urls_crawled': 5,
            'urls_discovered': 10,
            'max_depth_reached': 1,
            'resume_checkpoint': {},
        }

        try:
            os.environ['CRAWL_CONCURRENCY'] = '50'
            os.environ['CRAWL_BATCH_SAVE_SIZE'] = '500'
            os.environ['CRAWL_PERSIST_LINKS'] = 'false'
            os.environ['CRAWL_ENABLE_DUPLICATION_CHECK'] = 'false'

            crawler = WebCrawler()
            with (
                mock.patch('src.crawl_db.get_resume_data', return_value=crawl_data),
                mock.patch('src.crawl_db.load_crawled_urls', return_value=[]),
                mock.patch('src.crawl_db.load_crawl_links', return_value=[{'source_url': 'a', 'target_url': 'b'}]) as load_links,
                mock.patch('src.crawl_db.load_crawl_issues', return_value=[]),
                mock.patch('src.crawl_db.load_crawl_queue', return_value=[]),
                mock.patch('src.crawl_db.set_crawl_status', return_value=True),
                mock.patch.object(WebCrawler, '_start_auto_save_thread'),
                mock.patch('src.crawler.threading.Thread', side_effect=lambda target: SimpleNamespace(start=lambda: None)),
            ):
                success, _message = crawler.resume_from_database(12, user_id=1, session_id='s')

            self.assertTrue(success)
            self.assertEqual(crawler.config['concurrency'], 50)
            self.assertEqual(crawler.batch_save_size, 500)
            self.assertFalse(crawler.config['persist_links'])
            self.assertFalse(crawler.config['enable_duplication_check'])
            load_links.assert_not_called()
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == '__main__':
    unittest.main()
