"""Mixer tab: 16 channel strips (program, reverb, chorus, pan, fader, meter,
mute, solo) plus a master strip."""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget)

from ..gm import CHANNEL_COLORS, DRUM_CHANNEL, DRUM_KITS, GM_PROGRAMS, instrument_name
from . import theme
from .controller import Controller
from .widgets import ColorChip, Fader, Knob, VUMeter, small_button


def _knob_block(label: str, knob: Knob) -> QWidget:
    w = QWidget()
    l = QVBoxLayout(w)
    l.setContentsMargins(0, 0, 0, 0)
    l.setSpacing(0)
    l.addWidget(knob, 0, Qt.AlignHCenter)
    t = QLabel(label)
    t.setObjectName("dim")
    t.setAlignment(Qt.AlignCenter)
    t.setStyleSheet("font-size: 7pt;")
    l.addWidget(t)
    return w


class MixerStrip(QFrame):
    def __init__(self, ch: int, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ch, self.ctl = ch, ctl
        self.setObjectName("strip")
        self.setFixedWidth(92)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 8)
        lay.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(4)
        self.chip = ColorChip(CHANNEL_COLORS[ch], str(ch + 1))
        top.addWidget(self.chip)
        self.kind = QLabel("DRUMS" if ch == DRUM_CHANNEL else "CH")
        self.kind.setObjectName("dim")
        self.kind.setStyleSheet("font-size: 7pt; font-weight: 700;")
        top.addWidget(self.kind)
        top.addStretch(1)
        lay.addLayout(top)

        self.prog = QComboBox()
        self.prog.setFixedWidth(80)
        self.prog.setFocusPolicy(Qt.NoFocus)
        self.prog.view().setMinimumWidth(260)
        self.prog.addItem("From file", None)
        if ch == DRUM_CHANNEL:
            for p, name in DRUM_KITS.items():
                self.prog.addItem(f"{p + 1:03d} {name}", p)
        else:
            for p, name in enumerate(GM_PROGRAMS):
                self.prog.addItem(f"{p + 1:03d} {name}", p)
        self.prog.setToolTip("Instrument override")
        self.prog.activated.connect(self._on_prog)
        lay.addWidget(self.prog)

        self.instr = QLabel("")
        self.instr.setObjectName("dim")
        self.instr.setAlignment(Qt.AlignCenter)
        self.instr.setWordWrap(True)
        self.instr.setFixedHeight(28)
        self.instr.setStyleSheet("font-size: 7pt;")
        lay.addWidget(self.instr)

        knobs = QHBoxLayout()
        knobs.setSpacing(0)
        self.rev = Knob(0, 127, 40)
        self.rev.color = QColor(theme.ACCENT2)
        self.cho = Knob(0, 127, 0)
        self.cho.color = QColor("#b98cff")
        self.rev.setToolTip("Reverb send (CC91) — double-click: follow file")
        self.cho.setToolTip("Chorus send (CC93) — double-click: follow file")
        knobs.addWidget(_knob_block("REV", self.rev))
        knobs.addWidget(_knob_block("CHO", self.cho))
        lay.addLayout(knobs)
        self.pan = Knob(0, 127, 64, bipolar=True)
        self.pan.setFixedSize(40, 40)
        self.pan.setToolTip("Pan (CC10) — double-click: follow file")
        lay.addWidget(_knob_block("PAN", self.pan), 0, Qt.AlignHCenter)

        self.rev.valueChanged.connect(lambda v: self._user_knob("rev", v))
        self.cho.valueChanged.connect(lambda v: self._user_knob("cho", v))
        self.pan.valueChanged.connect(lambda v: self._user_knob("pan", v))
        self.rev.reset.connect(lambda: ctl.set_reverb(ch, None))
        self.cho.reset.connect(lambda: ctl.set_chorus(ch, None))
        self.pan.reset.connect(lambda: ctl.set_pan(ch, None))

        fl = QHBoxLayout()
        fl.setSpacing(6)
        fl.addStretch(1)
        self.fader = Fader(100)
        self.fader.setToolTip("Channel volume — double-click: 100%")
        self.meter = VUMeter(vertical=True)
        fl.addWidget(self.fader)
        fl.addWidget(self.meter)
        fl.addStretch(1)
        lay.addLayout(fl, 1)
        self.fader.valueChanged.connect(lambda v: ctl.set_volume(ch, v / 100.0))

        self.val = QLabel("100%")
        self.val.setAlignment(Qt.AlignCenter)
        self.val.setObjectName("time")
        lay.addWidget(self.val)

        ms = QHBoxLayout()
        ms.setSpacing(4)
        self.mute = small_button("M", "mute", tip="Mute")
        self.solo = small_button("S", "solo", tip="Solo (right-click: exclusive solo)")
        self.solo.setContextMenuPolicy(Qt.CustomContextMenu)
        self.solo.customContextMenuRequested.connect(lambda *_: ctl.exclusive_solo(ch))
        self.mute.toggled.connect(lambda v: ctl.set_mute(ch, v))
        self.solo.toggled.connect(lambda v: ctl.set_solo(ch, v))
        ms.addWidget(self.mute)
        ms.addWidget(self.solo)
        lay.addLayout(ms)
        self._updating = False

    def _user_knob(self, which: str, v: int):
        if self._updating:
            return
        {"rev": self.ctl.set_reverb, "cho": self.ctl.set_chorus, "pan": self.ctl.set_pan}[which](self.ch, v)

    def _on_prog(self, idx: int):
        self.ctl.set_program(self.ch, self.prog.itemData(idx))

    def refresh(self):
        cs = self.ctl.engine.channels[self.ch]
        self._updating = True
        try:
            for w, val in ((self.mute, cs.mute), (self.solo, cs.solo)):
                w.blockSignals(True)
                w.setChecked(val)
                w.blockSignals(False)
            self.fader.blockSignals(True)
            self.fader.setValue(int(round(cs.volume * 100)))
            self.fader.blockSignals(False)
            self.val.setText(f"{int(round(cs.volume * 100))}%")
            for knob, v, ov in ((self.rev, cs.reverb, cs.reverb_override), (self.cho, cs.chorus, cs.chorus_override),
                                (self.pan, cs.pan, cs.pan_override)):
                knob.setValue(v)
                knob.setProperty("override", ov is not None)
            if cs.program_override is None:
                self.prog.setCurrentIndex(0)
            else:
                i = self.prog.findData(cs.program_override)
                self.prog.setCurrentIndex(max(0, i))
            self.refresh_program()
            audible = self.ctl.engine.audible(self.ch)
            self.fader.setEnabled(True)
            self.instr.setEnabled(audible)
            self.chip.setEnabled(audible)
        finally:
            self._updating = False

    def refresh_program(self):
        cs = self.ctl.engine.channels[self.ch]
        self.instr.setText(instrument_name(self.ch, cs.effective_program))

    def refresh_file_values(self):
        """Knobs follow file-driven CC values when not overridden."""
        cs = self.ctl.engine.channels[self.ch]
        self._updating = True
        try:
            if cs.reverb_override is None and self.rev.value() != cs.reverb:
                self.rev.setValue(cs.reverb)
            if cs.chorus_override is None and self.cho.value() != cs.chorus:
                self.cho.setValue(cs.chorus)
            if cs.pan_override is None and self.pan.value() != cs.pan:
                self.pan.setValue(cs.pan)
        finally:
            self._updating = False


