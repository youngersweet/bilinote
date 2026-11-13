"""Local background images, bounded decoding and opacity-only painting."""
import hashlib
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QSize, QSaveFile, QIODevice
from PySide6.QtGui import QColor, QImageReader, QPainter, QPixmap, QLinearGradient
from PySide6.QtWidgets import QWidget


def read_photo(path):
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 80_000_000:
        raise ValueError('图片无效或尺寸过大，请选择不超过 8000 万像素的照片。')
    if max(size.width(), size.height()) > 2560:
        reader.setScaledSize(size.scaled(QSize(2560, 2560), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError('无法读取图片，请选择有效的 PNG、JPG 或 WebP 图片。')
    return image


def import_photo(source, directory):
    image = read_photo(source)
    # Store a normalized copy outside the repo; moving the original won't break the background.
    digest = hashlib.sha256(image.constBits()).hexdigest()[:24]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (digest + '.png')
    output = QSaveFile(str(path))
    if not output.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(output, 'PNG') or not output.commit():
        raise OSError('背景图片保存失败，请检查本机存储空间和写入权限。')
    return str(path)


class BackgroundPaint:
    def __init__(self):
        self.path = None
        self.pixmap = QPixmap()
        self.color = '#ffffff'
        self.transparency = 80

    def configure(self, color, path='', transparency=80):
        self.color, self.transparency = color, transparency
        if path != self.path:
            self.path = path
            try:
                self.pixmap = QPixmap.fromImage(read_photo(path)) if path else QPixmap()
            except (ValueError, OSError):
                self.pixmap = QPixmap()

    def draw(self, painter, rect):
        painter.fillRect(rect, QColor(self.color))
        if self.pixmap.isNull() or self.transparency == 100 or not rect.width() or not rect.height():
            return
        scale = max(rect.width() / self.pixmap.width(), rect.height() / self.pixmap.height())
        width, height = rect.width() / scale, rect.height() / scale
        source = QRectF((self.pixmap.width() - width) / 2, (self.pixmap.height() - height) / 2, width, height)
        painter.save()
        painter.setOpacity(1 - self.transparency / 100)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(QRectF(rect), self.pixmap, source)
        painter.restore()


class BackgroundSurface(QWidget):
    def __init__(self):
        super().__init__()
        self.background = BackgroundPaint()
        self.accent = ''

    def paintEvent(self, event):
        painter = QPainter(self)
        self.background.draw(painter, self.rect())
        if self.accent and self.background.pixmap.isNull():
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            tint = QColor(self.accent)
            tint.setAlpha(24)
            gradient.setColorAt(0, tint)
            tint.setAlpha(0)
            gradient.setColorAt(1, tint)
            painter.fillRect(self.rect(), gradient)
        painter.end()
