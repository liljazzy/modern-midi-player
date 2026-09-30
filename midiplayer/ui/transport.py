"""Bottom transport bar: play controls, seek bar, speed, transpose, master volume."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout

from . import glyphs, theme
from .controller import Controller
from .widgets import SeekSlider, fmt_time


class TransportBar(QFrame):
    prevRequested = Signal()
    nextRequested = Signal()

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setObjectName("transportBar")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 8, 16, 10)
        outer.setSpacing(4)

        # seek row
        sr = QHBoxLayout()
        self.cur = QLabel("0:00")
        self.cur.setObjectName("time")
        self.cur.setFixedWidth(48)
        self.seek = SeekSlider()
        self.seek.setRange(0, 1000)
        self.seek.setFocusPolicy(Qt.NoFocus)
        self.total = QLabel("0:00")
        self.total.setObjectName("time")
        self.total.setFixedWidth(48)
        self.total.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sr.addWidget(self.cur)
        sr.addWidget(self.seek, 1)
        sr.addWidget(self.total)
        outer.addLayout(sr)

        row = QHBoxLayout()
        row.setSpacing(6)

        def tbtn(text, tip, checkable=False):
            b = QPushButton(text)
            b.setObjectName("transport")
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCheckable(checkable)
            return b

        self.title = QLabel("")
        self.title.setStyleSheet("font-weight: 600;")
        self.title.setMinimumWidth(160)
        self.title.setMaximumWidth(340)
        row.addWidget(self.title, 1)

        icon_size = QSize(22, 22)
        self.prev_btn = tbtn("", "Previous song (Ctrl+Left)")
        self.prev_btn.setIcon(glyphs.icon("prev"))
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("play")
        self.play_btn.setFocusPolicy(Qt.NoFocus)
        self.play_btn.setIconSize(QSize(20, 20))
        self._play_icon = glyphs.icon("play", "#ffffff")
        self._pause_icon = glyphs.icon("pause", "#ffffff")
        self.next_btn = tbtn("", "Next song (Ctrl+Right)")
        self.next_btn.setIcon(glyphs.icon("next"))
        self.loop_btn = tbtn("Loop off", "Loop the current song: click to turn on", checkable=True)
        self.loop_btn.setObjectName("loop")
        self.loop_btn.setIcon(glyphs.icon("loop", theme.TEXT_DIM, "#ffffff"))
        self.loop_btn.setFixedWidth(108)
        for b in (self.prev_btn, self.next_btn):
            b.setIconSize(icon_size)
        self.loop_btn.setIconSize(QSize(18, 18))
        self._set_playing(False)
        row.addStretch(1)
        for b in (self.loop_btn, self.prev_btn, self.play_btn, self.next_btn):
            row.addWidget(b)
        row.addStretch(1)

        def small_label(t):
            l = QLabel(t)
            l.setObjectName("dim")
            return l

        row.addWidget(small_label("Speed"))
        self.speed = QDoubleSpinBox()
        self.speed.setRange(10, 400)
        self.speed.setDecimals(0)
        self.speed.setSuffix(" %")
        self.speed.setValue(100)
        self.speed.setSingleStep(5)
        self.speed.setToolTip("Playback speed")
        row.addWidget(self.speed)
        row.addWidget(small_label("Transpose"))
        self.transpose = QSpinBox()
        self.transpose.setRange(-24, 24)
        self.transpose.setToolTip("Transpose in semitones (drums unaffected)")
        row.addWidget(self.transpose)
        row.addSpacing(10)
        vol_icon = QLabel()
        vol_icon.setPixmap(glyphs.icon("speaker", theme.TEXT_DIM).pixmap(18, 18))
        row.addWidget(vol_icon)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 150)
        self.volume.setValue(100)
        self.volume.setFixedWidth(120)
        self.volume.setFocusPolicy(Qt.NoFocus)
        self.volume.setToolTip("Master volume")
        row.addWidget(self.volume)
        outer.addLayout(row)

        # wiring
        self.play_btn.clicked.connect(ctl.toggle)
        self.prev_btn.clicked.connect(self.prevRequested.emit)
        self.next_btn.clicked.connect(self.nextRequested.emit)
        self.loop_btn.toggled.connect(self._on_loop)
        self.speed.valueChanged.connect(lambda v: ctl.engine.set_speed(v / 100.0))
        self.transpose.valueChanged.connect(ctl.engine.set_transpose)
        self.volume.valueChanged.connect(lambda v: ctl.set_master(v / 100.0))
        ctl.masterChanged.connect(self._sync_master)
        self.seek.sliderMoved.connect(self._preview_seek)
        self.seek.sliderReleased.connect(self._do_seek)
        ctl.positionChanged.connect(self._on_pos)
        ctl.playStateChanged.connect(self._on_state)
        ctl.songLoaded.connect(self._on_song)
        ctl.songEdited.connect(lambda: self._on_song(ctl.song))

    def _sync_master(self, g: float):
        self.volume.blockSignals(True)
        self.volume.setValue(int(round(g * 100)))
        self.volume.blockSignals(False)

    def _duration(self) -> float:
        return self.ctl.engine.duration if self.ctl.song is not None else 0.0

    def _on_song(self, song):
        self.title.setText(song.title if song is not None else "")
        self.total.setText(fmt_time(self._duration()))
        self.seek.setEnabled(song is not None)

    def _on_pos(self, sec: float):
        d = self._duration()
        if not self.seek.isSliderDown():
            self.seek.blockSignals(True)
            self.seek.setValue(int(1000 * sec / d) if d > 0 else 0)
            self.seek.blockSignals(False)
            self.cur.setText(fmt_time(sec))
        if self.total.text() != fmt_time(d):
            self.total.setText(fmt_time(d))

    def _preview_seek(self, v: int):
        self.cur.setText(fmt_time(self._duration() * v / 1000))

    def _do_seek(self):
        self.ctl.seek(self._duration() * self.seek.value() / 1000)

    def _on_state(self, playing: bool):
        self._set_playing(playing)

    def _set_playing(self, playing: bool):
        self.playing = playing
        self.play_btn.setIcon(self._pause_icon if playing else self._play_icon)
        self.play_btn.setToolTip("Pause (Space)" if playing else "Play (Space)")

    def _on_loop(self, on: bool):
        self.ctl.engine.loop = on
        self.loop_btn.setText("Loop on" if on else "Loop off")
        self.loop_btn.setToolTip("Looping the current song: click to turn off" if on
                                 else "Loop the current song: click to turn on")
