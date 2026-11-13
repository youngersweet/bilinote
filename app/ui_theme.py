"""Bili Note：浅色分层、蓝色强调、统一矢量图标。"""
from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer
from pathlib import Path

ACCENT = '#0071e3'
GREEN = ACCENT  # Existing components share the same accent.
ASSETS = Path(__file__).resolve().parent / 'assets'


def book_icon():
    path = ASSETS / 'bili-note.ico'
    return QIcon(str(path if path.is_file() else ASSETS / 'bili-note.svg'))


PATHS = {
    'note': '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/>',
    'notes': '<path d="M5 5h13a2 2 0 0 1 2 2v13H7a3 3 0 0 1-3-3V6a2 2 0 0 1 2-2z"/><path d="M8 4v16M12 9h4M12 13h4"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'search': '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
    'user': '<circle cx="12" cy="8" r="4"/><path d="M4.5 21v-2a7.5 7.5 0 0 1 15 0v2"/>',
    'settings': '<path d="m9 3-.8 3-2.7 1.5L3 7v4l2.5 1.5L6 16l-1.5 2 3 2 2.5-1 3 .5 1.5 2 3-2-.5-3 2-2.5 2-1V9l-2.5-.5L17 6l-1-3z"/><circle cx="12" cy="12" r="3"/>',
    'folder': '<path d="M3 6h7l2 2h9v12H3z"/><path d="M3 10h18"/>',
    'copy': '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    'more': '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    'import': '<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>',
    'check': '<path d="m5 12 4 4L19 6"/>',
    'refresh': '<path d="M20 9a8 8 0 1 0 0 6M20 3v6h-6"/>',
    'video': '<rect x="3" y="5" width="13" height="14" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
}


def icon(name, color='#777b80', size=24):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</svg>'
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode('utf-8'))).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


STYLE = r'''
QWidget { font-family: "Microsoft YaHei UI"; font-size: 13px; color: #1d1d1f; }
QMainWindow, QDialog { background: #f5f5f7; }
QWidget#workspace { background: #f5f5f7; }
QFrame#rail { background: #ededf0; border: none; border-radius: 14px; }
QFrame#library { background: #f9f9fb; border: 1px solid #e2e2e7; border-radius: 14px; }
QFrame#reading { background: white; border: 1px solid #e2e2e7; border-radius: 14px; }
QFrame#readerHeader { background: #ffffff; border-bottom: 1px solid #e8e8ed; border-top-left-radius: 13px; border-top-right-radius: 13px; }
QFrame#footer { background: #fafafc; border-top: 1px solid #e8e8ed; border-bottom-left-radius: 13px; border-bottom-right-radius: 13px; }
QLabel { background: transparent; }
QLabel#muted { color: #6e6e73; font-size: 12px; }
QLabel#title { font-size: 21px; font-weight: 600; }
QLabel#dialogTitle { font-size: 25px; font-weight: 600; }
QLabel#green { color: #0071e3; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 7px; }
QToolButton:hover { background: #e8e8ed; }
QToolButton:focus, QPushButton:focus { border: 1px solid #0071e3; }
QToolButton#deleteNotes { color: #b42318; }
QToolButton#deleteNotes:hover { background: #fce9e7; }
QToolButton:disabled, QToolButton#deleteNotes:disabled { color: #aeaeb2; }
QToolButton::menu-indicator { image: none; }
QToolButton#nav { text-align: left; padding: 9px 10px; border-radius: 8px; }
QToolButton#nav:hover { background: #e0e0e6; }
QToolButton#nav:checked { background: #dceafa; color: #005bb5; }
QPushButton { background: white; border: 1px solid #d2d2d7; border-radius: 8px; padding: 9px 17px; }
QPushButton:hover { background: #f0f0f5; }
QPushButton:pressed { background: #e5e5ea; }
QPushButton:disabled { color: #aeaeb2; background: #f2f2f5; border-color: #e5e5ea; }
QPushButton#primary, QToolButton#primary { background: #0071e3; border: 1px solid #0071e3; color: white; }
QPushButton#primary:hover, QToolButton#primary:hover { background: #0077ed; }
QPushButton#primary:disabled { background: #c5dbf3; border-color: #c5dbf3; color: #ffffff; }
QLineEdit, QComboBox, QSpinBox { background: white; border: 1px solid #d2d2d7; border-radius: 8px; padding: 8px 10px; selection-background-color: #dceafa; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #0071e3; }
QLineEdit#search { background: #eaeaf0; border: 1px solid transparent; padding: 8px 10px; }
QLineEdit#search:focus { background: white; border-color: #0071e3; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #dceafa; selection-color: #1d1d1f; border: 1px solid #d2d2d7; padding: 5px; }
QListWidget#history { background: transparent; border: none; outline: none; }
QListWidget#history::item { border: none; border-radius: 9px; margin: 3px 0; }
QListWidget#history::item:hover { background: #eeeeF3; }
QListWidget#history::item:selected { background: #dceafa; }
QTabWidget::pane { border: none; background: white; }
QTabBar::tab { background: #eeeef2; color: #6e6e73; padding: 7px 22px; margin: 8px 2px; border: 1px solid transparent; border-radius: 7px; }
QTabBar::tab:selected { background: white; color: #1d1d1f; border-color: #d2d2d7; }
QTabBar::tab:hover { color: #0071e3; }
QTextBrowser, QPlainTextEdit { border: none; background: white; selection-background-color: #dceafa; }
QTextBrowser { font-size: 16px; }
QPlainTextEdit#status { background: #eeeef2; border: none; border-radius: 8px; padding: 10px; color: #515158; }
QPlainTextEdit#links { background: white; border: 1px solid #d2d2d7; border-radius: 10px; padding: 12px; }
QPlainTextEdit#links:focus { border-color: #0071e3; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #c1c1c7; min-height: 36px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #8e8e93; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal:hover { background: #d2d2d7; border-radius: 4px; margin: 24px 2px; }
QProgressBar { border: none; background: #e5e5ea; border-radius: 2px; max-height: 3px; min-height: 3px; }
QProgressBar::chunk { background: #0071e3; border-radius: 2px; }
QMenu { background: #fafafc; border: 1px solid #d2d2d7; border-radius: 8px; padding: 6px; }
QMenu::item { padding: 9px 24px; border-radius: 5px; }
QMenu::item:selected { background: #0071e3; color: white; }
QMenu::item:disabled { color: #aeaeb2; }
QCheckBox, QRadioButton { spacing: 8px; padding: 5px 0; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #a1a1a6; border-radius: 4px; background: white; }
QCheckBox::indicator:checked { background: #0071e3; border-color: #0071e3; image: url(CHECKMARK); }
QCheckBox::indicator:disabled { background: #e5e5ea; border-color: #d2d2d7; }
QGroupBox { border: none; margin-top: 4px; }
QToolTip { background: #fff; color: #1d1d1f; border: 1px solid #d2d2d7; padding: 6px; }
QTableWidget, QListWidget { background: white; border: 1px solid #e2e2e7; border-radius: 8px; gridline-color: #f2f2f7; }
QTableWidget::item { padding: 8px; }
QTableWidget::item:selected { background: #dceafa; color: #1d1d1f; }
QHeaderView::section { background: #f5f5f7; padding: 10px; border: none; color: #6e6e73; }
'''.replace('CHECKMARK', (ASSETS / 'check.svg').as_posix())
