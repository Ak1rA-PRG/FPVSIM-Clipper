import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QComboBox
import fpvsim_clipper_qt as ui


app = QApplication.instance() or QApplication([])


class SelectorHarness:
    add_saved_clip = ui.Window.add_saved_clip
    persist_recent_clip = ui.Window.persist_recent_clip
    load_recent_clips = ui.Window.load_recent_clips

    def __init__(self, history=None):
        self.saved_selector = QComboBox()
        self.store = SimpleNamespace(data={'recent_clips': history or []}, save=lambda: None)
        self.selected = None
        self.rows = []

    def select_saved_clip(self, index):
        self.selected = self.saved_selector.itemData(index)

    def add_row(self, *args):
        self.rows.append(args)

    def paths(self):
        return [self.saved_selector.itemData(i)['path'] for i in range(self.saved_selector.count())]


def entry(n):
    return {'path': f'clip{n}.mp4', 'track': 'Course', 'kind': '最速3周', 'seconds': float(n)}


class ShareOrderTests(unittest.TestCase):
    def test_newest_first_and_updated_item_does_not_duplicate(self):
        w = SelectorHarness()
        for n in range(1, 4):
            item = entry(n)
            w.add_saved_clip(item['path'], item['track'], item['kind'], item['seconds'])
        self.assertEqual(w.paths(), ['clip3.mp4', 'clip2.mp4', 'clip1.mp4'])
        self.assertEqual(w.selected['path'], 'clip3.mp4')
        w.add_saved_clip('clip2.mp4', 'Edited', '最速3周', 9.0)
        self.assertEqual(w.paths(), ['clip3.mp4', 'clip2.mp4', 'clip1.mp4'])
        self.assertEqual(w.selected['track'], 'Edited')

    def test_restart_restores_latest_five_newest_first(self):
        w = SelectorHarness([entry(n) for n in range(1, 8)])
        with patch.object(Path, 'is_file', return_value=True), patch.object(ui, 'read_metadata'):
            w.load_recent_clips()
        self.assertEqual(w.paths(), [f'clip{n}.mp4' for n in (7, 6, 5, 4, 3)])
        self.assertEqual(w.selected['path'], 'clip7.mp4')
        self.assertEqual(len(w.store.data['recent_clips']), 7)

    def test_edit_does_not_change_history_order_even_if_renamed(self):
        w = SelectorHarness([entry(n) for n in range(1, 4)])
        with patch.object(Path, 'is_file', return_value=True):
            w.persist_recent_clip('renamed.mp4', 'Edited', '最速1周', 9.0, previous_path='clip1.mp4')
        self.assertEqual([i['path'] for i in w.store.data['recent_clips']],
                         ['renamed.mp4', 'clip2.mp4', 'clip3.mp4'])


if __name__ == '__main__':
    unittest.main()
