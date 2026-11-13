"""Double-click gestures restricted to unoccupied panel space."""
from PySide6.QtCore import Qt, Signal, QEvent, QRect
from PySide6.QtWidgets import QFrame, QToolTip


class BlankToggleFrame(QFrame):
    blank_double_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.blank_hint = ''

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.childAt(event.position().toPoint()) is None:
            self.blank_double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip and self.childAt(event.pos()) is None:
            QToolTip.showText(event.globalPos(), self.blank_hint, self, QRect(), 5000)
            return True
        return super().event(event)
