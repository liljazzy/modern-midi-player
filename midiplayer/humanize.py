"""Humanize: make a played-back song sound like a person playing it instead of a machine.

Notes in a MIDI file usually sit exactly on the grid, with the same few velocities and perfectly regular lengths. A pianist
does none of that, so this module nudges every note a little at playback time (the song itself is never changed):

* **Timing.** Each note starts a few milliseconds early or late. Part of that is a slow drift shared by everything for a few
  seconds at a time (the way a player pushes and relaxes the tempo), part is a different drift per track (each hand
  wanders a little differently), and part is random per note.
* **Chords are rolled, not struck.** Notes that are meant to sound together are spread over a few milliseconds (centred on
  where the chord is written), usually from the bottom up; the top note (the melody) leans slightly ahead of the others.
* **Dynamics.** Velocity wanders with the phrase and varies from note to note; in a chord the melody note is a little
  louder, the bass a little firmer and the inner notes a little softer.
* **Skill levels.** Presets that change the *character* of the playing, not just how loose it is: see SKILLS. The lower
  ones also make mistakes - a fat finger (the neighbouring key struck as well), a wrong key, a missed note.
* **Familiar and unfamiliar passages** (the skill levels). The player knows some parts of the piece better than others - by
  chance, because a part comes round again, and because simple parts are easier to know. Known parts are played faster and
  cleaner, new parts slower (with hesitations), shakier and with more mistakes. See Fluency and Warp.
* **Repeated keys.** A key has to come back up before it can be struck again, so the same note repeated is never
  "instant": the earlier one is let go a little sooner, and a fast repeat is struck a little later and lighter.
* **Note lengths.** Each key is let go a little earlier or later than written, never so that a repeated note is cut off or
  overlaps itself, and never so that a chord is still sounding when the next chord in the same part is played (unless the
  music itself writes them overlapping), and never shorter than a short tap.

Drums are left exactly as written; so are deliberate near-silent "ghost" notes; and a note never starts before the program
or bank change that sets its instrument.

*strength* scales everything (0 = exactly as written, 1 = an expressive player, up to 2 = a very loose one). The same seed always gives the same
performance.
"""
from __future__ import annotations

import bisect
import json
import math
import random
from dataclasses import asdict, dataclass, fields
from typing import Dict, List, Tuple, Union

from .midifile import MIDI

DRUM_CHANNEL = 9
GHOST_VELOCITY = 24          # velocities below this are deliberate ghost notes and are left alone

# what a strength of 1.0 means (seconds / MIDI velocity units)
TIMING_RANDOM = 0.008        # per-note timing jitter (standard deviation)
TIMING_DRIFT = 0.012         # slow drift shared by all tracks
TIMING_TRACK_DRIFT = 0.007   # slow drift of each track on its own
TIMING_LIMIT = 0.045         # no note moves further than this
CHORD_WINDOW = 0.025         # notes starting within this of each other (seconds) are one chord
CHORD_STEP = (0.003, 0.008)  # gap between consecutive notes of a rolled chord
CHORD_MAX_ROLL = 0.040       # the whole roll never takes longer than this
MELODY_LEAD = 0.006          # the top note of a chord leans this far ahead
VELOCITY_RANDOM = 9.0        # (these are larger than they sound: 'Even out note volumes', on by default, halves them at playback)
VELOCITY_DRIFT = 7.0
VOICING = (9.0, 4.0, -4.0)   # velocity added to the top note / the bottom note / the inner notes of a chord
RELEASE_RANDOM = 0.014       # key release jitter
MIN_LENGTH = 0.04            # a note is never shortened below this (or below its written length, if that is shorter)
OVERLAP_GAP = 0.003          # (the least a note ends before the next note of the same pitch)
RESTRIKE_GAP = 0.050         # a key needs to be back up this long (s) before it can be struck again, at strength 1 and repeat 1...
RESTRIKE_MIN_INTERVAL = 0.02 # two strikes of the same key are never closer than this (s)
RESTRIKE_MAX = 0.08          # ...and never more than this, however loose the setting (a note is not chopped short)
RESTRIKE_SHARE = 0.5         # ...nor more than this share of the time between the two strikes
RESTRIKE_FAST = 0.25         # strikes of the same key closer together than this (s) are 'fast' repeats: lighter and later
RESTRIKE_LIMIT = 0.07        # ...fully so at this spacing or less (about as fast as fingers can repeat a key)
RESTRIKE_LAG = 0.012         # how much later the second strike of a very fast repeat may come
RESTRIKE_SOFT = 0.15         # and how much softer it is (share of its velocity)
FAST_REPEAT_MISS = 3.0       # a note that is a very fast repeat of the same key is this much more likely to be missed
SEGMENT_BARS = 2             # familiarity is judged passage by passage, this many bars at a time
KNOW_RATE = 0.16             # how fast the tempo drifts for each step of familiarity: a passage that is entirely new runs this much
                             # (x amount) slower, one that is entirely known this much faster
