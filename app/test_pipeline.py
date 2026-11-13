import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import Mock, patch

if importlib.util.find_spec('content_pipeline'):
    import content_pipeline as pipeline
else:
    pipeline = None


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(pipeline, '多来源完整内容流程尚未实现')

    def test_subtitle_success_does_not_download_audio(self):
        calls = []
        def captions():
            calls.append('captions')
            return {'body': [{'from': 0, 'to': 2, 'content': '正文'}]}
        def audio():
            calls.append('audio')
            return None
        source, payload = pipeline.resolve_page([('platform', captions), ('asr', audio)])
        self.assertEqual(source, 'platform')
        self.assertEqual(calls, ['captions'])

    def test_empty_captions_fall_through_browser_to_full_asr(self):
        source, payload = pipeline.resolve_page([
            ('platform', lambda: {'body': []}),
            ('browser', lambda: None),
            ('asr', lambda: {'body': [{'from': 1, 'to': 9, 'content': '完整转写'}]})])
        self.assertEqual(source, 'asr')
        self.assertEqual(payload['body'][0]['content'], '完整转写')

    def test_description_cannot_replace_missing_video_body(self):
        with self.assertRaises(pipeline.MaterialUnavailable):
            pipeline.resolve_page([('browser', lambda: {'title': '标题', 'description': '简介'})])

    def test_access_errors_are_not_silently_treated_as_no_subtitles(self):
        def denied():
            raise PermissionError('无访问权限')
        with self.assertRaises(PermissionError):
            pipeline.resolve_page([('platform', denied), ('asr', lambda: {'body': [{'content': 'x'}]})])

    def test_player_subtitle_request_is_wbi_signed(self):
        class Client:
            def request(self, host, path, params=None):
                return {'code': 0, 'data': {'wbi_img': {
                    'img_url': 'https://i0.hdslb.com/bfs/wbi/' + 'a'*32 + '.png',
                    'sub_url': 'https://i0.hdslb.com/bfs/wbi/' + 'b'*32 + '.png'}}}
        signed = pipeline.signed_params(Client(), {'bvid': 'BV1k5np69EKv', 'aid': 1, 'cid': 2})
        self.assertEqual(len(signed['w_rid']), 32)
        self.assertIn('wts', signed)
        self.assertEqual(signed['bvid'], 'BV1k5np69EKv')

    def test_preview_audio_cannot_be_accepted_as_full_video(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preview.wav'
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b'\x00\x00' * 16000)
            with self.assertRaises(pipeline.MaterialUnavailable):
                pipeline.validate_media(path, 60)
            self.assertAlmostEqual(pipeline.validate_media(path, 1)['duration'], 1, places=1)

    def test_skill_rules_are_loaded_from_file_each_time(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'SKILL.md'
            path.write_text('---\nname: bili-note\n---\n第一版规则', encoding='utf-8')
            first = pipeline.skill_instructions(path)
            path.write_text('---\nname: bili-note\n---\n第二版规则', encoding='utf-8')
            second = pipeline.skill_instructions(path)
            self.assertIn('第一版规则', first['text'])
            self.assertIn('第二版规则', second['text'])
            self.assertNotEqual(first['sha256'], second['sha256'])

    def test_only_real_selected_frame_ids_can_be_used(self):
        frames = [{'evidence_id': 'KF-P01-01'}, {'evidence_id': 'KF-P01-02'}]
        selected = pipeline.select_frame_ids({'selected_frame_ids': ['KF-P01-02', 'FAKE', 'KF-P01-02']}, frames)
        self.assertEqual(selected, ['KF-P01-02'])

    def test_full_transcript_is_preserved_when_building_model_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'p01.json'
            path.write_text(json.dumps({'body': [{'from': i, 'to': i+1, 'content': str(i)} for i in range(100)]}), encoding='utf-8')
            result = {'folder': folder, 'pages': [{'page': 1, 'title': '测试', 'paths': {'json': str(path)}}]}
            text = pipeline.transcript_text(result)
            self.assertIn('] 0\n', text)
            self.assertTrue(text.endswith('] 99'))

    def test_usable_subtitles_stop_all_later_recognition_stages(self):
        video = {'aid': 1, 'title': '测试', 'owner': {'name': '作者'}, 'duration': 60,
                 'pages': [{'page': 1, 'cid': 2, 'part': '测试', 'duration': 60}]}
        client = Mock()
        client.request.return_value = {'code': 0, 'data': video}
        payload = {'body': [{'from': 0, 'to': 59, 'content': '完整而可用的字幕正文'}], '_language': 'ai-zh'}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(pipeline.ContentPipeline, 'platform_page', return_value=payload), \
             patch.object(pipeline.ContentPipeline, 'browser_page') as browser, \
             patch.object(pipeline.ContentPipeline, 'audio_page') as audio, \
             patch.object(pipeline.ContentPipeline, 'archive'), \
             patch.object(pipeline, 'run_skill_script', side_effect=pipeline.MaterialUnavailable('不应提取画面')) as script:
            result = pipeline.collect_video('BV1k5np69EKv', folder, client=client, visual=True)
            browser.assert_not_called()
            audio.assert_not_called()
            script.assert_not_called()
            self.assertEqual(result['visual']['state'], 'not_needed')
            self.assertEqual(Path(result['folder']).name, '测试')

    def test_browser_channel_uses_isolated_profile_and_loopback(self):
        self.assertTrue(hasattr(pipeline, 'browser_launch_command'), '缺少隔离浏览器入口')
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'msedge.exe'
            binary.touch()
            command = pipeline.browser_launch_command('BV1k5np69EKv', binary)
            self.assertIn('--remote-debugging-address=127.0.0.1', command)
            self.assertTrue(any(x.startswith('--user-data-dir=') and 'BiliNote' in x for x in command))
            self.assertNotIn('--no-sandbox', command)
            self.assertNotIn('--remote-allow-origins=*', command)
            self.assertEqual(command[-1], 'https://www.bilibili.com/video/BV1k5np69EKv/')

    def test_multipart_only_missing_part_uses_audio_and_visual_fallback(self):
        video = {'aid': 1, 'title': '两集', 'owner': {'name': '作者'}, 'duration': 120,
                 'pages': [{'page': n, 'cid': n, 'part': str(n), 'duration': 60} for n in (1, 2)]}
        client = Mock()
        client.request.return_value = {'code': 0, 'data': video}
        payload = {'body': [{'from': 0, 'to': 59, 'content': '完整正文'}], '_language': 'zh'}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(pipeline.ContentPipeline, 'platform_page', side_effect=[payload, None]), \
             patch.object(pipeline.ContentPipeline, 'browser_page', return_value=None), \
             patch.object(pipeline.ContentPipeline, 'audio_page', return_value=payload) as audio, \
             patch.object(pipeline.ContentPipeline, 'archive'), \
             patch.object(pipeline, 'prepare_visual') as visual:
            result = pipeline.collect_video('BV1k5np69EKv', folder, client=client)
            self.assertTrue(result['complete'])
            self.assertEqual([p['material_source'] for p in result['pages']], ['platform_subtitle', 'local_asr'])
            self.assertEqual(audio.call_count, 1)
            self.assertEqual(audio.call_args.args[1]['page'], 2)
            self.assertEqual(visual.call_args.args[2], '2')


if __name__ == '__main__':
    unittest.main()
