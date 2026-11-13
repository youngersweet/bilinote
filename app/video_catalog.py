"""元数据目录：区分同BV分P与多个BV构成的合集，不下载正文。"""
from dataclasses import asdict, dataclass

from bili_service import BiliClient, parse_bvid, source_page


@dataclass(frozen=True)
class VideoPart:
    bvid: str
    page: int
    cid: int
    title: str
    duration: int
    video_title: str
    total_pages: int

    @property
    def source(self):
        return f'https://www.bilibili.com/video/{self.bvid}/' + (f'?p={self.page}' if self.total_pages > 1 else '')

    @property
    def key(self):
        return f'{self.bvid}-P{self.page:03}' if self.total_pages > 1 else self.bvid


@dataclass(frozen=True)
class RelatedVideo:
    bvid: str
    title: str


@dataclass(frozen=True)
class VideoInfo:
    bvid: str
    title: str
    parts: tuple[VideoPart, ...]
    requested_page: int | None
    related: tuple[RelatedVideo, ...] = ()
    season_id: int = 0
    season_title: str = ''
    season_count: int = 0
    owner_id: int = 0


class VideoCatalog:
    def __init__(self, client=None):
        self.client = client or BiliClient()

    def read(self, source):
        bvid, page = parse_bvid(source), source_page(source)
        self.client.load_saved()
        response = self.client.request('api.bilibili.com', '/x/web-interface/view', {'bvid': bvid})
        if response.get('code') != 0:
            raise RuntimeError('视频目录读取失败，请检查链接和访问权限。')
        data = response.get('data') or {}
        pages = data.get('pages')
        if not isinstance(pages, list) or not pages:
            raise RuntimeError('没有取得有效分集目录，未开始转换。')
        title = str(data.get('title') or bvid)
        parts = tuple(VideoPart(bvid, int(p['page']), int(p['cid']), str(p.get('part') or title),
                                int(p['duration']), title, len(pages)) for p in pages)
        if page and page not in {part.page for part in parts}:
            raise ValueError('链接指定的分P不在当前目录中，请检查链接。')
        season = data.get('ugc_season') or {}
        related = {}
        for section in season.get('sections') or []:
            for episode in section.get('episodes') or []:
                try:
                    bid = parse_bvid(str(episode.get('bvid') or ''))
                except ValueError:
                    continue
                related[bid] = RelatedVideo(bid, str(episode.get('title') or bid))
        return VideoInfo(bvid, title, parts, page, tuple(related.values()), int(season.get('id') or 0),
                         str(season.get('title') or ''), int(season.get('ep_count') or len(related)),
                         int(season.get('mid') or (data.get('owner') or {}).get('mid') or 0))

    def related(self, info):
        self.client.load_saved()
        if len(info.related) >= info.season_count:
            return info.related
        videos = {}
        page = 1
        while True:
            response = self.client.request('api.bilibili.com', '/x/polymer/web-space/seasons_archives_list',
                {'mid': info.owner_id, 'season_id': info.season_id, 'page_num': page,
                 'page_size': 30, 'sort_reverse': 'false'})
            data = response.get('data') or {}
            items = data.get('items') or data
            archives = items.get('archives')
            pagination = items.get('page') or data.get('page') or {}
            if response.get('code') != 0 or not isinstance(archives, list):
                raise RuntimeError('所属合集目录未读取完整，请稍后重试；未自动加入其他视频。')
            for item in archives:
                try:
                    bvid = parse_bvid(str(item.get('bvid') or ''))
                except ValueError:
                    continue
                videos[bvid] = RelatedVideo(bvid, str(item.get('title') or bvid))
            total = int(pagination.get('total') or info.season_count)
            if len(videos) >= total:
                return tuple(videos.values())
            if not archives or len(archives) < 30 or page >= 100:
                raise RuntimeError('所属合集目录未读取完整，未自动加入其他视频。')
            page += 1


def collection_plan(infos, related=()):
    first = infos[0]
    parts = {part.key: asdict(part) | {'key': part.key, 'source': part.source} for info in infos for part in info.parts}
    loaded = {info.bvid for info in infos}
    return {'id': f'season-{first.season_id}' if first.season_id else first.bvid,
            'title': first.season_title or first.title,
            'entries': list(parts.values()),
            'unexpanded': [asdict(video) for video in related if video.bvid not in loaded]}
