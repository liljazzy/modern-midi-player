"""Standard MIDI File (SMF) reader / writer with no third-party dependencies.

Supports format 0/1/2 files, running status, SysEx (F0/F7) events, meta
events, RIFF-wrapped (.rmi) files and tolerates common real-world defects
such as truncated tracks or wrong track lengths.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional


class MidiFileError(Exception):
    pass


# Kinds of raw events
MIDI = 0      # channel voice message, data = status + data bytes
SYSEX = 1     # data = full sysex message starting with 0xF0 (or raw F7 escape)
META = 2      # meta_type set, data = payload


@dataclass
class RawEvent:
    tick: int                  # absolute tick
    kind: int                  # MIDI / SYSEX / META
    data: bytes
    meta_type: int = -1

    @property
    def status(self) -> int:
        return self.data[0] if self.kind == MIDI and self.data else 0

    @property
    def channel(self) -> int:
        return self.data[0] & 0x0F if self.kind == MIDI else -1


@dataclass
class RawTrack:
    events: List[RawEvent] = field(default_factory=list)


@dataclass
class RawMidi:
    format: int = 1
    ticks_per_beat: int = 480
    tracks: List[RawTrack] = field(default_factory=list)
    smpte: Optional[tuple] = None   # (fps, ticks_per_frame) for SMPTE timing


# Number of data bytes for each channel-voice status high nibble.
_DATA_LEN = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}


def _read_varlen(buf: bytes, pos: int, end: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if pos >= end:
            raise MidiFileError("unexpected end of track in variable-length value")
        b = buf[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos
    return value, pos


def _write_varlen(value: int) -> bytes:
    value = max(0, int(value))
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def _parse_track(buf: bytes, pos: int, end: int) -> RawTrack:
    track = RawTrack()
    tick = 0
    running = 0
    while pos < end:
        try:
            delta, pos = _read_varlen(buf, pos, end)
        except MidiFileError:
            break
        tick += delta
        if pos >= end:
            break
        status = buf[pos]
        if status == 0xFF:  # meta
            if pos + 1 >= end:
                break
            mtype = buf[pos + 1]
            try:
                length, p = _read_varlen(buf, pos + 2, end)
            except MidiFileError:
                break
            data = bytes(buf[p:p + length])
            pos = p + length
            if mtype == 0x2F:  # end of track
                track.events.append(RawEvent(tick, META, b"", 0x2F))
                break
            track.events.append(RawEvent(tick, META, data, mtype))
            # meta events cancel running status in practice for many files;
            # the spec says they don't, so keep running status.
        elif status in (0xF0, 0xF7):
            try:
                length, p = _read_varlen(buf, pos + 1, end)
            except MidiFileError:
                break
            payload = bytes(buf[p:p + length])
            pos = p + length
            if status == 0xF0:
                track.events.append(RawEvent(tick, SYSEX, b"\xF0" + payload))
            else:
                track.events.append(RawEvent(tick, SYSEX, b"\xF7" + payload))
            running = 0
        else:
            if status & 0x80:
                pos += 1
                if status >= 0xF0:
                    # System common/realtime in a file: skip conservatively.
                    running = 0
                    continue
                running = status
            else:
                if not running:
                    # Data byte with no running status: skip it.
                    pos += 1
                    continue
                status = running
            n = _DATA_LEN[status & 0xF0]
            if pos + n > end:
                break
            data = bytes([status]) + bytes(b & 0x7F for b in buf[pos:pos + n])
            pos += n
            track.events.append(RawEvent(tick, MIDI, data))
    return track


def parse_bytes(buf: bytes) -> RawMidi:
    # RIFF RMID wrapper
    if buf[:4] == b"RIFF" and buf[8:12] == b"RMID":
        i = buf.find(b"MThd")
        if i < 0:
            raise MidiFileError("RIFF file without MIDI data")
        buf = buf[i:]
    i = buf.find(b"MThd")
    if i < 0:
        raise MidiFileError("not a MIDI file (missing MThd header)")
    buf = buf[i:]
    if len(buf) < 14:
        raise MidiFileError("truncated MIDI header")
    hlen, fmt, ntracks, division = struct.unpack(">IHHH", buf[4:14])
    midi = RawMidi(format=fmt)
    if division & 0x8000:
        fps = 256 - (division >> 8)
        tpf = division & 0xFF
        midi.smpte = (fps, tpf)
        # Express SMPTE time as ticks with a fixed 1 beat = 1 second tempo map
        midi.ticks_per_beat = max(1, fps * tpf)
    else:
        midi.ticks_per_beat = max(1, division)
    pos = 8 + hlen
    while pos + 8 <= len(buf) and len(midi.tracks) < max(ntracks, 1) + 64:
        cid = buf[pos:pos + 4]
        clen = struct.unpack(">I", buf[pos + 4:pos + 8])[0]
        start = pos + 8
        if cid == b"MTrk":
            end = min(start + clen, len(buf))
            # Some files have wrong chunk lengths; if next chunk isn't where it
            # should be, scan until the next MTrk header or EOF.
            nxt = start + clen
            if nxt < len(buf) and buf[nxt:nxt + 4] != b"MTrk" and len(midi.tracks) + 1 < ntracks:
                j = buf.find(b"MTrk", start)
                if j > start:
                    end = j
                    nxt = j
            midi.tracks.append(_parse_track(buf, start, end))
            pos = nxt
        else:
            if cid.strip(b"\x00") == b"":
                break
            pos = start + clen
    if not midi.tracks:
        raise MidiFileError("MIDI file contains no tracks")
    return midi


def read_file(path: str) -> RawMidi:
    with open(path, "rb") as f:
        return parse_bytes(f.read())


def to_bytes(midi: RawMidi) -> bytes:
    out = bytearray()
    fmt = 1 if len(midi.tracks) > 1 else midi.format if midi.format in (0, 1) else 1
    out += b"MThd" + struct.pack(">IHHH", 6, fmt, len(midi.tracks), midi.ticks_per_beat & 0x7FFF)
    for tr in midi.tracks:
        body = bytearray()
        last = 0
        events = sorted(tr.events, key=lambda e: e.tick)  # stable
        end_tick = events[-1].tick if events else 0
        for ev in events:
            if ev.kind == META and ev.meta_type == 0x2F:
                end_tick = max(end_tick, ev.tick)
                continue
            body += _write_varlen(ev.tick - last)
            last = ev.tick
            if ev.kind == MIDI:
                body += ev.data
            elif ev.kind == SYSEX:
                payload = ev.data[1:]
                body += bytes([ev.data[0]]) + _write_varlen(len(payload)) + payload
            else:
                body += bytes([0xFF, ev.meta_type & 0x7F]) + _write_varlen(len(ev.data)) + ev.data
        body += _write_varlen(max(0, end_tick - last)) + b"\xFF\x2F\x00"
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    return bytes(out)


def write_file(path: str, midi: RawMidi) -> None:
    data = to_bytes(midi)
    with open(path, "wb") as f:
        f.write(data)
