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
    'stage',
    'quality_score',
    'quality_passed',
    'repair_attempted',
    'cost_usd',
    'language',
    'tone',
    'model',
    'markdown_path',
    'html_path',
    'pdf_path',
    'error',
    'usage_json',
    'completed_at',
    'report_type',
    'commercial_context',
    'client_context_json',
    'branding_json',
    'enrichments_json',
    'facts_path',
    'analysis_path',
    'document_path',
    'quality_path',
    'manifest_path',
    'template_version',
    'pack_id',
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
                stage TEXT NOT NULL DEFAULT 'queued',
                quality_score INTEGER,
                quality_passed INTEGER,
                repair_attempted INTEGER,
                cost_usd REAL,
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
        _add_report_job_columns(cursor)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS report_packs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crawl_id INTEGER NOT NULL,
                language TEXT,
                model TEXT,
                options_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_report_jobs_pack
            ON report_jobs(pack_id)
        ''')


def create_report_job(crawl_id, language, tone, model, commercial_context='prospect', client_context=None,
                      pack_id=None, template_version='2.0', branding=None, enrichments=None):
    """Insert a queued report job and return its id."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO report_jobs (
                crawl_id, language, tone, report_type, model, commercial_context,
                client_context_json, branding_json, enrichments_json, pack_id, template_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                crawl_id, language, tone, tone, model, commercial_context,
                _json_dump_or_none(client_context or {}), _json_dump_or_none(branding or {}),
                _json_dump_or_none(enrichments or {}), pack_id, template_version,
            ),
        )
        return cursor.lastrowid


def create_report_pack(crawl_id, language, model, options=None):
    """Create a parent record for an all-audience report generation request."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO report_packs (crawl_id, language, model, options_json)
            VALUES (?, ?, ?, ?)
            ''',
            (crawl_id, language, model, _json_dump_or_none(options or {})),
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
    if 'branding' in values:
        values['branding_json'] = _json_dump_or_none(values.pop('branding'))
    if 'enrichments' in values:
        values['enrichments_json'] = _json_dump_or_none(values.pop('enrichments'))
    if 'status' in values:
        _validate_status(values['status'])
        if values['status'] in TERMINAL_STATUSES and 'completed_at' not in values:
            values['completed_at'] = _current_timestamp()
        if values['status'] in TERMINAL_STATUSES and 'stage' not in values:
            values['stage'] = 'complete' if values['status'] == 'completed' else 'failed'

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
                stage = 'failed',
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
    job['client_context'] = _json_loads_or_none(job.get('client_context_json')) or {}
    job['branding'] = _json_loads_or_none(job.get('branding_json')) or {}
    job['enrichments'] = _json_loads_or_none(job.get('enrichments_json')) or {}
    if job.get('stage') in (None, 'queued') and job.get('status') in TERMINAL_STATUSES:
        job['stage'] = 'complete' if job.get('status') == 'completed' else 'failed'
    job['report_type'] = job.get('report_type') or job.get('tone') or 'executive'
    return job


def _add_report_job_columns(cursor):
    existing = {row[1] for row in cursor.execute('PRAGMA table_info(report_jobs)').fetchall()}
    columns = {
        'report_type': 'TEXT',
        'stage': "TEXT DEFAULT 'queued'",
        'quality_score': 'INTEGER',
        'quality_passed': 'INTEGER',
        'repair_attempted': 'INTEGER',
        'cost_usd': 'REAL',
        'commercial_context': 'TEXT',
        'client_context_json': 'TEXT',
        'branding_json': 'TEXT',
        'enrichments_json': 'TEXT',
        'facts_path': 'TEXT',
        'analysis_path': 'TEXT',
        'document_path': 'TEXT',
        'quality_path': 'TEXT',
        'manifest_path': 'TEXT',
        'template_version': 'TEXT',
        'pack_id': 'INTEGER',
    }
    for name, definition in columns.items():
        if name not in existing:
            cursor.execute(f'ALTER TABLE report_jobs ADD COLUMN {name} {definition}')
    # Existing V1 rows predate public stages. Reflect their terminal status
    # rather than presenting a historical completed report as still queued.
    cursor.execute("UPDATE report_jobs SET stage = 'complete' WHERE status = 'completed' AND (stage IS NULL OR stage = 'queued')")
    cursor.execute("UPDATE report_jobs SET stage = 'failed' WHERE status = 'failed' AND (stage IS NULL OR stage = 'queued')")


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
