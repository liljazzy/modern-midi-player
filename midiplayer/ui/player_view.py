"""Player tab: song information plus a compact channel list with
mute / solo / volume control for each of the 16 MIDI channels."""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSlider,
                               QVBoxLayout, QWidget)

from ..gm import CHANNEL_COLORS, DRUM_CHANNEL, instrument_name
from ..song import Song
from .controller import Controller
from .widgets import ColorChip, VUMeter, fmt_time, small_button


class ChannelRow(QFrame):
    def __init__(self, ch: int, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ch = ch
        self.ctl = ctl
        self.setObjectName("strip")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)
        self.chip = ColorChip(CHANNEL_COLORS[ch], str(ch + 1))
        lay.addWidget(self.chip)
        self.name = QLabel("—")
        self.name.setMinimumWidth(190)
        lay.addWidget(self.name, 2)
        self.meter = VUMeter(vertical=False)
        self.meter.setMinimumWidth(90)
        lay.addWidget(self.meter, 1)
        self.mute = small_button("M", "mute", tip="Mute channel")
        self.solo = small_button("S", "solo", tip="Solo channel (right-click: solo only this channel)")
        self.solo.setContextMenuPolicy(Qt.CustomContextMenu)
        self.solo.customContextMenuRequested.connect(lambda *_: ctl.exclusive_solo(ch))
        lay.addWidget(self.mute)
        lay.addWidget(self.solo)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 150)
        self.vol.setValue(100)
        self.vol.setMinimumWidth(140)
        self.vol.setToolTip("Channel volume (double-click the % to reset)")
        lay.addWidget(self.vol, 2)
        self.vol_label = QPushButton("100%")
        self.vol_label.setFlat(True)
        self.vol_label.setFixedWidth(52)
        self.vol_label.setFocusPolicy(Qt.NoFocus)
        self.vol_label.setToolTip("Click to reset to 100%")
        self.vol_label.setStyleSheet("border:none; background:transparent; color:#8b93a7;")
        lay.addWidget(self.vol_label)

        self.mute.toggled.connect(lambda v: ctl.set_mute(ch, v))
        self.solo.toggled.connect(lambda v: ctl.set_solo(ch, v))
        self.vol.valueChanged.connect(lambda v: ctl.set_volume(ch, v / 100.0))
        self.vol_label.clicked.connect(lambda: self.vol.setValue(100))
        self.used = True

    def refresh(self):
        cs = self.ctl.engine.channels[self.ch]
        for w, val in ((self.mute, cs.mute), (self.solo, cs.solo)):
            w.blockSignals(True)
            w.setChecked(val)
            w.blockSignals(False)
        self.vol.blockSignals(True)
        self.vol.setValue(int(round(cs.volume * 100)))
        self.vol.blockSignals(False)
        self.vol_label.setText(f"{int(round(cs.volume * 100))}%")
        self.refresh_program()
        audible = self.ctl.engine.audible(self.ch)
        self.name.setEnabled(audible and self.used)
        self.chip.setEnabled(audible)
        self.setStyleSheet("" if audible else "QLabel { color: #5a6072; }")

    def refresh_program(self):
        cs = self.ctl.engine.channels[self.ch]
        nm = instrument_name(self.ch, cs.effective_program)
        if self.ch == DRUM_CHANNEL:
            nm = f"🥁 {nm}"
        if cs.program_override is not None:
            nm += "  ✎"
        self.name.setText(nm if self.used else f"{nm}  (unused)")


class PlayerView(QWidget):
    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 8)
        root.setSpacing(12)

        # --- song card
        card = QFrame()
        card.setObjectName("card")
        cl = QGridLayout(card)
        cl.setContentsMargins(18, 14, 18, 14)
        self.title = QLabel("No song loaded")
        self.title.setObjectName("title")
        self.subtitle = QLabel("Open a MIDI file or drop files onto the window")
        self.subtitle.setObjectName("dim")
        cl.addWidget(self.title, 0, 0, 1, 4)
        cl.addWidget(self.subtitle, 1, 0, 1, 4)
        self.info_labels = {}
        for i, key in enumerate(("Duration", "Tempo", "Time sig.", "Key", "Tracks", "Notes", "Format", "Resolution")):
            k = QLabel(key.upper())
            k.setObjectName("dim")
            k.setStyleSheet("font-size: 8pt; font-weight: 600;")
            v = QLabel("—")
            v.setStyleSheet("font-weight: 600;")
            cl.addWidget(k, 2 + (i // 4) * 2, i % 4)
            cl.addWidget(v, 3 + (i // 4) * 2, i % 4)
            self.info_labels[key] = v
        root.addWidget(card)

        # --- channel header
        hdr = QHBoxLayout()
        lbl = QLabel("CHANNELS")
        lbl.setObjectName("dim")
        lbl.setStyleSheet("font-weight: 700; letter-spacing: 1px;")
        hdr.addWidget(lbl)
        hdr.addStretch(1)
        self.unmute_all = QPushButton("Clear mute/solo")
        self.unmute_all.setFocusPolicy(Qt.NoFocus)
        self.unmute_all.clicked.connect(ctl.clear_mute_solo)
        self.reset_btn = QPushButton("Reset mixer")
        self.reset_btn.setFocusPolicy(Qt.NoFocus)
        self.reset_btn.clicked.connect(ctl.reset_mixer)
        hdr.addWidget(self.unmute_all)
        hdr.addWidget(self.reset_btn)
        root.addLayout(hdr)

        # --- channel rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        vl = QVBoxLayout(inner)
        vl.setContentsMargins(0, 0, 4, 0)
        vl.setSpacing(4)
        self.rows: List[ChannelRow] = []
        for ch in range(16):
            r = ChannelRow(ch, ctl)
            self.rows.append(r)
            vl.addWidget(r)
        vl.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        ctl.songLoaded.connect(self.on_song)
        ctl.songEdited.connect(lambda: self.on_song(ctl.song, keep=True))
        ctl.channelChanged.connect(lambda ch: self.rows[ch].refresh())
        ctl.allChannelsChanged.connect(self.refresh_all)
        ctl.programsChanged.connect(lambda: [r.refresh_program() for r in self.rows])
        ctl.levels.connect(self.on_levels)

    def on_song(self, song: Optional[Song], keep: bool = False):
        if song is None:
            self.title.setText("No song loaded")
            self.subtitle.setText("Open a MIDI file or drop files onto the window")
            for v in self.info_labels.values():
                v.setText("—")
            used = set()
        else:
            self.title.setText(song.title)
            self.subtitle.setText(song.path or "New song (not saved)")
            num, den = song.time_signature()
            fmt = f"SMF {song.format}"
            if song.smpte:
                fmt += " (SMPTE)"
            vals = {
                "Duration": fmt_time(song.duration()),
                "Tempo": f"{song.tempo_map.bpm_at(0):.1f} BPM",
                "Time sig.": f"{num}/{den}",
                "Key": song.key_signature() or "—",
                "Tracks": str(len(song.tracks)),
                "Notes": f"{song.note_count():,}",
                "Format": fmt,
                "Resolution": f"{song.ticks_per_beat} PPQ",
            }
            for k, v in vals.items():
                self.info_labels[k].setText(v)
            used = set(song.used_channels())
        for r in self.rows:
            r.used = r.ch in used
            r.setVisible(True)
        self.refresh_all()

    def refresh_all(self):
        for r in self.rows:
            r.refresh()

    def on_levels(self, lv):
        for r, v in zip(self.rows, lv):
            r.meter.push(v)
