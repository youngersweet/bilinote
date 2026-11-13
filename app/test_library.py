import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


class LibraryTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('note_library'), '尚未实现笔记历史库')
        import note_library
        return note_library

    def result_file(self, folder, title='原视频', bvid='BV1k5np69EKv'):
        folder.mkdir(parents=True)
        (folder / 'p01.txt').write_text('原字幕', encoding='utf-8')
        (folder / 'p01.json').write_text(json.dumps({'body': [{'from': 0, 'to': 1, 'content': '原字幕'}]}), encoding='utf-8')
        path = folder / 'video.json'
        path.write_text(json.dumps({'title': title, 'bvid': bvid, 'author': '作者', 'folder': str(folder),
                                   'complete': True, 'pages': [{'page': 1, 'paths': {'txt': str(folder / 'p01.txt'), 'json': str(folder / 'p01.json')}}],
                                   'source': f'https://www.bilibili.com/video/{bvid}/'}), encoding='utf-8')
        return path

    def test_history_search_and_missing_note_are_truthful(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = self.result_file(root / 'one', '嵌入式学习')
            second = self.result_file(root / 'two', 'AI软件', 'BV1L4np61EWq')
            (first.parent / '嵌入式学习—知识笔记.md').write_text('# 知识\n\n真实正文', encoding='utf-8')
            entries = module.scan_library(root)
            self.assertEqual(len(entries), 2)
            entry = next(e for e in entries if e.path == first.resolve())
            self.assertTrue(entry.note_path)
            self.assertEqual(entry.state, 'ready')
            self.assertEqual(next(e for e in entries if e.path == second.resolve()).state, 'extracted')
            self.assertEqual([e.title for e in module.filter_entries(entries, '嵌入式')], ['嵌入式学习'])
            self.assertEqual(len(module.filter_entries(entries, 'bv1l4np61ewq')), 1)

    def test_bad_metadata_is_skipped_and_external_import_survives_restart(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = root / 'preferences.json'
            external = self.result_file(root / 'external' / 'video', '外部导入')
            output = root / 'output'
            broken = output / 'bad'
            broken.mkdir(parents=True)
            (broken / 'video.json').write_text('{broken', encoding='utf-8')
            module.save_preferences({'output_dir': str(output), 'imports': [str(external)]}, store)
            prefs = module.load_preferences(store)
            entries = module.scan_library(output, prefs['imports'])
            self.assertEqual([e.title for e in entries], ['外部导入'])
            self.assertEqual(prefs['mode'], 'auto')

    def test_note_path_cannot_escape_its_video_folder(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = self.result_file(root / 'video')
            outside = root / 'unrelated.md'
            outside.write_text('不属于本视频', encoding='utf-8')
            result = json.loads(path.read_text(encoding='utf-8'))
            result['summary_path'] = str(outside)
            path.write_text(json.dumps(result), encoding='utf-8')
            self.assertIsNone(module.scan_library(root)[0].note_path)

    def test_sparse_and_malformed_history_cannot_crash_the_reader(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = self.result_file(root / 'sparse')
            data = json.loads(path.read_text(encoding='utf-8'))
            del data['title']
            data['folder'] = str(root / 'unrelated')
            path.write_text(json.dumps(data), encoding='utf-8')
            unchanged = path.read_bytes()
            entry = module.scan_library(root)[0]
            self.assertEqual(entry.result['title'], data['bvid'])
            self.assertEqual(Path(entry.result['folder']), path.parent.resolve())
            self.assertEqual(path.read_bytes(), unchanged)
            data['pages'] = [123]
            path.write_text(json.dumps(data), encoding='utf-8')
            self.assertEqual(module.scan_library(root), [])


if __name__ == '__main__':
    unittest.main()
