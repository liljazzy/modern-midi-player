"""Real-time MIDI playback engine.

The engine runs on its own thread and streams the song's events to a
backend with sub-millisecond scheduling.  Every message passes through the
channel mixer, which implements:

* mute / solo (with immediate silencing of sounding notes)
* per-channel volume faders (scales the file's CC7 values) and master volume
* pan / reverb / chorus / program overrides
* transpose and playback speed
* controller "chasing" so seeking restores instruments, volumes, bends etc.
"""
from __future__ import annotations

import bisect
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .backends import Backend, NullBackend
from .gm import DRUM_CHANNEL
from .song import PlaybackEvent, Song

def _is_reset_sysex(d: bytes) -> bool:
    if d[:5] == b"\xF0\x7E\x7F\x09\x01" or d[:5] == b"\xF0\x7E\x7F\x09\x03":    # GM1 / GM2 on
        return True
    if len(d) >= 9 and d[1] == 0x41 and d[3] == 0x42 and d[5:8] == b"\x40\x00\x7F":  # GS reset
        return True
    if len(d) >= 8 and d[1] == 0x43 and d[3] == 0x4C and d[4:7] == b"\x00\x00\x7E":  # XG reset
        return True
    return False


DEFAULTS = {"volume": 100, "pan": 64, "reverb": 40, "chorus": 0, "expression": 127}


@dataclass
class ChannelState:
    # user controls
    mute: bool = False
    solo: bool = False
    volume: float = 1.0                    # fader gain 0.0 .. 1.5 (1.0 = unity)
    pan_override: Optional[int] = None
    reverb_override: Optional[int] = None
    chorus_override: Optional[int] = None
    program_override: Optional[int] = None
    # file-driven state
    file_volume: int = 100
    file_pan: int = 64
    file_reverb: int = 40
    file_chorus: int = 0
    program: int = 0
    bank: int = 0
    # metering
    level: float = 0.0
    note_count: int = 0
    active: Dict[int, List[int]] = field(default_factory=dict)  # orig pitch -> [out pitches]

    @property
    def pan(self) -> int:
        return self.file_pan if self.pan_override is None else self.pan_override

    @property
    def reverb(self) -> int:
        return self.file_reverb if self.reverb_override is None else self.reverb_override

    @property
    def chorus(self) -> int:
        return self.file_chorus if self.chorus_override is None else self.chorus_override

    @property
    def effective_program(self) -> int:
        return self.program if self.program_override is None else self.program_override

    def reset_user(self):
        self.mute = self.solo = False
        self.volume = 1.0
        self.pan_override = self.reverb_override = self.chorus_override = self.program_override = None

    def reset_file(self):
        self.file_volume, self.file_pan = DEFAULTS["volume"], DEFAULTS["pan"]
        self.file_reverb, self.file_chorus = DEFAULTS["reverb"], DEFAULTS["chorus"]
        self.program = self.bank = 0


