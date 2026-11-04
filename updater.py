"""GitHub Releases update check and verified Windows self-update."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid

CURRENT_VERSION = '1.0.0'
DEFAULT_REPOSITORY = 'Ak1rA-PRG/FPVSIM-Clipper'
ASSET_NAME = 'FPVSIM-Clipper-Windows.exe'
MAX_DOWNLOAD = 400 * 1024 * 1024


def supports_self_update(executable, platform_name, frozen):
    """EXE-only updates must never be applied to a folder-based distribution."""
    executable = Path(executable).resolve()
    return (platform_name == 'win32' and frozen and
            executable.name.lower() == 'fpvsim-clipper.exe' and
            not (executable.parent / '_internal').is_dir())


def allowed_download(url):
    address = urllib.parse.urlsplit(url)
    host = (address.hostname or '').lower()
    return address.scheme == 'https' and (host == 'github.com' or host.endswith('.githubusercontent.com'))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_download(newurl):
            raise ValueError('更新ファイルの取得先が不正です。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def repository(value):
    value = value.strip().rstrip('/')
    if value.lower().startswith('https://'):
        url = urllib.parse.urlsplit(value)
        if url.netloc.lower() != 'github.com' or url.query or url.fragment:
            raise ValueError('GitHubリポジトリのURLを入力してください。')
        value = url.path.strip('/')
    if value.endswith('.git'):
        value = value[:-4]
    if not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', value) or value.split('/')[1] in ('.', '..'):
        raise ValueError('GitHubリポジトリを「所有者/リポジトリ名」で入力してください。')
    return value


def version(value):
    match = re.fullmatch(r'[vV]?(\d{1,6})(?:\.(\d{1,6}))?(?:\.(\d{1,6}))?', value)
    if not match:
        raise ValueError('リリースのバージョンは v1.0.0 の形式にしてください。')
    return tuple(int(item or 0) for item in match.groups())


def check_latest():
    repo = repository(DEFAULT_REPOSITORY)
    request = urllib.request.Request('https://api.github.com/repos/' + repo + '/releases/latest',
        headers={'Accept':'application/vnd.github+json', 'User-Agent':'FPVSIM-Clipper-updater',
                 'X-GitHub-Api-Version':'2022-11-28'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError('更新情報が見つかりません。GitHubリポジトリの公開状態と正式なリリースを確認してください。') from exc
        raise
    if len(data) > 1024 * 1024:
        raise ValueError('更新情報が大きすぎます。')
    release = json.loads(data)
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('正式公開されたリリースを指定してください。')
    tag = release.get('tag_name', '')
    newer = version(tag) > version(CURRENT_VERSION)
    page = release.get('html_url', '')
    url = urllib.parse.urlsplit(page)
    if url.scheme != 'https' or url.netloc.lower() != 'github.com' or not url.path.lower().startswith('/' + repo.lower() + '/releases/tag/'):
        raise ValueError('更新ページのURLが不正です。')
    result = {'tag': tag, 'newer': newer, 'page': page, 'asset': None}
    assets = [asset for asset in release.get('assets', []) if asset.get('name') == ASSET_NAME and asset.get('state') == 'uploaded']
    if len(assets) > 1:
        raise ValueError('更新ファイルが複数あります。')
    if assets:
        asset = assets[0]
        address = urllib.parse.urlsplit(asset.get('browser_download_url', ''))
        if address.scheme != 'https' or address.netloc.lower() != 'github.com' or not address.path.lower().startswith('/' + repo.lower() + '/releases/download/'):
            raise ValueError('更新ファイルのURLが不正です。')
        digest = asset.get('digest') or ''
        size = asset.get('size')
        if re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest) and isinstance(size, int) and 0 < size <= MAX_DOWNLOAD:
            result['asset'] = {'url': asset['browser_download_url'], 'digest': digest[7:].lower(), 'size': size}
    return result


def download_update(asset, app_dir):
    folder = Path(app_dir) / 'fpvsim_clipper_internal' / 'updates' / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    partial = folder / 'download.part'
    target = folder / ASSET_NAME
    digest = hashlib.sha256()
    count = 0
    request = urllib.request.Request(asset['url'], headers={'User-Agent':'FPVSIM-Clipper-updater'})
    try:
        with urllib.request.build_opener(SafeRedirect()).open(request, timeout=30) as response, partial.open('xb') as stream:
            if not allowed_download(response.geturl()):
                raise ValueError('更新ファイルの取得先が不正です。')
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk: break
                count += len(chunk)
                if count > asset['size'] or count > MAX_DOWNLOAD:
                    raise ValueError('更新ファイルのサイズが一致しません。')
                digest.update(chunk)
                stream.write(chunk)
        if count != asset['size'] or digest.hexdigest() != asset['digest']:
            raise ValueError('更新ファイルの検証に失敗しました。')
        with partial.open('rb') as stream:
            if stream.read(2) != b'MZ':
                raise ValueError('Windowsアプリとして読み込めません。')
        partial.replace(target)
        return target
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def launch_installer(staged, target):
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    subprocess.Popen([str(staged), '--apply-update', str(target), str(os.getpid())],
                     close_fds=True, creationflags=flags)


def apply_update(target, parent_pid):
    """Run from the verified staged EXE after the existing application exits."""
    import ctypes
    from ctypes import wintypes
    target = Path(target).resolve()
    staged = Path(sys.executable).resolve()
    allowed = (target.parent / 'fpvsim_clipper_internal' / 'updates').resolve()
    if target.name.lower() != 'fpvsim-clipper.exe' or staged.name != ASSET_NAME or not staged.is_relative_to(allowed):
        raise ValueError('更新先を確認できません。')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, int(parent_pid))
    if handle:
        try:
            if kernel.WaitForSingleObject(handle, 60000) != 0:
                raise RuntimeError('FPVSIM-Clipperの終了を確認できませんでした。')
        finally:
            kernel.CloseHandle(handle)
    elif ctypes.get_last_error() != 87:  # ERROR_INVALID_PARAMETER: process has already exited.
        raise OSError(ctypes.get_last_error(), 'FPVSIM-Clipperの終了を確認できませんでした。')
    backup = staged.parent / ('FPVSIM-Clipper-before-update-' + uuid.uuid4().hex + '.exe')
    temporary = target.with_name('FPVSIM-Clipper-update-' + uuid.uuid4().hex + '.tmp')
    shutil.copy2(staged, temporary)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                target.rename(backup)
                break
            except PermissionError:
                if time.monotonic() >= deadline: raise
                time.sleep(0.25)
        try:
            temporary.replace(target)
        except Exception:
            backup.rename(target)
            raise
    finally:
        temporary.unlink(missing_ok=True)
    subprocess.Popen([str(target)], close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
