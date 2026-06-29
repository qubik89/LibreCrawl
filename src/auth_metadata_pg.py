"""PostgreSQL-backed user auth and settings store."""
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta


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
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                verified INTEGER DEFAULT 0,
                tier TEXT DEFAULT 'guest',
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMPTZ
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                settings_json TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS verification_tokens (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token TEXT UNIQUE NOT NULL,
                app_source TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMPTZ NOT NULL,
                used INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_token ON verification_tokens(token)')
        print("PostgreSQL auth tables initialized successfully")


def create_user(username, email, password_hash):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, verified FROM users WHERE email = %s', (email,))
        existing = cursor.fetchone()
        if existing:
            if existing['verified'] == 1:
                return False, "Email already registered and verified", None
            cursor.execute('''
                UPDATE users
                SET username = %s, password_hash = %s
                WHERE id = %s
            ''', (username, password_hash, existing['id']))
            return True, "resend", existing['id']

        cursor.execute('''
            INSERT INTO users (username, email, password_hash, verified)
            VALUES (%s, %s, %s, 0)
            RETURNING id
        ''', (username, email, password_hash))
        return True, "Registration successful! Please wait for admin verification.", cursor.fetchone()['id']


def authenticate_user(username, verify_password):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, username, email, password_hash, verified, tier
            FROM users
            WHERE username = %s
        ''', (username,))
        user = cursor.fetchone()
        if not user:
            return False, "Invalid username or password", None
        if not verify_password(user['password_hash']):
            return False, "Invalid username or password", None
        if user['verified'] != 1:
            return False, "Account not verified yet. Please wait for admin approval.", None
        cursor.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = %s', (user['id'],))
        return True, "Login successful", {
            'id': user['id'],
            'username': user['username'],
            'email': user['email'],
            'tier': user['tier'] or 'guest',
        }


def get_user_by_id(user_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, username, email, verified, created_at, last_login FROM users WHERE id = %s', (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_users():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, username, email, verified, created_at, last_login FROM users ORDER BY created_at DESC')
        return [dict(row) for row in cursor.fetchall()]


def verify_user(user_id):
    with get_db() as conn:
        conn.cursor().execute('UPDATE users SET verified = 1 WHERE id = %s', (user_id,))
        return True, "User verified successfully"


def save_user_settings(user_id, settings_dict):
    with get_db() as conn:
        conn.cursor().execute('''
            INSERT INTO user_settings (user_id, settings_json, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                settings_json = excluded.settings_json,
                updated_at = CURRENT_TIMESTAMP
        ''', (user_id, json.dumps(settings_dict)))
        return True, "Settings saved successfully"


def get_user_settings(user_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT settings_json FROM user_settings WHERE user_id = %s', (user_id,))
        row = cursor.fetchone()
        return json.loads(row['settings_json']) if row else None


def delete_user_settings(user_id):
    with get_db() as conn:
        conn.cursor().execute('DELETE FROM user_settings WHERE user_id = %s', (user_id,))
        return True


def set_user_tier(user_id, tier):
    with get_db() as conn:
        conn.cursor().execute('UPDATE users SET tier = %s WHERE id = %s', (tier, user_id))
        return True, f"User tier updated to {tier}"


def get_user_tier(user_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT tier FROM users WHERE id = %s', (user_id,))
        row = cursor.fetchone()
        return row['tier'] if row else 'guest'


def get_user_by_email(email):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, username, email, verified, tier FROM users WHERE email = %s', (email,))
        row = cursor.fetchone()
        return dict(row) if row else None


def create_verification_token(user_id, token, app_source='main'):
    expires_at = datetime.now() + timedelta(hours=24)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM verification_tokens WHERE user_id = %s AND used = 0', (user_id,))
        cursor.execute('''
            INSERT INTO verification_tokens (user_id, token, app_source, expires_at)
            VALUES (%s, %s, %s, %s)
        ''', (user_id, token, app_source, expires_at))
        return token
