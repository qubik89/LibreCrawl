#!/usr/bin/env python3
"""Copy LibreCrawl SQLite metadata into PostgreSQL."""
import argparse
import os
import sqlite3
from contextlib import contextmanager


def pg_connect(database_url):
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(database_url, row_factory=dict_row)


@contextmanager
def sqlite_rows(sqlite_path):
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _fetch_all(conn, table):
    try:
        return [dict(row) for row in conn.execute(f'SELECT * FROM {table}').fetchall()]
    except sqlite3.OperationalError:
        return []


def migrate(sqlite_path, database_url):
    counts = {'users': 0, 'user_settings': 0, 'crawls': 0, 'skipped_user_settings': 0}
    pg = pg_connect(database_url)
    try:
        cursor = pg.cursor()
        with sqlite_rows(sqlite_path) as sqlite_conn:
            user_ids = set()
            for row in _fetch_all(sqlite_conn, 'users'):
                user_ids.add(row.get('id'))
                cursor.execute('''
                    INSERT INTO users (
                        id, username, email, password_hash, verified, tier, created_at, last_login
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(id) DO UPDATE SET
                        username = excluded.username,
                        email = excluded.email,
                        password_hash = excluded.password_hash,
                        verified = excluded.verified,
                        tier = excluded.tier,
                        created_at = excluded.created_at,
                        last_login = excluded.last_login
                ''', (
                    row.get('id'), row.get('username'), row.get('email'), row.get('password_hash'),
                    row.get('verified'), row.get('tier'), row.get('created_at'), row.get('last_login'),
                ))
                counts['users'] += 1

            for row in _fetch_all(sqlite_conn, 'user_settings'):
                if row.get('user_id') not in user_ids:
                    counts['skipped_user_settings'] += 1
                    continue
                cursor.execute('''
                    INSERT INTO user_settings (user_id, settings_json, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(user_id) DO UPDATE SET
                        settings_json = excluded.settings_json,
                        updated_at = excluded.updated_at
                ''', (row.get('user_id'), row.get('settings_json'), row.get('updated_at')))
                counts['user_settings'] += 1

            for row in _fetch_all(sqlite_conn, 'crawls'):
                cursor.execute('''
                    INSERT INTO crawls (
                        id, user_id, session_id, base_url, base_domain, status, config_snapshot,
                        urls_discovered, urls_crawled, max_depth_reached, started_at, completed_at,
                        last_saved_at, peak_memory_mb, estimated_size_mb, can_resume, resume_checkpoint
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(id) DO UPDATE SET
                        user_id = excluded.user_id,
                        session_id = excluded.session_id,
                        base_url = excluded.base_url,
                        base_domain = excluded.base_domain,
                        status = excluded.status,
                        config_snapshot = excluded.config_snapshot,
                        urls_discovered = excluded.urls_discovered,
                        urls_crawled = excluded.urls_crawled,
                        max_depth_reached = excluded.max_depth_reached,
                        started_at = excluded.started_at,
                        completed_at = excluded.completed_at,
                        last_saved_at = excluded.last_saved_at,
                        peak_memory_mb = excluded.peak_memory_mb,
                        estimated_size_mb = excluded.estimated_size_mb,
                        can_resume = excluded.can_resume,
                        resume_checkpoint = excluded.resume_checkpoint
                ''', (
                    row.get('id'), row.get('user_id'), row.get('session_id'), row.get('base_url'),
                    row.get('base_domain'), row.get('status'), row.get('config_snapshot'),
                    row.get('urls_discovered'), row.get('urls_crawled'), row.get('max_depth_reached'),
                    row.get('started_at'), row.get('completed_at'), row.get('last_saved_at'),
                    row.get('peak_memory_mb'), row.get('estimated_size_mb'), bool(row.get('can_resume')),
                    row.get('resume_checkpoint'),
                ))
                counts['crawls'] += 1

        cursor.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), COALESCE((SELECT MAX(id) FROM users), 1), true)")
        cursor.execute("SELECT setval(pg_get_serial_sequence('crawls', 'id'), COALESCE((SELECT MAX(id) FROM crawls), 1), true)")
        pg.commit()
        return counts
    except Exception:
        pg.rollback()
        raise
    finally:
        pg.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sqlite', default=os.path.join('data', 'users.db'))
    parser.add_argument('--postgres', default=os.getenv('METADATA_DATABASE_URL', ''))
    args = parser.parse_args()
    if not args.postgres:
        raise SystemExit('METADATA_DATABASE_URL or --postgres is required')
    print(migrate(args.sqlite, args.postgres))


if __name__ == '__main__':
    main()
