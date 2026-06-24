import threading
import time
import csv
import json
import xml.etree.ElementTree as ET
import uuid
import webbrowser
import argparse
import secrets
import string
import os
from io import StringIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
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
parser = argparse.ArgumentParser(description='LibreCrawl - SEO Spider Tool')
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

app = Flask(__name__, template_folder='web/templates', static_folder='web/static')
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
if not os.environ.get('SECRET_KEY'):
    print('⚠️  WARNING: SECRET_KEY not set — using an ephemeral random key. '
          'Sessions will not persist across restarts. Set SECRET_KEY in production.', flush=True)

# Enable compression for all responses
Compress(app)

# Initialize database on startup
init_db()

def generate_random_password(length=16):
    """Generate a random password with letters, digits, and symbols"""
    alphabet = string.ascii_letters + string.digits + string.punctuation
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def auto_login_local_mode():
    """Auto-login for local mode - creates or logs into 'local' admin account"""
    import sqlite3
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'users.db'))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if 'local' user exists
        cursor.execute('SELECT id, username, tier FROM users WHERE username = ?', ('local',))
        user = cursor.fetchone()

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
            from src.auth_db import hash_password
            password_hash = hash_password(random_password)

            cursor.execute('''
                INSERT INTO users (username, email, password_hash, verified, tier)
                VALUES (?, ?, ?, 1, 'admin')
            ''', ('local', 'local@localhost', password_hash))
            conn.commit()

            user_id = cursor.lastrowid

            # Log in the new user
            session['user_id'] = user_id
            session['username'] = 'local'
            session['tier'] = 'admin'
            session.permanent = True

            print(f"Created and auto-logged in as new 'local' admin user (ID: {user_id})")
            print(f"Generated password: {random_password}")

        conn.close()
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
    import sqlite3
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'users.db'))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('SELECT id, username FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()

        if user:
            user_id = user['id']
        else:
            from src.auth_db import hash_password
            random_password = generate_random_password()
            password_hash = hash_password(random_password)
            cursor.execute('''
                INSERT INTO users (username, email, password_hash, verified, tier)
                VALUES (?, ?, ?, 1, 'admin')
            ''', (username, f'{username}@skipauth.local', password_hash))
            conn.commit()
            user_id = cursor.lastrowid

        conn.close()

        session['user_id'] = user_id
        session['username'] = username
        session['tier'] = 'admin'
        session.permanent = True

        return True, 'Logged in (authentication skipped)'
    except sqlite3.IntegrityError as e:
        # Most likely the generated email collides with an existing account
        # whose email happens to match. Fall back to a clearer message.
        return False, f'Username conflict: try a different username ({e})'
    except Exception as e:
        print(f"Error in skip_auth_login: {e}")
        return False, f'Login error: {str(e)}'

if LOCAL_MODE:
    print("=" * 60)
    print("LOCAL MODE ENABLED")
    print("All users will have admin tier access")
    print("No rate limits or tier restrictions")
    print("Auto-login enabled with 'local' admin account")
    print("=" * 60)

if DISABLE_REGISTER:
    print("=" * 60)
    print("REGISTRATION DISABLED")
    print("New user registrations are not allowed")
    print("=" * 60)

if DISABLE_GUEST:
    print("=" * 60)
    print("GUEST MODE DISABLED")
    print("Guest login is not allowed")
    print("=" * 60)

if DEMO_MODE:
    print("=" * 60)
    print("DEMO MODE ENABLED")
    print("Memory limit: 1.5GB per user")
    print("Crawls will auto-stop when limit is reached")
    print("=" * 60)

if SKIP_AUTH:
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
        elif 'user_id' not in session:
            # Not in local mode and not logged in
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# Multi-tenant crawler instances
crawler_instances = {}  # session_id -> {'crawler': WebCrawler, 'settings': SettingsManager, 'last_accessed': datetime}
instances_lock = threading.Lock()

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
        discovered = link_stats.get('discovered', discovered)
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

REPORT_BRANDING_FIELDS = ('agency_name', 'primary_color', 'footer_text', 'logo_path')
REPORT_LANGUAGES = {'es-ES', 'en'}
REPORT_TONES = {'executive', 'technical', 'commercial'}
REPORT_MODEL_PREFIXES = ('openai/', 'anthropic/', 'deepseek/')
REPORT_MAX_FIELD_LENGTH = 500


def mask_openrouter_api_key(api_key):
    """Return a non-sensitive display value for an OpenRouter key."""
    if not api_key:
        return ''
    api_key = str(api_key)
    if len(api_key) <= 8:
        return '********'
    return f'{api_key[:4]}...{api_key[-4:]}'


def public_report_settings(settings):
    """Return report settings without exposing the stored API key."""
    public = dict(settings or {})
    api_key = public.pop('openrouter_api_key', None)
    public['openrouter_api_key'] = ''
    public['has_openrouter_api_key'] = bool(api_key)
    public['masked_openrouter_api_key'] = mask_openrouter_api_key(api_key)
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
    language = normalize_report_language(_first_nonblank(payload.get('language'), settings.get('default_language')) or 'es-ES')
    tone = normalize_report_tone(_first_nonblank(payload.get('tone'), settings.get('default_tone')) or 'executive')
    model = _first_nonblank(
        payload.get('manual_model'),
        payload.get('model'),
        payload.get('default_model'),
        settings.get('manual_model'),
        settings.get('default_model'),
    )
    model = normalize_report_model(model) if model else None
    branding = {
        field: _first_nonblank(payload.get(field), settings.get(field))
        for field in REPORT_BRANDING_FIELDS
    }
    return {
        'language': language,
        'tone': tone,
        'model': model,
        'branding': branding,
        'openrouter_api_key': settings.get('openrouter_api_key'),
    }


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
        raise ValueError('Invalid report model')
    if not model.startswith(REPORT_MODEL_PREFIXES):
        raise ValueError('Unsupported report model provider')
    return model


def current_user_can_use_reports():
    """Reports use a shared OpenRouter key, so only admins can manage/run them."""
    return session.get('user_id') is not None and session.get('tier') == 'admin'


def report_feature_forbidden():
    return jsonify({'success': False, 'error': 'Reports require an admin account'}), 403


def combine_report_usage(*usage_items):
    """Keep per-call usage and sum numeric token counters."""
    calls = [usage for usage in usage_items if usage]
    totals = {}
    for usage in calls:
        for key, value in usage.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return {'calls': calls, 'total': totals} if calls else None


def report_access_context(report_id, user_id, session_id):
    """Return (job, crawl, allowed) for report endpoints."""
    from src.reporting_jobs import get_report_job
    from src.crawl_db import get_crawl_by_id

    job = get_report_job(report_id)
    if not job:
        return None, None, False
    crawl = get_crawl_by_id(job.get('crawl_id'))
    return job, crawl, user_can_access_crawl(crawl, user_id, session_id)


def public_report_job(job):
    """Return report job fields safe for API responses."""
    if not job:
        return None
    return {
        'id': job.get('id'),
        'crawl_id': job.get('crawl_id'),
        'status': job.get('status'),
        'language': job.get('language'),
        'tone': job.get('tone'),
        'model': job.get('model'),
        'error': job.get('error') if job.get('status') == 'failed' else None,
        'created_at': job.get('created_at'),
        'updated_at': job.get('updated_at'),
        'completed_at': job.get('completed_at'),
        'usage': job.get('usage'),
        'has_pdf': bool(job.get('pdf_path')),
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


def run_report_job(report_id, crawl_id, options):
    """Generate one report in a background worker."""
    from src.reporting_jobs import update_report_job

    try:
        update_report_job(report_id, status='running')

        from src.openrouter_client import (
            OpenRouterClient,
            generate_report_markdown,
            generate_structured_findings,
        )
        from src.reporting_data import build_audit_packet
        from src.reporting_pdf import render_report_html, render_report_pdf, report_output_paths
        from src.reporting_prompts import get_prompt_bundle
        from src.reporting_settings import get_openrouter_model

        model = options['model']
        audit_packet = build_audit_packet(crawl_id)
        prompt_bundle = get_prompt_bundle(options['language'], options['tone'])
        model_metadata = get_openrouter_model(model)
        client = OpenRouterClient(options['openrouter_api_key'])

        findings, findings_usage = generate_structured_findings(
            client, model, audit_packet, prompt_bundle, model_metadata
        )
        markdown_text, report_usage = generate_report_markdown(
            client, model, audit_packet, findings, prompt_bundle, model_metadata
        )

        paths = report_output_paths(crawl_id, report_id)
        paths['dir'].mkdir(parents=True, exist_ok=True)
        paths['markdown'].write_text(markdown_text, encoding='utf-8')
        html = render_report_html(
            markdown_text,
            options.get('branding'),
            audit_packet.get('crawl_metadata') or {},
        )
        paths['html'].write_text(html, encoding='utf-8')
        render_report_pdf(html, paths['pdf'])

        update_report_job(
            report_id,
            status='completed',
            markdown_path=str(paths['markdown']),
            html_path=str(paths['html']),
            pdf_path=str(paths['pdf']),
            usage=combine_report_usage(findings_usage, report_usage),
        )
    except Exception as exc:
        update_report_job(report_id, status='failed', error=str(exc))

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
    root = ET.Element('librecrawl_export')
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
                             message='Invalid verification link',
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
            redirect_url = os.getenv('WORKSHOP_APP_URL', 'https://workshop.librecrawl.com')
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
        return jsonify({'success': False, 'message': 'Registration is currently disabled'})

    data = request.get_json()
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    success, message, user_id = create_user(username, email, password)

    # In local mode, auto-verify and set to admin tier
    if success and LOCAL_MODE:
        try:
            from src.auth_db import verify_user, set_user_tier
            # Get the user that was just created
            import sqlite3
            conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'users.db'))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
            user = cursor.fetchone()
            conn.close()

            if user:
                verify_user(user['id'])
                set_user_tier(user['id'], 'admin')
                message = 'Account created and verified! You have admin access in local mode.'
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
                        message = 'A verification email was already sent to this address. We\'ve updated your account details and sent a new verification link.'
                    else:
                        message = 'Registration successful! Please check your email to verify your account.'
                else:
                    message = 'Account created, but we could not send the verification email. Please contact support.'
                    print(f"Email error: {email_message}")
            else:
                message = 'Account created, but verification token generation failed. Please contact support.'
        except Exception as e:
            print(f"Error sending verification email: {e}")
            message = 'Account created, but we could not send the verification email. Please contact support.'

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
            return jsonify({'success': False, 'message': 'Username required'})
        if len(username) > 50:
            return jsonify({'success': False, 'message': 'Username must be 50 characters or less'})
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
        return jsonify({'success': False, 'message': 'Guest login is disabled'})

    # Create a guest session with no user_id but with tier='guest'
    # In local mode, guests also get admin tier
    session['user_id'] = None
    session['username'] = 'Guest'
    session['tier'] = 'admin' if LOCAL_MODE else 'guest'
    session.permanent = False  # Don't persist guest sessions

    return jsonify({'success': True, 'message': 'Logged in as guest'})

