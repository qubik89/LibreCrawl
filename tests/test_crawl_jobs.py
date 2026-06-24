import unittest

from src import crawl_jobs


class FakeCrawler:
    def __init__(self, crawl_id):
        self.crawl_id = crawl_id


class CrawlJobsTest(unittest.TestCase):
    def tearDown(self):
        for crawl_id in crawl_jobs.list_active_ids():
            crawl_jobs.unregister(crawl_id)

    def test_register_get_unregister(self):
        crawler = FakeCrawler(123)

        self.assertEqual(crawl_jobs.register(crawler), 123)
        self.assertIs(crawl_jobs.get(123), crawler)
        self.assertTrue(crawl_jobs.is_active(123))
        self.assertEqual(crawl_jobs.list_active_ids(), [123])

        self.assertIs(crawl_jobs.unregister(123), crawler)
        self.assertIsNone(crawl_jobs.get(123))
        self.assertFalse(crawl_jobs.is_active(123))

    def test_ignores_crawler_without_crawl_id(self):
        self.assertIsNone(crawl_jobs.register(FakeCrawler(None)))
        self.assertEqual(crawl_jobs.list_active_ids(), [])


if __name__ == '__main__':
    unittest.main()
