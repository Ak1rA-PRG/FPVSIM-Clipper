"""Verify every bundled PyWinRT extension against exactly one supplied wheel."""
import hashlib
from pathlib import Path
import sys
import zipfile

bundle = Path(sys.argv[1])
wheel_dir = Path(sys.argv[2])
root = bundle / '_internal'
wheel_paths = sorted(wheel_dir.glob('winrt_*.whl'))
if not wheel_paths:
    raise SystemExit('No PyWinRT wheels found')

with __import__('contextlib').ExitStack() as stack:
    wheels = [stack.enter_context(zipfile.ZipFile(path)) for path in wheel_paths]
    checked = []
    for path in (root / 'winrt').rglob('*.pyd'):
        relative = path.relative_to(root).as_posix()
        candidates = [wheel for wheel in wheels if relative in wheel.namelist()]
        if len(candidates) != 1:
            raise SystemExit(f'Expected exactly one wheel for {relative}, got {len(candidates)}')
        if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(candidates[0].read(relative)).digest():
            raise SystemExit(f'PyWinRT extension differs from the wheel: {relative}')
        checked.append(relative)
    if not checked:
        raise SystemExit('No bundled PyWinRT extensions found')
    print(f'OK: {len(checked)} PyWinRT extensions match the supplied wheels')
