import threading
import time
import csv
import copy
import json
import xml.etree.ElementTree as ET
import uuid
import webbrowser
import argparse
import re
import secrets
import string
import os
import multiprocessing
from io import StringIO
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit
from flask import Flask, Response, render_template, request, jsonify, session, redirect, url_for, send_file, stream_with_context
from flask_compress import Compress
from functools import wraps
from src.crawler import WebCrawler
from src import crawl_jobs
from src.settings_manager import SettingsManager
from src.auth_db import init_db, create_user, authenticate_user, get_user_by_id, log_guest_crawl, get_guest_crawls_last_24h, verify_user, set_user_tier, create_verification_token, verify_token, get_user_by_email
from src.email_service import send_verification_email, send_welcome_email

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Parse command line arguments
parser = argparse.ArgumentParser(description='Mitmore SEO Crawl - SEO Spider Tool')
parser.add_argument('--local', '-l', action='store_true',
                    help='Run in local mode (all users get admin tier, no rate limits)')
parser.add_argument('--disable-register', '-dr', action='store_true',
                    help='Disable new user registrations')
parser.add_argument('--disable-guest', '-dg', action='store_true',
                    help='Disable guest login')
parser.add_argument('--demo', '-dm', action='store_true',
                    help='Demo mode: 1.5GB memory limit per user, crawls auto-stop at limit')
parser.add_argument('--dangerously-skip-auth', '-dsa', action='store_true',
                    help='DANGEROUS: Allow anyone to log in as any username with no password. '
                         'The username is only used to separate per-user sessions. '
                         'Do NOT use on a public network or in production.')
args = parser.parse_args()

LOCAL_MODE = args.local or os.getenv('LOCAL_MODE', '').lower() in ('true', '1', 'yes')
DISABLE_REGISTER = args.disable_register or os.getenv('REGISTRATION_DISABLED', '').lower() in ('true', '1', 'yes')
DISABLE_GUEST = args.disable_guest or os.getenv('DISABLE_GUEST', '').lower() in ('true', '1', 'yes')
DEMO_MODE = args.demo or os.getenv('DEMO_MODE', '').lower() in ('true', '1', 'yes')
SKIP_AUTH = args.dangerously_skip_auth or os.getenv('DANGEROUSLY_SKIP_AUTH', '').lower() in ('true', '1', 'yes')
IS_MAIN_PROCESS = multiprocessing.current_process().name == 'MainProcess'

app = Flask(__name__, template_folder='web/templates', static_folder='web/static')
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
if IS_MAIN_PROCESS and not os.environ.get('SECRET_KEY'):
    print('⚠️  WARNING: SECRET_KEY not set — using an ephemeral random key. '
          'Sessions will not persist across restarts. Set SECRET_KEY in production.', flush=True)

# Enable compression for all responses
Compress(app)

# Initialize database on startup
if IS_MAIN_PROCESS:
    init_db()

def generate_random_password(length=16):
    """Generate a random password with letters, digits, and symbols"""
    alphabet = string.ascii_letters + string.digits + string.punctuation
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def auto_login_local_mode():
    """Auto-login for local mode - creates or logs into 'local' admin account"""
    try:
        from src.auth_db import create_user, get_user_by_username, set_user_tier, verify_user
        user = get_user_by_username('local')
        if user:
            # User exists, just log them in
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['tier'] = 'admin'
            session.permanent = True
            print(f"Auto-logged in as existing 'local' user (ID: {user['id']})")
        else:
            # Create new local user with random password
            random_password = generate_random_password()
            success, _message, user_id = create_user('local', 'local@localhost', random_password)
            if not success:
                return False
            verify_user(user_id)
            set_user_tier(user_id, 'admin')

            # Log in the new user
            session['user_id'] = user_id
            session['username'] = 'local'
            session['tier'] = 'admin'
            session.permanent = True

            print(f"Created and auto-logged in as new 'local' admin user (ID: {user_id})")
            print(f"Generated password: {random_password}")

        return True
    except Exception as e:
        print(f"Error in auto_login_local_mode: {e}")
        return False

def skip_auth_login(username):
    """Skip-auth login: create user record if missing, log them in.

    Each username gets its own user_id, which drives per-user crawler
    instance and settings isolation. No password is checked. Always
    grants admin tier (matches local-mode behavior).

    Returns (success, message).
    """
    try:
        from src.auth_db import create_user, get_user_by_username, set_user_tier, verify_user
        user = get_user_by_username(username)
        if user:
            user_id = user['id']
        else:
            random_password = generate_random_password()
            success, message, user_id = create_user(username, f'{username}@skipauth.local', random_password)
            if not success:
                return False, message
            verify_user(user_id)
            set_user_tier(user_id, 'admin')

        session['user_id'] = user_id
        session['username'] = username
        session['tier'] = 'admin'
        session.permanent = True

        return True, 'Sesión iniciada (autenticación omitida)'
    except Exception as e:
        print(f"Error in skip_auth_login: {e}")
        return False, f'Login error: {str(e)}'

if IS_MAIN_PROCESS and LOCAL_MODE:
    print("=" * 60)
    print("LOCAL MODE ENABLED")
    print("All users will have admin tier access")
    print("No rate limits or tier restrictions")
    print("Auto-login enabled with 'local' admin account")
    print("=" * 60)

if IS_MAIN_PROCESS and DISABLE_REGISTER:
    print("=" * 60)
    print("REGISTRATION DISABLED")
    print("New user registrations are not allowed")
    print("=" * 60)

if IS_MAIN_PROCESS and DISABLE_GUEST:
    print("=" * 60)
    print("GUEST MODE DISABLED")
    print("Guest login is not allowed")
    print("=" * 60)

if IS_MAIN_PROCESS and DEMO_MODE:
    print("=" * 60)
    print("DEMO MODE ENABLED")
    print("Memory limit: 1.5GB per user")
    print("Crawls will auto-stop when limit is reached")
    print("=" * 60)

if IS_MAIN_PROCESS and SKIP_AUTH:
    print("=" * 60)
    print("⚠️  DANGEROUSLY SKIP AUTH ENABLED")
    print("Anyone can log in as any username with no password!")
    print("Username is used only to separate per-user sessions.")
    print("DO NOT use on a public network or production server!")
    print("=" * 60)

def get_client_ip():
    """Get the real client IP address, checking Cloudflare headers first"""
    # Check Cloudflare header first
    if 'CF-Connecting-IP' in request.headers:
        return request.headers['CF-Connecting-IP']
    # Check other common proxy headers
    if 'X-Forwarded-For' in request.headers:
        # X-Forwarded-For can contain multiple IPs, take the first one
        return request.headers['X-Forwarded-For'].split(',')[0].strip()
    if 'X-Real-IP' in request.headers:
        return request.headers['X-Real-IP']
    # Fall back to direct connection IP
    return request.remote_addr