KNOW_SLOW_LIMIT = 0.9        # the player never falls further behind than this (s, x amount)...
KNOW_FAST_LIMIT = 0.25       # ...nor gets further ahead than this
PAUSE_CHANCE = 0.12          # chance of a hesitation before a chord in a passage that is entirely new
PAUSE_SIZE = (0.04, 0.16)    # how long a hesitation is (s)
NEXT_GAP = 0.004             # a chord is let go at least this long before the next chord in the same part starts

LEVELS = {"off": 0.0, "light": 0.4, "natural": 0.85, "loose": 1.7}      # (Loose is a sloppy, very expressive player)
MAX_STRENGTH = 2.0
MAX_AMOUNT = 3.0

# Skill levels. These are not just "more or less loose": a beginner is clumsy everywhere (uneven timing, chords that do not
# land together, no sense of which note is the melody, keys let go unevenly, now and then a stumble); a concert pianist is
# very precise note by note but shapes the music - a slow push and pull of the tempo, swelling and fading phrases, a clearly
# voiced melody over softer accompaniment and chords rolled only a touch.
SKILL_NAMES = ("beginner", "intermediate", "advanced", "pianist")
SKILL_LABELS = {"beginner": "Beginner", "intermediate": "Intermediate", "advanced": "Advanced", "pianist": "Concert pianist"}
SKILL_TEXT = {
    "beginner": "Still learning: uneven timing, ragged chords, no melody voicing, stumbles - and fat fingers, wrong notes and missed notes. Plays the parts they know faster and cleaner, the parts they do not know slower and worse",
    "intermediate": "Fairly steady but human: noticeable timing and volume differences, a few slips, the odd fat finger, wrong or missed note, and some difference between familiar and new passages",
    "advanced": "Controlled and musical: tight timing, a gentle push and pull of the tempo, shaped phrases, a clear melody - and very rarely a slip",
    "pianist": "A concert pianist: very precise note by note, expressive tempo and dynamics, a singing melody over soft accompaniment",
}
SKILLS = {
    "beginner": dict(strength=1.1, timing=2.1, drift=1.5, chords=2.0, melody=0.3, velocity=2.0, dynamics=0.6, release=2.2, repeat=1.8, know=1.6, slips=1.2,
                     fat=1.5, wrong=1.3, missed=1.3),
    "intermediate": dict(strength=1.0, timing=1.4, drift=1.1, chords=1.4, melody=0.6, velocity=1.4, dynamics=0.8, release=1.5, repeat=1.3, know=0.8, slips=0.5,
                         fat=0.6, wrong=0.5, missed=0.5),
    "advanced": dict(strength=0.8, timing=0.7, drift=1.2, chords=0.9, melody=1.2, velocity=0.8, dynamics=1.3, release=0.8, repeat=1.0, know=0.3, slips=0.0,
                     fat=0.12, wrong=0.08, missed=0.08),
    "pianist": dict(strength=0.7, timing=0.35, drift=1.0, chords=0.7, melody=1.6, velocity=0.5, dynamics=1.8, release=0.5, repeat=0.7, know=0.0, slips=0.0,
                    fat=0.0, wrong=0.0, missed=0.0),
}
SLIP_CHANCE = 0.035          # chance per note of a slip, at overall strength 1 and slips 1
FAT_CHANCE = 0.03            # chance per note that the key next to it is struck too, at overall strength 1 and fat 1
WRONG_CHANCE = 0.02          # chance per note that a neighbouring key is played instead
MISS_CHANCE = 0.02           # chance per note that it is not played at all
REPEAT_DECAY = 0.9           # a fat finger gets this much less likely for every time the same chord / note has just been played...
REPEAT_FLOOR = 0.08          # ...down to this share of its normal chance (the hands settle into a repeated figure)
SLIP_SIZE = (0.025, 0.065)   # how far (seconds) a slip moves a note; most are late


