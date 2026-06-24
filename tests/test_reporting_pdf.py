import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import reporting_pdf


class ReportingPdfTest(unittest.TestCase):
    def test_report_output_paths_are_rooted_and_reject_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir).resolve()
            paths = reporting_pdf.report_output_paths('42', report_id='7', base_dir=tmpdir)

            self.assertEqual(paths['dir'], base_dir / '42')
            self.assertEqual(paths['markdown'], base_dir / '42' / 'report-7.md')
            self.assertEqual(paths['html'], base_dir / '42' / 'report-7.html')
            self.assertEqual(paths['pdf'], base_dir / '42' / 'report-7.pdf')
            for path in paths.values():
                self.assertTrue(path.resolve().is_relative_to(base_dir))

            with self.assertRaises(ValueError):
                reporting_pdf.report_output_dir('../42', base_dir=tmpdir)
            with self.assertRaises(ValueError):
                reporting_pdf.report_output_paths(42, report_id='../../7', base_dir=tmpdir)

    def test_render_report_html_contains_branding_and_sanitizes_color(self):
        html = reporting_pdf.render_report_html(
            '# Audit\n\nFinding **one**.\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n![secret](file:///etc/passwd)\n\n![metadata](http://169.254.169.254/latest/meta-data/)',
            {
                'agency_name': 'ACME <SEO>',
                'primary_color': 'red; background:url(javascript:alert(1))',
                'footer_text': 'Prepared for <Client>',
                'logo_path': '/static/logo.png',
            },
            {'id': 42, 'base_url': 'https://example.com', 'base_domain': 'example.com'},
        )

        self.assertIn('ACME &lt;SEO&gt;', html)
        self.assertIn('Prepared for &lt;Client&gt;', html)
        self.assertIn('https://example.com', html)
        self.assertIn('/static/logo.png', html)
        self.assertIn('<h1>Audit</h1>', html)
        self.assertIn('<strong>one</strong>', html)
        self.assertIn('--brand-primary: #2563eb;', html)
        self.assertNotIn('javascript:alert', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertIn('<img class="logo"', html)
        self.assertNotIn('file:///etc/passwd', html)
        self.assertNotIn('169.254.169.254', html)
        self.assertNotIn('href="javascript:', html)

        unsafe_logo_html = reporting_pdf.render_report_html(
            '# Audit',
            {'logo_path': 'file:///etc/passwd'},
            {},
        )
        self.assertNotIn('file:///etc/passwd', unsafe_logo_html)

    def test_render_report_pdf_uses_playwright_without_launching_browser(self):
        page = mock.Mock()
        browser = mock.Mock()
        browser.new_page.return_value = page
        chromium = mock.Mock()
        chromium.launch.return_value = browser
        playwright = mock.Mock()
        playwright.chromium = chromium
        manager = mock.MagicMock()
        manager.__enter__.return_value = playwright

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / '42' / 'report.pdf'
            with mock.patch.object(reporting_pdf, 'DEFAULT_REPORTS_BASE_DIR', Path(tmpdir)):
                with mock.patch.object(reporting_pdf, 'sync_playwright', return_value=manager):
                    result = reporting_pdf.render_report_pdf('<h1>Report</h1>', output_path)
            self.assertEqual(output_path.parent.exists(), True)

        self.assertEqual(result, output_path.resolve())
        chromium.launch.assert_called_once_with(headless=True, args=['--no-sandbox'])
        page.set_content.assert_called_once_with('<h1>Report</h1>', wait_until='networkidle')
        page.pdf.assert_called_once_with(path=str(output_path.resolve()), format='A4', print_background=True)
        browser.close.assert_called_once_with()

    def test_render_report_pdf_rejects_paths_outside_reports_base_and_closes_on_error(self):
        page = mock.Mock()
        page.pdf.side_effect = RuntimeError('pdf failed')
        browser = mock.Mock()
        browser.new_page.return_value = page
        chromium = mock.Mock()
        chromium.launch.return_value = browser
        playwright = mock.Mock()
        playwright.chromium = chromium
        manager = mock.MagicMock()
        manager.__enter__.return_value = playwright

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(reporting_pdf, 'DEFAULT_REPORTS_BASE_DIR', Path(tmpdir)):
                with self.assertRaises(ValueError):
                    reporting_pdf.render_report_pdf('<h1>Report</h1>', Path(tmpdir).parent / 'escape.pdf')

                output_path = Path(tmpdir) / '42' / 'report.pdf'
                with mock.patch.object(reporting_pdf, 'sync_playwright', return_value=manager):
                    with self.assertRaisesRegex(RuntimeError, 'pdf failed'):
                        reporting_pdf.render_report_pdf('<h1>Report</h1>', output_path)

        browser.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
