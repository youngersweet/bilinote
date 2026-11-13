"""模型失败信号与结构化输出约束必须在落盘前拦截。"""
import json
import unittest

from codex_output import IncompleteModelOutput, validate_model_output


class ModelOutputTests(unittest.TestCase):
    def test_rejects_explicit_token_limit_in_json_and_jsonl_logs(self):
        logs = [
            {'finish_reason': 'length'},
            {'choices': [{'finish_reason': 'max_tokens'}]},
            {'response': {'finish_reason': 'max_output_tokens'}},
        ]
        for record in logs:
            for stream in ('stdout', 'stderr'):
                with self.subTest(record=record, stream=stream):
                    log = '\n'.join([json.dumps({'type': 'turn.started'}),
                                     json.dumps(record), json.dumps({'type': 'turn.completed'})])
                    with self.assertRaises(IncompleteModelOutput):
                        validate_model_output('正文已经足够长。' * 20, **{stream: log})

    def test_rejects_incomplete_details_even_without_a_known_reason(self):
        for details in ({'reason': 'max_output_tokens'}, {}, {'reason': 'future_reason'}):
            with self.subTest(details=details):
                with self.assertRaises(IncompleteModelOutput):
                    validate_model_output('正文', stdout=json.dumps({'response': {'incomplete_details': details}}))

    def test_rejects_structured_failure_statuses_and_events(self):
        records = [
            {'status': 'incomplete'}, {'status': 'failed'}, {'status': 'error'},
            {'status': 'turn.failed'}, {'type': 'turn.failed'}, {'type': 'error'},
            {'type': 'response.incomplete'}, {'type': 'response.failed'},
            {'error': {'message': 'Bearer private-token'}},
        ]
        for record in records:
            with self.subTest(record=record):
                with self.assertRaises(IncompleteModelOutput) as caught:
                    validate_model_output('可解析正文', stderr=json.dumps(record))
                self.assertNotIn('private-token', str(caught.exception))

    def test_plain_text_keywords_and_unfinished_punctuation_are_not_evidence(self):
        for content in ('作者讨论 length、max_tokens、incomplete 和 error：',
                        '待讨论的三个问题……', '{"status":"incomplete"}'):
            with self.subTest(content=content):
                self.assertEqual(validate_model_output(content, stdout='status=incomplete\nfinish_reason=length'),
                                 {'finish_reason': 'unknown', 'status': 'unknown'})

    def test_extracts_only_allowlisted_metadata_from_structured_records(self):
        log = '\n'.join([
            json.dumps({'response': {'status': 'completed', 'incomplete_details': None,
                                     'choices': [{'finish_reason': 'stop'}]},
                        'text': 'Bearer private-token', 'model': 'private-model'}),
            json.dumps({'type': 'turn.completed', 'usage': {'output_tokens': 100}}),
        ])
        self.assertEqual(validate_model_output('正文', stdout=log),
                         {'finish_reason': 'stop', 'status': 'completed'})
        self.assertEqual(validate_model_output('正文', stderr=json.dumps({
            'status': 'Bearer private-token', 'finish_reason': 'private-finish-reason'})),
            {'finish_reason': 'unknown', 'status': 'unknown'})

    def test_null_error_fields_are_not_failures(self):
        self.assertEqual(validate_model_output('正文', stdout=json.dumps({
            'status': 'completed', 'error': None, 'incomplete_details': None})),
            {'finish_reason': 'unknown', 'status': 'completed'})

    def test_schema_validates_nested_project_output(self):
        schema = {
            'type': 'object', 'required': ['complete', 'notes', 'version'],
            'additionalProperties': False,
            'properties': {
                'complete': {'type': 'boolean', 'const': True},
                'version': {'type': 'integer', 'enum': [1, 2]},
                'notes': {'type': 'array', 'minItems': 1, 'items': {
                    'type': 'object', 'required': ['text', 'kind', 'weight', 'link'],
                    'additionalProperties': False, 'properties': {
                        'text': {'type': 'string', 'minLength': 2},
                        'kind': {'type': 'string', 'enum': ['fact', 'opinion']},
                        'weight': {'type': 'number'}, 'link': {'type': 'null'},
                    }}},
            },
        }
        content = {'complete': True, 'notes': [{'text': '依据', 'kind': 'fact', 'weight': 0.5, 'link': None}],
                   'version': 1}
        self.assertEqual(validate_model_output(json.dumps(content), schema=schema),
                         {'finish_reason': 'unknown', 'status': 'unknown'})

    def test_schema_requires_one_complete_json_value(self):
        for content in ('', '{"text":', '{"text":"ok"} trailing', '```json\n{}\n```',
                        '{}\n{}', '{"number":NaN}', '{"number":Infinity}',
                        '{"number":1e999}', '{"text":"ok","text":"overwritten"}'):
            with self.subTest(content=content):
                with self.assertRaises(IncompleteModelOutput):
                    validate_model_output(content, schema={'type': 'object'})

    def test_schema_rejects_missing_fields_extra_fields_and_nested_wrong_types(self):
        schema = {'type': 'object', 'required': ['notes'], 'additionalProperties': False,
                  'properties': {'notes': {'type': 'array', 'items': {
                      'type': 'object', 'required': ['text'], 'additionalProperties': False,
                      'properties': {'text': {'type': 'string'}}}}}}
        for content in ({}, {'notes': [], 'extra': 1}, {'notes': [{'text': 1}]},
                        {'notes': [{}]}, {'notes': [{'text': 'ok', 'secret-key': 'private-token'}]}):
            with self.subTest(content=content):
                with self.assertRaises(IncompleteModelOutput) as caught:
                    validate_model_output(json.dumps(content), schema=schema)
                self.assertNotIn('private-token', str(caught.exception))
                self.assertNotIn('secret-key', str(caught.exception))

    def test_schema_type_checks_distinguish_booleans_from_numbers(self):
        cases = [
            ('object', [], False), ('array', {}, False), ('string', 1, False),
            ('boolean', 1, False), ('boolean', True, True), ('integer', True, False),
            ('integer', 2, True), ('integer', 2.0, True), ('integer', 2.5, False),
            ('number', True, False), ('number', 2.5, True), ('null', None, True),
            ('null', 'null', False),
        ]
        for type_name, content, valid in cases:
            with self.subTest(type=type_name, content=content):
                if valid:
                    validate_model_output(json.dumps(content), schema={'type': type_name})
                else:
                    with self.assertRaises(IncompleteModelOutput):
                        validate_model_output(json.dumps(content), schema={'type': type_name})

    def test_schema_enforces_enum_const_and_minimum_lengths(self):
        cases = [
            ({'enum': [True]}, 1), ({'const': True}, 1),
            ({'enum': ['fact']}, 'opinion'), ({'const': 'done'}, 'pending'),
            ({'type': 'array', 'minItems': 2}, ['one']),
            ({'type': 'string', 'minLength': 2}, '字'),
        ]
        for schema, content in cases:
            with self.subTest(schema=schema, content=content):
                with self.assertRaises(IncompleteModelOutput):
                    validate_model_output(json.dumps(content), schema=schema)
        validate_model_output('1.0', schema={'enum': [1], 'const': 1})
        validate_model_output('"两个"', schema={'minLength': 2})
        validate_model_output('[1, 2]', schema={'minItems': 2})

    def test_schema_does_not_require_optional_properties(self):
        validate_model_output('{}', schema={'type': 'object', 'additionalProperties': False,
                                          'properties': {'optional': {'type': 'string'}}})

    def test_explicit_failure_is_checked_even_when_json_output_passes_schema(self):
        with self.assertRaises(IncompleteModelOutput):
            validate_model_output('{"complete":true}', stdout='{"finish_reason":"length"}',
                                  schema={'type': 'object', 'properties': {'complete': {'const': True}}})


if __name__ == '__main__':
    unittest.main()
