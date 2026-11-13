import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class VideoFolderTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('video_folders'), '尚未实现标题目录规则')
        import video_folders
        return video_folders

    def sample(self, root, name, bvid, title):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / 'p01.txt').write_text('原始字幕，不得改写。', encoding='utf-8')
        note = folder / '笔记—知识笔记.md'
        note.write_text('# 原笔记\n\n原意保留。', encoding='utf-8')
        value = {'bvid': bvid, 'title': title, 'source': f'https://www.bilibili.com/video/{bvid}/',
                 'folder': str(folder), 'summary_path': str(note), 'complete': True,
                 'pages': [{'page': 1, 'paths': {'txt': str(folder / 'p01.txt')}}]}
        (folder / 'video.json').write_text(json.dumps(value), encoding='utf-8')
        return folder

    def test_titles_are_windows_safe_and_collisions_do_not_overwrite(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            folder = module.choose_video_folder(root, 'BV1QVnH6vEpQ', 'AI / PHP：新发现？')
            self.assertNotEqual(folder.name, 'BV1QVnH6vEpQ')
            self.assertNotIn('/', folder.name)
            self.assertNotIn('?', folder.name)
            occupied = self.sample(root, folder.name, 'BV1k5np69EKv', '相同标题')
            second = module.choose_video_folder(root, 'BV1QVnH6vEpQ', 'AI / PHP：新发现？')
            self.assertNotEqual(second, occupied)
            self.assertIn('BV1QVnH6vEpQ', second.name)
            self.assertTrue((occupied / 'video.json').is_file())
            for reserved in ('CON', 'nul.txt', 'COM1', '...'):
                safe = module.title_directory_name(reserved)
                self.assertTrue(safe)
                self.assertNotIn(safe.split('.')[0].upper(), ('CON', 'NUL', 'COM1'))

    def test_legacy_migration_repairs_paths_without_changing_content_or_bv(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            old = self.sample(root, 'BV1QVnH6vEpQ', 'BV1QVnH6vEpQ', 'AI发现PHP漏洞')
            original_note = (old / '笔记—知识笔记.md').read_bytes()
            (old / 'raw.json').write_text(json.dumps({'body': [{'content': str(old)}]}), encoding='utf-8')
            changes = module.migrate_legacy_folders(root)
            new = root / 'AI发现PHP漏洞'
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())
            result = json.loads((new / 'video.json').read_text(encoding='utf-8'))
            self.assertEqual(result['bvid'], 'BV1QVnH6vEpQ')
            self.assertTrue(result['source'].endswith('/BV1QVnH6vEpQ/'))
            self.assertEqual(Path(result['summary_path']).read_bytes(), original_note)
            self.assertEqual(Path(result['pages'][0]['paths']['txt']).read_text(encoding='utf-8'), '原始字幕，不得改写。')
            self.assertEqual(changes[str(old)], str(new))
            self.assertEqual(json.loads((new / 'raw.json').read_text(encoding='utf-8'))['body'][0]['content'], str(old))
            self.assertEqual(module.choose_video_folder(root, result['bvid'], result['title']), new)
            self.assertEqual(module.migrate_legacy_folders(root), {})

    def test_failed_metadata_repair_rolls_back_the_move(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            old = self.sample(root, 'BV1QVnH6vEpQ', 'BV1QVnH6vEpQ', '视频标题')
            before = (old / 'video.json').read_bytes()
            with patch.object(module, 'atomic_write', side_effect=OSError('磁盘故障')):
                with self.assertRaises(OSError):
                    module.relocate_folder(old, root / '视频标题', root)
            self.assertTrue(old.is_dir())
            self.assertEqual((old / 'video.json').read_bytes(), before)

    def test_duplicate_video_folders_are_not_merged_or_overwritten(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            existing = self.sample(root, '相同视频', 'BV1QVnH6vEpQ', '相同视频')
            legacy = self.sample(root, 'BV1QVnH6vEpQ', 'BV1QVnH6vEpQ', '相同视频')
            before = (existing / 'video.json').read_bytes()
            changes = module.migrate_legacy_folders(root)
            self.assertEqual((existing / 'video.json').read_bytes(), before)
            self.assertTrue(Path(changes[str(legacy)]).is_dir())
            self.assertNotEqual(Path(changes[str(legacy)]), existing)

    def test_parallel_same_title_reserves_distinct_directories(self):
        module = self.module()
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            self.assertTrue(hasattr(module, 'reserve_video_folder'), '尚未原子预留输出目录')
            with ThreadPoolExecutor(max_workers=2) as pool:
                paths = list(pool.map(lambda bvid: module.reserve_video_folder(root, bvid, '完全相同的标题'),
                                      ('BV1QVnH6vEpQ', 'BV1k5np69EKv')))
            self.assertNotEqual(paths[0], paths[1])
            self.assertTrue(all(path.is_dir() for path in paths))


if __name__ == '__main__':
    unittest.main()
