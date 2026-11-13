import json
from pathlib import Path
import tempfile
import unittest


class ReadingPreferencesTests(unittest.TestCase):
    def test_reading_preferences_are_saved_and_invalid_values_are_rejected(self):
        from note_library import save_preferences, load_preferences
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'prefs.json'
            save_preferences({'reading_font_size': 23, 'reading_background': '#F5F0E4'}, path)
            settings = load_preferences(path)
            self.assertEqual(settings.get('reading_font_size'), 23)
            self.assertEqual(settings.get('reading_background'), '#f5f0e4')
            path.write_text(json.dumps({'reading_font_size': 900, 'reading_background': 'red; image: url(x)'}), encoding='utf-8')
            invalid = load_preferences(path)
            self.assertTrue(12 <= invalid['reading_font_size'] <= 32)
            self.assertRegex(invalid['reading_background'], r'^#[a-f0-9]{6}$')


class ReadingTests(unittest.TestCase):
    def setUp(self):
        from test_desktop import DesktopTests
        DesktopTests.setUpClass()
        self.fixture = DesktopTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.qt = DesktopTests.qt

    def test_live_font_and_background_changes_apply_to_all_reading_tabs_and_restart(self):
        from note_library import load_preferences
        result = self.fixture.create_result('阅读笔记', 'BV11DjT6uEhc')
        original_file = Path(result['summary_path']).read_bytes()
        app = self.fixture.app()
        self.assertTrue(hasattr(app, 'reading_controls'), '缺少阅读设置入口')
        original = app.current_markdown
        app.reading_controls.font_box.setValue(23)
        theme = app.reading_controls.theme_box
        theme.setCurrentIndex(theme.findData('#f5f0e4'))
        self.qt.processEvents()
        for reader in (app.note_text, app.raw_text, app.materials_text):
            self.assertEqual(reader.document().defaultFont().pixelSize(), 23)
            self.assertIn('#f5f0e4', reader.styleSheet())
        self.assertEqual(app.current_markdown, original)
        self.assertEqual(Path(result['summary_path']).read_bytes(), original_file)
        prefs = load_preferences(self.fixture.preferences)
        self.assertEqual(prefs['reading_font_size'], 23)
        again = self.fixture.app()
        self.assertEqual(again.reading_controls.font_box.value(), 23)
        self.assertEqual(again.reading_controls.theme_box.currentData(), '#f5f0e4')

    def test_ctrl_wheel_updates_all_reading_tabs_and_saved_size(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        from note_library import load_preferences
        app = self.fixture.app()
        self.assertTrue(hasattr(app, 'reading_controls'), '缺少阅读设置入口')
        before = app.reading_controls.font_box.value()
        wheel = QWheelEvent(QPointF(60, 60), QPointF(60, 60), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        self.qt.sendEvent(app.raw_text.viewport(), wheel)
        self.assertEqual(app.reading_controls.font_box.value(), before + 1)
        self.assertEqual(app.note_text.document().defaultFont().pixelSize(), before + 1)
        self.assertEqual(load_preferences(self.fixture.preferences)['reading_font_size'], before + 1)

    def test_resizing_font_preserves_current_paragraph_and_note_content(self):
        from PySide6.QtCore import QPoint
        app = self.fixture.app()
        self.assertTrue(hasattr(app, 'reading_controls'), '缺少阅读设置入口')
        reader = app.note_text
        text = '\n\n'.join(f'第{i}段。这是一段用来验证长文阅读位置的正文。' * 3 for i in range(100))
        reader.markdown(text, self.fixture.root)
        app.view_stack.setCurrentIndex(1)
        app.show()
        self.qt.processEvents()
        reader.verticalScrollBar().setValue(reader.verticalScrollBar().maximum() // 2)
        self.qt.processEvents()
        before = reader.cursorForPosition(QPoint(8, 8)).blockNumber()
        app.reading_controls.font_box.setValue(25)
        self.qt.processEvents()
        after = reader.cursorForPosition(QPoint(8, 8)).blockNumber()
        self.assertLessEqual(abs(before - after), 1)
        self.assertIn('第99段', reader.toPlainText())

    def test_custom_dark_background_has_readable_foreground_and_safe_persistence(self):
        from PySide6.QtGui import QColor
        from unittest.mock import patch
        app = self.fixture.app()
        self.assertTrue(hasattr(app, 'reading_controls'), '缺少阅读设置入口')
        app.note_text.markdown('[原视频](https://www.bilibili.com/video/BV11DjT6uEhc/)')
        with patch('reading_settings.QColorDialog.getColor', return_value=QColor('#24352b')):
            app.reading_controls.custom_button.click()
        self.assertEqual(app.preferences['reading_background'], '#24352b')
        self.assertIn('#e3eae5', app.note_text.styleSheet())
        foreground = app.note_text.document().find('原视频').charFormat().foreground().color()
        def luminance(color):
            rgb = [color.redF(), color.greenF(), color.blueF()]
            linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
            return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))
        levels = sorted([luminance(foreground), luminance(QColor('#24352b'))])
        self.assertGreaterEqual((levels[1] + .05) / (levels[0] + .05), 4.5)
        before = app.preferences['reading_background']
        with patch('reading_settings.QColorDialog.getColor', return_value=QColor()):
            app.reading_controls.custom_button.click()
        self.assertEqual(app.preferences['reading_background'], before)
