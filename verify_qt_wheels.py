"""Verify bundled Qt/PySide/Shiboken files against the two official wheels."""
import hashlib
from pathlib import Path
import sys
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main(bundle, pyside_wheel, shiboken_wheel):
    internal = Path(bundle) / '_internal'
    checked = []
    with zipfile.ZipFile(pyside_wheel) as pyside, zipfile.ZipFile(shiboken_wheel) as shiboken:
        for path in internal.rglob('*'):
            if not path.is_file():
                continue
            rel = path.relative_to(internal).as_posix()
            if rel.startswith('PySide6/'):
                wheel, candidate = pyside, rel
            elif rel.startswith('shiboken6/'):
                wheel, candidate = shiboken, rel
            elif path.name.startswith('Qt6') and path.suffix.lower() == '.dll':
                wheel, candidate = pyside, 'PySide6/' + path.name
            elif path.name == 'pyside6.abi3.dll':
                wheel, candidate = pyside, 'PySide6/' + path.name
            elif path.name == 'shiboken6.abi3.dll':
                wheel, candidate = shiboken, 'shiboken6/' + path.name
            else:
                continue
            if candidate not in wheel.namelist():
                raise SystemExit(f'Not found in expected wheel: {rel} -> {candidate}')
            if digest(path.read_bytes()) != digest(wheel.read(candidate)):
                raise SystemExit(f'Wheel file differs: {rel}')
            checked.append(rel)
    if not checked:
        raise SystemExit('No Qt files checked')
    print(f'OK: {len(checked)} Qt / PySide / Shiboken files match the supplied wheels')


if __name__ == '__main__':
    main(*sys.argv[1:])
