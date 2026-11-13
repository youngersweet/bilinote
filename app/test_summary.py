"""总结输入不能丢掉分P、时间依据或长视频的实际覆盖范围。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bili_service as service
from model_settings import ModelSettings


class SummaryTests(unittest.TestCase):
    def summarize_fixture(self, folder, sources):
        pages = []
        for page, source, language, body in sources:
            path = Path(folder) / f'p{page}.json'
            path.write_text(json.dumps({'body': body}), encoding='utf-8')
            pages.append({'page': page, 'title': f'分集{page}', 'duration': max(row['to'] for row in body),
                          'material_source': source, 'language': language,
                          'paths': {'json': str(path)}})
        result = {'complete': True, 'folder': folder, 'title': '测试视频',
                  'source': 'https://www.bilibili.com/video/BV1k5np69EKv/',
                  'pages_total': len(pages), 'pages': pages, 'visual': {'state': 'off'}}
        requests = []
        settings = ModelSettings()

        def model(prompt, target, **kwargs):
            data = json.loads(prompt.splitlines()[-1])
            requests.append(data)
            if 'chunk' in data:
                from test_long_summary import reply
                note = json.dumps(reply(data), ensure_ascii=False)
            else:
                note = f'第{len(requests)}份整理材料'
            Path(target).write_text(note, encoding='utf-8')
            Path(str(target) + '.run.json').write_text(
                json.dumps({**settings.to_dict(), 'requested_model': settings.model,
                            'requested_reasoning_effort': settings.reasoning_effort}), encoding='utf-8')
            return note

        with patch.object(service, 'run_codex', side_effect=model):
            service.summarize(result, settings=settings)
        return requests

    def test_subtitles_keep_part_local_time_and_source_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            requests = self.summarize_fixture(folder, [
                (1, 'platform_subtitle', 'ai-zh', [
                    {'from': 1.2, 'to': 3.8, 'content': '作者认为可能节省时间'},
                    {'from': 3723.6, 'to': 3729.1, 'content': '但没有作独立验证'}]),
                (2, 'local_asr', 'zh', [
                    {'from': 0.0, 'to': 5.5, 'content': '第二集从零开始'}])])
            data = requests[0]
            draft_rows = [row for request in requests if request['stage'] == 'draft' for row in request['chunk']['rows']]
            self.assertEqual([(row['page'], row['from'], row['to']) for row in draft_rows],
                             [(1, 1.2, 3.8), (1, 3723.6, 3729.1), (2, 0.0, 5.5)])
            self.assertEqual(data['coverage']['parts'][0]['language'], 'ai-zh')
            self.assertEqual(data['coverage']['parts'][1]['material_source'], 'local_asr')
            self.assertEqual(data['coverage']['parts'][0]['first_start'], 1.2)
            self.assertEqual(data['coverage']['parts'][0]['last_end'], 3729.1)
            self.assertFalse(data['coverage']['full_text_supplied'])

    def test_short_video_stays_single_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            requests = self.summarize_fixture(folder, [(1, 'platform_subtitle', 'ai-zh', [
                {'from': 0, 'to': 59, 'content': '短视频中的核心观点'}])])
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]['stage'], 'final')
            self.assertTrue(requests[0]['coverage']['full_text_supplied'])

    def test_long_video_passes_every_row_once_with_part_context(self):
        with tempfile.TemporaryDirectory() as folder:
            sources = [(part, 'platform_subtitle', 'ai-zh', [
                {'from': i * 10, 'to': i * 10 + 9,
                 'content': f'ROW-{part}-{i:03d} ' + '保留案例的原始条件和限定' * 13}
                for i in range(160)]) for part in (1, 2)]
            requests = self.summarize_fixture(folder, sources)
            self.assertGreater(len(requests), 2)
            self.assertTrue(all(data['stage'] in ('draft', 'review') for data in requests))
            segments = [data for data in requests if data['stage'] == 'draft']
            combined = '\n'.join(row['content'] for data in segments for row in data['chunk']['rows'])
            for part in (1, 2):
                for i in range(160):
                    self.assertEqual(combined.count(f'ROW-{part}-{i:03d} '), 1)
            for data in segments:
                for row in data['chunk']['rows']:
                    self.assertTrue(row['id'].startswith(f'P{row["page"]}-L'))

    def test_long_video_is_assembled_from_reviewed_sections_without_final_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            requests = self.summarize_fixture(folder, [(3, 'platform_subtitle', 'zh', [
                {'from': i, 'to': i + 1, 'content': '详细技术方案与案例' * 100}
                for i in range(70)])])
            self.assertTrue(all(data['stage'] in ('draft', 'review') for data in requests))
            self.assertEqual(requests[-1]['stage'], 'review')
            path = Path(folder) / '测试视频—知识笔记.md'
            record = json.loads(path.with_suffix('.md.run.json').read_text(encoding='utf-8'))
            self.assertEqual(record['assembly'], 'reviewed_sections')
            self.assertEqual(record['report']['state'], 'complete')
            self.assertIn('P3 00:00', path.read_text(encoding='utf-8'))

    def test_failed_review_keeps_existing_note_and_model_record(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'p1.json'
            source.write_text(json.dumps({'body': [{'from': i * 30, 'to': i * 30 + 25,
                                                   'content': '核心观点与条件。' * 25} for i in range(100)]}), encoding='utf-8')
            result = {'complete': True, 'folder': folder, 'title': '测试视频', 'source': 'BV1k5np69EKv',
                      'pages': [{'page': 1, 'title': '正文', 'paths': {'json': str(source)}}]}
            note = Path(folder) / '测试视频—知识笔记.md'
            record = note.with_suffix('.md.run.json')
            note.write_text('旧笔记完整内容', encoding='utf-8')
            record.write_text('{"old":true}', encoding='utf-8')
            def model(prompt, target, **kwargs):
                from test_long_summary import reply
                data = json.loads(prompt.splitlines()[-1])
                answer = reply(data)
                if data['stage'] == 'review' and data['chunk']['id'] == 'C0002':
                    answer['last_source_id'] = 'FAKE'
                content = json.dumps(answer, ensure_ascii=False)
                Path(target).write_text(content, encoding='utf-8')
                settings = ModelSettings()
                Path(target).with_suffix('.json.run.json').write_text(json.dumps({
                    **settings.to_dict(), 'requested_model': settings.model,
                    'requested_reasoning_effort': settings.reasoning_effort}), encoding='utf-8')
                return content
            with patch.object(service, 'run_codex', side_effect=model), self.assertRaises(RuntimeError):
                service.summarize(result, settings=ModelSettings())
            self.assertEqual(note.read_text(encoding='utf-8'), '旧笔记完整内容')
            self.assertEqual(record.read_text(encoding='utf-8'), '{"old":true}')


if __name__ == '__main__':
    unittest.main()
