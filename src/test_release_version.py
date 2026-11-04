import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import updater


class ReleaseVersionTests(unittest.TestCase):
    def test_folder_build_never_uses_exe_only_updater(self):
        executable = Path('FPVSIM-Clipper.exe')
        with patch.object(Path, 'is_dir', return_value=False):
            self.assertTrue(updater.supports_self_update(executable, 'win32', True))
        with patch.object(Path, 'is_dir', return_value=True):
            self.assertFalse(updater.supports_self_update(executable, 'win32', True))
            self.assertFalse(updater.supports_self_update(executable, 'linux', True))
            self.assertFalse(updater.supports_self_update(executable, 'win32', False))

    def test_current_and_comparison(self):
        self.assertEqual(updater.CURRENT_VERSION, '1.0.0')
        self.assertEqual(updater.version('v1.0.0'), (1, 0, 0))
        self.assertGreater(updater.version('v1.0.1'), updater.version(updater.CURRENT_VERSION))
        self.assertLess(updater.version('v1.0.0'), updater.version('32.0.3'))

    def test_release_is_not_its_own_update(self):
        repo = updater.DEFAULT_REPOSITORY
        release = {'tag_name': 'v1.0.0', 'draft': False, 'prerelease': False,
                   'html_url': f'https://github.com/{repo}/releases/tag/v1.0.0', 'assets': []}
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(release).encode())):
            self.assertFalse(updater.check_latest()['newer'])
        release['tag_name'] = 'v1.0.1'
        release['html_url'] = f'https://github.com/{repo}/releases/tag/v1.0.1'
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(release).encode())):
            self.assertTrue(updater.check_latest()['newer'])


if __name__ == '__main__':
    unittest.main()
