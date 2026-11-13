"""新建、常规设置与 B 站账户窗口。"""
from io import BytesIO
from pathlib import Path
import time

import qrcode
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QToolButton, QComboBox, QCheckBox, QFileDialog, QTabWidget, QMessageBox,
    QPlainTextEdit, QSpinBox, QScrollArea, QRadioButton, QButtonGroup)

from bili_service import parse_bvid
from model_dialog import ModelSettingsPanel
from ui_theme import icon, GREEN
from task_queue import parse_video_sources

ROUTES = [('自动：字幕够用即停', 'auto'), ('仅提取字幕，不转写', 'subtitles'),
          ('浏览器字幕正文', 'browser'), ('完整音轨转写', 'audio')]


def heading(layout, title, subtitle):
    label = QLabel(title)
    label.setObjectName('dialogTitle')
    layout.addWidget(label)
    hint = QLabel(subtitle)
    hint.setObjectName('muted')
    hint.setWordWrap(True)
    layout.addWidget(hint)
    layout.addSpacing(18)


class OptionsPanel(QWidget):
    def __init__(self, preferences, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)
        layout.addWidget(QLabel('正文读取方式'))
        self.route = QComboBox()
        for title, code in ROUTES:
            self.route.addItem(title, code)
        self.route.setCurrentIndex(max(0, self.route.findData(preferences.get('mode', 'auto'))))
        layout.addWidget(self.route)
        hint = QLabel('先取字幕；正文不可用时再回退，不重复运行所有识别方式。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.visual = QCheckBox('音轨转写后按需核对画面')
        self.visual.setChecked(preferences.get('visual', True))
        self.auto_summary = QCheckBox('取得完整正文后自动生成笔记')
        self.auto_summary.setChecked(preferences.get('auto_summary', True))
        layout.addWidget(self.visual)
        layout.addWidget(self.auto_summary)
        layout.addSpacing(8)
        layout.addWidget(QLabel('笔记保存位置'))
        row = QHBoxLayout()
        self.output = QLineEdit(str(preferences.get('output_dir', '')))
        row.addWidget(self.output, 1)
        choose = QPushButton('选择')
        choose.clicked.connect(self.choose_folder)
        row.addWidget(choose)
        layout.addLayout(row)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '选择笔记保存位置', self.output.text())
        if folder:
            self.output.setText(folder)

    def values(self):
        return {'mode': self.route.currentData(), 'visual': self.visual.isChecked(),
                'auto_summary': self.auto_summary.isChecked(), 'output_dir': self.output.text().strip()}


