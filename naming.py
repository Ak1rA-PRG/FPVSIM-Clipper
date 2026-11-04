"""Portable, single-component output names and clip identity metadata."""
import json
import re
import hashlib
import os
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from string import Formatter

DEFAULT_FILE_TEMPLATE = '{course}_{kind}_{record}s_{year}-{month}-{date}_{time}'
DEFAULT_FOLDER_TEMPLATE = '{course}'
TOKENS = {'course', 'kind', 'Kind', 'record', 'year', 'month', 'date', 'time', 'datetime', 'full_date', 'aircraft'}
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
METADATA_DIR = APP_DIR / 'fpvsim_clipper_internal' / 'clip_metadata'
_metadata_lock = threading.RLock()

def validate_name(value):
    invalid = re.findall(r'[<>:"/\\|?*\x00-\x1f]', value)
    if invalid:
        chars = ' '.join(sorted(set(repr(char) for char in invalid)))
        raise ValueError(f'名前に使えない文字があります：{chars}。命名ルールを修正してください。')
    if not value or value in ('.', '..') or value.endswith((' ', '.')):
        raise ValueError('空の名前や、末尾が空白・ピリオドの名前は使えません。命名ルールを修正してください。')
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', value, re.I):
        raise ValueError(f'「{value}」はWindowsで予約された名前です。別の名前に変更してください。')
    if len(value.encode('utf-16-le')) // 2 > 180:
        raise ValueError('名前が長すぎます。180文字以内になるよう命名ルールを短くしてください。')
    return value

def render_name(template, course, kind, seconds, saved_at=None, pending=False, aircraft=''):
    saved_at = saved_at or datetime.now()
    values = {'course': course, 'kind': kind, 'Kind': kind, 'aircraft': aircraft or '未取得',
              'record': 'TIME_INPUT_REQUIRED' if pending else f'{seconds:.3f}',
              'year': saved_at.strftime('%Y'), 'month': saved_at.strftime('%m'),
              'date': saved_at.strftime('%d'), 'time': saved_at.strftime('%H-%M-%S'),
              'datetime': saved_at.strftime('%Y-%m-%d_%H-%M-%S'),
              'full_date': saved_at.strftime('%Y-%m-%d')}
    try:
        for literal, field, spec, conversion in Formatter().parse(template):
            if field is not None and (field not in TOKENS or spec or conversion):
                raise ValueError(f'使えない差し込み項目です：{{{field}}}。表示されている項目を使ってください。')
        result = template.format_map(values)
    except (KeyError, ValueError) as exc:
        raise ValueError(f'命名ルールを修正してください：{exc}') from exc
    return validate_name(result)

def metadata_path(path):
    """Keep per-video records inside the app, not alongside exported videos."""
    identity = os.path.normcase(str(Path(path).resolve()))
    return METADATA_DIR / (hashlib.sha256(identity.encode('utf-8')).hexdigest() + '.json')

def legacy_metadata_path(path):
    path = Path(path)
    return path.with_name(path.name + '.fpvsim_clipper.json')

def _read_metadata_file(sidecar):
    try:
        if sidecar.stat().st_size > 16384:
            return {}
        data = json.loads(sidecar.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) and data.get('fpvsim_clipper_metadata') == 1 else {}
    except (OSError, ValueError):
        return {}

def _store_metadata(path, data):
    target = metadata_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

def read_metadata(path):
    with _metadata_lock:
        data = _read_metadata_file(metadata_path(path))
        legacy = legacy_metadata_path(path)
        old = _read_metadata_file(legacy)
        if old:
            try:
                # Remove only our own sidecar, and only after a successful copy.
                _store_metadata(path, data or old)
                legacy.unlink()
            except OSError:
                pass
        return data or old

def delete_metadata(path):
    with _metadata_lock:
        metadata_path(path).unlink(missing_ok=True)
        legacy = legacy_metadata_path(path)
        if _read_metadata_file(legacy):
            legacy.unlink(missing_ok=True)

def write_metadata(path, course, kind, seconds, saved_at, template='', pending=False, aircraft=''):
    data = {'fpvsim_clipper_metadata': 1, 'course': course, 'kind': kind, 'seconds': seconds,
            'saved_at': saved_at.isoformat(), 'template': template, 'pending': pending, 'aircraft': aircraft}
    with _metadata_lock:
        _store_metadata(path, data)
        legacy = legacy_metadata_path(path)
        if _read_metadata_file(legacy):
            try: legacy.unlink(missing_ok=True)
            except OSError: pass
