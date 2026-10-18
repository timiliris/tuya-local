"""Validate a release tag and prepare public notes/allowlisted native assets."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import tomllib

ROOT = Path(__file__).resolve().parents[1]
ASSET_SUFFIXES = {'.dmg', '.msi', '.exe', '.deb', '.rpm', '.AppImage'}


def notes(root, tag):
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Use a stable version tag such as v0.5.0')
    version = tag[1:]
    versions = [json.loads((root / name).read_text())['version'] for name in (
        'desktop/package.json', 'desktop/package-lock.json', 'desktop/src-tauri/tauri.conf.json')]
    versions.append(json.loads((root / 'desktop/package-lock.json').read_text())['packages']['']['version'])
    versions.append(tomllib.loads((root / 'desktop/src-tauri/Cargo.toml').read_text())['package']['version'])
    lock = tomllib.loads((root / 'desktop/src-tauri/Cargo.lock').read_text())
    versions.extend(package['version'] for package in lock['package'] if package['name'] == 'tuya-local-desktop')
    if not versions or any(item != version for item in versions):
        raise ValueError('The tag and all desktop versions must match')
    changelog = (root / 'CHANGELOG.md').read_text()
    match = re.search(r'^## \[' + re.escape(version) + r'\] - (\d{4}-\d{2}-\d{2})\n(.*?)(?=^## |\Z)', changelog, re.M | re.S)
    if not match or not match[2].strip() or 'TODO' in match[2]:
        raise ValueError('Add a dated, completed changelog entry before tagging')
    return f'# Tuya Local {tag}\n\n{match[2].strip()}\n\nDesktop installers are currently unsigned. SHA256SUMS verifies downloaded files; it does not replace code signing.\n'


def assets(source, destination):
    files = sorted(path for path in source.rglob('*') if path.is_file() and path.suffix in ASSET_SUFFIXES)
    if any(path.is_symlink() for path in source.rglob('*')):
        raise ValueError('Symlinks are not release assets')
    if not all(any(path.suffix in group for path in files) for group in ({'.dmg'}, {'.msi', '.exe'}, {'.deb', '.rpm', '.AppImage'})):
        raise ValueError('A native installer is required for each of macOS, Windows and Linux')
    names = {path: re.sub(r'[^A-Za-z0-9_.-]', '.', path.name) for path in files}
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) for name in names.values()):
        raise ValueError('Invalid installer name')
    if len(set(names.values())) != len(files):
        raise ValueError('Duplicate installer names after normalization')
    destination.mkdir(parents=True, exist_ok=False)
    checksums = []
    for path in files:
        target = destination / names[path]
        shutil.copyfile(path, target)
        with target.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        checksums.append(f'{digest}  {target.name}\n')
    (destination / 'SHA256SUMS').write_text(''.join(checksums))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('notes', 'assets'))
    parser.add_argument('value')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'notes':
            args.output.write_text(notes(ROOT, args.value))
        else:
            assets(Path(args.value), args.output)
    except ValueError as error:
        parser.exit(1, str(error) + '\n')
