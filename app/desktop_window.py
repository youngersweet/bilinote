"""Qt 三栏桌面；界面状态与后台处理结果分别管理。"""
import json
import ctypes
import os
from pathlib import Path
import threading
import time

from PySide6.QtCore import Qt, Signal, QObject, QTimer, QSize, QUrl, QSignalBlocker, QPoint
from PySide6.QtGui import (QFont, QDesktopServices, QKeySequence, QShortcut, QImage,
                           QTextBlockFormat, QPalette, QColor, QTextCursor, QTextCharFormat, QPainter)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QHBoxLayout,
    QVBoxLayout, QLabel, QToolButton, QLineEdit, QListWidget, QListWidgetItem, QSplitter,
    QTabWidget, QTextBrowser, QStackedWidget, QProgressBar, QMenu, QFileDialog, QDialog,
    QMessageBox, QSizePolicy, QWidgetAction, QSystemTrayIcon, QPushButton, QScrollArea)

from bili_service import BiliClient, DATA_DIR, summarize
from content_pipeline import collect_video, prepare_visual, open_browser_channel
from model_settings import load_settings
from note_library import load_preferences, save_preferences, scan_library, filter_entries, find_note, read_result, delete_note_folder
from ui_theme import STYLE, GREEN, icon, book_icon
from video_folders import migrate_legacy_folders, rebase_paths
from task_queue import VideoTaskQueue, TaskSpec, process_video_task, STATES
from reading_preferences import colors
from reading_settings import ReadingControls
from visual_themes import clean, snapshot, stylesheet, ui_colors
from backgrounds import BackgroundPaint, BackgroundSurface
from layout_controls import BlankToggleFrame

_application = None


def ensure_application():
    global _application
    if QApplication.instance() is None and os.name == 'nt':
        try:
            identity = ctypes.WinDLL('shell32').SetCurrentProcessExplicitAppUserModelID
            identity.argtypes = [ctypes.c_wchar_p]
            identity.restype = ctypes.c_long
            identity('BiliNote.Desktop')
        except OSError:
            pass
    _application = QApplication.instance() or QApplication([])
    _application.setApplicationName('Bili Note')
    _application.setWindowIcon(book_icon())
    _application.setOrganizationName('BiliNote')
    _application.setFont(QFont('Microsoft YaHei UI', 10))
    _application.setStyleSheet(STYLE)
    return _application


class MainThreadBridge(QObject):
    callback = Signal(object)


