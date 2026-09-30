"""Editor tab: piano-roll note editor with velocity lane.

Tools: Select (move/resize/rubber-band), Draw, Erase.
Keys:  Del delete · Ctrl+A select all · ↑/↓ transpose (Shift = octave) ·
       ←/→ move by grid · Ctrl+C/X/V clipboard · Ctrl+D duplicate ·
       Ctrl+Z/Y undo/redo · Q quantize · 1/2/3 tools · Ctrl+wheel zoom.
"""
from __future__ import annotations

import bisect
import math
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter, QPen, QPolygon
from PySide6.QtWidgets import (QAbstractScrollArea, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout,
                               QInputDialog, QLabel, QMessageBox, QPushButton, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)

from ..gm import BLACK_KEYS, CHANNEL_COLORS, DRUM_CHANNEL, GM_PROGRAMS, note_name
from ..song import Note, Song, UndoStack
from . import theme
from .controller import Controller

KEY_W = 64
RULER_H = 24
TOOL_SELECT, TOOL_DRAW, TOOL_ERASE = 0, 1, 2

SNAPS = [("Bar", "bar"), ("1/2", 2), ("1/4", 4), ("1/8", 8), ("1/16", 16), ("1/32", 32),
         ("1/8 T", 12), ("1/16 T", 24), ("Off", 0)]


