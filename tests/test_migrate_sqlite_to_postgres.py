import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def load_migrator():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'migrate_sqlite_to_postgres.py'
    spec = importlib.util.spec_from_file_location('migrate_sqlite_to_postgres', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.sql.append((sql, params))
        return self


class FakeConnection:
    def __init__(self):
        self.sql = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class SqliteToPostgresMigrationTest(unittest.TestCase):
    def test_migrates_users_settings_and_crawls(self):
        migrator = load_migrator()
        pg = FakeConnection()

        with tempfile.NamedTemporaryFile(suffix='.db') as db_file:
            sqlite_conn = sqlite3.connect(db_file.name)
            sqlite_conn.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username TEXT,
                    email TEXT,
                    password_hash TEXT,
                    verified INTEGER,
                    tier TEXT,
                    created_at TEXT,
                    last_login TEXT
                );
                CREATE TABLE user_settings (
                    user_id INTEGER PRIMARY KEY,
                    settings_json TEXT,
                    updated_at TEXT
                );
                CREATE TABLE crawls (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    session_id TEXT,
                    base_url TEXT,
                    base_domain TEXT,
                    status TEXT,
                    config_snapshot TEXT,
                    urls_discovered INTEGER,
                    urls_crawled INTEGER,
                    max_depth_reached INTEGER,
                    started_at TEXT,
                    completed_at TEXT,
                    last_saved_at TEXT,
                    peak_memory_mb REAL,
                    estimated_size_mb REAL,
                    can_resume INTEGER,
                    resume_checkpoint TEXT
                );
            ''')
            sqlite_conn.execute(
                'INSERT INTO users VALUES (1, "romeo", "romeo@example.com", "hash", 1, "admin", "2026-01-01", NULL)'
            )
            sqlite_conn.execute(
                'INSERT INTO user_settings VALUES (1, "{""concurrency"":20}", "2026-01-02")'
            )
            sqlite_conn.execute(
                'INSERT INTO user_settings VALUES (2, "{""orphan"":true}", "2026-01-02")'
            )
            sqlite_conn.execute(
                'INSERT INTO crawls VALUES (6, 1, "s", "https://example.com", "example.com", "running", "{}", 10, 3, 1, "2026-01-01", NULL, "2026-01-02", 1.5, 2.5, 1, NULL)'
            )
            sqlite_conn.commit()
            sqlite_conn.close()

            with mock.patch.object(migrator, 'pg_connect', return_value=pg):
                counts = migrator.migrate(db_file.name, 'postgresql://example')

        self.assertEqual(counts, {'users': 1, 'user_settings': 1, 'crawls': 1, 'skipped_user_settings': 1})
        sql = '\n'.join(statement for statement, _params in pg.sql)
        self.assertIn('INSERT INTO users', sql)
        self.assertIn('INSERT INTO user_settings', sql)
        self.assertIn('INSERT INTO crawls', sql)


if __name__ == '__main__':
    unittest.main()
