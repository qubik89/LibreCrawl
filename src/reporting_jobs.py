"""SQLite helpers for AI report generation jobs."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime


DB_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data',
    'users.db',
)

UPDATE_FIELDS = {
    'status',
    'language',
    'tone',
    'model',
    'markdown_path',
    'html_path',
    'pdf_path',
    'error',
    'usage_json',
    'completed_at',
}

VALID_STATUSES = {'queued', 'running', 'completed', 'failed'}
TERMINAL_STATUSES = {'completed', 'failed'}


def set_db_file(db_file):
    """Point this module at another SQLite file, mainly for tests."""
    global DB_FILE
    DB_FILE = db_file


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_report_job_tables():
    """Create the report job table."""
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS report_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crawl_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                language TEXT,
                tone TEXT,
                model TEXT,
                markdown_path TEXT,
                html_path TEXT,
                pdf_path TEXT,
                error TEXT,
                usage_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_report_jobs_crawl
            ON report_jobs(crawl_id)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_report_jobs_status
            ON report_jobs(status)
        ''')


def create_report_job(crawl_id, language, tone, model):
    """Insert a queued report job and return its id."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO report_jobs (crawl_id, language, tone, model)
            VALUES (?, ?, ?, ?)
            ''',
            (crawl_id, language, tone, model),
        )
        return cursor.lastrowid


def get_report_job(report_id):
    """Return one report job or None."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM report_jobs WHERE id = ?', (report_id,))
        return _row_to_job(cursor.fetchone())


def update_report_job(report_id, **fields):
    """Update allowed report job fields."""
    if not fields:
        return False

    values = dict(fields)
    if 'usage' in values:
        values['usage_json'] = _json_dump_or_none(values.pop('usage'))
    if 'status' in values:
        _validate_status(values['status'])
        if values['status'] in TERMINAL_STATUSES and 'completed_at' not in values:
            values['completed_at'] = _current_timestamp()

    unknown = set(values) - UPDATE_FIELDS
    if unknown:
        raise ValueError(f"Unsupported report job fields: {', '.join(sorted(unknown))}")

    assignments = [f'{key} = ?' for key in values]
    assignments.append('updated_at = CURRENT_TIMESTAMP')
    params = list(values.values()) + [report_id]

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE report_jobs SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
        return cursor.rowcount > 0


def _validate_status(status):
    if status not in VALID_STATUSES:
        raise ValueError(f"Unsupported report job status: {status}")


def _current_timestamp():
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')


def list_report_jobs_for_crawl(crawl_id):
    """Return report jobs for one crawl, newest first."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM report_jobs
            WHERE crawl_id = ?
            ORDER BY created_at DESC, id DESC
            ''',
            (crawl_id,),
        )
        return [_row_to_job(row) for row in cursor.fetchall()]


def mark_incomplete_jobs_failed(error_message='Report job interrupted by server restart'):
    """Mark queued/running jobs failed during startup recovery."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            UPDATE report_jobs
            SET status = 'failed',
                error = ?,
                completed_at = COALESCE(completed_at, ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('queued', 'running')
            ''',
            (error_message, _current_timestamp()),
        )
        return cursor.rowcount


def _row_to_job(row):
    if not row:
        return None
    job = dict(row)
    job['usage'] = _json_loads_or_none(job.get('usage_json'))
    return job


def _json_dump_or_none(value):
    if value is None:
        return None
    return json.dumps(value)


def _json_loads_or_none(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
