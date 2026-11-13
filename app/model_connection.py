"""复用 Codex 的连接配置，不加载其工具配置，不读取 auth.json。"""
import json
import os
from pathlib import Path
import tomllib


def _toml(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return '[' + ', '.join(_toml(x) for x in value) + ']'
    if isinstance(value, dict):
        return '{' + ', '.join(_toml(k) + ' = ' + _toml(v) for k, v in value.items()) + '}'
    raise ValueError('Codex 连接配置包含不支持的值。')


def connection_options(path=None):
    """只传递连接字段；内联密钥/请求头改用子进程私有环境变量。"""
    path = Path(path) if path else Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'config.toml'
    try:
        config = tomllib.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    except (OSError, ValueError):
        raise ValueError('无法读取 Codex 连接配置，请检查 config.toml。') from None
    provider = config.get('model_provider', 'openai')
    values = {'model_provider': provider}
    for key in ('cli_auth_credentials_store', 'chatgpt_base_url'):
        if key in config:
            values[key] = config[key]
    environment = os.environ.copy()
    definition = config.get('model_providers', {}).get(provider)
    if definition is not None:
        definition = dict(definition)
        if 'experimental_bearer_token' in definition:
            environment['BILINOTE_PROVIDER_TOKEN'] = definition.pop('experimental_bearer_token')
            definition['env_key'] = 'BILINOTE_PROVIDER_TOKEN'
        headers = definition.pop('http_headers', {})
        env_headers = dict(definition.get('env_http_headers', {}))
        for index, (name, value) in enumerate(headers.items()):
            variable = f'BILINOTE_PROVIDER_HEADER_{index}'
            environment[variable] = value
            env_headers[name] = variable
        if env_headers:
            definition['env_http_headers'] = env_headers
        values['model_providers'] = {provider: definition}
    elif provider != 'openai':
        raise ValueError('Codex 当前提供商缺少定义，请检查 model_providers 配置。')
    arguments = []
    for key, value in values.items():
        arguments.extend(['-c', key + '=' + _toml(value)])
    return arguments, environment, provider
