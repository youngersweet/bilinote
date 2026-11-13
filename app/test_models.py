import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import bili_service as service


class ModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        diagnostics = patch.object(service, 'DATA_DIR', Path(temporary.name))
        diagnostics.start()
        self.addCleanup(diagnostics.stop)
        codex_home = patch.dict(service.os.environ, {'CODEX_HOME': temporary.name})
        codex_home.start()
        self.addCleanup(codex_home.stop)

    def settings_module(self):
        self.assertTrue(Path(__file__).with_name('model_settings.py').exists(), '尚无独立模型设置')
        import model_settings
        return model_settings

    def test_settings_survive_restart_and_reject_corruption(self):
        module = self.settings_module()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            self.assertEqual(module.load_settings(path).model, 'gpt-6.1-sol')
            value = module.ModelSettings('gpt-6.1-sol', 'high')
            module.save_settings(value, path)
            self.assertEqual(module.load_settings(path), value)
            path.write_text('{broken', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '配置'):
                module.load_settings(path)

    def test_desktop_codex_is_preferred_over_older_path_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            native = root / 'OpenAI/Codex/bin/current/codex.exe'
            native.parent.mkdir(parents=True)
            native.touch()
            with patch.dict(service.os.environ, {'LOCALAPPDATA': folder}), \
                 patch.object(service.Path, 'home', return_value=root / 'unused-home'), \
                 patch.object(service.shutil, 'which', return_value='C:/older/codex.exe'):
                self.assertEqual(service.codex_command(), [str(native)])

    def test_runtime_error_warning_does_not_misreport_model_unavailable(self):
        module = self.settings_module()
        stderr = ('WARN snapshot: Shell snapshot not supported yet for PowerShell\n'
                  'ERROR: connection reset by peer')
        with tempfile.TemporaryDirectory() as folder, patch.object(service, 'codex_command', return_value=['codex.exe']), \
             patch.object(service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', stderr)):
            with self.assertRaises(RuntimeError) as raised:
                service.run_codex('正文', Path(folder) / 'note.md', settings=module.ModelSettings())
            self.assertNotIn('模型或推理强度不可用', str(raised.exception))
            self.assertIn('connection reset by peer', str(raised.exception))

    def test_model_error_keeps_server_reason_and_safe_diagnostics(self):
        module = self.settings_module()
        payload = {'type': 'error', 'status': 400, 'error': {'type': 'invalid_request_error',
                   'message': "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account."}}
        with tempfile.TemporaryDirectory() as folder, patch.object(service, 'codex_command', return_value=['codex.exe']), \
             patch.object(service, 'DATA_DIR', Path(folder) / 'app-data'), \
             patch.object(service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'ERROR: ' + json.dumps(payload))):
            with self.assertRaises(RuntimeError) as raised:
                service.run_codex('不能出现在日志里的视频正文', Path(folder) / 'note.md', settings=module.ModelSettings())
            self.assertIn('ChatGPT account', str(raised.exception))
            record = json.loads((service.DATA_DIR / 'diagnostics/last-model-error.json').read_text(encoding='utf-8'))
            self.assertEqual(record['codex_executable'], 'codex.exe')
            self.assertNotIn('不能出现在日志里的视频正文', json.dumps(record, ensure_ascii=False))

    def test_invalid_model_and_unsupported_reasoning_are_rejected(self):
        module = self.settings_module()
        for model, effort in [('', 'medium'), ('--override', 'medium'), ('gpt-6.1-sol', 'invented')]:
            with self.assertRaises(ValueError):
                module.ModelSettings(model, effort)
        catalog = [{'slug': 'restricted-model', 'supported_reasoning_levels': [{'effort': 'low'}]}]
        with patch.object(module, 'model_catalog', return_value=catalog):
            with self.assertRaisesRegex(ValueError, '推理'):
                module.ModelSettings('restricted-model', 'high')

    def test_codex_receives_pinned_settings_and_records_actual_values(self):
        module = self.settings_module()
        def invoke(command, **kwargs):
            Path(command[command.index('-o') + 1]).write_text('已获取完整正文。' * 12, encoding='utf-8')
            self.assertEqual(command[command.index('--model') + 1], 'gpt-6.1-sol')
            self.assertIn('model_reasoning_effort="medium"', command)
            self.assertIn('--ignore-user-config', command)
            return subprocess.CompletedProcess(command, 0, '', 'model: gpt-6.1-sol\nreasoning effort: medium\n')
        with tempfile.TemporaryDirectory() as folder, patch.object(service, 'codex_command', return_value=['codex.exe']), \
             patch.object(service.subprocess, 'run', side_effect=invoke):
            target = Path(folder) / 'note.md'
            service.run_codex('完整正文', target, settings=module.ModelSettings())
            record = json.loads(target.with_suffix('.md.run.json').read_text(encoding='utf-8'))
            self.assertEqual(record['requested_model'], record['model'])
            self.assertEqual(record['reasoning_effort'], 'medium')
            self.assertEqual(record['requested_reasoning_effort'], 'medium')

    def test_model_mismatch_does_not_replace_old_note(self):
        module = self.settings_module()
        def invoke(command, **kwargs):
            Path(command[command.index('-o') + 1]).write_text('其他模型输出。' * 12, encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', 'model: unexpected-model\n')
        with tempfile.TemporaryDirectory() as folder, patch.object(service, 'codex_command', return_value=['codex.exe']), \
             patch.object(service.subprocess, 'run', side_effect=invoke):
            target = Path(folder) / 'note.md'
            target.write_text('原有笔记', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, '模型'):
                service.run_codex('正文', target, settings=module.ModelSettings())
            self.assertEqual(target.read_text(encoding='utf-8'), '原有笔记')

    def test_custom_connection_reaches_actual_subprocess(self):
        import tomllib
        module = self.settings_module()
        config = Path(service.os.environ['CODEX_HOME']) / 'config.toml'
        config.write_text('model_provider="custom"\n[model_providers.custom]\n'
                          'name="API"\nbase_url="https://example.test/v1"\n'
                          'requires_openai_auth=true\n', encoding='utf-8')
        def invoke(command, **kwargs):
            overrides = {}
            for index, token in enumerate(command):
                if token == '-c':
                    overrides.update(tomllib.loads(command[index + 1]))
            self.assertEqual(overrides['model_provider'], 'custom')
            self.assertEqual(overrides['model_providers']['custom']['base_url'], 'https://example.test/v1')
            self.assertIn('env', kwargs)
            Path(command[command.index('-o') + 1]).write_text('完整笔记正文。' * 20, encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', 'model: gpt-6.1-sol\nreasoning effort: medium\n')
        with tempfile.TemporaryDirectory() as folder, patch.object(service, 'codex_command', return_value=['codex.exe']), \
             patch.object(service.subprocess, 'run', side_effect=invoke):
            target = Path(folder) / 'note.md'
            service.run_codex('正文', target, settings=module.ModelSettings())
            record = json.loads(Path(str(target) + '.run.json').read_text(encoding='utf-8'))
            self.assertEqual(record['provider'], 'custom')

    def test_missing_actual_reasoning_is_not_fabricated(self):
        module = self.settings_module()
        def invoke(command, **kwargs):
            Path(command[command.index('-o') + 1]).write_text('完整的整理内容。' * 12, encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', 'model: gpt-6.1-sol\n')
        with tempfile.TemporaryDirectory() as folder, patch.object(service, 'codex_command', return_value=['codex.exe']), \
             patch.object(service.subprocess, 'run', side_effect=invoke):
            target = Path(folder) / 'note.md'
            service.run_codex('正文', target, settings=module.ModelSettings())
            record = json.loads(target.with_suffix('.md.run.json').read_text(encoding='utf-8'))
            self.assertIsNone(record['reasoning_effort'])
            self.assertEqual(record['requested_reasoning_effort'], 'medium')

    def test_unavailable_model_fails_once_without_fallback_or_overwrite(self):
        module = self.settings_module()
        with tempfile.TemporaryDirectory() as folder, patch.object(service, 'codex_command', return_value=['codex.exe']), \
             patch.object(service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'model_not_found')) as runner:
            target = Path(folder) / 'note.md'
            target.write_text('原有笔记', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, '未自动换模型'):
                service.run_codex('正文', target, settings=module.ModelSettings())
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(target.read_text(encoding='utf-8'), '原有笔记')

    def test_visual_model_mismatch_stops_summary_without_overwriting_note(self):
        module = self.settings_module()
        def invoke(command, **kwargs):
            visual = '--image' in command
            text = '{"overview":"画面","selected_frame_ids":[]}' if visual else '不应该覆盖的笔记正文。' * 12
            Path(command[command.index('-o') + 1]).write_text(text, encoding='utf-8')
            actual = 'unexpected-model' if visual else 'gpt-6.1-sol'
            return subprocess.CompletedProcess(command, 0, '', f'model: {actual}\nreasoning effort: medium\n')
        with tempfile.TemporaryDirectory() as folder, patch.object(service, 'codex_command', return_value=['codex.exe']), \
             patch.object(service.subprocess, 'run', side_effect=invoke) as runner:
            root = Path(folder)
            (root / 'source.json').write_text(json.dumps({'body': [{'from': 0, 'to': 1, 'content': '完整正文'}]}), encoding='utf-8')
            (root / 'manifest.json').write_text(json.dumps({'sheet': 'sheet.png', 'frames': [
                {'evidence_id': 'KF-P01-01', 'timestamp': '00:00', 'part': 'P01'}]}), encoding='utf-8')
            target = root / '测试—知识笔记.md'
            target.write_text('原有笔记', encoding='utf-8')
            result = {'complete': True, 'folder': folder, 'title': '测试', 'source': 'BV1k5np69EKv',
                      'visual': {'state': 'frames_ready', 'manifest': str(root / 'manifest.json')},
                      'pages': [{'page': 1, 'title': '正文', 'paths': {'json': str(root / 'source.json')}}]}
            with self.assertRaisesRegex(RuntimeError, '模型'):
                service.summarize(result, settings=module.ModelSettings())
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(target.read_text(encoding='utf-8'), '原有笔记')

    def test_all_long_video_calls_share_one_settings_snapshot(self):
        module = self.settings_module()
        pinned = module.ModelSettings()
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.json'
            source.write_text(json.dumps({'body': [{'from': i, 'to': i+1, 'content': '完整知识与观点' * 100} for i in range(70)]}), encoding='utf-8')
            result = {'complete': True, 'folder': folder, 'title': '长视频', 'source': 'BV1k5np69EKv',
                      'pages': [{'page': 1, 'title': '正文', 'paths': {'json': str(source)}}]}
            def run(prompt, target, **kwargs):
                self.assertIs(kwargs.get('settings'), pinned)
                from test_long_summary import reply
                text = json.dumps(reply(json.loads(prompt.splitlines()[-1])), ensure_ascii=False)
                Path(target).write_text(text, encoding='utf-8')
                Path(str(target) + '.run.json').write_text(json.dumps({
                    'model': pinned.model, 'reasoning_effort': pinned.reasoning_effort,
                    'requested_model': pinned.model, 'requested_reasoning_effort': pinned.reasoning_effort}), encoding='utf-8')
                return text
            with patch.object(service, 'run_codex', side_effect=run) as runner, \
                 patch('content_pipeline.review_visuals', return_value='') as visuals:
                service.summarize(result, settings=pinned)
                self.assertGreater(runner.call_count, 2)
                self.assertIs(visuals.call_args.kwargs.get('settings'), pinned)

    def test_truncated_structured_answer_cannot_replace_an_existing_note(self):
        module = self.settings_module()
        def invoke(command, **kwargs):
            Path(command[command.index('-o') + 1]).write_text('{"complete":true,"content":"' + '部分正文' * 30, encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', 'model: gpt-6.1-sol\nreasoning effort: medium\n')
        with tempfile.TemporaryDirectory() as folder, patch.object(service, 'codex_command', return_value=['codex.exe']), \
             patch.object(service.subprocess, 'run', side_effect=invoke):
            path = Path(folder) / 'note.md'
            path.write_text('旧笔记', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                service.run_codex('正文', path, schema={'type': 'object'}, settings=module.ModelSettings())
            self.assertEqual(path.read_text(encoding='utf-8'), '旧笔记')

    def test_model_mismatch_precedes_retryable_structure_failure(self):
        module = self.settings_module()
        def invoke(command, **kwargs):
            Path(command[command.index('-o') + 1]).write_text('{"broken":', encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', 'model: unexpected-model\nreasoning effort: medium\n')
        with tempfile.TemporaryDirectory() as folder, patch.object(service, 'codex_command', return_value=['codex.exe']), \
             patch.object(service.subprocess, 'run', side_effect=invoke):
            with self.assertRaises(service.ModelMismatchError):
                service.run_codex('正文', Path(folder) / 'note.json', schema={'type': 'object'}, settings=module.ModelSettings())

    def test_record_publish_failure_cannot_overwrite_short_note(self):
        module = self.settings_module()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'note.md'
            path.write_text('旧笔记完整内容', encoding='utf-8')
            original_replace = Path.replace
            def replace(source, target):
                if Path(target) == Path(str(path) + '.run.json'):
                    raise OSError('记录写入失败')
                return original_replace(source, target)
            def invoke(command, **kwargs):
                Path(command[command.index('-o') + 1]).write_text('新的核心内容。' * 20, encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, '', 'model: gpt-6.1-sol\nreasoning effort: medium\n')
            with patch.object(service, 'codex_command', return_value=['codex.exe']), \
                 patch.object(service.subprocess, 'run', side_effect=invoke), \
                 patch.object(Path, 'replace', replace), self.assertRaises(OSError):
                service.run_codex('正文', path, settings=module.ModelSettings())
            self.assertEqual(path.read_text(encoding='utf-8'), '旧笔记完整内容')


if __name__ == '__main__':
    unittest.main()
