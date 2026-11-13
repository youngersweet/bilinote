"""Regression checks for deployment outside the original personal workspace."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class PortableTests(unittest.TestCase):
    def test_first_asr_use_downloads_model_without_personal_snapshot(self):
        import content_pipeline as pipeline
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / 'media/p01_12/audio.m4a'
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b'fixture')
            segment = SimpleNamespace(start=0, end=10, text='正文', avg_logprob=-0.1, no_speech_prob=0)
            model = Mock()
            model.transcribe.return_value = ([segment], SimpleNamespace(duration=10))
            factory = Mock(return_value=model)
            with patch.dict(sys.modules, {'faster_whisper': SimpleNamespace(WhisperModel=factory)}), \
                 patch.object(pipeline, '_model', None), \
                 patch.object(pipeline, 'MODEL_DIR', root / 'new-model-cache'), \
                 patch.object(pipeline, 'validate_media', return_value={'duration': 10, 'validated': True}):
                result = pipeline.ContentPipeline(client=Mock()).audio_page(
                    'BV1k5np69EKv', {'page': 1, 'cid': 12, 'duration': 10}, root, 1)
            self.assertTrue(result['completed'])
            factory.assert_called_once_with('small', device='cpu', compute_type='int8',
                                            cpu_threads=6, download_root=str(root / 'new-model-cache'))

    def test_relocated_application_loads_its_own_resources(self):
        with tempfile.TemporaryDirectory(prefix='Bili Note 空格 ') as folder:
            target = Path(folder).resolve()
            shutil.copytree(ROOT / 'app', target / 'app', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(ROOT / 'resources', target / 'resources')
            code = ('import sys,json;sys.path.insert(0,sys.argv[1]);'
                    'import content_pipeline as p;'
                    'print(json.dumps({"path":str(p.SKILL_DIR),"rules":bool(p.skill_instructions()["writing_text"]),"output":str(p.DEFAULT_OUTPUT)}))')
            output = subprocess.run([sys.executable, '-I', '-c', code, str(target / 'app')],
                                    capture_output=True, text=True, check=True, timeout=30)
            value = json.loads(output.stdout)
            self.assertEqual(Path(value['path']), target / 'resources/bili-note')
            self.assertTrue(value['rules'])
            self.assertEqual(Path(value['output']), target)
            script = target / 'resources/bili-note/scripts/archive_bili_materials.py'
            subprocess.run([sys.executable, str(script), '--help'], check=True,
                           capture_output=True, timeout=30)

    def test_source_package_excludes_runtime_data_and_preserves_license(self):
        spec = importlib.util.spec_from_file_location('source_package', ROOT / 'scripts/package.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('app/main.py', 'app/__pycache__/bad.py', '.venv/private.py',
                         '生成笔记/private.md', 'auth.json', 'resources/bili-note/LICENSE'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture', encoding='utf-8')
            names = {str(p.relative_to(root)).replace('\\', '/') for p in module.release_files(root)}
            self.assertIn('app/main.py', names)
            self.assertIn('resources/bili-note/LICENSE', names)
            for name in ('app/__pycache__/bad.py', '.venv/private.py', '生成笔记/private.md', 'auth.json'):
                self.assertNotIn(name, names)