class PianoRoll(QAbstractScrollArea):
    edited = Signal()
    selectionChanged = Signal()
    zoomChanged = Signal()

    def __init__(self, ctl: Controller, undo: UndoStack, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.undo = undo
        self.song: Optional[Song] = None
        self.zx = 0.12          # pixels per tick
        self.kh = 14            # pixels per key
        self.tool = TOOL_SELECT
        self.snap_div = 16
        self.track_filter = -1  # -1 = all tracks
        self.insert_track = 0
        self.insert_channel = 0
        self.default_velocity = 100
        self.default_len = 0
        self.follow = True
        self.selection: Set[Note] = set()
        self.clipboard: List[Tuple[int, int, int, int, int, int]] = []
        self._cache: List[Tuple[List[int], List[Note], int]] = []
        self._mode: Optional[str] = None
        self._press_tick = 0
        self._press_pitch = 0
        self._orig: Dict[Note, Tuple[int, int, int]] = {}
        self._undo_pushed = False
        self._changed = False
        self._rubber: Optional[QRect] = None
        self._rubber_base: Set[Note] = set()
        self._last_preview = -1
        self._playhead_tick = 0.0
        self._hover_pitch = -1
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.horizontalScrollBar().valueChanged.connect(lambda *_: self._scrolled())
        self.verticalScrollBar().valueChanged.connect(lambda *_: self._scrolled())
        self.setMinimumHeight(200)
        self.setFrameShape(QAbstractScrollArea.NoFrame)

    # ------------------------------------------------------------ setup
    def set_song(self, song: Optional[Song]):
        self.song = song
        self.selection.clear()
        self.rebuild_cache()
        self._update_scrollbars()
        if song is not None:
            self.default_len = song.ticks_per_beat
            self.center_on_notes()
        self.viewport().update()

    def rebuild_cache(self):
        self._cache = []
        if not self.song:
            return
        for tr in self.song.tracks:
            tr.notes.sort(key=lambda n: (n.start, n.pitch))
            starts = [n.start for n in tr.notes]
            mx = max((n.end - n.start for n in tr.notes), default=0)
            self._cache.append((starts, tr.notes, mx))

    def center_on_notes(self):
        if not self.song:
            return
        pitches = [n.pitch for tr in self.song.tracks for n in tr.notes]
        mid = (sorted(pitches)[len(pitches) // 2]) if pitches else 60
        vis_h = self.viewport().height() - RULER_H
        self.verticalScrollBar().setValue(int((127 - mid) * self.kh - vis_h / 2))
        self.horizontalScrollBar().setValue(0)

    # --------------------------------------------------------- geometry
    @property
    def tpb(self) -> int:
        return self.song.ticks_per_beat if self.song else 480

    def snap_ticks(self) -> int:
        if self.snap_div == "bar":
            num, den = self.song.time_signature() if self.song else (4, 4)
            return int(self.tpb * 4 * num / den)
        if not self.snap_div:
            return 1
        return max(1, int(round(self.tpb * 4 / self.snap_div)))

    def snap(self, tick: float) -> int:
        s = self.snap_ticks()
        return int(math.floor(tick / s) * s) if s > 1 else int(tick)

    def snap_round(self, tick: float) -> int:
        s = self.snap_ticks()
        return int(round(tick / s) * s) if s > 1 else int(round(tick))

    def x_of(self, tick: float) -> float:
        return KEY_W + tick * self.zx - self.horizontalScrollBar().value()

    def tick_of(self, x: float) -> float:
        return max(0.0, (x - KEY_W + self.horizontalScrollBar().value()) / self.zx)

    def y_of(self, pitch: int) -> float:
        return RULER_H + (127 - pitch) * self.kh - self.verticalScrollBar().value()

    def pitch_of(self, y: float) -> int:
        return max(0, min(127, 127 - int(math.floor((y - RULER_H + self.verticalScrollBar().value()) / self.kh))))

    def content_ticks(self) -> int:
        end = self.song.end_tick() if self.song else 0
        num, den = self.song.time_signature() if self.song else (4, 4)
        bar = int(self.tpb * 4 * num / den)
        return end + bar * 16

    def _update_scrollbars(self):
        vw = self.viewport().width() - KEY_W
        vh = self.viewport().height() - RULER_H
        cw = int(self.content_ticks() * self.zx)
        ch = 128 * self.kh
        hs, vs = self.horizontalScrollBar(), self.verticalScrollBar()
        hs.setRange(0, max(0, cw - vw))
        hs.setPageStep(max(1, vw))
        hs.setSingleStep(20)
        vs.setRange(0, max(0, ch - vh))
        vs.setPageStep(max(1, vh))
        vs.setSingleStep(self.kh)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_scrollbars()

    def _scrolled(self):
        self.viewport().update()
        self.zoomChanged.emit()

    def set_zoom(self, zx: float, anchor_x: Optional[float] = None):
        zx = max(0.005, min(2.0, zx))
        if anchor_x is None:
            anchor_x = KEY_W + (self.viewport().width() - KEY_W) / 2
        tick = self.tick_of(anchor_x)
        self.zx = zx
        self._update_scrollbars()
        self.horizontalScrollBar().setValue(int(tick * zx - (anchor_x - KEY_W)))
        self.viewport().update()
        self.zoomChanged.emit()

    def set_key_height(self, kh: int, anchor_y: Optional[float] = None):
        kh = max(6, min(32, kh))
        if anchor_y is None:
            anchor_y = RULER_H + (self.viewport().height() - RULER_H) / 2
        pitch_f = 127 - (anchor_y - RULER_H + self.verticalScrollBar().value()) / self.kh
        self.kh = kh
        self._update_scrollbars()
        self.verticalScrollBar().setValue(int((127 - pitch_f) * kh - (anchor_y - RULER_H)))
        self.viewport().update()

    # ---------------------------------------------------------- queries
    def editable_tracks(self) -> List[int]:
        if not self.song:
            return []
        if self.track_filter < 0:
            return list(range(len(self.song.tracks)))
        return [self.track_filter] if self.track_filter < len(self.song.tracks) else []

    def visible_notes(self, t0: float, t1: float, tracks: Optional[List[int]] = None):
        """Yield (track_index, note) overlapping [t0, t1]."""
        if not self.song:
            return
        idxs = range(len(self._cache)) if tracks is None else tracks
        for ti in idxs:
            if ti >= len(self._cache):
                continue
            starts, notes, mx = self._cache[ti]
            lo = bisect.bisect_left(starts, t0 - mx)
            hi = bisect.bisect_right(starts, t1)
            for n in notes[lo:hi]:
                if n.end >= t0:
                    yield ti, n

    def note_at(self, pos: QPoint) -> Tuple[int, Optional[Note]]:
        if not self.song or pos.x() < KEY_W or pos.y() < RULER_H:
            return -1, None
        t = self.tick_of(pos.x())
        p = self.pitch_of(pos.y())
        tol = 3 / self.zx
        best = (-1, None)
        for ti, n in self.visible_notes(t - tol, t + tol, self.editable_tracks()):
            if n.pitch == p and n.start - tol * 0.3 <= t <= n.end + tol * 0.3:
                best = (ti, n)   # later (on top) wins
        return best

    def track_of(self, note: Note) -> int:
        for ti, tr in enumerate(self.song.tracks):
            starts, notes, _ = self._cache[ti] if ti < len(self._cache) else ([], tr.notes, 0)
            i = bisect.bisect_left(starts, note.start) if starts else 0
            for j in range(i, len(notes)):
                if notes[j] is note:
                    return ti
                if notes[j].start > note.start:
                    break
            if note in tr.notes:
                return ti
        return -1

    # ---------------------------------------------------------- editing
    def _begin_edit(self):
        if not self._undo_pushed:
            self.undo.push()
            self._undo_pushed = True

    def _commit(self):
        self.rebuild_cache()
        self._update_scrollbars()
        self.viewport().update()
        self.edited.emit()

    def _new_note(self, tick: int, pitch: int) -> Note:
        ti = self.insert_track
        if self.track_filter >= 0:
            ti = self.track_filter
        ti = max(0, min(ti, len(self.song.tracks) - 1))
        length = self.default_len or self.snap_ticks()
        n = Note(tick, tick + max(1, length), pitch, self.default_velocity, self.insert_channel)
        self.song.tracks[ti].notes.append(n)
        self.rebuild_cache()
        return n

    def delete_notes(self, notes):
        notes = set(notes)
        if not notes or not self.song:
            return
        self._undo_pushed = False
        self._begin_edit()
        for tr in self.song.tracks:
            tr.notes = [n for n in tr.notes if n not in notes]
        self.selection -= notes
        self._undo_pushed = False
        self._commit()
        self.selectionChanged.emit()

    def delete_selection(self):
        self.delete_notes(self.selection)

    def select_all(self):
        if not self.song:
            return
        self.selection = {n for ti in self.editable_tracks() for n in self.song.tracks[ti].notes}
        self.viewport().update()
        self.selectionChanged.emit()

    def clear_selection(self):
        self.selection.clear()
        self.viewport().update()
        self.selectionChanged.emit()

    def transform_selection(self, dt: int = 0, dp: int = 0):
        if not self.selection:
            return
        if dp:
            if any(not 0 <= n.pitch + dp <= 127 for n in self.selection):
                return
        if dt < 0 and any(n.start + dt < 0 for n in self.selection):
            dt = -min(n.start for n in self.selection)
            if not dt:
                return
        self._undo_pushed = False
        self._begin_edit()
        for n in self.selection:
            n.start += dt
            n.end += dt
            n.pitch += dp
        self._undo_pushed = False
        if dp and len(self.selection) <= 8:
            n = next(iter(self.selection))
            self.preview(n.pitch, n.channel)
        self._commit()

    def set_selection_velocity(self, v: int):
        if not self.selection:
            return
        self._undo_pushed = False
        self._begin_edit()
        for n in self.selection:
            n.velocity = max(1, min(127, v))
        self._undo_pushed = False
        self._commit()

    def set_selection_channel(self, ch: int):
        if not self.selection:
            return
        self._undo_pushed = False
        self._begin_edit()
        for n in self.selection:
            n.channel = ch
        self._undo_pushed = False
        self._commit()

    def quantize(self, ends_too: bool = True):
        if not self.song:
            return
        notes = self.selection or {n for ti in self.editable_tracks() for n in self.song.tracks[ti].notes}
        if not notes:
            return
        s = self.snap_ticks()
        self._undo_pushed = False
        self._begin_edit()
        for n in notes:
            length = n.end - n.start
            n.start = int(round(n.start / s) * s)
            if ends_too:
                n.end = max(n.start + s, int(round((n.start + length) / s) * s))
            else:
                n.end = n.start + length
        self._undo_pushed = False
        self._commit()

    def copy(self):
        if not self.selection:
            return
        base = min(n.start for n in self.selection)
        self.clipboard = [(self.track_of(n), n.start - base, n.end - n.start, n.pitch, n.velocity, n.channel)
                          for n in sorted(self.selection, key=lambda n: n.start)]

    def cut(self):
        self.copy()
        self.delete_selection()

    def paste(self, at_tick: Optional[int] = None):
        if not self.clipboard or not self.song:
            return
        if at_tick is None:
            at_tick = self.snap(self._playhead_tick)
        self._undo_pushed = False
        self._begin_edit()
        new = set()
        for ti, rel, length, pitch, vel, ch in self.clipboard:
            if self.track_filter >= 0 or not (0 <= ti < len(self.song.tracks)):
                ti = self.track_filter if self.track_filter >= 0 else max(0, min(self.insert_track, len(self.song.tracks) - 1))
            n = Note(at_tick + rel, at_tick + rel + length, pitch, vel, ch)
            self.song.tracks[ti].notes.append(n)
            new.add(n)
        self._undo_pushed = False
        self.selection = new
        self._commit()
        self.selectionChanged.emit()

    def duplicate(self):
        if not self.selection:
            return
        start = min(n.start for n in self.selection)
        end = max(n.end for n in self.selection)
        s = self.snap_ticks()
        span = max(s, int(math.ceil((end - start) / s) * s))
        self.copy()
        self.paste(start + span)

    def preview(self, pitch: int, ch: Optional[int] = None):
        if pitch == self._last_preview:
            return
        self._last_preview = pitch
        self.ctl.engine.preview_note(self.insert_channel if ch is None else ch, pitch, self.default_velocity)

    # ------------------------------------------------------------ mouse
    def mousePressEvent(self, e):
        if not self.song:
            return
        self.setFocus()
        pos = e.position().toPoint()
        self._last_preview = -1
        self._undo_pushed = False
        self._changed = False
        mods = e.modifiers()
        if pos.y() < RULER_H and pos.x() >= KEY_W:
            self._mode = "seek"
            self._seek_to(pos.x())
            return
        if pos.x() < KEY_W:
            self._mode = "keys"
            self.preview(self.pitch_of(pos.y()))
            return
        ti, note = self.note_at(pos)
        if e.button() == Qt.RightButton:
            if note is not None:
                self.delete_notes([note])
            return
        if e.button() != Qt.LeftButton:
            return
        self._press_tick = self.tick_of(pos.x())
        self._press_pitch = self.pitch_of(pos.y())
        if self.tool == TOOL_ERASE:
            self._mode = "erase"
            if note is not None:
                self._begin_edit()
                self._erase(note)
            return
        if note is not None:
            if mods & (Qt.ShiftModifier | Qt.ControlModifier):
                if note in self.selection:
                    self.selection.discard(note)
                else:
                    self.selection.add(note)
                self.viewport().update()
                self.selectionChanged.emit()
                self._mode = None
                return
            if note not in self.selection:
                self.selection = {note}
                self.selectionChanged.emit()
            if abs(self.x_of(note.end) - pos.x()) <= 6 and self.x_of(note.end) - self.x_of(note.start) > 10:
                self._mode = "resize"
            else:
                self._mode = "move"
            if mods & Qt.AltModifier and self._mode == "move":
                # Alt-drag = copy
                self._begin_edit()
                copies = set()
                for n in self.selection:
                    c = n.copy()
                    self.song.tracks[max(0, self.track_of(n))].notes.append(c)
                    copies.add(c)
                self.selection = copies
                self.rebuild_cache()
                self._changed = True
            self._orig = {n: (n.start, n.end, n.pitch) for n in self.selection}
            self.preview(note.pitch, note.channel)
            self.viewport().update()
            return
        # empty space
        if self.tool == TOOL_DRAW:
            self._begin_edit()
            n = self._new_note(self.snap(self._press_tick), self._press_pitch)
            self.selection = {n}
            self._orig = {n: (n.start, n.end, n.pitch)}
            self._press_tick = n.end   # dragging extends from the note's end
            self._mode = "resize"
            self._changed = True
            self.preview(n.pitch, n.channel)
            self.selectionChanged.emit()
            self.viewport().update()
            return
        self._mode = "rubber"
        self._rubber_base = set(self.selection) if mods & (Qt.ShiftModifier | Qt.ControlModifier) else set()
        if not self._rubber_base:
            self.selection.clear()
            self.selectionChanged.emit()
        self._rubber = QRect(pos, pos)
        self.viewport().update()

    def mouseDoubleClickEvent(self, e):
        if not self.song or self.tool != TOOL_SELECT:
            return super().mouseDoubleClickEvent(e)
        pos = e.position().toPoint()
        if pos.x() < KEY_W or pos.y() < RULER_H:
            return
        ti, note = self.note_at(pos)
        if note is None:
            self._undo_pushed = False
            self._begin_edit()
            n = self._new_note(self.snap(self.tick_of(pos.x())), self.pitch_of(pos.y()))
            self.selection = {n}
            self.preview(n.pitch, n.channel)
            self._undo_pushed = False
            self._commit()
            self.selectionChanged.emit()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if not self.song:
            return
        hp = self.pitch_of(pos.y()) if pos.y() >= RULER_H else -1
        if hp != self._hover_pitch:
            self._hover_pitch = hp
            self.viewport().update(QRect(0, RULER_H, KEY_W, self.viewport().height()))
        mode = self._mode
        if mode is None:
            self._update_cursor(pos)
            return
        if mode == "seek":
            self._seek_to(pos.x())
        elif mode == "keys":
            if e.buttons() & Qt.LeftButton:
                self.preview(self.pitch_of(pos.y()))
        elif mode == "erase":
            _, note = self.note_at(pos)
            if note is not None:
                self._begin_edit()
                self._erase(note)
        elif mode == "move":
            dt = self.snap_round(self.tick_of(pos.x()) - self._press_tick)
            dp = self.pitch_of(pos.y()) - self._press_pitch
            if self._orig:
                min_start = min(o[0] for o in self._orig.values())
                dt = max(dt, -min_start)
                lo = min(o[2] for o in self._orig.values())
                hi = max(o[2] for o in self._orig.values())
                dp = max(-lo, min(127 - hi, dp))
            changed = any((n.start, n.pitch) != (o[0] + dt, o[2] + dp) for n, o in self._orig.items())
            if changed:
                self._begin_edit()
                self._changed = True
                for n, (s, en, p) in self._orig.items():
                    n.start, n.end, n.pitch = s + dt, en + dt, p + dp
                if len(self._orig) <= 8:
                    first = next(iter(self._orig))
                    self.preview(first.pitch, first.channel)
                self.viewport().update()
        elif mode == "resize":
            dt = self.snap_round(self.tick_of(pos.x()) - self._press_tick)
            minlen = max(1, min(self.snap_ticks(), self.tpb // 8) if self.snap_div else 1)
            changed = False
            for n, (s, en, p) in self._orig.items():
                new_end = max(s + minlen, en + dt)
                if new_end != n.end:
                    changed = True
            if changed:
                self._begin_edit()
                self._changed = True
                for n, (s, en, p) in self._orig.items():
                    n.end = max(s + minlen, en + dt)
                self.viewport().update()
        elif mode == "rubber" and self._rubber is not None:
            self._rubber.setBottomRight(pos)
            r = self._rubber.normalized()
            t0, t1 = self.tick_of(r.left()), self.tick_of(r.right())
            p_hi, p_lo = self.pitch_of(r.top()), self.pitch_of(r.bottom())
            sel = set(self._rubber_base)
            for ti, n in self.visible_notes(t0, t1, self.editable_tracks()):
                if p_lo <= n.pitch <= p_hi and n.end >= t0 and n.start <= t1:
                    sel.add(n)
            self.selection = sel
            self.viewport().update()
        if mode in ("move", "resize", "rubber"):
            self._autoscroll(pos)

    def mouseReleaseEvent(self, e):
        mode = self._mode
        self._mode = None
        self._last_preview = -1
        if mode in ("move", "resize", "erase") and self._changed:
            if mode == "resize" and len(self.selection) == 1:
                n = next(iter(self.selection))
                self.default_len = n.end - n.start
            self._changed = False
            self._undo_pushed = False
            self._commit()
        elif mode == "rubber":
            self._rubber = None
            self.selectionChanged.emit()
            self.viewport().update()
        self._undo_pushed = False
        self._update_cursor(e.position().toPoint())

    def _erase(self, note: Note):
        for tr in self.song.tracks:
            if note in tr.notes:
                tr.notes.remove(note)
                break
        self.selection.discard(note)
        self._changed = True
        self.rebuild_cache()
        self.viewport().update()

    def _seek_to(self, x: float):
        tick = self.tick_of(x)
        self.ctl.seek(self.song.tempo_map.tick_to_sec(tick))
        self.set_playhead_tick(tick)

    def _autoscroll(self, pos: QPoint):
        vp = self.viewport()
        hs, vs = self.horizontalScrollBar(), self.verticalScrollBar()
        if pos.x() > vp.width() - 10:
            hs.setValue(hs.value() + 20)
        elif pos.x() < KEY_W + 10:
            hs.setValue(hs.value() - 20)
        if pos.y() > vp.height() - 10:
            vs.setValue(vs.value() + self.kh)
        elif pos.y() < RULER_H + 10:
            vs.setValue(vs.value() - self.kh)

    def _update_cursor(self, pos: QPoint):
        if pos.x() < KEY_W:
            self.viewport().setCursor(Qt.PointingHandCursor)
            return
        if pos.y() < RULER_H:
            self.viewport().setCursor(Qt.IBeamCursor)
            return
        if self.tool == TOOL_ERASE:
            self.viewport().setCursor(Qt.ForbiddenCursor)
            return
        _, note = self.note_at(pos)
        if note is not None:
            if abs(self.x_of(note.end) - pos.x()) <= 6 and self.x_of(note.end) - self.x_of(note.start) > 10:
                self.viewport().setCursor(Qt.SizeHorCursor)
            else:
                self.viewport().setCursor(Qt.SizeAllCursor)
        elif self.tool == TOOL_DRAW:
            self.viewport().setCursor(Qt.CrossCursor)
        else:
            self.viewport().setCursor(Qt.ArrowCursor)

    def wheelEvent(self, e):
        d = e.angleDelta()
        dy = d.y() or d.x()
        mods = e.modifiers()
        pos = e.position()
        if mods & Qt.ControlModifier:
            self.set_zoom(self.zx * (1.15 if dy > 0 else 1 / 1.15), pos.x())
        elif mods & Qt.AltModifier:
            self.set_key_height(self.kh + (1 if dy > 0 else -1), pos.y())
        elif mods & Qt.ShiftModifier or d.x():
            hs = self.horizontalScrollBar()
            hs.setValue(hs.value() - (dy if not d.x() else d.x()))
        else:
            vs = self.verticalScrollBar()
            vs.setValue(vs.value() - dy // 2)
        e.accept()

    def keyPressEvent(self, e):
        k = e.key()
        mods = e.modifiers()
        shift = bool(mods & Qt.ShiftModifier)
        if k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selection()
        elif k == Qt.Key_Up:
            self.transform_selection(dp=12 if shift else 1)
        elif k == Qt.Key_Down:
            self.transform_selection(dp=-12 if shift else -1)
        elif k == Qt.Key_Right:
            self.transform_selection(dt=self.snap_ticks())
        elif k == Qt.Key_Left:
            self.transform_selection(dt=-self.snap_ticks())
        elif k == Qt.Key_Escape:
            self.clear_selection()
        else:
            super().keyPressEvent(e)

    # ---------------------------------------------------------- playhead
    def set_playhead_tick(self, tick: float):
        old_x = self.x_of(self._playhead_tick)
        self._playhead_tick = tick
        new_x = self.x_of(tick)
        vp = self.viewport()
        if self.follow and self.ctl.engine.is_playing() and self._mode is None:
            if new_x > vp.width() - 40 or new_x < KEY_W:
                hs = self.horizontalScrollBar()
                hs.setValue(int(tick * self.zx - 40))
                return
        if int(old_x) != int(new_x):
            vp.update(QRect(int(min(old_x, new_x)) - 2, 0, int(abs(new_x - old_x)) + 5, vp.height()))
            vp.update(QRect(0, RULER_H, KEY_W, vp.height()))   # sounding keys

    # ------------------------------------------------------------ paint
    def paintEvent(self, e):
        vp = self.viewport()
        p = QPainter(vp)
        W, H = vp.width(), vp.height()
        p.fillRect(0, 0, W, H, QColor(theme.BG))
        if not self.song:
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(vp.rect(), Qt.AlignCenter, "Open a MIDI file or create a new song (File ▸ New) to start editing")
            return
        # --- key rows
        top_pitch = self.pitch_of(RULER_H)
        bot_pitch = self.pitch_of(H)
        for pitch in range(bot_pitch, top_pitch + 1):
            y = self.y_of(pitch)
            col = QColor("#171a21") if pitch % 12 in BLACK_KEYS else QColor("#1d2029")
            p.fillRect(QRectF(KEY_W, y, W - KEY_W, self.kh), col)
            if pitch % 12 == 0:
                p.fillRect(QRectF(KEY_W, y + self.kh - 1, W - KEY_W, 1), QColor("#2e3342"))
        # --- grid
        t0, t1 = self.tick_of(KEY_W), self.tick_of(W)
        num, den = self.song.time_signature()
        beat = self.tpb * 4 / den
        bar = beat * num
        sub = self.snap_ticks()
        if sub * self.zx >= 6 and sub < beat:
            k = int(t0 // sub)
            while k * sub <= t1:
                x = self.x_of(k * sub)
                p.fillRect(QRectF(x, RULER_H, 1, H), QColor("#1f232d"))
                k += 1
        if beat * self.zx >= 5:
            k = int(t0 // beat)
            while k * beat <= t1:
                x = self.x_of(k * beat)
                p.fillRect(QRectF(x, RULER_H, 1, H), QColor("#2a2f3c"))
                k += 1
        bar_step = 1
        while bar * bar_step * self.zx < 40:
            bar_step *= 2
        k = int(t0 // bar)
        k -= k % bar_step
        while k * bar <= t1:
            x = self.x_of(k * bar)
            p.fillRect(QRectF(x, RULER_H, 1, H), QColor("#3a4152"))
            k += bar_step
        # --- notes
        editable = set(self.editable_tracks())
        font = QFont(p.font())
        font.setPointSize(7)
        p.setFont(font)
        show_labels = self.kh >= 11
        for ti, n in self.visible_notes(t0, t1):
            x0, x1 = self.x_of(n.start), self.x_of(n.end)
            y = self.y_of(n.pitch)
            if y + self.kh < RULER_H or y > H:
                continue
            r = QRectF(x0, y + 1, max(3.0, x1 - x0 - 1), self.kh - 2)
            col = QColor(CHANNEL_COLORS[n.channel])
            if ti not in editable:
                col.setAlpha(55)
                p.setPen(Qt.NoPen)
            else:
                col = col.darker(100 + int((127 - n.velocity) * 0.8))
                sel = n in self.selection
                p.setPen(QPen(QColor("white"), 1.5) if sel else QPen(col.darker(160), 1))
            p.setBrush(col)
            p.drawRoundedRect(r, 2.5, 2.5)
            if show_labels and ti in editable and r.width() > 26:
                p.setPen(QColor(20, 20, 24, 200))
                p.drawText(r.adjusted(3, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, note_name(n.pitch))
        # --- rubber band
        if self._rubber is not None:
            p.setPen(QPen(QColor(theme.ACCENT), 1, Qt.DashLine))
            c = QColor(theme.ACCENT)
            c.setAlpha(40)
            p.setBrush(c)
            p.drawRect(self._rubber.normalized())
        # --- playhead
        px = self.x_of(self._playhead_tick)
        if KEY_W <= px <= W:
            p.fillRect(QRectF(px, 0, 2, H), QColor(theme.ACCENT2))
        # --- ruler
        p.fillRect(0, 0, W, RULER_H, QColor(theme.BG2))
        p.fillRect(0, RULER_H - 1, W, 1, QColor(theme.BORDER))
        p.setPen(QColor(theme.TEXT_DIM))
        f2 = QFont(p.font())
        f2.setPointSize(8)
        p.setFont(f2)
        k = int(t0 // bar)
        k -= k % bar_step
        while k * bar <= t1:
            x = self.x_of(k * bar)
            if x >= KEY_W:
                p.fillRect(QRectF(x, RULER_H - 8, 1, 8), QColor(theme.TEXT_DIM))
                p.drawText(QRectF(x + 3, 2, 60, RULER_H - 4), Qt.AlignLeft | Qt.AlignVCenter, str(k + 1))
            k += bar_step
        if KEY_W <= px <= W:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.ACCENT2))
            p.drawPolygon(QPolygon([QPoint(int(px) - 5, 2), QPoint(int(px) + 6, 2), QPoint(int(px) + 1, 9)]))
        # --- keyboard
        sounding = self._sounding_pitches()
        p.fillRect(0, RULER_H, KEY_W, H, QColor("#e9ebf0"))
        p.setFont(f2)
        for pitch in range(bot_pitch, top_pitch + 1):
            y = self.y_of(pitch)
            r = QRectF(0, y, KEY_W, self.kh)
            black = pitch % 12 in BLACK_KEYS
            if pitch in sounding:
                p.fillRect(r, QColor(theme.ACCENT2))
            elif pitch == self._hover_pitch:
                p.fillRect(r, QColor("#b8b0ff") if not black else QColor("#5a52a8"))
            elif black:
                p.fillRect(QRectF(0, y, KEY_W * 0.62, self.kh), QColor("#23262e"))
            p.fillRect(QRectF(0, y + self.kh - 1, KEY_W, 1), QColor("#c3c7d0"))
            if pitch % 12 == 0 and self.kh >= 8:
                p.setPen(QColor("#50566a"))
                p.drawText(QRectF(0, y, KEY_W - 4, self.kh), Qt.AlignRight | Qt.AlignVCenter, note_name(pitch))
        p.fillRect(QRectF(KEY_W - 1, RULER_H, 1, H), QColor(theme.BORDER))
        p.fillRect(0, 0, KEY_W, RULER_H, QColor(theme.BG2))

    def _sounding_pitches(self) -> Set[int]:
        eng = self.ctl.engine
        if not eng.is_playing() or not self.song:
            return set()
        chans = set()
        for ti in self.editable_tracks():
            chans.update(self.song.tracks[ti].channels())
        out = set()
        with eng.lock:
            for ch in chans:
                if ch == DRUM_CHANNEL and self.track_filter < 0:
                    continue
                out.update(eng.channels[ch].active.keys())
        return out


class VelocityLane(QWidget):
    edited = Signal()

    def __init__(self, roll: PianoRoll, parent=None):
        super().__init__(parent)
        self.roll = roll
        self.setFixedHeight(90)
        self._target: List[Note] = []
        self._pushed = False
        self._changed = False
        roll.zoomChanged.connect(self.update)
        roll.selectionChanged.connect(self.update)
        roll.edited.connect(self.update)

    def _notes_in_view(self):
        r = self.roll
        return list(r.visible_notes(r.tick_of(KEY_W), r.tick_of(self.width()), r.editable_tracks()))

    def paintEvent(self, _):
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor(theme.BG2))
        p.fillRect(0, 0, W, 1, QColor(theme.BORDER))
        p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(QRectF(0, 0, KEY_W - 6, H), Qt.AlignRight | Qt.AlignVCenter, "VELOCITY")
        p.fillRect(QRectF(KEY_W - 1, 0, 1, H), QColor(theme.BORDER))
        if not self.roll.song:
            return
        top, bot = 8, H - 4
        for frac in (0.25, 0.5, 0.75):
            y = bot - (bot - top) * frac
            p.fillRect(QRectF(KEY_W, y, W - KEY_W, 1), QColor("#252a35"))
        sel = self.roll.selection
        notes = self._notes_in_view()
        notes.sort(key=lambda tn: tn[1] in sel)  # selected drawn on top
        for _, n in notes:
            x = self.roll.x_of(n.start)
            if x < KEY_W:
                continue
            h = (bot - top) * n.velocity / 127
            col = QColor(CHANNEL_COLORS[n.channel])
            if n in sel:
                col = QColor("white")
            elif sel:
                col.setAlpha(120)
            p.fillRect(QRectF(x, bot - h, 3, h), col)
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(x - 2, bot - h - 3, 7, 7))

    def _vel_at(self, y: float) -> int:
        top, bot = 8, self.height() - 4
        return max(1, min(127, int(round((bot - y) / (bot - top) * 127))))

    def _pick(self, x: float) -> List[Note]:
        best, bd = None, 6.0
        sel = self.roll.selection
        for _, n in self._notes_in_view():
            d = abs(self.roll.x_of(n.start) + 1 - x)
            if d < bd or (d == bd and n in sel):
                best, bd = n, d
        if best is None:
            return []
        if best in sel:
            return list(sel)
        # all notes starting at the same tick (chords) in view
        return [best]

    def mousePressEvent(self, e):
        if not self.roll.song or e.button() != Qt.LeftButton:
            return
        pos = e.position()
        self._target = self._pick(pos.x())
        self._pushed = False
        self._changed = False
        self._apply(pos.y())

    def mouseMoveEvent(self, e):
        pos = e.position()
        if e.buttons() & Qt.LeftButton:
            if not self._target:
                self._target = self._pick(pos.x())
                self._pushed = False
            self._apply(pos.y())

    def _apply(self, y: float):
        if not self._target:
            return
        v = self._vel_at(y)
        if all(n.velocity == v for n in self._target):
            return
        if not self._pushed:
            self.roll.undo.push()
            self._pushed = True
        for n in self._target:
            n.velocity = v
        self._changed = True
        self.update()
        self.roll.viewport().update()

    def mouseReleaseEvent(self, e):
        if self._changed:
            self.edited.emit()
        self._target = []
        self._changed = False
        self._pushed = False


class EditorView(QWidget):
    edited = Signal()

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.song: Optional[Song] = None
        self.undo = UndoStack(Song())
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget()
        bar.setObjectName("editorBar")
        bar.setStyleSheet(f"#editorBar {{ background: {theme.BG2}; border-bottom: 1px solid {theme.BORDER}; }}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 6, 10, 6)
        bl.setSpacing(6)

        def lab(t):
            l = QLabel(t)
            l.setObjectName("dim")
            return l

        # tools
        self.tool_group = QButtonGroup(self)
        self.tool_btns = []
        for i, (txt, tip) in enumerate((("⬚ Select", "Select / move / resize (1)"), ("✎ Draw", "Draw notes (2)"),
                                        ("⌫ Erase", "Erase notes (3)"))):
            b = QToolButton()
            b.setText(txt)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            self.tool_group.addButton(b, i)
            self.tool_btns.append(b)
            bl.addWidget(b)
        self.tool_btns[0].setChecked(True)
        bl.addSpacing(8)

        bl.addWidget(lab("Track"))
        self.track_combo = QComboBox()
        self.track_combo.setMinimumWidth(170)
        self.track_combo.setFocusPolicy(Qt.NoFocus)
        bl.addWidget(self.track_combo)
        self.track_menu_btn = QToolButton()
        self.track_menu_btn.setText("⋯")
        self.track_menu_btn.setToolTip("Track actions")
        self.track_menu_btn.setPopupMode(QToolButton.InstantPopup)
        self.track_menu_btn.setFocusPolicy(Qt.NoFocus)
        bl.addWidget(self.track_menu_btn)

        bl.addWidget(lab("Ch"))
        self.chan_combo = QComboBox()
        self.chan_combo.setFocusPolicy(Qt.NoFocus)
        for c in range(16):
            self.chan_combo.addItem(f"{c + 1}" + (" (drums)" if c == DRUM_CHANNEL else ""), c)
        self.chan_combo.setToolTip("Channel for new notes (applies to selected notes too)")
        bl.addWidget(self.chan_combo)

        bl.addWidget(lab("Grid"))
        self.snap_combo = QComboBox()
        self.snap_combo.setFocusPolicy(Qt.NoFocus)
        for name, val in SNAPS:
            self.snap_combo.addItem(name, val)
        self.snap_combo.setCurrentIndex(4)
        bl.addWidget(self.snap_combo)

        bl.addWidget(lab("Vel"))
        self.vel_spin = QSpinBox()
        self.vel_spin.setRange(1, 127)
        self.vel_spin.setValue(100)
        self.vel_spin.setToolTip("Velocity for new notes; applies to selected notes")
        bl.addWidget(self.vel_spin)

        bl.addWidget(lab("BPM"))
        self.bpm_spin = QDoubleSpinBox()
        self.bpm_spin.setRange(10, 400)
        self.bpm_spin.setDecimals(1)
        self.bpm_spin.setKeyboardTracking(False)
        self.bpm_spin.setToolTip("Initial tempo of the song")
        bl.addWidget(self.bpm_spin)

        self.quant_btn = QPushButton("Quantize")
        self.quant_btn.setToolTip("Snap selected notes (or all in track) to the grid (Q)")
        self.quant_btn.setFocusPolicy(Qt.NoFocus)
        bl.addWidget(self.quant_btn)
        bl.addStretch(1)
        self.follow_chk = QCheckBox("Follow")
        self.follow_chk.setChecked(True)
        self.follow_chk.setFocusPolicy(Qt.NoFocus)
        bl.addWidget(self.follow_chk)
        for txt, tip, fn in (("−", "Zoom out (Ctrl+wheel)", lambda: self.roll.set_zoom(self.roll.zx / 1.4)),
                             ("+", "Zoom in (Ctrl+wheel)", lambda: self.roll.set_zoom(self.roll.zx * 1.4))):
            b = QToolButton()
            b.setText(txt)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            bl.addWidget(b)
        root.addWidget(bar)

        self.roll = PianoRoll(ctl, self.undo)
        root.addWidget(self.roll, 1)
        self.vel = VelocityLane(self.roll)
        root.addWidget(self.vel)
        self.status = QLabel("")
        self.status.setObjectName("dim")
        self.status.setContentsMargins(10, 3, 10, 3)
        root.addWidget(self.status)

        # actions (shortcuts active while editor has focus)
        self.shortcut_actions = {}
        for key, text, seq, fn in (
            ("undo", "Undo", QKeySequence.Undo, self.do_undo),
            ("redo", "Redo", QKeySequence.Redo, self.do_redo),
            ("copy", "Copy", QKeySequence.Copy, self.roll.copy),
            ("cut", "Cut", QKeySequence.Cut, self.roll.cut),
            ("paste", "Paste at playhead", QKeySequence.Paste, lambda: self.roll.paste()),
            ("dup", "Duplicate", QKeySequence("Ctrl+D"), self.roll.duplicate),
            ("all", "Select all", QKeySequence.SelectAll, self.roll.select_all),
            ("quant", "Quantize", QKeySequence("Q"), lambda: self.roll.quantize()),
            ("t1", "Select tool", QKeySequence("1"), lambda: self.set_tool(TOOL_SELECT)),
            ("t2", "Draw tool", QKeySequence("2"), lambda: self.set_tool(TOOL_DRAW)),
            ("t3", "Erase tool", QKeySequence("3"), lambda: self.set_tool(TOOL_ERASE)),
        ):
            a = QAction(text, self)
            a.setShortcut(QKeySequence(seq))
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            a.triggered.connect(fn)
            self.addAction(a)
            self.shortcut_actions[key] = a

        # wiring
        self.tool_group.idClicked.connect(self.set_tool)
        self.track_combo.currentIndexChanged.connect(self._on_track)
        self.chan_combo.activated.connect(self._on_channel)
        self.snap_combo.currentIndexChanged.connect(self._on_snap)
        self.vel_spin.valueChanged.connect(self._on_vel)
        self.bpm_spin.valueChanged.connect(self._on_bpm)
        self.quant_btn.clicked.connect(lambda: self.roll.quantize())
        self.follow_chk.toggled.connect(lambda v: setattr(self.roll, "follow", v))
        self.roll.edited.connect(self._on_edited)
        self.vel.edited.connect(self._on_edited)
        self.roll.selectionChanged.connect(self._update_status)
        ctl.positionChanged.connect(self._on_position)
        ctl.songLoaded.connect(self.set_song)
        self._build_track_menu()
        self._on_snap(self.snap_combo.currentIndex())

    # --------------------------------------------------------- song
    def set_song(self, song: Optional[Song]):
        self.song = song
        self.undo.reset(song if song is not None else Song())
        self.roll.set_song(song)
        self._fill_tracks()
        self.bpm_spin.blockSignals(True)
        self.bpm_spin.setValue(song.tempo_map.bpm_at(0) if song else 120.0)
        self.bpm_spin.blockSignals(False)
        self.vel.update()
        self._update_status()

    def _fill_tracks(self, keep: int = -2):
        cur = self.roll.track_filter if keep == -2 else keep
        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        self.track_combo.addItem("All tracks", -1)
        if self.song:
            for i, tr in enumerate(self.song.tracks):
                chans = tr.channels()
                ch_txt = ",".join(str(c + 1) for c in chans[:4]) + ("…" if len(chans) > 4 else "")
                label = f"{i + 1}. {tr.name or 'Untitled'}"
                if tr.notes:
                    label += f"  [ch {ch_txt}] · {len(tr.notes)} notes"
                self.track_combo.addItem(label, i)
        idx = self.track_combo.findData(cur)
        self.track_combo.setCurrentIndex(max(0, idx))
        self.track_combo.blockSignals(False)
        self._on_track(self.track_combo.currentIndex())

    def _build_track_menu(self):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("Add track", self.add_track)
        m.addAction("Rename track…", self.rename_track)
        m.addAction("Set track instrument…", self.set_track_instrument)
        m.addSeparator()
        m.addAction("Delete track", self.delete_track)
        self.track_menu_btn.setMenu(m)

    def _current_track(self) -> int:
        t = self.roll.track_filter
        if t < 0 and self.song and self.song.tracks:
            return self.roll.insert_track
        return t

    def add_track(self):
        if not self.song:
            return
        self.undo.push()
        idx = self.song.add_track()
        used = set(self.song.used_channels())
        ch = next((c for c in range(16) if c not in used and c != DRUM_CHANNEL), 0)
        self.song.set_track_program(idx, ch, 0)
        self._fill_tracks(keep=idx)
        self.chan_combo.setCurrentIndex(ch)
        self.roll.insert_channel = ch
        self._on_edited()

    def rename_track(self):
        ti = self._current_track()
        if not self.song or ti < 0:
            return
        name, ok = QInputDialog.getText(self, "Rename track", "Track name:", text=self.song.tracks[ti].name)
        if ok:
            self.undo.push()
            self.song.rename_track(ti, name.strip())
            self._fill_tracks()
            self._on_edited()

    def set_track_instrument(self):
        ti = self._current_track()
        if not self.song or ti < 0:
            return
        ch = self.song.tracks[ti].main_channel()
        items = [f"{i + 1:03d} {n}" for i, n in enumerate(GM_PROGRAMS)]
        cur = self.song.initial_programs().get(ch, 0)
        item, ok = QInputDialog.getItem(self, "Track instrument", f"Instrument for channel {ch + 1}:",
                                        items, cur, False)
        if ok:
            self.undo.push()
            self.song.set_track_program(ti, ch, items.index(item))
            self._on_edited()

    def delete_track(self):
        ti = self._current_track()
        if not self.song or ti < 0 or len(self.song.tracks) <= 1:
            return
        tr = self.song.tracks[ti]
        if QMessageBox.question(self, "Delete track",
                                f"Delete track {ti + 1} “{tr.name or 'Untitled'}” with {len(tr.notes)} notes?") \
                != QMessageBox.Yes:
            return
        self.undo.push()
        self.song.remove_track(ti)
        self.roll.selection.clear()
        self.roll.insert_track = 0
        self._fill_tracks(keep=-1)
        self._on_edited()

    # ------------------------------------------------------ handlers
    def set_tool(self, t: int):
        self.roll.tool = t
        self.tool_btns[t].setChecked(True)

    def _on_track(self, idx: int):
        t = self.track_combo.itemData(idx)
        t = -1 if t is None else t
        self.roll.track_filter = t
        if t >= 0 and self.song:
            self.roll.insert_track = t
            ch = self.song.tracks[t].main_channel()
            self.roll.insert_channel = ch
            self.chan_combo.setCurrentIndex(ch)
        self.roll.selection.clear()
        self.roll.viewport().update()
        self.vel.update()
        self._update_status()

    def _on_channel(self, idx: int):
        self.roll.insert_channel = idx
        if self.roll.selection:
            self.roll.set_selection_channel(idx)

    def _on_snap(self, idx: int):
        self.roll.snap_div = self.snap_combo.itemData(idx)
        self.roll.default_len = self.roll.snap_ticks() if self.roll.snap_div else self.roll.tpb // 4
        self.roll.viewport().update()

    def _on_vel(self, v: int):
        self.roll.default_velocity = v
        if self.roll.selection and self.vel_spin.hasFocus():
            self.roll.set_selection_velocity(v)

    def _on_bpm(self, v: float):
        if not self.song:
            return
        self.undo.push()
        self.song.set_initial_bpm(v)
        self._on_edited()

    def _on_edited(self):
        self.roll.rebuild_cache()
        self.roll.viewport().update()
        self.vel.update()
        self._update_status()
        self.ctl.notify_edited()
        self.edited.emit()

    def do_undo(self):
        if self.undo.undo():
            self._after_history()

    def do_redo(self):
        if self.undo.redo():
            self._after_history()

    def _after_history(self):
        self.roll.selection.clear()
        self._fill_tracks()
        self.bpm_spin.blockSignals(True)
        self.bpm_spin.setValue(self.song.tempo_map.bpm_at(0))
        self.bpm_spin.blockSignals(False)
        self._on_edited()

    def _on_position(self, sec: float):
        if self.song is None or not self.isVisible():
            return
        self.roll.set_playhead_tick(self.song.tempo_map.sec_to_tick(sec))

    def _update_status(self):
        if not self.song:
            self.status.setText("")
            return
        sel = self.roll.selection
        txt = f"{self.song.note_count():,} notes · {len(self.song.tracks)} tracks"
        if sel:
            txt += f" · {len(sel)} selected"
            if len(sel) == 1:
                n = next(iter(sel))
                bar_len = self.roll.tpb * 4
                txt += (f"  —  {note_name(n.pitch)}  vel {n.velocity}  ch {n.channel + 1}  "
                        f"start {n.start / bar_len + 1:.2f}  len {n.length / self.roll.tpb:.2f} beats")
            vels = {n.velocity for n in sel}
            if len(vels) == 1:
                self.vel_spin.blockSignals(True)
                self.vel_spin.setValue(vels.pop())
                self.vel_spin.blockSignals(False)
        txt += "   |   Double-click / Draw tool: add · Right-click: delete · Alt-drag: copy · ↑↓ transpose"
        self.status.setText(txt)
