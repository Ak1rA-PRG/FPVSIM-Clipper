"""Compare the candidate's collected Qt notices with its source archives.

Verifying the files does not decide which components or license terms apply.
"""
from pathlib import Path
import hashlib
import sys
import tarfile


ARCHIVES = {
    'pyside-6.10.3.tar.xz': 'pyside-setup-everywhere-src-6.10.3',
    'qtbase-6.10.3.tar.xz': 'qtbase-everywhere-src-6.10.3',
    'qtsvg-6.10.3.tar.xz': 'qtsvg-everywhere-src-6.10.3',
    'qtimageformats-6.10.3.tar.xz': 'qtimageformats-everywhere-src-6.10.3',
}


def main(root: Path) -> int:
    notices = root / 'LICENSES' / 'qt-upstream-6.10.3'
    errors = []
    copied = {path.relative_to(notices).as_posix() for path in notices.rglob('*') if path.is_file()}
    if not copied:
        raise SystemExit('No collected Qt notice files found')
    matched = set()
    attribution_files = 0
    for archive, source_root in ARCHIVES.items():
        with tarfile.open(root / 'UPSTREAM-SOURCE' / archive, 'r:xz') as tar:
            for member in tar:
                if not member.isfile() or not member.name.startswith(source_root + '/'):
                    continue
                rel = member.name
                if rel.endswith('/qt_attribution.json'):
                    attribution_files += 1
                    if rel not in copied:
                        errors.append(f'Missing attribution: {rel}')
                if rel not in copied:
                    continue
                stream = tar.extractfile(member)
                with (notices / rel).open('rb') as local:
                    if hashlib.file_digest(stream, 'sha256').digest() != hashlib.file_digest(local, 'sha256').digest():
                        errors.append(f'Notice differs from source: {rel}')
                matched.add(rel)
    for rel in sorted(copied - matched):
        errors.append(f'No corresponding archive file: {rel}')
    for error in errors:
        print(error)
    print(f'Checked {len(matched)} notice files and {attribution_files} attribution files; errors: {len(errors)}')
    return bool(errors)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python verify_qt_notice_sources.py CANDIDATE_DIRECTORY')
    raise SystemExit(main(Path(sys.argv[1])))
