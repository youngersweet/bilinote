"""长视频分段归属、核对、直接组装及可恢复缓存。"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from model_settings import ModelSettings

if importlib.util.find_spec('long_summary'):
    import long_summary as long
else:
    long = None


def rows(count=40, page=3):
    return [{'id': f'P{page}-L{i:06d}', 'page': page, 'from': i * 30, 'to': i * 30 + 25,
             'content': f'知识点{i}。' + '本案例的成立条件与核心论据。' * 12} for i in range(count)]


def reply(data):
    chunk = data['chunk']
    owned = chunk['rows']
    point_id = chunk['id'] + '-K1'
    return {'status': 'complete', 'chunk_id': chunk['id'], 'last_source_id': owned[-1]['id'],
            'core_points': [{'id': point_id, 'text': '仅在满足条件时预计12%', 'source_ids': [owned[0]['id']]}],
            'sections': [{'title': '方案成立的条件', 'start_id': owned[0]['id'], 'end_id': owned[-1]['id'],
                          'point_ids': [point_id], 'paragraphs': [f'核心{chunk["id"]}：仅在满足条件时预计12%。']}],
            'omitted_points': []}


class LongSummaryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(long, '尚无逐段核对与直接组装流程')

    def run_fixture(self, folder, source, mutate=None, calls=None, settings=None, rules=None):
        calls = calls if calls is not None else []
        settings = settings or ModelSettings()

        def runner(prompt, target, **kwargs):
            data = json.loads(prompt.splitlines()[-1])
            calls.append(data)
            answer = reply(data)
            if mutate:
                mutate(data, answer)
            text = json.dumps(answer, ensure_ascii=False)
            Path(target).write_text(text, encoding='utf-8')
            Path(target).with_suffix(Path(target).suffix + '.run.json').write_text(json.dumps({
                'model': settings.model, 'reasoning_effort': settings.reasoning_effort,
                'requested_model': settings.model, 'requested_reasoning_effort': settings.reasoning_effort}), encoding='utf-8')
            return text

        return long.run_long_summary('测试课程', 'https://www.bilibili.com/video/BV1k5np69EKv/',
                                     source, {'sources': ['platform_subtitle'], 'parts': [
                                         {'page': 3, 'language': 'ai-zh', 'material_source': 'platform_subtitle'}],
                                         'scope': '仅所选分集'}, rules or {'writing_text': '保留核心内容', 'sha256': 'rules-v1'},
                                     '', settings, Path(folder), runner)

    def test_owned_rows_appear_once_context_stays_in_same_part(self):
        source = rows(50, 1) + rows(15, 3)
        chunks = long.make_chunks(source, max_chars=2200, max_seconds=300)
        owned = [row['id'] for chunk in chunks for row in chunk['rows']]
        self.assertEqual(owned, [row['id'] for row in source])
        self.assertTrue(any(chunk['before'] or chunk['after'] for chunk in chunks))
        for chunk in chunks:
            page = chunk['rows'][0]['page']
            self.assertTrue(all(row['page'] == page for row in chunk['rows'] + chunk['before'] + chunk['after']))
            self.assertTrue(set(row['id'] for row in chunk['rows']).isdisjoint(row['id'] for row in chunk['before'] + chunk['after']))

    def test_reviewed_sections_are_assembled_without_global_model_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            result = self.run_fixture(folder, rows(80), calls=calls)
            chunks = result['report']['chunks_total']
            self.assertGreater(chunks, 1)
            self.assertEqual(len(calls), chunks * 2)
            self.assertTrue(all(call['stage'] in ('draft', 'review') for call in calls))
            for i in range(1, chunks + 1):
                self.assertEqual(result['markdown'].count(f'核心C{i:04d}：仅在满足条件时预计12%。'), 1)
            self.assertIn('P3 00:00', result['markdown'])
            self.assertEqual(result['record']['assembly'], 'reviewed_sections')
            self.assertEqual(len(result['record']['calls']), chunks * 2)

    def test_second_run_uses_validated_cache_and_rule_change_invalidates_it(self):
        with tempfile.TemporaryDirectory() as folder:
            self.run_fixture(folder, rows(50))
            calls = []
            result = self.run_fixture(folder, rows(50), calls=calls)
            self.assertEqual(calls, [])
            self.assertGreater(result['report']['cache_hits'], 0)
            self.run_fixture(folder, rows(50), calls=calls,
                             rules={'writing_text': '新规则', 'sha256': 'rules-v2'})
            self.assertGreater(len(calls), 0)

    def test_invalid_context_reference_is_rejected_and_failure_keeps_good_chunks(self):
        def invalid(data, answer):
            if data['stage'] == 'review' and data['chunk']['id'] == 'C0002':
                answer['sections'][0]['start_id'] = 'P3-L999999'
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                self.run_fixture(folder, rows(80), mutate=invalid)
            calls = []
            result = self.run_fixture(folder, rows(80), calls=calls)
            self.assertTrue(calls)
            self.assertEqual(calls[0]['stage'], 'review')
            self.assertEqual(calls[0]['chunk']['id'], 'C0002')
            self.assertGreaterEqual(result['report']['cache_hits'], 3)

    def test_review_cannot_silently_drop_declared_core_point(self):
        def invalid(data, answer):
            if data['stage'] == 'review':
                answer['core_points'] = []
                answer['sections'] = []
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                self.run_fixture(folder, rows(10), mutate=invalid)

    def test_invalid_times_and_oversized_line_fail_without_clipping(self):
        for bad in (float('nan'), -1, float('inf')):
            source = rows(1)
            source[0]['from'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                long.make_chunks(source)
        source = rows(1)
        source[0]['content'] = '超长字幕' * 10000
        with self.assertRaises(ValueError):
            long.make_chunks(source)

    def test_same_paragraph_under_different_conditions_is_not_blindly_deleted(self):
        def conditions(data, answer):
            number = data['chunk']['id']
            answer['sections'][0]['title'] = f'适用条件{number}'
            answer['sections'][0]['paragraphs'] = ['满足本节条件时，结论成立。']
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_fixture(folder, rows(80), mutate=conditions)
            self.assertEqual(result['markdown'].count('满足本节条件时，结论成立。'), result['report']['chunks_total'])

    def test_broken_run_cache_is_recomputed_and_previous_tail_never_crosses_part(self):
        with tempfile.TemporaryDirectory() as folder:
            self.run_fixture(folder, rows(40, 1) + rows(5, 3))
            path = next((Path(folder) / 'summary-sections').glob('*draft*.reply.json.run.json'))
            path.write_text('[]', encoding='utf-8')
            calls = []
            result = self.run_fixture(folder, rows(40, 1) + rows(5, 3), calls=calls)
            self.assertGreater(len(calls), 0)
            self.assertEqual(result['report']['state'], 'complete')
            calls = []
            self.run_fixture(folder, rows(40, 1) + rows(5, 3), calls=calls,
                             rules={'writing_text': '新规则', 'sha256': 'tail-check'})
            p3 = next(data for data in calls if data['chunk']['rows'][0]['page'] == 3)
            self.assertEqual(p3['previous_note_tail'], '')

    def test_section_end_includes_overlapping_long_subtitle(self):
        source = rows(2)
        source[0].update({'from': 0, 'to': 90})
        source[1].update({'from': 5, 'to': 6})
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_fixture(folder, source)
            self.assertIn('P3 00:00—01:30', result['markdown'])

    def test_review_cannot_relabel_an_old_point_with_unrelated_source(self):
        def swap(data, answer):
            if data['stage'] == 'review':
                answer['core_points'][0]['source_ids'] = [data['chunk']['rows'][-1]['id']]
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                self.run_fixture(folder, rows(10), mutate=swap)

    def test_cached_incomplete_call_record_is_not_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            self.run_fixture(folder, rows(10))
            path = next((Path(folder) / 'summary-sections').glob('*draft*.reply.json.run.json'))
            data = json.loads(path.read_text(encoding='utf-8'))
            data.update(status='incomplete', finish_reason='length')
            path.write_text(json.dumps(data), encoding='utf-8')
            calls = []
            self.run_fixture(folder, rows(10), calls=calls)
            self.assertEqual(calls[0]['stage'], 'draft')

    def test_three_hour_material_preserves_every_owned_row_and_last_time(self):
        with tempfile.TemporaryDirectory() as folder:
            source, calls = rows(400), []
            result = self.run_fixture(folder, source, calls=calls)
            owned = [row['id'] for data in calls if data['stage'] == 'draft' for row in data['chunk']['rows']]
            self.assertEqual(owned, [row['id'] for row in source])
            self.assertEqual(result['report']['chunks_checked'], result['report']['chunks_total'])
            self.assertIn('03:19:55', result['markdown'])
            self.assertTrue(all(data['stage'] != 'final' for data in calls))

    def test_empty_first_chunk_of_new_part_cannot_revive_previous_part_context(self):
        def empty(data, answer):
            if data['chunk']['id'] == 'C0002':
                answer['core_points'] = []
                answer['sections'] = []
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            self.run_fixture(folder, rows(5, 1) + rows(80, 3), calls=calls, mutate=empty)
            third = next(data for data in calls if data['chunk']['id'] == 'C0003')
            self.assertEqual(third['previous_note_tail'], '')


if __name__ == '__main__':
    unittest.main()