def login_required(f):
    """Decorator to require login for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # In local mode, auto-login if not already logged in
        if LOCAL_MODE and 'user_id' not in session:
            auto_login_local_mode()
        elif session.get('user_id') is not None and get_user_by_id(session.get('user_id')) is None:
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Autenticación requerida'}), 401
            return redirect(url_for('login_page'))
        elif 'user_id' not in session:
            # Not in local mode and not logged in
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Autenticación requerida'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# Multi-tenant crawler instances
crawler_instances = {}  # session_id -> {'crawler': WebCrawler, 'settings': SettingsManager, 'last_accessed': datetime}
instances_lock = threading.Lock()
dashboard_cache = {}
dashboard_cache_lock = threading.Lock()

def ensure_session_id():
    """Ensure the browser session has a stable id for settings and guest ownership."""
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    return session['session_id']

def user_can_access_crawl(crawl, user_id, session_id):
    """Check crawl ownership for account users and guests."""
    if not crawl:
        return False
    if crawl.get('user_id') is None:
        return crawl.get('session_id') == session_id
    return crawl.get('user_id') == user_id

def register_server_crawler(crawler):
    """Put a crawler in the server registry until it finishes."""
    crawler.on_finish = lambda finished: crawl_jobs.unregister(finished.crawl_id)
    return crawl_jobs.register(crawler)

def build_crawl_summary(crawl_id):
    """Return a small crawl summary from DB plus live runner state."""
    from src.crawl_db import get_crawl_by_id, get_crawl_counts

    crawl = get_crawl_by_id(crawl_id)
    if not crawl:
        return None

    active = crawl_jobs.get(crawl_id)
    counts = get_crawl_counts(crawl_id)
    analytics = None
    try:
        from src.crawl_clickhouse import get_summary
        analytics = get_summary(crawl_id)
    except Exception as e:
        print(f"ClickHouse summary unavailable for crawl {crawl_id}: {e}")
        analytics = None

    analytics_counts = (analytics or {}).get('counts') or {}
    url_count = analytics_counts.get('urls') or counts['urls']
    link_count = analytics_counts.get('links') or counts['links']
    issue_count = analytics_counts.get('issues') or counts['issues']

    status = crawl.get('status', 'unknown')
    discovered = crawl.get('urls_discovered') or url_count
    crawled = crawl.get('urls_crawled') or url_count
    depth = crawl.get('max_depth_reached') or 0
    speed = 0.0
    progress = min(100, (crawled / max(discovered, 1)) * 100)
    memory = {}
    memory_data = {}
    is_running_pagespeed = False
    demo_stopped = status == 'demo_stopped'

    if active:
        link_stats = active.link_manager.get_stats() if active.link_manager else {'discovered': discovered}
        discovered = max(discovered, link_stats.get('discovered', 0), active.stats.get('discovered', 0))
        crawled = active.stats.get('crawled', crawled)
        depth = active.stats.get('depth', depth)
        if active.stats.get('start_time'):
            speed = round(crawled / max(time.time() - active.stats['start_time'], 1), 2)
        status = 'paused' if active.is_paused else ('running' if active.is_running else status)
        progress = min(100, (crawled / max(discovered, 1)) * 100)
        memory = active.memory_monitor.get_stats()
        memory_data = active.user_memory.get_stats()
        is_running_pagespeed = active.is_running_pagespeed
        demo_stopped = active._demo_limit_reached

    return {
        'success': True,
        'crawl_id': crawl_id,
        'status': status,
        'is_active': active is not None,
        'is_running_pagespeed': is_running_pagespeed,
        'demo_stopped': demo_stopped,
        'crawl': crawl,
        'stats': {
            'baseUrl': crawl.get('base_url'),
            'discovered': discovered,
            'crawled': crawled,
            'depth': depth,
            'speed': speed,
            'url_count': url_count,
            'link_count': link_count,
            'issue_count': issue_count,
        },
        'progress': progress,
        'memory': memory,
        'memory_data': memory_data,
        'counts': counts,
        'analytics': analytics,
    }

REPORT_SETTING_FIELDS = (
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

REPORT_PROFILE_FIELDS = (
    'openrouter_api_key', 'default_model', 'manual_model', 'default_report_mode',
    'default_commercial_context', 'default_language', 'issuer_name', 'default_author',
    'default_contact', 'default_confidentiality', 'default_cta', 'primary_color',
    'secondary_color', 'accent_color', 'logo_asset_id',
)

REPORT_BRANDING_FIELDS = ('agency_name', 'primary_color', 'footer_text', 'logo_path')
REPORT_LANGUAGES = {'es-ES', 'en'}
REPORT_TONES = {'executive', 'technical', 'commercial'}  # Legacy API alias for report_type.
REPORT_TYPES = REPORT_TONES
REPORT_MODES = {'pack', 'executive', 'technical', 'commercial'}
REPORT_COMMERCIAL_CONTEXTS = {'prospect', 'existing_client'}
REPORT_MODEL_PREFIXES = ('openai/', 'anthropic/', 'deepseek/')
REPORT_MAX_FIELD_LENGTH = 500
REPORT_MAX_LOGO_ASSET_ID_LENGTH = 64
REPORTS_V2_ENABLED = os.getenv('REPORTS_V2_ENABLED', '').lower() in ('1', 'true', 'yes')


def mask_openrouter_api_key(api_key):
    """Return a non-sensitive display value for an OpenRouter key."""
    if not api_key:
        return ''
    api_key = str(api_key)
    if len(api_key) <= 8:
        return '********'
    return f'{api_key[:4]}...{api_key[-4:]}'


def _is_masked_openrouter_key(value):
    return bool(re.match(r'^[A-Za-z0-9_-]{4}\.\.\.[A-Za-z0-9_-]{4}$', str(value or '').strip()))


def _white_label_issuer_name(value):
    """Hide historical platform names from the V2 white-label surface."""
    text = ''.join(char for char in str(value or '').lower() if char.isalnum())
    if 'librecrawl' in text or 'mitmore' in text:
        return ''
    return str(value or '').strip()


def _public_profile_color(value, fallback):
    value = str(value or '').strip()
    return value.lower() if re.match(r'^#[0-9a-fA-F]{6}$', value) else fallback


def public_report_settings(settings, logo=None):
    """Return report settings without exposing the stored API key."""
    source = settings or {}
    # Whitelist the profile contract instead of echoing arbitrary persisted
    # keys.  This keeps future connection references or migration metadata out
    # of the public settings response by construction.
    public = {
        key: source.get(key)
        for key in REPORT_PROFILE_FIELDS + ('default_tone', 'agency_name', 'footer_text')
        if key in source
    }
    api_key = source.get('openrouter_api_key')
    mode = public.get('default_report_mode') or public.get('default_tone') or 'pack'
    if mode not in REPORT_MODES:
        mode = 'pack'
    issuer = {
        'name': _white_label_issuer_name(public.get('issuer_name') or public.get('agency_name')),
        'author': _white_label_issuer_name(public.get('default_author')),
        'contact': _white_label_issuer_name(public.get('default_contact')),
        'confidentiality': _white_label_issuer_name(public.get('default_confidentiality') or public.get('footer_text')),
        'cta': _white_label_issuer_name(public.get('default_cta')),
    }
    appearance = {
        'primary_color': _public_profile_color(public.get('primary_color'), '#1d4ed8'),
        'secondary_color': _public_profile_color(public.get('secondary_color'), '#334155'),
        'accent_color': _public_profile_color(public.get('accent_color'), '#b45309'),
        # Only advertise an asset that still resolves through the authenticated
        # ownership check performed by the route.
        'logo_asset_id': (logo or {}).get('asset_id') or '',
    }
    public['openrouter_api_key'] = ''
    public['has_openrouter_api_key'] = bool(api_key)
    public['masked_openrouter_api_key'] = mask_openrouter_api_key(api_key)
    public['default_model'] = public.get('default_model') or 'anthropic/claude-opus-4.7'
    public['default_language'] = public.get('default_language') or 'es-ES'
    public['default_report_mode'] = mode
    public['default_commercial_context'] = public.get('default_commercial_context') or 'prospect'
    public['issuer'] = issuer
    public['appearance'] = appearance
    public['logo'] = logo
    public['capabilities'] = {
        'reports_v2_enabled': REPORTS_V2_ENABLED,
        'quality_threshold': 90,
        'report_types': ['executive', 'commercial', 'technical'],
        'outputs': ['html', 'pdf'],
        'external_sources': ['gsc', 'ga4', 'crux', 'pagespeed'],
    }
    # Deprecated response aliases for older clients.
    public['default_tone'] = mode if mode in REPORT_TYPES else 'executive'
    public['agency_name'] = issuer['name']
    public['footer_text'] = issuer['confidentiality']
    public['logo_path'] = ''
    return public


def report_settings_update_from_payload(payload):
    """Build a save dict while preserving a blank/missing API key."""
    payload = payload or {}
    update = {}
    for field in REPORT_SETTING_FIELDS:
        if field not in payload:
            continue
        value = payload.get(field)
        if field == 'openrouter_api_key' and not str(value or '').strip():
            continue
        update[field] = normalize_report_setting(field, value)
    return update


def report_profile_update_from_payload(payload):
    """Validate a V2 profile while accepting old flat aliases."""
    payload = payload or {}
    issuer = payload.get('issuer') if isinstance(payload.get('issuer'), dict) else {}
    appearance = payload.get('appearance') if isinstance(payload.get('appearance'), dict) else {}
    update = {}
    for field in ('openrouter_api_key', 'default_model', 'manual_model', 'default_report_mode',
                  'default_commercial_context', 'default_language'):
        value = payload.get(field)
        if value is None and field == 'default_report_mode':
            value = _first_nonblank(payload.get('default_tone'), payload.get('tone'))
        if value is None:
            continue
        if field == 'openrouter_api_key' and _is_masked_openrouter_key(value):
            # Browser clients may submit the displayed mask unchanged. Keep
            # the server-side secret instead of replacing it with the mask.
            continue
        if field == 'openrouter_api_key' and not str(value).strip():
            continue
        if field == 'default_model' and not str(value).strip():
            # A fresh profile may not have a cached model option yet. Do not
            # replace the contractual Opus default with an empty value just
            # because the settings form was saved before the cache refresh.
            continue
        update[field] = normalize_report_profile_setting(field, value)

    aliases = {
        # The UI keeps identity fields grouped under ``issuer`` while older
        # clients still send the flat agency aliases.
        'issuer_name': ('issuer_name', 'agency_name', 'name'),
        'default_author': ('default_author',),
        'default_contact': ('default_contact',),
        'default_confidentiality': ('default_confidentiality', 'footer_text'),
        'default_cta': ('default_cta',),
        'primary_color': ('primary_color',),
        'secondary_color': ('secondary_color',),
        'accent_color': ('accent_color',),
        'logo_asset_id': ('logo_asset_id',),
    }
    for field, keys in aliases.items():
        value = next((issuer.get(key) for key in keys if issuer.get(key) is not None), None)
        if value is None:
            value = next((appearance.get(key) for key in keys if appearance.get(key) is not None), None)
        if value is None:
            value = next((payload.get(key) for key in keys if payload.get(key) is not None), None)
        if value is not None:
            update[field] = normalize_report_profile_setting(field, value)
    return update


def request_json_object():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ValueError('JSON body must be an object')
    return payload


def _first_nonblank(*values):
    for value in values:
        if value is not None and str(value).strip():
            return value
    return None


def resolve_report_request_options(payload, settings):
    """Merge per-report overrides with saved report settings."""
    payload = payload or {}
    settings = settings or {}
    if payload.get('use_v2') is True and not REPORTS_V2_ENABLED:
        raise ValueError('Report Suite V2 no está habilitado en este entorno')
    language = normalize_report_language(_first_nonblank(payload.get('language'), settings.get('default_language')) or 'es-ES')
    # Explicitly non-V2 callers retain the legacy single-product behaviour.
    # A new profile defaults to ``pack`` for the UI, but that default must not
    # unexpectedly turn an old ``use_v2:false`` request into three jobs.
    v2_requested = payload.get('use_v2') is True
    if not v2_requested:
        mode = _first_nonblank(
            payload.get('tone'), payload.get('report_type'), payload.get('default_tone'), settings.get('default_tone'),
        ) or 'executive'
    else:
        mode = _first_nonblank(
            payload.get('report_mode'), payload.get('report_type'), payload.get('tone'), payload.get('default_tone'),
            settings.get('default_report_mode'), settings.get('default_tone'),
        ) or 'pack'
    mode = str(mode)
    if mode == 'pack':
        report_type = 'executive'
        report_types = _normalise_report_types(payload.get('report_types'), 'executive')
        if payload.get('report_types') is None:
            report_types = ['executive', 'commercial', 'technical']
    else:
        report_type = normalize_report_tone(mode)
        report_types = _normalise_report_types(payload.get('report_types'), report_type)
    model = _first_nonblank(
        payload.get('manual_model'),
        payload.get('model'),
        payload.get('default_model'),
        settings.get('manual_model'),
        settings.get('default_model'),
    )
    model = normalize_report_model(model) if model else None
    issuer = payload.get('issuer') if isinstance(payload.get('issuer'), dict) else {}
    branding = {
        'agency_name': _first_nonblank(payload.get('issuer_name'), issuer.get('name'), settings.get('issuer_name'), settings.get('agency_name')),
        'primary_color': _first_nonblank(payload.get('primary_color'), settings.get('primary_color')),
        'secondary_color': _first_nonblank(payload.get('secondary_color'), settings.get('secondary_color')),
        'accent_color': _first_nonblank(payload.get('accent_color'), settings.get('accent_color')),
        'logo_asset_id': _first_nonblank(payload.get('logo_asset_id'), settings.get('logo_asset_id')),
    }
    brand_kit = _safe_report_mapping(payload.get('brand_kit'), {
        'client_name', 'primary_color', 'secondary_color', 'accent_color', 'logo_asset_id',
        'logo_path', 'footer_text', 'font_family',
    })
    if payload.get('use_v2') is True and brand_kit.get('logo_path'):
        raise ValueError('V2 logos must use an owned logo asset')
    branding.update({key: value for key, value in brand_kit.items() if value})
    if 'logo_asset_id' in brand_kit:
        branding['logo_asset_id'] = brand_kit['logo_asset_id']
    client_context = _safe_report_mapping(payload.get('client_context'), {
        'client_name', 'report_title', 'author', 'confidentiality', 'contact',
        'business_goals', 'cta', 'market',
    })
    for field, profile_key in {
        'author': 'default_author', 'contact': 'default_contact',
        'confidentiality': 'default_confidentiality', 'cta': 'default_cta',
    }.items():
        if not client_context.get(field) and settings.get(profile_key):
            client_context[field] = settings[profile_key]
    if brand_kit.get('client_name') and not client_context.get('client_name'):
        client_context['client_name'] = brand_kit['client_name']
    commercial_context = payload.get('commercial_context') or settings.get('default_commercial_context') or 'prospect'
    if commercial_context not in REPORT_COMMERCIAL_CONTEXTS:
        raise ValueError('Unsupported commercial context')
    baseline_crawl_id = _normalise_optional_crawl_id(payload.get('baseline_crawl_id'))
    enrichments = _safe_enrichments(payload.get('enrichments'))
    return {
        'language': language,
        'tone': report_type,
        'report_type': report_type,
        'report_types': report_types,
        'model': model,
        'branding': branding,
        'client_context': client_context,
        'commercial_context': commercial_context,
        'baseline_crawl_id': baseline_crawl_id,
        'enrichments': enrichments,
        'use_v2': bool(payload.get('use_v2', False)) and REPORTS_V2_ENABLED,
        'openrouter_api_key': settings.get('openrouter_api_key'),
    }


def _normalise_report_types(value, default):
    if value is None:
        return [default]
    if not isinstance(value, list) or not value:
        raise ValueError('report_types must be a non-empty list')
    result = []
    for item in value:
        report_type = normalize_report_tone(item)
        if report_type not in result:
            result.append(report_type)
    return result


def _safe_report_mapping(value, allowed):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('Report context must be an object')
    result = {}
    for key, item in value.items():
        if key not in allowed or item is None:
            continue
        text = str(item).strip()
        if len(text) > REPORT_MAX_FIELD_LENGTH:
            raise ValueError(f'{key} is too long')
        result[key] = text
    return result


def _normalise_optional_crawl_id(value):
    if value in (None, ''):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError('baseline_crawl_id must be an integer') from None
    if value < 1:
        raise ValueError('baseline_crawl_id must be positive')
    return value


def _safe_enrichments(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('enrichments must be an object')
    result = {}
    for source in ('gsc', 'ga4', 'crux', 'pagespeed'):
        item = value.get(source)
        if item is None:
            continue
        if not isinstance(item, dict):
            raise ValueError(f'{source} enrichment must be an object')
        # Jobs receive a source reference only. Enrichment payloads already
        # stored for the crawl are loaded server-side and never accepted from
        # this request, preventing credentials from entering a job contract.
        safe = {key: item[key] for key in ('status', 'connection_id') if key in item}
        if 'connection_id' in safe and len(str(safe['connection_id'])) > REPORT_MAX_FIELD_LENGTH:
            raise ValueError('connection_id is too long')
        result[source] = safe
    return result


def normalize_report_setting(field, value):
    """Normalize and validate one report setting value."""
    if value is None:
        return ''
    value = str(value).strip()
    if len(value) > REPORT_MAX_FIELD_LENGTH and field != 'openrouter_api_key':
        raise ValueError(f'{field} is too long')
    if field == 'default_language':
        return normalize_report_language(value)
    if field == 'default_tone':
        return normalize_report_tone(value)
    if field in ('default_model', 'manual_model') and value:
        return normalize_report_model(value)
    return value


def normalize_report_profile_setting(field, value):
    value = '' if value is None else str(value).strip()
    if field != 'openrouter_api_key' and len(value) > REPORT_MAX_FIELD_LENGTH:
        raise ValueError(f'{field} is too long')
    if field == 'default_language':
        return normalize_report_language(value)
    if field == 'default_report_mode':
        if value not in REPORT_MODES:
            raise ValueError('Unsupported report mode')
        return value
    if field == 'default_commercial_context':
        if value not in REPORT_COMMERCIAL_CONTEXTS:
            raise ValueError('Unsupported commercial context')
        return value
    if field in ('default_model', 'manual_model') and value:
        return normalize_report_model(value)
    if field in ('primary_color', 'secondary_color', 'accent_color'):
        if not re.match(r'^#[0-9a-fA-F]{6}$', value):
            raise ValueError(f'{field} must be a six-digit hex colour')
        return value.lower()
    if field == 'logo_asset_id' and value:
        if len(value) > REPORT_MAX_LOGO_ASSET_ID_LENGTH or not re.match(r'^[a-f0-9]+$', value):
            raise ValueError('Invalid logo asset')
    return value


def normalize_report_language(language):
    if language not in REPORT_LANGUAGES:
        raise ValueError('Unsupported report language')
    return language


def normalize_report_tone(tone):
    if tone not in REPORT_TONES:
        raise ValueError('Unsupported report tone')
    return tone


def normalize_report_model(model):
    model = str(model or '').strip()
    if not model or len(model) > 200 or any(ch.isspace() for ch in model):
        raise ValueError('Modelo de informe no válido')
    if not model.startswith(REPORT_MODEL_PREFIXES):
        raise ValueError('Unsupported report model provider')
    return model


def current_user_can_use_reports():
    """Reports use a shared OpenRouter key, so only admins can manage/run them."""
    return session.get('user_id') is not None and session.get('tier') == 'admin'


def report_feature_forbidden():
    return jsonify({'success': False, 'error': 'Los informes requieren una cuenta administradora'}), 403


def combine_report_usage(*usage_items):
    """Keep per-call usage and sum numeric token counters."""
    calls = [usage for usage in usage_items if usage]
    totals = {}
    for usage in calls:
        for key, value in usage.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return {'calls': calls, 'total': totals} if calls else None


def report_usage_cost(model_metadata, usage):
    """Calculate actual provider cost only when cached pricing is known."""
    pricing = (model_metadata or {}).get('pricing') or {}
    try:
        prompt_price = float(pricing.get('prompt'))
        completion_price = float(pricing.get('completion'))
    except (TypeError, ValueError):
        return None
    totals = (usage or {}).get('total') if isinstance(usage, dict) else {}
    totals = totals or {}
    prompt_tokens = totals.get('prompt_tokens', totals.get('input_tokens', 0)) or 0
    completion_tokens = totals.get('completion_tokens', totals.get('output_tokens', 0)) or 0
    try:
        return round(float(prompt_tokens) * prompt_price + float(completion_tokens) * completion_price, 6)
    except (TypeError, ValueError):
        return None


def report_access_context(report_id, user_id, session_id):
    """Return (job, crawl, allowed) for report endpoints."""
    from src.reporting_jobs import get_report_job
    from src.crawl_db import get_crawl_by_id

    job = get_report_job(report_id)
    if not job:
        return None, None, False
    crawl = get_crawl_by_id(job.get('crawl_id'))
    return job, crawl, user_can_access_crawl(crawl, user_id, session_id)


def _public_report_error(value):
    if not value:
        return None
    text = str(value)
    text = re.sub(r'\bsk-[A-Za-z0-9._-]+', '[clave redacted]', text)
    text = re.sub(r'(?<![A-Za-z0-9])/(?:[^\s,;]+)', '[ruta interna redacted]', text)
    return text[:500]


def public_report_job(job):
    """Return report job fields safe for API responses."""
    if not job:
        return None
    has_pdf = bool(job.get('pdf_path'))
    has_html = bool(job.get('html_path'))
    has_quality = bool(job.get('quality_path'))
    has_manifest = bool(job.get('manifest_path'))
    terminal = job.get('status') in {'completed', 'failed'}
    download_url = f"/api/reports/{job.get('id')}/download" if has_pdf and job.get('status') == 'completed' else None
    return {
        'id': job.get('id'),
        'crawl_id': job.get('crawl_id'),
        'status': job.get('status'),
        'stage': job.get('stage') or job.get('status') or 'queued',
        'language': job.get('language'),
        'tone': job.get('tone'),
        'report_type': job.get('report_type') or job.get('tone'),
        'commercial_context': job.get('commercial_context') or 'prospect',
        'pack_id': job.get('pack_id'),
        'template_version': job.get('template_version') or '1.0',
        'model': job.get('model'),
        'error': _public_report_error(job.get('error')) if job.get('status') == 'failed' else None,
        'created_at': job.get('created_at'),
        'updated_at': job.get('updated_at'),
        'completed_at': job.get('completed_at'),
        'usage': job.get('usage'),
        'cost_usd': job.get('cost_usd'),
        'quality_score': job.get('quality_score'),
        'quality_passed': bool(job.get('quality_passed')) if job.get('quality_passed') is not None else None,
        'repair_attempted': bool(job.get('repair_attempted')) if job.get('repair_attempted') is not None else None,
        'has_pdf': has_pdf,
        'has_html': has_html,
        'download_url': download_url,
        'html_url': f"/api/reports/{job.get('id')}/artifact/html" if has_html and job.get('status') == 'completed' else None,
        'quality_url': f"/api/reports/{job.get('id')}/artifact/quality" if has_quality and terminal else None,
        'manifest_url': f"/api/reports/{job.get('id')}/artifact/manifest" if has_manifest and terminal else None,
    }


def safe_report_pdf_path(job):
    """Return a verified report PDF path from a report job."""
    from pathlib import Path
    from src.reporting_pdf import DEFAULT_REPORTS_BASE_DIR

    if not job or not job.get('pdf_path'):
        return None
    path = Path(job['pdf_path']).resolve()
    base = DEFAULT_REPORTS_BASE_DIR.resolve()
    try:
        if not path.is_relative_to(base):
            return None
    except AttributeError:
        if base not in path.parents and path != base:
            return None
    return path


def safe_report_artifact_path(job, artifact):
    """Return a verified report artifact path without exposing server paths."""
    from pathlib import Path
    from src.reporting_pdf import DEFAULT_REPORTS_BASE_DIR

    fields = {
        'html': 'html_path', 'facts': 'facts_path', 'analysis': 'analysis_path',
        'document': 'document_path', 'quality': 'quality_path', 'manifest': 'manifest_path',
    }
    field = fields.get(artifact)
    if not field or not job or not job.get(field):
        return None
    path = Path(job[field]).resolve()
    base = DEFAULT_REPORTS_BASE_DIR.resolve()
    try:
        return path if path.is_relative_to(base) else None
    except AttributeError:
        return path if base in path.parents or path == base else None


def run_report_job(report_id, crawl_id, options, shared_facts=None, shared_analysis=None, shared_analysis_usage=None):
    """Generate one report in a background worker."""
    from src.reporting_jobs import update_report_job

    try:
        update_report_job(report_id, status='running')
        update_report_job(report_id, stage='facts')

        if options.get('use_v2'):
            _run_report_job_v2(
                report_id, crawl_id, options, shared_facts, shared_analysis, shared_analysis_usage
            )
            return

        from src.openrouter_client import OpenRouterClient, generate_report_markdown, generate_structured_findings
        from src.reporting_data import build_audit_packet
        from src.reporting_pdf import render_report_html, render_report_pdf, report_output_paths
        from src.reporting_prompts import get_prompt_bundle
        from src.reporting_settings import get_openrouter_model

        model = options['model']
        audit_packet = build_audit_packet(crawl_id)
        prompt_bundle = get_prompt_bundle(options['language'], options['tone'])
        model_metadata = get_openrouter_model(model)
        client = OpenRouterClient(options['openrouter_api_key'])

        update_report_job(report_id, stage='analysis')
        findings, findings_usage = generate_structured_findings(
            client, model, audit_packet, prompt_bundle, model_metadata
        )
        update_report_job(report_id, stage='writing')
        markdown_text, report_usage = generate_report_markdown(
            client, model, audit_packet, findings, prompt_bundle, model_metadata
        )

        paths = report_output_paths(crawl_id, report_id, language=options['language'], tone=options['tone'])
        paths['dir'].mkdir(parents=True, exist_ok=True)
        paths['markdown'].write_text(markdown_text, encoding='utf-8')
        html = render_report_html(
            markdown_text,
            options.get('branding'),
            audit_packet.get('crawl_metadata') or {},
            audit_packet=audit_packet,
        )
        paths['html'].write_text(html, encoding='utf-8')
        update_report_job(report_id, stage='render')
        render_report_pdf(html, paths['pdf'])

        update_report_job(
            report_id,
            status='completed',
            stage='complete',
            markdown_path=str(paths['markdown']),
            html_path=str(paths['html']),
            pdf_path=str(paths['pdf']),
            usage=combine_report_usage(findings_usage, report_usage),
            cost_usd=report_usage_cost(model_metadata, combine_report_usage(findings_usage, report_usage)),
        )
    except Exception as exc:
        # Persist the diagnostic in the same redacted form exposed by the API;
        # provider exceptions must never turn a job row into a secret sink.
        update_report_job(
            report_id,
            status='failed',
            stage='failed',
            error=_public_report_error(str(exc)) or 'Report generation failed',
        )


def run_report_quality_cycle(client, model, model_metadata, facts, analysis, document, prompt_bundle,
                             client_context=None, stage_callback=None):
    """Run deterministic QA, independent review, and at most one repair."""
    from src.openrouter_client import generate_report_quality_review, generate_report_repair
    from src.reporting_documents import (
        merge_quality_reviews, normalise_quality_review, normalise_repair, parse_json_response,
        validate_report_package,
    )

    usage = []
    if stage_callback:
        stage_callback('quality')
    deterministic_before = validate_report_package(facts, analysis, document)
    review_text, review_usage = generate_report_quality_review(
        client, model, facts, analysis, document, prompt_bundle, deterministic_before, model_metadata,
    )
    if review_usage:
        usage.append(review_usage)
    try:
        model_before = normalise_quality_review(parse_json_response(review_text))
    except ValueError as exc:
        model_before = _technical_quality_review(
            _structured_generation_error('independent quality review', exc, review_usage)
        )
    combined_before = merge_quality_reviews(deterministic_before, model_before)

    repaired = False
    repair_error = None
    repair_usage = None
    deterministic_after = deterministic_before
    model_after = model_before
    combined_after = combined_before
    if combined_before.get('verdict') == 'fail':
        repaired = True
        if stage_callback:
            stage_callback('repair')
        repair_text, repair_usage = generate_report_repair(
            client, model, facts, analysis, document, combined_before, prompt_bundle,
            client_context, model_metadata,
        )
        if repair_usage:
            usage.append(repair_usage)
        try:
            analysis, document = normalise_repair(
                parse_json_response(repair_text), facts, prompt_bundle['report_type'],
                prompt_bundle['language'], prompt_bundle['commercial_context'], client_context,
            )
        except ValueError as exc:
            repair_error = _structured_generation_error('report repair', exc, repair_usage)

        deterministic_after = validate_report_package(facts, analysis, document)
        if repair_error:
            model_after = _technical_quality_review(repair_error)
        else:
            final_text, final_usage = generate_report_quality_review(
                client, model, facts, analysis, document, prompt_bundle, deterministic_after, model_metadata,
            )
            if final_usage:
                usage.append(final_usage)
            try:
                model_after = normalise_quality_review(parse_json_response(final_text))
            except ValueError as exc:
                model_after = _technical_quality_review(
                    _structured_generation_error('final quality review', exc, final_usage)
                )
        combined_after = merge_quality_reviews(deterministic_after, model_after)

    quality = {
        'prompt_version': prompt_bundle.get('prompt_version', '2.1'),
        'initial': {
            'deterministic': deterministic_before,
            'model': model_before,
            'combined': combined_before,
        },
        'repair': {'attempted': repaired, 'error': repair_error},
        'final': {
            'deterministic': deterministic_after,
            'model': model_after,
            'combined': combined_after,
        },
    }
    return analysis, document, quality, usage


def _technical_quality_review(message):
    return {
        'verdict': 'error', 'passed': False, 'score': None, 'issues': [],
        'technical_error': message,
    }


def _structured_generation_error(stage, exc, usage):
    attempts = list((usage or {}).get('_attempts') or [])
    if not attempts and usage:
        attempts = [usage]
    reasons = [
        ((item or {}).get('_response') or {}).get('finish_reason')
        for item in attempts
    ]
    if 'length' in reasons:
        return f'The {stage} response was truncated before completing valid JSON.'
    return f'The {stage} response did not contain valid contract JSON: {exc}'


def _run_report_job_v2(report_id, crawl_id, options, shared_facts=None, shared_analysis=None, shared_analysis_usage=None):
    """Generate one V2 document from shared facts and a JSON-only model contract."""
    from src.openrouter_client import OpenRouterClient, generate_report_analysis, generate_report_document
    from src.reporting_documents import document_markdown, normalise_analysis, normalise_document, parse_json_response
    from src.reporting_pdf import render_report_document_html, render_report_pdf, report_output_paths
    from src.reporting_prompts import get_prompt_bundle
    from src.reporting_settings import get_openrouter_model
    from src.reporting_v2 import build_audit_facts
    from src.reporting_jobs import update_report_job

    facts = shared_facts or build_audit_facts(
        crawl_id,
        baseline_crawl_id=options.get('baseline_crawl_id'),
        enrichments=options.get('enrichments'),
    )
    model = options['model']
    model_metadata = get_openrouter_model(model)
    client = OpenRouterClient(options['openrouter_api_key'])
    update_report_job(report_id, stage='analysis')
    analysis_usage = shared_analysis_usage
    analysis = copy.deepcopy(shared_analysis) if shared_analysis is not None else None
    if analysis is None:
        analysis_bundle = get_prompt_bundle(options['language'], 'executive')
        analysis_text, analysis_usage = generate_report_analysis(client, model, facts, analysis_bundle, model_metadata)
        try:
            analysis = normalise_analysis(parse_json_response(analysis_text), facts, options['language'])
        except ValueError as exc:
            raise RuntimeError(_structured_generation_error('SEO analysis', exc, analysis_usage)) from exc

    prompt_bundle = get_prompt_bundle(
        options['language'], options.get('report_type') or options['tone'], options.get('commercial_context'),
    )
    update_report_job(report_id, stage='writing')
    document_text, document_usage = generate_report_document(
        client, model, facts, analysis, prompt_bundle, options.get('client_context'), model_metadata,
    )
    try:
        document = normalise_document(
            parse_json_response(document_text), facts, analysis, prompt_bundle['report_type'],
            options['language'], options.get('commercial_context'), options.get('client_context'),
        )
    except ValueError as exc:
        raise RuntimeError(_structured_generation_error('report composition', exc, document_usage)) from exc

    analysis, document, quality, quality_usage = run_report_quality_cycle(
        client, model, model_metadata, facts, analysis, document, prompt_bundle,
        options.get('client_context'), lambda stage: update_report_job(report_id, stage=stage),
    )

    paths = report_output_paths(crawl_id, report_id, language=options['language'], tone=prompt_bundle['report_type'])
    paths['dir'].mkdir(parents=True, exist_ok=True)
    paths['facts'].write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf-8')
    paths['analysis'].write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding='utf-8')
    paths['document'].write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
    paths['quality'].write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding='utf-8')
    final_quality = quality['final']['combined']
    manifest = {
        'template_version': '2.0', 'facts_version': facts.get('schema_version'),
        'report_type': prompt_bundle['report_type'], 'language': options['language'],
        'model': model, 'crawl_id': crawl_id, 'baseline_crawl_id': options.get('baseline_crawl_id'),
        'prompt_version': prompt_bundle.get('prompt_version', '2.1'), 'quality_score': final_quality['score'],
        'quality_passed': final_quality['passed'], 'repair_attempted': quality['repair']['attempted'],
    }
    combined_usage = combine_report_usage(analysis_usage, document_usage, *quality_usage)
    actual_cost = report_usage_cost(model_metadata, combined_usage)
    manifest['usage'] = combined_usage
    manifest['cost_usd'] = actual_cost
    paths['manifest'].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    if not final_quality['passed']:
        if final_quality.get('verdict') == 'error':
            error = (
                'The report content was preserved, but the independent quality review could not be completed.'
                if options['language'] == 'en'
                else 'El contenido del informe se conservó, pero no pudo completarse la revisión de calidad independiente.'
            )
        else:
            error = (
                f"The report did not pass quality control ({final_quality['score']}/100)."
                if options['language'] == 'en'
                else f"El informe no superó el control de calidad ({final_quality['score']}/100)."
            )
        update_report_job(
            report_id, status='failed', stage='failed', error=error,
            report_type=prompt_bundle['report_type'], commercial_context=options.get('commercial_context'),
            template_version='2.0', facts_path=str(paths['facts']), analysis_path=str(paths['analysis']),
            document_path=str(paths['document']), quality_path=str(paths['quality']),
            manifest_path=str(paths['manifest']), usage=combined_usage,
            cost_usd=actual_cost,
            quality_score=final_quality['score'], quality_passed=0,
            repair_attempted=int(quality['repair']['attempted']),
        )
        return
    paths['markdown'].write_text(document_markdown(document, analysis), encoding='utf-8')
    html = render_report_document_html(document, analysis, facts, options.get('branding'))
    paths['html'].write_text(html, encoding='utf-8')
    update_report_job(report_id, stage='render')
    render_report_pdf(html, paths['pdf'])
    update_report_job(
        report_id,
        status='completed', stage='complete',
        report_type=prompt_bundle['report_type'],
        commercial_context=options.get('commercial_context'),
        template_version='2.0',
        markdown_path=str(paths['markdown']), html_path=str(paths['html']), pdf_path=str(paths['pdf']),
        facts_path=str(paths['facts']), analysis_path=str(paths['analysis']),
        document_path=str(paths['document']), quality_path=str(paths['quality']), manifest_path=str(paths['manifest']),
        usage=combined_usage, quality_score=final_quality['score'], quality_passed=1,
        cost_usd=actual_cost,
        repair_attempted=int(quality['repair']['attempted']),
    )


def run_report_pack(crawl_id, jobs):
    """Generate one analysis for a pack, then compose every audience variant."""
    if not jobs:
        return
    shared_options = jobs[0][1]
    from src.reporting_jobs import update_report_job
    for report_id, _ in jobs:
        update_report_job(report_id, status='running', stage='facts')
    facts = None
    analysis = None
    analysis_usage = None
    try:
        from src.reporting_v2 import build_audit_facts
        facts = build_audit_facts(
            crawl_id, baseline_crawl_id=shared_options.get('baseline_crawl_id'),
            enrichments=shared_options.get('enrichments'),
        )
    except Exception:
        # The individual worker will surface a normal failed job if the
        # immutable facts snapshot itself cannot be built.
        facts = None

    if facts is not None:
        try:
            from src.openrouter_client import OpenRouterClient, generate_report_analysis
            from src.reporting_documents import normalise_analysis, parse_json_response
            from src.reporting_prompts import get_prompt_bundle
            from src.reporting_settings import get_openrouter_model

            client = OpenRouterClient(shared_options['openrouter_api_key'])
            raw_analysis, analysis_usage = generate_report_analysis(
                client, shared_options['model'], facts,
                get_prompt_bundle(shared_options['language'], 'executive'),
                get_openrouter_model(shared_options['model']),
            )
            try:
                analysis = normalise_analysis(parse_json_response(raw_analysis), facts, shared_options['language'])
            except ValueError:
                # Let each product retry the schema-bound analysis. A shared
                # placeholder would make every document look valid while
                # discarding the model's actual SEO reasoning.
                analysis = None
        except Exception:
            # Individual jobs retry independently so an unavailable shared
            # call cannot silently downgrade an entire report pack.
            analysis = None
    if facts is not None:
        for report_id, _ in jobs:
            update_report_job(report_id, stage='analysis')
    for report_id, options in jobs:
        run_report_job(report_id, crawl_id, options, facts, analysis, analysis_usage)

def get_or_create_crawler():
    """Get or create a crawler instance for the current session"""
    session_id = ensure_session_id()
    user_id = session.get('user_id')  # Get user_id from session
    tier = session.get('tier', 'guest')  # Get tier from session

    with instances_lock:
        # Check if crawler exists for this session
        if session_id not in crawler_instances:
            print(f"Creating new crawler instance for session: {session_id}, user: {user_id}, tier: {tier}")
            crawler_instances[session_id] = {
                'crawler': WebCrawler(),
                'settings': SettingsManager(session_id=session_id, user_id=user_id, tier=tier),  # Per-user settings
                'last_accessed': datetime.now()
            }
        else:
            # Update last accessed time
            crawler_instances[session_id]['last_accessed'] = datetime.now()

        return crawler_instances[session_id]['crawler']

def get_session_settings():
    """Get the settings manager for the current session"""
    session_id = ensure_session_id()
    user_id = session.get('user_id')  # Get user_id from session
    tier = session.get('tier', 'guest')  # Get tier from session

    with instances_lock:
        # Create instance if it doesn't exist
        if session_id not in crawler_instances:
            print(f"Creating new settings instance for session: {session_id}, user: {user_id}, tier: {tier}")
            crawler_instances[session_id] = {
                'crawler': WebCrawler(),
                'settings': SettingsManager(session_id=session_id, user_id=user_id, tier=tier),
                'last_accessed': datetime.now()
            }
        else:
            # Update last accessed time
            crawler_instances[session_id]['last_accessed'] = datetime.now()

        return crawler_instances[session_id]['settings']

def cleanup_old_instances():
    """Remove crawler instances that haven't been accessed in 1 hour"""
    timeout = timedelta(hours=1)
    now = datetime.now()

    with instances_lock:
        sessions_to_remove = []
        for session_id, instance_data in crawler_instances.items():
            if now - instance_data['last_accessed'] > timeout:
                sessions_to_remove.append(session_id)

        for session_id in sessions_to_remove:
            print(f"Cleaning up crawler instance for session: {session_id}")
            # Server-side crawl jobs are owned by crawl_jobs, not by browser sessions.
            # Removing an inactive session must not stop a crawl that is still running.
            del crawler_instances[session_id]

        if sessions_to_remove:
            print(f"Cleaned up {len(sessions_to_remove)} inactive crawler instances")

