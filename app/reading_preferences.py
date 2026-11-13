"""阅读外观的可保存值；不依赖桌面组件。"""
import re

DEFAULT_FONT_SIZE = 16
DEFAULT_BACKGROUND = '#ffffff'
BACKGROUNDS = [('纯白（默认）', DEFAULT_BACKGROUND), ('浅豆绿', '#e8f1e6'),
               ('暖米色', '#f5f0e4'), ('浅灰', '#eff0f2'), ('雾蓝', '#e8f0fa'),
               ('淡紫', '#eee9f6'), ('樱花粉', '#f9e9ed'), ('杏仁黄', '#faf2d8'),
               ('深石墨', '#25262a'), ('午夜蓝', '#182536')]


def transparency(value):
    return value if type(value) is int and 0 <= value <= 100 else 80


def font_size(value):
    return value if type(value) is int and 12 <= value <= 32 else DEFAULT_FONT_SIZE


def background(value):
    return value.lower() if isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value) else DEFAULT_BACKGROUND


def colors(value):
    value = background(value)
    channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4 for channel in channels]
    dark = sum(channel * weight for channel, weight in zip(linear, (.2126, .7152, .0722))) < .2
    return {'background': value, 'foreground': '#e3eae5' if dark else '#1d1d1f',
            'muted': '#bdc5d0' if dark else '#6e6e73', 'border': '#4a5260' if dark else '#e5e5ea',
            'link': '#a6cdff' if dark else '#0066cc', 'hover': '#394454' if dark else '#eeeef2',
            'selection': '#3c5679' if dark else '#dceafa'}
