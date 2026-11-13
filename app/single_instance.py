"""One desktop per user data directory; repeat launches restore its window."""
import hashlib
from pathlib import Path
import time

from PySide6.QtCore import QLockFile, QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    def __init__(self, data_dir, parent=None):
        super().__init__(parent)
        directory = Path(data_dir).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(str(directory).casefold().encode('utf-8')).hexdigest()[:24]
        self.name = 'BiliNote-desktop-' + identity
        self.lock = QLockFile(str(directory / 'desktop.lock'))
        # A running long-lived desktop must never become stale just because of age.
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._activate)
        self.restore = None
        self.pending = False
        self.owner = False

    def acquire_or_activate(self, timeout=8):
        deadline = time.monotonic() + timeout
        while True:
            if self.lock.tryLock(0):
                self.owner = True
                # Only the lock owner may remove a leftover server endpoint.
                QLocalServer.removeServer(self.name)
                if not self.server.listen(self.name):
                    self.close()
                    raise RuntimeError('无法建立 Bili Note 单实例通道，请稍后重试。')
                return True
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(250):
                socket.disconnectFromServer()
                return False
            if time.monotonic() >= deadline:
                raise RuntimeError('Bili Note 已在运行或正在启动，请稍后再次打开；不会重复创建窗口。')
            time.sleep(.05)

    def bind(self, restore):
        self.restore = restore
        if self.pending:
            self.pending = False
            QTimer.singleShot(0, restore)

    def _activate(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.close()
            socket.deleteLater()
            if self.restore:
                self.restore()
            else:
                self.pending = True

    def close(self):
        if self.owner:
            self.server.close()
            self.lock.unlock()
            self.owner = False
