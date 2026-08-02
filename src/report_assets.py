"""Private, sanitised assets used by white-label report rendering."""

import base64
import io
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / 'data' / 'report-assets'
MAX_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 4096
ALLOWED_FORMATS = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}


def sanitise_logo(file_storage, user_id):
    """Validate and re-encode a logo, returning path, MIME and original name."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError('Pillow is required to upload report logos') from exc

    if not file_storage or not getattr(file_storage, 'filename', ''):
        raise ValueError('Logo file is required')
    raw = file_storage.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Logo must be 2 MB or smaller')
    try:
        image = Image.open(io.BytesIO(raw))
        image.verify()
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise ValueError('The uploaded logo is not a valid image') from exc
    if image.format not in ALLOWED_FORMATS:
        raise ValueError('Logo format must be PNG, JPEG or WebP')
    if image.width > MAX_DIMENSION or image.height > MAX_DIMENSION:
        raise ValueError('Logo dimensions must not exceed 4096 pixels')

    if image.mode not in ('RGB', 'RGBA'):
        image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
    output = io.BytesIO()
    image.save(output, format='PNG', optimize=True)
    content = output.getvalue()
    if len(content) > MAX_BYTES:
        raise ValueError('Sanitised logo exceeds 2 MB')

    user_dir = (ASSET_ROOT / str(int(user_id))).resolve()
    user_dir.mkdir(parents=True, exist_ok=True)
    asset_id = os.urandom(16).hex()
    path = (user_dir / f'{asset_id}.png').resolve()
    if user_dir not in path.parents:
        raise ValueError('Invalid asset path')
    path.write_bytes(content)
    return path, 'image/png', str(file_storage.filename)[:200]


def asset_data_uri(asset):
    """Read an owned asset and return a safe data URI for HTML/PDF embedding."""
    if not asset:
        return None
    path = Path(asset.get('file_path', '')).resolve()
    root = ASSET_ROOT.resolve()
    if root not in path.parents or not path.is_file():
        return None
    mime = asset.get('mime_type') or 'image/png'
    if mime not in set(ALLOWED_FORMATS.values()):
        return None
    try:
        content = path.read_bytes()
    except OSError:
        return None
    if len(content) > MAX_BYTES:
        return None
    encoded = base64.b64encode(content).decode('ascii')
    return f'data:{mime};base64,{encoded}'


def remove_asset_file(file_path):
    if not file_path:
        return
    path = Path(file_path).resolve()
    root = ASSET_ROOT.resolve()
    if root in path.parents and path.is_file():
        path.unlink()