@dataclass
class HumanizeConfig:
    """How a song is humanized. *strength* is the master amount (0 = exactly as written, 1 = an expressive player, 2 = very
    loose); every other amount is a multiplier on one kind of nudge (1 = the normal amount, 0 = none of that kind)."""
    strength: float = 0.0
    timing: float = 1.0        # how early / late each note starts, note by note
    drift: float = 1.0         # the slow push and pull of the tempo (shared, and per track)
    chords: float = 1.0        # how much chords are rolled
    melody: float = 1.0        # how far the top note of a chord leans ahead and stands out
    velocity: float = 1.0      # how much loudness varies from note to note
    dynamics: float = 1.0      # how much loudness drifts over a phrase
    release: float = 1.0       # how unevenly keys are let go
    repeat: float = 1.0        # how hard it is to strike the same key again quickly: the time it needs to come back up, and how
                               # much lighter and later a fast re-strike is
    know: float = 0.0          # how much it matters whether the player knows a passage: known parts are played cleaner and faster,
                               # unfamiliar parts slower, shakier and with more mistakes (0 = every part is played the same)
    slips: float = 0.0         # now and then a note is a lot early or late, like a player who is still learning (0 = never)
    fat: float = 0.0           # fat fingers: now and then the key next to the right one is struck too (0 = never)
    wrong: float = 0.0         # now and then a neighbouring key is played instead of the right one (0 = never)
    missed: float = 0.0        # now and then a note is not played at all (0 = never)
    drums: bool = False        # also loosen the drums a little (off: drums stay exactly on the grid)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text) -> "HumanizeConfig":
        cfg = cls()
        try:
            data = json.loads(text) if text else {}
        except (TypeError, ValueError):
            return cfg
        if not isinstance(data, dict):
            return cfg
        for f in fields(cls):
            if f.name in data:
                try:
                    setattr(cfg, f.name, type(getattr(cfg, f.name))(data[f.name]))
                except (TypeError, ValueError):
                    pass
        return cfg.clamped()

    def clamped(self) -> "HumanizeConfig":
        self.strength = max(0.0, min(MAX_STRENGTH, float(self.strength)))
        for name in ("timing", "drift", "chords", "melody", "velocity", "dynamics", "release", "repeat", "know", "slips", "fat", "wrong", "missed"):
            setattr(self, name, max(0.0, min(MAX_AMOUNT, float(getattr(self, name)))))
        self.drums = bool(self.drums)
        return self

    @classmethod
    def preset(cls, name: str) -> "HumanizeConfig":
        """One of the looseness presets (off / light / natural / loose) or a skill level (beginner .. pianist)."""
        if name in SKILLS:
            return cls(**SKILLS[name])
        return cls(strength=LEVELS.get(name, 0.0))

    @classmethod
    def coerce(cls, value) -> "HumanizeConfig":
        """A config, or just a master strength (a number), as a config."""
        if isinstance(value, HumanizeConfig):
            return value
        return cls(strength=float(value or 0.0)).clamped()

    def preset_name(self):
        """The name of the preset this is (off / light / natural / loose / beginner / intermediate / advanced / pianist), or
        None if it is a custom mix."""
        for name in list(LEVELS) + list(SKILL_NAMES):
            other = HumanizeConfig.preset(name)
            if all(abs(getattr(self, f.name) - getattr(other, f.name)) < 0.006 for f in fields(self) if f.name != "drums") \
                    and self.drums == other.drums:
                return name
        return None


Humanize = Union[float, HumanizeConfig]


def strength_of(humanize) -> float:
    """The master strength of a float or a HumanizeConfig (0 = nothing to do)."""
    return HumanizeConfig.coerce(humanize).strength


class _Drift:
    """Smooth random wandering over time: a handful of slow sine waves with random frequency, phase and weight."""

    def __init__(self, rng: random.Random, amplitude: float, slowest: float = 0.05, fastest: float = 0.3, waves: int = 5):
        self.waves = []
        total = 0.0
        for _ in range(waves):
            w = rng.uniform(0.4, 1.0)
            self.waves.append((w, rng.uniform(slowest, fastest) * math.tau, rng.uniform(0, math.tau)))
            total += w * w
        norm = amplitude / math.sqrt(total / 2.0)                       # (so the standard deviation is about *amplitude*)
        self.waves = [(w * norm, f, ph) for w, f, ph in self.waves]

    def at(self, t: float) -> float:
        return sum(w * math.sin(f * t + ph) for w, f, ph in self.waves)


class Humanized(dict):
    """What humanizing a song gives. As a dict: (track index, index in track.notes) -> (start seconds, end seconds, velocity)
    for every note that was nudged (notes left alone are missing). Plus the mistakes, if the settings ask for any:
    *dropped* (notes not played), *repitch* (note -> the pitch actually played) and *extras* (notes struck by accident:
    (track, channel, pitch, start, end, velocity, tick of the note they belong to))."""

    def __init__(self):
        super().__init__()
        self.dropped = set()
        self.repitch = {}
        self.extras = []
        self.fast_repeats = {}          # (track, note) -> 0..1: how fast a re-strike of the same key this note is
        self.fluency = None             # how well the player knows each passage (a Fluency), if 'know' is on
        self.warp = None                # the slowing down and speeding up that goes with it (a Warp): applies to every event
        self.know = 0.0                 # overall amount


