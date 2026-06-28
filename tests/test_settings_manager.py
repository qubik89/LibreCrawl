import unittest

from src.settings_manager import SettingsManager


class SettingsManagerTests(unittest.TestCase):
    def test_default_crawl_profile_is_fast_for_whitelisted_sites(self):
        settings = SettingsManager(tier='admin').get_settings()

        self.assertEqual(settings['crawlDelay'], 0)
        self.assertEqual(settings['retries'], 1)
        self.assertEqual(settings['concurrency'], 20)


if __name__ == '__main__':
    unittest.main()
