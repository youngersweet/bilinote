import json
from pathlib import Path
import tempfile
import unittest

import note_library


class NoteDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / '生成笔记'
        self.output.mkdir()

    def note(self, folder, bvid='BV1k5np69EKv'):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'video.json'
        path.write_text(json.dumps({'bvid': bvid, 'title': '视频', 'pages': [],
                                    'folder': str(self.root)}), encoding='utf-8')
        (folder / '正文.md').write_text('知识笔记', encoding='utf-8')
        (folder / '素材').mkdir()
        (folder / '素材' / '音频.m4a').write_bytes(b'audio')
        return path

    def delete(self, path, **kwargs):
        self.assertTrue(callable(getattr(note_library, 'delete_note_folder', None)), '缺少安全删除笔记功能')
        return note_library.delete_note_folder(path, self.output, 'BV1k5np69EKv', **kwargs)

    def test_deletes_only_selected_video_folder_and_all_its_materials(self):
        selected = self.note(self.output / '删除的视频')
        other = self.note(self.output / '保留的视频', 'BV1L4np61EWq')
        self.delete(selected)
        self.assertFalse(selected.parent.exists())
        self.assertEqual((other.parent / '正文.md').read_text(encoding='utf-8'), '知识笔记')
        self.assertTrue(self.output.is_dir())

    def test_output_root_and_external_imports_cannot_be_deleted(self):
        external = self.note(self.root / '外部导入')
        root_metadata = self.note(self.output)
        for path in (external, root_metadata):
            with self.assertRaises(ValueError):
                self.delete(path)
            self.assertTrue(path.is_file())

    def test_running_or_queued_video_cannot_be_deleted(self):
        path = self.note(self.output / '工作中')
        with self.assertRaisesRegex(ValueError, '处理'):
            self.delete(path, active_bvids={'BV1k5np69EKv'})
        self.assertTrue(path.is_file())

    def test_replaced_metadata_cannot_delete_a_different_video(self):
        path = self.note(self.output / '已被替换', 'BV1L4np61EWq')
        with self.assertRaisesRegex(ValueError, '变化'):
            self.delete(path)
        self.assertTrue(path.is_file())

    def test_external_directory_junction_inside_note_is_rejected(self):
        import os
        import subprocess
        if os.name != 'nt':
            self.skipTest('Windows junction guard')
        path = self.note(self.output / '带链接')
        external = self.root / '其他资料'
        external.mkdir()
        sentinel = external / '保留.txt'
        sentinel.write_text('保留', encoding='utf-8')
        link = path.parent / '外部链接'
        created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(external)], capture_output=True)
        self.assertEqual(created.returncode, 0)
        with self.assertRaisesRegex(ValueError, '链接'):
            self.delete(path)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), '保留')
        self.assertTrue(path.is_file())
