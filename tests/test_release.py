"""Release inputs must agree; installers are allowlisted and checksummed."""
from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
spec = importlib.util.spec_from_file_location('release', Path(__file__).resolve().parents[1] / 'scripts/release.py')
release = importlib.util.module_from_spec(spec); spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_current_version_has_release_notes_and_mismatches_fail(self):
        version = json.loads((release.ROOT / 'desktop/package.json').read_text())['version']
        self.assertIn('Tuya Local v' + version, release.notes(release.ROOT, 'v' + version))
        for tag in ('v999.0.0', 'v1.0', 'main', 'v1.0.0;bad', 'v1.0.0-beta'):
            with self.assertRaises(ValueError): release.notes(release.ROOT, tag)

    def test_installer_checksums_exclude_account_data_and_require_all_platforms(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir()
            for name in ('demo.dmg', 'demo.msi', 'demo.AppImage', 'web-access.json', 'private.jpg'):
                (source / name).write_bytes(b'synthetic')
            release.assets(source, root / 'out')
            self.assertEqual({p.name for p in (root / 'out').iterdir()}, {'demo.dmg', 'demo.msi', 'demo.AppImage', 'SHA256SUMS'})
            checksum = hashlib.sha256(b'synthetic').hexdigest()
            self.assertIn(checksum + '  demo.msi', (root / 'out/SHA256SUMS').read_text())
            (source / 'demo.dmg').unlink()
            with self.assertRaises(ValueError): release.assets(source, root / 'missing')

    def test_names_match_github_downloads_and_checksum_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir()
            for name in ('Tuya Local.dmg', 'Tuya Local.msi', 'Tuya Local.AppImage'):
                (source / name).write_bytes(b'synthetic')
            release.assets(source, root / 'out')
            self.assertEqual({path.name for path in (root / 'out').iterdir()}, {'Tuya.Local.dmg', 'Tuya.Local.msi', 'Tuya.Local.AppImage', 'SHA256SUMS'})
            for line in (root / 'out/SHA256SUMS').read_text().splitlines():
                digest, name = line.split('  ', 1)
                self.assertEqual(hashlib.sha256((root / 'out' / name).read_bytes()).hexdigest(), digest)

    def test_name_normalization_cannot_overwrite_another_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir()
            for name in ('Tuya Local.dmg', 'Tuya.Local.dmg', 'demo.msi', 'demo.AppImage'):
                (source / name).touch()
            with self.assertRaises(ValueError): release.assets(source, root / 'out')

    def test_duplicate_names_or_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir()
            for name in ('demo.dmg', 'demo.msi', 'demo.deb'): (source / name).touch()
            (source / 'nested').mkdir(); (source / 'nested/demo.deb').touch()
            with self.assertRaises(ValueError): release.assets(source, root / 'duplicate')
            (source / 'nested/demo.deb').unlink()
            (source / 'linked.txt').symlink_to(source / 'demo.msi')
            with self.assertRaises(ValueError): release.assets(source, root / 'linked')


if __name__ == '__main__': unittest.main()
