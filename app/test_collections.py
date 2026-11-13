import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch


def video_data(bvid='BV11DjT6uEhc', title='课程', count=2):
    return {'bvid': bvid, 'aid': 1, 'title': title, 'owner': {'name': '作者', 'mid': 10},
            'duration': 180, 'pages': [{'page': n, 'cid': 100 + n, 'part': f'第{n}课', 'duration': 60 * n}
                                      for n in range(1, count + 1)]}


class CollectionTests(unittest.TestCase):
    def catalog(self):
        self.assertIsNotNone(importlib.util.find_spec('video_catalog'), '尚未实现合集目录读取')
        import video_catalog
        return video_catalog

    def test_catalog_distinguishes_multi_parts_and_related_videos_without_media(self):
        module = self.catalog()
        data = video_data()
        data['ugc_season'] = {'id': 9, 'title': '系列', 'ep_count': 2, 'mid': 10, 'sections': [
            {'episodes': [{'bvid': 'BV11DjT6uEhc', 'title': '课程'}, {'bvid': 'BV1k5np69EKv', 'title': '补充课'}]}]}
        client = Mock()
        client.request.return_value = {'code': 0, 'data': data}
        info = module.VideoCatalog(client).read('https://www.bilibili.com/video/BV11DjT6uEhc/?p=2')
        self.assertEqual(len(info.parts), 2)
        self.assertEqual(info.requested_page, 2)
        self.assertEqual(info.parts[1].source, 'https://www.bilibili.com/video/BV11DjT6uEhc/?p=2')
        self.assertEqual(len(info.related), 2)
        self.assertEqual(info.season_title, '系列')
        self.assertEqual(client.request.call_count, 1)

    def test_part_sources_are_not_deduplicated_by_bv_only(self):
        from task_queue import parse_video_sources
        sources = parse_video_sources('https://www.bilibili.com/video/BV11DjT6uEhc/?p=1\nhttps://www.bilibili.com/video/BV11DjT6uEhc/?p=2')
        self.assertEqual(len(sources), 2)

    def test_index_tracks_selected_failed_and_unselected_parts_without_claiming_complete_course(self):
        module = self.catalog()
        self.assertIsNotNone(importlib.util.find_spec('collection_index'), '缺少合集导航目录')
        from collection_index import update_index, index_paths, refresh_indexes
        client = Mock()
        client.request.return_value = {'code': 0, 'data': video_data()}
        info = module.VideoCatalog(client).read('BV11DjT6uEhc')
        plan = module.collection_plan([info])
        with tempfile.TemporaryDirectory() as folder:
            update_index(folder, plan, info.parts[0].source, 'queued')
            path, unused = index_paths(folder, plan)
            text = path.read_text(encoding='utf-8')
            self.assertIn('等待', text)
            self.assertIn('未选择', text)
            update_index(folder, plan, info.parts[0].source, 'failed', error='本集失败')
            self.assertIn('失败', path.read_text(encoding='utf-8'))
            note = Path(folder) / '第一集' / '笔记.md'
            note.parent.mkdir()
            note.write_text('第一集正文', encoding='utf-8')
            update_index(folder, plan, info.parts[0].source, 'done', result={'summary_path': str(note)})
            from urllib.parse import unquote
            self.assertIn('笔记.md', unquote(path.read_text(encoding='utf-8')))
            note.unlink()
            refresh_indexes(folder)
            self.assertIn('文件已删除', path.read_text(encoding='utf-8'))

    def test_stale_part_cid_cannot_process_a_replaced_episode(self):
        import content_pipeline as pipeline
        client = Mock()
        client.request.return_value = {'code': 0, 'data': video_data()}
        with tempfile.TemporaryDirectory() as folder, patch.object(pipeline.ContentPipeline, 'platform_page') as subtitle:
            with self.assertRaisesRegex(ValueError, '变化'):
                pipeline.collect_video('https://www.bilibili.com/video/BV11DjT6uEhc/?p=2', folder,
                                       client=client, expected_cid=999)
            subtitle.assert_not_called()

    def test_new_cid_does_not_inherit_old_note_link_in_collection_index(self):
        from collection_index import update_index, index_paths
        from video_catalog import VideoCatalog, collection_plan
        client = Mock()
        client.request.return_value = {'code': 0, 'data': video_data()}
        info = VideoCatalog(client).read('BV11DjT6uEhc')
        plan = collection_plan([info])
        with tempfile.TemporaryDirectory() as output:
            note = Path(output) / 'old.md'
            note.write_text('旧正文', encoding='utf-8')
            update_index(output, plan, info.parts[0].source, 'done', result={'summary_path': str(note)})
            plan['entries'][0].update(cid=999, title='替换后的新课')
            update_index(output, plan, info.parts[0].source, 'queued')
            self.assertNotIn('打开笔记', index_paths(output, plan)[0].read_text(encoding='utf-8'))
            self.assertEqual(note.read_text(encoding='utf-8'), '旧正文')

    def test_browser_fallback_requests_only_selected_part(self):
        import io
        import content_pipeline as pipeline
        client = Mock()
        client.request.return_value = {'code': 0, 'data': video_data()}
        calls = []
        def script(name, args, timeout=0):
            calls.append(args)
            out = Path(args[args.index('--out') + 1])
            out.mkdir(parents=True, exist_ok=True)
            raw = out / 'p2.json'
            raw.write_text(json.dumps({'body': [{'from': 0, 'to': 119, 'content': '第二集'}]}), encoding='utf-8')
            (out / 'browser_ai_subtitle_manifest.json').write_text(json.dumps({'bvid': 'BV11DjT6uEhc', 'outputs': [
                {'page': 2, 'files': {'json': str(raw)}}]}), encoding='utf-8')
        targets = json.dumps([{'url': 'https://www.bilibili.com/video/BV11DjT6uEhc/', 'id': 'target'}])
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(pipeline.ContentPipeline, 'platform_page', return_value=None), \
             patch.object(pipeline.ContentPipeline, 'archive'), \
             patch('content_pipeline.urllib.request.urlopen', return_value=io.StringIO(targets)), \
             patch('content_pipeline.run_skill_script', side_effect=script):
            result = pipeline.collect_video('https://www.bilibili.com/video/BV11DjT6uEhc/?p=2', folder, client=client)
        self.assertEqual(calls[0][calls[0].index('--parts') + 1], '2')
        self.assertEqual(result['pages'][0]['page'], 2)

    def test_manual_visual_on_part_cannot_expand_to_whole_course(self):
        import content_pipeline as pipeline
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / 'materials'
            work.mkdir()
            (work / 'keyframes_manifest.json').write_text('{"frame_count": 0}', encoding='utf-8')
            result = {'folder': folder, 'source': 'https://www.bilibili.com/video/BV11DjT6uEhc/?p=2',
                      'selected_pages': [2]}
            with patch('content_pipeline.run_skill_script') as script:
                pipeline.prepare_visual(result)
            args = script.call_args.args[1]
            self.assertEqual(args[args.index('--parts') + 1], '2')

    def test_replaced_cid_creates_new_folder_without_relabeling_old_note(self):
        import content_pipeline as pipeline
        from note_library import scan_library
        client = Mock()
        payload = {'body': [{'from': 0, 'to': 59, 'content': '字幕'}]}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(pipeline.ContentPipeline, 'platform_page', return_value=payload), \
             patch.object(pipeline.ContentPipeline, 'archive'):
            client.request.return_value = {'code': 0, 'data': video_data()}
            old = pipeline.collect_video('https://www.bilibili.com/video/BV11DjT6uEhc/?p=1', folder, client=client)
            (Path(old['folder']) / '旧课—知识笔记.md').write_text('旧版课程正文', encoding='utf-8')
            changed = video_data()
            changed['pages'][0].update(cid=999, part='替换后的新课')
            client.request.return_value = {'code': 0, 'data': changed}
            new = pipeline.collect_video('https://www.bilibili.com/video/BV11DjT6uEhc/?p=1', folder,
                                         client=client, expected_cid=999)
            self.assertNotEqual(old['folder'], new['folder'])
            entry = next(entry for entry in scan_library(folder) if entry.path.parent == Path(new['folder']))
            self.assertIsNone(entry.note_path)
            self.assertEqual((Path(old['folder']) / '旧课—知识笔记.md').read_text(encoding='utf-8'), '旧版课程正文')

    def test_single_part_collection_reads_only_selected_body_and_retains_full_scope(self):
        import content_pipeline as pipeline
        client = Mock()
        client.request.return_value = {'code': 0, 'data': video_data()}
        payload = {'body': [{'from': 0, 'to': 119, 'content': '第二课完整正文'}]}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(pipeline.ContentPipeline, 'platform_page', return_value=payload) as subtitle, \
             patch.object(pipeline.ContentPipeline, 'browser_page') as browser, \
             patch.object(pipeline.ContentPipeline, 'audio_page') as audio, \
             patch.object(pipeline.ContentPipeline, 'archive'):
            result = pipeline.collect_video('https://www.bilibili.com/video/BV11DjT6uEhc/?p=2', folder, client=client)
            self.assertEqual(len(result['pages']), 1)
            self.assertEqual(result['pages'][0]['page'], 2)
            self.assertEqual(result['pages_total'], 1)
            self.assertEqual(result['video_pages_total'], 2)
            self.assertEqual(result['selected_pages'], [2])
            self.assertEqual(result['duration'], 120)
            self.assertTrue(result['complete'])
            self.assertIn('P002', result['title'])
            self.assertEqual(subtitle.call_count, 1)
            browser.assert_not_called()
            audio.assert_not_called()

    def test_two_parts_of_same_bv_run_independently_and_keep_separate_folders(self):
        import content_pipeline as pipeline
        import task_queue as tasks
        barrier = threading.Barrier(2)
        def client_factory():
            client = Mock()
            client.request.return_value = {'code': 0, 'data': video_data()}
            return client
        def summary(result, progress, settings=None):
            barrier.wait(3)
            path = Path(result['folder']) / '分集—知识笔记.md'
            path.write_text(str(result['selected_pages']), encoding='utf-8')
            result['summary_path'] = str(path)
        payload = {'body': [{'from': 0, 'to': 59, 'content': '完整正文'}]}
        module = self.catalog()
        info = module.VideoCatalog(client_factory()).read('BV11DjT6uEhc')
        plan = module.collection_plan([info])
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(pipeline.ContentPipeline, 'platform_page', return_value=payload), \
             patch.object(pipeline.ContentPipeline, 'archive'), \
             patch('bili_service.summarize', side_effect=summary):
            queue = tasks.VideoTaskQueue(2, runner=lambda spec, progress, materials:
                tasks.process_video_task(spec, progress, materials, client_factory))
            try:
                ids, duplicates = queue.enqueue([tasks.TaskSpec(source=f'https://www.bilibili.com/video/BV11DjT6uEhc/?p={p}', output_dir=folder,
                                                               expected_cid=100 + p, collection=plan)
                                                 for p in (1, 2, 1)])
                self.assertEqual(len(ids), 2)
                end = time.monotonic() + 4
                while queue.has_pending() and time.monotonic() < end:
                    time.sleep(.01)
                results = [queue.get(identity) for identity in ids]
                self.assertEqual([r.status for r in results], ['done', 'done'])
                self.assertNotEqual(results[0].result['folder'], results[1].result['folder'])
                for p, task in enumerate(results, 1):
                    self.assertEqual(Path(task.result['summary_path']).read_text(encoding='utf-8'), f'[{p}]')
                    self.assertTrue(Path(task.result['collection_index']).is_file())
                    data = json.loads((Path(task.result['folder']) / 'video.json').read_text(encoding='utf-8'))
                    self.assertEqual(data['selected_pages'], [p])
                index = Path(results[0].result['collection_index']).read_text(encoding='utf-8')
                self.assertEqual(index.count('打开笔记'), 2)
            finally:
                queue.shutdown()

    def test_failed_part_can_retry_while_another_part_is_still_running(self):
        import task_queue as tasks
        from bili_service import source_page
        release = threading.Event()
        attempts = {1: 0, 2: 0}
        def runner(spec, progress, materials):
            page = source_page(spec.source)
            attempts[page] += 1
            if page == 1 and attempts[page] == 1:
                raise RuntimeError('仅第一集失败')
            if page == 2:
                release.wait(3)
            return {'complete': True, 'title': str(page)}
        queue = tasks.VideoTaskQueue(2, runner)
        self.addCleanup(queue.shutdown)
        self.addCleanup(release.set)
        ids, unused = queue.enqueue([tasks.TaskSpec(source=f'https://www.bilibili.com/video/BV11DjT6uEhc/?p={p}',
                                                     output_dir='test', auto_summary=False) for p in (1, 2)])
        end = time.monotonic() + 2
        while queue.get(ids[0]).status != 'failed' and time.monotonic() < end:
            time.sleep(.01)
        self.assertTrue(hasattr(queue, 'can_retry'), '缺少按集判断重试能力')
        self.assertTrue(queue.can_retry(ids[0]))
        self.assertIsNotNone(queue.retry(ids[0]))


