"""分集选择：初始只展示输入BV，所属合集额外视频按需展开。"""
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTreeWidget, QTreeWidgetItem, QHeaderView)

from bili_service import BiliClient
from video_catalog import VideoCatalog, collection_plan


class CollectionDialog(QDialog):
    def __init__(self, parent, host, infos, preselected=()):
        super().__init__(parent)
        self.host = host
        self.infos = list(infos)
        self.related = {v.bvid: v for info in infos for v in info.related}
        self.related_groups = {info.season_id: {v.bvid: v for v in info.related} for info in infos if info.season_id}
        self.selected = {part.key: part for info in infos for part in info.parts if part.key in preselected}
        self.loading = False
        self.closed = False
        self.generation = 0
        self.service = VideoCatalog(BiliClient(host.client.store))
        self.setWindowTitle('选择分集与合集 · Bili Note')
        self.resize(960, 660)
        self.setMinimumSize(820, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        title = QLabel('选择需要整理的分集')
        title.setObjectName('dialogTitle')
        layout.addWidget(title)
        hint = QLabel('每集一篇笔记，附合集目录。未勾选的分集不处理；所属合集的其他视频需另行展开。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('按分集标题筛选已展开的目录')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_tree)
        row.addWidget(self.search, 1)
        self.related_button = QPushButton('展开所属合集')
        self.related_button.setEnabled(any(info.season_id for info in infos))
        self.related_button.clicked.connect(self.expand_related)
        row.addWidget(self.related_button)
        layout.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['视频 / 分集', '时长', '范围'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 100)
        self.tree.setStyleSheet('QTreeWidget { background: white; border: 1px solid #e2e2e7; border-radius: 10px; } QTreeWidget::item { padding: 8px 4px; }')
        self.tree.itemChanged.connect(self.changed)
        self.tree.itemExpanded.connect(self.expanded)
        layout.addWidget(self.tree, 1)
        self.status = QLabel('多分P默认不全选；带 ?p= 的链接仅预选指定分集。')
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.select_all_button = QPushButton('全选当前结果')
        self.select_all_button.clicked.connect(self.select_all)
        buttons.addWidget(self.select_all_button)
        clear = QPushButton('清空选择')
        clear.clicked.connect(self.clear_selection)
        buttons.addWidget(clear)
        self.count = QLabel()
        buttons.addWidget(self.count)
        buttons.addStretch()
        cancel = QPushButton('返回')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.use_button = QPushButton('按分集加入任务')
        self.use_button.setObjectName('primary')
        self.use_button.clicked.connect(self.accept)
        buttons.addWidget(self.use_button)
        layout.addLayout(buttons)
        self.finished.connect(self._closed)
        for info in infos:
            self.add_info(info, preselect=True)
        self.update_controls()

    def _closed(self, unused=None):
        self.closed = True
        self.generation += 1

    def add_info(self, info, preselect=False, parent=None):
        with QSignalBlocker(self.tree):
            root = parent or QTreeWidgetItem(self.tree)
            root.takeChildren()
            root.setText(0, info.title)
            seconds = sum(part.duration for part in info.parts)
            root.setText(1, f'{seconds//3600}:{seconds//60%60:02}:{seconds%60:02}')
            root.setText(2, f'{len(info.parts)} 集')
            root.setData(0, Qt.ItemDataRole.UserRole, None)
            root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsUserCheckable & ~Qt.ItemFlag.ItemIsAutoTristate)
            for part in info.parts:
                item = QTreeWidgetItem(root, [f'P{part.page:03}  {part.title}', f'{part.duration//60}:{part.duration%60:02}', '单集笔记'])
                item.setData(0, Qt.ItemDataRole.UserRole, part)
                item.setToolTip(0, part.title)
                checked = part.key in self.selected or bool(preselect and
                    (info.requested_page == part.page or len(info.parts) == 1))
                item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                if checked:
                    self.selected[part.key] = part
            root.setExpanded(True)

    def part_items(self):
        return [self.tree.topLevelItem(i).child(j) for i in range(self.tree.topLevelItemCount())
                for j in range(self.tree.topLevelItem(i).childCount())
                if self.tree.topLevelItem(i).child(j).data(0, Qt.ItemDataRole.UserRole) is not None]

    def changed(self, unused=None, column=0):
        self.selected = {item.data(0, Qt.ItemDataRole.UserRole).key: item.data(0, Qt.ItemDataRole.UserRole)
                         for item in self.part_items() if item.checkState(0) == Qt.CheckState.Checked}
        self.update_controls()

    def update_controls(self):
        seconds = sum(part.duration for part in self.selected.values())
        self.count.setText(f'已选 {len(self.selected)} 集 · {seconds//3600}:{seconds//60%60:02}:{seconds%60:02}')
        self.use_button.setEnabled(bool(self.selected) and not self.loading)
        self.tree.setEnabled(not self.loading)

    def selected_parts(self):
        # 使用目录顺序，避免点击顺序导致课程重排。
        return [part for info in self.infos for part in info.parts if part.key in self.selected]

    def plans(self):
        result = {}
        for part in self.selected_parts():
            info = next(info for info in self.infos if info.bvid == part.bvid)
            group = [other for other in self.infos if info.season_id and other.season_id == info.season_id] or [info]
            if len(info.parts) > 1 or info.season_id:
                result[part.key] = collection_plan(group, tuple(self.related_groups.get(info.season_id, {}).values()))
        return result

    def filter_tree(self, unused=None):
        words = self.search.text().casefold().split()
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            visible = False
            for j in range(root.childCount()):
                item = root.child(j)
                match = all(word in f'{root.text(0)} {item.text(0)}'.casefold() for word in words)
                item.setHidden(not match)
                visible |= match
            root.setHidden(not visible)

    def select_all(self):
        items = [item for item in self.part_items() if not item.isHidden() and not item.parent().isHidden()]
        checked = not (items and all(item.checkState(0) == Qt.CheckState.Checked for item in items))
        with QSignalBlocker(self.tree):
            for item in items:
                item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.changed()

    def clear_selection(self):
        with QSignalBlocker(self.tree):
            for item in self.part_items():
                item.setCheckState(0, Qt.CheckState.Unchecked)
        self.changed()

    def _work(self, operation, done):
        self.loading = True
        self.generation += 1
        generation = self.generation
        self.related_button.setEnabled(False)
        self.update_controls()
        def success(value):
            if self.closed or generation != self.generation:
                return
            self.loading = False
            self.related_button.setEnabled(any(info.season_id for info in self.infos))
            done(value)
            self.update_controls()
        def failed(message):
            if self.closed or generation != self.generation:
                return
            self.loading = False
            self.related_button.setEnabled(True)
            self.status.setText(message + '；可再次展开重试。')
            self.update_controls()
        self.host.work(operation, success, on_error=failed)

    def expand_related(self):
        if self.loading:
            return
        infos = [info for info in self.infos if info.season_id]
        def read():
            return [(info.season_id, self.service.related(info)) for info in infos]
        def loaded(groups):
            videos = []
            for season_id, members in groups:
                self.related_groups.setdefault(season_id, {}).update({video.bvid: video for video in members})
                videos.extend(members)
            existing = {info.bvid for info in self.infos}
            for i in range(self.tree.topLevelItemCount()):
                identity = self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                if isinstance(identity, str):
                    existing.add(identity)
            for video in videos:
                self.related[video.bvid] = video
                if video.bvid not in existing:
                    root = QTreeWidgetItem(self.tree, [video.title, '', '展开读取目录'])
                    root.setData(0, Qt.ItemDataRole.UserRole, video.bvid)
                    QTreeWidgetItem(root, ['展开此视频以读取分集；不会自动勾选'])
                    existing.add(video.bvid)
            self.status.setText('已显示相关视频；展开某个视频才读取其分集，勾选后才转换。')
        self.status.setText('正在读取所属合集的视频目录…')
        self._work(read, loaded)

    def expanded(self, root):
        bvid = root.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(bvid, str) or self.loading:
            return
        def loaded(info):
            self.infos.append(info)
            self.add_info(info, parent=root)
            self.filter_tree()
            self.status.setText('分集目录已展开，请勾选需要处理的内容。')
        self.status.setText('正在读取该视频的分集目录…')
        self._work(lambda: self.service.read(bvid), loaded)
