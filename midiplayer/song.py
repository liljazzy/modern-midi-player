"""Editable song model built on top of the raw MIDI file representation.

Notes are stored as (start, end, pitch, velocity, channel) objects per track,
while every other event (controllers, program changes, meta, sysex) is kept
verbatim so that files round-trip without losing information.
"""
from __future__ import annotations

import bisect
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import midifile
from .midifile import META, MIDI, SYSEX, RawEvent, RawMidi, RawTrack

DEFAULT_TEMPO = 500_000  # microseconds per quarter note (120 BPM)


class Note:
    __slots__ = ("start", "end", "pitch", "velocity", "channel")

    def __init__(self, start: int, end: int, pitch: int, velocity: int, channel: int):
        self.start = int(start)
        self.end = int(end)
        self.pitch = int(pitch)
        self.velocity = int(velocity)
        self.channel = int(channel)

    @property
    def length(self) -> int:
        return self.end - self.start

    def copy(self) -> "Note":
        return Note(self.start, self.end, self.pitch, self.velocity, self.channel)

    def as_tuple(self):
        return (self.start, self.end, self.pitch, self.velocity, self.channel)

    def __repr__(self):  # pragma: no cover - debug helper
        return f"Note({self.start},{self.end},p={self.pitch},v={self.velocity},ch={self.channel})"


@dataclass
class Track:
    name: str = ""
    notes: List[Note] = field(default_factory=list)
    events: List[RawEvent] = field(default_factory=list)   # everything except notes & EOT
    end_tick: int = 0

    def channels(self) -> List[int]:
        chans = {n.channel for n in self.notes}
        chans.update(e.channel for e in self.events if e.kind == MIDI)
        return sorted(chans)

    def main_channel(self) -> int:
        counts: Dict[int, int] = defaultdict(int)
        for n in self.notes:
            counts[n.channel] += 1
        if counts:
            return max(counts, key=counts.get)
        chans = self.channels()
        return chans[0] if chans else 0


class TempoMap:
    """Converts between ticks and seconds for a list of tempo changes."""

    def __init__(self, ticks_per_beat: int, tempos: List[Tuple[int, int]]):
        self.tpb = max(1, ticks_per_beat)
        tempos = sorted(tempos, key=lambda t: t[0])
        if not tempos or tempos[0][0] != 0:
            tempos.insert(0, (0, DEFAULT_TEMPO))
        # Collapse duplicate ticks (last one wins)
        dedup: List[Tuple[int, int]] = []
        for tick, tempo in tempos:
            if dedup and dedup[-1][0] == tick:
                dedup[-1] = (tick, tempo)
            else:
                dedup.append((tick, max(1, tempo)))
        self.ticks = [t for t, _ in dedup]
        self.tempos = [v for _, v in dedup]
        self.seconds = [0.0]
        for i in range(1, len(dedup)):
            dt = self.ticks[i] - self.ticks[i - 1]
            self.seconds.append(self.seconds[-1] + dt * self.tempos[i - 1] / 1e6 / self.tpb)

    def tick_to_sec(self, tick: float) -> float:
        i = bisect.bisect_right(self.ticks, tick) - 1
        i = max(i, 0)
        return self.seconds[i] + (tick - self.ticks[i]) * self.tempos[i] / 1e6 / self.tpb

    def sec_to_tick(self, sec: float) -> float:
        i = bisect.bisect_right(self.seconds, sec) - 1
        i = max(i, 0)
        return self.ticks[i] + (sec - self.seconds[i]) * 1e6 * self.tpb / self.tempos[i]

    def bpm_at(self, tick: float) -> float:
        i = max(bisect.bisect_right(self.ticks, tick) - 1, 0)
        return 60_000_000 / self.tempos[i]


@dataclass
class PlaybackEvent:
    __slots__ = ("time", "tick", "data", "track")
    time: float
    tick: int
    data: bytes
    track: int


