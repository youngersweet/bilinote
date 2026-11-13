from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QSystemTrayIcon
from desktop import App, ensure_application
from note_library import save_preferences, load_preferences
from task_queue import VideoTaskQueue


class TrayTests(unittest.TestCase):
    def setUp(self):
        self.qt = ensure_application()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        prefs = Path(self.tmp.name) / 'prefs.json'
        save_preferences({'output_dir': str(Path(self.tmp.name) / 'notes')}, prefs)
        self.window = App(check_online=False, migrate_folders=False, preferences_path=prefs)
        self.window._setup_tray()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.window._exit_requested = True
        self.window.exit_timer.stop()
        self.window.close()

    def test_close_hides_without_stopping_queue_and_click_restores(self):
        self.window.show()
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.window.queue._closed)
        self.window._tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertTrue(self.window.isVisible())

    def test_exit_waits_for_active_work_then_closes(self):
        with patch.object(self.window.queue, 'has_pending', return_value=True), patch.object(self.qt, 'quit') as quit_app:
            self.window.request_exit()
            self.assertTrue(self.window.queue._closed)
            self.assertTrue(self.window.exit_timer.isActive())
            self.window._finish_exit()
            quit_app.assert_not_called()
        with patch.object(self.qt, 'quit') as quit_app:
            self.window._finish_exit()
            quit_app.assert_called_once()
        self.assertTrue(self.window._closing)
        self.assertFalse(self.window.tray.isVisible())

    def test_eight_workers_and_preference_survive_restart(self):
        prefs = Path(self.tmp.name) / 'parallel.json'
        save_preferences({'concurrency': 8}, prefs)
        self.assertEqual(load_preferences(prefs)['concurrency'], 8)
        queue = VideoTaskQueue(limit=8)
        try:
            self.assertEqual(queue.limit, 8)
            self.assertEqual(queue._pool._max_workers, 8)
            queue.set_limit(6)
            self.assertEqual(queue.limit, 6)
        finally:
            queue.shutdown()