def start_cleanup_thread():
    """Start background thread to cleanup old instances"""
    def cleanup_loop():
        while True:
            time.sleep(300)  # Check every 5 minutes
            try:
                cleanup_old_instances()
            except Exception as e:
                print(f"Error in cleanup thread: {e}")

    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()
    print("Started crawler instance cleanup thread")

def generate_csv_export(urls, fields):
    """Generate CSV export content"""
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()

    for url_data in urls:
        row = {}
        for field in fields:
            value = url_data.get(field, '')

            # Handle complex data types for CSV
            if field == 'analytics' and isinstance(value, dict):
                analytics_list = []
                if value.get('gtag') or value.get('ga4_id'): analytics_list.append('GA4')
                if value.get('google_analytics'): analytics_list.append('GA')
                if value.get('gtm_id'): analytics_list.append('GTM')
                if value.get('facebook_pixel'): analytics_list.append('FB')
                if value.get('hotjar'): analytics_list.append('HJ')
                if value.get('mixpanel'): analytics_list.append('MP')
                row[field] = ', '.join(analytics_list)
            elif field == 'og_tags' and isinstance(value, dict):
                row[field] = f"{len(value)} tags" if value else ''
            elif field == 'twitter_tags' and isinstance(value, dict):
                row[field] = f"{len(value)} tags" if value else ''
            elif field == 'json_ld' and isinstance(value, list):
                row[field] = f"{len(value)} scripts" if value else ''
            elif field == 'images' and isinstance(value, list):
                row[field] = f"{len(value)} images" if value else ''
            elif field == 'internal_links' and isinstance(value, (int, float)):
                row[field] = f"{int(value)} internal links" if value else '0 internal links'
            elif field == 'external_links' and isinstance(value, (int, float)):
                row[field] = f"{int(value)} external links" if value else '0 external links'
            elif field == 'h2' and isinstance(value, list):
                row[field] = ', '.join(value[:3]) + ('...' if len(value) > 3 else '')
            elif field == 'h3' and isinstance(value, list):
                row[field] = ', '.join(value[:3]) + ('...' if len(value) > 3 else '')
            elif isinstance(value, (dict, list)):
                row[field] = str(value)
            else:
                row[field] = value

        writer.writerow(row)

    return output.getvalue()

