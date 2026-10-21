"""Piano keyboard that lights up the notes being played, coloured by MIDI channel,
with configurable physics-driven particle effects (sparks, embers, bubbles, confetti, stars, fireflies, lightning,
steam, fireworks)."""
from __future__ import annotations

import json
import math
import random
import time
from dataclasses import asdict, dataclass, fields
from typing import Dict, List

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen, QPixmap, QPolygonF, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..gm import BLACK_KEYS, CHANNEL_COLORS
from . import theme

LOW, HIGH = 21, 108          # A0 .. C8, the 88 keys of a piano
KEYS_H = 76                  # height of the keyboard itself
MAX_PARTICLES = 900            # the default limit on particles alive at once (the settings allow MIN_LIMIT..MAX_LIMIT)
MIN_LIMIT, MAX_LIMIT = 100, 4000
MAX_LAUNCH = 2400            # px/s: the fastest any particle is ever launched
MAX_HEIGHT = 600             # the tallest effect area the settings allow (px)
BLACK_KEY_W = 0.62           # a black key is this share of a white key's width
EMIT_MARGIN = 0.08           # particles start this far in from each edge of the key (share of its width)
POP_TIME = 0.24              # how long a bubble's pop animation lasts (s)
SHIMMER_TIME = 0.55          # how long a star takes to shimmer away (s)
TRAIL_LEN = 9                # points kept behind a particle for its trail
SPRITE = 128                 # pixels across the pre-rendered soft shapes
SPRITE_RECT = QRectF(0, 0, SPRITE, SPRITE)
GLOW_REACH = 58.0             # how far (px) the held-note glow's spikes (when enabled) rise above a key, whatever the effect height
SOFT_REACH = 44.0             # how far the held-note glow's soft light reaches above a key (the old, round glow, smaller)
SOFT_WIDTH = 0.95             # ...and how far it spreads sideways, in key widths each side
SPIKES_ENABLED = False        # the spiky rays over the soft glow are switched off for now (the code is kept; set True to bring them back)
SPIKE_ALPHA = 0.55            # the spikes are drawn at this share of full strength
SPIKE_FRAMES = 4              # the glow's spikes flicker between this many pre-drawn patterns...
SPIKE_LEVELS = 4              # ...for each of this many loudness levels (quiet notes get a few short rays, loud ones many long ones)
SPIKE_SIZE = 96               # pixels across a spike picture
SPRITE_CAPS = {"spike": 400, "halo": 256, "puff": 256, "glow": 128}      # most pictures kept per kind (each kind is cleared on its own)
LEAVE_TIME = 0.5             # how long a particle sent away to make room takes to fade out (slowly at first, then quickly)
HARD_CEILING = 2.5           # particles on screen (including those leaving) are never allowed beyond this many times the limit
LEAVE_SPREAD = 0.6           # a crowd sent away together leaves over this long: a few at first, then faster and faster
FLASH_TIME = 0.09            # a note shorter than this still lights its key this long
MAX_LAG = 0.12               # a note-on that arrives this late is caught up (never further) so effects keep pace
SLOW_PAINT = 0.011           # a paint slower than this (s, smoothed) makes the effects shed detail ...
FAST_PAINT = 0.004           # ... and one faster than this lets it come back
TOP_EDGE = 6.0               # a star this close to the top of the effect area shimmers away


def _is_black(pitch: int) -> bool:
    return pitch % 12 in BLACK_KEYS


WHITE_KEYS = sum(1 for _p in range(LOW, HIGH + 1) if not _is_black(_p))        # 52 white keys


def _hscale(zone: float) -> float:
    """How much taller than the default (110 px) the effect area is: 1 up to 110 px, then up to 6x."""
    return max(1.0, min(6.0, zone / 110.0))


# --------------------------------------------------------------------- config
KINDS = {
    # Every particle is simulated with forces (px, seconds):
    #   gravity: downward pull          buoy: upward lift (hot air / air in a bubble)
    #   drag: velocity loss per second  qdrag: air resistance that grows with speed (fast = braked hard, so a
    #   fast launch slows quickly and settles to a gentle terminal speed)
    #   flutter: sideways swaying proportional to speed (leaf-like)    floor/rest/fric: bounce off the key tops
    #   (rest = bounciness, fric = sideways grip per bounce)
    #   jitter: meandering force (px/s^2) so each particle wanders on its own path (jitter_speed: it fades
    #   out while the particle is moving faster than this, so only the slow part of its flight wanders)     linger: chance a particle hangs around much longer
    #   scale_h: lift and launch speed grow with the effect height (relative to 110 px, up to 6x), so the particle
    #   always travels a similar share of the area however tall it is
    #   fade_top: share of the effect height, at the top, over which a particle fades out as it rises into it
    #   flicker: brightness flicker      pop: bubbles rise to a random height, then burst
    #   burst: particles per new note    hold: chance per frame a held key sheds one
    #   speed/ttl/size: (min, max)       spread: launch cone half-angle (rad)    shape: how it is drawn
    #   rest_time: (min, max) seconds a landed particle stays before it goes     fade: seconds it takes to fade out
    #   (without it a particle fades steadily over its whole life)
    #   clumps: particles are launched in clumps that share a launch direction, so different clumps fan out along
    #   different paths.  renew: seconds a held key keeps feeding the same clump;
    #   tight: launch-angle spread inside a clump (rad)
    #   rect_chance: share of particles drawn as long tumbling rectangles (like the confetti) instead of circles
    #   rise_var: (min, max) each particle's own lift factor, so every one rises at its own speed
    #   speed_wobble: share by which a particle's lift swells and fades over its life
    #   pop_chance / pop_burst: share of sparks that pop at a random moment, and how many small sparks they burst into
    #   shimmer: stars shimmer away (flicker, shrink, shed glitter) when they reach the top of the effect area or at a
    #   random point in their life, instead of just fading
    #   tempo: runs this effect's whole motion faster (1.25 = 25% faster; the paths and heights stay the same)
    #   cone: keeps sideways speed proportional to rising speed, so the stream opens up like a cone
    #   apex: (min, max) share of the effect height a particle peaks at - its launch speed is worked out to
    #         hit that, so it always goes high without leaving the area (replaces speed). In a taller area the air
    #         resistance is gentler (and gravity a little stronger), so the flight scales up with the area instead
    #         of needing an absurd launch speed
    "sparks":   dict(label="Sparks",   burst=10, hold=0.6, speed=(140, 340), ttl=(1.1, 1.9), size=(1.6, 3.4), spread=0.7,
                     gravity=420, buoy=0, drag=0.35, flutter=0.0, pop_chance=0.3, pop_burst=(4, 7), rect_chance=0.35,
                     floor=True, rest=0.55, fric=0.25, shape="dot"),
    "embers":   dict(label="Embers",   burst=5,  hold=0.4,  speed=(15, 150), ttl=(1.6, 3.0), size=(2.0, 4.2), spread=0.65,
                     gravity=0, buoy=95, drag=0.65, scale_h=True, flutter=0.0, rise_var=(0.4, 1.9), speed_wobble=0.35, rect_chance=0.5,
                     jitter=60.0, linger=0.3, flicker=0.4, fade_top=0.35,
                     clumps=dict(renew=(0.35, 0.7), tight=0.12),
                     floor=False, rest=0.0, fric=0.0, shape="dot"),
    "bubbles":  dict(label="Bubbles",  burst=3,  hold=0.1875, speed=(10, 45), ttl=(6.0, 8.0), size=(3.0, 7.0), spread=0.55,
                     gravity=0, buoy=55, drag=1.2, flutter=0.15, cone=12.0, pop=True, tempo=1.25,
                     floor=False, rest=0.0, fric=0.0, shape="ring"),
    "confetti": dict(label="Confetti", burst=12, hold=0.5,  apex=(0.55, 0.95), ttl=(8.0, 10.0), size=(2.5, 4.5), spread=0.45,
                     gravity=130, buoy=0, drag=0.25, qdrag=0.011, rest_time=(1.0, 2.0), fade=0.6, flutter=1.0,
                     jitter=110.0, jitter_speed=170.0, tempo=1.25,
                     floor=True, rest=0.2, fric=0.55, shape="rect"),
    "stars":    dict(label="Stars",    burst=5,  hold=0.3,  speed=(60, 150), ttl=(2.2, 3.6), size=(3.0, 6.0), spread=0.5,
                     gravity=20, buoy=25, drag=0.3, flutter=0.0, shimmer=True,
                     floor=True, rest=0.6, fric=0.1, shape="star"),
    # fireflies: slow glowing dots that wander on their own and blink on and off (pulse). tether: how firmly each is pulled
    # back towards where it started; roam: the furthest (px) it can ever get from there sideways
    "fireflies": dict(label="Fireflies", burst=3, hold=0.12, speed=(6, 26), ttl=(4.0, 7.0), size=(2.0, 3.6), spread=1.3,
                      gravity=0, buoy=12, drag=1.1, flutter=0.0, jitter=62.0, pulse=True, halo=1.4, fade_top=0.25, tether=2.2, roam=55.0,
                      floor=True, rest=0.3, fric=0.5, shape="dot"),
    # lightning: a jagged bolt flashes up from the key and is gone in a fraction of a second (bolt)
    "lightning": dict(label="Lightning", burst=3, hold=0.07, speed=(0, 0), ttl=(0.16, 0.3), size=(1.4, 2.4), spread=0.0,
                      gravity=0, buoy=0, drag=0, flutter=0.0, bolt=True,
                      floor=False, rest=0.0, fric=0.0, shape="bolt"),
    # steam: big soft puffs that rise, swell (grow) and dissolve
    "steam":     dict(label="Steam", burst=2, hold=0.22, speed=(25, 70), ttl=(2.0, 3.4), size=(7.0, 13.0), spread=0.45,
                      gravity=0, buoy=75, drag=0.9, scale_h=True, flutter=0.0, jitter=45.0, grow=2.2, fade_top=0.4, key_color=True,
                      floor=False, rest=0.0, fric=0.0, shape="puff"),
    # fireworks: a rocket climbs to the top of its flight, then bursts into a ring of sparks that fall (rocket)
    "fireworks": dict(label="Fireworks", burst=1, hold=0.015, speed=(0, 0), ttl=(5.0, 5.0), size=(2.2, 3.2), spread=0.22,
                      gravity=260, buoy=0, drag=0.12, flutter=0.0, rocket=True, flicker=0.3, pop_burst=(3, 5),
                      floor=True, rest=0.3, fric=0.3, shape="dot"),
}
PATTERNS = {"random": "Random", "peony": "Peony (round burst)", "ring": "Ring", "heart": "Heart", "star": "Star",
            "spiral": "Spiral", "double": "Double ring", "willow": "Willow (drooping trails)", "crackle": "Crackle"}
