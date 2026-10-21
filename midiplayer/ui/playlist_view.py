"""Playlist panel: add files/folders, drag & drop, reorder, search,
shuffle / repeat, save / load M3U."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
                               QPushButton, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..playlist import (MIDI_EXTENSIONS, REPEAT_ALL, REPEAT_OFF, REPEAT_ONE, Playlist, PlaylistItem, is_midi_file,
                        scan_folder)
from ..song import Song
from . import theme
from .widgets import fmt_time

ROLE = Qt.UserRole + 1
MIDI_FILTER = "MIDI files (" + " ".join("*" + e for e in MIDI_EXTENSIONS) + ");;All files (*)"


class PlaylistTree(QTreeWidget):
    filesDropped = Signal(list, int)   # paths, insert index
    reordered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHeaderLabels(["#", "Title", "Time"])
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.MoveAction)
        h = self.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            paths = []
            for u in e.mimeData().urls():
                p = u.toLocalFile()
                if os.path.isdir(p):
                    paths.extend(scan_folder(p))
                elif is_midi_file(p):
                    paths.append(p)
            item = self.itemAt(e.position().toPoint())
            idx = self.indexOfTopLevelItem(item) if item else self.topLevelItemCount()
            e.acceptProposedAction()
            if paths:
                self.filesDropped.emit(paths, idx)
            return
        super().dropEvent(e)
        self.reordered.emit()


class PlaylistView(QWidget):
    playRequested = Signal(int)
    changed = Signal()

    def __init__(self, playlist: Playlist, parent=None):
        super().__init__(parent)
        self.pl = playlist
        self._last_dir = os.path.expanduser("~")
        self._exists: dict = {}                 # path -> (when checked, exists)
        self._built: list = []                  # the playlist items the rows were built from
        self._styled_current = -1               # the row currently styled as the playing one
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        t = QLabel("PLAYLIST")
        t.setStyleSheet("font-weight: 800; letter-spacing: 1px;")
        head.addWidget(t)
        head.addStretch(1)
        self.summary = QLabel("")
        self.summary.setObjectName("dim")
        head.addWidget(self.summary)
        lay.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        lay.addWidget(self.search)

        self.tree = PlaylistTree()
        self.tree.itemActivated.connect(self._on_double)
        self.tree.filesDropped.connect(self.add_paths)
        self.tree.reordered.connect(self._sync_from_tree)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        lay.addWidget(self.tree, 1)

        row = QHBoxLayout()
        row.setSpacing(4)

        def btn(text, tip, fn):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            row.addWidget(b)
            return b

        btn("＋ Files", "Add MIDI files", self.add_files_dialog)
        btn("＋ Folder", "Add a folder (recursive)", self.add_folder_dialog)
        btn("−", "Remove selected (Del)", self.remove_selected)
        row.addStretch(1)
        self.shuffle_btn = QPushButton("⤮")
        self.shuffle_btn.setCheckable(True)
        self.shuffle_btn.setToolTip("Shuffle")
        self.shuffle_btn.setFocusPolicy(Qt.NoFocus)
        self.shuffle_btn.setFixedWidth(34)
        self.shuffle_btn.toggled.connect(self._on_shuffle)
        row.addWidget(self.shuffle_btn)
        self.repeat_btn = QPushButton("↻")
        self.repeat_btn.setToolTip("Repeat: off")
        self.repeat_btn.setFocusPolicy(Qt.NoFocus)
        self.repeat_btn.setFixedWidth(40)
        self.repeat_btn.clicked.connect(self._cycle_repeat)
        row.addWidget(self.repeat_btn)
        more = QToolButton()
        more.setText("⋯")
        more.setPopupMode(QToolButton.InstantPopup)
        more.setFocusPolicy(Qt.NoFocus)
        m = QMenu(self)
        m.addAction("Load playlist…", self.load_dialog)
        m.addAction("Save playlist…", self.save_dialog)
        m.addSeparator()
        m.addAction("Sort by title", self._sort)
        m.addAction("Remove missing files", self._remove_missing)
        m.addAction("Clear playlist", self.clear)
        more.setMenu(m)
        row.addWidget(more)
        lay.addLayout(row)

        # lazily compute durations so adding big folders stays responsive
        self._dur_timer = QTimer(self)
        self._dur_timer.setInterval(15)
        self._dur_timer.timeout.connect(self._compute_some_durations)
        self.rebuild()

    # ------------------------------------------------------------ model
    def add_paths(self, paths, index=None):
        if not paths:
            return 0
        self._last_dir = os.path.dirname(paths[0])
        n = self.pl.add(list(paths), index)
        self.rebuild()
        self.changed.emit()
        return n

    def add_files_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add MIDI files", self._last_dir, MIDI_FILTER)
        self.add_paths(paths)

    def add_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder", self._last_dir)
        if d:
            self.add_paths(scan_folder(d))

    def remove_selected(self):
        idxs = [self.tree.indexOfTopLevelItem(it) for it in self.tree.selectedItems()]
        if idxs:
            self.pl.remove(idxs)
            self.rebuild()
            self.changed.emit()

    def clear(self):
        self.pl.clear()
        self.rebuild()
        self.changed.emit()

    def _sort(self):
        self.pl.sort_by_title()
        self.rebuild()
        self.changed.emit()

    def _remove_missing(self):
        idxs = [i for i, it in enumerate(self.pl.items) if not os.path.exists(it.path)]
        if idxs:
            self.pl.remove(idxs)
            self.rebuild()
            self.changed.emit()

    def load_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Load playlist", self._last_dir, "Playlists (*.m3u *.m3u8)")
        if p:
            self.pl.load_m3u(p)
            self.rebuild()
            self.changed.emit()

    def save_dialog(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save playlist", os.path.join(self._last_dir, "playlist.m3u"),
                                           "Playlists (*.m3u)")
        if p:
            if not p.lower().endswith((".m3u", ".m3u8")):
                p += ".m3u"
            self.pl.save_m3u(p)

    def _on_shuffle(self, on):
        self.pl.set_shuffle(on)
        self.changed.emit()

    def _cycle_repeat(self):
        self.pl.repeat = (self.pl.repeat + 1) % 3
        self._sync_buttons()
        self.changed.emit()

    def _sync_buttons(self):
        self.shuffle_btn.blockSignals(True)
        self.shuffle_btn.setChecked(self.pl.shuffle)
        self.shuffle_btn.blockSignals(False)
        txt, tip, on = {REPEAT_OFF: ("↻", "Repeat: off", False), REPEAT_ALL: ("↻ All", "Repeat: all", True),
                        REPEAT_ONE: ("↻ 1", "Repeat: one", True)}[self.pl.repeat]
        self.repeat_btn.setText(txt)
        self.repeat_btn.setToolTip(tip)
        self.repeat_btn.setFixedWidth(56 if on else 40)
        self.repeat_btn.setStyleSheet(f"color: {theme.ACCENT2}; border-color: {theme.ACCENT2};" if on else "")

    def _sync_from_tree(self):
        items = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i).data(0, ROLE)
            if isinstance(it, PlaylistItem):
                items.append(it)
        if len(items) != len(self.pl.items):
            self.rebuild()
            return
        cur = self.pl.current_item()
        self.pl.items = items
        self.pl.current = items.index(cur) if cur in items else -1
        self.pl._reshuffle()
        self.rebuild()
        self.changed.emit()

    # -------------------------------------------------------------- view
    def rebuild(self):
        self.tree.setUpdatesEnabled(False)               # (one repaint at the end, not one per row)
        try:
            self._rebuild_rows()
        finally:
            self.tree.setUpdatesEnabled(True)

    def _file_exists(self, path: str) -> bool:
        """os.path.exists, remembered for a few seconds: the list is rebuilt often and a stat per song adds up."""
        now = time.monotonic()
        hit = self._exists.get(path)
        if hit is not None and now - hit[0] < 8.0:
            return hit[1]
        ok = os.path.exists(path)
        if len(self._exists) > 20000:
            self._exists.clear()
        self._exists[path] = (now, ok)
        return ok

    def _style_row(self, w: QTreeWidgetItem, i: int, it: PlaylistItem, bold: QFont):
        """Number column + colours + font of one row: the current song is bold, accent coloured and marked with a play arrow,
        a missing file is red."""
        current = i == self.pl.current
        for c in range(3):
            w.setData(c, Qt.FontRole, None)
            w.setData(c, Qt.ForegroundRole, None)
        if not self._file_exists(it.path):
            for c in range(3):
                w.setForeground(c, QColor(theme.DANGER))
        if current:
            for c in range(3):
                w.setFont(c, bold)
                w.setForeground(c, QColor(theme.ACCENT2))
        w.setText(0, "▶" if current else str(i + 1))

    def _rebuild_rows(self):
        self.tree.clear()
        bold = QFont(self.tree.font())
        bold.setBold(True)
        align = Qt.AlignRight | Qt.AlignVCenter
        flags = None
        for i, it in enumerate(self.pl.items):
            w = QTreeWidgetItem([str(i + 1), it.title, fmt_time(it.duration) if it.duration else ""])
            w.setData(0, ROLE, it)
            w.setToolTip(1, it.path)
            w.setTextAlignment(2, align)
            if flags is None:
                flags = (w.flags() | Qt.ItemIsDragEnabled) & ~Qt.ItemIsDropEnabled
            w.setFlags(flags)
            if not self._file_exists(it.path):
                for c in range(3):
                    w.setForeground(c, QColor(theme.DANGER))
            if i == self.pl.current:
                for c in range(3):
                    w.setFont(c, bold)
                    w.setForeground(c, QColor(theme.ACCENT2))
                w.setText(0, "▶")
            self.tree.addTopLevelItem(w)
        self._built = list(self.pl.items)
        self._styled_current = self.pl.current
        self._apply_filter(self.search.text())
        self._sync_buttons()
        self._update_summary()
        if any(it.duration is None for it in self.pl.items):
            self._dur_timer.start()

    def highlight_current(self):
        """The song changed: restyle just the row that was current and the one that is now, instead of rebuilding the whole
        list (which with thousands of songs was a visible hitch at every song change)."""
        items = self.pl.items
        built = self._built
        if (len(built) == len(items) == self.tree.topLevelItemCount()
                and all(a is b for a, b in zip(built, items))):
            bold = QFont(self.tree.font())
            bold.setBold(True)
            for i in {self._styled_current, self.pl.current}:
                if 0 <= i < len(items):
                    w = self.tree.topLevelItem(i)
                    if w is not None:
                        self._style_row(w, i, items[i], bold)
            self._styled_current = self.pl.current
            if not self._dur_timer.isActive() and any(it.duration is None for it in items):
                self._dur_timer.start()                   # (as a rebuild would: some lengths are still to be worked out)
        else:
            self.rebuild()
        item = self.tree.topLevelItem(self.pl.current) if self.pl.current >= 0 else None
        if item:
            self.tree.scrollToItem(item)

    def _apply_filter(self, text: str):
        text = text.lower().strip()
        for i in range(self.tree.topLevelItemCount()):
            w = self.tree.topLevelItem(i)
            w.setHidden(bool(text) and text not in w.text(1).lower())

    def _update_summary(self):
        total = sum(it.duration or 0 for it in self.pl.items)
        n = len(self.pl.items)
        self.summary.setText(f"{n} song{'s' if n != 1 else ''} · {fmt_time(total)}" if n else "empty")

    def _compute_some_durations(self):
        done = 0
        deadline = time.perf_counter() + 0.008          # (as many as fit in ~8 ms, at least one: keeps the window responsive)
        for i, it in enumerate(self.pl.items):
            if it.duration is None:
                try:
                    it.duration = Song.load(it.path).duration()
                except Exception:
                    it.duration = 0.0
                w = self.tree.topLevelItem(i)
                if w is not None:
                    w.setText(2, fmt_time(it.duration) if it.duration else "—")
                done += 1
                if done >= 3 or time.perf_counter() >= deadline:
                    break
        if not done:
            self._dur_timer.stop()
            self._update_summary()
            self.changed.emit()

    def _on_double(self, item, _col):
        self.playRequested.emit(self.tree.indexOfTopLevelItem(item))

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        m = QMenu(self)
        if item is not None:
            idx = self.tree.indexOfTopLevelItem(item)
            m.addAction("Play", lambda: self.playRequested.emit(idx))
            m.addAction("Remove", self.remove_selected)
            m.addAction("Open containing folder", lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(os.path.dirname(self.pl.items[idx].path))))
            m.addSeparator()
        m.addAction("Add files…", self.add_files_dialog)
        m.addAction("Add folder…", self.add_folder_dialog)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.tree.hasFocus():
            self.remove_selected()
        else:
            super().keyPressEvent(e)
