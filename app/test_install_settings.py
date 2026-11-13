from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from install_settings import configure_output, writable_folder
from note_library import load_preferences, save_preferences


class InstallSettingsTests(unittest.TestCase):
    def test_creates_chinese_path_and_preserves_other_preferences_and_old_notes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            settings = root / 'settings.json'
            old = root / 'old.md'
            old.write_text('existing note', encoding='utf-8')
            save_preferences({'output_dir': str(root), 'concurrency': 5}, settings)
            target = root / '中文 空格/生成笔记'
            configure_output(str(target), settings)
            self.assertTrue(target.is_dir())
            self.assertEqual(list(target.iterdir()), [])
            self.assertEqual(load_preferences(settings)['output_dir'], str(target))
            self.assertEqual(load_preferences(settings)['concurrency'], 5)
            self.assertEqual(old.read_text(encoding='utf-8'), 'existing note')

    def test_denied_write_does_not_replace_saved_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            settings = root / 'settings.json'
            save_preferences({'output_dir': str(root)}, settings)
            original = settings.read_bytes()
            with patch('install_settings.tempfile.TemporaryFile', side_effect=PermissionError('denied')):
                with self.assertRaises(ValueError):
                    configure_output(str(root / 'new'), settings)
            self.assertEqual(settings.read_bytes(), original)

    def test_empty_relative_and_existing_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary).resolve() / 'file.txt'
            file.write_text('keep', encoding='utf-8')
            for value in ('', 'relative/folder', str(file)):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    writable_folder(value)
            self.assertEqual(file.read_text(encoding='utf-8'), 'keep')
