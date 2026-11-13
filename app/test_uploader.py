import importlib.util
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from bili_service import BiliClient, CredentialStore


class UploaderServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = BiliClient(CredentialStore(Path(self.tmp.name) / 'auth.dpapi'))

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('uploader_service'), '尚未实现UP主导入')
        import uploader_service
        return uploader_service

    def nav(self):
        return {'code': 0, 'data': {'wbi_img': {
            'img_url': 'https://i0.hdslb.com/bfs/wbi/0123456789abcdef0123456789abcdef.png',
            'sub_url': 'https://i0.hdslb.com/bfs/wbi/fedcba9876543210fedcba9876543210.png'}}}

    def test_parses_uid_and_space_url_without_guessing_names(self):
        parse = self.module().parse_uploader
        for value in ('477527885', 'UID:477527885', 'https://space.bilibili.com/477527885/upload/video'):
            self.assertEqual(parse(value), 477527885)
        self.assertIsNone(parse('Coder-Dawn'))
        for bad in ('https://evil.example/477527885', 'https://space.bilibili.com.evil.example/477527885', '0', ''):
            with self.assertRaises(ValueError):
                parse(bad)

    def test_name_search_returns_candidates_and_uses_modern_signed_route(self):
        service = self.module().UploaderService(self.client)
        result = {'code': 0, 'data': {'result': [
            {'mid': 123, 'uname': '<em class="keyword">同名</em>UP', 'usign': '课程作者', 'fans': 12, 'videos': 4, 'upic': '//i0.hdslb.com/a.jpg'},
            {'mid': 456, 'uname': '同名UP', 'fans': 3, 'videos': 2}], 'numResults': 2, 'numPages': 1, 'page': 1}}
        with patch.object(self.client, 'request', side_effect=[self.nav(), result]) as request:
            page = service.search('同名UP')
        self.assertEqual([p.mid for p in page.items], [123, 456])
        self.assertEqual(page.items[0].name, '同名UP')
        self.assertFalse(page.has_more)
        self.assertEqual(request.call_args.args[1], '/x/web-interface/wbi/search/type')
        params = request.call_args.args[2]
        self.assertEqual(params['search_type'], 'bili_user')
        self.assertEqual(params['keyword'], '同名UP')
        self.assertRegex(params['w_rid'], r'^[a-f0-9]{32}$')

    def test_uid_profile_is_exact_and_does_not_use_name_search(self):
        service = self.module().UploaderService(self.client)
        data = {'code': 0, 'data': {'card': {'mid': '123', 'name': 'UP主', 'fans': 5, 'sign': '简介'}, 'archive_count': 38}}
        with patch.object(self.client, 'request', return_value=data) as request:
            page = service.search('123')
        self.assertEqual(page.items[0].mid, 123)
        self.assertEqual(page.items[0].video_count, 38)
        self.assertEqual(request.call_args.args[1], '/x/web-interface/card')

    def test_upload_pages_preserve_published_time_and_have_next_page(self):
        service = self.module().UploaderService(self.client)
        data = {'code': 0, 'data': {'list': {'vlist': [
            {'bvid': 'BV11DjT6uEhc', 'aid': 1, 'title': '课程', 'author': '作者', 'mid': 123,
             'length': '12:17:28', 'created': 1700000000}]}, 'page': {'pn': 1, 'ps': 30, 'count': 38}, 'is_risk': False}}
        with patch.object(self.client, 'request', side_effect=[self.nav(), data]) as request:
            page = service.videos(123)
        self.assertTrue(page.has_more)
        self.assertEqual(page.total, 38)
        self.assertEqual(page.items[0].duration, 44248)
        self.assertEqual(page.items[0].published, 1700000000)
        self.assertEqual(page.items[0].source, 'https://www.bilibili.com/video/BV11DjT6uEhc/')
        self.assertEqual(request.call_args.args[2]['mid'], '123')

    def test_risk_and_malformed_data_are_not_empty_uploads(self):
        for data in ({'code': -352}, {'code': 0, 'data': {'is_risk': True}}, {'code': 0, 'data': {}}):
            service = self.module().UploaderService(self.client)
            with patch.object(self.client, 'request', side_effect=[self.nav(), data]):
                with self.assertRaises(RuntimeError):
                    service.videos(123)

    def test_a_valid_empty_upload_list_is_distinct_from_request_failure(self):
        service = self.module().UploaderService(self.client)
        with patch.object(self.client, 'request', side_effect=[self.nav(), {'code': 0, 'data': {
                'list': {'vlist': []}, 'page': {'pn': 1, 'ps': 30, 'count': 0}, 'is_risk': False}}]):
            page = service.videos(123)
        self.assertEqual(page.items, ())
        self.assertFalse(page.has_more)

    def test_collaboration_post_can_have_another_original_author(self):
        data = {'code': 0, 'data': {'list': {'vlist': [
            {'bvid': 'BV11DjT6uEhc', 'title': '合作投稿', 'mid': 456, 'author': '原作者',
             'is_union_video': 1, 'length': '02:03'}]}, 'page': {'count': 1}}}
        service = self.module().UploaderService(self.client)
        with patch.object(self.client, 'request', side_effect=[self.nav(), data]):
            page = service.videos(123)
        self.assertEqual(page.items[0].author, '原作者')
        self.assertTrue(page.items[0].collaboration)
        data['data']['list']['vlist'][0]['is_union_video'] = 0
        with patch.object(self.client, 'request', return_value=data):
            with self.assertRaisesRegex(RuntimeError, '匹配'):
                service.videos(123)