def generate_json_export(urls, fields):
    """Generate JSON export content"""
    filtered_urls = []
    for url_data in urls:
        filtered_data = {}
        for field in fields:
            value = url_data.get(field, '')
            # Keep complex data structures intact in JSON
            filtered_data[field] = value
        filtered_urls.append(filtered_data)

    return json.dumps({
        'export_date': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_urls': len(filtered_urls),
        'fields': fields,
        'data': filtered_urls
    }, indent=2, default=str)

def generate_xml_export(urls, fields):
    """Generate XML export content"""
    root = ET.Element('mitmore_seo_crawl_export')
    root.set('export_date', time.strftime('%Y-%m-%d %H:%M:%S'))
    root.set('total_urls', str(len(urls)))

    urls_element = ET.SubElement(root, 'urls')

    for url_data in urls:
        url_element = ET.SubElement(urls_element, 'url')
        for field in fields:
            field_element = ET.SubElement(url_element, field)
            field_element.text = str(url_data.get(field, ''))

    return ET.tostring(root, encoding='unicode')

def generate_links_csv_export(links):
    """Generate CSV export for links data"""
    output = StringIO()
    fieldnames = ['source_url', 'target_url', 'anchor_text', 'is_internal', 'target_domain', 'target_status', 'placement']
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for link in links:
        row = {
            'source_url': link.get('source_url', ''),
            'target_url': link.get('target_url', ''),
            'anchor_text': link.get('anchor_text', ''),
            'is_internal': 'Yes' if link.get('is_internal') else 'No',
            'target_domain': link.get('target_domain', ''),
            'target_status': link.get('target_status', 'Not crawled'),
            'placement': link.get('placement', 'body')
        }
        writer.writerow(row)

    return output.getvalue()

def generate_links_json_export(links):
    """Generate JSON export for links data"""
    return json.dumps(links, indent=2)

def filter_issues_by_exclusion_patterns(issues, exclusion_patterns):
    """Filter issues based on exclusion patterns (applies current settings to loaded crawls)"""
    from fnmatch import fnmatch
    from urllib.parse import urlparse

    if not exclusion_patterns:
        return issues

    filtered_issues = []

    for issue in issues:
        url = issue.get('url', '')
        parsed = urlparse(url)
        path = parsed.path

        # Check if URL matches any exclusion pattern
        should_exclude = False
        for pattern in exclusion_patterns:
            if not pattern.strip() or pattern.strip().startswith('#'):
                continue

            if '*' in pattern:
                if fnmatch(path, pattern):
                    should_exclude = True
                    break
            elif path == pattern or path.startswith(pattern.rstrip('*')):
                should_exclude = True
                break

        if not should_exclude:
            filtered_issues.append(issue)

    return filtered_issues

def generate_issues_csv_export(issues):
    """Generate CSV export for issues data"""
    output = StringIO()
    fieldnames = ['url', 'type', 'category', 'issue', 'details']
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for issue in issues:
        row = {
            'url': issue.get('url', ''),
            'type': issue.get('type', ''),
            'category': issue.get('category', ''),
            'issue': issue.get('issue', ''),
            'details': issue.get('details', '')
        }
        writer.writerow(row)

    return output.getvalue()

def generate_issues_json_export(issues):
    """Generate JSON export for issues data"""
    # Group issues by URL for better organization
    issues_by_url = {}
    for issue in issues:
        url = issue.get('url', '')
        if url not in issues_by_url:
            issues_by_url[url] = []
        issues_by_url[url].append({
            'type': issue.get('type', ''),
            'category': issue.get('category', ''),
            'issue': issue.get('issue', ''),
            'details': issue.get('details', '')
        })

    return json.dumps({
        'export_date': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_issues': len(issues),
        'total_urls_with_issues': len(issues_by_url),
        'issues_by_url': issues_by_url,
        'all_issues': issues
    }, indent=2)

@app.route('/login')
def login_page():
    # In local mode, auto-login and redirect to index
    if LOCAL_MODE:
        auto_login_local_mode()
        return redirect(url_for('index'))
    # Redirect to app if already logged in
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('login.html', registration_disabled=DISABLE_REGISTER, guest_disabled=DISABLE_GUEST, skip_auth=SKIP_AUTH)

@app.route('/register')
def register_page():
    # Redirect to app if already logged in
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('register.html', registration_disabled=DISABLE_REGISTER)

@app.route('/verify')
def verify_email():
    """Email verification endpoint"""
    token = request.args.get('token')

    if not token:
        return render_template('verification_result.html',
                             success=False,
                             message='Enlace de verificación no válido',
                             app_source='main')

    # Verify the token
    success, message, app_source, user_email = verify_token(token)

    # Send welcome email if successful
    if success and user_email:
        try:
            user = get_user_by_email(user_email)
            if user:
                send_welcome_email(user_email, user['username'], app_source or 'main')
        except Exception as e:
            print(f"Error sending welcome email: {e}")

    # Determine redirect URL based on app_source
    redirect_url = None
    if success:
        if app_source == 'workshop':
            redirect_url = os.getenv('WORKSHOP_APP_URL', 'https://workshop.mitmore.dev')
        else:
            redirect_url = url_for('login_page')

    return render_template('verification_result.html',
                         success=success,
                         message=message,
                         app_source=app_source or 'main',
                         redirect_url=redirect_url)

@app.route('/api/register', methods=['POST'])
def register():
    # Check if registration is disabled
    if DISABLE_REGISTER:
        return jsonify({'success': False, 'message': 'El registro está desactivado'})

    data = request.get_json()
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    success, message, user_id = create_user(username, email, password)

    # In local mode, auto-verify and set to admin tier
    if success and LOCAL_MODE:
        try:
            from src.auth_db import verify_user, set_user_tier
            if user_id:
                verify_user(user_id)
                set_user_tier(user_id, 'admin')
                message = 'Cuenta creada y verificada. Tienes acceso de administrador en modo local.'
        except Exception as e:
            print(f"Error during local mode auto-verification: {e}")
            # Don't fail the registration, just log the error
            # The account is still created successfully
    elif success:
        # Not in local mode - send verification email
        is_resend = (message == 'resend')
        try:
            # Create verification token
            token = create_verification_token(user_id, app_source='main')
            if token:
                # Send verification email
                email_success, email_message = send_verification_email(
                    email, username, token, app_source='main', is_resend=is_resend
                )
                if email_success:
                    if is_resend:
                        message = 'Ya se había enviado un correo de verificación a esta dirección. Hemos actualizado los datos de tu cuenta y enviado un nuevo enlace de verificación.'
                    else:
                        message = 'Registro completado. Revisa tu correo para verificar la cuenta.'
                else:
                    message = 'Cuenta creada, pero no pudimos enviar el correo de verificación. Contacta con soporte.'
                    print(f"Email error: {email_message}")
            else:
                message = 'Cuenta creada, pero falló la generación del token de verificación. Contacta con soporte.'
        except Exception as e:
            print(f"Error sending verification email: {e}")
            message = 'Cuenta creada, pero no pudimos enviar el correo de verificación. Contacta con soporte.'

    return jsonify({'success': success, 'message': message})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    # Dangerously skip auth: accept any username with no password.
    # Username is only used to separate per-user sessions.
    if SKIP_AUTH:
        if not username:
            return jsonify({'success': False, 'message': 'Usuario requerido'})
        if len(username) > 50:
            return jsonify({'success': False, 'message': 'El usuario debe tener 50 caracteres o menos'})
        success, message = skip_auth_login(username)
        return jsonify({'success': success, 'message': message})

    success, message, user_data = authenticate_user(username, password)

    if success:
        session['user_id'] = user_data['id']
        session['username'] = user_data['username']
        # In local mode, always give admin tier
        session['tier'] = 'admin' if LOCAL_MODE else user_data['tier']
        session.permanent = True  # Remember login

    return jsonify({'success': success, 'message': message})

@app.route('/api/guest-login', methods=['POST'])
def guest_login():
    """Login as a guest user (no account required, limited to 3 crawls/24h)"""
    if DISABLE_GUEST:
        return jsonify({'success': False, 'message': 'El acceso como invitado está desactivado'})

    # Create a guest session with no user_id but with tier='guest'
    # In local mode, guests also get admin tier
    session['user_id'] = None
    session['username'] = 'Guest'
    session['tier'] = 'admin' if LOCAL_MODE else 'guest'
    session.permanent = False  # Don't persist guest sessions

    return jsonify({'success': True, 'message': 'Sesión iniciada como invitado'})

@app.route('/api/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Sesión cerrada correctamente'})

@app.route('/api/user/info')
@login_required
def user_info():
    """Get current user info including tier"""
    from src.auth_db import get_crawls_last_24h
    user_id = session.get('user_id')
    tier = session.get('tier', 'guest')
    username = session.get('username')

    # Get crawl count
    crawls_today = 0
    if tier == 'guest':
        # For guests, count from IP address
        client_ip = get_client_ip()
        crawls_today = get_guest_crawls_last_24h(client_ip)
    else:
        # For registered users, count from database
        crawls_today = get_crawls_last_24h(user_id)

    return jsonify({
        'success': True,
        'user': {
            'id': user_id,
            'username': username,
            'tier': tier,
            'crawls_today': crawls_today,
            'crawls_remaining': max(0, 3 - crawls_today) if tier == 'guest' else -1
        }
    })

@app.route('/')
def index():
    # In local mode, auto-login if not already logged in
    if LOCAL_MODE and 'user_id' not in session:
        auto_login_local_mode()
    elif 'user_id' not in session:
        # Not in local mode and not logged in, redirect to login
        return redirect(url_for('login_page'))
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
    """Crawl history dashboard"""
    return render_template('dashboard.html')

@app.route('/debug/memory')
@login_required
def debug_memory_page():
    """Debug page with nice UI for memory monitoring"""
    return render_template('debug_memory.html')

