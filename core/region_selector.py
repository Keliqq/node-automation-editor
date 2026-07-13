import cv2
import numpy as np

from PySide6 import QtCore, QtGui, QtWidgets

from core.screen import list_monitors, grab_monitor_bgr


class RegionSelector(QtWidgets.QWidget):
    region_selected = QtCore.Signal(int, int, int, int)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

        desktop = QtWidgets.QApplication.primaryScreen().virtualGeometry()
        self._desktop_origin = QtCore.QPoint(desktop.x(), desktop.y())
        desktop_size = desktop.size()

        screens = list_monitors()
        full = np.zeros((desktop_size.height(), desktop_size.width(), 3), dtype=np.uint8)
        for m in screens:
            img, off_x, off_y = grab_monitor_bgr(m["index"])
            x = off_x - desktop.x()
            y = off_y - desktop.y()
            h, w = img.shape[:2]
            full[y : y + h, x : x + w] = img

        rgb = cv2.cvtColor(full, cv2.COLOR_BGR2RGB)
        qimage = QtGui.QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.shape[1] * 3, QtGui.QImage.Format.Format_RGB888)
        self._screenshot = QtGui.QPixmap.fromImage(qimage)

        self.move(desktop.topLeft())
        self.setFixedSize(desktop_size)

        self._start_pos = None
        self._current_rect = None

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.drawPixmap(0, 0, self._screenshot)

        if self._current_rect:
            region = QtGui.QRegion(self.rect())
            region -= QtGui.QRegion(self._current_rect)
            painter.setClipRegion(region)

        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 140))
        painter.setClipping(False)

        if self._current_rect:
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 60, 60), 3))
            painter.drawRect(self._current_rect)

            painter.setPen(QtGui.QColor(255, 255, 255))
            info = f"{self._current_rect.width()} \u00d7 {self._current_rect.height()}"
            painter.drawText(
                self._current_rect.adjusted(8, 8, -8, -8),
                QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft,
                info,
            )

    def _to_global(self, local_point):
        return local_point + self._desktop_origin

    def mousePressEvent(self, event):
        self._start_pos = event.position().toPoint()
        self._current_rect = QtCore.QRect(self._start_pos, self._start_pos)
        self.update()

    def mouseMoveEvent(self, event):
        if self._start_pos:
            self._current_rect = QtCore.QRect(
                self._start_pos, event.position().toPoint()
            ).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if self._current_rect and self._current_rect.isValid():
            r = self._current_rect
            global_pos = self._to_global(r.topLeft())
            self.region_selected.emit(
                global_pos.x(), global_pos.y(), r.width(), r.height()
            )
        self.close()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()


def select_region(parent=None):
    selector = RegionSelector()
    result = []

    loop = QtCore.QEventLoop()
    selector.region_selected.connect(
        lambda x, y, w, h: (result.extend([x, y, w, h]), loop.quit())
    )
    selector.destroyed.connect(loop.quit)
    selector.show()
    loop.exec()

    return tuple(result) if result else None
