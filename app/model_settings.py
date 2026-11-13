"""Bili Note 独立模型配置；只读取模型目录，不读取或复制 Codex 凭证。"""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import tempfile

SETTINGS_PATH = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'BiliNote/model-settings.json'
EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')
EFFORT_LABELS = {'none': '关闭', 'minimal': '最少', 'low': '快速', 'medium': '均衡 · 推荐',
                 'high': '深入', 'xhigh': '更深入', 'max': '最高', 'ultra': '极高'}


def model_catalog():
    """本机缓存目录仅提供选择提示，账户是否可用需实际请求验证。"""
    path = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'models_cache.json'
    try:
        rows = json.loads(path.read_text(encoding='utf-8')).get('models', [])
        models = [row for row in rows if isinstance(row, dict) and isinstance(row.get('slug'), str)
                  and row.get('visibility', 'list') != 'hide' and row.get('slug') not in ('codex-auto-review', 'gpt-reserve')]
        if models:
            return models
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return [{'slug': 'gpt-6.1-sol', 'display_name': 'GPT-6.1-Sol',
             'supported_reasoning_levels': [{'effort': x} for x in ('low', 'medium', 'high')]}]


def supported_efforts(model):
    row = next((row for row in model_catalog() if row['slug'] == model), {})
    levels = [x.get('effort') for x in row.get('supported_reasoning_levels', []) if isinstance(x, dict)]
    return tuple(x for x in levels if x in EFFORTS) or ('low', 'medium', 'high')


@dataclass(frozen=True)
class ModelSettings:
    model: str = 'gpt-6.1-sol'
    reasoning_effort: str = 'medium'

    def __post_init__(self):
        if not isinstance(self.model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', self.model):
            raise ValueError('模型名称无效，请填写准确的模型标识。')
        if self.reasoning_effort not in supported_efforts(self.model):
            raise ValueError('该模型的本机目录不支持所选推理强度，请重新选择。')

    def to_dict(self):
        return asdict(self)


def load_settings(path=None):
    path = Path(path) if path else SETTINGS_PATH
    if not path.exists():
        return ModelSettings()
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return ModelSettings(value['model'], value['reasoning_effort'])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError('Bili Note 模型配置不可用，请打开“模型设置”重新保存。') from error


def save_settings(settings, path=None):
    path = Path(path) if path else SETTINGS_PATH
    value = ModelSettings(settings.model, settings.reasoning_effort)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            json.dump(value.to_dict(), output, ensure_ascii=False, indent=2)
            output.write('\n')
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return value


def check_connection(settings):
    from bili_service import run_codex
    schema = {'type': 'object', 'properties': {'status': {'type': 'string', 'enum': ['ok']}},
              'required': ['status'], 'additionalProperties': False}
    with tempfile.TemporaryDirectory(prefix='bili-note-model-check-') as folder:
        target = Path(folder) / 'check.json'
        content = run_codex('这是模型连接测试。只返回JSON：{"status":"ok"}。不调用工具。', target,
                            timeout=180, schema=schema, settings=settings)
        if json.loads(content).get('status') != 'ok':
            raise RuntimeError('模型返回了异常测试结果。')
        return json.loads(target.with_suffix('.json.run.json').read_text(encoding='utf-8'))
