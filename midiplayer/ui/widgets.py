"""Reusable custom widgets."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QDial, QPushButton, QSizePolicy, QSlider, QWidget

from . import theme


def fmt_time(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


class VUMeter(QWidget):
    """Peak meter with smooth decay and peak hold."""

    def __init__(self, vertical: bool = True, parent=None):
        super().__init__(parent)
        self.vertical = vertical
        self.level = 0.0
        self.peak = 0.0
        self._peak_hold = 0
        if vertical:
            self.setFixedWidth(8)
            self.setMinimumHeight(60)
        else:
            self.setFixedHeight(8)
            self.setMinimumWidth(60)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    def push(self, value: float):
        """Feed a new peak (0..1); call regularly (~30 Hz)."""
        old = (self.level, self.peak)
        self.level = max(value, self.level * 0.82)
        if self.level < 0.01:
            self.level = 0.0
        if self.level >= self.peak:
            self.peak = self.level
            self._peak_hold = 25
        elif self._peak_hold > 0:
            self._peak_hold -= 1
        else:
            self.peak = max(0.0, self.peak - 0.02)
        if (self.level, self.peak) != old:
            self.update()

    def reset(self):
        self.level = self.peak = 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0e1015"))
        p.drawRoundedRect(r, 3, 3)
        if self.vertical:
            g = QLinearGradient(0, r.bottom(), 0, r.top())
        else:
            g = QLinearGradient(r.left(), 0, r.right(), 0)
        g.setColorAt(0.0, QColor("#2fd6a2"))
        g.setColorAt(0.65, QColor("#d7e24a"))
        g.setColorAt(1.0, QColor("#ff5c5c"))
        p.setBrush(QBrush(g))
        lv = min(1.0, self.level)
        if self.vertical:
            h = r.height() * lv
            p.drawRoundedRect(QRectF(r.left(), r.bottom() - h, r.width(), h), 3, 3)
            if self.peak > 0.02:
                y = r.bottom() - r.height() * min(1.0, self.peak)
                p.fillRect(QRectF(r.left(), y, r.width(), 2), QColor(theme.TEXT))
        else:
            w = r.width() * lv
            p.drawRoundedRect(QRectF(r.left(), r.top(), w, r.height()), 3, 3)
            if self.peak > 0.02:
                x = r.left() + r.width() * min(1.0, self.peak)
                p.fillRect(QRectF(x - 2, r.top(), 2, r.height()), QColor(theme.TEXT))


class Knob(QDial):
    """Compact rotary control; double-click resets to default."""
    reset = Signal()

    def __init__(self, minimum=0, maximum=127, default=64, bipolar=False, parent=None):
        super().__init__(parent)
        self.bipolar = bipolar
        self.setRange(minimum, maximum)
        self.default = default
        self.setValue(default)
        self.setFixedSize(34, 34)
        self.setNotchesVisible(False)
        self.setWrapping(False)
        self.color = QColor(theme.ACCENT)

    def mouseDoubleClickEvent(self, e):
        self.setValue(self.default)
        self.reset.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = min(self.width(), self.height()) - 6
        r = QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s)
        p.setPen(QPen(QColor(theme.BORDER), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, -45 * 16, 270 * 16)
        span = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        pen = QPen(self.color if self.isEnabled() else QColor(theme.TEXT_DIM), 3, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        if self.bipolar:
            # bipolar: draw from centre (default)
            c = (self.default - self.minimum()) / max(1, self.maximum() - self.minimum())
            start = 225 - 270 * c
            p.drawArc(r, int(start * 16), int(-270 * (span - c) * 16))
        else:
            p.drawArc(r, 225 * 16, int(-270 * span * 16))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.PANEL2))
        p.drawEllipse(r.adjusted(6, 6, -6, -6))


class SeekSlider(QSlider):
    """Horizontal slider that jumps directly to the clicked position."""

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setObjectName("seek")

    def _value_at(self, e) -> int:
        x = e.position().x() if hasattr(e, "position") else e.x()
        frac = min(1.0, max(0.0, (x - 6) / max(1, self.width() - 12)))
        return int(round(self.minimum() + (self.maximum() - self.minimum()) * frac))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self.setSliderDown(True)
            v = self._value_at(e)
            self.setValue(v)
            self.sliderMoved.emit(v)
            e.accept()
        else:
            e.ignore()

    def mouseMoveEvent(self, e):
        if getattr(self, "_dragging", False):
            v = self._value_at(e)
            self.setValue(v)
            self.sliderMoved.emit(v)
            e.accept()

    def mouseReleaseEvent(self, e):
        if getattr(self, "_dragging", False):
            self._dragging = False
            self.setSliderDown(False)
            e.accept()


class Fader(QSlider):
    """Vertical fader; double-click resets to unity."""

    def __init__(self, default=100, parent=None):
        super().__init__(Qt.Vertical, parent)
        self.setRange(0, 150)
        self.default = default
        self.setValue(default)
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

    def mouseDoubleClickEvent(self, e):
        self.setValue(self.default)


def small_button(text: str, name: str = "", checkable: bool = True, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCheckable(checkable)
    b.setFixedSize(26, 22)
    b.setFocusPolicy(Qt.NoFocus)
    b.setStyleSheet("padding: 0px; font-weight: 700; font-size: 9pt;")
    if tip:
        b.setToolTip(tip)
    return b


class ColorChip(QWidget):
    def __init__(self, color: str, text: str = "", parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.text = text
        self.setFixedSize(26, 22)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 5, 5)
        p.setPen(QColor("#111"))
        f = p.font()
        f.setBold(True)
        f.setPointSize(9)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self.text)
