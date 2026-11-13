import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtCore import QEvent
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
import test_desktop as fixtures
from note_library import load_preferences


class LayoutControlsTests(unittest.TestCase):
    def setUp(self):
        fixtures.DesktopTests.setUpClass()
        self.fixture = fixtures.DesktopTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        result = self.fixture.create_result('阅读位置', 'BV1k5np69EKv')
        self.note = Path(result['summary_path'])
        self.original = self.note.read_bytes()
        self.window = self.fixture.app()
        self.window.show()
        self.qt = fixtures.DesktopTests.qt
        self.qt.processEvents()

    def double_blank(self, widget):
        point = QPoint(3, 3)
        self.assertIsNone(widget.childAt(point))
        QTest.mouseDClick(widget, Qt.MouseButton.LeftButton, pos=point)
        self.qt.processEvents()

    def test_header_double_click_toggles_only_bar_and_keeps_current_tab(self):
        w = self.window
        self.assertTrue(w.tabs.tabBar().isHidden())
        self.assertIn('展开', w.reader_header.blank_hint)
        self.double_blank(w.reader_header)
        self.assertFalse(w.tabs.tabBar().isHidden())
        w.tabs.setCurrentIndex(1)
        self.double_blank(w.reader_header)
        self.assertTrue(w.tabs.tabBar().isHidden())
        self.assertEqual(w.tabs.currentIndex(), 1)
        self.assertTrue(w.raw_text.isVisible())
        self.assertEqual(self.note.read_bytes(), self.original)

    def test_notes_click_preserves_filter_selection_and_width(self):
        w = self.window
        w.search.setText('阅读')
        selected = w.current_path
        w.splitter.setSizes([350, 700])
        width = w.splitter.sizes()[0]
        QTest.mouseClick(w.notes_button, Qt.MouseButton.LeftButton)
        self.assertTrue(w.library_panel.isHidden())
        self.assertIn('展开', w.notes_button.toolTip())
        QTest.mouseClick(w.notes_button, Qt.MouseButton.LeftButton)
        self.qt.processEvents()
        self.assertFalse(w.library_panel.isHidden())
        self.assertEqual(w.search.text(), '阅读')
        self.assertEqual(w.current_path, selected)
        self.assertLessEqual(abs(w.splitter.sizes()[0] - width), 2)

    def test_immersive_has_restore_edge_and_restores_previous_list_state(self):
        w = self.window
        for list_visible in (True, False):
            if w.preferences['library_visible'] != list_visible:
                w.toggle_library()
            self.double_blank(w.navigation_rail)
            self.assertTrue(w.navigation_rail.isHidden())
            self.assertTrue(w.library_panel.isHidden())
            self.assertTrue(w.navigation_handle.isVisible())
            self.assertTrue(w.note_text.isVisible())
            self.double_blank(w.navigation_handle)
            self.assertTrue(w.navigation_rail.isVisible())
            self.assertEqual(not w.library_panel.isHidden(), list_visible)
        self.assertEqual(self.note.read_bytes(), self.original)

    def test_buttons_do_not_trigger_blank_gestures_and_search_can_reveal(self):
        w = self.window
        with patch.object(w, 'toggle_immersive') as toggle:
            QTest.mouseDClick(w.notes_button, Qt.MouseButton.LeftButton)
            toggle.assert_not_called()
        self.assertFalse(w.preferences['immersive_reading'])
        with patch('desktop_window.QApplication.clipboard'):
            QTest.mouseDClick(w.copy_button, Qt.MouseButton.LeftButton)
        self.assertTrue(w.tabs.tabBar().isHidden())
        w.toggle_immersive()
        w.focus_library_search()
        self.qt.processEvents()
        self.assertTrue(w.navigation_rail.isVisible())
        self.assertTrue(w.library_panel.isVisible())
        self.assertTrue(w.search.hasFocus())

    def test_restart_retains_layout_and_restore_handle(self):
        w = self.window
        w.toggle_reading_tabs()
        w.toggle_immersive()
        prefs = load_preferences(self.fixture.preferences)
        self.assertTrue(prefs['reading_tabs_visible'])
        self.assertTrue(prefs['immersive_reading'])
        again = self.fixture.app()
        again.show()
        self.qt.processEvents()
        self.assertTrue(again.navigation_rail.isHidden())
        self.assertTrue(again.navigation_handle.isVisible())
        self.assertFalse(again.tabs.tabBar().isHidden())

    def test_blank_hover_displays_current_action_hint(self):
        area = self.window.header_blank_area
        for text in ('展开', '隐藏'):
            event = QHelpEvent(QEvent.Type.ToolTip, QPoint(3, 3), area.mapToGlobal(QPoint(3, 3)))
            with patch('layout_controls.QToolTip.showText') as show:
                self.qt.sendEvent(area, event)
            self.assertIn(text, show.call_args.args[1])
            self.double_blank(area)
