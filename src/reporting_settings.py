"""SQLite-backed settings and OpenRouter model cache for report generation."""

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager


DB_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data',
    'users.db',
)

REPORTING_SETTING_KEYS = (
    'openrouter_api_key',
    'default_model',
    'manual_model',
    'default_tone',
    'default_language',
    'agency_name',
    'primary_color',
    'footer_text',
    'logo_path',
)

REPORTING_PROFILE_KEYS = (
    'openrouter_api_key',
    'default_model',
    'manual_model',
    'default_report_mode',
    'default_commercial_context',
    'default_language',
    'issuer_name',
    'default_author',
    'default_contact',
    'default_confidentiality',
    'default_cta',
    'primary_color',
    'secondary_color',
    'accent_color',
    'logo_asset_id',
)

DEFAULT_REPORTING_PROFILE = {
    'openrouter_api_key': None,
    'default_model': 'anthropic/claude-opus-4.7',
    'manual_model': None,
    'default_report_mode': 'pack',
    'default_commercial_context': 'prospect',
    'default_language': 'es-ES',
    'issuer_name': None,
    'default_author': None,
    'default_contact': None,
    'default_confidentiality': None,
    'default_cta': None,
    'primary_color': '#1d4ed8',
    'secondary_color': '#334155',
    'accent_color': '#b45309',
    'logo_asset_id': None,
}

OPENROUTER_MODEL_PREFIXES = ('openai/', 'anthropic/', 'deepseek/')
TEXT_MODALITIES = ('text',)
PRODUCT_BRAND_MARKERS = ('librecrawl', 'mitmore')


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