class NewVideoDialog(QDialog):
    def __init__(self, parent, preferences):
        super().__init__(parent)
        self.selected_items = None
        self.collection_plans = {}
        self.preflight_generation = 0
        self.preflight_closed = False
        self.finished.connect(self._preflight_finished)
        self.setWindowTitle('新建笔记 · Bili Note')
        self.setMinimumWidth(590)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 28, 30, 26)
        layout.setSpacing(12)
        heading(layout, '添加视频', '粘贴 B站链接或 BV 号，每行一条。课程分集可在下一步选择。')
        self.link = QPlainTextEdit()
        self.link.setPlaceholderText('https://www.bilibili.com/video/BV…/\nhttps://www.bilibili.com/video/BV…/')
        self.link.setMinimumHeight(130)
        self.link.setMaximumHeight(180)
        self.link.setTabChangesFocus(True)
        self.link.setObjectName('links')
        layout.addWidget(self.link)
        source_row = QHBoxLayout()
        self.favorites_button = QPushButton('从 B站收藏选择')
        self.favorites_button.clicked.connect(self.add_favorites)
        source_row.addWidget(self.favorites_button)
        self.uploader_button = QPushButton('从 UP主选择')
        self.uploader_button.clicked.connect(self.add_uploader)
        source_row.addWidget(self.uploader_button)
        source_row.addStretch()
        layout.addLayout(source_row)
        self.error = QLabel('')
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color: #c2574c;')
        self.error.hide()
        layout.addWidget(self.error)
        toggle = QToolButton()
        toggle.setText('更多选项')
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.RightArrow)
        toggle.setCheckable(True)
        layout.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.advanced = OptionsPanel(preferences)
        self.advanced.auto_summary.hide()
        goal_row = QHBoxLayout()
        self.summary_choice = QRadioButton('生成知识笔记')
        self.extract_choice = QRadioButton('仅提取正文')
        self.goal_group = QButtonGroup(self)
        self.goal_group.addButton(self.summary_choice)
        self.goal_group.addButton(self.extract_choice)
        self.summary_choice.setChecked(preferences.get('auto_summary', True))
        self.extract_choice.setChecked(not self.summary_choice.isChecked())
        self.summary_choice.toggled.connect(self.advanced.auto_summary.setChecked)
        goal_row.addWidget(self.summary_choice)
        goal_row.addWidget(self.extract_choice)
        goal_row.addStretch()
        layout.insertLayout(layout.indexOf(toggle), goal_row)
        self.goal_hint = QLabel('生成笔记需要可用模型；仅提取正文可稍后再整理。')
        self.goal_hint.setObjectName('muted')
        self.goal_hint.setWordWrap(True)
        layout.insertWidget(layout.indexOf(toggle), self.goal_hint)
        self.advanced.hide()
        layout.addWidget(self.advanced)
        def expanded(value):
            self.advanced.setVisible(value)
            toggle.setArrowType(Qt.ArrowType.DownArrow if value else Qt.ArrowType.RightArrow)
            self.adjustSize()
        toggle.toggled.connect(expanded)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        self.start_button = QPushButton('开始整理')
        self.start_button.setObjectName('primary')
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.start)
        buttons.addWidget(cancel)
        buttons.addWidget(self.start_button)
        layout.addLayout(buttons)
        self.link.setFocus()
        self.summary_choice.toggled.connect(self.update_goal)
        self.update_goal()

    def update_goal(self, unused=None):
        self.start_button.setText('开始整理' if self.summary_choice.isChecked() else '提取正文')

    def add_favorites(self):
        from favorite_dialog import FavoriteDialog
        dialog = FavoriteDialog(self, self.parent())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._merge_sources(dialog.sources())

    def add_uploader(self):
        from uploader_dialog import UploaderDialog
        dialog = UploaderDialog(self, self.parent())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._merge_sources(dialog.sources())

    def _merge_sources(self, sources):
        original = self.link.toPlainText().strip()
        seen = set()
        for token in original.split():
            try:
                seen.add(parse_bvid(token))
            except ValueError:
                pass
        additions = []
        for source in sources:
            bvid = parse_bvid(source)
            if bvid not in seen:
                additions.append(source)
                seen.add(bvid)
        self.link.setPlainText('\n'.join(([original] if original else []) + additions))

    def values(self):
        sources = ([part.source for part in self.selected_items] if self.selected_items is not None
                   else parse_video_sources(self.link.toPlainText()))
        return {'source': sources[0], 'sources': sources, 'parts': self.selected_items,
                'collections': self.collection_plans, **self.advanced.values()}

    def _preflight_finished(self, unused=None):
        self.preflight_closed = True
        self.preflight_generation += 1

    def _preflight_idle(self):
        self.start_button.setEnabled(True)
        self.update_goal()
        self.link.setReadOnly(False)
        self.favorites_button.setEnabled(True)
        self.uploader_button.setEnabled(True)

    def start(self):
        try:
            sources = parse_video_sources(self.link.toPlainText())
            from install_settings import writable_folder
            self.advanced.output.setText(str(writable_folder(self.advanced.output.text())))
        except (ValueError, OSError) as error:
            self.error.setText(str(error))
            self.error.show()
            return
        from bili_service import BiliClient, source_page
        from video_catalog import VideoCatalog
        self.preflight_generation += 1
        generation = self.preflight_generation
        self.start_button.setEnabled(False)
        self.start_button.setText('正在检查视频目录…')
        self.link.setReadOnly(True)
        self.favorites_button.setEnabled(False)
        self.uploader_button.setEnabled(False)
        self.error.hide()
        host = self.parent()
        def inspect():
            service = VideoCatalog(BiliClient(host.client.store))
            infos = {}
            requested = set()
            for source in sources:
                bvid = parse_bvid(source)
                if bvid not in infos:
                    infos[bvid] = service.read(source)
                page = source_page(source)
                if page is not None:
                    part = next((part for part in infos[bvid].parts if part.page == page), None)
                    if part is None:
                        raise ValueError('指定分P不在当前目录中，请检查链接。')
                    requested.add(part.key)
            return list(infos.values()), requested
        def loaded(value):
            if self.preflight_closed or generation != self.preflight_generation:
                return
            self._preflight_idle()
            infos, requested = value
            if any(len(info.parts) > 1 or info.season_id for info in infos):
                from collection_dialog import CollectionDialog
                dialog = CollectionDialog(self, host, infos, requested)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                self.selected_items = dialog.selected_parts()
                self.collection_plans = dialog.plans()
            else:
                self.selected_items = [part for info in infos for part in info.parts]
            if self.selected_items:
                self.accept()
        def failed(message):
            if self.preflight_closed or generation != self.preflight_generation:
                return
            self._preflight_idle()
            self.error.setText(message)
            self.error.show()
        host.work(inspect, loaded, on_error=failed)