@app.route('/api/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})

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
    """Return report settings without exposing the OpenRouter API key."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.reporting_settings import get_reporting_settings

        return jsonify({
            'success': True,
            'settings': public_report_settings(get_reporting_settings()),
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/report-settings', methods=['POST'])
@login_required
def save_report_settings():
    """Save report settings, preserving a blank/missing API key."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.reporting_settings import get_reporting_settings, save_reporting_settings

        payload = request_json_object()
        update = report_settings_update_from_payload(payload)
        if update:
            save_reporting_settings(update)
        return jsonify({
            'success': True,
            'settings': public_report_settings(get_reporting_settings()),
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/report-models/refresh', methods=['POST'])
@login_required
def refresh_report_models():
    """Refresh and cache OpenRouter models using a stored or posted key."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.openrouter_client import refresh_models
        from src.reporting_settings import get_reporting_settings

        payload = request_json_object()
        settings = get_reporting_settings()
        api_key = _first_nonblank(payload.get('openrouter_api_key'), settings.get('openrouter_api_key'))
        if not api_key:
            return jsonify({'success': False, 'error': 'OpenRouter API key is required'}), 400
        return jsonify({'success': True, 'models': refresh_models(api_key)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/crawls/<int:crawl_id>/reports', methods=['POST'])
@login_required
def create_crawl_report(crawl_id):
    """Queue an AI PDF report for a completed crawl."""
    if not current_user_can_use_reports():
        return report_feature_forbidden()
    try:
        from src.crawl_db import get_crawl_by_id
        from src.reporting_jobs import create_report_job
        from src.reporting_settings import get_reporting_settings

        user_id = session.get('user_id')
        session_id = ensure_session_id()
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        if crawl.get('status') != 'completed':
            return jsonify({'success': False, 'error': 'Reports can only be generated for completed crawls'}), 400

        payload = request_json_object()
        options = resolve_report_request_options(payload, get_reporting_settings())
        if not options.get('model'):
            return jsonify({'success': False, 'error': 'Report model is required'}), 400
        if not options.get('openrouter_api_key'):
            return jsonify({'success': False, 'error': 'OpenRouter API key is required'}), 400

        report_id = create_report_job(
            crawl_id,
            options['language'],
            options['tone'],
            options['model'],
        )
        worker = threading.Thread(
            target=run_report_job,
            args=(report_id, crawl_id, options),
            daemon=True,
        )
        worker.start()
        return jsonify({'success': True, 'report_id': report_id})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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
            return jsonify({'success': False, 'error': 'Report not found'}), 404
        if not crawl:
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404
        if not allowed:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        return jsonify({'success': True, 'report': public_report_job(job)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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
            return jsonify({'success': False, 'error': 'Report not found'}), 404
        if not crawl:
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404
        if not allowed:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        pdf_path = safe_report_pdf_path(job)
        if job.get('status') != 'completed' or not pdf_path or not os.path.exists(pdf_path):
            return jsonify({'success': False, 'error': 'Report PDF is not ready'}), 404
        return send_file(
            pdf_path,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'librecrawl-report-{report_id}.pdf',
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/start_crawl', methods=['POST'])
@login_required
def start_crawl():
    from src.auth_db import get_crawls_last_24h, log_crawl_start

    data = request.get_json()
    url = data.get('url')

    if not url:
        return jsonify({'success': False, 'error': 'URL is required'})

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
        return jsonify({'success': False, 'error': 'No crawl selected'})
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
        return jsonify({'success': False, 'error': 'Crawl not found'}), 404
    if not user_can_access_crawl(crawl, session.get('user_id'), ensure_session_id()):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

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

@app.route('/api/visualization_data')
@login_required
def visualization_data():
    """Get graph data for site structure visualization"""
    try:
        crawl_id = session.get('current_crawl_id')
        if crawl_id:
            from src.crawl_db import load_crawled_urls, load_crawl_links
            crawled_pages = load_crawled_urls(crawl_id, limit=500)
            all_links = load_crawl_links(crawl_id, limit=2000)
        else:
            crawler = get_or_create_crawler()
            status_data = crawler.get_status()
            crawled_pages = status_data.get('urls', [])
            all_links = status_data.get('links', [])

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
            'total_pages': len(crawled_pages),
            'visualized_pages': len(nodes),
            'truncated': len(crawled_pages) > max_nodes
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
        return jsonify({'success': True, 'message': 'Crawler settings updated'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/pause_crawl', methods=['POST'])
@login_required
def pause_crawl():
    crawl_id = session.get('current_crawl_id')
    if not crawl_id:
        return jsonify({'success': False, 'error': 'No crawl selected'})
    return pause_crawl_by_id(crawl_id)

@app.route('/api/resume_crawl', methods=['POST'])
@login_required
def resume_crawl():
    crawl_id = session.get('current_crawl_id')
    if not crawl_id:
        return jsonify({'success': False, 'error': 'No crawl selected'})
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
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        summary = build_crawl_summary(crawl_id)
        return jsonify(summary)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        limit = min(limit or request.args.get('limit', 500, type=int), 1000)
        offset = offset if offset is not None else request.args.get('offset', 0, type=int)
        try:
            from src.crawl_clickhouse import load_urls
            clickhouse_page = load_urls(crawl_id, limit=limit, offset=offset)
            if clickhouse_page:
                return jsonify({
                    'success': True,
                    'crawl_id': crawl_id,
                    'urls': clickhouse_page['rows'],
                    'limit': limit,
                    'offset': offset,
                    'total': clickhouse_page['total'],
                    'source': 'clickhouse',
                })
        except Exception as e:
            print(f"ClickHouse URL page unavailable for crawl {crawl_id}: {e}")

        counts = get_crawl_counts(crawl_id)
        return jsonify({
            'success': True,
            'crawl_id': crawl_id,
            'urls': load_crawled_urls(crawl_id, limit=limit, offset=offset),
            'limit': limit,
            'offset': offset,
            'total': counts['urls'],
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
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        limit = min(limit or request.args.get('limit', 500, type=int), 1000)
        offset = offset if offset is not None else request.args.get('offset', 0, type=int)
        try:
            from src.crawl_clickhouse import load_links
            clickhouse_page = load_links(crawl_id, limit=limit, offset=offset)
            if clickhouse_page:
                return jsonify({
                    'success': True,
                    'crawl_id': crawl_id,
                    'links': clickhouse_page['rows'],
                    'limit': limit,
                    'offset': offset,
                    'total': clickhouse_page['total'],
                    'source': 'clickhouse',
                })
        except Exception as e:
            print(f"ClickHouse link page unavailable for crawl {crawl_id}: {e}")

        counts = get_crawl_counts(crawl_id)
        return jsonify({
            'success': True,
            'crawl_id': crawl_id,
            'links': load_crawl_links(crawl_id, limit=limit, offset=offset),
            'limit': limit,
            'offset': offset,
            'total': counts['links'],
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
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        limit = min(limit or request.args.get('limit', 500, type=int), 1000)
        offset = offset if offset is not None else request.args.get('offset', 0, type=int)
        try:
            from src.crawl_clickhouse import load_issues
            clickhouse_page = load_issues(crawl_id, limit=limit, offset=offset)
            if clickhouse_page:
                issues = clickhouse_page['rows']
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
                    'total': clickhouse_page['total'],
                    'source': 'clickhouse',
                })
        except Exception as e:
            print(f"ClickHouse issue page unavailable for crawl {crawl_id}: {e}")

        counts = get_crawl_counts(crawl_id)
        issues = load_crawl_issues(crawl_id, limit=limit, offset=offset)
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
            'offset': offset,
            'total': counts['issues'],
        })
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
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        active = crawl_jobs.get(crawl_id)
        if not active:
            return jsonify({'success': False, 'error': 'Crawl is not active'}), 404
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
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        active = crawl_jobs.get(crawl_id)
        if not active:
            return jsonify({'success': False, 'error': 'Crawl is not active'}), 404
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
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404

        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

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
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404

        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        counts = build_crawl_summary(crawl_id)['counts']
        session['current_crawl_id'] = crawl_id

        return jsonify({
            'success': True,
            'message': f'Attached to crawl {crawl_id}',
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
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404
        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        active = crawl_jobs.get(crawl_id)
        if active:
            if active.is_paused:
                success, message = active.resume_crawl()
            elif active.is_running:
                success, message = True, 'Crawl already running'
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
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404

        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        active = crawl_jobs.get(crawl_id)
        if active:
            active.stop_crawl()
            crawl_jobs.unregister(crawl_id)

        success = delete_crawl(crawl_id)
        return jsonify({'success': success, 'message': 'Crawl deleted successfully' if success else 'Failed to delete crawl'})
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
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404

        if not user_can_access_crawl(crawl, user_id, session_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        success = set_crawl_status(crawl_id, 'archived')
        return jsonify({'success': success, 'message': 'Crawl archived successfully' if success else 'Failed to archive crawl'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crawls/stats')
@login_required
def crawl_stats():
    """Get statistics about user's crawls"""
    try:
        user_id = session.get('user_id')
        from src.crawl_db import get_crawl_count, get_database_size_mb
        import sqlite3

        # Get counts by status
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'users.db'))
        cursor = conn.cursor()

        cursor.execute('''
            SELECT status, COUNT(*) as count
            FROM crawls
            WHERE user_id = ?
            GROUP BY status
        ''', (user_id,))

        status_counts = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()

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
        data = request.get_json()
        export_format = data.get('format', 'csv')
        export_fields = data.get('fields', ['url', 'status_code', 'title'])
        local_data = data.get('localData', {})
        crawl_id = data.get('crawlId') or session.get('current_crawl_id')

        # Use local data if provided (from loaded crawl), otherwise get from crawler
        if crawl_id:
            from src.crawl_db import load_crawled_urls, load_crawl_links, load_crawl_issues
            urls = load_crawled_urls(crawl_id)
            links = load_crawl_links(crawl_id)
            issues = load_crawl_issues(crawl_id)
        elif local_data and local_data.get('urls'):
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
            return jsonify({'success': False, 'error': 'No data to export'})

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
            print(f"DEBUG: After exclusion filter, {len(issues)} issues remain")

        # Collect files to export based on special field selections
        files_to_export = []

        # Check for special export fields and prepare them as separate files
        has_issues_export = 'issues_detected' in export_fields
        has_links_export = 'links_detailed' in export_fields

        # Remove special fields from regular export fields
        regular_fields = [f for f in export_fields if f not in ['issues_detected', 'links_detailed']]

        # Debug logging
        print(f"DEBUG: export_fields = {export_fields}")
        print(f"DEBUG: has_issues_export = {has_issues_export}")
        print(f"DEBUG: has_links_export = {has_links_export}")
        print(f"DEBUG: regular_fields = {regular_fields}")
        print(f"DEBUG: len(urls) = {len(urls)}")
        print(f"DEBUG: len(links) = {len(links)}")
        print(f"DEBUG: len(issues) = {len(issues)}")

        # Generate issues export if requested
        if has_issues_export:
            if export_format == 'csv':
                issues_content = generate_issues_csv_export(issues)
                issues_mimetype = 'text/csv'
                issues_filename = f'librecrawl_issues_{int(time.time())}.csv'
            elif export_format == 'json':
                issues_content = generate_issues_json_export(issues)
                issues_mimetype = 'application/json'
                issues_filename = f'librecrawl_issues_{int(time.time())}.json'
            else:
                issues_content = generate_issues_csv_export(issues)
                issues_mimetype = 'text/csv'
                issues_filename = f'librecrawl_issues_{int(time.time())}.csv'

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
                links_filename = f'librecrawl_links_{int(time.time())}.csv'
            elif export_format == 'json':
                links_content = generate_links_json_export(links)
                links_mimetype = 'application/json'
                links_filename = f'librecrawl_links_{int(time.time())}.json'
            else:
                links_content = generate_links_csv_export(links)
                links_mimetype = 'text/csv'
                links_filename = f'librecrawl_links_{int(time.time())}.csv'

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
                regular_filename = f'librecrawl_export_{int(time.time())}.csv'
            elif export_format == 'json':
                regular_content = generate_json_export(urls, regular_fields)
                regular_mimetype = 'application/json'
                regular_filename = f'librecrawl_export_{int(time.time())}.json'
            elif export_format == 'xml':
                regular_content = generate_xml_export(urls, regular_fields)
                regular_mimetype = 'application/xml'
                regular_filename = f'librecrawl_export_{int(time.time())}.xml'
            else:
                return jsonify({'success': False, 'error': 'Unsupported export format'})

            files_to_export.append({
                'content': regular_content,
                'mimetype': regular_mimetype,
                'filename': regular_filename
            })

        # Handle special case where only special fields are selected but no data
        if not files_to_export:
            if has_issues_export and not issues:
                return jsonify({'success': False, 'error': 'No issues data to export'})
            elif has_links_export and not links:
                return jsonify({'success': False, 'error': 'No links data to export'})
            else:
                return jsonify({'success': False, 'error': 'No data to export'})

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
                        crawler._save_queue_checkpoint()
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
                    crawler._save_queue_checkpoint()
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
    print("LibreCrawl - SEO Spider")
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
    print("Starting LibreCrawl on http://localhost:5000")
    print("Using Waitress WSGI server with multi-threading support")
    serve(app, host='0.0.0.0', port=5000, threads=8)

if __name__ == '__main__':
    main()