def init_reporting_tables():
    """Create report settings and OpenRouter model cache tables."""
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reporting_settings (
                setting_key TEXT PRIMARY KEY NOT NULL,
                setting_value TEXT,
                updated_at INTEGER NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS openrouter_models (
                id TEXT PRIMARY KEY NOT NULL,
                provider TEXT,
                name TEXT,
                context_length INTEGER,
                pricing_json TEXT,
                supported_parameters_json TEXT,
                fetched_at INTEGER NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reporting_user_settings (
                user_id INTEGER NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, setting_key)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reporting_assets (
                asset_id TEXT PRIMARY KEY NOT NULL,
                user_id INTEGER NOT NULL,
                asset_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                original_name TEXT,
                created_at INTEGER NOT NULL
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_reporting_assets_user ON reporting_assets(user_id)')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_openrouter_models_provider
            ON openrouter_models(provider)
        ''')


def get_reporting_setting(setting_key, default=None):
    """Return one report setting value."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT setting_value FROM reporting_settings WHERE setting_key = ?',
            (setting_key,),
        )
        row = cursor.fetchone()
        return row['setting_value'] if row else default


def save_reporting_setting(setting_key, setting_value):
    """Upsert one report setting value."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO reporting_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = excluded.updated_at
            ''',
            (setting_key, setting_value, int(time.time())),
        )


def get_reporting_settings():
    """Return all known report settings as a dict."""
    settings = {key: None for key in REPORTING_SETTING_KEYS}
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT setting_key, setting_value FROM reporting_settings')
        for row in cursor.fetchall():
            if row['setting_key'] in settings:
                settings[row['setting_key']] = row['setting_value']
    return settings


def get_user_reporting_settings(user_id, migrate_legacy=False):
    """Return one user's report profile without exposing secrets to other users."""
    profile = dict(DEFAULT_REPORTING_PROFILE)
    if not user_id:
        return profile
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT setting_key, setting_value FROM reporting_user_settings WHERE user_id = ?',
            (int(user_id),),
        )
        rows = cursor.fetchall()
        if not rows and migrate_legacy:
            legacy = _legacy_profile()
            if legacy:
                _write_user_profile(cursor, int(user_id), legacy)
                profile.update(legacy)
                return profile
        for row in rows:
            if row['setting_key'] not in profile:
                continue
            profile[row['setting_key']] = row['setting_value']
    return profile


def save_user_reporting_settings(user_id, settings):
    """Persist a validated profile for one user."""
    if not user_id:
        raise ValueError('A user is required for report settings')
    values = {key: settings.get(key) for key in REPORTING_PROFILE_KEYS if key in settings}
    with get_db() as conn:
        _write_user_profile(conn.cursor(), int(user_id), values)


def _write_user_profile(cursor, user_id, values):
    now = int(time.time())
    for key, value in values.items():
        cursor.execute(
            '''
            INSERT INTO reporting_user_settings (user_id, setting_key, setting_value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, setting_key) DO UPDATE SET
                setting_value = excluded.setting_value, updated_at = excluded.updated_at
            ''', (user_id, key, _json_value(value), now),
        )


def _legacy_profile():
    """Map the old global settings without deleting or mutating them."""
    legacy = get_reporting_settings()
    if not any(value not in (None, '') for value in legacy.values()):
        return {}
    mapped = {}
    aliases = {
        'openrouter_api_key': 'openrouter_api_key', 'default_model': 'default_model',
        'manual_model': 'manual_model', 'default_language': 'default_language',
        'default_tone': 'default_report_mode', 'agency_name': 'issuer_name',
        'footer_text': 'default_confidentiality',
    }
    for old, new in aliases.items():
        if legacy.get(old) not in (None, ''):
            if new == 'issuer_name' and _is_product_brand(legacy[old]):
                # Do not carry the platform's historical name into a
                # white-label V2 profile. Legacy rows remain untouched for
                # rollback and old report downloads.
                continue
            mapped[new] = legacy[old]
    if mapped.get('default_report_mode') not in {'executive', 'commercial', 'technical'}:
        mapped.pop('default_report_mode', None)
    if mapped.get('default_language') not in {'es-ES', 'en'}:
        mapped.pop('default_language', None)
    if mapped.get('default_model') and not str(mapped['default_model']).startswith(OPENROUTER_MODEL_PREFIXES):
        mapped.pop('default_model', None)
    return mapped


def _is_product_brand(value):
    text = ''.join(ch for ch in str(value or '').lower() if ch.isalnum())
    return any(marker in text for marker in PRODUCT_BRAND_MARKERS)


def create_report_asset(user_id, file_path, mime_type, original_name, asset_type='logo'):
    """Register an already-sanitised asset and return its stable ID."""
    asset_id = uuid.uuid4().hex
    with get_db() as conn:
        conn.execute(
            '''INSERT INTO reporting_assets
               (asset_id, user_id, asset_type, file_path, mime_type, original_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (asset_id, int(user_id), asset_type, str(file_path), mime_type,
             str(original_name or '')[:200], int(time.time())),
        )
    return asset_id


def get_report_asset(asset_id, user_id=None):
    """Return an asset only when it belongs to the requesting user."""
    if not asset_id:
        return None
    with get_db() as conn:
        query = 'SELECT * FROM reporting_assets WHERE asset_id = ?'
        params = [str(asset_id)]
        if user_id is not None:
            query += ' AND user_id = ?'
            params.append(int(user_id))
        row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def delete_report_asset(asset_id, user_id):
    """Delete an owned asset record and return its path for filesystem cleanup."""
    with get_db() as conn:
        row = conn.execute(
            'SELECT file_path FROM reporting_assets WHERE asset_id = ? AND user_id = ?',
            (str(asset_id), int(user_id)),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            'DELETE FROM reporting_assets WHERE asset_id = ? AND user_id = ?',
            (str(asset_id), int(user_id)),
        )
    return row['file_path']


def list_report_assets(user_id, asset_type='logo'):
    with get_db() as conn:
        rows = conn.execute(
            '''SELECT asset_id, asset_type, mime_type, original_name, created_at
               FROM reporting_assets WHERE user_id = ? AND asset_type = ? ORDER BY created_at DESC''',
            (int(user_id), asset_type),
        ).fetchall()
    return [dict(row) for row in rows]


def save_reporting_settings(settings_dict):
    """Upsert multiple report settings."""
    with get_db() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        for setting_key, setting_value in settings_dict.items():
            cursor.execute(
                '''
                INSERT INTO reporting_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                ''',
                (setting_key, setting_value, now),
            )


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _json_loads_or_default(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def filter_openrouter_models(models):
    """Keep text-compatible models from the selected provider families."""
    return [
        model for model in models
        if str(model.get('id', '')).startswith(OPENROUTER_MODEL_PREFIXES)
        and _model_supports_text(model)
    ]


def _model_supports_text(model):
    architecture = model.get('architecture') or {}
    input_modalities = (
        model.get('input_modalities')
        or architecture.get('input_modalities')
        or architecture.get('modality')
        or model.get('modality')
    )
    output_modalities = model.get('output_modalities') or architecture.get('output_modalities')

    if not input_modalities and not output_modalities:
        return True

    input_text = _has_text_modality(input_modalities)
    output_text = True if not output_modalities else _has_text_modality(output_modalities)
    return input_text and output_text


def _has_text_modality(value):
    if isinstance(value, str):
        parts = [part.strip().lower() for part in value.replace('+', ',').split(',')]
        return any(part in TEXT_MODALITIES for part in parts)
    try:
        return any(str(item).lower() in TEXT_MODALITIES for item in value)
    except TypeError:
        return False


def save_openrouter_models(models):
    """Store a batch of OpenRouter model metadata rows."""
    filtered_models = filter_openrouter_models(models)
    with get_db() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        for model in filtered_models:
            cursor.execute(
                '''
                INSERT INTO openrouter_models (
                    id, provider, name, context_length,
                    pricing_json, supported_parameters_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    provider = excluded.provider,
                    name = excluded.name,
                    context_length = excluded.context_length,
                    pricing_json = excluded.pricing_json,
                    supported_parameters_json = excluded.supported_parameters_json,
                    fetched_at = excluded.fetched_at
                ''',
                (
                    model.get('id'),
                    model.get('provider'),
                    model.get('name'),
                    model.get('context_length'),
                    _json_value(model.get('pricing_json', model.get('pricing'))),
                    _json_value(
                        model.get(
                            'supported_parameters_json',
                            model.get('supported_parameters'),
                        )
                    ),
                    now,
                ),
            )


def _row_to_model(row):
    if not row:
        return None
    return {
        'id': row['id'],
        'provider': row['provider'],
        'name': row['name'],
        'context_length': row['context_length'],
        'pricing': _json_loads_or_default(row['pricing_json'], None),
        'supported_parameters': _json_loads_or_default(
            row['supported_parameters_json'],
            [],
        ),
        'fetched_at': row['fetched_at'],
    }


def list_openrouter_models():
    """Return cached OpenRouter models ordered by provider then name."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT id, provider, name, context_length, pricing_json,
                   supported_parameters_json, fetched_at
            FROM openrouter_models
            ORDER BY provider, name, id
            '''
        )
        return [_row_to_model(row) for row in cursor.fetchall()]


def get_openrouter_model(model_id):
    """Return one cached OpenRouter model or None."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT id, provider, name, context_length, pricing_json,
                   supported_parameters_json, fetched_at
            FROM openrouter_models
            WHERE id = ?
            ''',
            (model_id,),
        )
        return _row_to_model(cursor.fetchone())


def safe_generation_params(model_metadata, desired_params):
    """
    Keep only the generation params a model says it supports.

    If metadata is missing, preserve only max_tokens so callers can still
    make a minimum viable request.
    """
    desired_params = desired_params or {}
    if not model_metadata:
        return {'max_tokens': desired_params['max_tokens']} if 'max_tokens' in desired_params else {}

    supported = model_metadata.get('supported_parameters') or model_metadata.get('supported_parameters_json') or []
    if isinstance(supported, str):
        try:
            supported = json.loads(supported)
        except json.JSONDecodeError:
            supported = []
    if isinstance(supported, dict):
        supported = supported.keys()
    supported = set(supported)

    safe_params = {}
    if 'max_tokens' in desired_params and 'max_tokens' in supported:
        safe_params['max_tokens'] = desired_params['max_tokens']
    for key, value in desired_params.items():
        if key == 'response_format' and {'response_format', 'structured_outputs'} & supported:
            safe_params[key] = value
        elif key not in {'max_tokens', 'provider'} and key in supported:
            safe_params[key] = value
    if 'response_format' in safe_params and desired_params.get('provider'):
        # Provider routing is an OpenRouter gateway option rather than a
        # model capability, so it is intentionally absent from model metadata.
        safe_params['provider'] = desired_params['provider']
    return safe_params
