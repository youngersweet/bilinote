"""Real-process coverage of shortcut relaunch and abandoned lock recovery."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


APP = Path(__file__).resolve().parent
OWNER = r'''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QTimer
import desktop
import bootstrap
root = Path(sys.argv[2])
original = desktop.App
def create(**kwargs):
    window = original(check_online=False, migrate_folders=False, enable_tray=False)
    window._setup_tray()
    restore = window.restore_window
    def activate():
        restore()
        (root / 'restored').write_text(str(window.isVisible()))
    window.restore_window = activate
    def hide():
        window.close()
        (root / 'ready').write_text(str(window.isVisible()))
    QTimer.singleShot(100, hide)
    window.test_timer = QTimer(window)
    def finish():
        if (root / 'stop').exists():
            window.request_exit()
    window.test_timer.timeout.connect(finish)
    window.test_timer.start(50)
    return window
desktop.App = create
bootstrap.main()
'''


class SingleInstanceTests(unittest.TestCase):
    def wait_file(self, path, process):
        deadline = time.monotonic() + 15
        while not path.exists() and time.monotonic() < deadline:
            self.assertIsNone(process.poll(), 'Owner exited before reporting its window state')
            time.sleep(.05)
        self.assertTrue(path.exists(), str(path))

    def test_shortcut_entry_restores_hidden_owner_and_secondary_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env = {**os.environ, 'LOCALAPPDATA': folder, 'BILINOTE_OUTPUT': str(root / 'notes')}
            owner = subprocess.Popen([sys.executable, '-c', OWNER, str(APP), folder], env=env)
            try:
                self.wait_file(root / 'ready', owner)
                self.assertEqual((root / 'ready').read_text(), 'False')
                for entry in ('bootstrap.py', 'desktop.py', 'bootstrap.py'):
                    (root / 'restored').unlink(missing_ok=True)
                    subprocess.run([sys.executable, str(APP / entry)], env=env,
                                   check=True, timeout=15, capture_output=True)
                    self.wait_file(root / 'restored', owner)
                    self.assertEqual((root / 'restored').read_text(), 'True')
                    self.assertIsNone(owner.poll())
                (root / 'stop').touch()
                self.assertEqual(owner.wait(timeout=10), 0)
            finally:
                if owner.poll() is None:
                    owner.kill()
                    owner.wait()

    def test_crashed_owner_does_not_block_next_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            code = ('import sys,time;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
                    'from PySide6.QtCore import QCoreApplication;from single_instance import SingleInstance;'
                    'app=QCoreApplication([]);g=SingleInstance(sys.argv[2]);'
                    'assert g.acquire_or_activate();Path(sys.argv[2],"ready").touch();time.sleep(30)')
            owner = subprocess.Popen([sys.executable, '-c', code, str(APP), folder])
            try:
                self.wait_file(root / 'ready', owner)
            finally:
                owner.kill()
                owner.wait()
            check = ('import sys;sys.path.insert(0,sys.argv[1]);'
                     'from PySide6.QtCore import QCoreApplication;from single_instance import SingleInstance;'
                     'app=QCoreApplication([]);g=SingleInstance(sys.argv[2]);'
                     'assert g.acquire_or_activate();g.close()')
            subprocess.run([sys.executable, '-c', check, str(APP), folder],
                           check=True, capture_output=True, timeout=15)
