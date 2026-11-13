"""只读当前登录账号的自建收藏夹及其视频；不获取正文或修改收藏。"""
from dataclasses import dataclass
import re

from bili_service import BiliClient, LoginRequired


@dataclass(frozen=True)
class FavoriteFolder:
    id: int
    title: str
    count: int


@dataclass(frozen=True)
class FavoriteVideo:
    bvid: str
    title: str
    author: str
    duration: int
    available: bool
    reason: str
    resource_id: int

    @property
    def source(self):
        return f'https://www.bilibili.com/video/{self.bvid}/' if self.bvid else ''

    @property
    def key(self):
        return self.bvid or f'unavailable:{self.resource_id}:{self.title}'


@dataclass(frozen=True)
class FavoritePage:
    items: tuple[FavoriteVideo, ...]
    total: int
    has_more: bool


def number(value):
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        return 0


class FavoriteService:
    def __init__(self, client=None):
        self.client = client or BiliClient()
        self.folder_ids = set()

    def _data(self, path, params=None):
        response = self.client.request('api.bilibili.com', path, params)
        code = response.get('code')
        if code == -101:
            raise LoginRequired('B站登录已失效，请打开登录管理重新扫码，再刷新收藏夹。')
        if code in (-352, -412, -799):
            raise RuntimeError('B站暂时限制了请求，请稍后刷新，不要连续重试。')
        if code in (-403, 62002):
            raise RuntimeError('当前账号没有读取该收藏夹的权限，请检查登录或刷新收藏夹。')
        if code != 0:
            raise RuntimeError(f'收藏读取失败（B站返回 {code}），请稍后刷新。')
        data = response.get('data')
        if not isinstance(data, dict):
            raise RuntimeError('B站返回的收藏数据不完整，请稍后刷新。')
        return data

    def folders(self):
        self.folder_ids.clear()
        self.client.load_saved()
        account = self._data('/x/web-interface/nav')
        mid = number(account.get('mid'))
        if not account.get('isLogin') or not mid:
            raise LoginRequired('请先打开登录管理，扫码登录B站，再刷新收藏夹。')
        data = self._data('/x/v3/fav/folder/created/list-all', {'up_mid': mid, 'type': 2})
        rows = data.get('list')
        rows = [] if rows is None else rows
        if 'list' not in data or not isinstance(rows, list):
            raise RuntimeError('收藏夹清单不完整，请稍后刷新。')
        folders = [FavoriteFolder(number(row.get('id')), str(row.get('title') or '未命名收藏夹'),
                                  number(row.get('media_count')))
                   for row in rows if isinstance(row, dict) and number(row.get('id'))]
        self.folder_ids = {folder.id for folder in folders}
        return folders

    def videos(self, folder_id, page=1):
        if folder_id not in self.folder_ids:
            raise ValueError('请先加载并选择当前账号的收藏夹。')
        if type(page) is not int or page < 1:
            raise ValueError('收藏页码必须从1开始。')
        data = self._data('/x/v3/fav/resource/list', {
            'media_id': folder_id, 'pn': page, 'ps': 20,
            'order': 'mtime', 'type': 0, 'tid': 0, 'platform': 'web'})
        rows = data.get('medias')
        rows = [] if rows is None else rows
        if ('medias' not in data or not isinstance(rows, list)
                or type(data.get('has_more')) not in (bool, int)):
            raise RuntimeError('收藏视频清单不完整，请稍后重试。')
        videos = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            bvid = str(row.get('bvid') or row.get('bv_id') or '')
            bvid = bvid if re.fullmatch(r'BV[A-Za-z0-9]{10}', bvid) else ''
            title = str(row.get('title') or '无标题视频')
            reason = ''
            if number(row.get('type')) != 2:
                reason = '暂不支持此类收藏内容'
            elif number(row.get('attr')) & 1 or title == '已失效视频':
                reason = '视频已失效或不可访问'
            elif not bvid:
                reason = '缺少可用的视频链接'
            upper = row.get('upper') if isinstance(row.get('upper'), dict) else {}
            videos.append(FavoriteVideo(bvid, title, str(upper.get('name') or ''),
                number(row.get('duration')), not bool(reason), reason, number(row.get('id'))))
        info = data.get('info') if isinstance(data.get('info'), dict) else {}
        return FavoritePage(tuple(videos), number(info.get('media_count')), bool(data.get('has_more')))
