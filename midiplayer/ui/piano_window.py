"""The popped-out piano: a frameless, transparent window that shows nothing but the keyboard and its particles,
plus the little title bar the piano has while it is docked in the main window."""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from .piano_strip import KEYS_H, WHITE_KEYS

WS_EX_TRANSPARENT, GWL_EXSTYLE = 0x20, -20


def _native_click_through(hwnd: int, on: bool) -> bool:
    """Windows: switch the window's WS_EX_TRANSPARENT style directly (Qt's own WindowTransparentForInput flag can switch it
    on but does not reliably switch it off again). Returns False if that was not possible."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        get = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        put = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        get.restype, get.argtypes = ctypes.c_ssize_t, [ctypes.c_void_p, ctypes.c_int]
        put.restype, put.argtypes = ctypes.c_ssize_t, [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        style = get(hwnd, GWL_EXSTYLE)
        put(hwnd, GWL_EXSTYLE, (style | WS_EX_TRANSPARENT) if on else (style & ~WS_EX_TRANSPARENT))
        return True
    except Exception:
        return False


GRIP_MIN, GRIP_MAX = 10, 60   # px: the resize handles are one white key wide, but never narrower / wider than this
MIN_WIDTH = 320
MAX_WIDTH = 8000


class EdgeGrip(QWidget):
    """A resize handle on one side (left or right) of the popped-out window: it sits on the end key of the keyboard
    and is the size of a piano key - as wide as a white key, as tall as the keys.

    It paints nothing until the mouse is over it (or it is being dragged), when it lights up the key. It does not need
    any opacity to be grabbable: the key under it is solid. Dragging changes the width from that side; the other
    side stays where it is."""

    def __init__(self, window, side):
        super().__init__(window)
        self.owner = window
        self.side = side
        self._start = None                 # (cursor x, window geometry) when the drag began
        self._hover = False
        self.setCursor(Qt.SizeHorCursor)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._start = (e.globalPosition().toPoint().x(), self.owner.geometry())
            self.update()

    def mouseMoveEvent(self, e):
        if self._start is not None and (e.buttons() & Qt.LeftButton):
            self.owner.resize_from(self.side, self._start[1], e.globalPosition().toPoint().x() - self._start[0])

    def mouseReleaseEvent(self, _e):
        self._start = None
        self.update()

    def lit(self) -> bool:
        return self._hover or self._start is not None

    def paintEvent(self, _e):
        if not self.lit():
            return                                                            # invisible unless hovered / dragged
        p = QPainter(self)
        p.fillRect(self.rect().adjusted(0, 0, 0, -2), QColor(124, 108, 255, 110))     # the end key lights up
        x = 0 if self.side == Qt.LeftEdge else self.width() - 3                        # with a line on the outer edge
        p.fillRect(x, 0, 3, self.height() - 2, QColor(124, 108, 255, 240))
        p.end()


class PianoWindow(QWidget):
    """Own top-level window (so a screen recorder lists it as a window), no frame, transparent background.
    Drag the keys to move it (drop it on the main window to dock it), drag the end key on either side to
    resize it, double-click to dock it back. Only the keys react to the mouse: the particle area above them is
    click-through, so clicks go to whatever is behind the window (a particle can't be clicked)."""
    dockRequested = Signal()          # the user wants the piano back in the main window
    dragMoved = Signal(QPoint)        # while being dragged: where the cursor is (global)
    dragFinished = Signal(QPoint)     # the drag ended here (global): the main window decides whether that docks it
    effectsRequested = Signal()       # open the effects settings
    onTopChanged = Signal(bool)
    shownChanged = Signal(bool)

    def __init__(self):
        super().__init__(None)
        self.strip = None
        self.quitting = False
        self.on_top = True
        self._drag_from = None
        self._through = None                 # is the window currently click-through? (None = not applied yet)
        self._input_timer = QTimer(self)
        self._input_timer.setInterval(25)
        self._input_timer.timeout.connect(self._sync_input)
        self.setWindowTitle("Modern MIDI Player - Piano")
        self._apply_flags()
        self.setAttribute(Qt.WA_TranslucentBackground, True)          # must be set before the window is shown
        self.setStyleSheet("background: transparent;")                # (the app theme would otherwise paint it)
        self.setMouseTracking(True)
        self.setMinimumWidth(MIN_WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.grip_left = EdgeGrip(self, Qt.LeftEdge)
        self.grip_right = EdgeGrip(self, Qt.RightEdge)

    # ---------------------------------------------------------------- the strip
    def take(self, strip):
        """Move the piano strip into this window."""
        strip.setParent(None)
        self.strip = strip
        self.layout().addWidget(strip)
        strip.show()
        strip.heightChanged.connect(self._follow_height)
        self._follow_height(strip.height())
        self._place_grips()

    def release(self):
        """Hand the strip back (it is parentless afterwards)."""
        s = self.strip
        if s is not None:
            try:
                s.heightChanged.disconnect(self._follow_height)
            except (RuntimeError, TypeError):
                pass
            self.layout().removeWidget(s)
            s.setParent(None)
        self.strip = None
        return s

    def _follow_height(self, h: int):
        self.setFixedHeight(int(h))          # the window is exactly as tall as the keys + the particle area

    # ---------------------------------------------------------------- resizing from either side
    def _place_grips(self):
        """One on the first white key and one on the last: a key wide, and as tall as the keys (at the bottom)."""
        gw = max(GRIP_MIN, min(GRIP_MAX, round(self.width() / WHITE_KEYS)))
        top = max(0, self.height() - KEYS_H)
        h = min(KEYS_H, self.height())
        self.grip_left.setGeometry(0, top, gw, h)
        self.grip_right.setGeometry(self.width() - gw, top, gw, h)
        self.grip_left.raise_()                # (above the piano strip)
        self.grip_right.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_grips()

    def resize_from(self, side, start, dx: int):
        """Change the width from one side by *dx* px (measured from the geometry *start* the drag began with).
        The opposite side stays where it is; the window never gets narrower than MIN_WIDTH."""
        if side == Qt.RightEdge:
            w = max(MIN_WIDTH, min(MAX_WIDTH, start.width() + dx))
            x = start.x()
        else:
            w = max(MIN_WIDTH, min(MAX_WIDTH, start.width() - dx))
            x = start.x() + start.width() - w
        self.setGeometry(x, start.y(), w, self.height())

    # ---------------------------------------------------------------- flags
    def _apply_flags(self):
        flags = Qt.Window | Qt.FramelessWindowHint
        if self.on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def set_on_top(self, on: bool):
        if on == self.on_top:
            return
        self.on_top = on
        was = self.isVisible()
        self._apply_flags()
        if was:
            self.show()                       # changing the window flags hides the window
        self.onTopChanged.emit(on)

    # ---------------------------------------------------------------- click-through particle area
    def keys_top(self) -> int:
        """Where the keys start (window coordinates): everything above this is the particle area."""
        return self.strip._key_top() if self.strip is not None else 0

    def _sync_input(self):
        """Make the window ignore the mouse (clicks fall through to whatever is behind it) except over the keyboard.

        A transparent window already lets clicks through its fully transparent pixels, but not through a particle's
        pixels, so the particle area as a whole is made click-through; the keys (and the resize handles on them) stay
        grabbable. It is switched by watching the cursor, because a window that ignores the mouse gets no mouse
        events to react to. A press that started on the keys (a drag or resize) is never interrupted."""
        wh = self.windowHandle()
        if wh is None or not self.isVisible():
            return
        pos = self.mapFromGlobal(QCursor.pos())
        on_keys = self.rect().contains(pos) and pos.y() >= self.keys_top()
        grip_busy = any(g._start is not None for g in (self.grip_left, self.grip_right))
        held = bool(QApplication.mouseButtons() & Qt.LeftButton) and self._through is False
        through = not (on_keys or grip_busy or self._drag_from is not None or held)
        if through != self._through:
            self._through = through
            if not _native_click_through(int(self.winId()), through):
                wh.setFlag(Qt.WindowTransparentForInput, through)

    # ---------------------------------------------------------------- mouse: move, resize, dock
    # Moving is done by hand (not the system's move loop) so every mouse event arrives - including the release,
    # which is what lets a drag onto the main window dock the piano.
    def mouseMoveEvent(self, e):
        if self._drag_from is not None and (e.buttons() & Qt.LeftButton):
            g = e.globalPosition().toPoint()
            self.move(g - self._drag_from)
            self.dragMoved.emit(g)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self._drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseReleaseEvent(self, e):
        if self._drag_from is not None:
            self._drag_from = None
            self.dragFinished.emit(e.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.dockRequested.emit()

    def build_menu(self) -> QMenu:
        m = QMenu(self)
        a = QAction("Dock piano back in the window", m)
        a.triggered.connect(self.dockRequested.emit)
        m.addAction(a)
        a = QAction("Piano effects…", m)
        a.triggered.connect(self.effectsRequested.emit)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Always on top", m)
        a.setCheckable(True)
        a.setChecked(self.on_top)
        a.toggled.connect(self.set_on_top)
        m.addAction(a)
        return m

    def contextMenuEvent(self, e):
        self.build_menu().exec(e.globalPos())

    # ---------------------------------------------------------------- closing and visibility
    def closeEvent(self, e):
        if self.quitting:
            e.accept()
        else:
            e.ignore()                        # closing the piano window docks it instead of losing it
            self.dockRequested.emit()

    def showEvent(self, e):
        super().showEvent(e)
        self._through = None                  # (a freshly created native window starts out solid)
        self._input_timer.start()
        self._sync_input()
        self.shownChanged.emit(True)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._input_timer.stop()
        self.shownChanged.emit(False)


class DockTitle(QWidget):
    """Title bar of the docked piano: a name, and a button to pop it out into its own window."""
    popRequested = Signal()
    hideRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 4, 2)
        lbl = QLabel("Piano")
        lbl.setObjectName("dim")
        lay.addWidget(lbl)
        lay.addStretch(1)
        self.pop_btn = QPushButton("Pop out")
        self.pop_btn.setToolTip("Move the piano into its own transparent window (double-click this bar does the same)")
        self.pop_btn.setFocusPolicy(Qt.NoFocus)
        self.hide_btn = QPushButton("Hide")
        self.hide_btn.setToolTip("Hide the piano (View ▸ Show piano brings it back)")
        self.hide_btn.setFocusPolicy(Qt.NoFocus)
        for b in (self.pop_btn, self.hide_btn):
            b.setFixedHeight(22)
            lay.addWidget(b)
        self.pop_btn.clicked.connect(self.popRequested.emit)
        self.hide_btn.clicked.connect(self.hideRequested.emit)

    def mouseDoubleClickEvent(self, e):
        self.popRequested.emit()


class DockHint(QWidget):
    """A translucent band shown over the main window while the popped-out piano is dragged over it:
    letting go there docks the piano. It never takes the mouse, so the drag carries on underneath."""

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.text = "Release to dock the piano here"

    def set_text(self, text: str):
        if text != self.text:
            self.text = text
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        p.setBrush(QColor(124, 108, 255, 60))
        pen = QPen(QColor(124, 108, 255, 230))
        pen.setWidthF(2.5)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(r, 14, 14)
        p.setPen(QColor(255, 255, 255, 235))
        p.drawText(r, Qt.AlignCenter, self.text)
        p.end()
