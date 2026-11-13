import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from model_connection import connection_options


class ConnectionTests(unittest.TestCase):
    def resolve(self, content):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.toml'
            path.write_text(content, encoding='utf-8')
            args, env, provider = connection_options(path)
        values = {}
        for item in args[1::2]:
            values.update(tomllib.loads(item))
        return values, env, provider

    def test_account_login_keeps_credentials_store_without_tools(self):
        values, _, provider = self.resolve('cli_auth_credentials_store="keyring"\n[features]\nplugins=true')
        self.assertEqual(provider, 'openai')
        self.assertEqual(values, {'model_provider': 'openai', 'cli_auth_credentials_store': 'keyring'})

    def test_api_gateway_keeps_endpoint_and_saved_api_auth(self):
        values, _, provider = self.resolve('''model_provider="custom"
model="unwanted-model"
[model_providers.custom]
name="API"
base_url="https://example.test/v1"
wire_api="responses"
requires_openai_auth=true
[mcp_servers.unwanted]
command="do-not-run"
''')
        self.assertEqual(provider, 'custom')
        self.assertNotIn('model', values)
        self.assertNotIn('mcp_servers', values)
        self.assertEqual(values['model_providers']['custom']['base_url'], 'https://example.test/v1')
        self.assertTrue(values['model_providers']['custom']['requires_openai_auth'])

    def test_environment_api_key_is_inherited(self):
        with patch.dict(os.environ, {'TEST_PROVIDER_KEY': 'test-secret'}):
            values, env, _ = self.resolve('''model_provider="custom"
[model_providers.custom]
name="API"
env_key="TEST_PROVIDER_KEY"
''')
        self.assertEqual(env['TEST_PROVIDER_KEY'], 'test-secret')
        self.assertNotIn('test-secret', json.dumps(values))

    def test_inline_credentials_are_not_command_arguments(self):
        values, env, _ = self.resolve('''model_provider="custom"
[model_providers.custom]
name="API"
experimental_bearer_token="token-secret"
[model_providers.custom.http_headers]
api-key="header-secret"
''')
        self.assertNotIn('token-secret', json.dumps(values))
        self.assertNotIn('header-secret', json.dumps(values))
        self.assertEqual(env['BILINOTE_PROVIDER_TOKEN'], 'token-secret')
        self.assertEqual(env['BILINOTE_PROVIDER_HEADER_0'], 'header-secret')

    def test_missing_provider_and_bad_config_fail_without_official_fallback(self):
        for config in ('model_provider="missing"', '[broken'):
            with self.assertRaises(ValueError):
                self.resolve(config)


if __name__ == '__main__':
    unittest.main()
