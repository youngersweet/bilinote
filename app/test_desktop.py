import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not importlib.util.find_spec('PySide6'):
            raise AssertionError('原生 Qt 界面尚未配置')
        from desktop import ensure_application
        cls.qt = ensure_application()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        from note_library import save_preferences
        self.preferences = self.root / 'prefs.json'
        save_preferences({'output_dir': str(self.root / 'notes')}, self.preferences)

    def create_result(self, name, bvid):
        folder = self.root / 'notes' / name
        folder.mkdir(parents=True)
        (folder / '字幕.txt').write_text('真实字幕', encoding='utf-8')
        note = folder / f'{name}—知识笔记.md'
        note.write_text(f'# {name}\n\n**核心观点**\n\n保留原意的正文。', encoding='utf-8')
        result = {'bvid': bvid, 'title': name, 'author': '作者', 'folder': str(folder), 'complete': True,
                  'source': f'https://www.bilibili.com/video/{bvid}/', 'duration': 60,
                  'pages': [{'page': 1, 'title': '第一集', 'duration': 60, 'segments': 1,
                             'paths': {'txt': str(folder / '字幕.txt')}}], 'summary_path': str(note)}
        (folder / 'video.json').write_text(json.dumps(result), encoding='utf-8')
        return result

    def app(self):
        from desktop import App
        app = App(check_online=False, preferences_path=self.preferences)
        self.addCleanup(app.close)
        return app

    def test_three_panes_render_existing_markdown_and_search(self):
        self.create_result('技术笔记', 'BV1k5np69EKv')
        self.create_result('软件方案', 'BV1L4np61EWq')
        app = self.app()
        self.assertEqual(app.splitter.count(), 2)
        self.assertEqual(app.tabs.count(), 3)
        self.assertEqual(app.history_list.count(), 2)
        self.assertIn('保留原意', app.note_text.toPlainText())
        self.assertNotIn('**核心观点**', app.note_text.toPlainText())
        app.search.setText('技术')
        self.assertEqual(app.history_list.count(), 1)

    def test_selection_clicks_toggle_notes_without_changing_reader(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.create_result('第一份', 'BV1k5np69EKv')
        self.create_result('第二份', 'BV1L4np61EWq')
        app = self.app()
        app.show()
        self.qt.processEvents()
        reading = app.current_path
        self.assertTrue(hasattr(app, 'selection_button'), '缺少列表选择入口')
        app.selection_button.click()
        item = app.history_list.item(1)
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        point = app.history_list.visualItemRect(item).center()
        QTest.mouseClick(app.history_list.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(app.checked_paths, {path})
        self.assertEqual(app.current_path, reading)
        QTest.mouseClick(app.history_list.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(app.checked_paths, set())
        self.assertFalse(app.delete_button.isEnabled())

    def test_select_all_is_limited_to_search_results_and_done_resets_selection(self):
        first = self.create_result('技术一', 'BV1k5np69EKv')
        self.create_result('生活二', 'BV1L4np61EWq')
        app = self.app()
        self.assertTrue(hasattr(app, 'selection_button'), '缺少列表选择入口')
        app.selection_button.click()
        app.search.setText('技术')
        app.select_all_button.click()
        self.assertEqual(app.checked_paths, {Path(first['folder']) / 'video.json'})
        app.refresh_library()
        self.assertEqual(len(app.checked_paths), 1)
        app.selection_button.click()
        self.assertEqual(app.checked_paths, set())

    def test_delete_cancel_keeps_files_then_confirm_removes_files_and_imports(self):
        from PySide6.QtWidgets import QMessageBox
        from note_library import save_preferences, load_preferences
        selected = self.create_result('要删除', 'BV1k5np69EKv')
        other = self.create_result('要保留', 'BV1L4np61EWq')
        selected_path = Path(selected['folder']) / 'video.json'
        other_path = Path(other['folder']) / 'video.json'
        save_preferences({'output_dir': str(self.root / 'notes'), 'imports': [str(selected_path), str(other_path)]}, self.preferences)
        app = self.app()
        self.assertTrue(hasattr(app, 'selection_button'), '缺少列表选择入口')
        app.open_result_path(selected_path)
        app.selection_button.click()
        app.search.setText('要删除')
        app.select_all_button.click()
        with patch('desktop_window.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
            app.delete_button.click()
        self.assertTrue(selected_path.is_file())
        self.assertTrue(app.selection_mode)
        with patch('desktop_window.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            app.delete_button.click()
        self.assertFalse(selected_path.parent.exists())
        self.assertTrue(other_path.is_file())
        self.assertEqual(load_preferences(self.preferences)['imports'], [str(other_path)])
        self.assertNotIn('要删除', app.note_text.toPlainText())
        self.assertEqual(app.checked_paths, set())
        self.assertFalse(app.selection_mode)
        self.assertTrue(app.selection_bar.isHidden())
        self.assertEqual(app.selection_button.text(), '选择')

    def test_active_video_is_excluded_from_select_all(self):
        from task_queue import TaskSpec
        active = self.create_result('工作中', 'BV1k5np69EKv')
        other = self.create_result('可删除', 'BV1L4np61EWq')
        app = self.app()
        release = threading.Event()
        def finish():
            release.set()
            app.queue.shutdown(wait=True)
            self.qt.processEvents()
        self.addCleanup(finish)
        app.queue.runner = lambda *args: (release.wait(3), active)[1]
        app.enqueue_tasks([TaskSpec(source=active['bvid'], output_dir='test', auto_summary=False)], show=False)
        app.selection_button.click()
        app.select_all_button.click()
        self.assertEqual(app.checked_paths, {Path(other['folder']) / 'video.json'})
        self.assertTrue((Path(active['folder']) / 'video.json').is_file())

    def test_partial_delete_keeps_failed_note_and_preserves_other_reader(self):
        from PySide6.QtWidgets import QMessageBox
        import shutil
        first = self.create_result('删第一份', 'BV1k5np69EKv')
        failed = self.create_result('删失败份', 'BV1QVnH6vEpQ')
        reader = self.create_result('保持阅读', 'BV1L4np61EWq')
        app = self.app()
        app.open_result_path(Path(reader['folder']) / 'video.json')
        app.selection_button.click()
        app.search.setText('删')
        app.select_all_button.click()
        real_delete = shutil.rmtree
        def delete_folder(path):
            if path == Path(failed['folder']):
                raise PermissionError('文件正在被其他程序使用')
            return real_delete(path)
        with patch('desktop_window.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes), \
             patch('desktop_window.QMessageBox.warning'), \
             patch('note_library.shutil.rmtree', side_effect=delete_folder):
            app.delete_button.click()
        self.assertFalse(Path(first['folder']).exists())
        self.assertTrue(Path(failed['folder']).is_dir())
        self.assertEqual(app.current_path, Path(reader['folder']) / 'video.json')
        self.assertIn('保持阅读', app.note_text.toPlainText())
        self.assertEqual(app.checked_paths, set())
        self.assertFalse(app.selection_mode)
        self.assertTrue(app.selection_bar.isHidden())

    def test_finished_background_note_does_not_replace_other_selected_video(self):
        first = self.create_result('正在处理', 'BV1k5np69EKv')
        second = self.create_result('当前阅读', 'BV1L4np61EWq')
        app = self.app()
        app.open_result_path(Path(second['folder']) / 'video.json')
        app.follow_task = False
        app.publish_result(first)
        self.assertEqual(app.result['bvid'], second['bvid'])
        self.assertIn('当前阅读', app.note_text.toPlainText())

    def test_new_video_dialog_keeps_advanced_options_collapsed(self):
        app = self.app()
        from ui_dialogs import NewVideoDialog
        dialog = NewVideoDialog(app, app.preferences)
        self.addCleanup(dialog.close)
        self.assertTrue(dialog.advanced.isHidden())
        dialog.link.setPlainText('https://www.bilibili.com/video/BV1k5np69EKv/')
        self.assertEqual(dialog.values()['mode'], 'auto')
        self.assertTrue(dialog.values()['auto_summary'])

    def test_startup_migrates_legacy_directory_and_updates_library_import(self):
        from note_library import save_preferences, load_preferences
        from video_folders import rebase_paths
        result = self.create_result('AI发现PHP漏洞', 'BV1QVnH6vEpQ')
        title_folder = Path(result['folder'])
        legacy = title_folder.with_name(result['bvid'])
        title_folder.rename(legacy)
        value = rebase_paths(result, {str(title_folder): str(legacy)})
        (legacy / 'video.json').write_text(json.dumps(value), encoding='utf-8')
        save_preferences({'output_dir': str(self.root / 'notes'), 'imports': [str(legacy / 'video.json')]}, self.preferences)
        app = self.app()
        self.assertFalse(legacy.exists())
        self.assertTrue(title_folder.is_dir())
        self.assertEqual(app.history_list.count(), 1)
        self.assertIn('保留原意', app.note_text.toPlainText())
        self.assertEqual(load_preferences(self.preferences)['imports'], [str(title_folder / 'video.json')])

    def test_old_qr_error_cannot_stop_new_login_polling(self):
        app = self.app()
        from ui_dialogs import AccountDialog
        with patch.object(app, 'work') as work:
            dialog = AccountDialog(app)
            self.addCleanup(dialog.reject)
            dialog.begin()
            old_error = work.call_args.kwargs['on_error']
            dialog.begin()
            dialog.hint.setText('新的二维码正在等待确认')
            dialog.timer.start()
            old_error('旧请求失败')
            self.assertTrue(dialog.timer.isActive())
            self.assertEqual(dialog.hint.text(), '新的二维码正在等待确认')

    def test_model_panel_tests_current_effort_and_recovers_from_error(self):
        app = self.app()
        from model_dialog import ModelSettingsPanel
        panel = ModelSettingsPanel(app)
        self.addCleanup(panel.close)
        panel.model_box.setCurrentText('gpt-6.1-sol')
        panel.effort_box.setCurrentIndex(panel.effort_box.findData('high'))
        with patch.object(app, 'work') as work, patch('model_dialog.check_connection') as check:
            panel.test()
            self.assertFalse(panel.test_button.isEnabled())
            work.call_args.args[0]()
            self.assertEqual(check.call_args.args[0].reasoning_effort, 'high')
            work.call_args.kwargs['on_error']('网络暂时不可用')
            self.assertTrue(panel.test_button.isEnabled())
            self.assertIn('网络暂时不可用', panel.status_text.toPlainText())

    def wait_ui(self, predicate):
        end = time.monotonic() + 3
        while not predicate() and time.monotonic() < end:
            self.qt.processEvents()
            time.sleep(.01)
        self.qt.processEvents()
        self.assertTrue(predicate())

    def test_batch_input_deduplicates_and_validates_each_line(self):
        from task_queue import parse_video_sources
        sources = parse_video_sources('BV1k5np69EKv\nhttps://www.bilibili.com/video/BV1L4np61EWq/\nBV1k5np69EKv')
        self.assertEqual(len(sources), 2)
        with self.assertRaisesRegex(ValueError, '第 2 行'):
            parse_video_sources('BV1k5np69EKv\nhttps://example.com')

    def test_queue_runs_while_user_reads_another_note_and_can_add_more(self):
        from task_queue import TaskSpec
        from task_dialog import TaskQueueDialog
        result = self.create_result('后台结果', 'BV1k5np69EKv')
        other = self.create_result('保持阅读', 'BV1L4np61EWq')
        third = self.create_result('后加的视频', 'BV1QVnH6vEpQ')
        app = self.app()
        release = threading.Event()
        self.addCleanup(release.set)
        entered = []
        def runner(spec, progress, materials):
            entered.append(spec.bvid)
            progress('正在模拟后台整理')
            release.wait(3)
            return result if spec.bvid == result['bvid'] else third
        app.queue.runner = runner
        ids, unused = app.enqueue_tasks([TaskSpec(source=result['bvid'], output_dir='test', auto_summary=False)], show=False)
        self.wait_ui(lambda: app.busy)
        self.assertTrue(app.new_button.isEnabled())
        app.open_result_path(Path(other['folder']) / 'video.json')
        app.enqueue_tasks([TaskSpec(source='BV1QVnH6vEpQ', output_dir='test', auto_summary=False)], show=False)
        # 在阅读期间加入更多任务，不能暗中改变阅读对象。
        self.wait_ui(lambda: len(entered) == 2)
        dialog = TaskQueueDialog(app)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.table.rowCount(), 2)
        release.set()
        self.wait_ui(lambda: not app.queue.has_pending())
        self.assertEqual(app.result['bvid'], other['bvid'])
        self.assertIn('保持阅读', app.note_text.toPlainText())
        self.assertEqual(app.queue.get(ids[0]).status, 'done')

    def test_importing_note_clears_auto_selected_background_task(self):
        from task_queue import TaskSpec
        app = self.app()
        release = threading.Event()
        self.addCleanup(release.set)
        result = self.create_result('后台任务', 'BV1k5np69EKv')
        imported = self.create_result('导入后阅读', 'BV1L4np61EWq')
        def runner(spec, progress, materials):
            release.wait(3)
            return result
        app.queue.runner = runner
        app.enqueue_tasks([TaskSpec(source=result['bvid'], output_dir='test', auto_summary=False)], show=False)
        with patch('desktop_window.QFileDialog.getOpenFileName', return_value=(str(Path(imported['folder']) / 'video.json'), '')):
            app.import_result()
        release.set()
        self.wait_ui(lambda: not app.queue.has_pending())
        self.assertEqual(app.result['bvid'], imported['bvid'])
        self.assertIsNone(app.selected_task_id)


if __name__ == '__main__':
    unittest.main()
