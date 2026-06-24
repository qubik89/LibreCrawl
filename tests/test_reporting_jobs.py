import os
import tempfile
import unittest

from src import reporting_jobs


class ReportingJobsTest(unittest.TestCase):
    def setUp(self):
        old_db_file = reporting_jobs.DB_FILE
        self.addCleanup(reporting_jobs.set_db_file, old_db_file)
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        reporting_jobs.set_db_file(os.path.join(tmpdir.name, 'reporting-jobs.db'))
        reporting_jobs.init_report_job_tables()

    def test_create_get_update_and_list_report_jobs(self):
        report_id = reporting_jobs.create_report_job(
            crawl_id=42,
            language='es-ES',
            tone='executive',
            model='openai/gpt-4.1',
        )

        job = reporting_jobs.get_report_job(report_id)
        self.assertEqual(job['id'], report_id)
        self.assertEqual(job['crawl_id'], 42)
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(job['language'], 'es-ES')
        self.assertEqual(job['tone'], 'executive')
        self.assertEqual(job['model'], 'openai/gpt-4.1')
        self.assertIsNone(job['markdown_path'])
        self.assertEqual(job['usage'], None)

        self.assertTrue(reporting_jobs.update_report_job(
            report_id,
            status='completed',
            markdown_path='/tmp/report.md',
            html_path='/tmp/report.html',
            pdf_path='/tmp/report.pdf',
            usage={'prompt_tokens': 10, 'completion_tokens': 20},
            completed_at='2026-06-24 12:00:00',
        ))

        updated = reporting_jobs.get_report_job(report_id)
        self.assertEqual(updated['status'], 'completed')
        self.assertEqual(updated['markdown_path'], '/tmp/report.md')
        self.assertEqual(updated['html_path'], '/tmp/report.html')
        self.assertEqual(updated['pdf_path'], '/tmp/report.pdf')
        self.assertEqual(updated['usage'], {'prompt_tokens': 10, 'completion_tokens': 20})
        self.assertIsNotNone(updated['updated_at'])

        self.assertEqual(
            [job['id'] for job in reporting_jobs.list_report_jobs_for_crawl(42)],
            [report_id],
        )

    def test_unknown_and_invalid_updates_are_rejected(self):
        report_id = reporting_jobs.create_report_job(1, 'en', 'technical', 'anthropic/claude')

        self.assertFalse(reporting_jobs.update_report_job(999, status='failed'))
        with self.assertRaises(ValueError):
            reporting_jobs.update_report_job(report_id, crawl_id=2)
        with self.assertRaises(ValueError):
            reporting_jobs.update_report_job(report_id, status='complete')

    def test_terminal_status_sets_completed_at_when_missing(self):
        report_id = reporting_jobs.create_report_job(1, 'en', 'technical', 'anthropic/claude')

        self.assertTrue(reporting_jobs.update_report_job(report_id, status='failed'))

        job = reporting_jobs.get_report_job(report_id)
        self.assertEqual(job['status'], 'failed')
        self.assertIsNotNone(job['completed_at'])

    def test_usage_json_is_parsed_defensively(self):
        with reporting_jobs.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO report_jobs (crawl_id, status, usage_json)
                VALUES (?, ?, ?)
            ''', (7, 'failed', '{not-json'))
            report_id = cursor.lastrowid

        job = reporting_jobs.get_report_job(report_id)

        self.assertEqual(job['usage'], None)
        self.assertEqual(job['usage_json'], '{not-json')

    def test_mark_incomplete_jobs_failed(self):
        queued_id = reporting_jobs.create_report_job(1, 'en', 'technical', 'anthropic/claude')
        running_id = reporting_jobs.create_report_job(1, 'en', 'technical', 'anthropic/claude')
        completed_id = reporting_jobs.create_report_job(1, 'en', 'technical', 'anthropic/claude')
        reporting_jobs.update_report_job(running_id, status='running')
        reporting_jobs.update_report_job(completed_id, status='completed')

        count = reporting_jobs.mark_incomplete_jobs_failed('interrupted')

        self.assertEqual(count, 2)
        self.assertEqual(reporting_jobs.get_report_job(queued_id)['status'], 'failed')
        self.assertEqual(reporting_jobs.get_report_job(running_id)['error'], 'interrupted')
        self.assertEqual(reporting_jobs.get_report_job(completed_id)['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
