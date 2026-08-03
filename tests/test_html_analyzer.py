import unittest

from src.core.html_analyzer import analyze_html_content


class HtmlAnalyzerTests(unittest.TestCase):
    def test_analyze_html_content_extracts_result_links_and_candidates(self):
        html = b'''
            <html lang="es">
              <head>
                <title>Example title with enough length</title>
                <meta name="description" content="Example description">
              </head>
              <body>
                <h1>Main heading</h1>
                <main><a href="/body">Body</a></main>
                <nav><a href="/nav">Nav</a></nav>
                <img src="/img.png" alt="Logo">
              </body>
            </html>
        '''

        analysis = analyze_html_content(
            html,
            'https://example.com/source',
            1,
            200,
            'text/html',
            True,
            'example.com',
            persist_links=True,
            allowed_placements=['body', 'image'],
        )

        self.assertEqual(analysis['result']['title'], 'Example title with enough length')
        self.assertEqual(analysis['result']['h1'], 'Main heading')
        self.assertEqual(
            [link['target_url'] for link in analysis['links']],
            ['https://example.com/body', 'https://example.com/img.png'],
        )
        self.assertEqual(
            [item['url'] for item in analysis['discovered_urls']],
            ['https://example.com/body', 'https://example.com/nav'],
        )

    def test_body_theme_menu_class_keeps_content_links_as_body(self):
        html = b'''
            <html>
              <body class="et_secondary_nav_only_menu et_divi_theme">
                <main><article><a href="/body">Body</a></article></main>
                <nav><a href="/nav">Nav</a></nav>
                <footer><a href="/footer">Footer</a></footer>
              </body>
            </html>
        '''

        analysis = analyze_html_content(
            html,
            'https://example.com/source',
            1,
            200,
            'text/html',
            True,
            'example.com',
            persist_links=True,
            allowed_placements=['body'],
        )

        self.assertEqual([link['target_url'] for link in analysis['links']], ['https://example.com/body'])


if __name__ == '__main__':
    unittest.main()
