import importlib.util
from pathlib import Path
import threading
import time
import json
import tempfile
from unittest.mock import Mock, patch
import unittest


class QueueTests(unittest.TestCase):
    def test_eight_tasks_really_start_in_parallel(self):
        module = self.module()
        release = threading.Event()
        all_started = threading.Event()
        lock = threading.Lock()
        started = []
        def runner(spec, progress, materials):
            with lock:
                started.append(spec.page)
                if len(started) == 8:
                    all_started.set()
            release.wait(5)
            return {'complete': True}
        queue = module.VideoTaskQueue(limit=8, runner=runner)
        try:
            queue.enqueue([module.TaskSpec(source=f'https://www.bilibili.com/video/BV1k5np69EKv/?p={page}',
                                           output_dir='test', auto_summary=False) for page in range(1, 9)])
            self.assertTrue(all_started.wait(3), '8 个任务应在释放前全部启动')
        finally:
            release.set()
            queue.shutdown()

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('task_queue'), '尚未实现多视频任务队列')
        import task_queue
        return task_queue

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate(), '任务状态没有按预期变化')

    def test_two_tasks_run_together_and_failure_does_not_block_next(self):
        module = self.module()
        release = threading.Event()
        lock = threading.Lock()
        running, peak = 0, 0
        started = []
        def runner(spec, progress, materials):
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
                started.append(spec.source)
            release.wait(3)
            with lock:
                running -= 1
            if spec.source == 'BV1k5np69EKv':
                raise RuntimeError('仅这条失败')
            return {'complete': True, 'title': spec.source}
        queue = module.VideoTaskQueue(limit=2, runner=runner)
        self.addCleanup(queue.shutdown)
        self.addCleanup(release.set)
        specs = [module.TaskSpec(source=source, output_dir='test', auto_summary=False)
                 for source in ('BV1k5np69EKv', 'BV1L4np61EWq', 'BV1QVnH6vEpQ')]
        ids, duplicates = queue.enqueue(specs)
        self.wait_for(lambda: len(started) == 2)
        self.assertEqual(len([x for x in queue.snapshots() if x.status == 'queued']), 1)
        release.set()
        self.wait_for(lambda: not queue.has_pending())
        self.assertEqual(peak, 2)
        self.assertEqual([queue.get(x).status for x in ids], ['failed', 'done', 'done'])

    def test_duplicate_cancel_retry_and_settings_snapshot(self):
        module = self.module()
        from model_settings import ModelSettings
        release = threading.Event()
        seen = []
        def runner(spec, progress, materials):
            seen.append(spec)
            release.wait(3)
            return {'complete': True, 'title': spec.source}
        queue = module.VideoTaskQueue(limit=1, runner=runner)
        self.addCleanup(queue.shutdown)
        self.addCleanup(release.set)
        spec = module.TaskSpec(source='BV1k5np69EKv', output_dir='test', settings=ModelSettings('gpt-6.1-sol','high'))
        ids, duplicates = queue.enqueue([spec, spec, module.TaskSpec(source='BV1L4np61EWq', output_dir='test', auto_summary=False)])
        self.assertEqual(len(ids), 2)
        self.assertEqual(duplicates, ['BV1k5np69EKv'])
        self.assertTrue(queue.cancel(ids[1]))
        self.assertFalse(queue.cancel(ids[0]))
        release.set()
        self.wait_for(lambda: not queue.has_pending())
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].settings.reasoning_effort, 'high')
        self.assertEqual(queue.get(ids[1]).status, 'cancelled')

    def test_partial_materials_survive_failure_and_retry(self):
        module = self.module()
        attempt = 0
        def runner(spec, progress, materials):
            nonlocal attempt
            attempt += 1
            result = {'complete': True, 'title': '已取得的字幕', 'folder': 'saved'}
            materials(result)
            if attempt == 1:
                raise RuntimeError('模型临时失败')
            return result
        queue = module.VideoTaskQueue(limit=1, runner=runner)
        self.addCleanup(queue.shutdown)
        ids, unused = queue.enqueue([module.TaskSpec(source='BV1k5np69EKv', output_dir='test', auto_summary=False)])
        self.wait_for(lambda: not queue.has_pending())
        self.assertEqual(queue.get(ids[0]).result['folder'], 'saved')
        retried = queue.retry(ids[0])
        self.wait_for(lambda: not queue.has_pending())
        self.assertEqual(queue.get(retried).status, 'done')

    def test_default_pipeline_runs_two_videos_with_same_title_without_mixing_files(self):
        module = self.module()
        from content_pipeline import ContentPipeline, save_json
        barrier = threading.Barrier(2)
        def client_factory():
            client = Mock()
            client.request.return_value = {'code': 0, 'data': {'aid': 1, 'title': '同名标题', 'duration': 60,
                'owner': {'name': '作者'}, 'pages': [{'page': 1, 'cid': 2, 'part': '正文', 'duration': 60}]}}
            return client
        def summarize(result, progress, settings=None):
            barrier.wait(3)
            note = Path(result['folder']) / '同名标题—知识笔记.md'
            note.write_text(result['bvid'], encoding='utf-8')
            result['summary_path'] = str(note)
            save_json(Path(result['folder']) / 'video.json', result)
        payload = {'body': [{'from': 0, 'to': 59, 'content': '完整原始字幕'}], '_language': 'zh'}
        with tempfile.TemporaryDirectory() as name, \
             patch.object(ContentPipeline, 'platform_page', return_value=payload), \
             patch.object(ContentPipeline, 'browser_page') as browser, \
             patch.object(ContentPipeline, 'audio_page') as audio, \
             patch.object(ContentPipeline, 'archive'), \
             patch('bili_service.summarize', side_effect=summarize):
            queue = module.VideoTaskQueue(limit=2, runner=lambda spec, progress, materials:
                module.process_video_task(spec, progress, materials, client_factory))
            try:
                ids, unused = queue.enqueue([module.TaskSpec(source=bvid, output_dir=name)
                                            for bvid in ('BV1k5np69EKv', 'BV1L4np61EWq')])
                self.wait_for(lambda: not queue.has_pending())
                results = [queue.get(identity).result for identity in ids]
                self.assertTrue(all(queue.get(identity).status == 'done' for identity in ids))
                self.assertNotEqual(results[0]['folder'], results[1]['folder'])
                for result in results:
                    self.assertEqual(Path(result['summary_path']).read_text(encoding='utf-8'), result['bvid'])
                    self.assertEqual(Path(result['pages'][0]['paths']['txt']).read_text(encoding='utf-8').strip(), '完整原始字幕')
                browser.assert_not_called()
                audio.assert_not_called()
            finally:
                queue.shutdown()

    def test_lowering_limit_does_not_start_next_until_active_tasks_finish(self):
        module = self.module()
        sources = ('BV1k5np69EKv', 'BV1L4np61EWq', 'BV1QVnH6vEpQ')
        started = {source: threading.Event() for source in sources}
        releases = {source: threading.Event() for source in sources}
        def runner(spec, progress, materials):
            started[spec.source].set()
            releases[spec.source].wait(3)
            return {'complete': True, 'title': spec.source}
        queue = module.VideoTaskQueue(limit=2, runner=runner)
        self.addCleanup(queue.shutdown)
        self.addCleanup(lambda: [event.set() for event in releases.values()])
        ids, unused = queue.enqueue([module.TaskSpec(source=source, output_dir='test', auto_summary=False) for source in sources])
        self.wait_for(lambda: started[sources[0]].is_set() and started[sources[1]].is_set())
        queue.set_limit(1)
        releases[sources[0]].set()
        self.wait_for(lambda: queue.get(ids[0]).status == 'done')
        self.assertFalse(started[sources[2]].wait(.05))
        releases[sources[1]].set()
        self.wait_for(lambda: started[sources[2]].is_set())
        releases[sources[2]].set()
        self.wait_for(lambda: not queue.has_pending())


if __name__ == '__main__':
    unittest.main()