class Song:
    def __init__(self):
        self.path: Optional[str] = None
        self.format: int = 1
        self.ticks_per_beat: int = 480
        self.tracks: List[Track] = []
        self.smpte = None
        self._tempo_map: Optional[TempoMap] = None

    # ------------------------------------------------------------------ io
    @classmethod
    def new(cls, ticks_per_beat: int = 480, bpm: float = 120.0) -> "Song":
        s = cls()
        s.ticks_per_beat = ticks_per_beat
        t0 = Track(name="Track 1")
        t0.events.append(RawEvent(0, META, b"Track 1", 0x03))
        t0.events.append(RawEvent(0, META, int(60_000_000 / bpm).to_bytes(3, "big"), 0x51))
        t0.events.append(RawEvent(0, META, bytes([4, 2, 24, 8]), 0x58))
        t0.events.append(RawEvent(0, MIDI, bytes([0xC0, 0])))
        s.tracks.append(t0)
        return s

    @classmethod
    def load(cls, path: str) -> "Song":
        s = cls.from_raw(midifile.read_file(path))
        s.path = path
        return s

    @classmethod
    def from_raw(cls, raw: RawMidi) -> "Song":
        s = cls()
        s.format = raw.format
        s.ticks_per_beat = raw.ticks_per_beat
        s.smpte = raw.smpte
        for rt in raw.tracks:
            tr = Track()
            open_notes: Dict[Tuple[int, int], deque] = defaultdict(deque)
            last_tick = 0
            for ev in rt.events:
                last_tick = max(last_tick, ev.tick)
                if ev.kind == META:
                    if ev.meta_type == 0x2F:
                        continue
                    if ev.meta_type == 0x03 and not tr.name:
                        tr.name = _decode_text(ev.data)
                    tr.events.append(ev)
                    continue
                if ev.kind == MIDI:
                    st = ev.data[0] & 0xF0
                    ch = ev.data[0] & 0x0F
                    if st == 0x90 and ev.data[2] > 0:
                        open_notes[(ch, ev.data[1])].append((ev.tick, ev.data[2]))
                        continue
                    if st == 0x80 or (st == 0x90 and ev.data[2] == 0):
                        q = open_notes.get((ch, ev.data[1]))
                        if q:
                            start, vel = q.popleft()
                            tr.notes.append(Note(start, max(ev.tick, start), ev.data[1], vel, ch))
                        continue
                tr.events.append(ev)
            tr.end_tick = last_tick
            for (ch, pitch), q in open_notes.items():
                for start, vel in q:
                    tr.notes.append(Note(start, max(last_tick, start + raw.ticks_per_beat // 4), pitch, vel, ch))
            tr.notes.sort(key=lambda n: (n.start, n.pitch))
            s.tracks.append(tr)
        # Format 0 files: split channels into separate tracks for easier editing
        if s.format == 0 and len(s.tracks) == 1:
            s._split_format0()
        s.invalidate()
        return s

    def _split_format0(self):
        src = self.tracks[0]
        chans = sorted({n.channel for n in src.notes} | {e.channel for e in src.events if e.kind == MIDI})
        if len(chans) <= 1:
            return
        meta = Track(name=src.name or "Conductor", end_tick=src.end_tick)
        meta.events = [e for e in src.events if e.kind != MIDI]
        new_tracks = [meta]
        for ch in chans:
            t = Track(name=f"Channel {ch + 1}", end_tick=src.end_tick)
            t.notes = [n for n in src.notes if n.channel == ch]
            t.events = [e for e in src.events if e.kind == MIDI and e.channel == ch]
            new_tracks.append(t)
        self.tracks = new_tracks
        self.format = 1

    def to_raw(self) -> RawMidi:
        raw = RawMidi(format=1 if len(self.tracks) > 1 else 0, ticks_per_beat=self.ticks_per_beat)
        for tr in self.tracks:
            rt = RawTrack()
            evs: List[Tuple[int, int, RawEvent]] = []
            # ordering priority at the same tick: meta/sysex/controllers (0),
            # note-offs (1), note-ons (2)
            has_name = False
            for e in tr.events:
                if e.kind == META and e.meta_type == 0x03 and not has_name:
                    has_name = True
                    if tr.name:
                        e = RawEvent(e.tick, META, tr.name.encode("utf-8", "replace"), 0x03)
                evs.append((e.tick, 0, e))
            if not has_name and tr.name:
                evs.insert(0, (0, -1, RawEvent(0, META, tr.name.encode("utf-8", "replace"), 0x03)))
            for n in tr.notes:
                evs.append((n.end, 1, RawEvent(n.end, MIDI, bytes([0x80 | n.channel, n.pitch, 0x40]))))
                evs.append((n.start, 2, RawEvent(n.start, MIDI, bytes([0x90 | n.channel, n.pitch, max(1, n.velocity)]))))
            evs.sort(key=lambda x: (x[0], x[1]))
            rt.events = [e for _, _, e in evs]
            end = max([tr.end_tick] + [e.tick for e in rt.events])
            rt.events.append(RawEvent(end, META, b"", 0x2F))
            raw.tracks.append(rt)
        return raw

    def save(self, path: str) -> None:
        midifile.write_file(path, self.to_raw())
        self.path = path

    # ------------------------------------------------------------ queries
    @property
    def title(self) -> str:
        if self.path:
            return os.path.splitext(os.path.basename(self.path))[0]
        return "Untitled"

    def invalidate(self):
        self._tempo_map = None

    @property
    def tempo_map(self) -> TempoMap:
        if self._tempo_map is None:
            if self.smpte:
                self._tempo_map = TempoMap(self.ticks_per_beat, [(0, 1_000_000)])
            else:
                tempos = []
                for tr in self.tracks:
                    for e in tr.events:
                        if e.kind == META and e.meta_type == 0x51 and len(e.data) >= 3:
                            tempos.append((e.tick, int.from_bytes(e.data[:3], "big")))
                self._tempo_map = TempoMap(self.ticks_per_beat, tempos)
        return self._tempo_map

    def set_initial_bpm(self, bpm: float):
        tempo = int(round(60_000_000 / max(1.0, bpm)))
        data = tempo.to_bytes(3, "big")
        for tr in self.tracks:
            for i, e in enumerate(tr.events):
                if e.kind == META and e.meta_type == 0x51 and e.tick == 0:
                    tr.events[i] = RawEvent(0, META, data, 0x51)
                    self.invalidate()
                    return
        if not self.tracks:
            self.tracks.append(Track(name="Track 1"))
        self.tracks[0].events.insert(0, RawEvent(0, META, data, 0x51))
        self.invalidate()

    def time_signature(self) -> Tuple[int, int]:
        for tr in self.tracks:
            for e in tr.events:
                if e.kind == META and e.meta_type == 0x58 and len(e.data) >= 2 and e.tick == 0:
                    return e.data[0] or 4, 2 ** e.data[1]
        return 4, 4

    def key_signature(self) -> str:
        for tr in self.tracks:
            for e in tr.events:
                if e.kind == META and e.meta_type == 0x59 and len(e.data) >= 2:
                    sf = e.data[0] - 256 if e.data[0] > 127 else e.data[0]
                    minor = e.data[1]
                    majors = ["Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E", "B", "F#", "C#"]
                    minors = ["Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E", "B", "F#", "C#", "G#", "D#", "A#"]
                    idx = max(0, min(14, sf + 7))
                    return f"{minors[idx]} minor" if minor else f"{majors[idx]} major"
        return ""

    def end_tick(self) -> int:
        end = 0
        for tr in self.tracks:
            if tr.notes:
                end = max(end, max(n.end for n in tr.notes))
            if tr.events:
                end = max(end, max(e.tick for e in tr.events))
        return end

    def duration(self) -> float:
        return self.tempo_map.tick_to_sec(self.end_tick())

    def note_count(self) -> int:
        return sum(len(t.notes) for t in self.tracks)

    def initial_programs(self) -> Dict[int, int]:
        """First program change for each channel (used for display)."""
        first: Dict[int, Tuple[int, int]] = {}
        for tr in self.tracks:
            for e in tr.events:
                if e.kind == MIDI and e.data[0] & 0xF0 == 0xC0:
                    ch = e.data[0] & 0x0F
                    if ch not in first or e.tick < first[ch][0]:
                        first[ch] = (e.tick, e.data[1])
        return {ch: v for ch, (_, v) in first.items()}

    def used_channels(self) -> List[int]:
        chans = set()
        for tr in self.tracks:
            chans.update(n.channel for n in tr.notes)
        return sorted(chans)

    # ------------------------------------------------------------ playback
    def playback_events(self) -> List[PlaybackEvent]:
        tm = self.tempo_map
        items: List[Tuple[int, int, int, bytes]] = []  # tick, prio, track, data
        for ti, tr in enumerate(self.tracks):
            for e in tr.events:
                if e.kind == MIDI:
                    items.append((e.tick, 0, ti, e.data))
                elif e.kind == SYSEX and e.data[:1] == b"\xF0":
                    data = e.data if e.data.endswith(b"\xF7") else e.data + b"\xF7"
                    items.append((e.tick, 0, ti, data))
            for n in tr.notes:
                if n.end <= n.start:
                    continue
                items.append((n.start, 2, ti, bytes([0x90 | n.channel, n.pitch, max(1, min(127, n.velocity))])))
                items.append((n.end, 1, ti, bytes([0x80 | n.channel, n.pitch, 0])))
        items.sort(key=lambda x: (x[0], x[1]))
        return [PlaybackEvent(tm.tick_to_sec(t), t, d, ti) for t, _, ti, d in items]

    # ------------------------------------------------------------ editing
    def snapshot(self):
        return [(tr.name, [n.as_tuple() for n in tr.notes], list(tr.events), tr.end_tick) for tr in self.tracks]

    def restore(self, snap):
        self.tracks = []
        for name, notes, events, end in snap:
            self.tracks.append(Track(name=name, notes=[Note(*n) for n in notes], events=list(events), end_tick=end))
        self.invalidate()

    def add_track(self, name: str = "") -> int:
        self.tracks.append(Track(name=name or f"Track {len(self.tracks) + 1}"))
        return len(self.tracks) - 1

    def remove_track(self, index: int):
        if 0 <= index < len(self.tracks):
            del self.tracks[index]
            self.invalidate()

    def rename_track(self, index: int, name: str):
        tr = self.tracks[index]
        tr.name = name
        data = name.encode("latin-1", "replace")
        for i, e in enumerate(tr.events):
            if e.kind == META and e.meta_type == 0x03:
                tr.events[i] = RawEvent(e.tick, META, data, 0x03)
                return
        tr.events.insert(0, RawEvent(0, META, data, 0x03))

    def set_track_program(self, index: int, channel: int, program: int):
        """Set/replace the program change at tick 0 for a channel in a track."""
        tr = self.tracks[index]
        for i, e in enumerate(tr.events):
            if e.kind == MIDI and e.data[0] == (0xC0 | channel) and e.tick == 0:
                tr.events[i] = RawEvent(0, MIDI, bytes([0xC0 | channel, program & 0x7F]))
                return
        tr.events.insert(0, RawEvent(0, MIDI, bytes([0xC0 | channel, program & 0x7F])))


class UndoStack:
    def __init__(self, song: Song, limit: int = 200):
        self.song = song
        self.limit = limit
        self._undo: list = []
        self._redo: list = []

    def reset(self, song: Song):
        self.song = song
        self._undo.clear()
        self._redo.clear()

    def push(self):
        """Call *before* modifying the song."""
        self._undo.append(self.song.snapshot())
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.song.snapshot())
        self.song.restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.song.snapshot())
        self.song.restore(self._redo.pop())
        return True


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc).strip("\x00").strip()
        except UnicodeDecodeError:
            continue
    return ""
