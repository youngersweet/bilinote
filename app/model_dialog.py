"""Qt 模型设置：复用原有配置、账户和短文本测试。"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                QComboBox, QPushButton, QPlainTextEdit)
from model_settings import (ModelSettings, EFFORT_LABELS, model_catalog, supported_efforts,
                            load_settings, save_settings, check_connection)


class ModelSettingsPanel(QWidget):
    saved = Signal(object)

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.testing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        title = QLabel('模型与推理')
        title.setObjectName('title')
        layout.addWidget(title)
        hint = QLabel('支持 Codex 账号登录和 API 登录，自动复用当前登录与服务地址。\n切换方式请在 Codex 中登录，再在这里测试连接；模型设置仅对 Bili Note 生效。')
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        layout.addWidget(hint)
        layout.addSpacing(18)
        layout.addWidget(QLabel('总结模型'))
        self.model_box = QComboBox()
        self.model_box.setEditable(True)
        self.model_box.addItems([row['slug'] for row in model_catalog()])
        layout.addWidget(self.model_box)
        layout.addSpacing(10)
        layout.addWidget(QLabel('推理强度'))
        self.effort_box = QComboBox()
        layout.addWidget(self.effort_box)
        suggestion = QLabel('日常笔记建议 medium；复杂技术内容可试 high。\n更高强度可能更慢，不能补齐缺失的字幕与画面。')
        suggestion.setObjectName('muted')
        suggestion.setWordWrap(True)
        layout.addWidget(suggestion)
        self.status_text = QPlainTextEdit()
        self.status_text.setObjectName('status')
        self.status_text.setReadOnly(True)
        self.status_text.setMinimumHeight(90)
        self.status_text.setPlainText('列表来自本机目录。测试成功才确认账户可用；测试会消耗少量额度。')
        layout.addWidget(self.status_text, 1)
        row = QHBoxLayout()
        self.reload_button = QPushButton('重读目录')
        self.reload_button.clicked.connect(self.reload_catalog)
        self.test_button = QPushButton('测试当前选择')
        self.test_button.clicked.connect(self.test)
        self.save_button = QPushButton('保存模型设置')
        self.save_button.setObjectName('primary')
        self.save_button.clicked.connect(self.save)
        row.addWidget(self.reload_button)
        row.addStretch()
        row.addWidget(self.test_button)
        row.addWidget(self.save_button)
        layout.addLayout(row)
        self.model_box.currentTextChanged.connect(self.update_efforts)
        try:
            current = load_settings()
            self.model_box.setCurrentText(current.model)
            self.update_efforts()
            self.effort_box.setCurrentIndex(self.effort_box.findData(current.reasoning_effort))
        except ValueError as error:
            self.status_text.setPlainText(str(error))
            self.model_box.setCurrentText('gpt-6.1-sol')
            self.update_efforts()
        self.saved_selection = (self.model_box.currentText().strip(), self.effort_box.currentData())

    def has_changes(self):
        return self.saved_selection != (self.model_box.currentText().strip(), self.effort_box.currentData())

    def update_efforts(self, unused=None):
        old = self.effort_box.currentData()
        values = supported_efforts(self.model_box.currentText().strip())
        self.effort_box.clear()
        for value in values:
            self.effort_box.addItem(value + ' · ' + EFFORT_LABELS.get(value, value), value)
        preferred = old if old in values else ('medium' if 'medium' in values else values[0])
        self.effort_box.setCurrentIndex(self.effort_box.findData(preferred))

    def reload_catalog(self):
        previous = self.model_box.currentText()
        self.model_box.blockSignals(True)
        self.model_box.clear()
        self.model_box.addItems([row['slug'] for row in model_catalog()])
        self.model_box.setCurrentText(previous)
        self.model_box.blockSignals(False)
        self.update_efforts()

    def selected(self):
        return ModelSettings(self.model_box.currentText().strip(), self.effort_box.currentData())

    def save(self):
        try:
            value = save_settings(self.selected())
            self.saved.emit(value)
            self.saved_selection = (value.model, value.reasoning_effort)
            self.status_text.setPlainText(f'已保存：{value.model} / {value.reasoning_effort}\n后续任务和下次启动自动复用。')
            return True
        except (ValueError, OSError) as error:
            self.status_text.setPlainText(str(error))
            return False

    def test(self):
        try:
            settings = self.selected()
        except ValueError as error:
            self.status_text.setPlainText(str(error))
            return
        self.testing = True
        self.enable_controls(False)
        self.status_text.setPlainText(f'正在测试 {settings.model} / {settings.reasoning_effort}…')
        self.host.work(lambda: check_connection(settings), self.succeeded, on_error=self.failed)

    def enable_controls(self, enabled):
        for widget in (self.model_box, self.effort_box, self.test_button, self.save_button, self.reload_button):
            widget.setEnabled(enabled)

    def succeeded(self, record):
        self.finish(f'测试成功：{record.get("model") or "未返回实际模型"} / {record.get("reasoning_effort") or "未返回实际推理强度"}\n提供商：{record.get("provider", "未返回")}\n本次选择尚未自动保存。')

    def failed(self, message):
        self.finish(message)

    def finish(self, message):
        self.testing = False
        self.enable_controls(True)
        self.status_text.setPlainText(message)


class ModelDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('模型设置 · Bili Note')
        self.resize(650, 550)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.panel = ModelSettingsPanel(parent, self)
        self.panel.saved.connect(parent.refresh_model_label)
        layout.addWidget(self.panel)

    def reject(self):
        if not self.panel.testing:
            super().reject()
