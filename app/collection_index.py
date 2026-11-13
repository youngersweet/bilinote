"""无模型的合集导航；并发原子更新，正文仍在每集各自的目录。"""
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import quote

from bili_service import parse_bvid, source_page
from video_folders import atomic_write, title_directory_name

_lock = threading.RLock()
LABELS = {'queued': '等待', 'running': '处理中', 'done': '完成', 'failed': '失败',
          'partial': '正文待补齐', 'cancelled': '已取消'}


def index_paths(output, plan):
    root = Path(output).resolve()
    directory = root / '_合集目录'
    if directory.resolve().parent != root:
        raise ValueError('合集目录不能指向保存位置以外。')
    identity = str(plan['id'])
    if not re.fullmatch(r'(?:season-\d+|BV[A-Za-z0-9]{10})', identity):
        raise ValueError('合集标识无效。')
    name = f'{title_directory_name(plan["title"])} [{identity}]'
    paths = directory / f'{name}.md', directory / f'{name}.index.json'
    if any(path.resolve().parent != directory.resolve() for path in paths):
        raise ValueError('合集目录文件不能指向其他位置。')
    return paths


def _label(text):
    text = str(text).replace('\n', ' ').replace('\r', ' ').replace('<', '&lt;').replace('>', '&gt;')
    for char in ('\\', '|', '[', ']', '*', '`'):
        text = text.replace(char, '\\' + char)
    return text


def _render(output, path, data):
    root = Path(output).resolve()
    lines = [f'# {_label(data["title"])}', '', '每集独立整理。本目录只导航已读取的分集，不代表整个合集已经完成。', '',
             '| 视频 / 分集 | 时长 | 状态 | 笔记 |', '| --- | --- | --- | --- |']
    for entry in data['entries']:
        record = data.get('records', {}).get(entry['key'], {})
        label = LABELS.get(record.get('state'), '未选择')
        if record.get('state') == 'done' and not record.get('note'):
            label = '正文已提取'
        note_link = ''
        changed = bool(record and record.get('cid') != entry.get('cid'))
        if changed:
            label = '内容已变更，需重新整理'
        if record.get('note') and not changed:
            note = (root / record['note']).resolve()
            try:
                note.relative_to(root)
                if note.is_file():
                    relative = Path(os.path.relpath(note, path.parent)).as_posix()
                    note_link = f'[打开笔记]({quote(relative, safe="/.")})'
                else:
                    label = '文件已删除'
            except ValueError:
                label = '笔记路径不可用'
        seconds = entry['duration']
        title = f'{entry["video_title"]} · P{entry["page"]:03} {entry["title"]}' if entry['total_pages'] > 1 else entry['title']
        source = f'https://www.bilibili.com/video/{entry["bvid"]}/?p={entry["page"]}'
        lines.append(f'| [{_label(title)}]({source}) | {seconds//60}:{seconds%60:02} | {label} | {note_link} |')
    if data.get('unexpanded'):
        lines += ['', '## 尚未展开的相关视频', '', '以下只取得视频清单，尚未展开分集或处理正文：', '']
        lines += [f'- {_label(video["title"])}' for video in data['unexpanded']]
    atomic_write(path, ('\n'.join(lines) + '\n').encode('utf-8'))


def update_index(output, plan, source, state, result=None, error=''):
    path, metadata = index_paths(output, plan)
    bvid, page = parse_bvid(source), source_page(source)
    key = f'{bvid}-P{page:03}' if page else bvid
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(metadata.read_text(encoding='utf-8'))
        except FileNotFoundError:
            data = dict(plan) | {'records': {}}
        existing = {entry['key']: entry for entry in data['entries']}
        existing.update({entry['key']: entry for entry in plan['entries']})
        data['entries'] = list(existing.values())
        loaded = {entry['bvid'] for entry in data['entries']}
        unexpanded = {v['bvid']: v for v in data.get('unexpanded', []) + plan.get('unexpanded', [])}
        data['unexpanded'] = [v for key, v in unexpanded.items() if key not in loaded]
        previous = data.setdefault('records', {}).get(key, {})
        cid = existing.get(key, {}).get('cid')
        record = {'state': state, 'error': error, 'cid': cid}
        if result and result.get('summary_path'):
            note = Path(result['summary_path']).resolve()
            note.relative_to(Path(output).resolve())
            record['note'] = str(note.relative_to(Path(output).resolve()))
        elif previous.get('note') and previous.get('cid') == cid:
            record['note'] = previous['note']
        if previous == record and path.exists():
            return
        data['records'][key] = record
        atomic_write(metadata, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
        _render(output, path, data)


def refresh_indexes(output):
    with _lock:
        for metadata in (Path(output).resolve() / '_合集目录').glob('*.index.json'):
            try:
                data = json.loads(metadata.read_text(encoding='utf-8'))
                path, checked = index_paths(output, data)
                if checked == metadata:
                    _render(output, path, data)
            except (OSError, ValueError, KeyError, TypeError):
                continue
