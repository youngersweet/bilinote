"""收藏选择窗口；只返回选中的视频链接，转换仍使用原有任务入口。"""
from PySide6.QtCore import Qt, QSignalBlocker, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSplitter, QWidget)

from bili_service import BiliClient
from favorite_service import FavoriteService


class FavoriteDialog(QDialog):
    def __init__(self, parent, host):
        super().__init__(parent)
        self.host = host
        self.closed = False
        self.generation = 0
        self.loading = False
        self.service = None
        self.current_folder = None
        self.page = 0
        self.has_more = False
        self.total = 0
        self.rows = []
        self.selected = {}
        self.setWindowTitle('从 B站收藏选择 · Bili Note')
        self.resize(1000, 680)
        self.setMinimumSize(880, 580)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        top = QHBoxLayout()
        title = QLabel('从 B站收藏选择')
        title.setObjectName('dialogTitle')
        top.addWidget(title, 1)
        self.login_button = QPushButton('登录管理')
        self.login_button.clicked.connect(self.manage_login)
        top.addWidget(self.login_button)
        self.reload_button = QPushButton('刷新收藏夹')
        self.reload_button.setToolTip('重新读取收藏夹并清空本次勾选。')
        self.reload_button.clicked.connect(self.reload)
        top.addWidget(self.reload_button)
        layout.addLayout(top)
        hint = QLabel('浏览当前账号的收藏夹，可跨收藏夹勾选。这里只读取清单，返回后由你确认加入转换队列。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.folders_list = QListWidget()
        self.folders_list.setMinimumWidth(170)
        self.folders_list.setMaximumWidth(270)
        self.folders_list.setStyleSheet('QListWidget { background: #f5f5f7; border: none; border-radius: 10px; padding: 6px; } QListWidget::item { padding: 12px 8px; border-radius: 6px; } QListWidget::item:selected { background: #dceafa; color: #005bb5; }')
        self.folders_list.currentItemChanged.connect(self.change_folder)
        splitter.addWidget(self.folders_list)
        right = QWidget()
        body = QVBoxLayout(right)
        body.setContentsMargins(10, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索已加载的视频标题或 UP 主')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.render)
        body.addWidget(self.search)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['', '视频', 'UP 主', '时长', '状态'])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setStyleSheet('QTableWidget { background: white; border: 1px solid #e2e2e7; border-radius: 8px; gridline-color: #f2f2f7; } QTableWidget::item { padding: 6px; } QTableWidget::item:selected { background: #dceafa; color: #1d1d1f; } QHeaderView::section { background: #f5f5f7; padding: 8px; border: none; color: #6e6e73; }')
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 34), (2, 100), (3, 65), (4, 110)):
            self.table.setColumnWidth(column, width)
        self.table.itemChanged.connect(self.item_changed)
        self.table.cellClicked.connect(self.cell_clicked)
        body.addWidget(self.table, 1)
        paging = QHBoxLayout()
        self.count_label = QLabel('尚未加载')
        self.count_label.setObjectName('muted')
        paging.addWidget(self.count_label, 1)
        self.more_button = QPushButton('加载更多')
        self.more_button.clicked.connect(self.load_more)
        paging.addWidget(self.more_button)
        body.addLayout(paging)
        splitter.addWidget(right)
        splitter.setSizes([210, 720])
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName('muted')
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.select_all_button = QPushButton('全选已加载')
        self.select_all_button.setToolTip('只选择当前收藏夹、当前搜索结果中已加载且可处理的视频。')
        self.select_all_button.clicked.connect(self.select_all)
        buttons.addWidget(self.select_all_button)
        clear = QPushButton('清空选择')
        clear.clicked.connect(self.clear_selection)
        buttons.addWidget(clear)
        self.selected_label = QLabel('已选 0 个')
        buttons.addWidget(self.selected_label)
        buttons.addStretch()
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.use_button = QPushButton('使用选中视频')
        self.use_button.setObjectName('primary')
        self.use_button.clicked.connect(self.use_selection)
        buttons.addWidget(self.use_button)
        layout.addLayout(buttons)
        self.finished.connect(self.close_requests)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.update_controls)
        self.timer.start()
        self.reload()

    def close_requests(self, unused=None):
        self.closed = True
        self.generation += 1
        self.timer.stop()

    def _request(self, operation, done, generation):
        self.loading = True
        self.update_controls()
        def finished(value):
            if self.closed or generation != self.generation:
                return
            self.loading = False
            done(value)
            self.update_controls()
        def failed(message):
            if self.closed or generation != self.generation:
                return
            self.loading = False
            self.status.setText(message)
            self.update_controls()
        self.host.work(operation, finished, on_error=failed)

    def reload(self):
        self.generation += 1
        self.selected.clear()
        self.rows.clear()
        self.current_folder = None
        self.page, self.total, self.has_more = 0, 0, False
        with QSignalBlocker(self.folders_list):
            self.folders_list.clear()
        self.render()
        self.status.setText('正在读取当前登录账号的收藏夹…')
        # 独立 CookieJar；不与视频后台任务或扫码登录共用可变会话。
        service = FavoriteService(BiliClient(self.host.client.store))
        self.service = service
        self._request(service.folders, self.folders_loaded, self.generation)

    def folders_loaded(self, folders):
        with QSignalBlocker(self.folders_list):
            for folder in folders:
                item = QListWidgetItem(f'{folder.title}  ({folder.count})')
                item.setData(Qt.ItemDataRole.UserRole, folder.id)
                item.setToolTip(folder.title)
                self.folders_list.addItem(item)
        if folders:
            self.folders_list.setCurrentRow(0)
        else:
            self.status.setText('当前账号没有可读取的自建收藏夹。可在B站收藏视频后刷新。')

    def change_folder(self, item, unused=None):
        if not item or self.closed:
            return
        self.generation += 1
        self.loading = False
        self.current_folder = item.data(Qt.ItemDataRole.UserRole)
        self.rows = []
        self.page, self.total, self.has_more = 0, 0, True
        self.render()
        self.load_more()

    def load_more(self):
        if self.loading or self.current_folder is None or not self.has_more or self.closed:
            return
        page = self.page + 1
        self.status.setText(f'正在加载第 {page} 页，每页最多20条…')
        service, folder_id = self.service, self.current_folder
        def loaded(result):
            seen = {video.key for video in self.rows}
            for video in result.items:
                if video.key not in seen:
                    self.rows.append(video)
                    seen.add(video.key)
            self.page = page
            self.total = result.total
            self.has_more = result.has_more
            self.status.setText('可继续加载更多视频；搜索和全选仅作用于已加载的内容。' if self.rows else '这个收藏夹暂时没有视频。')
            self.render()
        self._request(lambda: service.videos(folder_id, page=page), loaded, self.generation)

    def visible_videos(self):
        words = self.search.text().casefold().split()
        return [video for video in self.rows if all(word in f'{video.title} {video.author} {video.bvid}'.casefold() for word in words)]

    def block_reason(self, video):
        if not video.available:
            return video.reason or '不可处理'
        return '正在处理或等待' if self.host.queue.has_pending(video.bvid) else ''

    def video_state(self, video):
        reason = self.block_reason(video)
        if reason:
            return reason
        entry = next((entry for entry in self.host.entries if entry.bvid == video.bvid), None)
        return '已有笔记' if entry and entry.note_path else '已有正文' if entry else '可转换'

    def render(self, unused=None):
        videos = self.visible_videos()
        with QSignalBlocker(self.table):
            self.table.setRowCount(len(videos))
            for row, video in enumerate(videos):
                minutes, seconds = divmod(video.duration, 60)
                for column, text in enumerate(('', video.title, video.author, f'{minutes}:{seconds:02}', self.video_state(video))):
                    cell = QTableWidgetItem(text)
                    cell.setToolTip(text)
                    if column == 0:
                        cell.setData(Qt.ItemDataRole.UserRole, video.key)
                        cell.setCheckState(Qt.CheckState.Checked if video.bvid in self.selected else Qt.CheckState.Unchecked)
                    self.table.setItem(row, column, cell)
                self.table.setRowHeight(row, 44)
        self.update_controls()

    def update_controls(self):
        if self.closed:
            return
        self.selected = {key: video for key, video in self.selected.items() if not self.block_reason(video)}
        self.reload_button.setEnabled(not self.loading)
        self.more_button.setEnabled(not self.loading and self.current_folder is not None and self.has_more)
        self.count_label.setText(f'已加载 {len(self.rows)} / {self.total} 条' if self.current_folder is not None else '尚未加载视频')
        self.selected_label.setText(f'已选 {len(self.selected)} 个')
        self.use_button.setEnabled(bool(self.selected))
        visible = self.visible_videos()
        eligible = {video.bvid for video in visible if not self.block_reason(video)}
        self.select_all_button.setEnabled(bool(eligible))
        self.select_all_button.setText('取消全选' if eligible and eligible <= self.selected.keys() else '全选已加载')
        with QSignalBlocker(self.table):
            for row, video in enumerate(visible):
                cell = self.table.item(row, 0)
                if cell is None:
                    continue
                flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                if not self.block_reason(video):
                    flags |= Qt.ItemFlag.ItemIsUserCheckable
                else:
                    flags &= ~Qt.ItemFlag.ItemIsEnabled
                cell.setFlags(flags)
                cell.setCheckState(Qt.CheckState.Checked if video.bvid in self.selected else Qt.CheckState.Unchecked)
                state = self.video_state(video)
                self.table.item(row, 4).setText(state)
                self.table.item(row, 4).setToolTip(state)

    def item_changed(self, cell):
        if cell.column() != 0:
            return
        video = next((video for video in self.rows if video.key == cell.data(Qt.ItemDataRole.UserRole)), None)
        if video and not self.block_reason(video):
            if cell.checkState() == Qt.CheckState.Checked:
                self.selected[video.bvid] = video
            else:
                self.selected.pop(video.bvid, None)
        self.update_controls()

    def cell_clicked(self, row, column):
        if column == 0:
            return
        cell = self.table.item(row, 0)
        if cell and cell.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            cell.setCheckState(Qt.CheckState.Unchecked if cell.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked)

    def select_all(self):
        eligible = {video.bvid: video for video in self.visible_videos() if not self.block_reason(video)}
        if eligible and eligible.keys() <= self.selected.keys():
            for key in eligible:
                self.selected.pop(key, None)
        else:
            self.selected.update(eligible)
        self.update_controls()

    def clear_selection(self):
        self.selected.clear()
        self.update_controls()

    def sources(self):
        return [video.source for video in self.selected.values()]

    def use_selection(self):
        self.update_controls()
        if self.selected:
            self.accept()

    def manage_login(self):
        self.generation += 1
        self.host.open_account()
        if not self.closed:
            self.reload()
