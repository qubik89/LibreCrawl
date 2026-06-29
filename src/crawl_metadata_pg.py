"""PostgreSQL-backed crawl metadata store."""
import json
import os
from contextlib import contextmanager


def database_url():
    return os.getenv('METADATA_DATABASE_URL', '').strip()


def enabled():
    return bool(database_url())


@contextmanager
def get_db():
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(database_url(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_tables():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS crawls (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT,
                session_id TEXT NOT NULL,
                base_url TEXT NOT NULL,
                base_domain TEXT,
                status TEXT DEFAULT 'running',
                config_snapshot TEXT,
                urls_discovered BIGINT DEFAULT 0,
                urls_crawled BIGINT DEFAULT 0,
                max_depth_reached BIGINT DEFAULT 0,
                started_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMPTZ,
                last_saved_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                peak_memory_mb DOUBLE PRECISION,
                estimated_size_mb DOUBLE PRECISION,
                can_resume BOOLEAN DEFAULT TRUE,
                resume_checkpoint TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawls_user_status ON crawls(user_id, status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawls_session ON crawls(session_id)')
        print("PostgreSQL crawl metadata tables initialized successfully")


def create_crawl(user_id, session_id, base_url, base_domain, config_snapshot):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO crawls (user_id, session_id, base_url, base_domain, config_snapshot, status)
            VALUES (%s, %s, %s, %s, %s, 'running')
            RETURNING id
        ''', (user_id, session_id, base_url, base_domain, json.dumps(config_snapshot)))
        row = cursor.fetchone()
        crawl_id = row['id']
        print(f"Created new crawl record: ID={crawl_id}, URL={base_url}")
        return crawl_id


def update_stats(crawl_id, discovered=None, crawled=None, max_depth=None, peak_memory_mb=None, estimated_size_mb=None):
    with get_db() as conn:
        cursor = conn.cursor()
        updates = []
        params = []
        if discovered is not None:
            updates.append("urls_discovered = %s")
            params.append(discovered)
        if crawled is not None:
            updates.append("urls_crawled = %s")
            params.append(crawled)
        if max_depth is not None:
            updates.append("max_depth_reached = %s")
            params.append(max_depth)
        if peak_memory_mb is not None:
            updates.append("peak_memory_mb = %s")
            params.append(peak_memory_mb)
        if estimated_size_mb is not None:
            updates.append("estimated_size_mb = %s")
            params.append(estimated_size_mb)
        updates.append("last_saved_at = CURRENT_TIMESTAMP")
        params.append(crawl_id)
        cursor.execute(f"UPDATE crawls SET {', '.join(updates)} WHERE id = %s", params)
        return True


def save_checkpoint(crawl_id, checkpoint_data):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE crawls
            SET resume_checkpoint = %s, last_saved_at = CURRENT_TIMESTAMP
            WHERE id = %s
        ''', (json.dumps(checkpoint_data), crawl_id))
        return True


def set_status(crawl_id, status):
    with get_db() as conn:
        cursor = conn.cursor()
        if status in ['completed', 'failed', 'stopped', 'demo_stopped']:
            cursor.execute('''
                UPDATE crawls
                SET status = %s, completed_at = CURRENT_TIMESTAMP
                WHERE id = %s
            ''', (status, crawl_id))
        else:
            cursor.execute('UPDATE crawls SET status = %s WHERE id = %s', (status, crawl_id))
        print(f"Updated crawl {crawl_id} status to: {status}")
        return True


def _decode(row):
    if not row:
        return None
    crawl = dict(row)
    if crawl.get('config_snapshot'):
        crawl['config_snapshot'] = json.loads(crawl['config_snapshot'])
    if crawl.get('resume_checkpoint'):
        crawl['resume_checkpoint'] = json.loads(crawl['resume_checkpoint'])
    return crawl


def get_by_id(crawl_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM crawls WHERE id = %s', (crawl_id,))
        return _decode(cursor.fetchone())


def get_counts(_crawl_id):
    return {'urls': 0, 'links': 0, 'issues': 0}


def get_user_crawls(user_id, limit=50, offset=0, status_filter=None):
    with get_db() as conn:
        cursor = conn.cursor()
        query = '''
            SELECT crawls.*, 0 AS link_count, 0 AS issue_count
            FROM crawls
            WHERE user_id = %s
        '''
        params = [user_id]
        if status_filter:
            query += ' AND status = %s'
            params.append(status_filter)
        query += ' ORDER BY started_at DESC LIMIT %s OFFSET %s'
        params.extend([limit, offset])
        cursor.execute(query, params)
        crawls = []
        for row in cursor.fetchall():
            crawl = dict(row)
            crawl['config_snapshot'] = None
            crawl['resume_checkpoint'] = None
            crawls.append(crawl)
        return crawls


def delete_crawl(crawl_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM crawls WHERE id = %s', (crawl_id,))
        print(f"Deleted crawl {crawl_id} and all associated metadata")
        return True


def get_crashed_crawls():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM crawls WHERE status = 'running' ORDER BY started_at DESC")
        return [dict(row) for row in cursor.fetchall()]


def cleanup_old_crawls(days=90):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM crawls
            WHERE started_at < CURRENT_TIMESTAMP - (%s || ' days')::interval
            AND status IN ('completed', 'failed', 'stopped')
        ''', (days,))
        deleted = cursor.rowcount
        print(f"Cleaned up {deleted} old crawls")
        return deleted


def get_crawl_count(user_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM crawls WHERE user_id = %s', (user_id,))
        result = cursor.fetchone()
        return result['count'] if result else 0


def get_crawl_status_counts(user_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT status, COUNT(*) as count
            FROM crawls
            WHERE user_id = %s
            GROUP BY status
        ''', (user_id,))
        return {row['status']: row['count'] for row in cursor.fetchall()}
