import os
import sys
import types
import unittest
from unittest import mock

sys.modules.setdefault(
    'bcrypt',
    types.SimpleNamespace(
        gensalt=lambda: b'salt',
        hashpw=lambda password, _salt: b'hashed:' + password,
        checkpw=lambda password, password_hash: password_hash == b'hashed:' + password,
    ),
)

from src import auth_db


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.row = None
        self.rows = []

    def execute(self, sql, params=None):
        self.conn.sql.append((sql, params))
        sql_clean = ' '.join(sql.split()).lower()
        params = params or ()

        if sql_clean.startswith('insert into users'):
            self.conn.next_user_id += 1
            row = {
                'id': self.conn.next_user_id,
                'username': params[0],
                'email': params[1],
                'password_hash': params[2],
                'verified': 0,
                'tier': 'guest',
                'created_at': None,
                'last_login': None,
            }
            self.conn.users[row['id']] = row
            self.row = {'id': row['id']}
            return self

        if sql_clean.startswith('select id, verified from users where email'):
            self.row = next((u for u in self.conn.users.values() if u['email'] == params[0]), None)
            return self

        if 'from users' in sql_clean and 'where username' in sql_clean:
            self.row = next((u for u in self.conn.users.values() if u['username'] == params[0]), None)
            return self

        if 'from users' in sql_clean and 'where id' in sql_clean and sql_clean.startswith('select'):
            self.row = self.conn.users.get(params[0])
            return self

        if 'from users' in sql_clean and 'where email' in sql_clean and sql_clean.startswith('select'):
            self.row = next((u for u in self.conn.users.values() if u['email'] == params[0]), None)
            return self

        if sql_clean.startswith('select id, username, email, verified'):
            self.rows = list(self.conn.users.values())
            return self

        if sql_clean.startswith('update users set verified'):
            self.conn.users[params[0]]['verified'] = 1
            return self

        if sql_clean.startswith('update users set last_login'):
            self.conn.users[params[0]]['last_login'] = 'now'
            return self

        if sql_clean.startswith('update users set tier'):
            tier, user_id = params
            self.conn.users[user_id]['tier'] = tier
            return self

        if sql_clean.startswith('insert into user_settings'):
            user_id, settings_json = params[:2]
            self.conn.settings[user_id] = settings_json
            return self

        if sql_clean.startswith('select settings_json'):
            settings_json = self.conn.settings.get(params[0])
            self.row = {'settings_json': settings_json} if settings_json else None
            return self

        if sql_clean.startswith('delete from user_settings'):
            self.conn.settings.pop(params[0], None)
            return self

        return self

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.sql = []
        self.users = {}
        self.settings = {}
        self.next_user_id = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class AuthDbPostgresTest(unittest.TestCase):
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

    def test_postgres_auth_and_settings_lifecycle(self):
        with mock.patch('src.auth_db.get_db', side_effect=AssertionError('sqlite should not be used')):
            auth_db.init_db()
            success, message, user_id = auth_db.create_user('romeo', 'romeo@example.com', 'secret123')
            self.assertTrue(success)
            self.assertEqual(user_id, 1)

            self.assertEqual(auth_db.authenticate_user('romeo', 'secret123')[1], 'Account not verified yet. Please wait for admin approval.')
            self.assertEqual(auth_db.verify_user(user_id), (True, 'User verified successfully'))

            success, message, user = auth_db.authenticate_user('romeo', 'secret123')
            self.assertTrue(success)
            self.assertEqual(message, 'Login successful')
            self.assertEqual(user['tier'], 'guest')

            self.assertEqual(auth_db.set_user_tier(user_id, 'admin'), (True, 'User tier updated to admin'))
            self.assertEqual(auth_db.get_user_tier(user_id), 'admin')

            self.assertEqual(auth_db.save_user_settings(user_id, {'concurrency': 20}), (True, 'Settings saved successfully'))
            self.assertEqual(auth_db.get_user_settings(user_id), {'concurrency': 20})
            self.assertTrue(auth_db.delete_user_settings(user_id))
            self.assertIsNone(auth_db.get_user_settings(user_id))

        create_sql = ' '.join(self.conn.sql[0][0].split()).lower()
        self.assertIn('create table if not exists users', create_sql)
        self.assertIn('bigserial primary key', create_sql)


if __name__ == '__main__':
    unittest.main()
