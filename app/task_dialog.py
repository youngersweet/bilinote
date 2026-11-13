"""任务监视窗口：逐条进度、错误、重试及取消等待。"""
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QPlainTextEdit, QPushButton)

from task_queue import STATES


class TaskQueueDialog(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.setWindowTitle('视频任务 · Bili Note')
        self.resize(900, 590)
        self.setMinimumSize(750, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        title = QLabel('视频任务')
        title.setObjectName('dialogTitle')
        layout.addWidget(title)
        self.counts = QLabel()
        self.counts.setObjectName('muted')
        layout.addWidget(self.counts)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['视频', '状态', '当前阶段', '耗时'])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 230)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(3, 80)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.cellDoubleClicked.connect(lambda *unused: self.open_result())
        layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setObjectName('status')
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(100)
        self.details.setPlaceholderText('选中任务查看详情。运行中可继续添加视频，也可返回主窗口阅读笔记。')
        layout.addWidget(self.details)
        buttons = QHBoxLayout()
        self.retry_button = QPushButton('重试')
        self.retry_button.clicked.connect(self.retry)
        self.cancel_button = QPushButton('取消等待')
        self.cancel_button.clicked.connect(self.cancel)
        clear = QPushButton('清除已结束记录')
        clear.setToolTip('仅清除本次任务列表，不删除字幕、笔记或材料。')
        clear.clicked.connect(self.clear_finished)
        self.open_button = QPushButton('查看结果')
        self.open_button.setObjectName('primary')
        self.open_button.clicked.connect(self.open_result)
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(clear)
        buttons.addStretch()
        buttons.addWidget(self.open_button)
        layout.addLayout(buttons)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def selected_id(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self, select_id=None):
        selected = select_id or self.selected_id()
        tasks = self.host.queue.snapshots()
        counts = {key: sum(task.status == key for task in tasks) for key in STATES}
        self.counts.setText(f'并发 {self.host.queue.limit} · 进行 {counts["running"]} · 等待 {counts["queued"]} · 完成 {counts["done"]} · 失败/待补齐 {counts["failed"] + counts["partial"]}')
        scroll = self.table.verticalScrollBar().value()
        self.table.blockSignals(True)
        self.table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            elapsed = (task.finished_at or time.time()) - task.started_at if task.started_at else 0
            texts = [task.title, STATES[task.status], task.error or task.stage, f'{int(elapsed)//60}:{int(elapsed)%60:02}']
            for column, text in enumerate(texts):
                item = self.table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(row, column, item)
                item.setText(text)
                item.setToolTip(text)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, task.id)
                if column == 1:
                    item.setForeground(QColor('#b14a43' if task.status in ('failed', 'partial') else '#078c4c' if task.status in ('running', 'done') else '#909790'))
            self.table.setRowHeight(row, 47)
            if task.id == selected:
                self.table.selectRow(row)
        self.table.verticalScrollBar().setValue(scroll)
        self.table.blockSignals(False)
        self.selection_changed()

    def selection_changed(self):
        identity = self.selected_id()
        tasks = {task.id: task for task in self.host.queue.snapshots()}
        task = tasks.get(identity)
        self.retry_button.setEnabled(bool(task and self.host.queue.can_retry(task.id)))
        self.cancel_button.setEnabled(bool(task and task.status == 'queued'))
        self.open_button.setEnabled(bool(task and task.result and (Path(task.result.get('folder', '')) / 'video.json').is_file()))
        if task:
            model = f'{task.settings.model} / {task.settings.reasoning_effort}' if task.settings else '仅提取正文'
            self.details.setPlainText(f'{task.title}\n{task.source}\n模型设置：{model}\n{task.error or task.stage}')
        else:
            self.details.clear()

    def cancel(self):
        if self.selected_id():
            self.host.queue.cancel(self.selected_id())
            self.refresh()

    def retry(self):
        if self.selected_id():
            identity = self.host.queue.retry(self.selected_id())
            task = self.host.queue.get(identity) if identity else None
            if task and (self.host.result is None or self.host.result.get('bvid') == task.bvid):
                self.host.selected_task_id = identity
            self.refresh(select_id=identity)

    def open_result(self):
        if self.selected_id():
            task = self.host.queue.get(self.selected_id())
            if task.result and task.result.get('folder'):
                self.host.selected_task_id = task.id
                self.host.open_result_path(Path(task.result['folder']) / 'video.json', user=False)

    def clear_finished(self):
        self.host.queue.clear_finished()
        self.host.sync_queue_status()
        self.refresh()

    def showEvent(self, event):
        self.refresh()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
