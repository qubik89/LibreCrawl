import unittest

from src.core.issue_detector import IssueDetector


class IssueDetectorV2Test(unittest.TestCase):
    def test_root_canonical_slash_is_not_flagged_as_different(self):
        detector = IssueDetector()
        issues = []
        detector._check_technical_issues({
            'url': 'https://example.test/', 'status_code': 200, 'canonical_url': 'https://example.test'
        }, issues)
        self.assertFalse(any(issue['issue'] == 'Canonical URL Different' for issue in issues))

    def test_contextual_signals_are_not_automatic_errors(self):
        detector = IssueDetector()
        issues = []
        result = {'url': 'https://example.test/private', 'robots': 'noindex, nofollow', 'json_ld': [], 'schema_org': []}
        detector._check_structured_data_issues(result, issues)
        detector._check_indexability_issues(result, issues)
        self.assertTrue(all(issue['type'] == 'info' for issue in issues))

    def test_crawl_duration_is_labelled_as_non_cwv_signal(self):
        detector = IssueDetector()
        issues = []
        detector._check_performance_issues({'url': 'https://example.test/', 'response_time': 4000, 'size': 0}, issues)
        duration = next(issue for issue in issues if issue['category'] == 'Performance')
        self.assertEqual(duration['issue'], 'Elevated Crawl Request Duration')
        self.assertIn('not a Core Web Vital', duration['details'])


if __name__ == '__main__':
    unittest.main()
