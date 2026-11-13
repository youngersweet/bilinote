"""Complete visual presets and validated, portable appearance snapshots."""
import re
from reading_preferences import colors

EXTRA = dict(theme_id='paper', app_background='#eee9df', text_color='#302d28', accent_color='#88643d',
             reading_font='SimSun', reading_line_height=165, reading_width=760, ui_density=10, ui_radius=12)
KEYS = tuple(EXTRA) + ('reading_background', 'reading_font_size', 'match_app_background', 'background_image', 'background_transparency')
THEMES = {}
for key, name, description, bg, app, fg, accent, font in [
    ('paper', '纸间 · 阅读', '暖纸与墨色，舒展书页', '#faf6ed', '#eee9df', '#302d28', '#88643d', 'SimSun'),
    ('forest', '雾林 · 安静', '柔绿与薄雾，安静留白', '#f0f5ef', '#e2ebe3', '#28392f', '#406a53', 'Microsoft YaHei UI'),
    ('sky', '晴空 · 清爽', '冷白与浅蓝，轻盈清晰', '#f8fbff', '#e8f0fa', '#233548', '#2266ac', 'Microsoft YaHei UI'),
    ('motion', '跃动 · 动感', '桃色与珊瑚，明快有序', '#fff8f3', '#f6e4dc', '#43312d', '#b34229', 'Microsoft YaHei UI'),
    ('future', '星航 · 未来', '深蓝与冰光，精细秩序', '#172432', '#101c29', '#dae8f4', '#86c7ee', 'Microsoft YaHei UI'),
    ('night', '夜读 · 深色', '石墨与暖光，低调柔和', '#252629', '#1d1e21', '#e5e0d6', '#d1b185', 'Microsoft YaHei UI'),
]:
    THEMES[key] = dict(EXTRA, theme_id=key, name=name, description=description, reading_background=bg,
                       app_background=app, text_color=fg, accent_color=accent, reading_font=font,
                       reading_font_size=17, match_app_background=False, background_image='', background_transparency=80)

def snapshot(values):
    return {key: values[key] for key in KEYS if key in values}

def clean(values):
    result = snapshot(THEMES['paper'])
    for key in KEYS:
        v = values.get(key)
        if key in ('reading_background', 'app_background', 'text_color', 'accent_color'):
            if isinstance(v, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', v): result[key] = v.lower()
        elif key in ('reading_font_size', 'reading_line_height', 'reading_width', 'ui_density', 'ui_radius', 'background_transparency'):
            low, high = {'reading_font_size': (12,32), 'reading_line_height':(120,220), 'reading_width':(400,1400), 'ui_density':(4,18), 'ui_radius':(0,20), 'background_transparency':(0,100)}[key]
            if type(v) is int and low <= v <= high: result[key] = v
        elif key == 'match_app_background':
            if type(v) is bool: result[key] = v
        elif key == 'reading_font':
            if v in ('Microsoft YaHei UI', 'SimSun', 'KaiTi'): result[key] = v
        elif isinstance(v, str): result[key] = v[:4096]
    return result

def blend(base, tint, amount):
    a = [int(base[i:i+2],16) for i in (1,3,5)]
    b = [int(tint[i:i+2],16) for i in (1,3,5)]
    return '#' + ''.join(f'{round(x*(1-amount)+y*amount):02x}' for x,y in zip(a,b))

def ui_colors(values):
    bg = values['reading_background'] if values.get('match_app_background') else values['app_background']
    c = colors(bg)
    c['selection'] = blend(bg, values['accent_color'], .18)
    c['hover'] = blend(bg, values['accent_color'], .08)
    c['border'] = blend(bg, c['foreground'], .16)
    return c

def stylesheet(values):
    from ui_theme import STYLE
    p = clean(values)
    bg = p['reading_background'] if p['match_app_background'] else p['app_background']
    c = ui_colors(p)
    fg, accent = c['foreground'], p['accent_color']
    mapping = {'#1d1d1f':fg, '#6e6e73':c['muted'], '#515158':c['muted'], '#005bb5':accent,
               '#0071e3':accent, '#0077ed':accent, '#0066cc':accent,
               '#dceafa':c['selection'], '#e8e8ed':c['border'], '#d2d2d7':c['border'], '#e2e2e7':c['border'],
               '#e5e5ea':c['border'], '#f5f5f7':bg, '#ededf0':bg, '#f9f9fb':bg, '#fafafc':bg,
               '#ffffff':bg, '#fff':bg, 'white':bg}
    for token in ('#e8e8ed','#e0e0e6','#f0f0f5','#f2f2f5','#eaeaf0','#eeeef3','#eeeef2','#f2f2f7','#c5dbf3'):
        mapping[token] = c['hover']
    style = re.sub(r'#[0-9a-fA-F]{3,6}\b|\bwhite\b', lambda m: mapping.get(m[0].lower(), m[0]), STYLE)
    radius, density = p['ui_radius'], p['ui_density']
    ink = colors(accent)['foreground']
    style += f'''
    QFrame#rail, QFrame#library, QFrame#reading {{ border-radius: {radius}px; }}
    QPushButton, QToolButton, QLineEdit, QComboBox, QSpinBox {{ border-radius: {min(radius,10)}px; }}
    QPushButton {{ padding: {density}px 16px; }}
    QListWidget#history::item {{ padding: {density}px; }}
    QPushButton#primary, QToolButton#primary, QMenu::item:selected {{ color: {ink}; background: {accent}; }}
    QToolButton:disabled, QPushButton:disabled, QPushButton#primary:disabled, QToolButton#primary:disabled {{ color: {c['muted']}; }}
    QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {c['muted']}; border-radius: 9px; background: {bg}; }}
    QRadioButton::indicator:checked {{ width: 8px; height: 8px; border: 5px solid {accent}; border-radius: 9px; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {c['muted']}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {accent}; width: 14px; margin: -5px 0; border-radius: 7px; }}
    QDialog QWidget {{ background: {bg}; }}
    QWidget#appearanceControls {{ background: {bg}; }}
    QToolButton:checked {{ background: {c["selection"]}; border: 1px solid {accent}; }}
    QScrollArea {{ border: none; background: {bg}; }}
    '''
    return style