class Fluency:
    """How well the player knows each passage of the piece (0 = never seen it, 1 = knows it cold), by time."""

    def __init__(self, centres: List[float], values: List[float]):
        self.centres, self.values = centres, values

    def at(self, t: float) -> float:
        c, v = self.centres, self.values
        if not c:
            return 0.5
        i = bisect.bisect_right(c, t)
        if i <= 0:
            return v[0]
        if i >= len(c):
            return v[-1]
        span = c[i] - c[i - 1]
        return v[i - 1] if span <= 1e-9 else v[i - 1] + (v[i] - v[i - 1]) * (t - c[i - 1]) / span


class Warp:
    """The player's own tempo: how far behind (+) or ahead (-) of the written time they are at any moment."""

    def __init__(self, times: List[float], offsets: List[float]):
        self.times, self.offsets = times, offsets

    def _at(self, t: float, after: bool) -> float:
        T, O = self.times, self.offsets
        if not T:
            return 0.0
        i = bisect.bisect_right(T, t) if after else bisect.bisect_left(T, t)
        if i <= 0:
            return O[0]
        if i >= len(T):
            return O[-1]
        span = T[i] - T[i - 1]
        return O[i] if span <= 1e-12 else O[i - 1] + (O[i] - O[i - 1]) * (t - T[i - 1]) / span

    def start(self, t: float) -> float:
        """The offset for something that begins at written time t (after any hesitation just before it)."""
        return self._at(t, True)

    def end(self, t: float) -> float:
        """The offset for something that ends at written time t (before any hesitation just after it)."""
        return self._at(t, False)


