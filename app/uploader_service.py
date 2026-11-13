"""UP账号搜索与投稿目录；只读取元数据，使用当前站点WBI签名。"""
from dataclasses import dataclass
import hashlib
from html import unescape
import re
import time
import urllib.parse
import urllib.request

from bili_service import BiliClient, LoginRequired, HEADERS

MIXIN = (46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,
         29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,
         22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52)


def plain(value):
    return unescape(re.sub(r'<[^>]*>', '', str(value or '')))


def parse_uploader(value):
    value = value.strip()
    if not value:
        raise ValueError('请输入UP名称、UID或B站个人空间地址。')
    if '://' in value:
        url = urllib.parse.urlparse(value)
        match = re.fullmatch(r'/([1-9][0-9]*)(?:/.*)?', url.path)
        if url.scheme not in ('http', 'https') or url.hostname != 'space.bilibili.com' or not match:
            raise ValueError('请使用 space.bilibili.com 开头的UP主空间地址。')
        return int(match[1])
    match = re.fullmatch(r'(?:uid\s*[:：]?\s*)?([1-9][0-9]*)', value, re.I)
    if match:
        return int(match[1])
    if value.isdecimal() or len(value) > 80:
        raise ValueError('UID必须大于0；UP名称请限制在80个字符内。')
    return None


@dataclass(frozen=True)
class Uploader:
    mid: int
    name: str
    face: str
    sign: str
    followers: int
    video_count: int


@dataclass(frozen=True)
class UploaderPage:
    items: tuple[Uploader, ...]
    total: int
    has_more: bool


@dataclass(frozen=True)
class UploadVideo:
    bvid: str
    title: str
    author: str
    duration: int
    published: int
    available: bool
    reason: str
    collaboration: bool = False

    @property
    def source(self):
        return f'https://www.bilibili.com/video/{self.bvid}/'


@dataclass(frozen=True)
class UploadPage:
    items: tuple[UploadVideo, ...]
    total: int
    has_more: bool


