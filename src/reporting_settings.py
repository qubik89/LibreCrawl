"""SQLite-backed settings and OpenRouter model cache for report generation."""

import json
import os
import sqlite3
import time
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

OPENROUTER_MODEL_PREFIXES = ('openai/', 'anthropic/', 'deepseek/')
TEXT_MODALITIES = ('text',)


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
        if key != 'max_tokens' and key in supported:
            safe_params[key] = value
    return safe_params