PATTERN_NAMES = [n for n in PATTERNS if n != "random"]
SHAPE_DF = 14.0              # shaped bursts (heart, star, spiral, double ring) brake hard so the shape holds, then drift down
COLOR_MODES = {"channel": "Channel colour", "rainbow": "Rainbow", "custom": "One colour"}


@dataclass
class FxConfig:
    enabled: bool = True       # master switch: off = no effects at all
    docked: bool = False       # also show them while the piano is docked in the window (by default: only when popped out)
    kind: str = "sparks"
    amount: float = 1.0        # multiplier: how many particles
    size: float = 1.0
    speed: float = 1.0
    life: float = 1.0          # multiplier: how long they last
    gravity: int = 0           # extra pull: negative = floats up, positive = falls (-100..100)
    bounce: float = 1.0        # multiplier on how bouncy the keys are
    glow: bool = True          # additive glow around each particle
    glow_amount: float = 1.0   # how strong the glows are (particle glow, held-note glow, lightning glow): 0 = none, 3 = very bright
    color_mode: str = "channel"
    color: str = "#ffffff"     # used by color_mode == "custom"
    height: int = 110          # headroom above the keys (px), up to MAX_HEIGHT
    max_particles: int = MAX_PARTICLES   # the most particles alive at once; when full, the oldest make way for new notes
    held_glow: bool = False    # a soft glow above a key for as long as it is held
    pitch_color: bool = False  # colour by pitch (low = red ... high = violet) instead of the colour setting
    velocity: bool = False     # harder notes make more, bigger, faster / longer effects
    velocity_amount: float = 1.0   # how strongly: 0 = no difference, 1 = normal, 3 = extreme
    trails: bool = False       # particles leave a short fading streak behind them
    pattern: str = "random"    # fireworks: the burst shape ("random" = a different one each time)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text) -> "FxConfig":
        cfg = cls()
        try:
            data = json.loads(text) if text else {}
        except (TypeError, ValueError):
            return cfg
        if not isinstance(data, dict):
            return cfg
        for f in fields(cls):
            if f.name not in data:
                continue
            try:
                v = type(getattr(cfg, f.name))(data[f.name])
            except (TypeError, ValueError):
                continue
            setattr(cfg, f.name, v)
        if cfg.kind not in KINDS:                  # e.g. a removed effect: fall back to the default
            cfg.kind = "sparks"
        if cfg.color_mode not in COLOR_MODES:
            cfg.color_mode = "channel"
        if cfg.pattern not in PATTERNS:
            cfg.pattern = "random"
        if not QColor(cfg.color).isValid():
            cfg.color = "#ffffff"
        cfg.amount = max(0.0, min(4.0, cfg.amount))
        cfg.size = max(0.3, min(4.0, cfg.size))
        cfg.speed = max(0.2, min(4.0, cfg.speed))
        cfg.life = max(0.2, min(4.0, cfg.life))
        cfg.gravity = max(-100, min(100, cfg.gravity))
        cfg.bounce = max(0.0, min(1.5, cfg.bounce))
        cfg.glow_amount = max(0.0, min(3.0, cfg.glow_amount))
        cfg.velocity_amount = max(0.0, min(3.0, cfg.velocity_amount))
        cfg.max_particles = max(MIN_LIMIT, min(MAX_LIMIT, cfg.max_particles))
        cfg.height = max(20, min(MAX_HEIGHT, cfg.height))
        return cfg


class _Clump:
    """A group of particles launched together in the same direction."""
    __slots__ = ("angle", "expire")


class _Particle:
    __slots__ = ("x", "y", "vx", "vy", "age", "ttl", "size", "color", "rot", "spin", "phase", "bf", "df", "pop", "popt", "slope", "landed", "clump", "rect", "fuse", "shim", "shim_at", "pts", "trail", "rocket", "power", "ox", "gf", "streak", "sprite", "out", "leave", "wait")

    def __init__(self, x, y, vx, vy, ttl, size, color, rot, spin, phase):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.age, self.ttl, self.size, self.color = 0.0, ttl, size, color
        self.rot, self.spin, self.phase = rot, spin, phase
        self.fuse = 0.0                       # sparks: age at which it pops (0 = never)
        self.shim = -1.0                      # stars: seconds into the shimmer (-1 = not yet, -2 = a plain speck)
        self.shim_at = 0.0                    # stars: age at which it shimmers away (it also does at the top edge)
        self.ox = x                           # where it started (fireflies stay within reach of it)
        self.gf = 1.0                         # scales gravity (fireworks: shaped bursts fall gently)
        self.out = False                      # being sent away to make room (it leaves in its effect's own way)
        self.wait = 0.0                       # seconds until it actually starts leaving (a crowd leaves one after another)
        self.leave = -1.0                     # seconds into a plain fade-out (-1 = not fading)
        self.sprite = None                    # lightning: the bolt, pre-rendered
        self.streak = False                   # always draws a trail (willow fireworks), whatever the Trails setting
        self.pts = None                       # lightning: the bolt's polylines (a bolt has no motion)
        self.trail = None                     # recent positions, for the trail
        self.rocket = False                   # fireworks: climbs, then bursts at the top of its flight
        self.power = 1.0                      # velocity scaling: how hard the note was struck (1 = normal)
        self.rect = False                     # drawn as a long rectangle instead of a circle
        self.clump = None                     # the _Clump it travels with (embers)
        self.landed = False                   # has come to rest on the keys
        self.slope = 0.0                      # cone kinds: sideways speed per unit of rising speed
        self.bf = 1.0                         # scales buoyancy (lingering embers barely rise)
        self.df = 1.0                         # scales drag
        self.pop = 0.0                        # bubbles: height (y) at which this one bursts
        self.popt = -1.0                      # bubbles: seconds into the pop animation, -1 = not popping