class UploaderService:
    def __init__(self, client=None):
        self.client = client or BiliClient()
        self._key = None
        self._key_until = 0

    def _signed(self, params):
        if not self._key or time.monotonic() >= self._key_until:
            nav = self.client.request('api.bilibili.com', '/x/web-interface/nav')
            images = (nav.get('data') or {}).get('wbi_img') or {}
            keys = [urllib.parse.urlparse(str(images.get(name) or '')).path.rsplit('/', 1)[-1].split('.')[0]
                    for name in ('img_url', 'sub_url')]
            if not all(re.fullmatch(r'[a-fA-F0-9]{32}', key) for key in keys):
                raise RuntimeError('未取得B站当前请求签名，请检查登录与网络后重试。')
            combined = ''.join(keys)
            self._key = ''.join(combined[index] for index in MIXIN)[:32]
            self._key_until = time.monotonic() + 300
        values = {key: ''.join(c for c in str(value) if c not in "!'()*")
                  for key, value in (dict(params) | {'wts': int(time.time())}).items()}
        values = dict(sorted(values.items()))
        values['w_rid'] = hashlib.md5((urllib.parse.urlencode(values) + self._key).encode()).hexdigest()
        return values

    def _data(self, path, params, signed=False):
        self.client.load_saved()
        response = self.client.request('api.bilibili.com', path, self._signed(params) if signed else params)
        code = response.get('code')
        if code == -101:
            raise LoginRequired('B站登录已失效，请在主界面登录管理中重新扫码。')
        if code in (-352, -412, -799):
            raise RuntimeError('B站暂时限制了读取，请稍后再试，不要连续重复请求。')
        if code in (-403, 62002):
            raise RuntimeError('B站拒绝读取此账号内容，请检查登录和访问权限，或稍后再试。')
        if code != 0:
            raise RuntimeError(f'UP主信息读取失败（B站返回 {code}），请核对账号后重试。')
        data = response.get('data')
        if not isinstance(data, dict):
            raise RuntimeError('B站返回的UP主数据不完整，请稍后重试。')
        return data

    def search(self, query, page=1):
        uid = parse_uploader(query)
        if type(page) is not int or page < 1:
            raise ValueError('页码必须从1开始。')
        if uid is not None:
            data = self._data('/x/web-interface/card', {'mid': uid})
            card = data.get('card')
            if not isinstance(card, dict) or int(card.get('mid') or 0) != uid:
                raise RuntimeError('没有取得该UID的有效账号资料，请核对UID。')
            profile = Uploader(uid, plain(card.get('name')), str(card.get('face') or ''),
                               plain(card.get('sign')), int(card.get('fans') or 0), int(data.get('archive_count') or 0))
            return UploaderPage((profile,), 1, False)
        data = self._data('/x/web-interface/wbi/search/type',
                          {'search_type': 'bili_user', 'keyword': query.strip(), 'page': page, 'page_size': 20}, signed=True)
        rows = data.get('result')
        if rows is None and data.get('numResults') == 0:
            rows = []
        if not isinstance(rows, list) or 'numResults' not in data or 'numPages' not in data:
            raise RuntimeError('UP主搜索结果不完整，请稍后重试或输入UID。')
        profiles = tuple(Uploader(int(row['mid']), plain(row.get('uname')), str(row.get('upic') or ''),
            plain(row.get('usign')), int(row.get('fans') or 0), int(row.get('videos') or 0))
            for row in rows if isinstance(row, dict) and int(row.get('mid') or 0) > 0)
        return UploaderPage(profiles, int(data['numResults']), page < int(data['numPages']))

    def videos(self, uid, page=1):
        if type(uid) is not int or uid <= 0 or type(page) is not int or page < 1:
            raise ValueError('请先选择有效UP主，页码必须从1开始。')
        data = self._data('/x/space/wbi/arc/search',
                          {'mid': uid, 'pn': page, 'ps': 30, 'order': 'pubdate', 'keyword': ''}, signed=True)
        if data.get('is_risk') or data.get('gaia_res_type'):
            raise RuntimeError('B站要求进一步验证，未取得有效投稿清单；请稍后再试。')
        listing, pagination = data.get('list'), data.get('page')
        if not isinstance(listing, dict) or not isinstance(listing.get('vlist'), list) or not isinstance(pagination, dict) or 'count' not in pagination:
            raise RuntimeError('投稿清单不完整，请稍后重试，不能据此判断该UP没有视频。')
        videos = []
        for row in listing['vlist']:
            bvid = str(row.get('bvid') or '')
            valid = bool(re.fullmatch(r'BV[A-Za-z0-9]{10}', bvid))
            collaboration = row.get('is_union_video') in (True, 1, '1')
            if int(row.get('mid') or uid) != uid and not collaboration:
                raise RuntimeError('投稿返回的UP标识不匹配，已停止读取。')
            duration = 0
            pieces = str(row.get('length') or '').split(':')
            if 1 <= len(pieces) <= 3 and all(p.isdecimal() for p in pieces):
                for value in pieces:
                    duration = duration * 60 + int(value)
            videos.append(UploadVideo(bvid, plain(row.get('title')), plain(row.get('author')), duration,
                int(row.get('created') or 0), valid, '' if valid else '缺少有效视频链接', collaboration))
        total = int(pagination['count'])
        if total and not videos and (page - 1) * 30 < total:
            raise RuntimeError('该页投稿数据缺失，请稍后重试。')
        return UploadPage(tuple(videos), total, page * 30 < total)

    @staticmethod
    def avatar(url):
        url = 'https:' + url if url.startswith('//') else url
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ('http', 'https') or not (parsed.hostname or '').endswith('.hdslb.com'):
            return b''
        url = urllib.parse.urlunparse(parsed._replace(scheme='https'))
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        # 独立无凭证opener，头像CDN不接收B站登录Cookie。
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=8) as response:
            content = response.read(2 * 1024 * 1024 + 1)
            return content if len(content) <= 2 * 1024 * 1024 else b''