class SettingsDialog(QDialog):
    def __init__(self, parent, preferences):
        super().__init__(parent)
        self.host = parent
        self.setWindowTitle('设置 · Bili Note')
        self.resize(700, 600)
        self.setMinimumSize(650, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        general = QWidget()
        body = QVBoxLayout(general)
        body.setContentsMargins(28, 22, 28, 20)
        heading(body, '处理偏好', '简单的默认值，下次新建时继续使用。')
        self.options = OptionsPanel(preferences)
        body.addWidget(self.options)
        row = QHBoxLayout()
        row.addWidget(QLabel('同时处理视频数'))
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 8)
        self.concurrency.setValue(preferences.get('concurrency', 2))
        self.concurrency.setFixedWidth(90)
        row.addWidget(self.concurrency)
        row.addStretch()
        body.addLayout(row)
        resource_hint = QLabel('支持同时处理 1—8 条，建议先试 4 条再调高；API 限流时请降低并发。\n本地语音转写仍逐条执行，避免争抢 CPU。')
        resource_hint.setObjectName('muted')
        resource_hint.setWordWrap(True)
        body.addWidget(resource_hint)
        self.shortcut_button = QPushButton('创建桌面快捷方式')
        self.shortcut_button.clicked.connect(self.create_shortcut)
        body.addWidget(self.shortcut_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.shortcut_status = QLabel('')
        self.shortcut_status.setObjectName('muted')
        self.shortcut_status.setWordWrap(True)
        body.addWidget(self.shortcut_status)
        body.addStretch()
        general_scroll = QScrollArea()
        general_scroll.setWidgetResizable(True)
        general_scroll.setWidget(general)
        general_scroll.setStyleSheet('QScrollArea { border: none; background: transparent; }')
        self.tabs.addTab(general_scroll, '常规')
        self.model_panel = ModelSettingsPanel(parent)
        self.model_panel.saved.connect(parent.refresh_model_label)
        self.tabs.addTab(self.model_panel, '模型与推理')
        row = QHBoxLayout()
        row.setContentsMargins(28, 0, 28, 0)
        row.addStretch()
        done = QPushButton('完成')
        done.setObjectName('primary')
        done.clicked.connect(self.finish)
        row.addWidget(done)
        layout.addLayout(row)

    def finish(self):
        if self.model_panel.testing:
            return
        try:
            from install_settings import writable_folder
            folder = writable_folder(self.options.output.text())
            self.options.output.setText(str(folder))
            # Save pending model edits when completing the settings dialog.
            if self.model_panel.has_changes() and not self.model_panel.save():
                self.tabs.setCurrentIndex(1)
                return
            self.host.save_ui_preferences({**self.options.values(), 'concurrency': self.concurrency.value()})
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, '设置未保存', str(error))
            return
        self.accept()

    def create_shortcut(self):
        from desktop_shortcut import create_desktop_shortcut
        self.shortcut_button.setEnabled(False)
        self.shortcut_status.setText('正在创建桌面快捷方式…')
        def done(message):
            self.shortcut_button.setEnabled(True)
            self.shortcut_status.setText(message)
        self.host.work(create_desktop_shortcut, done, on_error=done)

    def reject(self):
        if not self.model_panel.testing:
            super().reject()


class AccountDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.host = parent
        self.client = parent.client
        self.generation = 0
        self.closed = False
        self.polling = False
        self.ticket = None
        self.setWindowTitle('B站账户 · Bili Note')
        self.setFixedSize(430, 565)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 26, 32, 28)
        layout.setSpacing(12)
        heading(layout, 'B站账户', '登录用于读取视频正文和收藏清单，加密保存在本机。')
        self.identity = QLabel('正在检查登录状态…')
        self.identity.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.identity)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setMinimumHeight(200)
        layout.addWidget(self.qr)
        self.hint = QLabel('')
        self.hint.setObjectName('muted')
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint)
        layout.addStretch()
        self.login_button = QPushButton('扫码登录')
        self.login_button.setObjectName('primary')
        self.login_button.clicked.connect(self.begin)
        self.login_button.setEnabled(not parent.busy)
        layout.addWidget(self.login_button)
        row = QHBoxLayout()
        check = QPushButton('检查状态')
        check.clicked.connect(self.check)
        self.clear_button = QPushButton('清除本机登录')
        self.clear_button.clicked.connect(self.clear_login)
        self.clear_button.setEnabled(not parent.busy)
        row.addWidget(check)
        row.addWidget(self.clear_button)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.poll)
        self.render(parent.account_status)
        self.check()

    def check(self):
        generation = self.generation
        def checked(status):
            if self.closed or generation != self.generation:
                return
            self.host.show_account(status)
            if not self.timer.isActive():
                self.render(status)
        def failed(message):
            if not self.closed and generation == self.generation:
                self.hint.setText(message)
        self.host.work(self.client.status, checked, on_error=failed)

    def checked(self, status):
        if self.closed:
            return
        self.host.show_account(status)
        self.render(status)

    def render(self, status):
        if status.get('logged_in'):
            self.identity.setText(status.get('name') or '已登录')
            self.qr.setPixmap(icon('check', GREEN, 80).pixmap(80, 80))
            self.hint.setText('登录有效 · 已加密保存\n关闭软件后自动保留，下次打开继续使用。')
            self.login_button.setText('重新扫码登录')
        else:
            self.identity.setText('登录已失效' if status.get('saved') else '尚未登录')
            self.qr.clear()
            self.qr.setPixmap(icon('user', '#aeb6b0', 70).pixmap(70, 70))
            self.hint.setText('点击下方扫码登录，使用 B站 App 确认。')
            self.login_button.setText('扫码登录')

    def begin(self):
        if self.host.busy:
            return
        self.generation += 1
        generation = self.generation
        self.timer.stop()
        self.ticket = None
        self.polling = False
        self.hint.setText('正在生成二维码…')
        self.host.work(self.client.begin_login, lambda ticket: self.ticket_ready(ticket, generation),
                       on_error=lambda message: self.generation_error(generation, message))

    def ticket_ready(self, ticket, generation):
        if self.closed or generation != self.generation:
            return
        self.ticket = ticket
        buffer = BytesIO()
        qrcode.make(ticket['url']).save(buffer, format='PNG')
        pixmap = QPixmap()
        pixmap.loadFromData(buffer.getvalue())
        self.qr.setPixmap(pixmap.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.hint.setText('使用 B站 App 扫码，并在手机上确认。')
        self.login_button.setText('刷新二维码')
        self.timer.start()

    def poll(self):
        if self.closed or self.polling or not self.ticket:
            return
        self.polling = True
        generation, ticket = self.generation, self.ticket
        def done(status):
            if self.closed or generation != self.generation:
                return
            self.polling = False
            if status['state'] == 'confirmed':
                self.timer.stop()
                self.checked({'logged_in': True, 'name': status['name'], 'saved': True})
            elif status['state'] == 'expired':
                self.timer.stop()
                self.qr.clear()
                self.qr.setText('二维码已过期')
                self.hint.setText('点击“刷新二维码”重新生成。')
            elif status['state'] == 'confirm':
                self.hint.setText('已扫码，请在手机上确认登录。')
        self.host.work(lambda: self.client.poll_login(ticket), done,
                       on_error=lambda message: self.generation_error(generation, message))

    def generation_error(self, generation, message):
        if not self.closed and generation == self.generation:
            self.error(message)

    def error(self, message):
        self.polling = False
        if not self.closed:
            self.timer.stop()
            self.hint.setText(message)

    def clear_login(self):
        if self.host.busy:
            return
        if QMessageBox.question(self, '清除本机登录', '清除本工具保存的登录？不会退出其他浏览器或设备。') != QMessageBox.StandardButton.Yes:
            return
        self.generation += 1
        self.timer.stop()
        self.client.logout()
        self.checked({'logged_in': False, 'saved': False})

    def set_processing(self, processing):
        self.login_button.setEnabled(not processing)
        self.clear_button.setEnabled(not processing)

    def done(self, result):
        self.closed = True
        self.generation += 1
        self.timer.stop()
        super().done(result)