class PianoStrip(QWidget):
    heightChanged = Signal(int)                # the strip's height changed (the popped-out window follows it)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip("Notes being played (colour = MIDI channel)")
        self.fx = FxConfig()
        self._popped = False                   # the piano is in its own window (effects show only then, unless set otherwise)
        self._active: Dict[int, int] = {}      # pitch -> channel
        self._whites = [p for p in range(LOW, HIGH + 1) if not _is_black(p)]
        self._white_index = {pitch: i for i, pitch in enumerate(self._whites)}
        self._particles: List[_Particle] = []
        self._clump_cur: Dict[int, _Clump] = {}   # the clump each held key is currently feeding
        self._glow: Dict[int, list] = {}       # held-note glow: pitch -> [level 0..1, colour, strength, loudness 0..1]
        self._sprites: Dict[tuple, QPixmap] = {}   # pre-rendered soft shapes (steam puffs, glows), by colour
        self._sprite_n: Dict[str, int] = {}     # how many of each kind are in it
        self._paint_ema = 0.0                  # smoothed time a paint takes (s)
        self._leaving = 0                      # how many particles are on their way out (they do not count towards the limit)
        self._quality = 0                      # 0 full detail, 1 no trails, 2 also half the particles per note (glow is never shed)
        self._q_wait = 0                       # frames to wait before the quality may change again
        self.event_source = None               # callable -> new note-ons [(time, pitch, channel, velocity)] (set by the main window)
        self._flash: Dict[int, list] = {}      # pitch -> [channel, expire time]: a key that only just sounded and is already gone
        self.velocity_of = None                # callable pitch -> MIDI velocity (set by the main window)
        self._t = 0.0
        self._demo: Dict[int, int] = {}
        self._notes: Dict[int, int] = {}      # what the engine reports (the demo chord is separate)
        self._last = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._demo_timer = QTimer(self)
        self._demo_timer.setSingleShot(True)
        self._demo_timer.timeout.connect(self._end_demo)
        self._apply_height()

    # ------------------------------------------------------------ settings
    def effects_on(self) -> bool:
        """Effects show when switched on, and the piano is popped out (or the setting allows them while docked)."""
        return self.fx.enabled and (self._popped or self.fx.docked)

    def set_fx(self, cfg: FxConfig):
        if cfg.kind != self.fx.kind:               # a different effect: the old one's particles follow other physics - clear them
            self._particles.clear()
            self._leaving = 0
            self._clump_cur.clear()
        self.fx = cfg
        self._refresh()

    def set_popped(self, popped: bool):
        """Called when the piano leaves / returns to the main window."""
        self._popped = bool(popped)
        self._refresh()

    def _refresh(self):
        if not self.effects_on():
            self._particles.clear()
            self._clump_cur.clear()
            self._glow.clear()
            self._flash.clear()
            self._leaving = 0
            self._timer.stop()
        else:
            self._ensure_timer()
        self._apply_height()                   # no particle area while the effects are off: the strip stays compact
        self.update()

    def _apply_height(self):
        h = KEYS_H + (self.fx.height if self.effects_on() else 0)
        if h != self.height():
            self.setFixedHeight(h)
            self.heightChanged.emit(h)

    # ------------------------------------------------------------ geometry
    def _key_top(self) -> float:
        return self.height() - KEYS_H

    def _key_x(self, pitch: int) -> float:
        """Horizontal centre of a key."""
        kw = self.width() / len(self._whites)
        if not _is_black(pitch):
            return (self._white_index[pitch] + 0.5) * kw
        left = self._white_index.get(pitch - 1)
        return (left + 1) * kw if left is not None else -1.0

    def _key_span(self, pitch: int):
        """(left, right) edges of a key in widget pixels - the width particles are spread across when it plays:
        a white key's full width, a (narrower) black key's width. None if the key is not on the keyboard."""
        kw = self.width() / len(self._whites)
        if not _is_black(pitch):
            i = self._white_index.get(pitch)
            return None if i is None else (i * kw, (i + 1) * kw)
        left = self._white_index.get(pitch - 1)
        if left is None:
            return None
        c, half = (left + 1) * kw, kw * BLACK_KEY_W / 2
        return c - half, c + half

    # ------------------------------------------------------------- input
    def set_active(self, active: Dict[int, int]):
        self._set_notes(dict(active))

    def _set_notes(self, notes: Dict[int, int]):
        shown = {**{p: f[0] for p, f in self._flash.items()}, **notes, **self._demo}
        if shown == self._active:
            return
        if self.effects_on():
            for pitch, ch in shown.items():
                if pitch not in self._active and LOW <= pitch <= HIGH:
                    if pitch not in self._flash:                              # (a flash pitch was already hit by its own event)
                        self._glow_hit(pitch, ch)
                    if pitch in self._demo or (self.event_source is None and pitch not in self._flash):   # (real notes burst from their events)
                        self._burst(pitch, ch, round(KINDS[self.fx.kind]["burst"] * self.fx.amount))
        self._notes = notes
        self._active = shown
        self._ensure_timer()
        self.update()

    def note_events(self, events):
        """New note-ons, each with the time it started. Every one makes its own burst right away - however short the note,
        and however many arrive together - and a late one is advanced by how late it is, so a stalled frame does not turn
        into a clump of particles all appearing at once."""
        if not events:
            return
        now = time.perf_counter()
        if not self.effects_on():
            return
        flash_changed = False
        for t, pitch, ch, vel in events:
            lag = now - t
            if lag > 1.0 or not LOW <= pitch <= HIGH:                  # stale (e.g. from while the effects were hidden)
                continue
            if pitch not in self._notes and pitch not in self._demo:   # gone already (or about to be): keep the key lit a moment
                self._flash[pitch] = [ch, now + FLASH_TIME]
                flash_changed = True
            self._glow_hit(pitch, ch, vel)
            self._burst(pitch, ch, round(KINDS[self.fx.kind]["burst"] * self.fx.amount), vel=vel, lag=min(MAX_LAG, max(0.0, lag)))
        if flash_changed:
            self._set_notes(self._notes)
        self._ensure_timer()

    def _drain_events(self):
        if self.event_source is not None:
            try:
                self.note_events(self.event_source())
            except Exception:
                pass

    def _ensure_timer(self):
        if self.effects_on() and (self._active or self._particles or self._glow or self.event_source is not None) and not self._timer.isActive():
            self._last = time.perf_counter()
            self._timer.start()

    def demo(self, seconds: float = 1.6):
        """Light a chord so the effect can be previewed without playing a song."""
        self._demo = {48: 0, 55: 0, 60: 1, 64: 1, 67: 1, 72: 4, 76: 5}
        self._set_notes(dict(self._notes))
        self._demo_timer.start(int(seconds * 1000))

    def _end_demo(self):
        self._demo = {}
        self._set_notes(dict(self._notes))

    # ---------------------------------------------------------- particles
    def _base_color(self, pitch: int, ch: int) -> QColor:
        """The colour a note's effects start from (no randomness)."""
        fx = self.fx
        if fx.pitch_color:                         # low = red ... high = violet, whatever the colour setting says
            return QColor.fromHsvF(0.8 * max(0.0, min(1.0, (pitch - LOW) / (HIGH - LOW))), 0.85, 1.0)
        if fx.color_mode == "custom":
            return QColor(fx.color)
        if fx.color_mode == "rainbow":
            return QColor.fromHsvF(((pitch - LOW) / 40.0) % 1.0, 0.75, 1.0)
        return QColor(CHANNEL_COLORS[ch])

    def _color_for(self, pitch: int, ch: int) -> QColor:
        if self.fx.color_mode == "rainbow" and not self.fx.pitch_color:
            return QColor.fromHsvF(((pitch - LOW) / 40.0 + random.uniform(-0.04, 0.04)) % 1.0, 0.75, 1.0)
        return self._base_color(pitch, ch)

    def _key_color(self, pitch: int, ch: int) -> QColor:
        """The colour a lit key is drawn in."""
        if self.fx.pitch_color and self.effects_on():
            return self._base_color(pitch, ch)
        return QColor(CHANNEL_COLORS[ch])

    def _vel_norm(self, pitch: int, vel=None) -> float:
        """How hard a note was struck, relative to a normal one (1.0 at velocity 90, about 0.2 for a whisper, 1.7 at the top);
        1.0 when scaling is off."""
        if not self.fx.velocity:
            return 1.0
        v = vel
        if v is None:
            try:
                v = self.velocity_of(pitch) if self.velocity_of else 90
            except Exception:
                v = 90
        vn = max(0.2, min(1.8, (v / 90.0) ** 1.5))
        return max(0.1, min(3.0, 1.0 + (vn - 1.0) * self.fx.velocity_amount))     # (the slider stretches it around 'normal')

    def _loudness(self, pitch: int, vel=None) -> float:
        """How loud a note is, 0..1 (its MIDI velocity), for the glow's spikes. Unlike velocity scaling this always applies;
        the Velocity strength slider only exaggerates or flattens it when scaling is on."""
        v = vel
        if v is None:
            try:
                v = self.velocity_of(pitch) if self.velocity_of else 90
            except Exception:
                v = 90
        t = max(0.0, min(1.0, v / 127.0))
        if self.fx.velocity:
            t = max(0.0, min(1.0, 0.5 + (t - 0.5) * self.fx.velocity_amount))
        return t

    def _glow_hit(self, pitch: int, ch: int, vel=None):
        """A key goes down: its glow takes this note's colour and strength (and keeps any level it still has)."""
        if not self.fx.held_glow:
            return None
        g = self._glow.get(pitch)
        if g is None:
            g = self._glow[pitch] = [0.0, QColor(), 1.0, 0.7]
        g[1], g[2], g[3] = self._base_color(pitch, ch), self._vel_norm(pitch, vel), self._loudness(pitch, vel)
        return g

    def _burst(self, pitch: int, ch: int, n: int, hold: bool = False, vel=None, lag: float = 0.0):
        span = self._key_span(pitch)
        limit = self.fx.max_particles if self._quality < 2 else self.fx.max_particles // 3
        if n <= 0 or span is None or not self.effects_on():
            return
        over = len(self._particles) - min(self._leaving, len(self._particles)) + n - limit
        if over > 0:
            if hold:                                                   # a held key just waits for room...
                return
            self._make_room(over, limit)                               # ...but a new note always gets its burst: the oldest go
        lo, hi = span
        margin = (hi - lo) * EMIT_MARGIN
        lo, hi = lo + margin, hi - margin
        k, fx = KINDS[self.fx.kind], self.fx
        base = self._color_for(pitch, ch)
        y = self._key_top() - 1
        vn = self._vel_norm(pitch, vel)                                # harder notes: more, bigger, faster
        if not hold and fx.velocity:
            n = max(1, round(n * vn))
        if self._quality >= 2 and n > 1:                               # struggling: fewer particles per note
            n = max(1, n // 2)
        if k.get("bolt"):
            for _ in range(n + (random.randint(0, 1) if hold else 0)):
                self._bolt(lo, hi, y, base, hold, vn, lag)
            return
        for _ in range(n):
            ang = random.uniform(-math.pi / 2 - k["spread"], -math.pi / 2 + k["spread"])
            clump = None
            if k.get("clumps"):
                clump = self._clump_for(pitch, new=not hold)
                ang = clump.angle + random.uniform(-k["clumps"]["tight"], k["clumps"]["tight"])
            off = ang + math.pi / 2                                    # angle from straight up
            if k.get("apex"):
                # with quadratic drag a rise of H needs v0 = sqrt(g/k * (exp(2 k H) - 1)); the 1.08 allows for the
                # linear drag too. Dividing by cos() keeps the *upward* speed (and so the height) the same at any angle.
                height = max(20.0, y) * random.uniform(*k["apex"])
                hs = _hscale(y)
                qd, g = k["qdrag"] / hs, k["gravity"] * math.sqrt(hs)       # (the same scaling the simulation uses)
                need = math.sqrt(g / qd * (math.exp(2.0 * qd * height * 1.08) - 1.0))
                v = need / max(0.3, math.cos(off)) * fx.speed * (0.55 if hold else 1.0)
            elif k.get("rocket"):
                # the climb needs v0 = sqrt(2 g H) to peak at height H; it bursts at the peak (or the top edge)
                height = max(20.0, y) * random.uniform(0.5, 0.92) * (0.55 if hold else 1.0)
                g = max(60.0, k["gravity"] + fx.gravity * 6.0)
                v = math.sqrt(2.0 * g * height) * 1.12 / max(0.3, math.cos(off)) * fx.speed
            else:
                v = random.uniform(*k["speed"]) * fx.speed * (0.55 if hold else 1.0)
            v *= min(1.0, 0.6 + 0.4 * vn) if (k.get("apex") or k.get("rocket")) else min(1.5, 0.6 + 0.4 * vn)
            if k.get("scale_h"):
                v *= max(0.6, min(6.0, y / 110.0))
            v = min(v, MAX_LAUNCH)
            c = QColor(base) if (fx.color_mode == "rainbow" and not fx.pitch_color) else QColor(base).lighter(random.randint(100, 150))
            p = _Particle(
                random.uniform(lo, hi), y, math.cos(ang) * v, math.sin(ang) * v,       # (anywhere across the key)
                random.uniform(*k["ttl"]) * fx.life, random.uniform(*k["size"]) * fx.size * (0.5 + 0.5 * vn), c,
                random.uniform(0, math.tau), random.uniform(-6, 6), random.uniform(0, math.tau))
            if k.get("key_color"):                                      # steam: exactly the colour of the key
                p.color = QColor(self._key_color(pitch, ch))
            p.clump = clump
            p.power = vn
            p.rocket = bool(k.get("rocket"))
            if k.get("pop_chance") and random.random() < k["pop_chance"]:      # a few sparks pop part-way
                p.fuse = p.ttl * random.uniform(0.25, 0.75)
            if k.get("shimmer"):                                               # stars shimmer away at a random point
                p.shim_at = p.ttl * random.uniform(0.35, 0.95)
            if k.get("rect_chance"):
                p.rect = random.random() < k["rect_chance"]
            if k.get("rise_var"):
                p.bf = random.uniform(*k["rise_var"])
            if k.get("cone"):
                p.slope = math.tan(off)
            if k.get("linger") and random.random() < k["linger"]:      # a few embers hover and glow for much longer
                p.ttl *= random.uniform(2.2, 3.4)
                p.bf, p.df = 0.3, 1.8
            if k.get("pop"):                                           # float up to a random height, then burst
                p.pop = max(4.0, y - random.uniform(0.3, 0.95) * max(30.0, y - 6.0))
            if lag > 0.0:                                                      # a late note: start it where it would be by now
                p.age += lag
                p.x += p.vx * lag
                p.y += p.vy * lag
            self._particles.append(p)

    def _make_room(self, count: int, limit: int):
        """Send the *count* oldest particles away - each in its effect's own way (stars shimmer, bubbles pop, rockets burst,
        the rest fade) - instead of cutting them out."""
        chosen = []
        for p in self._particles:
            if len(chosen) >= count:
                break
            if not p.out:
                chosen.append(p)
        total = len(chosen)
        for i, p in enumerate(chosen):                                  # oldest first, on a curve: slow at the start, then faster
            p.out = True
            p.wait = LEAVE_SPREAD * math.sqrt((i + 0.5) / total)        # (the number gone grows with the square of the time)
            self._leaving += 1

    def _exit(self, p: _Particle, k: dict):
        """It is this particle's turn to go: in its effect's own way."""
        if k.get("shimmer") and p.shim > -1.5:                          # stars: shimmer away (and shed glitter)
            if p.shim < 0.0:
                p.shim_at = max(p.age, 1e-4)
            return
        if k.get("pop") and p.popt < 0.0:                               # bubbles: pop
            p.pop = 1e9
            return
        if p.rocket:                                                    # a rocket still climbing: burst now
            p.vy = 0.0
            return
        if k.get("pop_chance") and not p.fuse and random.random() < 0.3:   # some sparks pop away
            p.fuse = max(p.age, 1e-4)
            return
        if p.shim == -2.0 or p.popt >= 0.0 or p.shim >= 0.0:            # already on its way out in its own way
            return
        p.leave = 0.0                                                   # everything else fades out

    def _bolt(self, lo: float, hi: float, y: float, color: QColor, hold: bool, vn: float, lag: float = 0.0):
        """A lightning bolt: a jagged line climbing from a key, with a few short side branches."""
        fx, k = self.fx, KINDS["lightning"]
        x = random.uniform(lo, hi)
        reach = min(y - 4.0, y * random.uniform(0.55, 0.97) * (0.6 if hold else 1.0) * min(1.2, 0.35 + 0.65 * vn))
        if reach < 8.0:
            return
        seg = max(8.0, min(22.0, reach / 9.0))
        width = float(self.width())
        lines, main = [], [(x, y)]
        cx, cy = x, y
        lean = random.uniform(-0.4, 0.4)                                # each bolt leans its own way
        while y - cy < reach:
            cy = max(2.0, cy - seg * random.uniform(0.7, 1.3))             # (never above the top of the area)
            cx = max(0.0, min(width, cx + (random.uniform(-1.0, 1.0) * 0.9 + lean) * seg))
            main.append((cx, cy))
            if random.random() < 0.22 and y - cy < reach * 0.85:       # a side branch
                side, bx, by, br = random.choice((-1, 1)), cx, cy, [(cx, cy)]
                for _ in range(random.randint(2, 4)):
                    bx = max(0.0, min(width, bx + side * seg * random.uniform(0.4, 1.0)))
                    by = max(2.0, by - seg * random.uniform(0.2, 0.7))
                    br.append((bx, by))
                lines.append(br)
        lines.insert(0, main)
        p = _Particle(x, y, 0.0, 0.0, random.uniform(*k["ttl"]) * fx.life, random.uniform(*k["size"]) * fx.size,
                      QColor(color), 0.0, 0.0, random.uniform(0, math.tau))
        p.pts = lines
        p.age = min(lag, p.ttl * 0.6)
        self._particles.append(p)

    @staticmethod
    def _pattern_points(name: str):
        """The offsets (as shares of the burst radius, y down) a shaped burst's sparks fly out to, as (dx, dy, ring)."""
        pts = []
        if name == "heart":
            for i in range(30):
                t = i * math.tau / 30
                pts.append((16 * math.sin(t) ** 3 / 17.0,
                            -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)) / 17.0, 0))
        elif name == "star":
            verts = []
            for i in range(10):
                a = -math.pi / 2 + i * math.pi / 5
                r = 1.0 if i % 2 == 0 else 0.42
                verts.append((math.cos(a) * r, math.sin(a) * r))
            for i in range(10):
                (x0, y0), (x1, y1) = verts[i], verts[(i + 1) % 10]
                for j in range(3):
                    f = j / 3.0
                    pts.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, 0))
        elif name == "spiral":
            ph = random.uniform(0, math.tau)
            for i in range(32):
                r = 0.2 + 0.8 * (i // 2) / 15.0
                a = ph + (i % 2) * math.pi + r * 2.4 * math.pi
                pts.append((math.cos(a) * r, math.sin(a) * r, 0))
        elif name == "double":
            ph = random.uniform(0, math.tau)
            pts += [(math.cos(ph + i * math.tau / 20), math.sin(ph + i * math.tau / 20), 0) for i in range(20)]
            pts += [(0.5 * math.cos(ph + i * math.tau / 12), 0.5 * math.sin(ph + i * math.tau / 12), 1) for i in range(12)]
        return pts

    def _explode(self, p: _Particle, out: list):
        """A rocket reaches the top of its flight and bursts - in the chosen pattern (or a random one)."""
        fx, k = self.fx, KINDS["fireworks"]
        name = fx.pattern if fx.pattern in PATTERN_NAMES else random.choice(PATTERN_NAMES)
        power = 0.5 + 0.5 * p.power
        tint = QColor(p.color)

        def spark(vx, vy, ttl, size, color, df=1.0, gf=1.0, trail=False, fuse=0.0):
            c = _Particle(p.x, p.y, vx, vy, ttl * fx.life, max(1.0, size), color, random.uniform(0, math.tau),
                          random.uniform(-6, 6), random.uniform(0, math.tau))
            c.df, c.gf, c.fuse = df, gf, fuse * fx.life
            if trail:
                c.trail, c.streak = [], True
            out.append(c)

        size = p.size * 0.7
        if name in ("heart", "star", "spiral", "double"):
            # brake hard so the sparks stop where the shape puts them (reach = v0 / rate), then they drift down gently
            rate = k["drag"] * SHAPE_DF
            reach = min(random.uniform(60, 95) * fx.speed * power, max(25.0, p.y - 6.0))
            second = QColor.fromHsv((max(0, tint.hue()) + 55) % 360, max(120, tint.saturation()), 255)
            for dx, dy, ring in self._pattern_points(name):
                spark(dx * reach * rate, dy * reach * rate, random.uniform(1.3, 1.8), size,
                      QColor(second if ring else tint).lighter(random.randint(100, 130)), df=SHAPE_DF, gf=0.5)
        elif name == "willow":                             # long golden trails that droop
            speed = random.uniform(70, 110) * fx.speed * power
            gold = QColor(tint)
            gold.setHsv(40, 150, 255)
            for i in range(26):
                a = i * math.tau / 26 + random.uniform(-0.1, 0.1)
                v = speed * random.uniform(0.7, 1.0)
                spark(math.cos(a) * v, math.sin(a) * v, random.uniform(2.2, 3.0), size * 0.9,
                      QColor(gold).lighter(random.randint(100, 125)), df=5.0, gf=0.4, trail=True)
        elif name == "crackle":                            # a ring of sparks that each pop again
            speed = random.uniform(100, 150) * fx.speed * power
            n = 14
            for i in range(n):
                a = i * math.tau / n + random.uniform(-0.1, 0.1)
                v = speed * random.uniform(0.8, 1.0)
                ttl = random.uniform(1.0, 1.4)
                spark(math.cos(a) * v, math.sin(a) * v, ttl, size, QColor(tint).lighter(random.randint(100, 130)),
                      fuse=ttl * random.uniform(0.45, 0.75))
        else:                                              # "peony" a filled round burst, "ring" a single ring
            speed = random.uniform(110, 170) * fx.speed * power
            n = random.randint(18, 28) if name == "ring" else random.randint(34, 46)
            ph = random.uniform(0, math.tau)
            for i in range(n):
                a = ph + i * math.tau / n + random.uniform(-0.05, 0.05)
                v = speed * (random.uniform(0.85, 1.0) if name == "ring" else math.sqrt(random.uniform(0.04, 1.0)))
                spark(math.cos(a) * v, math.sin(a) * v, random.uniform(1.0, 1.6), size,
                      QColor(tint).lighter(random.randint(100, 135)))
        out.append(_Particle(p.x, p.y, 0.0, 0.0, 0.12, p.size * 3.4, QColor(p.color).lighter(190), 0.0, 0.0, 0.0))     # the flash

    def _clump_for(self, pitch: int, new: bool = False) -> _Clump:
        """The clump a key is feeding: a new one for each note-on, and a fresh one every so often while it is held."""
        c = self._clump_cur.get(pitch)
        if c is None or new or self._t >= c.expire:
            cfg = KINDS[self.fx.kind]["clumps"]
            c = _Clump()
            c.angle = -math.pi / 2 + random.uniform(-KINDS[self.fx.kind]["spread"], KINDS[self.fx.kind]["spread"])
            c.expire = self._t + random.uniform(*cfg["renew"])
            self._clump_cur[pitch] = c
        return c

    def _pop_spark(self, p: _Particle, k: dict, out: list):
        """A spark pops: a quick flash and a burst of small sparks flung outwards."""
        fx = self.fx
        for _ in range(random.randint(*k.get("pop_burst", (3, 5)))):
            ang = random.uniform(0, math.tau)
            v = random.uniform(70, 170) * fx.speed
            out.append(_Particle(p.x, p.y, p.vx * 0.4 + math.cos(ang) * v, p.vy * 0.4 + math.sin(ang) * v,
                                 random.uniform(0.25, 0.55) * fx.life, max(0.8, p.size * 0.55), QColor(p.color).lighter(115),
                                 random.uniform(0, math.tau), random.uniform(-6, 6), random.uniform(0, math.tau)))
        out.append(_Particle(p.x, p.y, 0.0, 0.0, 0.1, p.size * 2.6, QColor(p.color).lighter(190), 0.0, 0.0, 0.0))     # the flash

    def _sparkle(self, p: _Particle, out: list):
        """A star starts to shimmer away: it sheds a little glitter."""
        for _ in range(random.randint(5, 8)):
            ang = random.uniform(0, math.tau)
            v = random.uniform(15, 55)
            s = _Particle(p.x + random.uniform(-3, 3), p.y + random.uniform(-3, 3), math.cos(ang) * v, math.sin(ang) * v - 12.0,
                          random.uniform(0.35, 0.7) * self.fx.life, max(0.8, p.size * random.uniform(0.25, 0.45)),
                          QColor(p.color).lighter(130), random.uniform(0, math.tau), random.uniform(-8, 8), random.uniform(0, math.tau))
            s.shim = -2.0                                         # (a plain speck: it just fades)
            out.append(s)

    # -------------------------------------------------------------- physics
    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        self._drain_events()
        if self.event_source is not None and not (self._active or self._particles or self._glow or self._flash):
            return                                                     # idle: nothing to animate, nothing to repaint
        self.step(dt)

    def step(self, dt: float):
        """Advance the simulation by *dt* seconds (public so it can be driven from tests)."""
        if not self.effects_on():                  # nothing to animate while the effects are hidden
            self._particles.clear()
            self._glow.clear()
            self._flash.clear()
            self._timer.stop()
            return
        k, fx = KINDS[self.fx.kind], self.fx
        if self._glow or fx.held_glow:             # held-note glow: eases in while a key is down, fades out after
            for pitch, ch in self._active.items():
                if fx.held_glow:
                    g = self._glow.get(pitch) or self._glow_hit(pitch, ch)
                    g[0] += (1.0 - g[0]) * min(1.0, dt * 9.0)
            for pitch in list(self._glow):
                if pitch not in self._active or not fx.held_glow:
                    g = self._glow[pitch]
                    g[0] -= dt * 1.1
                    if g[0] <= 0.01:
                        del self._glow[pitch]
        dt *= k.get("tempo", 1.0)                  # (a faster effect: its whole motion runs a little quicker)
        self._t += dt
        base_chance = k["hold"] * fx.amount
        for pitch, ch in self._active.items():
            if random.random() < min(1.0, base_chance * self._vel_norm(pitch)):
                self._burst(pitch, ch, 1, hold=True)

        gravity = k["gravity"] + fx.gravity * 6.0        # the user's gravity slider adds (or removes) pull
        qdrag = k.get("qdrag", 0.0)
        if k.get("apex"):                                # (the same scaling the launch speed was worked out with)
            hs = _hscale(self._key_top() - 1.0)
            gravity = k["gravity"] * math.sqrt(hs) + fx.gravity * 6.0
            qdrag /= hs
        buoy, flutter = k["buoy"], k["flutter"]
        if k.get("scale_h"):
            buoy *= max(0.6, min(6.0, (self._key_top() - 1.0) / 110.0))
        drag = math.exp(-k["drag"] * dt)
        floor = self._key_top() - 1.0
        rest = k["rest"] * fx.bounce
        height, width = self.height(), self.width()
        t_now = self._t
        wobble = k.get("speed_wobble", 0.0)
        jitter, pops = k.get("jitter", 0.0), k.get("pop", False)
        jspeed, cone = k.get("jitter_speed", 0.0), k.get("cone", 0.0)
        rest_time = k.get("rest_time")
        shimmers, shim_damp = k.get("shimmer", False), math.exp(-3.0 * dt)
        tether, roam = k.get("tether", 0.0), k.get("roam", 0.0)
        spawned: list = []
        alive = []
        trails = fx.trails
        leaving = 0
        for p in self._particles:
            p.age += dt
            if p.wait > 0.0:                               # sent away to make room, but waiting its turn
                p.wait -= dt
                if p.wait <= 0.0:
                    self._exit(p, k)
            if p.leave >= 0.0:                             # its turn came: fades out, then goes
                p.leave += dt
                if p.leave >= LEAVE_TIME:
                    continue
            if p.out:
                leaving += 1
            if p.pts is not None:                          # a lightning bolt: it only flashes, then goes
                if p.age < p.ttl:
                    alive.append(p)
                continue
            if shimmers and p.shim > -1.5:                 # stars: shimmer away at the top edge or at a random point
                if p.shim >= 0.0:
                    p.shim += dt
                    if p.shim >= SHIMMER_TIME:
                        continue
                elif p.y <= TOP_EDGE or (p.shim_at and p.age >= p.shim_at) or p.age >= p.ttl:
                    p.shim = 0.0
                    if not p.out or random.random() < 0.35:   # (fewer glitter bursts when many are leaving at once)
                        self._sparkle(p, spawned)
            elif pops:
                if p.popt >= 0.0:                          # bursting: stays put while the pop plays
                    p.popt += dt
                    if p.popt < POP_TIME:
                        alive.append(p)
                    continue
                if p.y <= p.pop or p.age >= p.ttl:
                    p.popt = 0.0
                    p.vx = p.vy = 0.0
                    alive.append(p)
                    continue
            elif p.age >= p.ttl:
                continue
            if p.fuse and p.age >= p.fuse:                 # sparks: this one pops
                self._pop_spark(p, k, spawned)
                continue
            lift = buoy * p.bf
            if wobble:                                     # its own lift swells and fades as it rises: speeds keep changing
                lift *= 1.0 + wobble * math.sin(p.age * (1.6 + p.phase * 0.3) + p.phase * 2.0)
            ax, ay = 0.0, gravity * p.gf - lift
            if jitter:                                     # every particle meanders on its own random curve
                jf = jitter
                if jspeed:                                 # ...but only once it has slowed down
                    jf = jitter / (1.0 + (math.hypot(p.vx, p.vy) / jspeed) ** 2)
                w1 = 2.2 + p.phase * 0.55                  # (own frequencies from its random phase) plus some noise
                ax += jf * math.sin(p.age * w1 + p.phase * 3.0) + random.uniform(-1.0, 1.0) * jf * 0.35
                ay += jf * 0.7 * math.cos(p.age * (w1 + 0.9) + p.phase * 5.0) + random.uniform(-1.0, 1.0) * jf * 0.25
            if tether:                                     # fireflies: a gentle pull back towards where it started
                ax -= (p.x - p.ox) * tether
            if flutter:                                    # leaf-like swaying while falling / rising
                ax += math.sin(p.age * (5.0 + p.phase * 0.7) + p.phase * 2.0) * flutter * (abs(p.vy) + 20.0)
            if cone:                                       # hold the stream's shape: sideways speed follows rising speed
                ax += (p.slope * max(0.0, -p.vy) - p.vx) * cone
            if qdrag:                                      # fast = braked hard, slow = barely braked
                sp = math.hypot(p.vx, p.vy)
                ax -= qdrag * sp * p.vx
                ay -= qdrag * sp * p.vy
            pd = drag if p.df == 1.0 else math.exp(-k["drag"] * p.df * dt)
            if p.shim >= 0.0:
                pd *= shim_damp                            # a shimmering star slows to a hover
            p.vx = (p.vx + ax * dt) * pd
            p.vy = (p.vy + ay * dt) * pd
            p.x += p.vx * dt
            p.y += p.vy * dt
            if roam:                                       # ...and never further away sideways than *roam* px
                off = p.x - p.ox
                if abs(off) > roam:
                    p.x = p.ox + math.copysign(roam, off)
                    if p.vx * off > 0:
                        p.vx *= -0.3
            if p.rocket and (p.vy > -15.0 or p.y <= TOP_EDGE + 2.0):     # at the top of its flight: burst
                self._explode(p, spawned)
                continue
            if k["floor"] and p.y > floor:                 # land on the key tops
                p.y = floor
                if p.vy > 0:
                    p.vy = -p.vy * rest
                    p.vx *= 1.0 - k["fric"]
                    p.spin *= 0.6
                    if abs(p.vy) < 14:
                        p.vy = 0.0                         # comes to rest
                        if rest_time and not p.landed:     # ...stays a while, then goes
                            p.landed = True
                            p.ttl = min(p.ttl, p.age + random.uniform(*rest_time) * fx.life)
            if trails or p.rocket or p.streak:  # remember where it has been
                tr = p.trail
                if tr is None:
                    tr = p.trail = []
                tr.append((p.x, p.y))
                if len(tr) > TRAIL_LEN:
                    del tr[0]
            if k["shape"] == "rect":                       # confetti tumbles faster the faster it moves
                p.rot += (p.spin + p.vx * 0.04) * dt
            else:
                p.rot += p.spin * dt
            if -40 < p.y < height + 20 and -30 < p.x < width + 30:
                alive.append(p)
        self._leaving = leaving
        hard = int(fx.max_particles * HARD_CEILING)                # a last-resort ceiling, far above the limit: crowds leave on
        alive.extend(spawned[:max(0, hard - len(alive))])          # their curve long before it (bursts and pops use the headroom)
        self._particles = alive
        if self._flash:                                            # short notes: their key goes dark once the flash is over
            now = time.perf_counter()
            gone = [p for p, f in self._flash.items() if f[1] <= now]
            for p in gone:
                del self._flash[p]
            if gone:
                self._set_notes(self._notes)
        if not self._particles and not self._active and not self._glow and self.event_source is None:
            self._timer.stop()
        self.update()

    # -------------------------------------------------------------- paint
    def paintEvent(self, _e):
        t0 = time.perf_counter()
        self._paint(_e)
        if self._particles or self._glow:
            self._adapt(time.perf_counter() - t0)

    def _adapt(self, took: float):
        """Watch how long painting takes and, if this machine is struggling, shed detail (trails first, then
        half the particles per note; the glow stays); bring it back when there is plenty of time to spare."""
        self._paint_ema = took if self._paint_ema == 0.0 else self._paint_ema * 0.9 + took * 0.1
        if self._q_wait > 0:
            self._q_wait -= 1
            return
        if self._paint_ema > SLOW_PAINT and self._quality < 2:
            self._quality += 1
            self._q_wait = 45
        elif self._paint_ema < FAST_PAINT and self._quality > 0:
            self._quality -= 1
            self._q_wait = 240

    def _paint(self, _e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        top = self._key_top()
        if not self._popped:                       # popped out: the window is transparent - just keys and particles
            p.fillRect(0, 0, w, h, QColor(theme.BG2))
        kw = w / len(self._whites)
        edge = QPen(QColor("#0d0f14"))
        p.setRenderHint(QPainter.Antialiasing, False)

        for pitch, i in self._white_index.items():                      # white keys
            ch = self._active.get(pitch)
            p.setPen(edge)
            p.setBrush(self._key_color(pitch, ch) if ch is not None else QColor("#e9ecf4"))
            p.drawRect(QRectF(i * kw, top, kw, KEYS_H - 2))

        bw, bh = kw * BLACK_KEY_W, (KEYS_H - 2) * 0.62                  # black keys
        for pitch in range(LOW, HIGH + 1):
            if not _is_black(pitch):
                continue
            left = self._white_index.get(pitch - 1)
            if left is None:
                continue
            ch = self._active.get(pitch)
            p.setPen(edge)
            p.setBrush(self._key_color(pitch, ch).darker(115) if ch is not None else QColor("#1a1d26"))
            p.drawRect(QRectF((left + 1) * kw - bw / 2, top, bw, bh))

        if self._glow:
            self._paint_glow(p, top, kw)
        if self._particles:
            self._paint_particles(p)
        p.end()

    def _sprite(self, kind: str, col: QColor, variant: int = 0) -> QPixmap:
        """A soft round shape in *col*, rendered once and then just scaled and blended (much cheaper than a gradient or a
        big antialiased ellipse for every particle on every frame)."""
        shift = 3 if kind in ("puff", "glow") else 4                    # (the many-coloured kinds use coarser colour buckets)
        key = (kind, variant, col.red() >> shift, col.green() >> shift, col.blue() >> shift)
        pm = self._sprites.get(key)
        if pm is None:
            same = [k for k in self._sprites if k[0] == kind] if self._sprite_n.get(kind, 0) >= SPRITE_CAPS.get(kind, 256) else None
            if same:                                                    # this kind has filled its share of the cache: start it over
                for k in same:                                          # (the other kinds keep their pictures)
                    del self._sprites[k]
                self._sprite_n[kind] = 0
            self._sprite_n[kind] = self._sprite_n.get(kind, 0) + 1
            size = SPIKE_SIZE if kind == "spike" else SPRITE
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.Antialiasing, True)
            q.setPen(Qt.NoPen)
            if kind == "spike":                                          # held-note glow: a small bright core with spiky rays
                self._draw_spikes(q, col, variant, size)
                q.end()
                self._sprites[key] = pm
                return pm
            if kind == "puff":
                grad = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
                for stop, mul in ((0.0, 1.0), (0.5, 0.5), (1.0, 0.0)):
                    c = QColor(col)
                    c.setAlphaF(mul)
                    grad.setColorAt(stop, c)
                q.setBrush(QBrush(grad))
            elif kind == "glow":                                        # held-note glow: bright core, long soft fall-off
                grad = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
                for stop, mul in ((0.0, 1.0), (0.35, 0.45), (0.7, 0.14), (1.0, 0.0)):
                    c = QColor(col)
                    c.setAlphaF(mul)
                    grad.setColorAt(stop, c)
                q.setBrush(QBrush(grad))
            else:                                                       # "halo": a flat disc, faded at its rim
                grad = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
                for stop, mul in ((0.0, 1.0), (0.5, 0.8), (1.0, 0.0)):
                    c = QColor(col)
                    c.setAlphaF(mul)
                    grad.setColorAt(stop, c)
                q.setBrush(QBrush(grad))
            q.drawEllipse(QPointF(size / 2, size / 2), size / 2, size / 2)
            q.end()
            self._sprites[key] = pm
        return pm

    @staticmethod
    def _draw_spikes(q: QPainter, col: QColor, variant: int, size: int):
        """The held-note glow picture: a small bright core and a fan of thin, tapering rays (each with a hot white centre)
        above the bottom-centre of the picture, which is where the key's top edge goes. *variant* picks a loudness level
        (quiet: few short rays; loud: many long ones) and one of several flicker patterns, so it can twinkle."""
        level, frame = divmod(variant, SPIKE_FRAMES)
        f = size / 128.0
        ox, oy = size / 2.0, size - 1.0
        rng = random.Random(variant * 977 + 13)
        core_r = (20.0 + 10.0 * level / max(1, SPIKE_LEVELS - 1)) * f
        core = QRadialGradient(QPointF(ox, oy), core_r)
        for stop, mul in ((0.0, 1.0), (0.35, 0.55), (1.0, 0.0)):
            c = QColor(col)
            c.setAlphaF(mul)
            core.setColorAt(stop, c)
        q.setBrush(QBrush(core))
        q.drawEllipse(QPointF(ox, oy), core_r, core_r)
        hot = QColor(col).lighter(180)
        share = level / max(1, SPIKE_LEVELS - 1)                         # 0 = quietest .. 1 = loudest
        n = int(round(5 + 8 * share))
        longest = (0.45 + 0.55 * share) * (size - 6)
        for i in range(n):
            t = i / (n - 1) * 2.0 - 1.0                                  # -1 (left) .. 1 (right)
            ang = t * math.radians(74) + rng.uniform(-0.09, 0.09)       # from straight up
            length = (1.0 - 0.5 * abs(t)) * rng.uniform(0.5, 1.0) * longest
            if rng.random() < 0.3:
                length *= 0.45                                           # a few short stubs between the long spikes
            length = min(length, (size / 2.0 - 3.0) / max(0.15, abs(math.sin(ang))))   # (stay inside the picture sideways)
            dx, dy = math.sin(ang), -math.cos(ang)
            tip = QPointF(ox + dx * length, max(2.0, oy + dy * length))
            for half, base, alpha in ((rng.uniform(1.8, 3.4) * f, col, 0.9), (rng.uniform(0.6, 1.1) * f, hot, 0.95)):
                px, py = -dy * half, dx * half
                poly = QPolygonF([QPointF(ox + px, oy + py), tip, QPointF(ox - px, oy - py)])
                g = QLinearGradient(QPointF(ox, oy), tip)
                for stop, mul in ((0.0, alpha), (0.55, alpha * 0.5), (1.0, 0.0)):
                    c = QColor(base)
                    c.setAlphaF(mul)
                    g.setColorAt(stop, c)
                q.setBrush(QBrush(g))
                q.drawPolygon(poly)

    def _paint_glow(self, p: QPainter, top: float, kw: float):
        """Held-note glow: a soft round light plus a fan of fainter spikes rising from each held key, brighter and taller for
        harder notes."""
        reach = GLOW_REACH                          # (a fixed size: it no longer grows with the effect height)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.setPen(Qt.NoPen)
        p.save()
        p.setClipRect(QRectF(0, 0, self.width(), top))
        pulse = 0.92 + 0.08 * math.sin(time.perf_counter() * 7.0)
        flick = time.perf_counter() * 14.0
        ga = self.fx.glow_amount
        for pitch, (level, col, strength, loud) in self._glow.items():
            span = self._key_span(pitch)
            if span is None or level <= 0.0:
                continue
            grow = 0.7 + 0.3 * ga                                       # (a stronger glow is also a little bigger)
            cx = (span[0] + span[1]) / 2
            lvl = level * pulse * min(1.2, 0.35 + 0.65 * strength) * ga
            # the soft round light underneath (the original glow, smaller)...
            sry = max(12.0, SOFT_REACH * (0.6 + 0.4 * min(1.4, strength)) * grow)
            srx = kw * SOFT_WIDTH * grow
            p.setOpacity(max(0.0, min(1.0, 0.7 * lvl)))
            p.drawPixmap(QRectF(cx - srx, top - sry, srx * 2, sry * 2), self._sprite("glow", col), SPRITE_RECT)
            if not SPIKES_ENABLED:
                continue
            # ...and the spikes over it, fainter. How many, how long and how bright depend on how loud the note is.
            ry = max(14.0, reach * (0.4 + 0.9 * loud) * grow)
            rx = ry * 0.62                                              # (the spikes fan out to about this far each side)
            p.setOpacity(max(0.0, min(1.0, SPIKE_ALPHA * level * pulse * ga * (0.35 + 0.65 * loud))))
            lv = min(SPIKE_LEVELS - 1, int(loud * SPIKE_LEVELS))        # which loudness level's picture
            frame = int(flick + pitch * 3) % SPIKE_FRAMES                # which flicker pattern this key shows right now
            pm = self._sprite("spike", col, lv * SPIKE_FRAMES + frame)
            p.drawPixmap(QRectF(cx - rx, top - ry, rx * 2, ry), pm, QRectF(0, 0, pm.width(), pm.height()))
        p.setOpacity(1.0)
        p.restore()
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

    def _paint_particles(self, p: QPainter):
        fx = self.fx
        shape = KINDS[fx.kind]["shape"]
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setCompositionMode(QPainter.CompositionMode_Plus if fx.glow else QPainter.CompositionMode_SourceOver)
        pops, flicker = KINDS[fx.kind].get("pop", False), KINDS[fx.kind].get("flicker", 0.0)
        fade = KINDS[fx.kind].get("fade", 0.0)
        fade_top, top = KINDS[fx.kind].get("fade_top", 0.0), max(1.0, self._key_top())
        puff_grow, pulse = KINDS[fx.kind].get("grow", 0.0), KINDS[fx.kind].get("pulse", False)
        halo_mul = KINDS[fx.kind].get("halo", 1.0)
        ga = fx.glow_amount
        want_trails = fx.trails and self._quality == 0 and shape not in ("puff", "ring")     # (soft puffs and bubbles gain nothing from them)
        for q in self._particles:
            if q.pts is not None:
                self._paint_bolt(p, q)
                continue
            if pops:                                                        # bubbles stay until they pop
                life = min(1.0, q.age / 0.25)
            elif fade:                                                      # full brightness until the last moment
                life = max(0.0, min(1.0, (q.ttl - q.age) / fade))
            else:
                life = 1.0 - q.age / q.ttl
            r = q.size * (0.4 + 0.6 * life)
            if q.shim >= 0.0:                                               # shimmering away: flickers fast, shrinks, fades
                t = q.shim / SHIMMER_TIME
                life = (1.0 - t) ** 0.7 * (0.3 + 0.7 * abs(math.sin(q.shim * 42 + q.phase)))
                r = q.size * (1.0 - 0.45 * t) * (0.85 + 0.35 * abs(math.sin(q.shim * 30 + q.phase * 2)))
            col = QColor(q.color)
            if q.popt >= 0.0:
                self._paint_pop(p, q, col)
                continue
            if shape == "star" and q.shim < 0.0:                            # twinkle
                life *= 0.65 + 0.35 * math.sin(q.age * 14 + q.phase)
            if fade_top:                                                    # dissolve into the top edge instead of being cut off
                life *= max(0.0, min(1.0, q.y / (fade_top * top)))
            if flicker:                                                     # embers flicker as they burn
                life *= 1.0 - flicker * (0.5 + 0.5 * math.sin(q.age * 13 + q.phase * 3))
            if pulse:                                                       # fireflies blink clearly on and off
                s = 0.5 + 0.5 * math.sin(q.age * (2.3 + q.phase * 0.25) + q.phase * 2.0)
                s = max(0.0, min(1.0, (s - 0.3) / 0.5))                       # off for a good part of each cycle
                s = s * s * (3.0 - 2.0 * s)
                life *= 0.015 + 0.985 * s
                r *= 0.55 + 0.45 * s
            if q.power != 1.0:                                              # velocity scaling: soft notes are dimmer
                life *= min(1.0, 0.45 + 0.55 * q.power)
            if q.leave >= 0.0:                                              # sent away to make room: fading out
                life *= max(0.0, 1.0 - (q.leave / LEAVE_TIME) ** 2)       # (slow at first, then quickly)
            tr = q.trail
            if tr and len(tr) > 1 and (want_trails or q.rocket or q.streak):  # a fading streak behind it: faint thin tail, then a
                n = len(tr)                                                 # stronger thicker head (two strokes, not one per step)
                mid = n // 2
                pen = QPen()
                pen.setCapStyle(Qt.RoundCap)
                tc = QColor(col)
                (x0, y0), (xm, ym), (x1, y1) = tr[0], tr[mid], tr[-1]
                for ax, ay, bx, by, wmul, amul in ((x0, y0, xm, ym, 0.8, 0.28), (xm, ym, x1, y1, 1.5, 0.6)):
                    if abs(bx - ax) + abs(by - ay) < 0.6:
                        continue
                    tc.setAlphaF(max(0.0, min(1.0, amul * life)))
                    pen.setColor(tc)
                    pen.setWidthF(max(0.8, r * wmul))
                    p.setPen(pen)
                    p.drawLine(QPointF(ax, ay), QPointF(bx, by))
            if shape == "puff":                                             # steam: a big soft puff that swells as it rises
                t = min(1.0, q.age / q.ttl)
                a = max(0.0, min(1.0, q.age / 0.3)) * life * 0.42
                rad = q.size * (1.0 + puff_grow * t)
                if a > 0.004:                                                   # (the key's own colour, as a cached soft sprite)
                    p.setOpacity(min(1.0, a))
                    p.drawPixmap(QRectF(q.x - rad, q.y - rad, rad * 2, rad * 2), self._sprite("puff", col), SPRITE_RECT)
                    p.setOpacity(1.0)
                continue
            if fx.glow and shape != "ring":              # (a bubble's halo is barely visible - skipped)
                ha = 0.18 * life * ga
                if ha > 0.02:
                    hr = r * 2.9 * halo_mul * (0.75 + 0.25 * ga)
                    p.setOpacity(min(1.0, ha))
                    p.drawPixmap(QRectF(q.x - hr, q.y - hr, hr * 2, hr * 2), self._sprite("halo", col), SPRITE_RECT)
                    p.setOpacity(1.0)
            col.setAlphaF(max(0.0, min(1.0, 1.2 * life)))
            if shape == "ring":
                pen = QPen(col)
                pen.setWidthF(max(1.0, r * 0.28))
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(q.x, q.y), r, r)
                continue
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            if shape == "dot" and q.rect:                                   # a long rectangle, tumbling as it rises
                p.save()                                                    # (the same one the confetti uses)
                p.translate(q.x, q.y)
                p.rotate(math.degrees(q.rot))
                p.drawRect(QRectF(-r, -r * 0.55, r * 2, r * 1.1))
                p.restore()
            elif shape == "dot":
                p.drawEllipse(QPointF(q.x, q.y), r, r)
            elif shape == "rect":
                p.save()
                p.translate(q.x, q.y)
                p.rotate(math.degrees(q.rot))
                p.drawRect(QRectF(-r, -r * 0.55, r * 2, r * 1.1))
                p.restore()
            else:                                                           # four-point star
                pts = []
                for i in range(8):
                    a = q.rot + i * math.pi / 4
                    d = r * 1.9 if i % 2 == 0 else r * 0.6
                    pts.append(QPointF(q.x + math.cos(a) * d, q.y + math.sin(a) * d))
                p.drawPolygon(QPolygonF(pts))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

    def _paint_bolt(self, p: QPainter, q: _Particle):
        """Lightning: a flickering white-hot core inside a wider coloured glow, gone in a fraction of a second. The bolt is
        drawn once into a picture; each frame only fades that picture."""
        t = q.age / max(0.01, q.ttl)
        a = (1.0 - t) ** 0.8 * (0.55 + 0.45 * abs(math.sin(q.age * 110.0 + q.phase)))
        if q.leave >= 0.0:
            a *= max(0.0, 1.0 - (q.leave / LEAVE_TIME) ** 2)
        if q.sprite is None:
            q.sprite = self._render_bolt(q)
        pm, x0, y0 = q.sprite
        p.setOpacity(max(0.0, min(1.0, a)))
        p.drawPixmap(QPointF(x0, y0), pm)
        p.setOpacity(1.0)

    def _render_bolt(self, q: _Particle):
        col = QColor(q.color)
        ga = self.fx.glow_amount
        layers = [(q.size * 0.7, QColor(col).lighter(200), 1.0), (q.size * 2.0, col, min(1.0, 0.4 * (0.6 + 0.4 * ga)))]
        if self.fx.glow:
            layers.append((q.size * 5.0 * (0.7 + 0.3 * ga), col, min(1.0, 0.13 * ga)))
        pad = max(w for w, _c, _m in layers) + 2.0
        xs = [x for line in q.pts for x, _ in line]
        ys = [y for line in q.pts for _, y in line]
        x0, y0 = math.floor(min(xs) - pad), math.floor(min(ys) - pad)
        w, h = math.ceil(max(xs) + pad) - x0, math.ceil(max(ys) + pad) - y0
        dpr = max(1.0, self.devicePixelRatioF())
        pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        g = QPainter(pm)
        g.setRenderHint(QPainter.Antialiasing, True)
        g.setCompositionMode(QPainter.CompositionMode_Plus)
        for width, c, mul in reversed(layers):
            c = QColor(c)
            c.setAlphaF(mul)
            pen = QPen(c)
            pen.setWidthF(max(1.0, width))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            g.setPen(pen)
            g.setBrush(Qt.NoBrush)
            for line in q.pts:
                g.drawPolyline(QPolygonF([QPointF(x - x0, y - y0) for x, y in line]))
        g.end()
        return pm, float(x0), float(y0)

    @staticmethod
    def _paint_pop(p: QPainter, q: _Particle, col: QColor):
        """A bursting bubble: its ring snaps outwards and fades, flinging out a few tiny droplets."""
        t = q.popt / POP_TIME
        a = max(0.0, 1.0 - t)
        col.setAlphaF(0.9 * a)
        pen = QPen(col)
        pen.setWidthF(max(0.8, q.size * 0.25 * a))
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        rad = q.size * (1.0 + 0.9 * t)
        p.drawEllipse(QPointF(q.x, q.y), rad, rad)
        pen.setWidthF(1.1)
        p.setPen(pen)
        for i in range(7):                                                  # droplets flying outwards
            ang = q.phase + i * math.tau / 7
            r0, r1 = rad * 1.1 + t * q.size * 1.2, rad * 1.1 + t * q.size * 2.4 + 1.5
            p.drawLine(QPointF(q.x + math.cos(ang) * r0, q.y + math.sin(ang) * r0),
                       QPointF(q.x + math.cos(ang) * r1, q.y + math.sin(ang) * r1))
