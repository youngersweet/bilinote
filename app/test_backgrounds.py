import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtGui import QImage, QColor, QPainter
from backgrounds import BackgroundPaint, import_photo
from note_library import load_preferences
import test_desktop as fixtures


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        fixtures.DesktopTests.setUpClass()
        self.fixture = fixtures.DesktopTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def photo(self):
        path = self.fixture.root / '背景 照片.png'
        image = QImage(80, 50, QImage.Format.Format_RGB32)
        image.fill(QColor('#0000ff'))
        self.assertTrue(image.save(str(path)))
        return path

    def test_import_copies_and_normalizes_photo_and_rejects_corruption(self):
        source = self.photo()
        target = import_photo(source, self.fixture.root / 'cache')
        source.unlink()
        self.assertFalse(QImage(target).isNull())
        self.assertTrue(Path(target).is_file())
        invalid = self.fixture.root / 'bad.jpg'
        invalid.write_text('not a photo')
        with self.assertRaises(ValueError):
            import_photo(invalid, self.fixture.root / 'cache')

    def test_opacity_affects_photo_only_and_missing_image_falls_back(self):
        paint = BackgroundPaint()
        for opacity, expected in ((0, '#0000ff'), (100, '#ffffff')):
            paint.configure('#ffffff', str(self.photo()), opacity)
            output = QImage(100, 100, QImage.Format.Format_RGB32)
            painter = QPainter(output)
            paint.draw(painter, output.rect())
            painter.end()
            self.assertEqual(output.pixelColor(50, 50).name(), expected)
        paint.configure('#ffffff', str(self.fixture.root / 'missing.png'))
        self.assertTrue(paint.pixmap.isNull())

    def test_controls_persist_sync_photo_opacity_and_reset_without_changing_note(self):
        result = self.fixture.create_result('原文不变', 'BV1k5np69EKv')
        note = Path(result['summary_path'])
        original = note.read_bytes()
        window = self.fixture.app()
        control = window.reading_controls
        source = self.photo()
        with patch('reading_settings.QFileDialog.getOpenFileName', return_value=(str(source), '')), \
             patch('bili_service.DATA_DIR', self.fixture.root / 'local'):
            control.photo_button.click()
        control.match_app.setChecked(True)
        control.opacity_slider.setValue(35)
        saved = load_preferences(self.fixture.preferences)
        self.assertTrue(saved['match_app_background'])
        self.assertEqual(saved['background_transparency'], 35)
        self.assertTrue(Path(saved['background_image']).is_file())
        self.assertFalse(window.note_text.background.pixmap.isNull())
        self.assertEqual(window.centralWidget().background.path, saved['background_image'])
        again = self.fixture.app()
        self.assertTrue(again.reading_controls.match_app.isChecked())
        self.assertEqual(again.reading_controls.opacity_slider.value(), 35)
        control.match_app.setChecked(False)
        self.assertEqual(window.centralWidget().background.path, '')
        self.assertFalse(window.note_text.background.pixmap.isNull())
        control.reset()
        self.assertTrue(window.note_text.background.pixmap.isNull())
        self.assertEqual(load_preferences(self.fixture.preferences)['background_image'], '')
        self.assertEqual(note.read_bytes(), original)

    def test_invalid_saved_values_are_sanitized(self):
        path = self.fixture.root / 'bad.json'
        path.write_text(json.dumps({'match_app_background': 'yes', 'background_image': {},
                                    'background_transparency': 999}))
        saved = load_preferences(path)
        self.assertFalse(saved['match_app_background'])
        self.assertEqual(saved['background_image'], '')
        self.assertEqual(saved['background_transparency'], 80)