class Engine:
    def __init__(self, backend: Optional[Backend] = None):
        self.backend: Backend = backend or NullBackend()
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.channels = [ChannelState() for _ in range(16)]
        self.master = 1.0
        self.speed = 1.0
        self.transpose = 0
        self.loop = False
        self.events: List[PlaybackEvent] = []
        self.times: List[float] = []
        self.duration = 0.0
        self.idx = 0
        self.playing = False
        self.song_pos = 0.0
        self.anchor = time.perf_counter()
        self.on_finished: Optional[Callable[[], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self._quit = False
        self._generation = 0
        self._thread = threading.Thread(target=self._run, name="midi-engine", daemon=True)
        self._thread.start()

    # ================================================================ public
    def load(self, song: Song, keep_position: bool = False, reset_mixer: bool = True):
        events = song.playback_events()
        duration = song.duration()
        with self.cond:
            pos = self.position() if keep_position else 0.0
            self._silence_all()
            self.events = events
            self.times = [e.time for e in events]
            self.duration = duration
            if not keep_position:
                for cs in self.channels:
                    if reset_mixer:
                        cs.reset_user()
                    cs.note_count = 0
            self._seek(min(pos, duration))
            self.cond.notify_all()

    def unload(self):
        with self.cond:
            self.playing = False
            self._silence_all()
            self.events, self.times, self.duration = [], [], 0.0
            self.idx = 0
            self.song_pos = 0.0
            self.cond.notify_all()

    def position(self) -> float:
        with self.lock:
            if self.playing:
                p = self.song_pos + (time.perf_counter() - self.anchor) * self.speed
                return min(p, self.duration)
            return self.song_pos

    def is_playing(self) -> bool:
        return self.playing

    def play(self):
        with self.cond:
            if not self.events:
                return
            if self.song_pos >= self.duration - 1e-6:
                self._seek(0.0)
            self.anchor = time.perf_counter()
            self.playing = True
            self.cond.notify_all()

    def pause(self):
        with self.cond:
            if self.playing:
                self.song_pos = self.position()
                self.playing = False
            self._silence_all()
            self.cond.notify_all()

    def toggle(self):
        if self.playing:
            self.pause()
        else:
            self.play()

    def stop(self):
        with self.cond:
            self.playing = False
            self._silence_all()
            self._seek(0.0)
            self.cond.notify_all()

    def seek(self, seconds: float):
        with self.cond:
            self._seek(max(0.0, min(seconds, self.duration)))
            self.cond.notify_all()

    def set_speed(self, speed: float):
        with self.cond:
            self.song_pos = self.position()
            self.anchor = time.perf_counter()
            self.speed = max(0.1, min(4.0, speed))
            self.cond.notify_all()

    def set_transpose(self, semitones: int):
        with self.lock:
            self._silence_all()
            self.transpose = max(-24, min(24, int(semitones)))

    def set_backend(self, backend: Backend):
        with self.cond:
            old = self.backend
            try:
                self._silence_all()
            except Exception:
                pass
            self.backend = backend
            try:
                old.close()
            except Exception:
                pass
            self._seek(self.position())
            self.cond.notify_all()

    def close(self):
        with self.cond:
            self._quit = True
            self.playing = False
            try:
                self._silence_all()
            except Exception:
                pass
            self.cond.notify_all()
        self._thread.join(timeout=1.0)
        try:
            self.backend.close()
        except Exception:
            pass

    # ------------------------------------------------------------- mixer
    def any_solo(self) -> bool:
        return any(c.solo for c in self.channels)

    def audible(self, ch: int) -> bool:
        cs = self.channels[ch]
        if cs.mute:
            return False
        return cs.solo or not self.any_solo()

    def set_mute(self, ch: int, mute: bool):
        with self.lock:
            self.channels[ch].mute = bool(mute)
            self._apply_audibility()

    def set_solo(self, ch: int, solo: bool):
        with self.lock:
            self.channels[ch].solo = bool(solo)
            self._apply_audibility()

    def set_exclusive_solo(self, ch: int):
        with self.lock:
            for i, cs in enumerate(self.channels):
                cs.solo = i == ch
            self._apply_audibility()

    def clear_mute_solo(self):
        with self.lock:
            for cs in self.channels:
                cs.mute = cs.solo = False
            self._apply_audibility()

    def set_volume(self, ch: int, gain: float):
        with self.lock:
            self.channels[ch].volume = max(0.0, min(1.5, gain))
            self._send_volume(ch)

    def set_master(self, gain: float):
        with self.lock:
            self.master = max(0.0, min(1.5, gain))
            # Above unity the boost goes to the synth's gain: scaling CC7 would
            # clip at 127 and skew the balance between channels.
            try:
                self.backend.set_gain(max(1.0, self.master))
            except Exception:
                pass
            for ch in range(16):
                self._send_volume(ch)

    def set_pan(self, ch: int, value: Optional[int]):
        with self.lock:
            cs = self.channels[ch]
            cs.pan_override = None if value is None else max(0, min(127, int(value)))
            self._send(bytes([0xB0 | ch, 10, cs.pan]))

    def set_reverb(self, ch: int, value: Optional[int]):
        with self.lock:
            cs = self.channels[ch]
            cs.reverb_override = None if value is None else max(0, min(127, int(value)))
            self._send(bytes([0xB0 | ch, 91, cs.reverb]))

    def set_chorus(self, ch: int, value: Optional[int]):
        with self.lock:
            cs = self.channels[ch]
            cs.chorus_override = None if value is None else max(0, min(127, int(value)))
            self._send(bytes([0xB0 | ch, 93, cs.chorus]))

    def set_program(self, ch: int, program: Optional[int]):
        with self.lock:
            cs = self.channels[ch]
            cs.program_override = None if program is None else max(0, min(127, int(program)))
            self._kill_channel(ch)
            self._send(bytes([0xC0 | ch, cs.effective_program]))

    def reset_channel(self, ch: int):
        with self.lock:
            cs = self.channels[ch]
            cs.reset_user()
            self._apply_audibility()
            self._send_volume(ch)
            self._send(bytes([0xB0 | ch, 10, cs.pan]))
            self._send(bytes([0xB0 | ch, 91, cs.reverb]))
            self._send(bytes([0xB0 | ch, 93, cs.chorus]))
            self._send(bytes([0xC0 | ch, cs.effective_program]))

    def take_levels(self) -> List[float]:
        """Peak levels since last call (0..1), post-fader."""
        with self.lock:
            out = []
            for cs in self.channels:
                out.append(cs.level)
                cs.level = 0.0
            return out

    def send_direct(self, msg: bytes):
        """Send a message straight to the output (e.g. note preview)."""
        with self.lock:
            self._send(msg)

    def preview_note(self, ch: int, pitch: int, velocity: int = 100, duration: float = 0.35):
        self.send_direct(bytes([0x90 | ch, pitch & 0x7F, velocity & 0x7F]))
        t = threading.Timer(duration, self.send_direct, args=(bytes([0x80 | ch, pitch & 0x7F, 0]),))
        t.daemon = True
        t.start()

    # ============================================================ internals
    def _send(self, msg: bytes):
        try:
            self.backend.send(msg)
        except Exception as exc:  # never let a device error kill playback
            if self.on_error:
                try:
                    self.on_error(str(exc))
                except Exception:
                    pass

    def _volume_out(self, ch: int) -> int:
        cs = self.channels[ch]
        return max(0, min(127, int(round(cs.file_volume * cs.volume * min(1.0, self.master)))))

    def _send_volume(self, ch: int):
        self._send(bytes([0xB0 | ch, 7, self._volume_out(ch)]))

    def _kill_channel(self, ch: int, hard: bool = False):
        cs = self.channels[ch]
        for outs in cs.active.values():
            for p in outs:
                self._send(bytes([0x80 | ch, p, 0]))
        cs.active.clear()
        self._send(bytes([0xB0 | ch, 123, 0]))
        if hard:
            self._send(bytes([0xB0 | ch, 120, 0]))

    def _silence_all(self):
        for ch in range(16):
            self._kill_channel(ch)

    def _apply_audibility(self):
        for ch in range(16):
            if not self.audible(ch):
                self._kill_channel(ch, hard=True)

    def _seek(self, t: float):
        """Position playback at *t* seconds and chase controller state."""
        self._silence_all()
        self.idx = bisect.bisect_left(self.times, t)
        self.song_pos = t
        self.anchor = time.perf_counter()
        # --- chase
        progs: Dict[int, int] = {}
        banks: Dict[int, Dict[int, int]] = {}
        ccs: Dict[int, Dict[int, int]] = {ch: {} for ch in range(16)}
        bends: Dict[int, bytes] = {}
        # include controller/program events *at* t so the UI shows the right
        # instruments before playback starts (they are re-sent harmlessly)
        for ev in self.events[:bisect.bisect_right(self.times, t)]:
            d = ev.data
            hi = d[0] & 0xF0
            ch = d[0] & 0x0F
            if hi == 0xB0:
                cc = d[1]
                if cc in (0, 32):
                    banks.setdefault(ch, {})[cc] = d[2]
                elif cc in (120, 123, 124, 125, 126, 127):
                    continue
                elif cc == 121:
                    keep = {k: v for k, v in ccs[ch].items() if k in (7, 10, 91, 93, 0, 32)}
                    ccs[ch] = keep
                    bends.pop(ch, None)
                else:
                    ccs[ch].pop(cc, None)   # keep chronological order of last writes
                    ccs[ch][cc] = d[2]
            elif hi == 0xC0:
                progs[ch] = d[1]
            elif hi == 0xE0:
                bends[ch] = d
        for ch in range(16):
            cs = self.channels[ch]
            cs.reset_file()
            self._send(bytes([0xB0 | ch, 121, 0]))
            b = banks.get(ch, {})
            cs.bank = b.get(0, 0)
            if b:
                for cc, v in b.items():
                    self._send(bytes([0xB0 | ch, cc, v]))
            cs.program = progs.get(ch, 0)
            self._send(bytes([0xC0 | ch, cs.effective_program]))
            c = ccs[ch]
            cs.file_volume = c.get(7, DEFAULTS["volume"])
            cs.file_pan = c.get(10, DEFAULTS["pan"])
            cs.file_reverb = c.get(91, DEFAULTS["reverb"])
            cs.file_chorus = c.get(93, DEFAULTS["chorus"])
            for cc, v in c.items():
                if cc in (7, 10, 91, 93):
                    continue
                self._send(bytes([0xB0 | ch, cc, v]))
            self._send_volume(ch)
            self._send(bytes([0xB0 | ch, 10, cs.pan]))
            self._send(bytes([0xB0 | ch, 91, cs.reverb]))
            self._send(bytes([0xB0 | ch, 93, cs.chorus]))
            self._send(bends.get(ch, bytes([0xE0 | ch, 0, 64])))

    def _dispatch(self, d: bytes):
        st = d[0]
        if st == 0xF0:
            self._send(d)
            if not _is_reset_sysex(d):
                return
            # A GM/GS/XG reset resets volumes - re-apply our mixer on top.
            for ch in range(16):
                self.channels[ch].reset_file()
                self._send_volume(ch)
                cs = self.channels[ch]
                if cs.pan_override is not None:
                    self._send(bytes([0xB0 | ch, 10, cs.pan]))
            return
        hi, ch = st & 0xF0, st & 0x0F
        cs = self.channels[ch]
        if hi == 0x90 and d[2] > 0:
            if not self.audible(ch):
                return
            p = d[1]
            out = p if ch == DRUM_CHANNEL else max(0, min(127, p + self.transpose))
            cs.active.setdefault(p, []).append(out)
            self._send(bytes([st, out, d[2]]))
            lvl = (d[2] / 127.0) * min(1.0, self._volume_out(ch) / 100.0)
            if lvl > cs.level:
                cs.level = lvl
            cs.note_count += 1
            return
        if hi == 0x80 or hi == 0x90:
            outs = cs.active.get(d[1])
            if outs:
                out = outs.pop(0)
                if not outs:
                    del cs.active[d[1]]
                self._send(bytes([0x80 | ch, out, 0]))
            return
        if hi == 0xB0:
            cc, v = d[1], d[2]
            if cc == 7:
                cs.file_volume = v
                self._send_volume(ch)
                return
            if cc == 10:
                cs.file_pan = v
                if cs.pan_override is None:
                    self._send(d)
                return
            if cc == 91:
                cs.file_reverb = v
                if cs.reverb_override is None:
                    self._send(d)
                return
            if cc == 93:
                cs.file_chorus = v
                if cs.chorus_override is None:
                    self._send(d)
                return
            if cc == 0:
                cs.bank = v
            self._send(d)
            return
        if hi == 0xC0:
            cs.program = d[1]
            if cs.program_override is None:
                self._send(d)
            return
        if hi == 0xA0 and not self.audible(ch):
            return
        self._send(d)

    def _run(self):
        while True:
            finished = False
            with self.cond:
                while not self.playing and not self._quit:
                    self.cond.wait()
                if self._quit:
                    return
                pos = self.song_pos + (time.perf_counter() - self.anchor) * self.speed
                evs = self.events
                n = len(evs)
                try:
                    while self.idx < n and evs[self.idx].time <= pos:
                        self._dispatch(evs[self.idx].data)
                        self.idx += 1
                except Exception as exc:  # corrupt event - skip it
                    self.idx += 1
                    if self.on_error:
                        self.on_error(f"Playback error: {exc}")
                if self.idx >= n and pos >= self.duration:
                    if self.loop and n:
                        self._seek(0.0)
                        continue
                    self.playing = False
                    self.song_pos = self.duration
                    self._silence_all()
                    finished = True
                else:
                    nxt = evs[self.idx].time if self.idx < n else self.duration
                    wait = (nxt - pos) / self.speed
                    if wait > 0.003:
                        self.cond.wait(min(wait - 0.0015, 0.02))
                        continue
            if finished:
                cb = self.on_finished
                if cb:
                    try:
                        cb()
                    except Exception:
                        pass
            else:
                time.sleep(0.0005)