def _fluency_map(song, tempo_map, rng: random.Random) -> Fluency:
    """Judge every passage (SEGMENT_BARS bars) of the song: how well would this player know it?

    Part chance (what they happened to practise), part the music itself: a passage that comes round again, as a verse or
    chorus does, is better known the second and third time; a busy, wide-leaping, many-note passage is harder to know."""
    num, den = song.time_signature()
    seg_ticks = max(1, int(song.ticks_per_beat * 4 * num / den * SEGMENT_BARS))
    quarter = max(1, song.ticks_per_beat // 4)
    segs: Dict[int, List] = {}
    for tr in song.tracks:
        for n in tr.notes:
            if n.end > n.start and n.channel != DRUM_CHANNEL:
                segs.setdefault(n.start // seg_ticks, []).append(n)
    if not segs:
        return Fluency([], [])
    centres: List[float] = []
    values: List[float] = []
    signatures: List[frozenset] = []
    for k in sorted(segs):
        notes = segs[k]
        t0, t1 = tempo_map.tick_to_sec(k * seg_ticks), tempo_map.tick_to_sec((k + 1) * seg_ticks)
        onsets: Dict[int, List[int]] = {}
        for n in notes:
            onsets.setdefault(n.start, []).append(n.pitch)
        ticks = sorted(onsets)
        density = len(ticks) / max(0.5, t1 - t0)
        chord = len(notes) / len(ticks)
        tops = [max(onsets[t]) for t in ticks]
        leap = sum(abs(a - b) for a, b in zip(tops, tops[1:])) / max(1, len(tops) - 1)
        difficulty = (0.45 * max(0.0, min(1.0, (density - 2.0) / 6.0)) + 0.25 * max(0.0, min(1.0, (chord - 1.0) / 3.0))
                      + 0.30 * max(0.0, min(1.0, (leap - 2.0) / 10.0)))
        sig = frozenset((int(round((n.start - k * seg_ticks) / quarter)), n.pitch % 12) for n in notes)
        seen = sum(1 for other in signatures if len(sig & other) / max(1, len(sig | other)) >= 0.6)
        signatures.append(sig)
        base = rng.betavariate(2.0, 2.0)                                # what they happened to practise
        f = max(0.0, min(1.0, base + min(0.5, 0.25 * seen) - 0.5 * difficulty))
        centres.append((t0 + t1) / 2.0)
        values.append(f)
    smooth = [0.25 * values[max(0, i - 1)] + 0.5 * values[i] + 0.25 * values[min(len(values) - 1, i + 1)] for i in range(len(values))]
    return Fluency(centres, smooth)


def _build_warp(song, tempo_map, fluency: Fluency, amount: float, rng: random.Random) -> Warp:
    """The player's tempo over the piece: in passages they know they push ahead, in new ones they drag, and before some
    chords of a new passage they hesitate. The distance behind or ahead is kept within limits (they catch up in the parts they
    know). Everything - every track, the drums, pedal and program changes - moves together."""
    onset_times = sorted({round(tempo_map.tick_to_sec(n.start), 4) for tr in song.tracks for n in tr.notes if n.end > n.start})
    lo, hi = -KNOW_FAST_LIMIT * amount, KNOW_SLOW_LIMIT * amount
    T, O = [0.0], [0.0]
    offset, prev_t, last_pause = 0.0, 0.0, -9.0
    for t in onset_times:
        dt = t - prev_t
        if dt > 0:
            offset += (0.5 - fluency.at((prev_t + t) / 2.0)) * KNOW_RATE * amount * dt
            offset = max(lo, min(hi, offset))
        T.append(t)
        O.append(offset)
        f = fluency.at(t)
        if f < 0.45 and t - last_pause > 0.25 and rng.random() < PAUSE_CHANCE * (0.45 - f) / 0.45 * min(1.5, amount):
            offset = min(hi, offset + rng.uniform(*PAUSE_SIZE) * (1.0 + (1.0 - f)))        # a hesitation: nothing for a moment
            T.append(t)
            O.append(offset)
            last_pause = t
        prev_t = t
    return Warp(T, O)


def humanize_notes(song, tempo_map, humanize, seed: int = 0) -> Humanized:
    """Work out how a person would play this song: see the module description. Returns a Humanized."""
    out = Humanized()
    cfg = HumanizeConfig.coerce(humanize)
    s = max(0.0, min(MAX_STRENGTH, cfg.strength))
    if s <= 0.0:
        return out
    rng = random.Random(seed)
    global_drift = _Drift(rng, TIMING_DRIFT * s * cfg.drift)
    vel_drift = _Drift(rng, VELOCITY_DRIFT * s * cfg.dynamics, slowest=0.04, fastest=0.25)
    reach = max(1.0, cfg.timing, cfg.drift, cfg.chords, 2.0 if cfg.slips > 0 else 1.0)    # (stronger mixes may move notes further)
    slip_chance = min(0.4, SLIP_CHANCE * s * cfg.slips)

    # when each channel's instrument is set: a note must not start before it
    setup: Dict[int, List[float]] = {}
    for tr in song.tracks:
        for e in tr.events:
            if e.kind == MIDI and e.data:
                st = e.data[0]
                if (st & 0xF0) == 0xC0 or ((st & 0xF0) == 0xB0 and len(e.data) > 1 and e.data[1] in (0, 32)):
                    setup.setdefault(st & 0x0F, []).append(tempo_map.tick_to_sec(e.tick))
    for times in setup.values():
        times.sort()

    # familiarity: some parts are known better than others, which changes the speed and the care taken
    kk = cfg.know * s
    fluency = warp = None
    if kk > 0.0:
        know_rng = random.Random((int(seed) * 1103515245) ^ 0xC0FFEE)
        fluency = _fluency_map(song, tempo_map, know_rng)
        warp = _build_warp(song, tempo_map, fluency, kk, know_rng)
        out.fluency, out.warp, out.know = fluency, warp, kk
        for ch, times in setup.items():
            setup[ch] = sorted(t + warp.start(t) for t in times)

    for ti, tr in enumerate(song.tracks):
        track_drift = _Drift(rng, TIMING_TRACK_DRIFT * s * cfg.drift)
        notes = [(i, n) for i, n in enumerate(tr.notes) if n.end > n.start and (cfg.drums or n.channel != DRUM_CHANNEL)]
        if not notes:
            continue
        written = {i: (tempo_map.tick_to_sec(n.start), tempo_map.tick_to_sec(n.end)) for i, n in notes}
        info = {i: (a + warp.start(a), b + warp.end(b)) for i, (a, b) in written.items()} if warp else written
        notes.sort(key=lambda x: (info[x[0]][0], x[1].pitch))

        # ---- group notes that start together into chords (same channel)
        chords: List[List[Tuple[int, object]]] = []
        by_channel: Dict[int, List[Tuple[int, object]]] = {}
        for i, n in notes:
            cur = by_channel.get(n.channel)
            if cur and info[i][0] - info[cur[0][0]][0] <= CHORD_WINDOW:
                cur.append((i, n))
            else:
                cur = by_channel[n.channel] = [(i, n)]
                chords.append(cur)

        # how close each chord is to the neighbouring ones in the same channel: in a fast passage the notes may only move a
        # fraction of that, so they can never swap places or smear into each other
        starts_by_channel: Dict[int, List[float]] = {}
        for chord in chords:
            starts_by_channel.setdefault(chord[0][1].channel, []).append(info[chord[0][0]][0])

        new: Dict[int, List[float]] = {}                                 # note index -> [start, end, velocity]
        for chord in chords:
            t0 = info[chord[0][0]][0]
            ts = starts_by_channel[chord[0][1].channel]
            j = _last_le(ts, t0 + 1e-9)
            gaps = [ts[j + 1] - t0] if j + 1 < len(ts) else []
            if j is not None and j > 0:
                gaps.append(t0 - ts[j - 1])
            drums = chord[0][1].channel == DRUM_CHANNEL                    # (loosened drums: timing and loudness only, no rolling)
            tscale = 0.6 if drums else 1.0
            # a passage the player does not know is played less cleanly, one they know more cleanly
            loose = 1.0 + (0.5 - fluency.at(written[chord[0][0]][0])) * 1.2 * min(1.0, kk) if fluency else 1.0
            limit = min(TIMING_LIMIT * s * reach, max(0.004 * s, 0.3 * min(gaps))) if gaps else TIMING_LIMIT * s * reach
            shared = (global_drift.at(t0) + track_drift.at(t0)) * tscale * loose + rng.gauss(0.0, TIMING_RANDOM * s * cfg.timing * 0.8 * tscale * loose)
            k = len(chord)
            order = list(range(k))                                       # chord is sorted low -> high
            if k > 1:
                r = rng.random()
                if r < 0.2:
                    order.reverse()                                      # sometimes top-down
                elif r < 0.4:
                    rng.shuffle(order)                                   # sometimes uneven
                gap = rng.uniform(*CHORD_STEP) * s * cfg.chords * loose
                gap = min(gap, CHORD_MAX_ROLL * s * cfg.chords * loose / (k - 1))
                if drums:
                    gap = 0.0
            for pos, (i, n) in enumerate(chord):
                rank = order[pos] if k > 1 else 0
                off = shared + ((rank - (k - 1) / 2.0) * gap if k > 1 else 0.0) + rng.gauss(0.0, TIMING_RANDOM * s * cfg.timing * (0.25 if k > 1 else 1.0) * tscale * loose)
                is_top, is_bottom = pos == k - 1, pos == 0
                if k > 1 and is_top and not drums:
                    off -= MELODY_LEAD * s * cfg.melody * rng.uniform(0.5, 1.5)
                if slip_chance > 0.0 and not drums and rng.random() < slip_chance * loose:             # a stumble: much too early or late
                    off += (1.0 if rng.random() < 0.8 else -1.0) * rng.uniform(*SLIP_SIZE) * min(1.5, s)
                off = max(-limit, min(limit, off))
                start = info[i][0] + off
                # never before the instrument for its channel has been set up
                ready = setup.get(n.channel)
                if ready:
                    j = _last_le(ready, info[i][0] + 1e-9)
                    if j is not None and start < ready[j]:
                        start = ready[j] + 0.0005 + abs(off) * 0.5     # (kept after it, but still spread out)
                start = max(0.0, start)
                # velocity
                v = n.velocity
                if v >= GHOST_VELOCITY:
                    v += (vel_drift.at(info[i][0]) + rng.gauss(0.0, VELOCITY_RANDOM * s * cfg.velocity * loose)) * tscale
                    if k > 1 and not drums:
                        v += (VOICING[0] if is_top else VOICING[1] if is_bottom else VOICING[2]) * s * cfg.melody * rng.uniform(0.6, 1.3)
                    v = max(GHOST_VELOCITY, min(127, int(round(v))))
                # release
                length = info[i][1] - info[i][0]
                end = info[i][1] + off + rng.gauss(0.0, RELEASE_RANDOM * s * cfg.release * tscale * loose)
                floor = min(length, MIN_LENGTH)
                end = max(end, start + floor)
                new[i] = [start, end, v]

        # ---- a chord (or note) is let go before the next one in the same part is played, wherever the music has them one
        # after the other: you cannot still be playing the previous chord while you play the next. (Where the music itself
        # overlaps them - written legato - that is left alone.)
        clusters: Dict[int, List[Tuple[float, float]]] = {}              # channel -> [(written start, earliest actual start)]
        for chord in chords:
            ch = chord[0][1].channel
            clusters.setdefault(ch, []).append((info[chord[0][0]][0], min(new[i][0] for i, _ in chord)))
        cluster_starts = {ch: [w for w, _ in arr] for ch, arr in clusters.items()}
        for i, n in notes:
            arr = clusters[n.channel]
            j = bisect.bisect_left(cluster_starts[n.channel], info[i][1] - 1e-6)     # first chord that starts as this note ends or later
            if j < len(arr):
                bound = arr[j][1] - NEXT_GAP
                if new[i][1] > bound:
                    new[i][1] = max(bound, new[i][0] + 0.005)

        # ---- striking the same key again: a real finger has to let the key come back up first, and a fast repeat comes
        # out a little later and lighter. (The file just ends one note and starts the next in the same instant.)
        rep_rng = random.Random((int(seed) * 40503) ^ 0x5BD1E995 ^ (ti * 7919))      # (its own random numbers)
        last_of: Dict[Tuple[int, int], int] = {}
        for i, n in notes:                                               # (sorted by written start)
            key = (n.channel, n.pitch)
            prev = last_of.get(key)
            if prev is not None:
                p_start, p_end, _ = new[prev]
                written_overlap = info[prev][1] > info[i][0] + 1e-9      # (the file itself overlaps them: leave that alone)
                if not written_overlap:
                    if new[i][0] < p_start + RESTRIKE_MIN_INTERVAL:             # (no finger strikes a key twice within a few ms)
                        new[i][0] = p_start + RESTRIKE_MIN_INTERVAL
                    gap = OVERLAP_GAP
                    if cfg.repeat > 0.0:
                        interval = new[i][0] - p_start
                        fast = max(0.0, min(1.0, (RESTRIKE_FAST - interval) / (RESTRIKE_FAST - RESTRIKE_LIMIT)))
                        amount = s * cfg.repeat * fast
                        if fast > 0.0:
                            out.fast_repeats[(ti, i)] = fast
                            new[i][0] += min(rep_rng.uniform(0.3, 1.0) * RESTRIKE_LAG * amount, 0.25 * interval)   # the re-strike comes a little late
                            if new[i][2] >= GHOST_VELOCITY:
                                soft = 1.0 - RESTRIKE_SOFT * amount * rep_rng.uniform(0.5, 1.2)         # ...and a little light
                                new[i][2] = max(GHOST_VELOCITY, int(new[i][2] * max(0.4, soft)))
                            new[i][1] = max(new[i][1], new[i][0] + min(info[i][1] - info[i][0], MIN_LENGTH))
                        wanted = min(RESTRIKE_GAP * s * cfg.repeat, RESTRIKE_MAX) * rep_rng.uniform(0.6, 1.4)     # (never exactly the same twice)
                        gap = max(OVERLAP_GAP, min(wanted, RESTRIKE_SHARE * (new[i][0] - p_start)))
                    start_i = new[i][0]
                    if p_end > start_i - gap:
                        new[prev][1] = max(p_start + 0.01, start_i - gap)
            last_of[key] = i
        for i, (a, b, v) in new.items():
            out[(ti, i)] = (a, max(b, a + 0.005), v)
    if cfg.fat > 0.0 or cfg.wrong > 0.0 or cfg.missed > 0.0:
        _make_mistakes(song, tempo_map, cfg, s, seed, out)
    return out


def repeat_depths(song, tempo_map) -> Dict[Tuple[int, int], int]:
    """How settled into a repeated figure each note is: (track, note index) -> the number of times in a row the same chord (or
    note) has just been played before it. 0 = something new. A figure that repeats every two or three chords counts too (A B A B
    ... gets deeper each time round). Notes are grouped into chords per track and channel, as when nudging."""
    depth: Dict[Tuple[int, int], int] = {}
    parts: Dict[Tuple[int, int], List[Tuple[float, int, int]]] = {}
    for ti, tr in enumerate(song.tracks):
        for i, n in enumerate(tr.notes):
            if n.end > n.start and n.channel != DRUM_CHANNEL:
                parts.setdefault((ti, n.channel), []).append((tempo_map.tick_to_sec(n.start), n.pitch, i))
    for (ti, _ch), lst in parts.items():
        lst.sort()
        chords: List[Tuple[float, set, List[int]]] = []
        for t, pitch, i in lst:
            if chords and t - chords[-1][0] <= CHORD_WINDOW:
                chords[-1][1].add(pitch)
                chords[-1][2].append(i)
            else:
                chords.append((t, {pitch}, [i]))
        runs = {1: [0] * len(chords), 2: [0] * len(chords), 3: [0] * len(chords)}
        for c, (_t, pitches, members) in enumerate(chords):
            best = 0
            for period in (1, 2, 3):
                if c - period >= 0 and pitches == chords[c - period][1]:
                    runs[period][c] = (runs[period][c - 1] if c > 0 else 0) + 1
                    best = max(best, runs[period][c])
            for i in members:
                depth[(ti, i)] = best
    return depth


def _make_mistakes(song, tempo_map, cfg: HumanizeConfig, s: float, seed: int, out: Humanized) -> None:
    """Fat fingers, wrong notes and missed notes, added to *out* (using its own random numbers, so the nudging above is the
    same whether or not mistakes are switched on).

    * **Missed:** the note is not played at all.
    * **Wrong:** a neighbouring key is played instead of the right one (mostly the next key, sometimes two or more away).
    * **Fat finger:** the right note is played and the key next to it is struck too - a little softer, a little off in time
      and let go sooner, like a finger grazing it.
    Fat fingers get rarer the more times in a row the same chord (or note, or a short figure) has just been played: the
    hands settle into it (see repeat_depths). A mistake is never made where it would put a key down that is already sounding,
    and drums and ghost notes are left alone (a note shorter than a tap can only be missed)."""
    rng = random.Random((int(seed) * 2654435761) ^ 0x9E3779B1)
    depth = repeat_depths(song, tempo_map) if p_fat_enabled(cfg) else {}
    p_miss = min(0.3, MISS_CHANCE * s * cfg.missed)
    p_wrong = min(0.3, WRONG_CHANCE * s * cfg.wrong)
    p_fat = min(0.3, FAT_CHANCE * s * cfg.fat)

    # where every key is down (so a mistake never lands on a sounding key): (channel, pitch) -> [(start, end)] sorted by start
    keys: Dict[Tuple[int, int], List[Tuple[float, float]]] = {}
    longest: Dict[Tuple[int, int], float] = {}
    candidates = []
    for ti, tr in enumerate(song.tracks):
        for i, n in enumerate(tr.notes):
            t = out.get((ti, i))
            if t is None:
                continue
            keys.setdefault((n.channel, n.pitch), []).append((t[0], t[1]))
            longest[(n.channel, n.pitch)] = max(longest.get((n.channel, n.pitch), 0.0), t[1] - t[0])
            if n.channel != DRUM_CHANNEL and n.velocity >= GHOST_VELOCITY and t[1] - t[0] >= 0.02:
                candidates.append((t[0], ti, i, n, t))
    for lst in keys.values():
        lst.sort()
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))

    def free(ch: int, pitch: int, a: float, b: float) -> bool:
        if not 0 <= pitch <= 127:
            return False
        lst = keys.get((ch, pitch))
        if not lst:
            return True
        pad = 0.03
        k = bisect.bisect_right(lst, (b + pad, 1e18))
        reach_back = a - pad - longest[(ch, pitch)] - 0.01
        while k > 0:
            k -= 1
            st, en = lst[k]
            if st < reach_back:
                break
            if st < b + pad and en > a - pad:
                return False
        return True

    def occupy(ch: int, pitch: int, a: float, b: float) -> None:
        lst = keys.setdefault((ch, pitch), [])
        bisect.insort(lst, (a, b))
        longest[(ch, pitch)] = max(longest.get((ch, pitch), 0.0), b - a)

    def neighbour_order(pitch: int, far: bool):
        """Candidate neighbouring pitches, the likeliest first."""
        steps = [1, 2, 3, 4] if far else [1, 2]
        weights = [0.45, 0.35, 0.12, 0.08] if far else [0.7, 0.3]
        order = []
        pool = list(zip(steps, weights))
        while pool:
            total = sum(w for _, w in pool)
            r = rng.uniform(0.0, total)
            for idx, (st, w) in enumerate(pool):
                r -= w
                if r <= 0 or idx == len(pool) - 1:
                    order.append(st)
                    pool.pop(idx)
                    break
        direction = 1 if rng.random() < 0.5 else -1
        return [pitch + direction * st for st in order] + [pitch - direction * st for st in order]

    for a, ti, i, n, (start, end, vel) in candidates:
        r = rng.random()
        # in a passage the player knows well mistakes are rare, in one they do not know they are common
        scale = 1.0
        if out.fluency is not None:
            scale = max(0.1, 1.0 + (0.5 - out.fluency.at(tempo_map.tick_to_sec(n.start))) * 1.8 * min(1.0, out.know))
        miss = min(0.6, p_miss * scale * (1.0 + FAST_REPEAT_MISS * out.fast_repeats.get((ti, i), 0.0)))   # (fast re-strikes: hard to get right)
        wrong_p, fat_p = p_wrong * scale, p_fat * scale
        if r < miss:
            out.dropped.add((ti, i))
            continue
        r -= miss
        if end - start < 0.06:                       # (a note this short can be missed, but not fumbled or grazed: nothing to graze)
            continue
        if r < wrong_p:
            for p2 in neighbour_order(n.pitch, far=True):
                if free(n.channel, p2, start, end):
                    out.repitch[(ti, i)] = p2
                    occupy(n.channel, p2, start, end)
                    break
            continue
        r -= wrong_p
        settled = depth.get((ti, i), 0)
        if r < fat_p * max(REPEAT_FLOOR, 1.0 / (1.0 + REPEAT_DECAY * settled)):
            for p2 in neighbour_order(n.pitch, far=False):
                a2 = max(0.0, start + rng.uniform(-0.004, 0.008))
                b2 = min(end, a2 + rng.uniform(0.06, 0.22))
                if b2 - a2 >= 0.03 and free(n.channel, p2, a2, b2):
                    v2 = max(GHOST_VELOCITY, int(vel * rng.uniform(0.45, 0.8)))
                    out.extras.append((ti, n.channel, p2, a2, b2, v2, n.start))
                    occupy(n.channel, p2, a2, b2)
                    break


def p_fat_enabled(cfg: HumanizeConfig) -> bool:
    return cfg.fat > 0.0


def _last_le(sorted_list: List[float], value: float):
    lo, hi = 0, len(sorted_list)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_list[mid] <= value:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1 if lo > 0 else None