class CollectionDialogTests(unittest.TestCase):
    def setUp(self):
        from desktop import ensure_application
        self.qt = ensure_application()
        from test_desktop import DesktopTests
        self.fixture = DesktopTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.host = self.fixture.app()

    def info(self, source='BV11DjT6uEhc'):
        from video_catalog import VideoCatalog
        client = Mock()
        client.request.return_value = {'code': 0, 'data': video_data()}
        return VideoCatalog(client).read(source)

    def dialog(self, info):
        self.assertIsNotNone(importlib.util.find_spec('collection_dialog'), '缺少分集选择窗口')
        from collection_dialog import CollectionDialog
        dialog = CollectionDialog(self.host, self.host, [info])
        self.addCleanup(dialog.reject)
        return dialog

    def test_multi_part_defaults_to_no_hidden_selection_and_supports_filtered_select_all(self):
        dialog = self.dialog(self.info())
        self.assertEqual(dialog.selected_parts(), [])
        dialog.search.setText('第2课')
        dialog.select_all_button.click()
        selected = dialog.selected_parts()
        self.assertEqual([part.page for part in selected], [2])
        self.assertEqual(self.host.queue.snapshots(), [])

    def test_explicit_p_link_preselects_only_that_part(self):
        dialog = self.dialog(self.info('https://www.bilibili.com/video/BV11DjT6uEhc/?p=2'))
        self.assertEqual([part.page for part in dialog.selected_parts()], [2])

    def test_new_dialog_preflight_failure_does_not_start_or_close_input(self):
        from ui_dialogs import NewVideoDialog
        dialog = NewVideoDialog(self.host, self.host.preferences)
        self.addCleanup(dialog.reject)
        dialog.link.setPlainText('BV11DjT6uEhc')
        with patch.object(self.host, 'work') as work:
            dialog.start_button.click()
            self.assertTrue(work.called, '提交前未检查目录')
            work.call_args.kwargs['on_error']('目录读取失败')
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertIn('目录读取失败', dialog.error.text())
        self.assertEqual(self.host.queue.snapshots(), [])

    def test_preflight_selected_part_is_returned_with_scope_and_no_task_started(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDialog
        from collection_dialog import CollectionDialog
        from ui_dialogs import NewVideoDialog
        dialog = NewVideoDialog(self.host, self.host.preferences)
        self.addCleanup(dialog.reject)
        dialog.link.setPlainText('BV11DjT6uEhc')
        info = self.info()
        def select_second(selection):
            selection.tree.topLevelItem(0).child(1).setCheckState(0, Qt.CheckState.Checked)
            return QDialog.DialogCode.Accepted
        with patch.object(self.host, 'work') as work, patch.object(CollectionDialog, 'exec', new=select_second):
            dialog.start()
            work.call_args.args[1](([info], set()))
        value = dialog.values()
        self.assertEqual(value['sources'], ['https://www.bilibili.com/video/BV11DjT6uEhc/?p=2'])
        self.assertEqual(value['parts'][0].cid, 102)
        self.assertEqual(len(value['collections']), 1)
        self.assertEqual(self.host.queue.snapshots(), [])

    def test_related_video_is_lazy_and_closed_callback_cannot_add_parts(self):
        from dataclasses import replace
        from video_catalog import RelatedVideo
        info = replace(self.info(), season_id=9, season_title='系列', season_count=2,
                       related=(RelatedVideo('BV11DjT6uEhc', '课程'), RelatedVideo('BV1k5np69EKv', '其他课')))
        dialog = self.dialog(info)
        with patch.object(self.host, 'work') as work:
            dialog.expand_related()
            work.call_args.args[1]([(9, info.related)])
            self.assertEqual(len(dialog.infos), 1)
            self.assertEqual(dialog.tree.topLevelItemCount(), 2)
            dialog.tree.topLevelItem(1).setExpanded(True)
            old_done = work.call_args.args[1]
            dialog.reject()
            other = replace(info, bvid='BV1k5np69EKv')
            old_done(other)
        self.assertEqual(len(dialog.infos), 1)

    def test_clicking_relative_note_link_in_index_opens_saved_note(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from desktop_window import Reader
        from collection_index import update_index, index_paths
        from video_catalog import collection_plan
        destination = self.fixture.create_result('目标分集', 'BV11DjT6uEhc')
        origin = self.fixture.create_result('当前阅读', 'BV1k5np69EKv')
        info = self.info()
        plan = collection_plan([info])
        output = self.fixture.root / 'notes'
        update_index(output, plan, info.parts[0].source, 'done', result=destination)
        path = index_paths(output, plan)[0]
        origin['collection_index'] = str(path)
        metadata = Path(origin['folder']) / 'video.json'
        metadata.write_text(json.dumps(origin), encoding='utf-8')
        self.host.open_result_path(metadata)
        def click_link(dialog):
            dialog.show()
            self.qt.processEvents()
            reader = dialog.findChild(Reader)
            cursor = reader.document().find('打开笔记')
            self.assertFalse(cursor.isNull())
            cursor.setPosition(cursor.selectionStart() + 1)
            point = reader.cursorRect(cursor).center()
            QTest.mouseClick(reader.viewport(), Qt.MouseButton.LeftButton, pos=point)
            self.qt.processEvents()
            dialog.close()
            return 0
        with patch('desktop_window.QDialog.exec', new=click_link):
            self.host.open_collection_index()
        self.assertEqual(self.host.current_path, Path(destination['folder']) / 'video.json')
