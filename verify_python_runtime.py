"""Check collected Python files against an official embeddable Python ZIP."""
import hashlib
from pathlib import Path
import sys
import zipfile

bundle, archive = map(Path, sys.argv[1:])
root = bundle / '_internal'
checked = []
with zipfile.ZipFile(archive) as source:
    names = set(source.namelist())
    for path in root.iterdir():
        if path.is_file() and path.name in names and path.suffix.lower() in ('.dll', '.pyd'):
            if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(source.read(path.name)).digest():
                raise SystemExit(f'Runtime file differs from official Python ZIP: {path.name}')
            checked.append(path.name)
required = {'python314.dll', 'python3.dll', '_ssl.pyd', 'libcrypto-3.dll', 'libssl-3.dll'}
if not required.issubset(checked):
    raise SystemExit(f'Missing expected runtime files: {required - set(checked)}')
print(f'OK: {len(checked)} Python runtime files match the official ZIP')
