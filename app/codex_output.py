"""Codex 输出完成信号与本地 JSON 结构校验。"""
from __future__ import annotations

import json
import math


_LIMIT_REASONS = frozenset({'length', 'max_tokens', 'max_output_tokens'})
_SAFE_FINISH_REASONS = frozenset({'stop', 'end_turn', 'stop_sequence', 'tool_calls', 'function_call',
                                  'eos', 'eos_token'})
_FAILURE_STATES = frozenset({'incomplete', 'failed', 'error', 'cancelled', 'canceled', 'aborted'})
_FAILURE_EVENTS = frozenset({'error', 'turn.failed', 'turn.incomplete', 'turn.error',
                            'response.failed', 'response.incomplete', 'response.error', 'item.failed'})
_COMPLETE_STATES = frozenset({'completed', 'complete', 'succeeded', 'success'})
_COMPLETE_EVENTS = frozenset({'turn.completed', 'response.completed'})


class IncompleteModelOutput(RuntimeError):
    """模型明确失败、截断或未满足结构化输出约定。"""


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('non-finite number')
    return value


def _reject_constant(_value):
    raise ValueError('non-JSON numeric constant')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _load_json(content):
    return json.loads(content, parse_float=_finite_float, parse_constant=_reject_constant,
                      object_pairs_hook=_unique_object)


def _log_records(log):
    """只识别完整 JSON 或独立 JSONL 行，不从混杂文本中猜测日志字段。"""
    if not isinstance(log, str) or not log.strip():
        return
    try:
        yield _load_json(log)
        return
    except (ValueError, TypeError, RecursionError):
        pass
    for line in log.splitlines():
        try:
            yield _load_json(line)
        except (ValueError, TypeError, RecursionError):
            continue


def _objects(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            yield item
            pending.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item))


def _token(value):
    return value.strip().lower() if isinstance(value, str) else ''


def _completion_metadata(stdout, stderr):
    metadata = {'finish_reason': 'unknown', 'status': 'unknown'}
    for log in (stdout, stderr):
        for record in _log_records(log):
            for item in _objects(record):
                reason = _token(item.get('finish_reason'))
                state = _token(item.get('status'))
                events = (_token(item.get('type')), _token(item.get('event')))
                error = item.get('error')
                if (reason in _LIMIT_REASONS or item.get('incomplete_details') is not None
                        or state in _FAILURE_STATES or state in _FAILURE_EVENTS
                        or any(event in _FAILURE_EVENTS for event in events)
                        or error or isinstance(error, dict)):
                    raise IncompleteModelOutput('模型返回了截断或失败信号，本次未保存输出。')
                if reason in _SAFE_FINISH_REASONS:
                    metadata['finish_reason'] = reason
                if state in _COMPLETE_STATES or any(event in _COMPLETE_EVENTS for event in events):
                    metadata['status'] = 'completed'
    return metadata


def _matches_type(value, type_name):
    if type_name == 'object':
        return isinstance(value, dict)
    if type_name == 'array':
        return isinstance(value, list)
    if type_name == 'string':
        return isinstance(value, str)
    if type_name == 'boolean':
        return isinstance(value, bool)
    if type_name == 'null':
        return value is None
    if type_name in ('integer', 'number'):
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        return numeric and (type_name == 'number' or value == int(value))
    return False


def _json_equal(left, right):
    """JSON 布尔值与数字不同；1 和 1.0 作为 JSON 数字相等。"""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    if type(left) is not type(right) and not (isinstance(left, (int, float)) and isinstance(right, (int, float))):
        return False
    return left == right


def _schema_failure():
    # 异常不带正文、字段值、日志或未知字段名，避免诊断路径泄露输入。
    raise IncompleteModelOutput('模型输出 JSON 结构不符合约定，本次未保存输出。')


def _validate_schema(value, schema):
    if not isinstance(schema, dict):
        _schema_failure()
    if 'type' in schema:
        types = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        if not any(_matches_type(value, type_name) for type_name in types):
            _schema_failure()
    if 'enum' in schema and not any(_json_equal(value, option) for option in schema['enum']):
        _schema_failure()
    if 'const' in schema and not _json_equal(value, schema['const']):
        _schema_failure()
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if any(key not in value for key in schema.get('required', [])):
            _schema_failure()
        if schema.get('additionalProperties') is False and any(key not in properties for key in value):
            _schema_failure()
        for key, child_schema in properties.items():
            if key in value:
                _validate_schema(value[key], child_schema)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0):
            _schema_failure()
        if 'items' in schema:
            for item in value:
                _validate_schema(item, schema['items'])
    if isinstance(value, str) and len(value) < schema.get('minLength', 0):
        _schema_failure()


def validate_model_output(content, stdout='', stderr='', schema=None) -> dict:
    """先拒绝结构化失败信号，再按非空 schema 校验完整 JSON。

    返回值仅含白名单化的 ``finish_reason`` 和 ``status``；没有日志证据时为
    ``unknown``。没有 schema 时不推测正文是否完整，调用者仍负责原有空正文检查。
    所有校验失败都抛出不含原文或日志的 :class:`IncompleteModelOutput`。
    """
    metadata = _completion_metadata(stdout, stderr)
    if schema:
        try:
            value = _load_json(content)
            _validate_schema(value, schema)
        except IncompleteModelOutput:
            raise
        except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
            raise IncompleteModelOutput('模型输出不是符合约定的完整 JSON，本次未保存输出。') from None
    return metadata