class UploaderDialogTests(unittest.TestCase):
    def setUp(self):
        from desktop import ensure_application
        ensure_application()
        from test_desktop import DesktopTests
        self.fixture = DesktopTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.host = self.fixture.app()
        self.calls = []
        worker = patch.object(self.host, 'work', side_effect=lambda fn, done, **kw: self.calls.append((fn, done, kw)))
        worker.start()
        self.addCleanup(worker.stop)

    def dialog(self):
        self.assertIsNotNone(importlib.util.find_spec('uploader_dialog'), '尚未实现UP主选择界面')
        from uploader_dialog import UploaderDialog
        dialog = UploaderDialog(self.host, self.host)
        self.addCleanup(dialog.reject)
        return dialog

    def profiles(self, dialog):
        from uploader_service import Uploader, UploaderPage
        dialog.query.setText('同名UP')
        dialog.search_button.click()
        self.calls.pop(0)[1](UploaderPage((Uploader(123, '同名UP', '', '第一位', 10, 38),
                                         Uploader(456, '同名UP', '', '第二位', 20, 1)), 2, False))

    def videos_ready(self, ids, has_more=False):
        from uploader_service import UploadPage, UploadVideo
        values = tuple(UploadVideo(bvid, bvid, '作者', 65, 1700000000, True, '') for bvid in ids)
        self.calls.pop(0)[1](UploadPage(values, 38, has_more))

    def test_confirm_uid_select_uploads_and_paginate_without_starting_tasks(self):
        dialog = self.dialog()
        self.profiles(dialog)
        self.videos_ready(['BV11DjT6uEhc'], True)
        self.assertIn('123', dialog.identity.text())
        self.assertEqual(dialog.sources(), [])
        dialog.select_all_button.click()
        dialog.more_button.click()
        self.videos_ready(['BV1k5np69EKv'])
        self.assertEqual(len(dialog.sources()), 1)
        self.assertEqual(self.host.queue.snapshots(), [])

    def test_switch_up_ignores_old_page_and_closed_dialog_ignores_callbacks(self):
        dialog = self.dialog()
        self.profiles(dialog)
        stale = self.calls.pop(0)[1]
        dialog.accounts_list.setCurrentRow(1)
        self.videos_ready(['BV1k5np69EKv'])
        from uploader_service import UploadPage, UploadVideo
        stale(UploadPage((UploadVideo('BV11DjT6uEhc', '过期页面', '', 0, 0, True, ''),), 1, False))
        self.assertEqual(dialog.rows[0].bvid, 'BV1k5np69EKv')
        dialog.query.setText('新搜索')
        dialog.search_button.click()
        done = self.calls.pop(0)[1]
        dialog.reject()
        done(None)
        self.assertTrue(dialog.closed)

    def test_failed_page_preserves_selection_and_retries_same_page(self):
        dialog = self.dialog()
        self.profiles(dialog)
        self.videos_ready(['BV11DjT6uEhc'], True)
        dialog.select_all_button.click()
        dialog.more_button.click()
        self.calls.pop(0)[2]['on_error']('B站暂时限制读取')
        self.assertEqual(dialog.page, 1)
        self.assertEqual(len(dialog.sources()), 1)
        self.assertTrue(dialog.more_button.isEnabled())
        with patch.object(dialog.service, 'videos') as videos:
            dialog.more_button.click()
            self.calls.pop(0)[0]()
        self.assertEqual(videos.call_args.args, (123,))
        self.assertEqual(videos.call_args.kwargs['page'], 2)

    def test_select_all_only_checks_loaded_filtered_uploads(self):
        dialog = self.dialog()
        self.profiles(dialog)
        self.videos_ready(['BV11DjT6uEhc', 'BV1k5np69EKv'], True)
        dialog.filter.setText('BV1k5')
        dialog.select_all_button.click()
        self.assertEqual(dialog.sources(), ['https://www.bilibili.com/video/BV1k5np69EKv/'])
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertTrue(dialog.has_more)
        self.assertEqual(self.host.queue.snapshots(), [])

    def test_new_video_dialog_merges_up_links_and_preserves_existing_input(self):
        dialog = self.dialog()
        from PySide6.QtWidgets import QDialog
        from ui_dialogs import NewVideoDialog
        from uploader_dialog import UploaderDialog
        source = NewVideoDialog(self.host, self.host.preferences)
        self.addCleanup(source.reject)
        source.link.setPlainText('BV11DjT6uEhc')
        self.assertTrue(hasattr(source, 'uploader_button'), '新建窗口缺少UP主导入入口')
        with patch.object(UploaderDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
             patch.object(UploaderDialog, 'sources', return_value=['https://www.bilibili.com/video/BV11DjT6uEhc/',
                                                                 'https://www.bilibili.com/video/BV1k5np69EKv/']):
            source.uploader_button.click()
        self.assertEqual(source.values()['sources'], ['BV11DjT6uEhc', 'https://www.bilibili.com/video/BV1k5np69EKv/'])
