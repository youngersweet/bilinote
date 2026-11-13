"""先确认UP账号，再选择投稿；返回链接并沿用原有转换入口。"""
from PySide6.QtCore import Qt, QSignalBlocker, QTimer, QUrl
from PySide6.QtGui import QPixmap, QDesktopServices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSplitter, QWidget)

from bili_service import BiliClient
from uploader_service import UploaderService


class UploaderDialog(QDialog):
    def __init__(self, parent, host):
        super().__init__(parent)
        self.host = host
        self.service = UploaderService(BiliClient(host.client.store))
        self.closed = False
        self.generation = 0
        self.loading = False
        self.current = None
        self.page = self.search_page = self.total = 0
        self.has_more = self.search_has_more = False
        self.last_query = ''
        self.rows = []
        self.selected = {}
        self.setWindowTitle('从 UP主投稿选择 · Bili Note')
        self.resize(1040, 700)
        self.setMinimumSize(880, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        top = QHBoxLayout()
        title = QLabel('从 UP主投稿选择')
        title.setObjectName('dialogTitle')
        top.addWidget(title, 1)
        login = QPushButton('登录管理')
        login.clicked.connect(self.manage_login)
        top.addWidget(login)
        layout.addLayout(top)
        hint = QLabel('输入名称后确认左侧账号，也可用UID或个人空间地址精确定位。勾选视频后，再确认加入转换队列。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText('UP名称 / UID / https://space.bilibili.com/…')
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.search)
        search_row.addWidget(self.query, 1)
        self.search_button = QPushButton('查找 UP主')
        self.search_button.setObjectName('primary')
        self.search_button.clicked.connect(self.search)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left.setMinimumWidth(200)
        left.setMaximumWidth(290)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        self.accounts_list = QListWidget()
        self.accounts_list.setWordWrap(True)
        self.accounts_list.setStyleSheet('QListWidget { background: #f5f5f7; border: none; border-radius: 10px; padding: 5px; } QListWidget::item { padding: 12px 8px; border-radius: 6px; } QListWidget::item:selected { background: #dceafa; color: #005bb5; }')
        self.accounts_list.currentItemChanged.connect(self.change_account)
        left_layout.addWidget(self.accounts_list, 1)
        self.accounts_more = QPushButton('更多同名账号')
        self.accounts_more.clicked.connect(self.more_accounts)
        left_layout.addWidget(self.accounts_more)
        splitter.addWidget(left)
        right = QWidget()
        body = QVBoxLayout(right)
        body.setContentsMargins(4, 0, 0, 0)
        profile = QHBoxLayout()
        self.avatar = QLabel('UP')
        self.avatar.setFixedSize(48, 48)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setStyleSheet('background: #e7f0fc; color: #0071e3; border-radius: 10px;')
        profile.addWidget(self.avatar)
        self.identity = QLabel('尚未选择 UP主')
        self.identity.setTextFormat(Qt.TextFormat.PlainText)
        self.identity.setWordWrap(True)
        profile.addWidget(self.identity, 1)
        self.space_button = QPushButton('打开空间')
        self.space_button.clicked.connect(self.open_space)
        profile.addWidget(self.space_button)
        body.addLayout(profile)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText('筛选已加载的投稿标题')
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.render)
        body.addWidget(self.filter)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['', '投稿视频', '时长', '状态'])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setStyleSheet('QTableWidget { background: white; border: 1px solid #e2e2e7; border-radius: 8px; gridline-color: #f2f2f7; } QTableWidget::item { padding: 6px; } QTableWidget::item:selected { background: #dceafa; color: #1d1d1f; } QHeaderView::section { background: #f5f5f7; padding: 8px; border: none; color: #6e6e73; }')
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 34), (2, 75), (3, 115)):
            self.table.setColumnWidth(column, width)
        self.table.itemChanged.connect(self.item_changed)
        self.table.cellClicked.connect(self.cell_clicked)
        body.addWidget(self.table, 1)
        paging = QHBoxLayout()
        self.count = QLabel('尚未加载投稿')
        self.count.setObjectName('muted')
        paging.addWidget(self.count, 1)
        self.more_button = QPushButton('加载更多投稿')
        self.more_button.clicked.connect(self.load_more)
        paging.addWidget(self.more_button)
        body.addLayout(paging)
        splitter.addWidget(right)
        splitter.setSizes([240, 750])
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.status = QLabel('这里只读取公开账号与投稿清单，不下载媒体或调用总结模型。')
        self.status.setObjectName('muted')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.select_all_button = QPushButton('全选已加载')
        self.select_all_button.clicked.connect(self.select_all)
        buttons.addWidget(self.select_all_button)
        clear = QPushButton('清空选择')
        clear.clicked.connect(self.clear_selection)
        buttons.addWidget(clear)
        self.selected_count = QLabel()
        buttons.addWidget(self.selected_count)
        buttons.addStretch()
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.use_button = QPushButton('使用选中视频')
        self.use_button.setObjectName('primary')
        self.use_button.clicked.connect(self.use_selection)
        buttons.addWidget(self.use_button)
        layout.addLayout(buttons)
        self.finished.connect(self.finish_requests)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.update_controls)
        self.timer.start()
        self.update_controls()
        self.query.setFocus()

    def finish_requests(self, unused=None):
        self.closed = True
        self.generation += 1
        self.timer.stop()

    def _request(self, operation, done):
        self.generation += 1
        generation = self.generation
        self.loading = True
        self.update_controls()
        def success(value):
            if self.closed or generation != self.generation:
                return
            self.loading = False
            done(value)
            self.update_controls()
        def failure(message):
            if self.closed or generation != self.generation:
                return
            self.loading = False
            self.status.setText(message)
            self.update_controls()
        self.host.work(operation, success, on_error=failure)

    def search(self):
        if self.loading or self.closed:
            return
        query = self.query.text().strip()
        if not query:
            self.status.setText('请输入UP名称、UID或空间地址。')
            return
        self.last_query = query
        self.search_page = 0
        self.search_has_more = True
        self.current = None
        self.rows = []
        self.selected.clear()
        self.has_more = False
        self.page = self.total = 0
        with QSignalBlocker(self.accounts_list):
            self.accounts_list.clear()
        self.identity.setText('正在查找账号…')
        self.avatar.clear()
        self.avatar.setText('UP')
        self.render()
        self.more_accounts()

    def more_accounts(self):
        if self.loading or not self.search_has_more or self.closed:
            return
        page, query = self.search_page + 1, self.last_query
        self.status.setText('正在查找UP主；请用UID区分同名账号。')
        def loaded(result):
            self.search_page, self.search_has_more = page, result.has_more
            seen = {self.accounts_list.item(i).data(Qt.ItemDataRole.UserRole).mid for i in range(self.accounts_list.count())}
            with QSignalBlocker(self.accounts_list):
                for profile in result.items:
                    if profile.mid in seen:
                        continue
                    item = QListWidgetItem(f'{profile.name}\nUID {profile.mid}\n投稿 {profile.video_count} · 粉丝 {profile.followers}')
                    item.setData(Qt.ItemDataRole.UserRole, profile)
                    item.setToolTip(profile.sign or profile.name)
                    self.accounts_list.addItem(item)
                    seen.add(profile.mid)
            if self.current is None and self.accounts_list.count():
                self.accounts_list.setCurrentRow(0)
            elif not self.accounts_list.count():
                self.identity.setText('未找到账号')
                self.status.setText('没有找到匹配账号，可试用UID或B站个人空间地址。')
            else:
                self.status.setText('账号列表已更新，请核对名称与UID。')
        self._request(lambda: self.service.search(query, page=page), loaded)

    def change_account(self, item, unused=None):
        if not item or self.closed:
            return
        self.generation += 1
        self.loading = False
        self.current = profile = item.data(Qt.ItemDataRole.UserRole)
        self.page = self.total = 0
        self.has_more = True
        self.rows = []
        self.selected.clear()
        self.identity.setText(f'{profile.name}  ·  UID {profile.mid}\n{profile.sign[:100]}')
        self.avatar.clear()
        self.avatar.setText('UP')
        self.render()
        self.load_more()
        if profile.face:
            generation = self.generation
            def image_ready(content):
                if self.closed or generation != self.generation:
                    return
                pixmap = QPixmap()
                if content and pixmap.loadFromData(content):
                    self.avatar.setPixmap(pixmap.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation))
            self.host.work(lambda: self.service.avatar(profile.face), image_ready, on_error=lambda unused: None)

    def load_more(self):
        if self.loading or not self.current or not self.has_more or self.closed:
            return
        uid, page = self.current.mid, self.page + 1
        self.status.setText(f'正在读取第 {page} 页投稿，每页最多30条…')
        def loaded(result):
            seen = {video.bvid for video in self.rows}
            for video in result.items:
                if video.bvid not in seen or not video.available:
                    self.rows.append(video)
                    seen.add(video.bvid)
            self.page, self.total, self.has_more = page, result.total, result.has_more
            self.status.setText('最新投稿在前。筛选和全选只作用于已加载视频；切换UP会清空本次勾选。' if self.rows else '该UP当前没有可读取的普通视频投稿。')
            self.render()
        self._request(lambda: self.service.videos(uid, page=page), loaded)

    def visible(self):
        words = self.filter.text().casefold().split()
        return [video for video in self.rows if all(word in f'{video.title} {video.bvid}'.casefold() for word in words)]

    def block_reason(self, video):
        if not video.available:
            return video.reason or '不可处理'
        return '正在处理或等待' if self.host.queue.has_pending(video.bvid) else ''

    def state(self, video):
        if self.block_reason(video):
            return self.block_reason(video)
        entry = next((entry for entry in self.host.entries if entry.bvid == video.bvid), None)
        return '已有笔记' if entry and entry.note_path else '已有正文' if entry else '合作投稿' if video.collaboration else '可转换'

    def render(self, unused=None):
        videos = self.visible()
        with QSignalBlocker(self.table):
            self.table.setRowCount(len(videos))
            for row, video in enumerate(videos):
                seconds = video.duration
                duration = (f'{seconds//3600}:{seconds//60%60:02}:{seconds%60:02}' if seconds >= 3600 else f'{seconds//60}:{seconds%60:02}') if seconds else '—'
                for column, value in enumerate(('', video.title, duration, self.state(video))):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    if column == 1 and video.collaboration:
                        item.setToolTip(f'{video.title}\n合作投稿 · 原作者：{video.author}')
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, video)
                    self.table.setItem(row, column, item)
                self.table.setRowHeight(row, 44)
        self.update_controls()

    def update_controls(self):
        if self.closed:
            return
        self.search_button.setEnabled(not self.loading)
        self.accounts_more.setEnabled(not self.loading and self.search_has_more)
        self.more_button.setEnabled(not self.loading and bool(self.current) and self.has_more)
        self.space_button.setEnabled(bool(self.current))
        self.count.setText(f'已加载 {len(self.rows)} / {self.total} 条投稿' if self.current else '尚未加载投稿')
        self.selected = {key: video for key, video in self.selected.items() if not self.block_reason(video)}
        self.selected_count.setText(f'已选 {len(self.selected)} 个')
        self.use_button.setEnabled(bool(self.selected))
        eligible = {video.bvid for video in self.visible() if not self.block_reason(video)}
        self.select_all_button.setEnabled(bool(eligible))
        self.select_all_button.setText('取消全选' if eligible and eligible <= self.selected.keys() else '全选已加载')
        with QSignalBlocker(self.table):
            for row, video in enumerate(self.visible()):
                cell = self.table.item(row, 0)
                if cell is None:
                    continue
                flags = Qt.ItemFlag.ItemIsSelectable
                if not self.block_reason(video):
                    flags |= Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                cell.setFlags(flags)
                cell.setCheckState(Qt.CheckState.Checked if video.bvid in self.selected else Qt.CheckState.Unchecked)
                self.table.item(row, 3).setText(self.state(video))

    def item_changed(self, cell):
        if cell.column() != 0:
            return
        video = cell.data(Qt.ItemDataRole.UserRole)
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
        videos = {video.bvid: video for video in self.visible() if not self.block_reason(video)}
        if videos and videos.keys() <= self.selected.keys():
            for key in videos:
                self.selected.pop(key, None)
        else:
            self.selected.update(videos)
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

    def open_space(self):
        if self.current:
            QDesktopServices.openUrl(QUrl(f'https://space.bilibili.com/{self.current.mid}'))

    def manage_login(self):
        self.generation += 1
        self.host.open_account()
        if not self.closed:
            self.loading = False
            self.service = UploaderService(BiliClient(self.host.client.store))
            self.search()
