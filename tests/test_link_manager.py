import unittest

from bs4 import BeautifulSoup

from src.core.link_manager import LinkManager


class LinkManagerTests(unittest.TestCase):
    def test_collect_all_links_uses_status_lookup(self):
        manager = LinkManager('example.com')
        soup = BeautifulSoup('<a href="/target">Target</a>', 'html.parser')

        manager.collect_all_links(
            soup,
            'https://example.com/source',
            {'https://example.com/target': 200},
        )

        self.assertEqual(manager.all_links[0]['target_status'], 200)

    def test_collect_all_links_still_accepts_result_rows(self):
        manager = LinkManager('example.com')
        soup = BeautifulSoup('<a href="/target">Target</a>', 'html.parser')

        manager.collect_all_links(
            soup,
            'https://example.com/source',
            [{'url': 'https://example.com/target', 'status_code': 200}],
        )

        self.assertEqual(manager.all_links[0]['target_status'], 200)

    def test_collect_all_links_can_filter_by_placement(self):
        manager = LinkManager('example.com')
        soup = BeautifulSoup(
            '''
            <nav><a href="/nav">Nav</a></nav>
            <main><a href="/body">Body</a><img src="/image.jpg" alt="Image"></main>
            <footer><a href="/footer">Footer</a></footer>
            ''',
            'html.parser',
        )

        manager.collect_all_links(
            soup,
            'https://example.com/source',
            {},
            allowed_placements=['body'],
        )

        self.assertEqual([link['target_url'] for link in manager.all_links], ['https://example.com/body'])


if __name__ == '__main__':
    unittest.main()
