"""整套主题与阅读外观；由宿主管理预览、确认和取消，不重写笔记。"""
from pathlib import Path
from PySide6.QtCore import Signal, QSignalBlocker, Qt, QSize
from PySide6.QtGui import QColor, QPixmap, QPainter, QIcon
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton,
                               QSpinBox, QComboBox, QPushButton, QColorDialog, QCheckBox,
                               QSlider, QFileDialog, QMessageBox, QGridLayout, QInputDialog)

from reading_preferences import DEFAULT_FONT_SIZE, DEFAULT_BACKGROUND, BACKGROUNDS, background, font_size, transparency


from visual_themes import THEMES, snapshot, clean
from reading_preferences import colors


class ReadingControls(QWidget):
    changed = Signal(dict)
    modal_started = Signal()
    modal_finished = Signal()
    confirmed = Signal()
    cancelled = Signal()

    def __init__(self, preferences, parent=None):
        super().__init__(parent)
        self._values = clean(preferences)
        self.custom_themes = dict(preferences.get('custom_themes', {}))
        self._loading = False
        self._background = background(preferences.get('reading_background'))
        self._image = preferences.get('background_image', '')
        self.setObjectName('appearanceControls')
        self.setMinimumWidth(270)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        label = QLabel('外观与阅读')
        label.setStyleSheet('font-weight: 600; font-size: 16px;')
        layout.addWidget(label)
        self.build_themes(layout)
        layout.addWidget(QLabel('字体大小'))
        row = QHBoxLayout()
        smaller = QToolButton()
        smaller.setText('A−')
        smaller.setToolTip('缩小字体')
        row.addWidget(smaller)
        self.font_box = QSpinBox()
        self.font_box.setRange(12, 32)
        self.font_box.setValue(font_size(preferences.get('reading_font_size')))
        self.font_box.setAccessibleName('阅读字号')
        self.font_box.setKeyboardTracking(False)
        row.addWidget(self.font_box, 1)
        larger = QToolButton()
        larger.setText('A+')
        larger.setToolTip('放大字体')
        row.addWidget(larger)
        smaller.clicked.connect(self.font_box.stepDown)
        larger.clicked.connect(self.font_box.stepUp)
        layout.addLayout(row)
        layout.addWidget(QLabel('背景颜色'))
        self.theme_box = QComboBox()
        self.theme_box.setAccessibleName('阅读背景颜色')
        self._populate_backgrounds()
        layout.addWidget(self.theme_box)
        self.custom_button = QPushButton('自选颜色…')
        self.custom_button.clicked.connect(self.custom_color)
        layout.addWidget(self.custom_button)
        self.match_app = QCheckBox('应用背景与文章一致')
        self.match_app.setChecked(preferences.get('match_app_background', False))
        layout.addWidget(self.match_app)
        photo_row = QHBoxLayout()
        self.photo_button = QPushButton('选择背景照片…')
        self.photo_button.clicked.connect(self.choose_photo)
        self.remove_photo = QPushButton('移除照片')
        self.remove_photo.clicked.connect(self.clear_photo)
        photo_row.addWidget(self.photo_button)
        photo_row.addWidget(self.remove_photo)
        layout.addLayout(photo_row)
        self.photo_status = QLabel()
        self.photo_status.setObjectName('muted')
        layout.addWidget(self.photo_status)
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel('照片透明度'))
        self.opacity_label = QLabel()
        opacity_row.addWidget(self.opacity_label)
        layout.addLayout(opacity_row)
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(transparency(preferences.get('background_transparency')))
        self.opacity_slider.setAccessibleName('背景照片透明度')
        self.opacity_slider.setToolTip('0% 显示原图，100% 隐藏照片；文字和按钮不变透明。')
        layout.addWidget(self.opacity_slider)
        self.update_photo_status()
        self.build_details(layout)
        reset = QToolButton()
        reset.setText('恢复当前内置主题')
        reset.clicked.connect(self.reset)
        layout.addWidget(reset)
        hint = QLabel('即时预览 · 确认后保存 · 照片仅在本机')
        hint.setObjectName('muted')
        layout.addWidget(hint)
        self.actions_widget = QWidget()
        actions = QHBoxLayout(self.actions_widget)
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.cancelled.emit)
        apply = QPushButton('确认保存')
        apply.setObjectName('primary')
        apply.clicked.connect(self.confirmed.emit)
        actions.addWidget(cancel)
        actions.addWidget(apply)
        layout.addWidget(self.actions_widget)
        self.font_box.valueChanged.connect(self.emit_values)
        self.theme_box.currentIndexChanged.connect(self.theme_changed)
        self.match_app.toggled.connect(self.emit_values)
        self.opacity_slider.valueChanged.connect(self.emit_values)
        self.set_values(preferences)

    def _populate_backgrounds(self):
        with QSignalBlocker(self.theme_box):
            self.theme_box.clear()
            for label, value in BACKGROUNDS:
                self.theme_box.addItem(label, value)
            if self._background not in {value for label, value in BACKGROUNDS}:
                self.theme_box.addItem(f'自选颜色（{self._background}）', self._background)
            self.theme_box.setCurrentIndex(self.theme_box.findData(self._background))

    def emit_values(self, unused=None):
        if self._loading:
            return
        self.update_photo_status()
        self._values.update(reading_font_size=self.font_box.value(), reading_background=self._background,
                            match_app_background=self.match_app.isChecked(), background_image=self._image,
                            background_transparency=self.opacity_slider.value())
        self.update_color_labels()
        self.changed.emit(dict(self._values, custom_themes=dict(self.custom_themes)))

    def update_photo_status(self):
        self.remove_photo.setEnabled(bool(self._image))
        self.opacity_slider.setEnabled(bool(self._image))
        self.opacity_label.setText(f'{self.opacity_slider.value()}%')
        self.photo_status.setText('已使用本地照片' if self._image and Path(self._image).is_file()
                                 else '照片不可用，已显示底色' if self._image else '未选择照片，使用纯色背景')

    def dialog_call(self, callback, *args):
        # Qt closes popup menus when opening a modal child; retain the preview.
        self.modal_started.emit()
        try:
            return callback(*args)
        finally:
            self.modal_finished.emit()

    def choose_photo(self):
        source, _ = self.dialog_call(QFileDialog.getOpenFileName, self, '选择背景照片', '', '图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if not source:
            return
        try:
            from backgrounds import import_photo
            from bili_service import DATA_DIR
            self._image = import_photo(source, DATA_DIR / 'backgrounds')
        except (ValueError, OSError) as error:
            self.dialog_call(QMessageBox.warning, self, '无法使用背景照片', str(error))
            return
        self.emit_values()

    def clear_photo(self):
        self._image = ''
        self.emit_values()

    def theme_changed(self, unused=None):
        self._background = background(self.theme_box.currentData())
        self.adapt_ink()
        self.emit_values()

    def custom_color(self):
        color = self.dialog_call(QColorDialog.getColor, QColor(self._background), self, '选择阅读背景颜色')
        if color.isValid():
            self._background = color.name()
            self.adapt_ink()
            self._populate_backgrounds()
            self.emit_values()

    def adapt_ink(self):
        palette = colors(self._background)
        self._values.update(text_color=palette['foreground'], accent_color=palette['link'])

    def reset(self):
        key = self._values.get('theme_id', 'paper')
        self.set_values(THEMES.get(key, THEMES['paper']))
        self.emit_values()

    def build_themes(self, layout):
        grid = QGridLayout()
        grid.setSpacing(8)
        self.theme_cards = {}
        for i, (key, preset) in enumerate(THEMES.items()):
            card = QToolButton()
            card.setText(preset['name'])
            card.setToolTip(preset['description'])
            card.setToolTipDuration(5000)
            card.setCheckable(True)
            card.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            card.setIcon(self.thumbnail(preset))
            card.setIconSize(QSize(125, 65))
            card.setMinimumWidth(145)
            card.clicked.connect(lambda checked=False, k=key: self.select_theme(k))
            self.theme_cards[key] = card
            grid.addWidget(card, i // 2, i % 2)
        layout.addLayout(grid)
        row = QHBoxLayout()
        self.saved_box = QComboBox()
        self.saved_box.setAccessibleName('我的主题')
        self.refresh_saved()
        self.saved_box.activated.connect(self.select_saved)
        row.addWidget(self.saved_box, 1)
        save = QPushButton('另存为…')
        save.clicked.connect(self.save_custom)
        row.addWidget(save)
        layout.addLayout(row)

    @staticmethod
    def thumbnail(preset):
        pix = QPixmap(250, 130)
        pix.fill(QColor(preset['app_background']))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(preset['reading_background']))
        painter.drawRoundedRect(58, 9, 182, 112, 8, 8)
        painter.setBrush(QColor(preset['accent_color']))
        painter.drawRoundedRect(10, 23, 36, 9, 4, 4)
        painter.drawRoundedRect(76, 28, 74, 7, 3, 3)
        painter.setBrush(QColor(preset['text_color']))
        painter.setOpacity(.55)
        for y, width in ((49,140),(64,135),(79,142),(94,100)):
            painter.drawRoundedRect(76, y, width, 3, 1, 1)
        painter.end()
        pix.setDevicePixelRatio(2)
        return QIcon(pix)

    def refresh_saved(self):
        with QSignalBlocker(self.saved_box):
            self.saved_box.clear()
            self.saved_box.addItem('我的搭配…')
            self.saved_box.addItems(list(self.custom_themes))

    def select_theme(self, key):
        self.set_values(THEMES[key])
        self.emit_values()

    def select_saved(self, index):
        name = self.saved_box.itemText(index)
        if name in self.custom_themes:
            self.set_values(self.custom_themes[name])
            self.emit_values()

    def save_custom(self):
        name, ok = self.dialog_call(QInputDialog.getText, self, '保存搭配', '搭配名称')
        name = name.strip()[:40]
        if not ok or not name:
            return
        if name in self.custom_themes and self.dialog_call(QMessageBox.question, self, '覆盖搭配', f'替换“{name}”？') != QMessageBox.StandardButton.Yes:
            return
        self.custom_themes[name] = snapshot(self._values)
        self.refresh_saved()
        self.saved_box.setCurrentText(name)
        self.emit_values()

    def build_details(self, layout):
        self.detail_controls = {}
        font = QComboBox()
        for name, value in [('现代黑体', 'Microsoft YaHei UI'), ('书页宋体', 'SimSun'), ('人文楷体', 'KaiTi')]:
            font.addItem(name, value)
        font.setCurrentIndex(max(0, font.findData(self._values['reading_font'])))
        font.currentIndexChanged.connect(lambda: self.set_detail('reading_font', font.currentData()))
        layout.addWidget(QLabel('正文字体'))
        layout.addWidget(font)
        self.detail_controls['reading_font'] = font
        for key, title, low, high, step, suffix in [
            ('reading_line_height', '行距',120,220,5,'%'),
            ('reading_width','正文最大宽度',400,1400,40,' px')]:
            self.add_spin(layout, key, title, low, high, step, suffix)
        toggle = QToolButton()
        toggle.setText('进阶调整')
        toggle.setCheckable(True)
        layout.addWidget(toggle)
        advanced = QWidget()
        advanced.setVisible(False)
        toggle.toggled.connect(advanced.setVisible)
        details = QVBoxLayout(advanced)
        details.setContentsMargins(0,0,0,0)
        self.color_buttons = {}
        self.color_titles = {}
        for key, title in [('app_background','应用底色'),('text_color','正文文字'),('accent_color','强调颜色')]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, k=key, t=title: self.pick_detail_color(k,t))
            self.color_buttons[key] = button
            self.color_titles[key] = title
            details.addWidget(button)
        self.add_spin(details, 'ui_density', '界面留白',4,18,1,'')
        self.add_spin(details, 'ui_radius', '控件圆角',0,20,1,' px')
        layout.addWidget(advanced)

    def add_spin(self, layout, key, title, low, high, step, suffix):
        row = QHBoxLayout()
        row.addWidget(QLabel(title))
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setSuffix(suffix)
        spin.setValue(self._values[key])
        spin.setKeyboardTracking(False)
        spin.setAccessibleName(title)
        spin.valueChanged.connect(lambda value, k=key: self.set_detail(k, value))
        self.detail_controls[key] = spin
        row.addWidget(spin)
        layout.addLayout(row)

    def update_color_labels(self):
        for key, button in self.color_buttons.items():
            button.setText(f'{self.color_titles[key]}  {self._values[key]}')

    def set_detail(self, key, value):
        if not self._loading:
            self._values[key] = value
            self.emit_values()

    def pick_detail_color(self, key, title):
        color = self.dialog_call(QColorDialog.getColor, QColor(self._values[key]), self, title)
        if color.isValid():
            self.set_detail(key, color.name())

    def set_values(self, values):
        self._loading = True
        self._values = clean(values)
        if 'custom_themes' in values:
            self.custom_themes = dict(values['custom_themes'])
            self.refresh_saved()
        self._background = self._values['reading_background']
        self._image = self._values['background_image']
        self._populate_backgrounds()
        self.font_box.setValue(self._values['reading_font_size'])
        self.match_app.setChecked(self._values['match_app_background'])
        self.opacity_slider.setValue(self._values['background_transparency'])
        for key, control in self.detail_controls.items():
            if isinstance(control, QComboBox):
                control.setCurrentIndex(control.findData(self._values[key]))
            else:
                control.setValue(self._values[key])
        for key, card in self.theme_cards.items():
            card.setChecked(key == self._values['theme_id'])
        self.update_photo_status()
        self.update_color_labels()
        self._loading = False
