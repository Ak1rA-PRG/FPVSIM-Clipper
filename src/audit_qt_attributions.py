"""List upstream Qt attribution files whose referenced license files are absent."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent / 'LICENSES' / 'qt-upstream-6.10.3'
missing = []
count = 0
for path in root.rglob('qt_attribution.json'):
    with path.open(encoding='utf-8-sig') as stream:
        data = json.load(stream, strict=False)
    for entry in data if isinstance(data, list) else [data]:
        count += 1
        names = entry.get('LicenseFiles', [])
        if entry.get('LicenseFile'):
            names = [*names, entry['LicenseFile']]
        if entry.get('CopyrightFile'):
            names = [*names, entry['CopyrightFile']]
        for name in names:
            if not (path.parent / name).is_file():
                missing.append((path.relative_to(root), name))
print(f'Attributions: {count}; missing referenced files: {len(missing)}')
for path, name in missing:
    print(f'{path}: {name}')
raise SystemExit(bool(missing))
