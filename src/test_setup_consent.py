import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
import setup_ui
from setup_ui import ConsentPage


class ConsentPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_both_confirmations_required_and_documents_readable(self):
        page = ConsentPage()
        self.assertTrue(page.documents_available)
        for index in range(page.documents.count()):
            page.documents.setCurrentIndex(index)
            self.assertTrue(page.text.toPlainText().strip())
            self.assertNotIn('文書が見つかりません', page.text.toPlainText())
        self.assertFalse(page.isComplete())
        page.license_consent.setChecked(True)
        self.assertFalse(page.isComplete())
        page.clip_consent.setChecked(True)
        self.assertTrue(page.isComplete())
        page.license_consent.setChecked(False)
        self.assertFalse(page.isComplete())

    def test_missing_documents_blocks_completion(self):
        page = ConsentPage()
        page.documents_available = False
        page.license_consent.setChecked(True)
        page.clip_consent.setChecked(True)
        self.assertFalse(page.isComplete())

    def test_packaged_layout_and_missing_file(self):
        names = ['LICENSE', 'THIRD_PARTY_NOTICES.md', 'MICROSOFT_REDISTRIBUTABLE_NOTICE.md',
                 'LICENSES/Python-License.txt', 'LICENSES/LGPL-3.0.txt']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / 'FPVSIM-Clipper' / 'FPVSIM-Clipper.exe'
            module = executable.parent / '_internal' / 'setup_ui.py'
            module.parent.mkdir(parents=True)
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('test: ' + name, encoding='utf-8')
            with patch.object(setup_ui, '__file__', str(module)), \
                    patch.object(setup_ui.sys, 'frozen', True, create=True), \
                    patch.object(setup_ui.sys, 'executable', str(executable)):
                page = ConsentPage()
                self.assertTrue(page.documents_available)
                for index, name in enumerate(names):
                    page.documents.setCurrentIndex(index)
                    self.assertEqual(page.text.toPlainText(), 'test: ' + name)
                (root / names[-1]).unlink()
                incomplete = ConsentPage()
                incomplete.license_consent.setChecked(True)
                incomplete.clip_consent.setChecked(True)
                self.assertFalse(incomplete.documents_available)
                self.assertFalse(incomplete.isComplete())


if __name__ == '__main__':
    unittest.main()
