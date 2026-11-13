"""User-facing navigation, extraction and settings regressions."""
from pathlib import Path
import unittest
from unittest.mock import patch

import test_desktop as fixtures


class ProductFlowTests(unittest.TestCase):
    def setUp(self):
        fixtures.DesktopTests.setUpClass()
        self.fixture = fixtures.DesktopTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.app()

    def test_extract_choice_and_primary_action_are_consistent(self):
        from ui_dialogs import NewVideoDialog
        dialog = NewVideoDialog(self.window, self.window.preferences)
        dialog.link.setPlainText('BV1k5np69EKv')
        dialog.extract_choice.setChecked(True)
        self.assertFalse(dialog.values()['auto_summary'])
        self.assertEqual(dialog.start_button.text(), '提取正文')
        dialog.summary_choice.setChecked(True)
        self.assertTrue(dialog.values()['auto_summary'])
        self.assertEqual(dialog.start_button.text(), '开始整理')
        dialog.reject()

    def test_model_shortcut_opens_model_tab(self):
        from ui_dialogs import SettingsDialog
        seen = []
        with patch.object(SettingsDialog, 'exec', lambda dialog: seen.append(dialog.tabs.currentIndex())):
            self.window.open_model_settings()
        self.assertEqual(seen, [1])

    def test_done_saves_pending_model_and_general_preferences(self):
        from ui_dialogs import SettingsDialog
        dialog = SettingsDialog(self.window, self.window.preferences)
        dialog.model_panel.model_box.setCurrentText('test-model')
        value = dialog.model_panel.selected()
        with patch('model_dialog.save_settings', return_value=value) as save:
            dialog.finish()
        save.assert_called_once_with(value)
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)

    def test_invalid_model_keeps_settings_dialog_open(self):
        from ui_dialogs import SettingsDialog
        dialog = SettingsDialog(self.window, self.window.preferences)
        dialog.model_panel.model_box.setCurrentText('invalid model name')
        with patch.object(self.window, 'save_ui_preferences') as save:
            dialog.finish()
        save.assert_not_called()
        self.assertEqual(dialog.tabs.currentIndex(), 1)
        self.assertNotEqual(dialog.result(), dialog.DialogCode.Accepted)
        dialog.reject()

    def test_extracted_note_has_direct_generate_action_and_no_empty_copy(self):
        result = self.fixture.create_result('仅提取正文', 'BV1k5np69EKv')
        Path(result['summary_path']).unlink()
        self.window.open_result_path(Path(result['folder']) / 'video.json')
        self.assertFalse(self.window.generate_button.isHidden())
        self.assertTrue(self.window.generate_button.isEnabled())
        self.assertFalse(self.window.copy_button.isEnabled())
        self.window.tabs.setCurrentIndex(1)
        self.assertTrue(self.window.copy_button.isEnabled())
