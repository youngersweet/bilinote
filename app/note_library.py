"""界面偏好、笔记历史及用户明确选择的文件删除。"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from bili_service import DATA_DIR, DEFAULT_OUTPUT
from reading_preferences import DEFAULT_FONT_SIZE, DEFAULT_BACKGROUND, font_size, background, transparency, colors

from visual_themes import EXTRA, THEMES, snapshot, clean

PREFERENCES_PATH = DATA_DIR / 'ui-settings.json'
DEFAULTS = {'output_dir': str(DEFAULT_OUTPUT / '生成笔记'), 'imports': [], 'mode': 'auto',
            'visual': True, 'auto_summary': True, 'concurrency': 2,
            'reading_font_size': DEFAULT_FONT_SIZE, 'reading_background': DEFAULT_BACKGROUND,
            'match_app_background': False, 'background_image': '', 'background_transparency': 80,
            'reading_tabs_visible': False, 'library_visible': True, 'immersive_reading': False}

DEFAULTS.update(EXTRA)
DEFAULTS.update(appearance_version=1, custom_themes={})


def load_preferences(path=None):
    value = dict(DEFAULTS)
    saved = {}
    value['imports'] = []
    try:
        saved = json.loads((Path(path) if path else PREFERENCES_PATH).read_text(encoding='utf-8'))
        if isinstance(saved, dict):
            value.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if not isinstance(value['output_dir'], str) or not value['output_dir'].strip():
        value['output_dir'] = DEFAULTS['output_dir']
    if not isinstance(value['imports'], list):
        value['imports'] = []
    value['imports'] = [x for x in value['imports'] if isinstance(x, str)]
    if value['mode'] not in ('auto', 'subtitles', 'browser', 'audio'):
        value['mode'] = 'auto'
    for key in ('visual', 'auto_summary'):
        if not isinstance(value[key], bool):
            value[key] = True
    if type(value['concurrency']) is not int or not 1 <= value['concurrency'] <= 8:
        value['concurrency'] = 2
    value['reading_font_size'] = font_size(value['reading_font_size'])
    value['reading_background'] = background(value['reading_background'])
    value['match_app_background'] = value['match_app_background'] is True
    value['background_image'] = value['background_image'] if isinstance(value['background_image'], str) else ''
    value['background_transparency'] = transparency(value['background_transparency'])
    for key in ('reading_tabs_visible', 'library_visible', 'immersive_reading'):
        if type(value[key]) is not bool:
            value[key] = DEFAULTS[key]
    if isinstance(saved, dict) and saved and 'appearance_version' not in saved:
        legacy = dict(value, theme_id='legacy', app_background='#f5f5f7',
                      text_color=colors(value['reading_background'])['foreground'],
                      accent_color='#0071e3', reading_font='Microsoft YaHei UI', reading_line_height=155, reading_width=1400)
        value.update(snapshot(legacy))
        value['custom_themes'] = {'我的原有外观': snapshot(legacy)}
    elif not saved:
        value.update(snapshot(THEMES['paper']))
    value.update(clean(value))
    custom = value.get('custom_themes', {})
    value['custom_themes'] = {k[:40]: clean(v) for k, v in custom.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(custom, dict) else {}
    return value


def save_preferences(value, path=None):
    path = Path(path) if path else PREFERENCES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False, suffix='.tmp') as file:
            temporary = Path(file.name)
            json.dump({key: value.get(key, default) for key, default in DEFAULTS.items()}, file, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def find_note(result, folder):
    folder = Path(folder).resolve()
    candidates = [Path(result['summary_path'])] if isinstance(result.get('summary_path'), str) else []
    candidates += list(folder.glob('*—知识笔记.md'))
    for path in candidates:
        try:
            path.resolve().relative_to(folder)
            if path.is_file():
                return path
        except (OSError, ValueError):
            pass
    return None


def read_result(path):
    path = Path(path).resolve()
    result = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(result, dict) or not isinstance(result.get('pages'), list)
            or not all(isinstance(page, dict) for page in result['pages'])
            or not re.fullmatch(r'BV[A-Za-z0-9]{10}', str(result.get('bvid', '')))):
        raise ValueError('不是有效的视频笔记记录。')
    result['title'] = str(result.get('title') or result['bvid'])
    result['folder'] = str(path.parent)
    result['complete'] = result.get('complete') is True and bool(result['pages'])
    result['source'] = result.get('source') or f'https://www.bilibili.com/video/{result["bvid"]}/'
    return result


@dataclass
class NoteEntry:
    path: Path
    title: str
    author: str
    bvid: str
    result: dict
    note_path: Path | None
    modified: float

    @property
    def state(self):
        if self.note_path:
            return 'ready'
        return 'extracted' if self.result.get('complete') else 'partial'

    @property
    def preview(self):
        if self.note_path:
            try:
                for line in self.note_path.read_text(encoding='utf-8').splitlines():
                    if line.strip() and not line.lstrip().startswith(('#', '---', '>', '|', '来源', '- 来源')):
                        return re.sub(r'[\[\]*`_]', '', line).strip()[:90]
            except (OSError, UnicodeError):
                pass
        return '字幕已就绪，可以生成笔记' if self.state == 'extracted' else '正文尚不完整'


def scan_library(root, imports=()):
    root = Path(root)
    paths = list(root.glob('*/video.json')) if root.is_dir() else []
    if (root / 'video.json').is_file():
        paths.append(root / 'video.json')
    paths += [Path(x) for x in imports]
    entries, seen = [], set()
    for path in paths:
        try:
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            result = read_result(path)
            note = find_note(result, path.parent)
            modified = max(path.stat().st_mtime, note.stat().st_mtime if note else 0)
            entries.append(NoteEntry(path, str(result.get('title') or result['bvid']), str(result.get('author') or ''),
                                     result['bvid'], result, note, modified))
        except (OSError, ValueError, TypeError, UnicodeError):
            continue
    return sorted(entries, key=lambda entry: entry.modified, reverse=True)


def filter_entries(entries, query):
    words = query.casefold().split()
    return [entry for entry in entries if all(word in f'{entry.title} {entry.author} {entry.bvid}'.casefold() for word in words)]


def delete_note_folder(metadata, output_root, expected_bvid, *, active_bvids=()):
    """仅删除明确输出根目录的单个视频子目录；不信任 JSON 内保存的路径。"""
    path = Path(metadata).absolute()
    folder, root = path.parent, Path(output_root).resolve()
    if (path.name != 'video.json' or folder.parent.resolve() != root
            or folder.resolve().parent != root or folder.resolve() == root):
        raise ValueError('只能删除当前生成笔记目录内的视频文件夹，不能删除根目录或外部导入。')
    if expected_bvid in active_bvids:
        raise ValueError('这条视频正在处理或等待，请结束任务后再删除。')

    def reject_link(candidate):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise ValueError('视频文件夹中包含链接，请先在文件管理器中检查，未执行删除。')

    reject_link(folder)
    reject_link(path)
    current = read_result(path)
    if current['bvid'] != expected_bvid:
        raise ValueError('笔记记录已发生变化，请刷新后重新选择。')
    # 完整检查后才删除；包括 Windows junction，避免跟随链接触及其他资料。
    def walk_error(error):
        raise error
    for parent, directories, files in os.walk(folder, followlinks=False, onerror=walk_error):
        for name in directories + files:
            reject_link(Path(parent) / name)
    shutil.rmtree(folder)
