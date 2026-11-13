import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from bili_service import BiliClient, CredentialStore, LoginRequired


class FavoriteServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = BiliClient(CredentialStore(Path(self.temp.name) / 'session.dpapi'))

    def service(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('favorite_service'), '尚未实现登录收藏读取')
        from favorite_service import FavoriteService
        return FavoriteService(self.client)

    def nav(self):
        return {'code': 0, 'data': {'isLogin': True, 'mid': 123}}

    def folders(self):
        return {'code': 0, 'data': {'list': [{'id': 900, 'title': '私有学习收藏', 'media_count': 42, 'attr': 1}]}}

    def test_reads_logged_in_users_folders_and_paginated_video_resources(self):
        service = self.service()
        responses = [self.nav(), self.folders(), {'code': 0, 'data': {
            'info': {'media_count': 42}, 'has_more': True, 'medias': [
                {'id': 1, 'type': 2, 'bvid': 'BV1k5np69EKv', 'title': '学习视频',
                 'upper': {'name': '作者'}, 'duration': 986, 'attr': 0}]}}]
        with patch.object(self.client, 'request', side_effect=responses) as request:
            folders = service.folders()
            page = service.videos(900, page=2)
        self.assertEqual([(f.id, f.title, f.count) for f in folders], [(900, '私有学习收藏', 42)])
        self.assertEqual(page.items[0].source, 'https://www.bilibili.com/video/BV1k5np69EKv/')
        self.assertEqual(page.items[0].author, '作者')
        self.assertTrue(page.items[0].available)
        self.assertTrue(page.has_more)
        self.assertEqual(page.total, 42)
        calls = request.call_args_list
        self.assertEqual(calls[1].args[2]['up_mid'], 123)
        self.assertEqual(calls[2].args[2]['media_id'], 900)
        self.assertEqual(calls[2].args[2]['pn'], 2)
        self.assertEqual(calls[2].args[2]['ps'], 20)

    def test_logged_out_session_cannot_read_public_folders_as_own_account(self):
        service = self.service()
        with patch.object(self.client, 'request', return_value={'code': -101, 'data': {'isLogin': False}}):
            with self.assertRaises(LoginRequired):
                service.folders()

    def test_expired_session_and_permission_errors_are_not_empty_favorites(self):
        service = self.service()
        with patch.object(self.client, 'request', side_effect=[self.nav(), self.folders(), {'code': -101}]):
            service.folders()
            with self.assertRaises(LoginRequired):
                service.videos(900)
        with patch.object(self.client, 'request', side_effect=[self.nav(), self.folders(), {'code': -403}]):
            service.folders()
            with self.assertRaisesRegex(RuntimeError, '权限'):
                service.videos(900)

    def test_invalid_and_nonvideo_favorites_are_unavailable(self):
        service = self.service()
        invalid = {'code': 0, 'data': {'info': {'media_count': 2}, 'has_more': False, 'medias': [
            {'id': 1, 'type': 2, 'bvid': 'BV1k5np69EKv', 'title': '已失效视频', 'attr': 9},
            {'id': 2, 'type': 12, 'title': '音频收藏', 'attr': 0}]}}
        with patch.object(self.client, 'request', side_effect=[self.nav(), self.folders(), invalid]):
            service.folders()
            page = service.videos(900)
        self.assertFalse(any(v.available for v in page.items))
        self.assertEqual(page.items[0].reason, '视频已失效或不可访问')
        self.assertFalse(page.has_more)

    def test_empty_folder_and_unknown_folder_are_handled_separately(self):
        service = self.service()
        with patch.object(self.client, 'request', side_effect=[self.nav(), self.folders(), {
                'code': 0, 'data': {'info': {'media_count': 0}, 'medias': None, 'has_more': False}}]):
            service.folders()
            page = service.videos(900)
        self.assertEqual(len(page.items), 0)
        self.assertFalse(page.has_more)
        with self.assertRaises(ValueError):
            service.videos(901)

    def test_malformed_success_payload_is_not_reported_as_empty_favorites(self):
        service = self.service()
        with patch.object(self.client, 'request', side_effect=[self.nav(), {'code': 0, 'data': {}}]):
            with self.assertRaisesRegex(RuntimeError, '完整'):
                service.folders()
        with patch.object(self.client, 'request', side_effect=[self.nav(), self.folders(), {'code': 0, 'data': {}}]):
            service.folders()
            with self.assertRaisesRegex(RuntimeError, '完整'):
                service.videos(900)


