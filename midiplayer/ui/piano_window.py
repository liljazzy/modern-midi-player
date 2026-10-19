"""The popped-out piano: a frameless, transparent window that shows nothing but the keyboard and its particles,
plus the little title bar the piano has while it is docked in the main window."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

EDGE = 7          # px at the left / right edge that resize the window
MIN_WIDTH = 320


class PianoWindow(QWidget):
    """Own top-level window (so a screen recorder lists it as a window), no frame, transparent background.
    Drag the keys to move it (drop it on the main window to dock it), drag its left / right edge to resize,
    double-click to dock it back."""
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
        self.setWindowTitle("Modern MIDI Player - Piano")
        self._apply_flags()
        self.setAttribute(Qt.WA_TranslucentBackground, True)          # must be set before the window is shown
        self.setStyleSheet("background: transparent;")                # (the app theme would otherwise paint it)
        self.setMouseTracking(True)
        self.setMinimumWidth(MIN_WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

    # ---------------------------------------------------------------- the strip
    def take(self, strip):
        """Move the piano strip into this window."""
        strip.setParent(None)
        self.strip = strip
        self.layout().addWidget(strip)
        strip.show()
        strip.heightChanged.connect(self._follow_height)
        self._follow_height(strip.height())

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

    # ---------------------------------------------------------------- mouse: move, resize, dock
    def _edge_at(self, pos: QPoint):
        if pos.x() < EDGE:
            return Qt.LeftEdge
        if pos.x() >= self.width() - EDGE:
            return Qt.RightEdge
        return None

    # Moving is done by hand (not the system's move loop) so every mouse event arrives - including the release,
    # which is what lets a drag onto the main window dock the piano.
    def mouseMoveEvent(self, e):
        if self._drag_from is not None and (e.buttons() & Qt.LeftButton):
            g = e.globalPosition().toPoint()
            self.move(g - self._drag_from)
            self.dragMoved.emit(g)
            return
        self.setCursor(Qt.SizeHorCursor if self._edge_at(e.position().toPoint()) is not None else Qt.ArrowCursor)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        edge = self._edge_at(e.position().toPoint())
        if edge is not None:                                                      # resizing: let the system do it
            handle = self.windowHandle()
            try:
                if handle is not None:
                    handle.startSystemResize(edge)
            except (AttributeError, TypeError):
                pass
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
        self.shownChanged.emit(True)

    def hideEvent(self, e):
        super().hideEvent(e)
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
