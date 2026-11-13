import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QPoint
from note_library import load_preferences, save_preferences
from visual_themes import THEMES, clean
import test_desktop as fixtures


class ThemeTests(unittest.TestCase):
    def setUp(self):
        fixtures.DesktopTests.setUpClass()
        self.fixture = fixtures.DesktopTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.qt = fixtures.DesktopTests.qt

    def test_fresh_defaults_and_legacy_migration_roundtrip(self):
        path = self.fixture.root / 'old.json'
        self.assertEqual(load_preferences(path)['theme_id'], 'paper')
        old = dict(reading_background='#263139', reading_font_size=21, background_image='local-photo.png', match_app_background=True)
        path.write_text(json.dumps(old))
        p = load_preferences(path)
        self.assertEqual(p['theme_id'], 'legacy')
        for key, value in old.items():
            self.assertEqual(p[key], value)
            self.assertEqual(p['custom_themes']['我的原有外观'][key], value)
        save_preferences(p, path)
        self.assertEqual(load_preferences(path), p)

    def test_preview_cancel_never_persists_and_preserves_layout(self):
        self.fixture.create_result('主题测试', 'BV1k5np69EKv')
        w = self.fixture.app()
        old = dict(w.preferences)
        disk = self.fixture.preferences.read_bytes()
        w.search.setText('主题')
        current = w.current_path
        w.begin_appearance_preview()
        w.reading_controls.select_theme('future')
        self.assertIn('#172432', w.note_text.styleSheet())
        self.assertEqual(w.preferences, old)
        self.assertEqual(self.fixture.preferences.read_bytes(), disk)
        w.cancel_appearance_preview()
        self.assertEqual(w.reading_controls._values['theme_id'], old['theme_id'])
        self.assertIn(old['reading_background'], w.note_text.styleSheet())
        self.assertEqual(w.current_path, current)
        self.assertEqual(w.search.text(), '主题')

    def test_commit_custom_theme_and_reopen(self):
        w = self.fixture.app()
        w.begin_appearance_preview()
        c = w.reading_controls
        c.select_theme('forest')
        c.detail_controls['reading_line_height'].setValue(185)
        with patch('reading_settings.QInputDialog.getText', return_value=('我的雾林', True)):
            c.save_custom()
        w.commit_appearance_preview()
        p = load_preferences(self.fixture.preferences)
        self.assertEqual(p['reading_line_height'],185)
        self.assertEqual(p['custom_themes']['我的雾林']['reading_line_height'],185)
        again = self.fixture.app()
        self.assertEqual(again.reading_controls._values['theme_id'],'forest')
        again.reading_controls.select_theme('night')
        again.reading_controls.select_saved(again.reading_controls.saved_box.findText('我的雾林'))
        self.assertEqual(again.reading_controls._values['reading_line_height'],185)

    def test_all_presets_keep_paragraph_and_content_with_readable_ink(self):
        from PySide6.QtGui import QColor
        w = self.fixture.app()
        text = '\n\n'.join(f'第{i}段。阅读测试：清楚的文字与舒展的段落。' * 4 for i in range(100))
        w.note_text.markdown(text)
        w.view_stack.setCurrentIndex(1)
        w.show()
        self.qt.processEvents()
        w.note_text.verticalScrollBar().setValue(w.note_text.verticalScrollBar().maximum()//2)
        self.qt.processEvents()
        def luminance(hex):
            c = QColor(hex)
            rgb = [c.redF(),c.greenF(),c.blueF()]
            return sum((x/12.92 if x <= .04045 else ((x+.055)/1.055)**2.4)*y for x,y in zip(rgb,(.2126,.7152,.0722)))
        for key, p in THEMES.items():
            before = w.note_text.cursorForPosition(QPoint(8,8)).blockNumber()
            w.reading_controls.select_theme(key)
            self.qt.processEvents()
            after = w.note_text.cursorForPosition(QPoint(8,8)).blockNumber()
            self.assertLessEqual(abs(before-after),1, key)
            self.assertIn('第99段',w.note_text.toPlainText())
            l = sorted([luminance(p['text_color']),luminance(p['reading_background'])])
            self.assertGreaterEqual((l[1]+.05)/(l[0]+.05),4.5,key)
        self.assertEqual(clean({'ui_radius':999,'text_color':'red; image: url(x)'})['ui_radius'],12)

    def test_save_failure_retains_preview_and_committed_values(self):
        w = self.fixture.app()
        old = dict(w.preferences)
        w.begin_appearance_preview()
        w.reading_controls.select_theme('night')
        with patch('desktop_window.save_preferences', side_effect=OSError), patch('desktop_window.QMessageBox.warning'):
            w.commit_appearance_preview()
        self.assertEqual(w.preferences, old)
        self.assertIsNotNone(w._appearance_preview)
        w.cancel_appearance_preview()

    def test_menu_escape_and_confirmation_buttons(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPushButton
        w = self.fixture.app()
        w.show()
        self.qt.processEvents()
        original = w.preferences['theme_id']
        w.reading_menu.popup(w.reading_button.mapToGlobal(QPoint(0,30)))
        self.qt.processEvents()
        QTest.mouseClick(w.reading_controls.theme_cards['night'], Qt.MouseButton.LeftButton)
        self.qt.processEvents()
        self.assertIsNotNone(w._appearance_preview)
        self.assertEqual(w._appearance_preview['theme_id'], 'night')
        QTest.keyClick(w.reading_menu, Qt.Key.Key_Escape)
        self.qt.processEvents()
        self.assertIsNone(w._appearance_preview)
        self.assertEqual(w.preferences['theme_id'], original)
        w.reading_menu.popup(w.reading_button.mapToGlobal(QPoint(0,30)))
        self.qt.processEvents()
        QTest.mouseClick(w.reading_controls.theme_cards['forest'], Qt.MouseButton.LeftButton)
        button = next(b for b in w.reading_controls.actions_widget.findChildren(QPushButton) if b.text() == '确认保存')
        self.assertTrue(button.isVisible())
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.qt.processEvents()
        self.assertFalse(w.reading_menu.isVisible())
        self.assertEqual(load_preferences(self.fixture.preferences)['theme_id'], 'forest')

    def test_dark_settings_scroll_content_uses_theme_background(self):
        from ui_dialogs import SettingsDialog
        w = self.fixture.app()
        w.reading_controls.select_theme('future')
        d = SettingsDialog(w, w.preferences)
        self.addCleanup(d.close)
        d.show()
        self.qt.processEvents()
        self.assertEqual(d.grab().toImage().pixelColor(400,80).name(), THEMES['future']['app_background'])

    def test_native_color_dialog_keeps_preview_transaction(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QApplication, QColorDialog
        w = self.fixture.app()
        w.show()
        self.qt.processEvents()
        w.reading_menu.popup(w.reading_button.mapToGlobal(QPoint(0,30)))
        self.qt.processEvents()
        old = self.fixture.preferences.read_bytes()
        def choose():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QColorDialog):
                dialog.setCurrentColor(QColor('#eff0dd'))
                dialog.accept()
        QTimer.singleShot(200, choose)
        w.reading_controls.custom_button.click()
        self.qt.processEvents()
        self.assertIsNotNone(w._appearance_preview)
        self.assertTrue(w.reading_menu.isVisible())
        self.assertEqual(w._appearance_preview['reading_background'], '#eff0dd')
        self.assertEqual(self.fixture.preferences.read_bytes(), old)
        w.reading_menu.hide()
