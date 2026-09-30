"""Vector icons for the transport bar, drawn with QPainter (no emoji / font dependence)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from . import theme

S = 64  # drawn at 64x64, scaled down by the button's icon size


def _pixmap(draw, color: str) -> QPixmap:
    pm = QPixmap(S, S)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(color)
    p.setBrush(QBrush(c))
    p.setPen(Qt.NoPen)
    draw(p, c)
    p.end()
    return pm


def _poly(*pts) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _play(p, c):
    p.drawPolygon(_poly((18, 10), (18, 54), (54, 32)))


def _pause(p, c):
    p.drawRoundedRect(QRectF(14, 10, 12, 44), 3, 3)
    p.drawRoundedRect(QRectF(38, 10, 12, 44), 3, 3)


def _prev(p, c):
    p.drawRoundedRect(QRectF(10, 12, 8, 40), 2, 2)
    p.drawPolygon(_poly((54, 12), (54, 52), (22, 32)))


def _next(p, c):
    p.drawRoundedRect(QRectF(46, 12, 8, 40), 2, 2)
    p.drawPolygon(_poly((10, 12), (10, 52), (42, 32)))


def _loop(p, c):
    pen = QPen(c, 6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    path = QPainterPath()
    path.moveTo(20, 16)
    path.lineTo(44, 16)
    path.arcTo(QRectF(36, 16, 16, 32), 90, -180)
    path.lineTo(20, 48)
    path.arcTo(QRectF(12, 16, 16, 32), -90, -180)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(_poly((32, 5), (32, 27), (46, 16)))     # top arrow  ->
    p.drawPolygon(_poly((32, 37), (32, 59), (18, 48)))    # bottom arrow <-


def _speaker(p, c):
    p.drawPolygon(_poly((8, 25), (20, 25), (34, 12), (34, 52), (20, 39), (8, 39)))
    pen = QPen(c, 5)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(28, 22, 16, 20), -60 * 16, 120 * 16)
    p.drawArc(QRectF(26, 12, 30, 40), -60 * 16, 120 * 16)


def icon(kind: str, color: str = theme.TEXT, on_color: str = "") -> QIcon:
    """*on_color* (optional) is used when the button is checked."""
    draw = {"play": _play, "pause": _pause, "prev": _prev, "next": _next,
            "loop": _loop, "speaker": _speaker}[kind]
    ic = QIcon()
    ic.addPixmap(_pixmap(draw, color), QIcon.Normal, QIcon.Off)
    ic.addPixmap(_pixmap(draw, on_color or color), QIcon.Normal, QIcon.On)
    return ic