@app.route('/api/report-settings')
@login_required
def get_report_settings():
    """Return the authenticated user's report profile without exposing secrets."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.reporting_settings import get_report_asset

        user_id = session.get('user_id')
        profile = _get_report_profile(user_id)
        logo = get_report_asset(profile.get('logo_asset_id'), user_id)
        if logo:
            logo = {
                'asset_id': logo['asset_id'], 'mime_type': logo['mime_type'],
                'original_name': logo.get('original_name'),
                'preview_url': f"/api/report-assets/logo/{logo['asset_id']}",
            }
        return jsonify({
            'success': True,
            'settings': public_report_settings(profile, logo),
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': 'No se pudieron cargar los ajustes de informes'}), 500


@app.route('/api/report-settings', methods=['POST'])
@login_required
def save_report_settings():
    """Save the authenticated user's report profile."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.reporting_settings import save_user_reporting_settings, get_report_asset

        payload = request_json_object()
        update = report_profile_update_from_payload(payload)
        if update.get('logo_asset_id') and not get_report_asset(update['logo_asset_id'], session.get('user_id')):
            return jsonify({'success': False, 'error': 'Logo no encontrado o no autorizado'}), 400
        if update:
            save_user_reporting_settings(session.get('user_id'), update)
        profile = _get_report_profile(session.get('user_id'))
        logo = get_report_asset(profile.get('logo_asset_id'), session.get('user_id'))
        logo = {
            'asset_id': logo['asset_id'], 'mime_type': logo['mime_type'],
            'original_name': logo.get('original_name'),
            'preview_url': f"/api/report-assets/logo/{logo['asset_id']}",
        } if logo else None
        return jsonify({
            'success': True,
            'settings': public_report_settings(profile, logo),
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': 'No se pudieron guardar los ajustes de informes'}), 500


def _is_sole_admin(user_id):
    """Allow legacy profile migration only when ownership is unambiguous."""
    try:
        from src.auth_db import get_all_users
        admins = [row for row in (get_all_users() or []) if row.get('tier') == 'admin']
        return len(admins) == 1 and str(admins[0].get('id')) == str(user_id)
    except Exception:
        return False


def _get_report_profile(user_id):
    """Load a user profile, with a read-only legacy fallback during migration."""
    from src.reporting_settings import DEFAULT_REPORTING_PROFILE, get_reporting_settings, get_user_reporting_settings

    try:
        return get_user_reporting_settings(user_id, migrate_legacy=_is_sole_admin(user_id))
    except Exception:
        legacy = get_reporting_settings()
        profile = dict(DEFAULT_REPORTING_PROFILE)
        profile.update({key: value for key, value in legacy.items() if value not in (None, '')})
        return profile


@app.route('/api/report-assets/logo', methods=['POST'])
@login_required
def upload_report_logo():
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.reporting_settings import create_report_asset
        from src.report_assets import sanitise_logo

        path, mime_type, original_name = sanitise_logo(request.files.get('file'), session.get('user_id'))
        asset_id = create_report_asset(session.get('user_id'), path, mime_type, original_name)
        return jsonify({
            'success': True,
            'asset': {
                'asset_id': asset_id, 'mime_type': mime_type, 'original_name': original_name,
                'preview_url': f'/api/report-assets/logo/{asset_id}',
            },
        })
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 503
    except Exception as exc:
        return jsonify({'success': False, 'error': 'No se pudo procesar el logo'}), 500


@app.route('/api/report-assets/logo/<asset_id>')
@login_required
def preview_report_logo(asset_id):
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from pathlib import Path
        from src.reporting_settings import get_report_asset

        asset = get_report_asset(asset_id, session.get('user_id'))
        if not asset or not Path(asset['file_path']).is_file():
            return jsonify({'success': False, 'error': 'Logo no encontrado'}), 404
        return send_file(asset['file_path'], mimetype=asset['mime_type'], as_attachment=False)
    except Exception as exc:
        return jsonify({'success': False, 'error': 'No se pudo cargar el logo'}), 500


@app.route('/api/report-assets/logo/<asset_id>', methods=['DELETE'])
@login_required
def delete_report_logo(asset_id):
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.report_assets import remove_asset_file
        from src.reporting_settings import delete_report_asset, get_user_reporting_settings, save_user_reporting_settings

        profile = get_user_reporting_settings(session.get('user_id'))
        path = delete_report_asset(asset_id, session.get('user_id'))
        if not path:
            return jsonify({'success': False, 'error': 'Logo no encontrado'}), 404
        remove_asset_file(path)
        # Avoid leaving a stale default reference after deleting the asset that
        # is actually selected as the issuer logo. Client logos may be kept for
        # another report and must not clear the issuer profile.
        if profile.get('logo_asset_id') == asset_id:
            save_user_reporting_settings(session.get('user_id'), {'logo_asset_id': None})
        return jsonify({'success': True})
    except Exception as exc:
        return jsonify({'success': False, 'error': 'No se pudo eliminar el logo'}), 500


def _report_domain(crawl):
    value = str(
        (crawl or {}).get('final_domain')
        or (crawl or {}).get('final_url')
        or (crawl or {}).get('base_domain')
        or (crawl or {}).get('base_url')
        or ''
    ).strip().lower()
    if '://' in value:
        value = urlsplit(value).hostname or value
    value = value.split(':', 1)[0].rstrip('.')
    return value[4:] if value.startswith('www.') else value


def _crawl_is_prior(candidate, current):
    """Return whether a compatible crawl predates the current crawl.

    Older metadata rows may not have timestamps, so missing or malformed dates
    remain eligible and are still checked for ownership, completion, and final
    domain elsewhere.  This keeps the migration compatible without proposing a
    newer crawl as the historical baseline when reliable dates are available.
    """
    candidate_value = (candidate or {}).get('completed_at') or (candidate or {}).get('started_at')
    # A baseline should be finished before the current crawl began whenever
    # both lifecycle timestamps are available.
    current_value = (current or {}).get('started_at') or (current or {}).get('completed_at')
    if not candidate_value or not current_value:
        return True

    def parse(value):
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    try:
        return parse(candidate_value) < parse(current_value)
    except (TypeError, ValueError, OverflowError):
        # Preserve compatibility with legacy timestamp strings while avoiding
        # a hard failure in the preflight endpoint.
        return str(candidate_value) < str(current_value)


def _report_source_statuses(facts):
    labels = {
        'gsc': 'Google Search Console', 'ga4': 'Google Analytics 4',
        'crux': 'Chrome UX Report', 'pagespeed': 'PageSpeed / Lighthouse',
    }
    rows = []
    for key, label in labels.items():
        source = (facts.get('enrichments') or {}).get(key) or {}
        status = source.get('status') or 'not_connected'
        available = status in {'available', 'connected', 'stored'}
        rows.append({'key': key, 'label': label, 'status': 'available' if available else 'not_connected', 'available': available})
    return rows


def _report_cost_estimate(model, report_types, facts):
    """Estimate token envelope; return no money when provider pricing is absent."""
    from src.reporting_settings import get_openrouter_model
    from src.reporting_v2 import model_audit_facts

    metadata = get_openrouter_model(model) if model else None
    types = report_types or ['executive']
    writer_budgets = {'executive': 18000, 'commercial': 26000, 'technical': 45000}
    repair_budgets = {'executive': 24000, 'commercial': 34000, 'technical': 56000}
    model_facts = model_audit_facts(facts or {})
    facts_tokens = max(1, len(json.dumps(model_facts, ensure_ascii=False)) // 4)
    normal_output = 18000 + sum(writer_budgets.get(item, 18000) + 12000 for item in types)
    maximum_output = int(18000 * 2.5) + sum(int(
        (writer_budgets.get(item, 18000) + 12000 + repair_budgets.get(item, 24000) + 12000) * 2.5
    ) for item in types)
    normal_calls = 1 + (len(types) * 2)
    maximum_calls = 2 + (len(types) * 8)
    result = {
        'model': model, 'normal_calls': normal_calls, 'maximum_calls': maximum_calls,
        'normal_output_tokens': normal_output, 'maximum_output_tokens': maximum_output,
        'pricing_available': False, 'currency': 'USD', 'normal_cost_usd': None, 'maximum_cost_usd': None,
    }
    pricing = (metadata or {}).get('pricing') or {}
    try:
        prompt_price = float(pricing.get('prompt'))
        completion_price = float(pricing.get('completion'))
    except (TypeError, ValueError):
        return result
    normal_input = facts_tokens * normal_calls
    maximum_input = facts_tokens * maximum_calls
    result.update({
        'pricing_available': True,
        'normal_cost_usd': round((normal_input * prompt_price) + (normal_output * completion_price), 4),
        'maximum_cost_usd': round((maximum_input * prompt_price) + (maximum_output * completion_price), 4),
    })
    return result


@app.route('/api/crawls/<int:crawl_id>/report-preflight')
@login_required
def report_preflight(crawl_id):
    """Return a factual, token-free preview before a V2 report is queued."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.crawl_db import get_crawl_by_id, get_user_crawls
        from src.reporting_v2 import build_audit_facts

        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not user_can_access_crawl(crawl, session.get('user_id'), ensure_session_id()):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403
        if crawl.get('status') != 'completed':
            return jsonify({'success': False, 'error': 'El rastreo debe estar completado'}), 400
        payload = request.args
        requested_values = payload.getlist('report_types') if hasattr(payload, 'getlist') else None
        if not requested_values:
            requested_values = [payload.get('report_types', 'executive,commercial,technical')]
        requested = []
        for value in requested_values:
            if isinstance(value, (list, tuple)):
                requested.extend(str(item).strip() for item in value)
            else:
                requested.extend(str(value).strip() for value in str(value).split(',') if str(value).strip())
        requested = [item for item in requested if item]
        invalid_types = [item for item in requested if item not in REPORT_TYPES]
        if invalid_types:
            return jsonify({'success': False, 'error': 'Tipo de informe no válido'}), 400
        report_types = requested or ['executive', 'commercial', 'technical']
        baseline_raw = payload.get('baseline_crawl_id')
        baseline_disabled = str(baseline_raw or '').strip().lower() in {'none', 'null', 'off', 'disabled'}
        baseline_id = None if baseline_disabled else (_normalise_optional_crawl_id(baseline_raw) if baseline_raw else None)
        if baseline_id:
            if int(baseline_id) == int(crawl_id):
                return jsonify({'success': False, 'error': 'El rastreo base debe ser anterior al rastreo actual'}), 400
            baseline = get_crawl_by_id(baseline_id)
            if not baseline or not user_can_access_crawl(baseline, session.get('user_id'), ensure_session_id()):
                return jsonify({'success': False, 'error': 'No autorizado para usar el rastreo base'}), 403
            if baseline.get('status') != 'completed':
                return jsonify({'success': False, 'error': 'El rastreo base debe estar completado'}), 400
            if _report_domain(baseline) != _report_domain(crawl):
                return jsonify({'success': False, 'error': 'El rastreo base debe pertenecer al mismo dominio final'}), 400
            if not _crawl_is_prior(baseline, crawl):
                return jsonify({'success': False, 'error': 'El rastreo base debe ser anterior al rastreo actual'}), 400
        candidates = []
        for candidate in get_user_crawls(session.get('user_id'), limit=100, offset=0, status_filter='completed'):
            if (
                int(candidate.get('id')) == int(crawl_id)
                or _report_domain(candidate) != _report_domain(crawl)
                or not _crawl_is_prior(candidate, crawl)
            ):
                continue
            candidates.append({
                'id': candidate.get('id'), 'domain': _report_domain(candidate),
                'completed_at': candidate.get('completed_at'),
            })
        candidates.sort(key=lambda item: (item.get('completed_at') or '', item.get('id') or 0), reverse=True)
        recommended = candidates[0]['id'] if candidates and baseline_id is None and not baseline_disabled else baseline_id
        effective_baseline_id = recommended
        facts = build_audit_facts(crawl_id, crawl_metadata=crawl, baseline_crawl_id=effective_baseline_id)
        profile = _get_report_profile(session.get('user_id'))
        requested_model = payload.get('model')
        if requested_model:
            requested_model = normalize_report_model(requested_model)
        estimate = _report_cost_estimate(
            _first_nonblank(requested_model, profile.get('manual_model'), profile.get('default_model')),
            report_types, facts,
        )
        denoms = (facts.get('coverage') or {}).get('denominators') or {}
        return jsonify({
            'success': True,
            'crawl': {'id': crawl_id, 'domain': _report_domain(crawl), 'completed_at': crawl.get('completed_at')},
            'coverage': {
                'denominators': denoms, 'coverage_ratio': (facts.get('coverage') or {}).get('coverage_ratio'),
                'limitations': len(facts.get('limitations') or []),
                'limitation_items': list(facts.get('limitations') or []),
            },
            'sources': _report_source_statuses(facts),
            'historical': facts.get('comparison') or {'available': False},
            'baseline': {'selected_id': effective_baseline_id, 'candidates': candidates},
            'estimate': estimate,
            'feature': {'reports_v2_enabled': REPORTS_V2_ENABLED, 'quality_threshold': 90},
        })
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        return jsonify({'success': False, 'error': 'No se pudo calcular la cobertura del informe'}), 500


@app.route('/api/report-models')
@login_required
def get_report_models():
    """Return cached OpenRouter models."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.reporting_settings import list_openrouter_models

        return jsonify({'success': True, 'models': list_openrouter_models()})
    except Exception as e:
        return jsonify({'success': False, 'error': 'No se pudieron cargar los modelos'}), 500


@app.route('/api/report-models/refresh', methods=['POST'])
@login_required
def refresh_report_models():
    """Refresh and cache OpenRouter models using a stored or posted key."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.openrouter_client import refresh_models

        payload = request_json_object()
        settings = _get_report_profile(session.get('user_id'))
        api_key = _first_nonblank(payload.get('openrouter_api_key'), settings.get('openrouter_api_key'))
        if not api_key:
            return jsonify({'success': False, 'error': 'Se requiere la clave API de OpenRouter'}), 400
        return jsonify({'success': True, 'models': refresh_models(api_key)})
    except Exception as e:
        return jsonify({'success': False, 'error': 'No se pudieron actualizar los modelos'}), 500


@app.route('/api/crawls/<int:crawl_id>/reports', methods=['POST'])
@login_required
def create_crawl_report(crawl_id):
    """Queue one legacy report or a V2 report pack for a completed crawl."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.crawl_db import get_crawl_by_id
        from src.reporting_jobs import create_report_job, create_report_pack
        from src.reporting_settings import get_report_asset
        from src.report_assets import asset_data_uri

        user_id = session.get('user_id')
        session_id = ensure_session_id()
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403
        if crawl.get('status') != 'completed':
            return jsonify({'success': False, 'error': 'Los informes solo pueden generarse para rastreos completados'}), 400

        payload = request_json_object()
        profile = _get_report_profile(session.get('user_id'))
        options = resolve_report_request_options(payload, profile)
        if not options.get('model'):
            return jsonify({'success': False, 'error': 'Se requiere un modelo de informe'}), 400
        if not options.get('openrouter_api_key'):
            return jsonify({'success': False, 'error': 'Se requiere la clave API de OpenRouter'}), 400

        baseline_crawl_id = options.get('baseline_crawl_id')
        if baseline_crawl_id:
            if int(baseline_crawl_id) == int(crawl_id):
                return jsonify({'success': False, 'error': 'El rastreo base debe ser anterior al rastreo actual'}), 400
            baseline = get_crawl_by_id(baseline_crawl_id)
            if not baseline or not user_can_access_crawl(baseline, user_id, session_id):
                return jsonify({'success': False, 'error': 'No autorizado para usar el rastreo base'}), 403
            if baseline.get('status') != 'completed':
                return jsonify({'success': False, 'error': 'El rastreo base debe estar completado'}), 400
            if _report_domain(baseline) != _report_domain(crawl):
                return jsonify({'success': False, 'error': 'El rastreo base debe pertenecer al mismo dominio final'}), 400
            if not _crawl_is_prior(baseline, crawl):
                return jsonify({'success': False, 'error': 'El rastreo base debe ser anterior al rastreo actual'}), 400

        if len(options['report_types']) > 1 and not options.get('use_v2'):
            return jsonify({'success': False, 'error': 'El paquete de informes requiere activar Report Suite V2'}), 400

        pack_id = None
        if options.get('use_v2') and len(options['report_types']) > 1:
            pack_id = create_report_pack(crawl_id, options['language'], options['model'], {
                'report_types': options['report_types'],
                'commercial_context': options['commercial_context'],
                'baseline_crawl_id': baseline_crawl_id,
                'enrichments': options.get('enrichments') or {},
            })

        queued_jobs = []
        for report_type in options['report_types']:
            job_options = dict(options)
            job_options['tone'] = report_type
            job_options['report_type'] = report_type
            client_context = dict(options.get('client_context') or {})
            client_context.setdefault('client_name', _report_domain(crawl) or crawl.get('base_url') or '')
            job_options['client_context'] = client_context
            logo_asset_id = (options.get('branding') or {}).get('logo_asset_id')
            asset = get_report_asset(logo_asset_id, user_id) if logo_asset_id else None
            if logo_asset_id and not asset:
                return jsonify({'success': False, 'error': 'Logo no encontrado o no autorizado'}), 400
            if asset:
                logo_data_uri = asset_data_uri(asset)
                if not logo_data_uri:
                    return jsonify({'success': False, 'error': 'El logo guardado ya no está disponible'}), 400
                job_options['branding'] = dict(options.get('branding') or {})
                job_options['branding']['logo_path'] = logo_data_uri
            persisted_branding = {
                key: value for key, value in (job_options.get('branding') or {}).items()
                if key in {'agency_name', 'primary_color', 'secondary_color', 'accent_color',
                           'font_family', 'logo_asset_id', 'client_name'}
            }
            report_id = create_report_job(
                crawl_id, options['language'], report_type, options['model'],
                commercial_context=options['commercial_context'], branding=persisted_branding,
                client_context=client_context, enrichments=options.get('enrichments'), pack_id=pack_id,
                template_version='2.0' if options.get('use_v2') else '1.0',
            )
            queued_jobs.append((report_id, job_options))

        worker_target = run_report_pack if options.get('use_v2') and len(queued_jobs) > 1 else run_report_job
        worker_args = (crawl_id, queued_jobs) if worker_target is run_report_pack else (queued_jobs[0][0], crawl_id, queued_jobs[0][1])
        worker = threading.Thread(target=worker_target, args=worker_args, daemon=True)
        worker.start()
        return jsonify({
            'success': True,
            'report_id': queued_jobs[0][0],
            'report_ids': [report_id for report_id, _ in queued_jobs],
            'pack_id': pack_id,
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': 'No se pudo encolar el informe'}), 500


@app.route('/api/crawls/<int:crawl_id>/reports')
@login_required
def list_crawl_reports(crawl_id):
    """Return generated report jobs for one crawl."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.crawl_db import get_crawl_by_id
        from src.reporting_jobs import list_report_jobs_for_crawl

        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not user_can_access_crawl(crawl, session.get('user_id'), ensure_session_id()):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403
        return jsonify({
            'success': True,
            'reports': [public_report_job(job) for job in list_report_jobs_for_crawl(crawl_id)],
        })
    except Exception as e:
        return jsonify({'success': False, 'error': 'No se pudieron cargar los informes'}), 500


@app.route('/api/reports/<int:report_id>/status')
@login_required
def get_report_status(report_id):
    """Return one report job after verifying crawl access."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        job, crawl, allowed = report_access_context(
            report_id,
            session.get('user_id'),
            ensure_session_id(),
        )
        if not job:
            return jsonify({'success': False, 'error': 'Informe no encontrado'}), 404
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not allowed:
            return jsonify({'success': False, 'error': 'No autorizado'}), 403
        return jsonify({'success': True, 'report': public_report_job(job)})
    except Exception as e:
        return jsonify({'success': False, 'error': 'No se pudo consultar el estado del informe'}), 500


@app.route('/api/reports/<int:report_id>/download')
@login_required
def download_report(report_id):
    """Download a completed report PDF after verifying crawl access."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        job, crawl, allowed = report_access_context(
            report_id,
            session.get('user_id'),
            ensure_session_id(),
        )
        if not job:
            return jsonify({'success': False, 'error': 'Informe no encontrado'}), 404
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not allowed:
            return jsonify({'success': False, 'error': 'No autorizado'}), 403
        pdf_path = safe_report_pdf_path(job)
        if job.get('status') != 'completed' or not pdf_path or not os.path.exists(pdf_path):
            return jsonify({'success': False, 'error': 'El PDF del informe aún no está listo'}), 404
        return send_file(
            pdf_path,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'seo-report-{report_id}.pdf',
        )
    except Exception as e:
        return jsonify({'success': False, 'error': 'No se pudo descargar el informe'}), 500


@app.route('/api/reports/<int:report_id>/artifact/<artifact>')
@login_required
def report_artifact(report_id, artifact):
    """Serve a completed V2 HTML or JSON artifact after crawl access checks."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        job, crawl, allowed = report_access_context(report_id, session.get('user_id'), ensure_session_id())
        if not job:
            return jsonify({'success': False, 'error': 'Informe no encontrado'}), 404
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not allowed:
            return jsonify({'success': False, 'error': 'No autorizado'}), 403
        path = safe_report_artifact_path(job, artifact)
        can_download = job.get('status') == 'completed' or (
            job.get('status') == 'failed' and artifact in {'facts', 'analysis', 'document', 'quality', 'manifest'}
        )
        if not can_download or not path or not os.path.exists(path):
            return jsonify({'success': False, 'error': 'El artefacto del informe aún no está listo'}), 404
        mimetype = {
            'html': 'text/html', 'facts': 'application/json', 'analysis': 'application/json',
            'document': 'application/json', 'quality': 'application/json', 'manifest': 'application/json',
        }[artifact]
        return send_file(path, mimetype=mimetype, as_attachment=artifact != 'html')
    except Exception as e:
        return jsonify({'success': False, 'error': 'No se pudo cargar el artefacto del informe'}), 500

@app.route('/api/start_crawl', methods=['POST'])
@login_required
def start_crawl():
    from src.auth_db import get_crawls_last_24h, log_crawl_start

    data = request.get_json()
    url = data.get('url')

    if not url:
        return jsonify({'success': False, 'error': 'Se requiere una URL'})

    user_id = session.get('user_id')
    session_id = ensure_session_id()
    tier = session.get('tier', 'guest')

    # Check guest limits (IP-based) - skip in local mode
    if tier == 'guest' and not LOCAL_MODE:
        client_ip = get_client_ip()
        crawls_from_ip = get_guest_crawls_last_24h(client_ip)

        if crawls_from_ip >= 3:
            return jsonify({
                'success': False,
                'error': 'Guest limit reached: 3 crawls per 24 hours from your IP address. Please register for unlimited crawls.'
            })

        # Log this guest crawl
        log_guest_crawl(client_ip)

    settings_manager = get_session_settings()
    crawler = WebCrawler()

    # Apply current settings to crawler before starting
    try:
        crawler_config = settings_manager.get_crawler_config()
        crawler.update_config(crawler_config)
    except Exception as e:
        print(f"Warning: Could not apply settings: {e}")

    # Enforce demo mode limits
    if DEMO_MODE:
        crawler.config['demo_mode'] = True
        crawler.config['demo_memory_limit_bytes'] = int(1.5 * 1024 * 1024 * 1024)  # 1.5GB

    # Pass user_id and session_id for database persistence
    success, message = crawler.start_crawl(url, user_id=user_id, session_id=session_id)

    # Store crawl_id in session
    if success and crawler.crawl_id:
        session['current_crawl_id'] = crawler.crawl_id
        register_server_crawler(crawler)
        # Also log to old crawl_history for compatibility
        log_crawl_start(user_id, url)

    return jsonify({'success': success, 'message': message, 'crawl_id': crawler.crawl_id})

@app.route('/api/stop_crawl', methods=['POST'])
@login_required
def stop_crawl():
    crawl_id = session.get('current_crawl_id')
    if not crawl_id:
        return jsonify({'success': False, 'error': 'No hay ningún rastreo seleccionado'})
    return stop_crawl_by_id(crawl_id)

@app.route('/api/crawl_status')
@login_required
def crawl_status():
    crawl_id = session.get('current_crawl_id')
    if not crawl_id:
        crawler = get_or_create_crawler()
        return jsonify(crawler.get_status())

    from src.crawl_db import get_crawl_by_id
    crawl = get_crawl_by_id(crawl_id)
    if not crawl:
        return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
    if not user_can_access_crawl(crawl, session.get('user_id'), ensure_session_id()):
        return jsonify({'success': False, 'error': 'No autorizado'}), 403

    payload = build_crawl_summary(crawl_id)

    # Check for incremental update parameters
    url_since = request.args.get('url_since', type=int)
    link_since = request.args.get('link_since', type=int)
    issue_since = request.args.get('issue_since', type=int)

    # Legacy shape for old callers, backed by paged DB reads.
    payload['urls'] = load_crawl_urls_page(crawl_id, limit=500, offset=url_since or 0).get_json().get('urls', [])
    payload['links'] = load_crawl_links_page(crawl_id, limit=500, offset=link_since or 0).get_json().get('links', [])
    payload['issues'] = load_crawl_issues_page(crawl_id, limit=500, offset=issue_since or 0).get_json().get('issues', [])
    return jsonify(payload)


def _load_visualization_rows(crawl_id, url_limit=500, link_limit=2000):
    """Load the bounded graph input from the active result backend.

    Crawl results may live only in ClickHouse in production, while local
    installs can still use SQLite. Each collection falls back independently so
    a temporary failure in one ClickHouse table does not hide the other data.
    """
    from src.crawl_db import get_crawl_counts, load_crawled_urls, load_crawl_links

    crawled_pages = None
    all_links = None
    total_pages = None

    try:
        from src.crawl_clickhouse import load_links, load_urls

        url_page = load_urls(crawl_id, limit=url_limit)
        if url_page is not None:
            crawled_pages = url_page.get('rows') or []
            total_pages = int(url_page.get('total') or len(crawled_pages))
    except Exception as e:
        print(f"ClickHouse visualization URLs unavailable for crawl {crawl_id}: {e}")

    try:
        from src.crawl_clickhouse import load_links

        link_page = load_links(crawl_id, limit=link_limit)
        if link_page is not None:
            all_links = link_page.get('rows') or []
    except Exception as e:
        print(f"ClickHouse visualization links unavailable for crawl {crawl_id}: {e}")

    if crawled_pages is None:
        crawled_pages = load_crawled_urls(crawl_id, limit=url_limit)
        try:
            total_pages = int((get_crawl_counts(crawl_id) or {}).get('urls') or len(crawled_pages))
        except Exception:
            total_pages = len(crawled_pages)

    if all_links is None:
        all_links = load_crawl_links(crawl_id, limit=link_limit)

    return crawled_pages, all_links, total_pages


@app.route('/api/visualization_data')
@login_required
def visualization_data():
    """Get graph data for site structure visualization"""
    try:
        crawl_id = session.get('current_crawl_id')
        if crawl_id:
            from src.crawl_db import get_crawl_by_id

            crawl = get_crawl_by_id(crawl_id)
            if not crawl:
                return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
            if not user_can_access_crawl(crawl, session.get('user_id'), ensure_session_id()):
                return jsonify({'success': False, 'error': 'No autorizado'}), 403

            crawled_pages, all_links, total_pages = _load_visualization_rows(crawl_id)
        else:
            crawler = get_or_create_crawler()
            status_data = crawler.get_status()
            crawled_pages = status_data.get('urls', [])
            all_links = status_data.get('links', [])
            total_pages = len(crawled_pages)

        # Build nodes and edges for the graph
        nodes = []
        edges = []
        url_to_id = {}

        # Create nodes from crawled pages (limit to prevent lag)
        max_nodes = 500  # Optimization: limit nodes for performance
        pages_to_visualize = crawled_pages[:max_nodes]

        for idx, page in enumerate(pages_to_visualize):
            url = page.get('url', '')
            status_code = page.get('status_code', 0)

            # Assign color based on status code
            if 200 <= status_code < 300:
                color = '#10b981'  # Green for 2xx
            elif 300 <= status_code < 400:
                color = '#3b82f6'  # Blue for 3xx
            elif 400 <= status_code < 500:
                color = '#f59e0b'  # Orange for 4xx
            elif 500 <= status_code < 600:
                color = '#ef4444'  # Red for 5xx
            else:
                color = '#6b7280'  # Gray for other

            # Create node
            node = {
                'data': {
                    'id': f'node-{idx}',
                    'label': url.split('/')[-1] or url.split('//')[-1],  # Use last path segment or domain
                    'url': url,
                    'status_code': status_code,
                    'title': page.get('title', ''),
                    'color': color,
                    'size': 30 if idx == 0 else 20,  # Make root node larger
                    'depth': page.get('depth', 0)
                }
            }
            nodes.append(node)
            url_to_id[url] = f'node-{idx}'

        # Create edges from links data
        # Links are stored as: {'source_url': url, 'target_url': url, 'is_internal': bool, ...}
        edges_set = set()  # Use set to avoid duplicate edges
        for link in all_links:
            if link.get('is_internal'):  # Only use internal links
                source_url = link.get('source_url', '')
                target_url = link.get('target_url', '')

                source_id = url_to_id.get(source_url)
                target_id = url_to_id.get(target_url)

                if source_id and target_id and source_id != target_id:
                    edge_key = f'{source_id}-{target_id}'
                    if edge_key not in edges_set:
                        edges_set.add(edge_key)
                        edge = {
                            'data': {
                                'id': f'edge-{edge_key}',
                                'source': source_id,
                                'target': target_id
                            }
                        }
                        edges.append(edge)

        return jsonify({
            'success': True,
            'nodes': nodes,
            'edges': edges,
            'total_pages': total_pages,
            'visualized_pages': len(nodes),
            'truncated': total_pages > max_nodes
        })

    except Exception as e:
        print(f"Error generating visualization data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'nodes': [],
            'edges': []
        })

@app.route('/api/debug/memory')
@login_required
def debug_memory():
    """Debug endpoint showing memory stats for all active crawler instances"""
    with instances_lock:
        memory_stats = {
            'total_instances': len(crawler_instances),
            'instances': []
        }

        for session_id, instance_data in crawler_instances.items():
            crawler = instance_data['crawler']
            stats = crawler.memory_monitor.get_stats()

            memory_stats['instances'].append({
                'session_id': session_id[:8] + '...',  # Truncate for privacy
                'last_accessed': instance_data['last_accessed'].isoformat(),
                'urls_crawled': len(crawler.crawl_results),
                'memory': stats,
                'data_sizes': crawler.user_memory.get_stats()
            })

        return jsonify(memory_stats)

@app.route('/api/debug/memory/profile')
@login_required
def debug_memory_profile():
    """Detailed memory profiling - what's actually using the RAM"""
    from src.core.memory_profiler import MemoryProfiler

    with instances_lock:
        profiles = []

        for session_id, instance_data in crawler_instances.items():
            crawler = instance_data['crawler']

            # Get object breakdown
            breakdown = MemoryProfiler.get_object_memory_breakdown()

            profiles.append({
                'session_id': session_id[:8] + '...',
                'urls_crawled': len(crawler.crawl_results),
                'object_breakdown': breakdown,
                'data_sizes': crawler.user_memory.get_stats()
            })

        return jsonify({
            'total_instances': len(crawler_instances),
            'profiles': profiles
        })

@app.route('/api/filter_issues', methods=['POST'])
@login_required
def filter_issues():
    try:
        data = request.get_json()
        issues = data.get('issues', [])
        settings_manager = get_session_settings()

        # Get current exclusion patterns
        current_settings = settings_manager.get_settings()
        exclusion_patterns_text = current_settings.get('issueExclusionPatterns', '')
        exclusion_patterns = [p.strip() for p in exclusion_patterns_text.split('\n') if p.strip()]

        # Filter issues
        filtered_issues = filter_issues_by_exclusion_patterns(issues, exclusion_patterns)

        return jsonify({'success': True, 'issues': filtered_issues})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/get_settings')
@login_required
def get_settings():
    try:
        settings_manager = get_session_settings()
        settings = settings_manager.get_settings()
        return jsonify({'success': True, 'settings': settings})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/save_settings', methods=['POST'])
@login_required
def save_settings():
    try:
        data = request.get_json()
        settings_manager = get_session_settings()
        success, message = settings_manager.save_settings(data)
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/reset_settings', methods=['POST'])
@login_required
def reset_settings():
    try:
        settings_manager = get_session_settings()
        success, message = settings_manager.reset_settings()
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/update_crawler_settings', methods=['POST'])
@login_required
def update_crawler_settings():
    try:
        crawler = get_or_create_crawler()
        settings_manager = get_session_settings()
        # Get current settings and update crawler configuration
        crawler_config = settings_manager.get_crawler_config()
        crawler.update_config(crawler_config)
        return jsonify({'success': True, 'message': 'Ajustes del rastreador actualizados'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/pause_crawl', methods=['POST'])
@login_required
def pause_crawl():
    crawl_id = session.get('current_crawl_id')
    if not crawl_id:
        return jsonify({'success': False, 'error': 'No hay ningún rastreo seleccionado'})
    return pause_crawl_by_id(crawl_id)

@app.route('/api/resume_crawl', methods=['POST'])
@login_required
def resume_crawl():
    crawl_id = session.get('current_crawl_id')
    if not crawl_id:
        return jsonify({'success': False, 'error': 'No hay ningún rastreo seleccionado'})
    return resume_crawl_endpoint(crawl_id)

@app.route('/api/crawls/<int:crawl_id>/status')
@login_required
def crawl_status_by_id(crawl_id):
    """Get a small crawl status summary by ID."""
    try:
        from src.crawl_db import get_crawl_by_id

        session_id = ensure_session_id()
        user_id = session.get('user_id')
        crawl = get_crawl_by_id(crawl_id)

        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403

        summary = build_crawl_summary(crawl_id)
        return jsonify(summary)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/crawls/<int:crawl_id>/dashboard')
@login_required
def crawl_dashboard_by_id(crawl_id):
    """Return a compact technical SEO overview for the selected crawl."""
    try:
        from src.crawl_db import get_crawl_by_id
        from src.dashboard_data import build_dashboard_snapshot

        session_id = ensure_session_id()
        user_id = session.get('user_id')
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403

        exclusion_patterns_text = get_session_settings().get_settings().get('issueExclusionPatterns', '')
        exclusion_patterns = tuple(pattern.strip() for pattern in exclusion_patterns_text.split('\n') if pattern.strip())
        active = crawl_jobs.get(crawl_id)
        is_live = active is not None and active.is_running
        ttl = 5 if is_live else 60
        key = (crawl_id, exclusion_patterns)
        now = time.monotonic()
        with dashboard_cache_lock:
            cached = dashboard_cache.get(key)
            if cached and now - cached['created_at'] < ttl:
                payload = dict(cached['payload'])
            else:
                payload = None

        if payload is None:
            payload = build_dashboard_snapshot(crawl_id, exclusion_patterns)
            with dashboard_cache_lock:
                dashboard_cache[key] = {'created_at': now, 'payload': dict(payload)}

        crawl_payload = dict(payload.get('crawl') or {})
        discovered = int(crawl_payload.get('discovered') or 0)
        crawled = int(crawl_payload.get('crawled') or 0)
        if active:
            discovered = max(discovered, int(active.stats.get('discovered') or 0))
            crawled = int(active.stats.get('crawled') or crawled)
            crawl_payload['status'] = 'paused' if active.is_paused else 'running'
        crawl_payload['discovered'] = discovered
        crawl_payload['crawled'] = crawled
        crawl_payload['progress'] = round(min(100, (crawled / max(discovered, 1)) * 100), 1)
        crawl_payload['partial'] = crawl_payload.get('status') in {'running', 'paused'}
        payload['crawl'] = crawl_payload
        payload['success'] = True
        payload['generated_at'] = datetime.utcnow().isoformat(timespec='seconds') + 'Z'
        return jsonify(payload)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

def _next_cursor(rows, current):
    value = rows[-1].get('_row_order', current) if rows else current
    return str(value) if value is not None else None

def _offset_cursor(rows, offset):
    return offset + len(rows) if rows else offset

def _has_more(rows, limit):
    return len(rows) >= limit

def _page_limit(value, default=500, maximum=1000):
    try:
        return max(1, min(int(value or default), maximum))
    except (TypeError, ValueError):
        return default

def _page_offset(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _request_filters(include_issues=False):
    filters = {
        key: request.args.get(key)
        for key in ('kind', 'scope', 'status_family', 'content_type', 'depth', 'issue_type', 'category')
        if request.args.get(key) not in (None, '')
    }
    if include_issues:
        filters['issues'] = [value for value in request.args.getlist('issue') if value]
    return filters or None


def _row_matches_filters(row, filters, kind):
    filters = filters or {}
    legacy_kind = filters.get('kind')
    scope = filters.get('scope') or (legacy_kind if legacy_kind in {'internal', 'external'} else None)
    is_internal = row.get('is_internal') in (True, 1, '1', 'true', 'True')
    if scope == 'internal' and not is_internal:
        return False
    if scope == 'external' and is_internal:
        return False
    status = int(row.get('status_code' if kind == 'urls' else 'target_status') or 0)
    family = filters.get('status_family') or (legacy_kind if legacy_kind in ('2xx', '3xx', '4xx', '5xx', 'no_response') else None)
    if family in ('2xx', '3xx', '4xx', '5xx') and not (int(family[0]) * 100 <= status < int(family[0]) * 100 + 100):
        return False
    if kind == 'urls':
        if family == 'no_response' and status != 0:
            return False
        if filters.get('depth') not in (None, '') and int(row.get('depth') or 0) != int(filters['depth']):
            return False
        content_type = str(row.get('content_type') or '').lower()
        wanted = filters.get('content_type') or (legacy_kind if legacy_kind in ('html', 'css', 'js', 'images') else None)
        if wanted == 'js': wanted = 'javascript'
        if wanted and wanted not in content_type:
            return False
    if kind == 'issues':
        if filters.get('issue_type') and row.get('type') != filters['issue_type']:
            return False
        if filters.get('category') and row.get('category') != filters['category']:
            return False
        if filters.get('issues') and row.get('issue') not in filters['issues']:
            return False
    return True

@app.route('/api/crawls/<int:crawl_id>/urls')
@login_required
def load_crawl_urls_page(crawl_id, limit=None, offset=None):
    """Load one page of crawl URL rows."""
    try:
        from src.crawl_db import get_crawl_by_id, get_crawl_counts, load_crawled_urls

        session_id = ensure_session_id()
        user_id = session.get('user_id')
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403

        limit = _page_limit(limit if limit is not None else request.args.get('limit', 500, type=int))
        offset = _page_offset(offset if offset is not None else request.args.get('offset', 0, type=int))
        after = request.args.get('after', type=int)
        after = _page_offset(after) if after is not None else None
        filters = _request_filters()
        page_offset = after if after is not None else offset
        cursor_fallback = page_offset
        try:
            from src.crawl_clickhouse import load_urls
            clickhouse_page = load_urls(crawl_id, limit=limit, offset=offset, after=after, filters=filters)
            if clickhouse_page:
                rows = clickhouse_page['rows']
                return jsonify({
                    'success': True,
                    'crawl_id': crawl_id,
                    'urls': rows,
                    'limit': limit,
                    'offset': offset,
                    'next_cursor': _next_cursor(rows, cursor_fallback),
                    'has_more': _has_more(rows, limit),
                    'total': clickhouse_page['total'],
                    'source': 'clickhouse',
                })
        except Exception as e:
            print(f"ClickHouse URL page unavailable for crawl {crawl_id}: {e}")

        if filters:
            all_rows = [row for row in load_crawled_urls(crawl_id) if _row_matches_filters(row, filters, 'urls')]
            rows = all_rows[page_offset:page_offset + limit]
            total = len(all_rows)
        else:
            counts = get_crawl_counts(crawl_id)
            rows = load_crawled_urls(crawl_id, limit=limit, offset=page_offset)
            total = counts['urls']
        return jsonify({
            'success': True,
            'crawl_id': crawl_id,
            'urls': rows,
            'limit': limit,
            'offset': page_offset,
            'next_cursor': _offset_cursor(rows, page_offset),
            'has_more': _has_more(rows, limit),
            'total': total,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawls/<int:crawl_id>/links')
@login_required
def load_crawl_links_page(crawl_id, limit=None, offset=None):
    """Load one page of crawl link rows."""
    try:
        from src.crawl_db import get_crawl_by_id, get_crawl_counts, load_crawl_links

        session_id = ensure_session_id()
        user_id = session.get('user_id')
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403

        limit = _page_limit(limit if limit is not None else request.args.get('limit', 500, type=int))
        offset = _page_offset(offset if offset is not None else request.args.get('offset', 0, type=int))
        after = request.args.get('after', type=int)
        after = _page_offset(after) if after is not None else None
        filters = _request_filters()
        page_offset = after if after is not None else offset
        cursor_fallback = page_offset
        try:
            from src.crawl_clickhouse import load_links
            clickhouse_page = load_links(crawl_id, limit=limit, offset=offset, after=after, filters=filters)
            if clickhouse_page:
                rows = clickhouse_page['rows']
                return jsonify({
                    'success': True,
                    'crawl_id': crawl_id,
                    'links': rows,
                    'limit': limit,
                    'offset': offset,
                    'next_cursor': _next_cursor(rows, cursor_fallback),
                    'has_more': _has_more(rows, limit),
                    'total': clickhouse_page['total'],
                    'source': 'clickhouse',
                })
        except Exception as e:
            print(f"ClickHouse link page unavailable for crawl {crawl_id}: {e}")

        if filters:
            all_rows = [row for row in load_crawl_links(crawl_id) if _row_matches_filters(row, filters, 'links')]
            rows = all_rows[page_offset:page_offset + limit]
            total = len(all_rows)
        else:
            counts = get_crawl_counts(crawl_id)
            rows = load_crawl_links(crawl_id, limit=limit, offset=page_offset)
            total = counts['links']
        return jsonify({
            'success': True,
            'crawl_id': crawl_id,
            'links': rows,
            'limit': limit,
            'offset': page_offset,
            'next_cursor': _offset_cursor(rows, page_offset),
            'has_more': _has_more(rows, limit),
            'total': total,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawls/<int:crawl_id>/issues')
@login_required
def load_crawl_issues_page(crawl_id, limit=None, offset=None):
    """Load one page of crawl issue rows."""
    try:
        from src.crawl_db import get_crawl_by_id, get_crawl_counts, load_crawl_issues

        session_id = ensure_session_id()
        user_id = session.get('user_id')
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403

        limit = _page_limit(limit if limit is not None else request.args.get('limit', 500, type=int))
        offset = _page_offset(offset if offset is not None else request.args.get('offset', 0, type=int))
        after = request.args.get('after', type=int)
        after = _page_offset(after) if after is not None else None
        filters = _request_filters(include_issues=True)
        page_offset = after if after is not None else offset
        cursor_fallback = page_offset
        try:
            from src.crawl_clickhouse import load_issues
            clickhouse_page = load_issues(crawl_id, limit=limit, offset=offset, after=after, filters=filters)
            if clickhouse_page:
                rows = clickhouse_page['rows']
                issues = rows
                current_settings = get_session_settings().get_settings()
                exclusion_patterns_text = current_settings.get('issueExclusionPatterns', '')
                exclusion_patterns = [p.strip() for p in exclusion_patterns_text.split('\n') if p.strip()]
                issues = filter_issues_by_exclusion_patterns(issues, exclusion_patterns)
                return jsonify({
                    'success': True,
                    'crawl_id': crawl_id,
                    'issues': issues,
                    'limit': limit,
                    'offset': offset,
                    'next_cursor': _next_cursor(rows, cursor_fallback),
                    'has_more': _has_more(rows, limit),
                    'total': clickhouse_page['total'],
                    'source': 'clickhouse',
                })
        except Exception as e:
            print(f"ClickHouse issue page unavailable for crawl {crawl_id}: {e}")

        if filters:
            all_rows = [row for row in load_crawl_issues(crawl_id) if _row_matches_filters(row, filters, 'issues')]
            rows = all_rows[page_offset:page_offset + limit]
            total = len(all_rows)
        else:
            counts = get_crawl_counts(crawl_id)
            rows = load_crawl_issues(crawl_id, limit=limit, offset=page_offset)
            total = counts['issues']
        issues = rows
        if issues:
            current_settings = get_session_settings().get_settings()
            exclusion_patterns_text = current_settings.get('issueExclusionPatterns', '')
            exclusion_patterns = [p.strip() for p in exclusion_patterns_text.split('\n') if p.strip()]
            issues = filter_issues_by_exclusion_patterns(issues, exclusion_patterns)
        return jsonify({
            'success': True,
            'crawl_id': crawl_id,
            'issues': issues,
            'limit': limit,
            'offset': page_offset,
            'next_cursor': _offset_cursor(rows, page_offset),
            'has_more': _has_more(rows, limit),
            'total': total,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawls/<int:crawl_id>/samples')
@login_required
def crawl_samples_by_id(crawl_id):
    """Load recent ClickHouse samples plus the existing crawl summary."""
    try:
        from src.crawl_db import get_crawl_by_id
        from src.crawl_clickhouse import load_recent_issues, load_recent_urls

        session_id = ensure_session_id()
        user_id = session.get('user_id')
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403

        limit = _page_limit(request.args.get('limit', 20, type=int), default=20, maximum=100)
        summary = build_crawl_summary(crawl_id) or {}
        recent_urls = load_recent_urls(crawl_id, limit=limit) or {}
        recent_issues = load_recent_issues(crawl_id, limit=limit) or {}
        return jsonify({
            'success': True,
            'crawl_id': crawl_id,
            'recent_urls': recent_urls.get('rows', []),
            'recent_issues': recent_issues.get('rows', []),
            'analytics': summary.get('analytics'),
            'stats': summary.get('stats'),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawls/<int:crawl_id>/export/<dataset>')
@login_required
def download_crawl_export(crawl_id, dataset):
    """Stream one persisted crawl dataset from ClickHouse or legacy SQLite."""
    try:
        from src.crawl_db import get_crawl_by_id
        from src.crawl_export import (
            export_body,
            export_mimetype,
            normalize_export_fields,
            normalize_export_format,
            open_crawl_rows,
        )

        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not user_can_access_crawl(crawl, session.get('user_id'), ensure_session_id()):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403

        export_format = normalize_export_format(request.args.get('format', 'csv'))
        fields = normalize_export_fields(dataset, request.args.get('fields', ''))
        row_source = open_crawl_rows(crawl_id, dataset)
        rows = row_source.rows

        if dataset == 'issues':
            current_settings = get_session_settings().get_settings()
            patterns_text = current_settings.get('issueExclusionPatterns', '')
            patterns = [pattern.strip() for pattern in patterns_text.split('\n') if pattern.strip()]
            if patterns:
                rows = (
                    issue
                    for issue in rows
                    if filter_issues_by_exclusion_patterns([issue], patterns)
                )

        timestamp = time.strftime('%Y%m%d-%H%M%S')
        filename = f'mitmore_seo_crawl_{dataset}_{crawl_id}_{timestamp}.{export_format}'
        response = Response(
            stream_with_context(export_body(rows, fields, dataset, export_format)),
            content_type=export_mimetype(export_format),
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Cache-Control': 'no-store',
                'X-Accel-Buffering': 'no',
            },
        )
        return response
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawls/<int:crawl_id>/pause', methods=['POST'])
@login_required
def pause_crawl_by_id(crawl_id):
    """Pause an active crawl."""
    try:
        from src.crawl_db import get_crawl_by_id

        crawl = get_crawl_by_id(crawl_id)
        if not user_can_access_crawl(crawl, session.get('user_id'), ensure_session_id()):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403
        active = crawl_jobs.get(crawl_id)
        if not active:
            return jsonify({'success': False, 'error': 'El rastreo no está activo'}), 404
        success, message = active.pause_crawl()
        session['current_crawl_id'] = crawl_id
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawls/<int:crawl_id>/stop', methods=['POST'])
@login_required
def stop_crawl_by_id(crawl_id):
    """Stop an active crawl."""
    try:
        from src.crawl_db import get_crawl_by_id

        crawl = get_crawl_by_id(crawl_id)
        if not user_can_access_crawl(crawl, session.get('user_id'), ensure_session_id()):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403
        active = crawl_jobs.get(crawl_id)
        if not active:
            return jsonify({'success': False, 'error': 'El rastreo no está activo'}), 404
        success, message = active.stop_crawl()
        crawl_jobs.unregister(crawl_id)
        session['current_crawl_id'] = crawl_id
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawls/list')
@login_required
def list_crawls():
    """Get all crawls for current user"""
    try:
        user_id = session.get('user_id')
        from src.crawl_db import get_user_crawls, get_crawl_count

        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        status_filter = request.args.get('status')

        crawls = get_user_crawls(user_id, limit=limit, offset=offset, status_filter=status_filter)
        for crawl in crawls:
            crawl['is_active'] = crawl_jobs.is_active(crawl['id'])
        total_count = get_crawl_count(user_id)

        return jsonify({
            'success': True,
            'crawls': crawls,
            'total': total_count
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crawls/<int:crawl_id>')
@login_required
def get_crawl(crawl_id):
    """Get complete crawl data by ID"""
    try:
        user_id = session.get('user_id')
        session_id = ensure_session_id()
        from src.crawl_db import get_crawl_by_id, load_crawled_urls, load_crawl_links, load_crawl_issues

        # Get crawl metadata
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404

        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403

        # Load all data
        urls = load_crawled_urls(crawl_id)
        links = load_crawl_links(crawl_id)
        issues = load_crawl_issues(crawl_id)

        return jsonify({
            'success': True,
            'crawl': crawl,
            'urls': urls,
            'links': links,
            'issues': issues
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawls/<int:crawl_id>/load', methods=['POST'])
@login_required
def load_crawl_into_session(crawl_id):
    """Load a historical crawl into the current session"""
    try:
        user_id = session.get('user_id')
        session_id = ensure_session_id()
        from src.crawl_db import get_crawl_by_id

        # Get crawl metadata
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404

        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403

        counts = build_crawl_summary(crawl_id)['counts']
        session['current_crawl_id'] = crawl_id

        return jsonify({
            'success': True,
            'message': f'Adjuntado al rastreo {crawl_id}',
            'crawl_id': crawl_id,
            'urls_count': counts['urls'],
            'links_count': counts['links'],
            'issues_count': counts['issues'],
            'should_refresh_ui': True
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawls/<int:crawl_id>/resume', methods=['POST'])
@login_required
def resume_crawl_endpoint(crawl_id):
    """Resume an interrupted crawl"""
    try:
        user_id = session.get('user_id')
        session_id = ensure_session_id()
        from src.crawl_db import get_crawl_by_id

        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403

        active = crawl_jobs.get(crawl_id)
        if active:
            if active.is_paused:
                success, message = active.resume_crawl()
            elif active.is_running:
                success, message = True, 'El rastreo ya está en curso'
            else:
                crawl_jobs.unregister(crawl_id)
                active = None
            if active:
                session['current_crawl_id'] = crawl_id
                return jsonify({'success': success, 'message': message, 'crawl_id': crawl_id})

        crawler = WebCrawler()

        # Enforce demo mode limits on resumed crawls
        if DEMO_MODE:
            crawler.config['demo_mode'] = True
            crawler.config['demo_memory_limit_bytes'] = int(1.5 * 1024 * 1024 * 1024)

        # Resume from database
        success, message = crawler.resume_from_database(crawl_id, user_id=user_id, session_id=session_id)

        if success:
            session['current_crawl_id'] = crawl_id
            register_server_crawler(crawler)

        return jsonify({'success': success, 'message': message, 'crawl_id': crawl_id})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crawls/<int:crawl_id>/delete', methods=['DELETE'])
@login_required
def delete_crawl_endpoint(crawl_id):
    """Delete a crawl and all associated data"""
    try:
        user_id = session.get('user_id')
        session_id = ensure_session_id()
        from src.crawl_db import delete_crawl, get_crawl_by_id

        # Verify ownership
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404

        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403

        active = crawl_jobs.get(crawl_id)
        if active:
            active.stop_crawl()
            crawl_jobs.unregister(crawl_id)

        success = delete_crawl(crawl_id)
        return jsonify({'success': success, 'message': 'Rastreo eliminado correctamente' if success else 'No se pudo eliminar el rastreo'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crawls/<int:crawl_id>/archive', methods=['POST'])
@login_required
def archive_crawl(crawl_id):
    """Archive crawl (mark as archived but keep data)"""
    try:
        user_id = session.get('user_id')
        session_id = ensure_session_id()
        from src.crawl_db import set_crawl_status, get_crawl_by_id

        # Verify ownership
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404

        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'No autorizado'}), 403

        success = set_crawl_status(crawl_id, 'archived')
        return jsonify({'success': success, 'message': 'Rastreo archivado correctamente' if success else 'No se pudo archivar el rastreo'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crawls/stats')
@login_required
def crawl_stats():
    """Get statistics about user's crawls"""
    try:
        user_id = session.get('user_id')
        from src.crawl_db import get_crawl_count, get_crawl_status_counts, get_database_size_mb
        status_counts = get_crawl_status_counts(user_id)

        return jsonify({
            'success': True,
            'total_crawls': get_crawl_count(user_id),
            'by_status': status_counts,
            'database_size_mb': get_database_size_mb()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/export_data', methods=['POST'])
@login_required
def export_data():
    try:
        from src.crawl_export import normalize_export_fields, normalize_export_format

        data = request.get_json() or {}
        export_format = normalize_export_format(data.get('format', 'csv'))
        export_fields = data.get('fields', ['url', 'status_code', 'title'])
        if not isinstance(export_fields, list):
            return jsonify({'success': False, 'error': 'Los campos de exportación no son válidos'}), 400
        local_data = data.get('localData', {})
        crawl_id = data.get('crawlId') or session.get('current_crawl_id')

        # Persisted crawls are exported by dedicated streaming downloads. This
        # avoids loading million-row crawls into Flask and encoding them in JSON.
        if crawl_id:
            from src.crawl_db import get_crawl_by_id

            crawl = get_crawl_by_id(crawl_id)
            if not crawl:
                return jsonify({'success': False, 'error': 'Rastreo no encontrado'}), 404
            if not user_can_access_crawl(crawl, session.get('user_id'), ensure_session_id()):
                return jsonify({'success': False, 'error': 'No autorizado'}), 403

            regular_fields = [
                field for field in export_fields
                if field not in ('issues_detected', 'links_detailed')
            ]
            downloads = []
            if regular_fields:
                regular_fields = normalize_export_fields('urls', regular_fields)
                query = urlencode({
                    'format': export_format,
                    'fields': ','.join(regular_fields),
                })
                downloads.append({
                    'url': f'/api/crawls/{int(crawl_id)}/export/urls?{query}',
                    'dataset': 'urls',
                })
            if 'links_detailed' in export_fields:
                query = urlencode({'format': export_format})
                downloads.append({
                    'url': f'/api/crawls/{int(crawl_id)}/export/links?{query}',
                    'dataset': 'links',
                })
            if 'issues_detected' in export_fields:
                query = urlencode({'format': export_format})
                downloads.append({
                    'url': f'/api/crawls/{int(crawl_id)}/export/issues?{query}',
                    'dataset': 'issues',
                })
            if not downloads:
                return jsonify({'success': False, 'error': 'No hay campos para exportar'}), 400
            return jsonify({'success': True, 'downloads': downloads})

        # Keep the legacy in-memory path for local, non-persisted crawl data.
        if local_data and local_data.get('urls'):
            urls = local_data.get('urls', [])
            links = local_data.get('links', [])
            issues = local_data.get('issues', [])
        else:
            # Get current crawl results
            crawler = get_or_create_crawler()
            crawl_data = crawler.get_status()
            urls = crawl_data.get('urls', [])
            links = crawl_data.get('links', [])
            issues = crawl_data.get('issues', [])

        if not urls:
            return jsonify({'success': False, 'error': 'No hay datos para exportar'})

        if export_format == 'xlsx':
            return jsonify({
                'success': False,
                'error': 'La exportación XLSX requiere un rastreo guardado',
            }), 400

        # Update link statuses from crawled URLs (fixes missing status codes in exports)
        if links and urls:
            status_lookup = {url_data['url']: url_data.get('status_code') for url_data in urls}
            for link in links:
                target_url = link.get('target_url')
                if target_url in status_lookup:
                    link['target_status'] = status_lookup[target_url]

        # Apply current issue exclusion patterns (works for loaded crawls too)
        if issues:
            settings_manager = get_session_settings()
            current_settings = settings_manager.get_settings()
            exclusion_patterns_text = current_settings.get('issueExclusionPatterns', '')
            exclusion_patterns = [p.strip() for p in exclusion_patterns_text.split('\n') if p.strip()]
            issues = filter_issues_by_exclusion_patterns(issues, exclusion_patterns)

        # Collect files to export based on special field selections
        files_to_export = []

        # Check for special export fields and prepare them as separate files
        has_issues_export = 'issues_detected' in export_fields
        has_links_export = 'links_detailed' in export_fields

        # Remove special fields from regular export fields
        regular_fields = [f for f in export_fields if f not in ['issues_detected', 'links_detailed']]

        # Generate issues export if requested
        if has_issues_export:
            if export_format == 'csv':
                issues_content = generate_issues_csv_export(issues)
                issues_mimetype = 'text/csv'
                issues_filename = f'mitmore_seo_crawl_issues_{int(time.time())}.csv'
            elif export_format == 'json':
                issues_content = generate_issues_json_export(issues)
                issues_mimetype = 'application/json'
                issues_filename = f'mitmore_seo_crawl_issues_{int(time.time())}.json'
            else:
                issues_content = generate_issues_csv_export(issues)
                issues_mimetype = 'text/csv'
                issues_filename = f'mitmore_seo_crawl_issues_{int(time.time())}.csv'

            files_to_export.append({
                'content': issues_content,
                'mimetype': issues_mimetype,
                'filename': issues_filename
            })

        # Generate links export if requested
        if has_links_export:
            if export_format == 'csv':
                links_content = generate_links_csv_export(links)
                links_mimetype = 'text/csv'
                links_filename = f'mitmore_seo_crawl_links_{int(time.time())}.csv'
            elif export_format == 'json':
                links_content = generate_links_json_export(links)
                links_mimetype = 'application/json'
                links_filename = f'mitmore_seo_crawl_links_{int(time.time())}.json'
            else:
                links_content = generate_links_csv_export(links)
                links_mimetype = 'text/csv'
                links_filename = f'mitmore_seo_crawl_links_{int(time.time())}.csv'

            files_to_export.append({
                'content': links_content,
                'mimetype': links_mimetype,
                'filename': links_filename
            })

        # Generate regular export if there are regular fields
        if regular_fields:
            if export_format == 'csv':
                regular_content = generate_csv_export(urls, regular_fields)
                regular_mimetype = 'text/csv'
                regular_filename = f'mitmore_seo_crawl_export_{int(time.time())}.csv'
            elif export_format == 'json':
                regular_content = generate_json_export(urls, regular_fields)
                regular_mimetype = 'application/json'
                regular_filename = f'mitmore_seo_crawl_export_{int(time.time())}.json'
            elif export_format == 'xml':
                regular_content = generate_xml_export(urls, regular_fields)
                regular_mimetype = 'application/xml'
                regular_filename = f'mitmore_seo_crawl_export_{int(time.time())}.xml'
            else:
                return jsonify({'success': False, 'error': 'Formato de exportación no admitido'})

            files_to_export.append({
                'content': regular_content,
                'mimetype': regular_mimetype,
                'filename': regular_filename
            })

        # Handle special case where only special fields are selected but no data
        if not files_to_export:
            if has_issues_export and not issues:
                return jsonify({'success': False, 'error': 'No hay datos de incidencias para exportar'})
            elif has_links_export and not links:
                return jsonify({'success': False, 'error': 'No hay datos de enlaces para exportar'})
            else:
                return jsonify({'success': False, 'error': 'No hay datos para exportar'})

        # Return multiple files if we have more than one, otherwise single file
        if len(files_to_export) > 1:
            return jsonify({
                'success': True,
                'multiple_files': True,
                'files': files_to_export
            })
        else:
            # Single file
            file_data = files_to_export[0]
            return jsonify({
                'success': True,
                'content': file_data['content'],
                'mimetype': file_data['mimetype'],
                'filename': file_data['filename']
            })

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def recover_crashed_crawls():
    """Check for and recover any crashed crawls on startup"""
    try:
        from src.crawl_db import get_crashed_crawls, set_crawl_status

        crashed = get_crashed_crawls()

        if crashed:
            print("\n" + "=" * 60)
            print("CRASH RECOVERY")
            print("=" * 60)
            for crawl in crashed:
                set_crawl_status(crawl['id'], 'failed')
                print(f"Found crashed crawl: {crawl['base_url']} (ID: {crawl['id']})")
                print(f"  → Marked as failed. User can resume from dashboard.")
            print("=" * 60 + "\n")
    except Exception as e:
        print(f"Error during crash recovery: {e}")

def recover_interrupted_report_jobs():
    """Mark interrupted queued/running report jobs as failed on startup."""
    try:
        from src.reporting_jobs import mark_incomplete_jobs_failed

        count = mark_incomplete_jobs_failed()
        if count:
            print(f"Marked {count} interrupted report job(s) as failed")
    except Exception as e:
        print(f"Error during report job recovery: {e}")

def graceful_shutdown(signum, frame):
    """Save all active crawls before shutdown"""
    print("\n" + "=" * 60)
    print("GRACEFUL SHUTDOWN")
    print("=" * 60)
    print("Saving all active crawls...")

    try:
        with instances_lock:
            for session_id, instance_data in list(crawler_instances.items()):
                crawler = instance_data['crawler']
                if crawler.is_running and crawler.crawl_id and crawler.db_save_enabled:
                    print(f"  → Saving crawl {crawler.crawl_id}...")
                    try:
                        crawler._save_batch_to_db(force=True)
                        crawler._save_queue_checkpoint(force=True)
                        from src.crawl_db import set_crawl_status
                        set_crawl_status(crawler.crawl_id, 'paused')
                    except Exception as e:
                        print(f"    Error saving crawl {crawler.crawl_id}: {e}")

        for crawl_id in crawl_jobs.list_active_ids():
            crawler = crawl_jobs.get(crawl_id)
            if crawler and crawler.is_running and crawler.db_save_enabled:
                print(f"  → Saving server crawl {crawler.crawl_id}...")
                try:
                    crawler._save_batch_to_db(force=True)
                    crawler._save_queue_checkpoint(force=True)
                    from src.crawl_db import set_crawl_status
                    set_crawl_status(crawler.crawl_id, 'paused')
                except Exception as e:
                    print(f"    Error saving server crawl {crawler.crawl_id}: {e}")

        print("All crawls saved successfully")
        print("=" * 60)
    except Exception as e:
        print(f"Error during shutdown: {e}")

    print("Goodbye!")
    import sys
    sys.exit(0)

def main():
    import signal

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    # Recover any crashed crawls from previous session
    recover_crashed_crawls()
    recover_interrupted_report_jobs()

    # Start cleanup thread for old crawler instances
    start_cleanup_thread()

    print("=" * 60)
    print("Mitmore SEO Crawl - SEO Spider")
    print("=" * 60)
    print(f"\n🚀 Server starting on http://0.0.0.0:5000")
    print(f"🌐 Access from browser: http://localhost:5000")
    print(f"📱 Access from network: http://<your-ip>:5000")
    print(f"\n✨ Multi-tenancy enabled - each browser session is isolated")
    print(f"💾 Settings stored in browser localStorage")
    print(f"\nPress Ctrl+C to stop the server\n")
    print("=" * 60 + "\n")

    # Open browser in a separate thread after short delay
    def open_browser():
        time.sleep(1.5)  # Wait for Flask to start
        webbrowser.open('http://localhost:5000')

    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()

    # Run Flask server with Waitress (production-grade WSGI server)
    from waitress import serve
    print("Starting Mitmore SEO Crawl on http://localhost:5000")
    print("Using Waitress WSGI server with multi-threading support")
    serve(app, host='0.0.0.0', port=5000, threads=8)

if __name__ == '__main__':
    main()