class FavoriteDialogTests(unittest.TestCase):
    def setUp(self):
        from desktop import ensure_application
        ensure_application()
        from test_desktop import DesktopTests
        self.fixture = DesktopTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.host = self.fixture.app()
        self.calls = []
        self.work_patch = patch.object(self.host, 'work', side_effect=lambda function, done, **kw: self.calls.append((function, done, kw)))
        self.work_patch.start()
        self.addCleanup(self.work_patch.stop)

    def dialog(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('favorite_dialog'), '尚未实现收藏选择界面')
        from favorite_dialog import FavoriteDialog
        dialog = FavoriteDialog(self.host, self.host)
        self.addCleanup(dialog.reject)
        return dialog

    def item(self, bvid, title='收藏视频', available=True):
        from favorite_service import FavoriteVideo
        return FavoriteVideo(bvid=bvid, title=title, author='作者', duration=65,
                             available=available, reason='' if available else '已失效', resource_id=1)

    def folders_ready(self, dialog):
        from favorite_service import FavoriteFolder
        self.calls.pop(0)[1]([FavoriteFolder(10, '技术', 21), FavoriteFolder(20, '其他', 1)])

    def page_ready(self, items, has_more=False):
        from favorite_service import FavoritePage
        self.calls.pop(0)[1](FavoritePage(tuple(items), total=21, has_more=has_more))

    def test_select_across_folders_deduplicates_and_does_not_start_processing(self):
        dialog = self.dialog()
        self.folders_ready(dialog)
        self.page_ready([self.item('BV1k5np69EKv'), self.item('BV1L4np61EWq', available=False)], True)
        dialog.select_all_button.click()
        dialog.folders_list.setCurrentRow(1)
        self.page_ready([self.item('BV1k5np69EKv'), self.item('BV1QVnH6vEpQ')])
        dialog.select_all_button.click()
        self.assertEqual(set(dialog.sources()), {'https://www.bilibili.com/video/BV1k5np69EKv/',
                                                  'https://www.bilibili.com/video/BV1QVnH6vEpQ/'})
        self.assertEqual(len(self.host.queue.snapshots()), 0)
        self.assertTrue(dialog.use_button.isEnabled())

    def test_paging_preserves_checks_and_filters_only_loaded_titles(self):
        dialog = self.dialog()
        self.folders_ready(dialog)
        self.page_ready([self.item('BV1k5np69EKv', '技术视频')], True)
        dialog.select_all_button.click()
        dialog.more_button.click()
        self.assertEqual(len(self.calls), 1)
        self.page_ready([self.item('BV1QVnH6vEpQ', '生活视频')])
        self.assertEqual(len(dialog.sources()), 1)
        dialog.search.setText('生活')
        self.assertEqual(dialog.table.rowCount(), 1)
        dialog.select_all_button.click()
        self.assertEqual(len(dialog.sources()), 2)

    def test_stale_folder_result_and_closed_dialog_callbacks_are_ignored(self):
        dialog = self.dialog()
        self.folders_ready(dialog)
        stale_done = self.calls.pop(0)[1]
        dialog.folders_list.setCurrentRow(1)
        self.page_ready([self.item('BV1QVnH6vEpQ', '新收藏夹')])
        from favorite_service import FavoritePage
        stale_done(FavoritePage((self.item('BV1k5np69EKv', '旧收藏夹'),), 21, False))
        self.assertIn('新收藏夹', dialog.table.item(0, 1).text())
        dialog.reload_button.click()
        closed_done = self.calls.pop(0)[1]
        dialog.reject()
        closed_done([])
        self.assertTrue(dialog.closed)

    def test_login_failure_is_visible_and_can_retry(self):
        dialog = self.dialog()
        self.calls.pop(0)[2]['on_error']('登录已失效，请重新扫码')
        self.assertIn('登录已失效', dialog.status.text())
        self.assertTrue(dialog.reload_button.isEnabled())
        self.assertFalse(dialog.use_button.isEnabled())

    def test_failed_page_retry_keeps_existing_selection_and_requests_same_page(self):
        dialog = self.dialog()
        self.folders_ready(dialog)
        self.page_ready([self.item('BV1k5np69EKv')], True)
        dialog.select_all_button.click()
        dialog.more_button.click()
        failure = self.calls.pop(0)[2]['on_error']
        failure('连接失败，请重试')
        self.assertEqual(dialog.page, 1)
        self.assertEqual(len(dialog.sources()), 1)
        self.assertTrue(dialog.more_button.isEnabled())
        with patch.object(dialog.service, 'videos', return_value=None) as videos:
            dialog.more_button.click()
            self.calls.pop(0)[0]()
        self.assertEqual(videos.call_args.kwargs['page'], 2)

    def test_pending_video_is_unchecked_before_accepting_selection(self):
        from task_queue import TaskSpec
        import threading
        dialog = self.dialog()
        self.folders_ready(dialog)
        self.page_ready([self.item('BV1k5np69EKv')])
        dialog.select_all_button.click()
        release = threading.Event()
        def finish():
            release.set()
            self.host.queue.shutdown(wait=True)
        self.addCleanup(finish)
        self.host.queue.runner = lambda *args: (release.wait(3), None)[1]
        self.host.queue.enqueue([TaskSpec(source='BV1k5np69EKv', output_dir='test', auto_summary=False)])
        dialog.use_selection()
        self.assertEqual(dialog.sources(), [])
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertFalse(dialog.closed)

    def test_new_video_dialog_merges_chosen_favorites_into_existing_links(self):
        from PySide6.QtWidgets import QDialog
        from ui_dialogs import NewVideoDialog
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('favorite_dialog'), '尚未实现收藏选择界面')
        from favorite_dialog import FavoriteDialog
        dialog = NewVideoDialog(self.host, self.host.preferences)
        self.addCleanup(dialog.reject)
        dialog.link.setPlainText('BV1k5np69EKv')
        self.assertTrue(hasattr(dialog, 'favorites_button'), '新建入口缺少收藏导入')
        with patch.object(FavoriteDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
             patch.object(FavoriteDialog, 'sources', return_value=['https://www.bilibili.com/video/BV1k5np69EKv/',
                                                                    'https://www.bilibili.com/video/BV1QVnH6vEpQ/']):
            dialog.favorites_button.click()
        self.assertEqual(dialog.values()['sources'], ['BV1k5np69EKv', 'https://www.bilibili.com/video/BV1QVnH6vEpQ/'])
        self.assertEqual(self.host.queue.snapshots(), [])
