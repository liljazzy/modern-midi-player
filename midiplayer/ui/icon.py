"""Programmatically drawn application icon (no image files needed)."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPixmap


def make_icon(size: int = 256) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#7c6cff"))
    g.setColorAt(1, QColor("#39d0c8"))
    p.setBrush(QBrush(g))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(8, 8, size - 16, size - 16), size * 0.22, size * 0.22)
    # piano keys
    kw = (size - 80) / 5
    for i in range(5):
        p.setBrush(QColor("white"))
        p.drawRoundedRect(QRectF(40 + i * kw + 2, 60, kw - 4, size - 120), 8, 8)
    p.setBrush(QColor("#1b1e26"))
    for i in (0, 1, 3):
        p.drawRoundedRect(QRectF(40 + (i + 1) * kw - kw * 0.3, 60, kw * 0.6, (size - 120) * 0.58), 6, 6)
    p.end()
    return pm