class MasterStrip(QFrame):
    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setObjectName("strip")
        self.setFixedWidth(100)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 8)
        t = QLabel("MASTER")
        t.setAlignment(Qt.AlignCenter)
        t.setStyleSheet("font-weight: 800; letter-spacing: 1px;")
        lay.addWidget(t)
        lay.addStretch(0)
        fl = QHBoxLayout()
        fl.addStretch(1)
        self.fader = Fader(100)
        self.meter_l = VUMeter(True)
        self.meter_r = VUMeter(True)
        fl.addWidget(self.fader)
        fl.addWidget(self.meter_l)
        fl.addWidget(self.meter_r)
        fl.addStretch(1)
        lay.addLayout(fl, 1)
        self.val = QLabel("100%")
        self.val.setObjectName("time")
        self.val.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.val)
        self.fader.valueChanged.connect(self._on_fader)
        ctl.masterChanged.connect(self._sync)

    def _on_fader(self, v):
        self.ctl.set_master(v / 100.0)

    def _sync(self, gain: float):
        self.fader.blockSignals(True)
        self.fader.setValue(int(round(gain * 100)))
        self.fader.blockSignals(False)
        self.val.setText(f"{int(round(gain * 100))}%")


class MixerView(QWidget):
    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        hl = QHBoxLayout(inner)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        self.strips: List[MixerStrip] = []
        for ch in range(16):
            s = MixerStrip(ch, ctl)
            self.strips.append(s)
            hl.addWidget(s)
        hl.addSpacing(10)
        self.master = MasterStrip(ctl)
        hl.addWidget(self.master)
        hl.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        ctl.channelChanged.connect(lambda ch: self.strips[ch].refresh())
        ctl.allChannelsChanged.connect(self.refresh_all)
        ctl.songLoaded.connect(lambda *_: self.refresh_all())
        ctl.programsChanged.connect(lambda: [s.refresh_program() for s in self.strips])
        ctl.levels.connect(self.on_levels)
        self._tick = 0

    def refresh_all(self):
        for s in self.strips:
            s.refresh()

    def on_levels(self, lv):
        for s, v in zip(self.strips, lv):
            s.meter.push(v)
        m = max(lv) if lv else 0.0
        # rough stereo split by pan for the master meters
        left = right = 0.0
        for cs, v in zip(self.ctl.engine.channels, lv):
            pan = cs.pan / 127.0
            left = max(left, v * min(1.0, 2 * (1 - pan)))
            right = max(right, v * min(1.0, 2 * pan))
        g = min(1.0, self.ctl.engine.master)
        self.master.meter_l.push(left * g if m else 0.0)
        self.master.meter_r.push(right * g if m else 0.0)
        self._tick += 1
        if self._tick % 6 == 0 and self.isVisible():
            for s in self.strips:
                s.refresh_file_values()
