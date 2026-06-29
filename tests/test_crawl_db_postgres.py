import json
import os
import sys
import types
import unittest
from unittest import mock

from src import crawl_db


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.row = None
        self.rows = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.conn.sql.append((sql, params))
        sql_clean = ' '.join(sql.split()).lower()
        params = params or ()

        if sql_clean.startswith('insert into crawls'):
            self.conn.next_id += 1
            row = {
                'id': self.conn.next_id,
                'user_id': params[0],
                'session_id': params[1],
                'base_url': params[2],
                'base_domain': params[3],
                'config_snapshot': params[4],
                'status': 'running',
                'urls_discovered': 0,
                'urls_crawled': 0,
                'max_depth_reached': 0,
                'resume_checkpoint': None,
            }
            self.conn.crawls[row['id']] = row
            self.row = {'id': row['id']}
            return self

        if sql_clean.startswith('update crawls set status'):
            status, crawl_id = params
            self.conn.crawls[crawl_id]['status'] = status
            return self

        if sql_clean.startswith('update crawls set resume_checkpoint'):
            checkpoint, crawl_id = params
            self.conn.crawls[crawl_id]['resume_checkpoint'] = checkpoint
            return self

        if sql_clean.startswith('update crawls set'):
            crawl_id = params[-1]
            row = self.conn.crawls[crawl_id]
            for assignment, value in zip(sql_clean.split(' set ')[1].split(' where ')[0].split(','), params):
                column = assignment.strip().split(' ')[0]
                if column != 'last_saved_at':
                    row[column] = value
            return self

        if sql_clean.startswith('select * from crawls where id'):
            self.row = self.conn.crawls.get(params[0])
            return self

        if sql_clean.startswith('select count(*) as count from crawls'):
            self.row = {'count': sum(1 for row in self.conn.crawls.values() if row['user_id'] == params[0])}
            return self

        if sql_clean.startswith('select status, count(*) as count from crawls'):
            counts = {}
            for row in self.conn.crawls.values():
                if row['user_id'] == params[0]:
                    counts[row['status']] = counts.get(row['status'], 0) + 1
            self.rows = [{'status': status, 'count': count} for status, count in counts.items()]
            return self

        if 'from crawls' in sql_clean and 'where user_id' in sql_clean:
            rows = [row for row in self.conn.crawls.values() if row['user_id'] == params[0]]
            self.rows = sorted(rows, key=lambda row: row['id'], reverse=True)
            return self

        if sql_clean.startswith('select * from crawls where status'):
            self.rows = [row for row in self.conn.crawls.values() if row['status'] == 'running']
            return self

        if sql_clean.startswith('delete from crawls'):
            self.rowcount = 1 if self.conn.crawls.pop(params[0], None) else 0
            return self

        return self

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.sql = []
        self.crawls = {}
        self.next_id = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class CrawlDbPostgresTest(unittest.TestCase):
    def setUp(self):
        self.old_url = os.environ.get('METADATA_DATABASE_URL')
        os.environ['METADATA_DATABASE_URL'] = 'postgresql://example'
        self.conn = FakeConnection()
        self.old_psycopg = sys.modules.get('psycopg')
        sys.modules['psycopg'] = types.SimpleNamespace(connect=lambda *_args, **_kwargs: self.conn)
        sys.modules['psycopg.rows'] = types.SimpleNamespace(dict_row=object())

    def tearDown(self):
        if self.old_url is None:
            os.environ.pop('METADATA_DATABASE_URL', None)
        else:
            os.environ['METADATA_DATABASE_URL'] = self.old_url
        if self.old_psycopg is None:
            sys.modules.pop('psycopg', None)
        else:
            sys.modules['psycopg'] = self.old_psycopg
        sys.modules.pop('psycopg.rows', None)

    def test_postgres_crawl_metadata_lifecycle(self):
        with mock.patch('src.crawl_db.get_db', side_effect=AssertionError('sqlite should not be used')):
            crawl_db.init_crawl_tables()
            crawl_id = crawl_db.create_crawl(1, 'session', 'https://example.com', 'example.com', {'delay': 0})
            self.assertEqual(crawl_id, 1)

            self.assertTrue(crawl_db.update_crawl_stats(crawl_id, discovered=10, crawled=3, max_depth=2))
            self.assertTrue(crawl_db.save_checkpoint(crawl_id, {'pending_count': 7}))
            self.assertTrue(crawl_db.set_crawl_status(crawl_id, 'paused'))

            crawl = crawl_db.get_crawl_by_id(crawl_id)
            self.assertEqual(crawl['status'], 'paused')
            self.assertEqual(crawl['urls_discovered'], 10)
            self.assertEqual(crawl['config_snapshot'], {'delay': 0})
            self.assertEqual(crawl['resume_checkpoint'], {'pending_count': 7})

            crawls = crawl_db.get_user_crawls(1)
            self.assertEqual(crawls[0]['id'], crawl_id)
            self.assertIsNone(crawls[0]['config_snapshot'])
            self.assertIsNone(crawls[0]['resume_checkpoint'])
            self.assertEqual(crawl_db.get_crawl_count(1), 1)
            self.assertEqual(crawl_db.get_crawl_status_counts(1), {'paused': 1})

        create_sql = ' '.join(self.conn.sql[0][0].split()).lower()
        self.assertIn('create table if not exists crawls', create_sql)
        self.assertIn('bigserial primary key', create_sql)


if __name__ == '__main__':
    unittest.main()
