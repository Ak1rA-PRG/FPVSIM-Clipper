# PyInstaller onedir build; run with the pinned project environment activated.
from pathlib import Path
import os
import sys
from PyInstaller.utils.hooks import collect_submodules

project = Path(SPECPATH)
source = project / 'src'
a = Analysis(
    [str(source / 'fpvsim_clipper_qt.py')],
    pathex=[str(source)],
    binaries=[],
    datas=[(str(source / 'assets' / 'app-icon.png'), 'assets')],
    hiddenimports=collect_submodules('winrt'),
    hookspath=[str(source / 'pyinstaller_hooks')], hooksconfig={}, runtime_hooks=[],
    excludes=['imageio_ffmpeg', 'av', 'PySide6.QtMultimedia',
              'AVFoundation', 'CoreMedia', 'Foundation'],
    noarchive=False, optimize=0,
)

# The app does not install a QTranslator. Avoid bundling unrelated Qt
# translation catalogs; they are a separate upstream component.
a.datas = [(name, source, kind) for name, source, kind in a.datas
           if not name.lower().replace('/', '\\').startswith('pyside6\\translations\\')]

# Avoid silently bundling OpenSSL DLLs from unrelated applications on PATH.
# The intended versions must match the Python interpreter used for the build.
python_root = Path(sys.base_prefix)
python_dlls = python_root / 'DLLs' if (python_root / 'DLLs').is_dir() else python_root
openssl_names = ({'libcrypto-3-x64.dll', 'libssl-3-x64.dll'}
                 if (python_dlls / 'libcrypto-3-x64.dll').is_file()
                 else {'libcrypto-3.dll', 'libssl-3.dll'})
for name in openssl_names:
    if not (python_dlls / name).is_file():
        raise SystemExit(f'Missing Python OpenSSL dependency: {python_dlls / name}')
found_openssl = {name.lower() for name, _, _ in a.binaries} & openssl_names
if found_openssl != openssl_names:
    raise SystemExit(f'Unexpected OpenSSL dependencies in build: {found_openssl}')
a.binaries = [
    (name, str(python_dlls / name), kind)
    if name.lower() in openssl_names else (name, source, kind)
    for name, source, kind in a.binaries
]

# Supported Windows 10/11 releases provide UCRT, API-set DLLs and Windows ICU.
# Qt uses Schannel; do not collect unrelated OpenSSL or ICU builds from PATH.
excluded_names = {'qopensslbackend.dll', 'libcrypto-3-x64.dll', 'libssl-3-x64.dll',
                  'ucrtbase.dll', 'opengl32sw.dll', 'icuuc.dll', 'icuin.dll', 'icu.dll'}
a.binaries = [
    (name, source, kind) for name, source, kind in a.binaries
    if Path(name).name.lower() not in excluded_names
    and not Path(name).name.lower().startswith(('api-ms-win-', 'ext-ms-win-'))
    and not (Path(name).name.lower().startswith('icudt') and Path(name).name.lower().endswith('.dll'))
]

# Only use the publisher's licensed, unmodified Visual Studio redistributables.
redist = Path(os.environ.get('FPVSIM_VC_REDIST', ''))
crt_names = {'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
             'vcruntime140.dll', 'vcruntime140_1.dll'}
if not os.environ.get('FPVSIM_VC_REDIST') or not redist.is_dir():
    raise SystemExit('Set FPVSIM_VC_REDIST to your licensed Visual Studio x64 CRT redist folder')
for name in crt_names:
    if not (redist / name).is_file():
        raise SystemExit(f'Missing Visual Studio redistributable: {redist / name}')
found_crt = {Path(name).name.lower() for name, _, _ in a.binaries} & crt_names
if found_crt != crt_names:
    raise SystemExit(f'Unexpected Visual Studio runtime dependencies: {found_crt}')
a.binaries = [
    (name, str(redist / Path(name).name.lower()), kind)
    if Path(name).name.lower() in crt_names else (name, source, kind)
    for name, source, kind in a.binaries
]
unexpected = [source for _, source, _ in a.binaries if 'codex-runtimes' in source.lower()]
if unexpected:
    raise SystemExit(f'Unexpected development-runtime binaries: {unexpected}')

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='FPVSIM-Clipper',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(source / 'assets' / 'app-icon.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='FPVSIM-Clipper')
