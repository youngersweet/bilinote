"""回归：GUI调用的关键帧脚本，其下载及FFmpeg子进程也必须无控制台。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.name == 'nt', 'Windows 后台窗口回归')
class BackgroundWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'resources/bili-note/scripts/extract_video_keyframes.py'
        spec = importlib.util.spec_from_file_location('bili_note_keyframe_window_test', path)
        cls.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.script)

    def test_single_frame_ffmpeg_process_has_no_window_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'frame.png'
            def frame(command, **options):
                output.write_bytes(b'frame-fixture')
                self.assertTrue(options.get('creationflags', 0) & subprocess.CREATE_NO_WINDOW,
                                '逐帧FFmpeg未隐藏控制台')
            with patch.object(self.script.subprocess, 'run', side_effect=frame):
                self.script.extract_frame(Path(folder) / 'input.mp4', 1.5, output)

    def test_contact_sheet_ffmpeg_process_has_no_window_flag(self):
        def contact(command, **options):
            self.assertTrue(options.get('creationflags', 0) & subprocess.CREATE_NO_WINDOW,
                            '联系图FFmpeg未隐藏控制台')
        with tempfile.TemporaryDirectory() as folder, patch.object(self.script.subprocess, 'run', side_effect=contact):
            self.script.create_contact_sheet(Path(folder), 12, Path(folder) / 'sheet.png')

    def test_video_download_fallback_process_has_no_window_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def download(command, **options):
                self.assertTrue(options.get('creationflags', 0) & subprocess.CREATE_NO_WINDOW,
                                '下载后备进程未隐藏控制台')
                (root / 'p01_12.mp4').write_bytes(b'cached-video-fixture')
            with patch.object(self.script.subprocess, 'run', side_effect=download):
                result = self.script.download_video_with_ytdlp('BV11DjT6uEhc', {'page': 1, 'cid': 12}, root)
            self.assertEqual(result.name, 'p01_12.mp4')

    def test_real_nested_console_program_is_created_without_a_console(self):
        # 用可报告控制台句柄的真实子进程，验证标志确实到达Windows，不下载媒体。
        real_run = subprocess.run
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'window.json'
            output = Path(folder) / 'frame.png'
            child = ('import ctypes,json,sys; from pathlib import Path; '
                     'k=ctypes.WinDLL("kernel32"); k.GetConsoleWindow.restype=ctypes.c_void_p; '
                     'Path(sys.argv[1]).write_text(json.dumps({"console": k.GetConsoleWindow() or 0})); '
                     'Path(sys.argv[2]).write_bytes(b"frame")')
            def invoke(command, **options):
                python = Path(sys.executable).with_name('python.exe')
                return real_run([str(python), '-c', child, str(report), str(output)], **options)
            with patch.object(self.script.subprocess, 'run', side_effect=invoke):
                self.script.extract_frame(Path(folder) / 'input.mp4', 0, output)
            self.assertEqual(json.loads(report.read_text())['console'], 0)
