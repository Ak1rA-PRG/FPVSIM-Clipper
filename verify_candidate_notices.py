"""Check selected notice references and Qt source hashes in a review folder.

This is an integrity check, not an assessment of license compliance.
"""
import hashlib
from pathlib import Path
import re
import sys


def main(root: Path) -> int:
    notices = (root / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
    errors = []
    references = set(re.findall(r'`(LICENSES/[^`]+)`', notices))
    for name in sorted(references):
        if not (root / name).exists():
            errors.append(f'Missing notice reference: {name}')

    archives = {
        'pyside-6.10.3.tar.xz': '2c7462fe0cecb5b8ac0a3d92014b8d0b88bd4d9f8646709dab5286d9416f45bc',
        'qtbase-6.10.3.tar.xz': '383dc907816338f0cba72088a524c07458dfc69ce684ca9132fcc4fe91c24b0b',
        'qtsvg-6.10.3.tar.xz': 'b3223fe005f6a4c7f21f34e4ee6ce0094737cff9503ba15bbb171ac18794f76d',
        'qtimageformats-6.10.3.tar.xz': '84605dd91037482b5b7c7ecc5c27aee8acc1cd7f1fe77bc564777ddf365d7d28',
    }
    for name, expected in archives.items():
        path = root / 'UPSTREAM-SOURCE' / name
        if not path.is_file():
            errors.append(f'Missing source archive: {name}')
            continue
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected or expected not in notices:
            errors.append(f'Incorrect archive hash or notice: {name}')

    for error in errors:
        print(error)
    print(f'Checked {len(references)} notice references and {len(archives)} source archives; errors: {len(errors)}')
    return bool(errors)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python verify_candidate_notices.py CANDIDATE_DIRECTORY')
    raise SystemExit(main(Path(sys.argv[1])))
