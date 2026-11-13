"""安装时选择笔记位置；复用应用设置，不迁移或删除已有笔记。"""
from pathlib import Path
import sys
import tempfile

from note_library import load_preferences, save_preferences


def writable_folder(value):
    if not value.strip():
        raise ValueError('请选择笔记保存位置。')
    folder = Path(value.strip()).expanduser()
    if not folder.is_absolute():
        raise ValueError('请使用完整路径，或点击“浏览”选择文件夹。')
    folder = folder.resolve()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder) as probe:
            probe.write(b'BiliNote write check')
            probe.flush()
    except OSError as error:
        raise ValueError('无法写入该目录，请选择有写入权限的文件夹：\n' + str(folder)) from error
    return folder


def configure_output(value, preferences_path=None):
    folder = writable_folder(value)
    preferences = load_preferences(preferences_path)
    preferences['output_dir'] = str(folder)
    save_preferences(preferences, preferences_path)
    return folder


def main():
    from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                  QLineEdit, QPushButton, QFileDialog, QMessageBox)
    from desktop import ensure_application
    application = ensure_application()

    class SetupDialog(QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle('Bili Note · 笔记保存位置')
            self.resize(620, 220)
            layout = QVBoxLayout(self)
            hint = QLabel('默认在安装目录下创建“生成笔记”文件夹。\n检测到已有设置时，优先保留原保存位置；可在这里修改。')
            hint.setWordWrap(True)
            layout.addWidget(hint)
            row = QHBoxLayout()
            self.output = QLineEdit(load_preferences()['output_dir'])
            self.output.setAccessibleName('笔记保存位置')
            row.addWidget(self.output)
            browse = QPushButton('浏览…')
            browse.clicked.connect(self.browse)
            row.addWidget(browse)
            layout.addLayout(row)
            layout.addWidget(QLabel('确认后自动创建目录。原有笔记不会搬动，之后仍可在应用设置中更改。'))
            buttons = QHBoxLayout()
            buttons.addStretch()
            cancel = QPushButton('取消')
            cancel.clicked.connect(self.reject)
            buttons.addWidget(cancel)
            confirm = QPushButton('确认保存位置')
            confirm.setDefault(True)
            confirm.clicked.connect(self.save)
            buttons.addWidget(confirm)
            layout.addLayout(buttons)

        def browse(self):
            selected = QFileDialog.getExistingDirectory(self, '选择笔记保存位置', self.output.text())
            if selected:
                self.output.setText(selected)

        def save(self):
            try:
                configure_output(self.output.text())
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, '保存位置不可用', str(error))
                return
            self.accept()

    return 0 if SetupDialog().exec() == QDialog.DialogCode.Accepted else 1


if __name__ == '__main__':
    sys.exit(main())