class Reader(QTextBrowser):
    font_step = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.material_root = None
        self._link_color = None
        self._appearance = {}
        self.background = BackgroundPaint()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self.open_link)
        self.setFont(QFont('Microsoft YaHei UI', 11))
        self.document().setDocumentMargin(34)

    def reading_style(self, size, color, image='', transparency=80, appearance=None):
        cursor = self.cursorForPosition(QPoint(8, 8))
        before = self.cursorRect(cursor).top()
        self._appearance = appearance or {}
        palette = colors(color)
        palette['foreground'] = self._appearance.get('text_color', palette['foreground'])
        palette['link'] = self._appearance.get('accent_color', palette['link'])
        self._link_color = palette['link']
        self.background.configure(color, image, transparency)
        photo = not self.background.pixmap.isNull()
        fill = 'transparent' if photo else palette['background']
        self.setStyleSheet(f'QTextBrowser {{ background: {fill}; color: {palette["foreground"]}; '
                          f'font-family: \"{self._appearance.get("reading_font", "Microsoft YaHei UI")}\"; font-size: {size}px; border: none; selection-background-color: {palette["selection"]}; }}')
        font = QFont(self.font())
        font.setFamily(self._appearance.get('reading_font', 'Microsoft YaHei UI'))
        font.setPixelSize(size)
        self.setFont(font)
        self.document().setDefaultFont(font)
        widget_palette = self.palette()
        widget_palette.setColor(QPalette.ColorRole.Base, QColor('transparent' if photo else palette['background']))
        widget_palette.setColor(QPalette.ColorRole.Text, QColor(palette['foreground']))
        widget_palette.setColor(QPalette.ColorRole.Link, QColor(palette['link']))
        self.setPalette(widget_palette)
        self.viewport().setAutoFillBackground(not photo)
        self._style_links()
        self._format_blocks()
        self.document().documentLayout().documentSize()
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + self.cursorRect(cursor).top() - before)
        self.viewport().update()

    def paintEvent(self, event):
        if not self.background.pixmap.isNull():
            painter = QPainter(self.viewport())
            self.background.draw(painter, self.viewport().rect())
            painter.end()
        super().paintEvent(event)

    def _style_links(self):
        if not self._link_color:
            return
        spans = []
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isAnchor():
                    spans.append((fragment.position(), fragment.length()))
                iterator += 1
            block = block.next()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._link_color))
        for start, length in spans:
            cursor = QTextCursor(self.document())
            cursor.setPosition(start)
            cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(fmt)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.angleDelta().y():
            self.font_step.emit(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def open_link(self, url):
        if url.scheme() in ('http', 'https'):
            QDesktopServices.openUrl(url)

    def loadResource(self, kind, url):
        if kind == 2:  # 不在阅读文档时隐式加载远程资源或视频目录外的图片。
            path = Path(url.toLocalFile()) if url.isLocalFile() else None
            if path and self.material_root:
                try:
                    path.resolve().relative_to(self.material_root)
                    return QImage(str(path))
                except (OSError, ValueError):
                    pass
            return QImage()
        return super().loadResource(kind, url)

    def markdown(self, text, root=None):
        self.material_root = Path(root).resolve() if root else None
        if self.material_root:
            self.document().setBaseUrl(QUrl.fromLocalFile(str(self.material_root) + '/'))
        self.setMarkdown(text)
        self._style_links()
        self._format_blocks()
        self.verticalScrollBar().setValue(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._format_blocks()

    def _format_blocks(self):
        width = self._appearance.get('reading_width', 1400)
        margin = max(0, (self.viewport().width() - width) / 2 - self.document().documentMargin())
        block = self.document().begin()
        while block.isValid():
            cursor = self.textCursor()
            cursor.setPosition(block.position())
            fmt = block.blockFormat()
            fmt.setLineHeight(self._appearance.get('reading_line_height', 155), QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            fmt.setLeftMargin(margin)
            fmt.setRightMargin(margin)
            fmt.setTopMargin(16 if fmt.headingLevel() else 3)
            fmt.setBottomMargin(9)
            cursor.setBlockFormat(fmt)
            block = block.next()


def tool(name, tooltip, parent=None, *, color='#777b80', text=''):
    button = QToolButton(parent)
    button.setIcon(icon(name, color))
    button.setIconSize(QSize(20, 20))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if text:
        button.setText(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    return button


def duration_text(seconds):
    seconds = int(seconds or 0)
    return f'{seconds//3600}:{seconds//60%60:02}:{seconds%60:02}' if seconds >= 3600 else f'{seconds//60}:{seconds%60:02}'


class App(QMainWindow):
    def __init__(self, *, check_online=True, preferences_path=None, migrate_folders=True, enable_tray=False):
        ensure_application()
        super().__init__()
        self.setWindowTitle('Bili Note')
        self.setWindowIcon(book_icon())
        area = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1220, area.width() - 60), min(820, area.height() - 60))
        self.setMinimumSize(980, 650)
        self.preferences_path = preferences_path
        self.preferences = load_preferences(preferences_path)
        startup_notice = ''
        if migrate_folders:
            try:
                changes = migrate_legacy_folders(self.preferences['output_dir'])
                if changes:
                    self.preferences['imports'] = rebase_paths(self.preferences['imports'], changes)
                    save_preferences(self.preferences, self.preferences_path)
                    startup_notice = f'已将 {len(changes)} 个 BV 目录改为视频标题，原笔记与正文已保留。'
            except (OSError, ValueError) as error:
                startup_notice = '部分目录改名未完成，已保留可用资料 · ' + str(error)
        self.client = BiliClient()
        self.account_status = {'logged_in': False, 'saved': False}
        self.busy = False
        self._closing = False
        self._exit_requested = False
        self.tray = None
        self.follow_task = True
        self.current_path = None
        self.active_path = None
        self.active_result = None
        self.result = None
        self.current_markdown = ''
        self.entries = []
        self.selection_mode = False
        self.checked_paths = set()
        self._dialogs = []
        self.started_at = None
        self.selected_task_id = None
        self.task_dialog = None
        self._task_revisions = {}
        self._indexed_tasks = set()
        self.bridge = MainThreadBridge(self)
        self.bridge.callback.connect(self._dispatch)
        self.queue = VideoTaskQueue(limit=self.preferences['concurrency'],
            runner=lambda spec, progress, materials: process_video_task(spec, progress, materials,
                client_factory=lambda: BiliClient(self.client.store)),
            on_update=lambda snapshot: self.post(lambda: self.on_task_update(snapshot)))
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self._tick)
        self._layout()
        self._library_sizes = [300, 850]
        self.apply_layout_visibility()
        self.apply_reading_style()
        self._shortcuts()
        self.refresh_model_label()
        self.refresh_library(select_latest=True)
        if enable_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self._setup_tray()
        if startup_notice:
            self.set_status(startup_notice)
        if check_online:
            QTimer.singleShot(150, self.check_account)

    def _layout(self):
        central = BackgroundSurface()
        central.setObjectName('workspace')
        self.setCentralWidget(central)
        shell = QHBoxLayout(central)
        shell.setContentsMargins(10, 10, 10, 10)
        shell.setSpacing(10)
        rail = BlankToggleFrame()
        self.navigation_rail = rail
        rail.blank_double_clicked.connect(self.toggle_immersive)
        rail.blank_hint = '双击进入沉浸阅读'
        rail.setObjectName('rail')
        rail.setFixedWidth(144)
        nav = QVBoxLayout(rail)
        nav.setContentsMargins(10, 22, 10, 16)
        nav.setSpacing(8)
        brand = QLabel('Bili Note')
        self.brand_label = brand
        brand.setStyleSheet('font-size: 19px; font-weight: 600; color: #1d1d1f;')
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(brand)
        nav.addSpacing(16)
        notes = tool('notes', '全部笔记', color=GREEN, text='全部笔记')
        self.notes_button = notes
        notes.setToolTipDuration(5000)
        notes.setObjectName('nav')
        notes.setFixedSize(124, 42)
        notes.setCheckable(True)
        notes.setChecked(True)
        notes.clicked.connect(self.toggle_library)
        nav.addWidget(notes)
        nav.addStretch()
        self.account_button = tool('user', 'B站账户', color='#6e6e73', text='B站账户')
        self.account_button.setObjectName('nav')
        self.account_button.setFixedSize(124, 42)
        self.account_button.clicked.connect(self.open_account)
        nav.addWidget(self.account_button)
        self.settings_button = tool('settings', '设置', color='#6e6e73', text='设置')
        self.settings_button.setObjectName('nav')
        self.settings_button.setFixedSize(124, 42)
        self.settings_button.clicked.connect(self.open_settings)
        nav.addWidget(self.settings_button)
        shell.addWidget(rail)
        self.navigation_handle = BlankToggleFrame()
        self.navigation_handle.setObjectName('rail')
        self.navigation_handle.setFixedWidth(14)
        self.navigation_handle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.navigation_handle.setAccessibleName('双击恢复导航侧栏')
        self.navigation_handle.blank_hint = '双击恢复侧栏'
        self.navigation_handle.blank_double_clicked.connect(self.toggle_immersive)
        shell.addWidget(self.navigation_handle)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(8)
        shell.addWidget(self.splitter, 1)
        library = QFrame()
        self.library_panel = library
        library.setObjectName('library')
        library.setMinimumWidth(250)
        library.setMaximumWidth(430)
        left = QVBoxLayout(library)
        left.setContentsMargins(8, 8, 8, 8)
        left.setSpacing(0)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(8, 14, 4, 16)
        search_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName('search')
        self.search.setPlaceholderText('搜索视频或笔记')
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon('search', '#929793', 16), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self.filter_history)
        search_row.addWidget(self.search, 1)
        self.new_button = tool('plus', '添加视频  Ctrl+N', color='white', text='添加')
        self.new_button.setObjectName('primary')
        self.new_button.clicked.connect(self.new_video)
        search_row.addWidget(self.new_button)
        left.addLayout(search_row)
        label_row = QHBoxLayout()
        label_row.setContentsMargins(20, 0, 14, 10)
        self.library_count = QLabel('全部笔记')
        self.library_count.setObjectName('muted')
        label_row.addWidget(self.library_count)
        label_row.addStretch()
        self.selection_button = QToolButton()
        self.selection_button.setText('选择')
        self.selection_button.setAccessibleName('选择笔记')
        self.selection_button.setToolTip('选择笔记并同步删除对应文件')
        self.selection_button.clicked.connect(self.toggle_selection_mode)
        label_row.addWidget(self.selection_button)
        refresh = tool('refresh', '刷新笔记列表')
        refresh.setIconSize(QSize(15, 15))
        refresh.clicked.connect(lambda: self.refresh_library())
        label_row.addWidget(refresh)
        left.addLayout(label_row)
        self.selection_bar = QWidget()
        selection_row = QHBoxLayout(self.selection_bar)
        selection_row.setContentsMargins(14, 0, 10, 8)
        selection_row.setSpacing(4)
        self.select_all_button = QToolButton()
        self.select_all_button.setText('全选')
        self.select_all_button.setToolTip('全选当前搜索结果中可删除的笔记')
        self.select_all_button.clicked.connect(self.toggle_select_all)
        selection_row.addWidget(self.select_all_button)
        self.selection_count = QLabel('已选 0')
        self.selection_count.setObjectName('muted')
        selection_row.addWidget(self.selection_count)
        selection_row.addStretch()
        self.delete_button = QToolButton()
        self.delete_button.setText('删除')
        self.delete_button.setObjectName('deleteNotes')
        self.delete_button.clicked.connect(self.delete_selected_notes)
        selection_row.addWidget(self.delete_button)
        self.selection_bar.hide()
        left.addWidget(self.selection_bar)
        self.history_list = QListWidget()
        self.history_list.setObjectName('history')
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_list.currentItemChanged.connect(self._history_selected)
        self.history_list.itemClicked.connect(self._history_clicked)
        self.library_stack = QStackedWidget()
        self.library_stack.addWidget(self.history_list)
        self.no_results = QLabel('还没有笔记\n点击上方 ＋ 添加一个视频')
        self.no_results.setObjectName('muted')
        self.no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_results.setMinimumHeight(100)
        self.library_stack.addWidget(self.no_results)
        left.addWidget(self.library_stack, 1)
        self.import_button = tool('import', '导入已有结果', text='导入已有笔记')
        self.import_button.setStyleSheet('padding: 14px 20px; text-align: left; color: #878c88;')
        self.import_button.clicked.connect(self.import_result)
        left.addWidget(self.import_button)
        self.splitter.addWidget(library)

        reading = QFrame()
        self.reading_panel = reading
        reading.setObjectName('reading')
        right = QVBoxLayout(reading)
        right.setContentsMargins(1, 1, 1, 1)
        right.setSpacing(0)
        header = BlankToggleFrame()
        self.reader_header = header
        header.blank_double_clicked.connect(self.toggle_reading_tabs)
        header.setObjectName('readerHeader')
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(24, 20, 16, 18)
        titles = QVBoxLayout()
        titles.setSpacing(6)
        self.video_title = QLabel('Bili Note')
        self.video_title.setObjectName('title')
        self.video_title.setWordWrap(True)
        self.video_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        titles.addWidget(self.video_title)
        self.video_meta = QLabel('视频里的知识，随时回来读。')
        self.video_meta.setObjectName('muted')
        titles.addWidget(self.video_meta)
        header_row.addLayout(titles, 1)
        self.header_blank_area = BlankToggleFrame()
        self.header_blank_area.setMinimumSize(36, 44)
        self.header_blank_area.setAccessibleName('双击展开或隐藏阅读标签栏')
        self.header_blank_area.blank_double_clicked.connect(self.toggle_reading_tabs)
        header_row.addWidget(self.header_blank_area)
        self.reading_button = QToolButton()
        self.reading_button.setText('Aa')
        self.reading_button.setAccessibleName('阅读设置')
        self.reading_button.setToolTip('主题与阅读外观')
        self.reading_button.setToolTipDuration(5000)
        reading_menu = QMenu(self)
        self.reading_menu = reading_menu
        self._appearance_preview = None
        self._appearance_modal = False
        reading_menu.aboutToShow.connect(self.begin_appearance_preview)
        reading_menu.aboutToHide.connect(self.cancel_appearance_preview)
        self.reading_controls = ReadingControls(self.preferences, reading_menu)
        self.reading_controls.changed.connect(self.change_reading_style)
        self.reading_controls.modal_started.connect(self.begin_appearance_modal)
        self.reading_controls.modal_finished.connect(self.end_appearance_modal)
        self.reading_controls.confirmed.connect(self.commit_appearance_preview)
        self.reading_controls.cancelled.connect(reading_menu.hide)
        reading_action = QWidgetAction(reading_menu)
        appearance_scroll = QScrollArea()
        appearance_scroll.setWidgetResizable(True)
        appearance_scroll.setFrameShape(QFrame.Shape.NoFrame)
        appearance_scroll.setWidget(self.reading_controls)
        appearance_scroll.setMinimumWidth(380)
        appearance_scroll.setFixedHeight(min(620, QApplication.primaryScreen().availableGeometry().height() - 170))
        appearance_body = QWidget()
        appearance_body_layout = QVBoxLayout(appearance_body)
        appearance_body_layout.setContentsMargins(0,0,0,0)
        appearance_body_layout.addWidget(appearance_scroll)
        appearance_body_layout.addWidget(self.reading_controls.actions_widget)
        reading_action.setDefaultWidget(appearance_body)
        reading_menu.addAction(reading_action)
        self.reading_button.setMenu(reading_menu)
        self.reading_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        header_row.addWidget(self.reading_button)
        self.copy_button = tool('copy', '复制当前正文', text='复制')
        self.copy_button.clicked.connect(self.copy_text)
        header_row.addWidget(self.copy_button)
        self.folder_button = tool('folder', '打开笔记目录')
        self.folder_button.clicked.connect(self.open_folder)
        header_row.addWidget(self.folder_button)
        self.more_button = tool('more', '更多操作')
        menu = QMenu(self)
        self.summary_action = menu.addAction('重新生成笔记', self.generate)
        self.visual_action = menu.addAction('补充画面校正', self.manual_visual)
        menu.addSeparator()
        self.source_action = menu.addAction('打开原视频', self.open_source)
        self.collection_action = menu.addAction('查看合集目录', self.open_collection_index)
        menu.addAction('浏览器读取通道', self.browser_channel)
        self.more_button.setMenu(menu)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        header_row.addWidget(self.more_button)
        right.addWidget(header)
        self.generate_button = QPushButton('生成笔记')
        self.generate_button.setObjectName('primary')
        self.generate_button.clicked.connect(self.generate)
        header_row.addWidget(self.generate_button)
        self.view_stack = QStackedWidget()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch()
        mark = QLabel()
        mark.setPixmap(icon('notes', '#9dbfea', 68).pixmap(68, 68))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(mark)
        self.empty_title = QLabel('从一个视频，开始积累。')
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title.setStyleSheet('font-size: 25px; font-weight: 600; color: #1d1d1f; padding-top: 16px;')
        empty_layout.addWidget(self.empty_title)
        self.empty_hint = QLabel('粘贴视频链接，整理成随时可读的笔记。\n首次使用可先登录 B站、设置模型；也支持仅提取正文。')
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setObjectName('muted')
        empty_layout.addWidget(self.empty_hint)
        onboarding = QHBoxLayout()
        onboarding.addStretch()
        for label, callback in (('添加视频', self.new_video),
                                ('B站登录', self.open_account),
                                ('模型设置', self.open_model_settings)):
            button = QPushButton(label)
            if label == '添加视频':
                button.setObjectName('primary')
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(callback)
            onboarding.addWidget(button)
        onboarding.addStretch()
        empty_layout.addLayout(onboarding)
        empty_layout.addStretch()
        self.view_stack.addWidget(empty)
        self.tabs = QTabWidget()
        self.note_text, self.raw_text, self.materials_text = Reader(), Reader(), Reader()
        for reader in (self.note_text, self.raw_text, self.materials_text):
            reader.font_step.connect(self.step_reading_font)
        self.tabs.addTab(self.note_text, '笔记')
        self.tabs.addTab(self.raw_text, '字幕')
        self.tabs.addTab(self.materials_text, '来源')
        self.tabs.currentChanged.connect(lambda: self._update_actions())
        self.view_stack.addWidget(self.tabs)
        right.addWidget(self.view_stack, 1)
        footer = QFrame()
        footer.setObjectName('footer')
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(24, 10, 24, 8)
        footer_layout.setSpacing(7)
        status_row = QHBoxLayout()
        self.stage = QLabel('准备好了')
        self.stage.setObjectName('muted')
        self.stage.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status_row.addWidget(self.stage, 1)
        self.elapsed = QLabel('')
        self.elapsed.setObjectName('muted')
        status_row.addWidget(self.elapsed)
        self.queue_status_button = tool('video', '查看全部视频任务', text='任务')
        self.queue_status_button.setToolTipDuration(5000)
        self.queue_status_button.clicked.connect(self.show_tasks)
        status_row.addWidget(self.queue_status_button)
        self.model_label = QLabel('')
        self.model_label.setObjectName('muted')
        status_row.addWidget(self.model_label)
        footer_layout.addLayout(status_row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.hide()
        footer_layout.addWidget(self.progress)
        right.addWidget(footer)
        self.splitter.addWidget(reading)
        self.splitter.setSizes([300, 850])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self._update_actions()

    def _shortcuts(self):
        for keys, callback in [('Ctrl+N', self.new_video), ('Ctrl+F', self.focus_library_search),
                               ('Ctrl+,', self.open_settings), ('Ctrl+Shift+O', self.import_result),
                               ('Ctrl+J', self.show_tasks)]:
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(callback)
        select_note = QShortcut(QKeySequence('Space'), self.history_list)
        select_note.setContext(Qt.ShortcutContext.WidgetShortcut)
        select_note.activated.connect(lambda: self._history_clicked(self.history_list.currentItem()))

    def apply_layout_visibility(self):
        immersive = self.preferences['immersive_reading']
        show_list = self.preferences['library_visible'] and not immersive
        was_hidden = self.library_panel.isHidden()
        self.navigation_rail.setVisible(not immersive)
        self.navigation_handle.setVisible(immersive)
        self.library_panel.setVisible(show_list)
        self.notes_button.setChecked(self.preferences['library_visible'])
        action = '隐藏' if self.preferences['library_visible'] else '展开'
        self.notes_button.setToolTip(f'单击{action}笔记列表')
        visible = self.preferences['reading_tabs_visible']
        self.tabs.tabBar().setVisible(visible)
        action = '隐藏' if visible else '展开'
        self.reader_header.blank_hint = f'双击{action}阅读标签'
        self.header_blank_area.blank_hint = self.reader_header.blank_hint
        if show_list and was_hidden:
            self.splitter.setSizes(self._library_sizes)

    def _save_layout(self):
        self.apply_layout_visibility()
        try:
            save_preferences(self.preferences, self.preferences_path)
        except OSError:
            self.set_status('布局已切换，但暂未保存成功。')

    def toggle_reading_tabs(self):
        self.preferences['reading_tabs_visible'] = not self.preferences['reading_tabs_visible']
        self._save_layout()

    def toggle_library(self):
        if not self.library_panel.isHidden():
            self._library_sizes = self.splitter.sizes()
        self.preferences['library_visible'] = not self.preferences['library_visible']
        self._save_layout()

    def toggle_immersive(self):
        if not self.library_panel.isHidden():
            self._library_sizes = self.splitter.sizes()
        self.preferences['immersive_reading'] = not self.preferences['immersive_reading']
        self._save_layout()

    def focus_library_search(self):
        self.preferences['immersive_reading'] = False
        self.preferences['library_visible'] = True
        self._save_layout()
        self.search.setFocus()

    def step_reading_font(self, step):
        self.reading_controls.font_box.setValue(self.reading_controls.font_box.value() + step)

    def begin_appearance_modal(self):
        self._appearance_modal = self._appearance_preview is not None
        self._appearance_popup_pos = self.reading_menu.pos()

    def end_appearance_modal(self):
        if self._appearance_modal and not self.reading_menu.isVisible():
            self.reading_menu.popup(self._appearance_popup_pos)
        self._appearance_modal = False

    def begin_appearance_preview(self):
        if self._appearance_preview is not None:
            return
        self._appearance_preview = dict(snapshot(self.preferences), custom_themes=dict(self.preferences['custom_themes']))
        self.reading_controls.set_values(self._appearance_preview)

    def cancel_appearance_preview(self):
        if self._appearance_modal:
            return
        if self._appearance_preview is not None:
            self._appearance_preview = None
            self.reading_controls.set_values(self.preferences)
            self.apply_reading_style()

    def commit_appearance_preview(self):
        if self._appearance_preview is not None:
            previous = dict(self.preferences)
            self.preferences.update(self._appearance_preview)
            try:
                save_preferences(self.preferences, self.preferences_path)
            except OSError:
                self.preferences = previous
                self.reading_controls.dialog_call(QMessageBox.warning, self, '未能保存', '请检查设置目录的写入权限，再试一次。')
                return
            self._appearance_preview = None
        self.reading_menu.hide()

    def change_reading_style(self, values):
        if self._appearance_preview is not None:
            self._appearance_preview.update(values)
            self.apply_reading_style()
            return
        self.preferences.update(values)
        self.apply_reading_style()
        try:
            save_preferences(self.preferences, self.preferences_path)
        except OSError:
            self.set_status('阅读设置已应用，但暂未保存成功。')

    def apply_reading_style(self):
        p = dict(self.preferences)
        if self._appearance_preview is not None:
            p.update(self._appearance_preview)
        size, bg = p['reading_font_size'], p['reading_background']
        palette = colors(bg)
        image, alpha = p.get('background_image', ''), p.get('background_transparency', 80)
        matched = p.get('match_app_background', False)
        app_bg = bg if matched else p['app_background']
        ui = ui_colors(p)
        # Scope to this window and its dialogs, not other application windows.
        native = self.palette()
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
            native.setColor(role, QColor(app_bg))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
            native.setColor(role, QColor(ui['foreground']))
        native.setColor(QPalette.ColorRole.Highlight, QColor(ui['selection']))
        native.setColor(QPalette.ColorRole.HighlightedText, QColor(ui['foreground']))
        self.setPalette(native)
        self.reading_menu.setPalette(native)
        self.setStyleSheet(stylesheet(p))
        central = self.centralWidget()
        central.background.configure(app_bg, image if matched else '', alpha)
        central.accent = p['accent_color'] if p.get('theme_id') in ('motion', 'future') else ''
        central.layout().setSpacing(p['ui_density'])
        surface = app_bg
        if matched and image:
            rgb = QColor(app_bg)
            surface = f'rgba({rgb.red()}, {rgb.green()}, {rgb.blue()}, 215)'
        central.setStyleSheet(f'QFrame#rail, QFrame#library {{ background: {surface}; }}')
        central.update()
        self.brand_label.setStyleSheet(f'font-size: 19px; font-weight: 600; color: {ui["foreground"]};')
        self.empty_title.setStyleSheet(f'font-size: 25px; font-weight: 600; color: {ui["foreground"]}; padding-top: 16px;')
        self.reading_panel.setStyleSheet(f'QFrame#readerHeader, QFrame#footer, QFrame#reading, QTabWidget::pane {{ background: {bg}; }}'
                                       f'QFrame#readerHeader QLabel, QFrame#readerHeader QToolButton, QFrame#footer QLabel, QFrame#footer QToolButton {{ color: {palette["foreground"]}; }}')
        for reader in (self.note_text, self.raw_text, self.materials_text):
            reader.reading_style(size, bg, image, alpha, p)
        for button, name in ((self.copy_button, 'copy'), (self.folder_button, 'folder'), (self.more_button, 'more')):
            button.setIcon(icon(name, palette['muted']))
        for button, name in ((self.settings_button, 'settings'), (self.account_button, 'user'),
                             (self.queue_status_button, 'video'), (self.import_button, 'import')):
            button.setIcon(icon(name, ui['muted']))
        self.notes_button.setIcon(icon('notes', p['accent_color']))
        self.new_button.setIcon(icon('plus', colors(p['accent_color'])['foreground']))
        for badge in self.history_list.findChildren(QLabel, 'noteBadge'):
            badge.setPixmap(icon('note', p['accent_color'], 25).pixmap(25, 25))
            badge.setStyleSheet(f'background: {ui["selection"]}; border-radius: {min(p["ui_radius"],10)}px;')
        for i in range(self.history_list.count()):
            item = self.history_list.item(i)
            row = self.history_list.itemWidget(item)
            if row:
                d = p['ui_density']
                row.layout().setContentsMargins(12, d, 12, d)
                item.setSizeHint(QSize(0, 81 + 2*d))

    def post(self, callback):
        if not self._closing:
            self.bridge.callback.emit(callback)

    def _dispatch(self, callback):
        if not self._closing:
            callback()

    def work(self, function, done, *, job=False, on_error=None):
        if job:
            self._set_busy(True)
        def finish(value):
            if job:
                self._set_busy(False)
            done(value)
        def failed(message):
            if job:
                self._set_busy(False)
            if on_error:
                on_error(message)
            else:
                self.set_status('处理未完成 · ' + message)
                QMessageBox.warning(self, '处理未完成', message)
        def run():
            try:
                value = function()
                self.post(lambda: finish(value))
            except Exception as error:
                self.post(lambda message=str(error): failed(message))
        threading.Thread(target=run, daemon=True).start()

    def _set_busy(self, value):
        changed = value != self.busy
        self.busy = value
        self.new_button.setEnabled(True)
        self.settings_button.setEnabled(True)
        self.progress.setVisible(value)
        for dialog in self._dialogs:
            if hasattr(dialog, 'set_processing'):
                dialog.set_processing(value)
        if value and changed:
            self.started_at = time.monotonic()
            self.elapsed_timer.start()
        elif not value:
            self.elapsed_timer.stop()
        self._tick()
        self._update_actions()

    def _tick(self):
        tasks = self.queue.snapshots()
        selected = next((task for task in tasks if task.id == self.selected_task_id), None)
        active = selected or next((task for task in tasks if task.status == 'running'), None)
        seconds = ((active.finished_at or time.time()) - active.started_at) if active and active.started_at else None
        self.elapsed.setText(duration_text(seconds) if seconds is not None else '')

    def _update_actions(self):
        ready = bool(self.result and self.result.get('complete'))
        processing = bool(self.result and self.queue.has_pending(self.result.get('bvid')))
        self.summary_action.setEnabled(ready and not processing)
        self.summary_action.setText('重新生成笔记' if self.current_markdown else '生成笔记')
        self.generate_button.setVisible(bool(ready and not self.current_markdown))
        self.generate_button.setEnabled(ready and not processing)
        self.visual_action.setEnabled(ready and not processing)
        self.source_action.setEnabled(bool(self.result))
        self.collection_action.setEnabled(bool(self.result and self.result.get('collection_index')))
        self.copy_button.setEnabled(bool(self.result and (self.tabs.currentIndex() != 0 or self.current_markdown)))
        self.folder_button.setEnabled(bool(self.result))

    def set_status(self, text):
        self.stage.setText(text)
        self.stage.setToolTip(text)

    def update_progress(self, text):
        self.post(lambda: self.set_status(text))

    def save_ui_preferences(self, values):
        self.preferences.update(values)
        save_preferences(self.preferences, self.preferences_path)
        self.queue.set_limit(self.preferences['concurrency'])
        self.refresh_library()

    def refresh_model_label(self, unused=None):
        try:
            settings = load_settings()
            self.model_label.setText(f'{settings.model} · {settings.reasoning_effort}')
        except ValueError:
            self.model_label.setText('请配置模型')

    def refresh_library(self, *, preferred=None, select_latest=False):
        self.entries = scan_library(self.preferences['output_dir'], self.preferences['imports'])
        selected = Path(preferred).resolve() if preferred else self.current_path
        if selected is None and select_latest and self.entries:
            selected = self.entries[0].path
        self._fill_history(selected)

    def filter_history(self, unused=None):
        self._fill_history(self.current_path, render=False)

    def _fill_history(self, selected, render=True):
        visible = filter_entries(self.entries, self.search.text())
        blocker = QSignalBlocker(self.history_list)
        self.history_list.clear()
        self.library_count.setText(f'全部笔记  {len(self.entries)}')
        active_item = None
        for entry in visible:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, str(entry.path))
            item.setData(Qt.ItemDataRole.AccessibleTextRole, entry.title)
            item.setSizeHint(QSize(0, 105))
            item.setToolTip(entry.title)
            self.history_list.addItem(item)
            row = QWidget()
            row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            body = QHBoxLayout(row)
            body.setContentsMargins(18, 16, 16, 14)
            body.setSpacing(12)
            badge = QLabel()
            if self.selection_mode:
                badge.setObjectName('noteSelectionCircle')
                badge.setFixedSize(22, 22)
            else:
                badge.setObjectName('noteBadge')
                badge.setPixmap(icon('note', self.preferences['accent_color'], 25).pixmap(25, 25))
                badge.setFixedSize(40, 44)
                badge.setStyleSheet(f'background: {ui_colors(self.preferences)["selection"]}; border-radius: 8px;')
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            body.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(5)
            title = QLabel(entry.title)
            title.setWordWrap(True)
            title.setMaximumHeight(40)
            title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            title.setStyleSheet('font-size: 14px; font-weight: 600;')
            text.addWidget(title)
            preview = QLabel(entry.preview)
            preview.setObjectName('muted')
            preview.setMaximumHeight(18)
            preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            text.addWidget(preview)
            body.addLayout(text, 1)
            self.history_list.setItemWidget(item, row)
            if entry.path == selected:
                active_item = item
        self.no_results.setText('没有匹配的笔记\n试试标题、作者或 BV 号' if self.search.text().strip() else '还没有笔记\n点击上方 ＋ 添加一个视频')
        self.library_stack.setCurrentIndex(0 if visible else 1)
        if active_item:
            self.history_list.setCurrentItem(active_item)
        del blocker
        self._update_selection_controls()
        if active_item and render:
            self.open_result_path(Path(active_item.data(Qt.ItemDataRole.UserRole)), user=False)

    def _history_selected(self, item, previous=None):
        if item and not self.selection_mode:
            self.open_result_path(Path(item.data(Qt.ItemDataRole.UserRole)), user=True)

    def _deletion_block_reason(self, entry):
        root = Path(self.preferences['output_dir']).resolve()
        if entry.path.parent.parent != root:
            return '外部导入或根目录记录，请到其原位置管理文件。'
        if self.queue.has_pending(entry.bvid):
            return '视频正在处理或等待，任务结束后才能删除。'
        return ''

    def toggle_selection_mode(self):
        self.selection_mode = not self.selection_mode
        self.checked_paths.clear()
        self.selection_button.setText('完成' if self.selection_mode else '选择')
        self.selection_bar.setVisible(self.selection_mode)
        self._fill_history(self.current_path, render=False)

    def _history_clicked(self, item):
        if not self.selection_mode or item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        entry = next((entry for entry in self.entries if entry.path == path), None)
        if entry is None or self._deletion_block_reason(entry):
            return
        if path in self.checked_paths:
            self.checked_paths.remove(path)
        else:
            self.checked_paths.add(path)
        self._update_selection_controls()

    def toggle_select_all(self):
        visible = {entry.path for entry in filter_entries(self.entries, self.search.text())
                   if not self._deletion_block_reason(entry)}
        if visible and visible <= self.checked_paths:
            self.checked_paths.difference_update(visible)
        else:
            self.checked_paths.update(visible)
        self._update_selection_controls()

    def _update_selection_controls(self):
        eligible = {entry.path for entry in self.entries if not self._deletion_block_reason(entry)}
        self.checked_paths.intersection_update(eligible)
        visible = {entry.path for entry in filter_entries(self.entries, self.search.text()) if entry.path in eligible}
        self.select_all_button.setEnabled(bool(visible))
        self.select_all_button.setText('取消全选' if visible and visible <= self.checked_paths else '全选')
        self.selection_count.setText(f'已选 {len(self.checked_paths)}')
        self.delete_button.setEnabled(bool(self.checked_paths))
        self.delete_button.setText(f'删除 ({len(self.checked_paths)})' if self.checked_paths else '删除')
        if not self.selection_mode:
            return
        entries = {str(entry.path): entry for entry in self.entries}
        for index in range(self.history_list.count()):
            item = self.history_list.item(index)
            entry = entries.get(item.data(Qt.ItemDataRole.UserRole))
            if not entry:
                continue
            checked = entry.path in self.checked_paths
            reason = self._deletion_block_reason(entry)
            item.setToolTip(entry.title + ('\n' + reason if reason else ''))
            circle = self.history_list.itemWidget(item).findChild(QLabel, 'noteSelectionCircle')
            if circle:
                circle.setText('✓' if checked else '')
                circle.setStyleSheet('border-radius: 11px; font-weight: 600; color: white; ' +
                    ('background: #07aa5a; border: 1px solid #07aa5a;' if checked else
                     'background: #e5e6e6; border: 1px solid #d6d8d6;' if reason else
                     'background: transparent; border: 1px solid #adb8b0;'))

    def delete_selected_notes(self):
        selected = [entry for entry in self.entries if entry.path in self.checked_paths]
        if not selected:
            return
        titles = '\n'.join('• ' + entry.title for entry in selected[:10])
        if len(selected) > 10:
            titles += f'\n……共 {len(selected)} 份'
        answer = QMessageBox.question(self, '删除笔记与对应文件',
            f'确定永久删除选中的 {len(selected)} 份笔记？\n\n{titles}\n\n'
            f'将同时删除以下保存目录中对应视频的整个文件夹，包括笔记、字幕、音视频及画面材料。\n'
            f'{self.preferences["output_dir"]}\n\n此操作无法撤销。',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed, failures = set(), []
        for entry in selected:
            try:
                active = {task.bvid for task in self.queue.snapshots() if task.status in ('queued', 'running')}
                delete_note_folder(entry.path, self.preferences['output_dir'], entry.bvid, active_bvids=active)
                removed.add(entry.path)
            except (OSError, ValueError) as error:
                failures.append(f'{entry.title}：{error}')
        self.preferences['imports'] = [path for path in self.preferences['imports'] if Path(path).resolve() not in removed]
        try:
            save_preferences(self.preferences, self.preferences_path)
        except OSError as error:
            failures.append('列表设置保存失败：' + str(error))
        self.checked_paths.difference_update(removed)
        if removed:
            from collection_index import refresh_indexes
            refresh_indexes(self.preferences['output_dir'])
        if self.current_path in removed:
            self.current_path = self.result = self.selected_task_id = None
            self.current_markdown = ''
            self.note_text.clear()
            self.raw_text.clear()
            self.materials_text.clear()
            self.video_title.setText('Bili Note')
            self.video_meta.clear()
            self.empty_title.setText('笔记已删除')
            self.empty_hint.setText('从左侧选择其他笔记，或添加新的视频。')
            self.view_stack.setCurrentIndex(0)
        if self.active_path in removed:
            self.active_path = self.active_result = None
        self.selection_mode = False
        self.checked_paths.clear()
        self.selection_button.setText('选择')
        self.selection_bar.hide()
        self.refresh_library()
        self._update_actions()
        if self.task_dialog is not None:
            self.task_dialog.refresh()
        self.set_status(f'已删除 {len(removed)} 份笔记及对应文件' + (f'，{len(failures)} 项未完成' if failures else ''))
        if failures:
            QMessageBox.warning(self, '部分删除未完成', '\n'.join(failures))

    def open_result_path(self, path, *, user=True):
        path = Path(path).resolve()
        try:
            result = read_result(path)
            self.current_path, self.result = path, result
            if user:
                self.selected_task_id = None
            if user and self.busy:
                self.follow_task = path == self.active_path
            self.render_result(result, path.parent)
        except (OSError, ValueError, TypeError) as error:
            self.set_status('无法打开这份记录 · ' + str(error))

    def render_result(self, result, root):
        self.view_stack.setCurrentIndex(1)
        self.video_title.setText(result['title'])
        self.video_title.setToolTip(result['title'])
        note = find_note(result, root)
        state = '已整理' if note else ('正文已就绪' if result.get('complete') else '正文待补齐')
        self.video_meta.setText('  ·  '.join(x for x in (result.get('author'), duration_text(result.get('duration')), state) if x))
        try:
            self.current_markdown = note.read_text(encoding='utf-8') if note else ''
        except (OSError, UnicodeError):
            self.current_markdown = ''
        self.note_text.markdown(self.current_markdown or '## 正文已保存\n\n这份记录尚未生成笔记。点击右上角“生成笔记”即可整理。', root)
        sections = []
        for page in result['pages']:
            try:
                material = Path(page['paths']['txt']).resolve()
                material.relative_to(Path(root).resolve())
                text = material.read_text(encoding='utf-8')
            except (OSError, KeyError, ValueError):
                text = '字幕文件不可用，请检查保存目录。'
            sections.append(f'第 {page.get("page", "?")} 集 · {page.get("title", "")}\n\n{text}')
        self.raw_text.setPlainText('\n\n'.join(sections))
        labels = {'platform_subtitle': 'B站可下载字幕', 'browser_subtitle': '浏览器字幕正文', 'local_asr': '完整音轨机器转写'}
        visual = result.get('visual') or {}
        states = {'not_needed': '字幕可用，未额外抽帧', 'off': '本次未开启画面核对', 'frames_ready': '画面已提取，尚未核对',
                  'reviewed': '已抽样核对画面，不代表全程视觉审查', 'failed': '画面核对未成功', 'unavailable': '未取得可用画面'}
        details = ['## 来源与覆盖', '', f'原视频：{result.get("source", "")}', '',
                   f'正文覆盖：{len(result["pages"])} / {result.get("pages_total", len(result["pages"]))} 集',
                   f'正文完整：{"是" if result.get("complete") else "否"}', '']
        if result.get('selected_pages'):
            details += [f'正文范围：仅所选分集 {result["selected_pages"]}；原视频共 {result.get("video_pages_total", "?")} 集。',
                        '当前分集正文完整不代表整个课程已完整整理。', '']
        if result.get('collection_index'):
            details += [f'合集：{result.get("collection_title", "")}', '可在右上角“更多 → 查看合集目录”浏览各集笔记与状态。', '']
        for page in result['pages']:
            method = labels.get(page.get('material_source', 'platform_subtitle'), '来源未记录')
            details.append(f'- 第 {page.get("page", "?")} 集：{method} · {page.get("segments", 0)} 段')
        details += ['', '### 画面', states.get(visual.get('state'), '未记录画面核对'), visual.get('warning', ''), '',
                    '### 整理', '规则：bili-note', f'实际模型：{result.get("summary_model") or "未记录"}',
                    f'实际推理强度：{result.get("summary_reasoning_effort") or "未记录"}']
        self.materials_text.markdown('\n\n'.join(details), root)
        self._update_actions()

    def publish_result(self, result, *, select=None):
        path = (Path(result['folder']) / 'video.json').resolve()
        self.active_result, self.active_path = result, path
        if str(path) not in self.preferences['imports']:
            self.preferences['imports'].append(str(path))
            save_preferences(self.preferences, self.preferences_path)
        if select is False:
            self.entries = scan_library(self.preferences['output_dir'], self.preferences['imports'])
            self._fill_history(self.current_path, render=False)
        else:
            self.refresh_library(preferred=path if self.follow_task else self.current_path)

    def new_video(self):
        from ui_dialogs import NewVideoDialog
        dialog = NewVideoDialog(self, self.preferences)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            settings = load_settings() if values['auto_summary'] else None
            parts = {part.source: part for part in values.get('parts') or ()}
            specs = [TaskSpec(source=source, output_dir=values['output_dir'], mode=values['mode'],
                              visual=values['visual'], auto_summary=values['auto_summary'], settings=settings,
                              expected_cid=parts[source].cid if source in parts else None,
                              title=(f'{parts[source].video_title} · P{parts[source].page:03} {parts[source].title}'
                                     if source in parts and parts[source].total_pages > 1 else parts[source].video_title if source in parts else ''),
                              collection=values.get('collections', {}).get(parts[source].key) if source in parts else None)
                     for source in values['sources']]
        except ValueError as error:
            QMessageBox.warning(self, '请先配置模型', str(error))
            return
        self.preferences.update({key: value for key, value in values.items() if key in self.preferences})
        save_preferences(self.preferences, self.preferences_path)
        self.enqueue_tasks(specs)

    def _summarize(self, result, settings, visual=False):
        collection = None
        if result.get('collection_index'):
            try:
                index = Path(result['collection_index']).resolve()
                index.relative_to(Path(result['folder']).resolve().parent / '_合集目录')
                collection = json.loads(index.with_name(index.stem + '.index.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        spec = TaskSpec(source=result.get('source') or result['bvid'],
                        output_dir=str(Path(result['folder']).parent), settings=settings,
                        kind='visual' if visual else 'summary', result=result, collection=collection)
        self.enqueue_tasks([spec])

    def generate(self):
        self._generate_selected(False)

    def manual_visual(self):
        self._generate_selected(True)

    def _generate_selected(self, visual):
        if not self.result or not self.result.get('complete') or self.queue.has_pending(self.result['bvid']):
            return
        try:
            settings = load_settings()
        except ValueError as error:
            QMessageBox.warning(self, '请先配置模型', str(error))
            return
        self._summarize(self.result, settings, visual)

    def enqueue_tasks(self, specs, *, show=True):
        ids, duplicates = self.queue.enqueue(specs)
        if ids:
            if self.result is None and self.selected_task_id is None:
                self.selected_task_id = ids[0]
                self.view_stack.setCurrentIndex(0)
                self.empty_title.setText('视频已加入任务队列')
                self.empty_hint.setText('可以继续添加视频，也可以阅读历史笔记。')
            self.set_status(f'已加入 {len(ids)} 条任务' + (f'，跳过 {len(duplicates)} 条正在处理的重复视频' if duplicates else ''))
        elif duplicates:
            self.set_status('这些视频已经在队列中，已跳过重复提交。')
        self.sync_queue_status()
        if show:
            self.show_tasks()
        return ids, duplicates

    def show_tasks(self):
        if self.task_dialog is None:
            from task_dialog import TaskQueueDialog
            self.task_dialog = TaskQueueDialog(self)
        self.task_dialog.show()
        self.task_dialog.raise_()
        self.task_dialog.activateWindow()

    def on_task_update(self, task):
        if task.revision <= self._task_revisions.get(task.id, 0):
            return
        self._task_revisions[task.id] = task.revision
        terminal = task.status not in ('queued', 'running')
        if task.result and (task.id not in self._indexed_tasks or terminal):
            path = Path(task.result.get('folder', '')) / 'video.json'
            if path.is_file():
                self.publish_result(task.result, select=False)
                self._indexed_tasks.add(task.id)
                if self.selected_task_id == task.id or self.current_path == path.resolve():
                    self.open_result_path(path, user=False)
                    if task.status == 'done':
                        self.tabs.setCurrentIndex(0)
        if self.selected_task_id == task.id and task.result is None:
            self.video_title.setText(task.title)
            self.video_meta.setText(STATES[task.status])
            self.empty_title.setText(task.stage)
            self.empty_hint.setText(task.error or '可以在任务窗口查看所有视频的进度。')
        self.sync_queue_status()
        if self.task_dialog is not None:
            self.task_dialog.refresh()

    def sync_queue_status(self):
        tasks = self.queue.snapshots()
        running = sum(task.status == 'running' for task in tasks)
        waiting = sum(task.status == 'queued' for task in tasks)
        done = sum(task.status == 'done' for task in tasks)
        failed = sum(task.status in ('failed', 'partial') for task in tasks)
        self._set_busy(bool(running or waiting))
        p = dict(self.preferences, **(self._appearance_preview or {}))
        muted = colors(p['reading_background'])['muted']
        self.queue_status_button.setIcon(icon('video', p['accent_color'] if running or waiting else muted))
        self.queue_status_button.setToolTip(f'进行 {running} · 等待 {waiting} · 失败 {failed}（Ctrl+J）')
        self.queue_status_button.setText(f'任务 {running + waiting}' if running or waiting else '任务')
        selected = next((task for task in tasks if task.id == self.selected_task_id), None)
        summary = f'进行 {running} · 等待 {waiting}' if running or waiting else f'完成 {done} · 失败/待补齐 {failed}'
        self.set_status(summary + (' · ' + selected.stage if selected else ''))
        self._update_selection_controls()

    def copy_text(self):
        index = self.tabs.currentIndex()
        value = self.current_markdown if index == 0 else (self.raw_text, self.materials_text)[index - 1].toPlainText()
        QApplication.clipboard().setText(value)
        self.set_status(('笔记', '字幕', '来源')[index] + '已复制')

    def open_folder(self):
        folder = self.current_path.parent if self.current_path else Path(self.preferences['output_dir'])
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_source(self):
        if self.result:
            url = QUrl(self.result.get('source', ''))
            if url.scheme() in ('http', 'https'):
                QDesktopServices.openUrl(url)

    def open_collection_index(self):
        if not self.result or not self.result.get('collection_index'):
            return
        try:
            root = Path(self.result['folder']).resolve().parent
            path = Path(self.result['collection_index']).resolve()
            if path.parent != root / '_合集目录':
                raise ValueError('合集目录不在当前笔记保存位置内。')
            from collection_index import refresh_indexes
            refresh_indexes(root)
            text = path.read_text(encoding='utf-8')
        except (OSError, ValueError) as error:
            QMessageBox.information(self, '合集目录暂不可用', str(error))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('合集目录 · Bili Note')
        dialog.resize(980, 650)
        layout = QVBoxLayout(dialog)
        reader = Reader()
        reader.reading_style(self.preferences['reading_font_size'], self.preferences['reading_background'], self.preferences['background_image'], self.preferences['background_transparency'], self.preferences)
        def resize_index(step):
            self.step_reading_font(step)
            reader.reading_style(self.preferences['reading_font_size'], self.preferences['reading_background'], self.preferences['background_image'], self.preferences['background_transparency'], self.preferences)
        reader.font_step.connect(resize_index)
        reader.markdown(text, path.parent)
        reader.anchorClicked.disconnect(reader.open_link)
        def open_link(url):
            url = reader.document().baseUrl().resolved(url)
            if url.scheme() in ('http', 'https'):
                reader.open_link(url)
            elif url.isLocalFile():
                try:
                    note = Path(url.toLocalFile()).resolve()
                    note.relative_to(root)
                    metadata = note.parent / 'video.json'
                    value = read_result(metadata)
                    if find_note(value, note.parent) == note:
                        self.search.clear()
                        self.open_result_path(metadata)
                        dialog.accept()
                except (OSError, ValueError):
                    pass
        reader.anchorClicked.connect(open_link)
        layout.addWidget(reader)
        dialog.exec()

    def import_result(self):
        name, unused = QFileDialog.getOpenFileName(self, '导入已保存的视频结果', self.preferences['output_dir'], '视频结果 (video.json)')
        if name:
            found = scan_library(Path(name).parent, [name])
            if not found:
                QMessageBox.information(self, '无法导入', '请选择有效的 video.json。')
                return
            path = str(Path(name).resolve())
            if path not in self.preferences['imports']:
                self.preferences['imports'].append(path)
                save_preferences(self.preferences, self.preferences_path)
            self.search.clear()
            self.follow_task = False
            self.selected_task_id = None
            self.refresh_library(preferred=path)

    def open_account(self):
        from ui_dialogs import AccountDialog
        dialog = AccountDialog(self)
        self._dialogs.append(dialog)
        dialog.exec()

    def check_account(self):
        self.work(self.client.status, self.show_account, on_error=lambda message: self.account_button.setToolTip('登录状态暂未验证 · ' + message))

    def show_account(self, status):
        self.account_status = status
        self.account_button.setIcon(icon('user', '#17c47a' if status.get('logged_in') else '#6e6e73'))
        self.account_button.setToolTip('B站账户 · ' + (status.get('name') or '已登录') if status.get('logged_in') else 'B站账户 · 点击登录')

    def open_model_settings(self):
        self.open_settings(model=True)

    def open_settings(self, checked=False, *, model=False):
        from ui_dialogs import SettingsDialog
        dialog = SettingsDialog(self, self.preferences)
        if model:
            dialog.tabs.setCurrentIndex(1)
        self._dialogs.append(dialog)
        dialog.exec()

    def browser_channel(self):
        answer = QMessageBox.question(self, '浏览器读取通道', '将打开一个专用 Edge 窗口，请在其中登录 B站。\n\n通道仅供本机读取，使用独立目录，不复制日常浏览器登录。平台字幕可用时无需开启。\n\n是否开启？')
        if answer == QMessageBox.StandardButton.Yes:
            source = self.result.get('source') if self.result else 'https://www.bilibili.com/video/BV1k5np69EKv/'
            self.work(lambda: open_browser_channel(source), self.set_status)

    def _setup_tray(self):
        QApplication.instance().setQuitOnLastWindowClosed(False)
        self.tray = QSystemTrayIcon(book_icon(), self)
        self.tray.setToolTip('Bili Note · 点击打开')
        menu = QMenu(self)
        menu.addAction('打开 Bili Note', self.restore_window)
        menu.addSeparator()
        self.tray_exit_action = menu.addAction('退出', self.request_exit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()
        self.exit_timer = QTimer(self)
        self.exit_timer.setInterval(300)
        self.exit_timer.timeout.connect(self._finish_exit)

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore_window()

    def restore_window(self):
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def request_exit(self):
        if self._exit_requested:
            return
        self._exit_requested = True
        # Stop accepting/starting work, but allow running tasks to publish safely.
        self.queue.shutdown(wait=False)
        if self.queue.has_pending():
            self.tray_exit_action.setText('正在等待当前任务结束…')
            self.tray_exit_action.setEnabled(False)
            self.tray.setToolTip('Bili Note · 当前任务结束后退出')
            self.centralWidget().setEnabled(False)
            self.set_status('正在退出：已取消等待项，当前任务结束后自动退出。')
            self.exit_timer.start()
        else:
            self._finish_exit()

    def _finish_exit(self):
        if self.queue.has_pending():
            return
        self.exit_timer.stop()
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self.tray is not None and not self._exit_requested:
            self.hide()
            event.ignore()
            return
        if self.queue.has_pending():
            QMessageBox.information(self, '任务正在进行', '还有视频正在处理或等待。请完成当前任务，或在任务列表取消等待项后再关闭。')
            event.ignore()
            return
        self._closing = True
        if self.tray is not None:
            self.tray.hide()
        self.elapsed_timer.stop()
        self.queue.shutdown(wait=False)
        event.accept()
