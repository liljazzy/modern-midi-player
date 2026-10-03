"""Piano keyboard that lights up the notes being played, coloured by MIDI channel."""
from __future__ import annotations

from typing import Dict

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..gm import BLACK_KEYS, CHANNEL_COLORS
from . import theme

LOW, HIGH = 21, 108          # A0 .. C8, the 88 keys of a piano


def _is_black(pitch: int) -> bool:
    return pitch % 12 in BLACK_KEYS


class PianoStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(76)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip("Notes being played (colour = MIDI channel)")
        self._active: Dict[int, int] = {}      # pitch -> channel
        self._whites = [p for p in range(LOW, HIGH + 1) if not _is_black(p)]

    def set_active(self, active: Dict[int, int]):
        if active != self._active:
            self._active = dict(active)
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(theme.BG2))
        n = len(self._whites)
        kw = w / n
        pos = {pitch: i for i, pitch in enumerate(self._whites)}
        edge = QPen(QColor("#0d0f14"))

        # white keys
        for pitch, i in pos.items():
            r = QRectF(i * kw, 0, kw, h - 2)
            ch = self._active.get(pitch)
            p.setPen(edge)
            p.setBrush(QColor(CHANNEL_COLORS[ch]) if ch is not None else QColor("#e9ecf4"))
            p.drawRect(r)

        # black keys sit on the boundary between the two neighbouring white keys
        bw, bh = kw * 0.62, (h - 2) * 0.62
        for pitch in range(LOW, HIGH + 1):
            if not _is_black(pitch):
                continue
            left = pos.get(pitch - 1)
            if left is None:
                continue
            ch = self._active.get(pitch)
            x = (left + 1) * kw - bw / 2
            p.setPen(edge)
            p.setBrush(QColor(CHANNEL_COLORS[ch]).darker(115) if ch is not None else QColor("#1a1d26"))
            p.drawRect(QRectF(x, 0, bw, bh))
        p.end()
