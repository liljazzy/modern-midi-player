"""Qt-side controller that wraps the engine and keeps all views in sync."""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from ..engine import Engine
from ..song import Song


class Controller(QObject):
    channelChanged = Signal(int)          # user mixer state of a channel changed
    allChannelsChanged = Signal()
    masterChanged = Signal(float)
    songLoaded = Signal(object)           # Song
    songEdited = Signal()                 # song content changed in the editor
    playStateChanged = Signal(bool)
    positionChanged = Signal(float)       # seconds (emitted ~30 Hz)
    levels = Signal(list)                 # 16 floats (~30 Hz)
    programsChanged = Signal()            # file-driven program change seen
    finished = Signal()                   # emitted in GUI thread
    engineError = Signal(str)

    def __init__(self, engine: Engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.song: Optional[Song] = None
        engine.on_finished = self.finished.emit       # queued across threads
        engine.on_error = self.engineError.emit
        self._last_playing = False
        self._last_programs: List[int] = [0] * 16
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(120)
        self._reload_timer.timeout.connect(self._reload_engine)
        self._tick = QTimer(self)
        self._tick.setInterval(33)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

    # ------------------------------------------------------------ song
    def set_song(self, song: Optional[Song]):
        self.song = song
        if song is None:
            self.engine.unload()
        else:
            self.engine.load(song, keep_position=False, reset_mixer=True)
        self._last_programs = [cs.effective_program for cs in self.engine.channels]
        self.songLoaded.emit(song)
        self.allChannelsChanged.emit()
        self.positionChanged.emit(self.engine.position())

    def notify_edited(self):
        """Editor changed the song: schedule a (debounced) engine reload."""
        if self.song is not None:
            self.song.invalidate()
        self.songEdited.emit()
        self._reload_timer.start()

    def _reload_engine(self):
        if self.song is not None:
            self.engine.load(self.song, keep_position=True, reset_mixer=False)
            self.allChannelsChanged.emit()

    # --------------------------------------------------------- transport
    def play(self):
        self.engine.play()
        self._emit_state()

    def pause(self):
        self.engine.pause()
        self._emit_state()

    def toggle(self):
        self.engine.toggle()
        self._emit_state()

    def stop(self):
        self.engine.stop()
        self._emit_state()
        self.positionChanged.emit(0.0)

    def seek(self, sec: float):
        self.engine.seek(sec)
        self.positionChanged.emit(self.engine.position())

    def _emit_state(self):
        p = self.engine.is_playing()
        self._last_playing = p
        self.playStateChanged.emit(p)

    # -------------------------------------------------------------- mixer
    def set_mute(self, ch: int, v: bool):
        self.engine.set_mute(ch, v)
        self.allChannelsChanged.emit()     # audibility of others may change display

    def set_solo(self, ch: int, v: bool):
        self.engine.set_solo(ch, v)
        self.allChannelsChanged.emit()

    def exclusive_solo(self, ch: int):
        self.engine.set_exclusive_solo(ch)
        self.allChannelsChanged.emit()

    def clear_mute_solo(self):
        self.engine.clear_mute_solo()
        self.allChannelsChanged.emit()

    def set_volume(self, ch: int, gain: float):
        self.engine.set_volume(ch, gain)
        self.channelChanged.emit(ch)

    def set_pan(self, ch: int, v):
        self.engine.set_pan(ch, v)
        self.channelChanged.emit(ch)

    def set_reverb(self, ch: int, v):
        self.engine.set_reverb(ch, v)
        self.channelChanged.emit(ch)

    def set_chorus(self, ch: int, v):
        self.engine.set_chorus(ch, v)
        self.channelChanged.emit(ch)

    def set_program(self, ch: int, v):
        self.engine.set_program(ch, v)
        self.channelChanged.emit(ch)

    def reset_channel(self, ch: int):
        self.engine.reset_channel(ch)
        self.allChannelsChanged.emit()

    def reset_mixer(self):
        for ch in range(16):
            self.engine.reset_channel(ch)
        self.allChannelsChanged.emit()

    def set_master(self, gain: float):
        self.engine.set_master(gain)
        self.masterChanged.emit(gain)

    # --------------------------------------------------------------- tick
    def _on_tick(self):
        eng = self.engine
        self.positionChanged.emit(eng.position())
        self.levels.emit(eng.take_levels())
        p = eng.is_playing()
        if p != self._last_playing:
            self._last_playing = p
            self.playStateChanged.emit(p)
        progs = [cs.effective_program for cs in eng.channels]
        if progs != self._last_programs:
            self._last_programs = progs
            self.programsChanged.emit()
