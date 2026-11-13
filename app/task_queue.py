"""有限并发的视频任务队列；后台仅发布独立快照，不操作界面。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
import threading
import time
import uuid

from bili_service import parse_bvid, source_page
from model_settings import load_settings

PENDING = {'queued', 'running'}
STATES = {'queued': '等待处理', 'running': '处理中', 'done': '已完成',
          'partial': '正文待补齐', 'failed': '失败', 'cancelled': '已取消'}


def parse_video_sources(text):
    sources, seen = [], set()
    for line_number, line in enumerate(text.splitlines(), 1):
        for value in line.split():
            try:
                bvid = parse_bvid(value)
                key = (bvid, source_page(value))
            except ValueError as error:
                raise ValueError(f'第 {line_number} 行：{error}') from None
            if key not in seen:
                sources.append(value)
                seen.add(key)
    if not sources:
        raise ValueError('请粘贴至少一条视频链接或 BV 号。')
    return sources


@dataclass(frozen=True)
class TaskSpec:
    source: str
    output_dir: str
    mode: str = 'auto'
    visual: bool = True
    auto_summary: bool = True
    settings: object = None
    kind: str = 'collect'
    result: dict | None = None
    expected_cid: int | None = None
    title: str = ''
    collection: dict | None = None

    def __post_init__(self):
        parse_bvid(self.source)
        source_page(self.source)
        if self.kind not in ('collect', 'summary', 'visual'):
            raise ValueError('不支持的任务类型。')
        if self.settings is None and (self.auto_summary or self.kind != 'collect'):
            object.__setattr__(self, 'settings', load_settings())

    @property
    def bvid(self):
        return parse_bvid(self.source)

    @property
    def page(self):
        return source_page(self.source)


@dataclass(frozen=True)
class TaskSnapshot:
    id: str
    bvid: str
    source: str
    title: str
    status: str
    stage: str
    error: str
    result: dict | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    revision: int
    settings: object


def process_video_task(spec, progress, materials, client_factory=None):
    from bili_service import BiliClient, summarize
    from content_pipeline import collect_video, prepare_visual
    if spec.kind == 'collect':
        options = {'expected_cid': spec.expected_cid} if spec.expected_cid is not None else {}
        result = collect_video(spec.source, spec.output_dir, client=(client_factory or BiliClient)(),
                               mode=spec.mode, visual=spec.visual, progress=progress, **options)
        if spec.collection:
            from collection_index import index_paths
            from content_pipeline import save_json
            result['collection_index'] = str(index_paths(spec.output_dir, spec.collection)[0])
            result['collection_title'] = spec.collection['title']
            save_json(Path(result['folder']) / 'video.json', result)
        materials(result)
        if not result.get('complete') or not spec.auto_summary:
            return result
    else:
        result = deepcopy(spec.result)
        if not result or not result.get('complete'):
            raise ValueError('当前视频正文尚不完整。')
        materials(result)
        if spec.kind == 'visual':
            prepare_visual(result, progress)
    summarize(result, progress, settings=spec.settings)
    return result


class VideoTaskQueue:
    def __init__(self, limit=2, runner=None, on_update=lambda snapshot: None):
        self.limit = max(1, min(8, int(limit)))
        self.runner = runner or process_video_task
        self.on_update = on_update
        self._lock = threading.RLock()
        self._jobs = {}
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='bili-video')
        self._closed = False

    def _snapshot(self, job):
        return TaskSnapshot(**{key: deepcopy(value) if key == 'result' else value
                               for key, value in job.items() if key != 'spec'})

    def _emit(self, job):
        job['revision'] += 1
        if job['spec'].collection:
            try:
                from collection_index import update_index
                update_index(job['spec'].output_dir, job['spec'].collection, job['source'], job['status'],
                             result=job['result'], error=job['error'])
            except (OSError, ValueError, TypeError, KeyError):
                job['stage'] += ' · 合集目录暂未更新'
        snapshot = self._snapshot(job)
        try:
            self.on_update(snapshot)
        except Exception:
            pass  # 订阅者失败不能把已经保存的任务改成处理失败。

    def snapshots(self):
        with self._lock:
            return [self._snapshot(job) for job in self._jobs.values()]

    def get(self, identity):
        with self._lock:
            return self._snapshot(self._jobs[identity])

    def has_pending(self, bvid=None):
        with self._lock:
            return any(job['status'] in PENDING and (bvid is None or job['bvid'] == bvid)
                       for job in self._jobs.values())

    def can_retry(self, identity):
        with self._lock:
            job = self._jobs[identity]
            spec = job['spec']
            return job['status'] in ('failed', 'partial', 'cancelled') and not any(
                other['status'] in PENDING and other['bvid'] == spec.bvid and
                (spec.page is None or other['spec'].page is None or spec.page == other['spec'].page)
                for other in self._jobs.values())

    def enqueue(self, specs):
        ids, duplicates = [], []
        with self._lock:
            if self._closed:
                raise RuntimeError('任务队列已关闭。')
            for supplied in specs:
                spec = replace(supplied, result=deepcopy(supplied.result), collection=deepcopy(supplied.collection))
                if any(job['status'] in PENDING and job['bvid'] == spec.bvid and
                       (spec.page is None or job['spec'].page is None or spec.page == job['spec'].page)
                       for job in self._jobs.values()):
                    duplicates.append(spec.bvid)
                    continue
                identity = uuid.uuid4().hex
                job = {'id': identity, 'bvid': spec.bvid, 'source': spec.source,
                       'title': (spec.result or {}).get('title') or spec.title or spec.bvid,
                       'status': 'queued', 'stage': '等待处理', 'error': '', 'result': None,
                       'created_at': time.time(), 'started_at': None, 'finished_at': None,
                       'revision': 0, 'settings': spec.settings, 'spec': spec}
                self._jobs[identity] = job
                ids.append(identity)
                self._emit(job)
            self._pump()
        return ids, duplicates

    def _pump(self):
        with self._lock:
            if self._closed:
                return
            running = sum(job['status'] == 'running' for job in self._jobs.values())
            for job in self._jobs.values():
                if running >= self.limit:
                    break
                if job['status'] != 'queued':
                    continue
                job.update(status='running', stage='开始处理', started_at=time.time())
                self._emit(job)
                running += 1
                self._pool.submit(self._run, job['id'])

    def _run(self, identity):
        with self._lock:
            spec = self._jobs[identity]['spec']
        def progress(message):
            with self._lock:
                job = self._jobs[identity]
                job['stage'] = str(message)
                self._emit(job)
        def materials(result):
            with self._lock:
                job = self._jobs[identity]
                job['title'] = result.get('title') or job['bvid']
                job['result'] = deepcopy(result)
                job['stage'] = '正文已保存' if result.get('complete') else '部分正文已保存'
                self._emit(job)
        try:
            result = self.runner(spec, progress, materials)
            if not isinstance(result, dict):
                raise RuntimeError('处理没有返回有效视频结果。')
            with self._lock:
                job = self._jobs[identity]
                job.update(result=deepcopy(result), title=result.get('title') or job['bvid'],
                           status='done' if result.get('complete') else 'partial', finished_at=time.time(),
                           stage=('笔记已完成' if result.get('summary_path') else '完整正文已保存')
                                 if result.get('complete') else '正文尚不完整，未生成全片笔记')
                self._emit(job)
        except Exception as error:
            with self._lock:
                job = self._jobs[identity]
                job.update(status='failed', stage='处理失败', error=str(error), finished_at=time.time())
                self._emit(job)
        self._pump()

    def set_limit(self, value):
        with self._lock:
            self.limit = max(1, min(8, int(value)))
            self._pump()

    def cancel(self, identity):
        with self._lock:
            job = self._jobs[identity]
            if job['status'] != 'queued':
                return False
            job.update(status='cancelled', stage='已取消等待', finished_at=time.time())
            self._emit(job)
            return True

    def retry(self, identity):
        with self._lock:
            job = self._jobs[identity]
            if not self.can_retry(identity):
                return None
            ids, unused = self.enqueue([job['spec']])
            return ids[0] if ids else None

    def clear_finished(self):
        with self._lock:
            self._jobs = {identity: job for identity, job in self._jobs.items() if job['status'] in PENDING}

    def shutdown(self, wait=True):
        with self._lock:
            self._closed = True
            for job in self._jobs.values():
                if job['status'] == 'queued':
                    job.update(status='cancelled', stage='队列已关闭', finished_at=time.time())
                    self._emit(job)
        self._pool.shutdown(wait=wait)
