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

    def test_collect_all_links_can_cap_links_per_page(self):
        manager = LinkManager('example.com')
        soup = BeautifulSoup(
            '<main><a href="/one">One</a><a href="/two">Two</a><a href="/three">Three</a></main>',
            'html.parser',
        )

        manager.collect_all_links(
            soup,
            'https://example.com/source',
            {},
            allowed_placements=['body'],
            max_links=2,
        )

        self.assertEqual(
            [link['target_url'] for link in manager.all_links],
            ['https://example.com/one', 'https://example.com/two'],
        )

    def test_collect_all_links_returns_only_current_new_links(self):
        manager = LinkManager('example.com')
        first = BeautifulSoup('<main><a href="/one">One</a></main>', 'html.parser')
        second = BeautifulSoup('<main><a href="/two">Two</a></main>', 'html.parser')

        first_links = manager.collect_all_links(first, 'https://example.com/a', {})
        second_links = manager.collect_all_links(second, 'https://example.com/b', {})

        self.assertEqual([link['target_url'] for link in first_links], ['https://example.com/one'])
        self.assertEqual([link['target_url'] for link in second_links], ['https://example.com/two'])

    def test_apply_collected_links_dedupes_caps_and_adds_status(self):
        manager = LinkManager('example.com')
        candidates = [
            {
                'source_url': 'https://example.com/source',
                'target_url': 'https://example.com/one',
                'anchor_text': 'One',
                'is_internal': True,
                'target_domain': 'example.com',
                'target_status': None,
                'placement': 'body',
            },
            {
                'source_url': 'https://example.com/source',
                'target_url': 'https://example.com/one',
                'anchor_text': 'One again',
                'is_internal': True,
                'target_domain': 'example.com',
                'target_status': None,
                'placement': 'body',
            },
            {
                'source_url': 'https://example.com/source',
                'target_url': 'https://example.com/two',
                'anchor_text': 'Two',
                'is_internal': True,
                'target_domain': 'example.com',
                'target_status': None,
                'placement': 'body',
            },
        ]

        links = manager.apply_collected_links(
            candidates,
            {'https://example.com/one': 200, 'https://example.com/two': 404},
            max_links=2,
        )

        self.assertEqual([link['target_url'] for link in links], ['https://example.com/one', 'https://example.com/two'])
        self.assertEqual([link['target_status'] for link in links], [200, 404])
        self.assertEqual(manager.get_source_pages('https://example.com/one'), ['https://example.com/source'])

    def test_apply_collected_links_can_skip_memory_retention(self):
        manager = LinkManager('example.com', retain_link_state=False)
        candidates = [
            {
                'source_url': 'https://example.com/source',
                'target_url': 'https://example.com/one',
                'anchor_text': 'One',
                'is_internal': True,
                'target_domain': 'example.com',
                'target_status': None,
                'placement': 'body',
            },
            {
                'source_url': 'https://example.com/source',
                'target_url': 'https://example.com/one',
                'anchor_text': 'Duplicate',
                'is_internal': True,
                'target_domain': 'example.com',
                'target_status': None,
                'placement': 'body',
            },
        ]

        links = manager.apply_collected_links(candidates, {'https://example.com/one': 200})

        self.assertEqual(len(links), 1)
        self.assertEqual(manager.all_links, [])
        self.assertEqual(manager.links_set, set())
        self.assertEqual(manager.get_source_pages('https://example.com/one'), [])


if __name__ == '__main__':
    unittest.main()
