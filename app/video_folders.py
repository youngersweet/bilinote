"""标题目录与旧 BV 目录迁移；保留原始正文和视频标识。"""
import json
from pathlib import Path
import re
import tempfile
import threading

from bili_service import safe_name

_reservation_lock = threading.RLock()


def title_directory_name(title):
    value = safe_name(re.sub(r'\s+', ' ', str(title))).strip()[:64].rstrip('. ')
    if not value or value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
            *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        value = '视频_' + (value or '笔记')
    return value


def _video_id(folder):
    try:
        data = json.loads((folder / 'video.json').read_text(encoding='utf-8'))
        return (data.get('note_id') or data.get('bvid')) if isinstance(data, dict) else None
    except (OSError, ValueError):
        try:
            return (folder / '.bili-note-id').read_text(encoding='utf-8').strip()
        except OSError:
            return None


def _target(root, bvid, title, exclude=None):
    name = title_directory_name(title)
    candidate = root / name
    if candidate == exclude or not candidate.exists() or (exclude is None and _video_id(candidate) == bvid):
        return candidate
    candidate = root / f'{name} [{bvid}]'
    number = 2
    while candidate.exists() and candidate != exclude and not (exclude is None and _video_id(candidate) == bvid):
        candidate = root / f'{name} [{bvid}] {number}'
        number += 1
    return candidate


def rebase_paths(value, changes):
    if isinstance(value, dict):
        return {key: item if key in ('content', 'text') and isinstance(item, str) else rebase_paths(item, changes)
                for key, item in value.items()}
    if isinstance(value, list):
        return [rebase_paths(item, changes) for item in value]
    if isinstance(value, str):
        normalized = value.replace('\\', '/')
        for old, new in changes.items():
            prefix = str(old).replace('\\', '/').rstrip('/')
            if normalized.casefold() == prefix.casefold() or normalized.casefold().startswith(prefix.casefold() + '/'):
                return str(Path(new) / normalized[len(prefix):].lstrip('/'))
    return value


def atomic_write(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix='.tmp') as file:
            temporary = Path(file.name)
            file.write(data)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def relocate_folder(old, new, output_root):
    old, new, root = Path(old).resolve(), Path(new).resolve(), Path(output_root).resolve()
    # 在同一个明确的输出目录内改名；不得跨目录、覆盖现有文件或移动链接指向的外部目录。
    if old.parent != root or new.parent != root:
        raise ValueError('目录改名必须在当前笔记保存目录内。')
    if old == new:
        return new
    if new.exists():
        raise FileExistsError('目标目录已存在，未合并或覆盖：' + str(new))
    changes = {str(old): str(new)}
    edits = []
    for path in old.rglob('*.json'):
        try:
            path.resolve().relative_to(old)
            original = path.read_bytes()
            value = json.loads(original.decode('utf-8-sig'))
        except (OSError, ValueError, UnicodeError):
            continue
        revised = rebase_paths(value, changes)
        if revised != value:
            edits.append((path.relative_to(old), original, (json.dumps(revised, ensure_ascii=False, indent=2) + '\n').encode('utf-8')))
    old.rename(new)
    written = []
    try:
        for relative, original, revised in edits:
            atomic_write(new / relative, revised)
            written.append((relative, original))
    except OSError:
        for relative, original in reversed(written):
            (new / relative).write_bytes(original)
        new.rename(old)
        raise
    return new


def choose_video_folder(output_root, bvid, title):
    root = Path(output_root).resolve()
    if root.is_dir():
        for metadata in sorted(root.glob('*/video.json'), key=lambda path: (path.parent.name == bvid, str(path))):
            folder = metadata.parent
            if _video_id(folder) == bvid:
                if folder.name == bvid:
                    return relocate_folder(folder, _target(root, bvid, title, folder), root)
                return folder
    return _target(root, bvid, title)


def reserve_video_folder(output_root, bvid, title):
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with _reservation_lock:
        for unused in range(100):
            target = choose_video_folder(root, bvid, title)
            if target.is_dir() and _video_id(target) == bvid:
                return target
            try:
                target.mkdir()
            except FileExistsError:
                continue
            atomic_write(target / '.bili-note-id', bvid.encode('utf-8'))
            return target
    raise RuntimeError('未能预留独立的视频目录，请稍后重试。')


def migrate_legacy_folders(output_root):
    root = Path(output_root).resolve()
    changes = {}
    if not root.is_dir():
        return changes
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or not re.fullmatch(r'BV[A-Za-z0-9]{10}', folder.name):
            continue
        try:
            result = json.loads((folder / 'video.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(result, dict) or result.get('bvid') != folder.name or not result.get('title'):
            continue
        target = _target(root, result['bvid'], result['title'], folder)
        if target != folder:
            relocate_folder(folder, target, root)
            changes[str(folder)] = str(target)
    return changes
