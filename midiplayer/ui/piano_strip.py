"""Piano keyboard that lights up the notes being played, coloured by MIDI channel,
with configurable physics-driven particle effects (sparks, embers, bubbles, confetti, stars)."""
from __future__ import annotations

import json
import math
import random
import time
from dataclasses import asdict, dataclass, fields
from typing import Dict, List

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..gm import BLACK_KEYS, CHANNEL_COLORS
from . import theme

LOW, HIGH = 21, 108          # A0 .. C8, the 88 keys of a piano
KEYS_H = 76                  # height of the keyboard itself
MAX_PARTICLES = 900
BLACK_KEY_W = 0.62           # a black key is this share of a white key's width
EMIT_MARGIN = 0.08           # particles start this far in from each edge of the key (share of its width)
POP_TIME = 0.24              # how long a bubble's pop animation lasts (s)


def _is_black(pitch: int) -> bool:
    return pitch % 12 in BLACK_KEYS


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
    #   scale_h: lift and launch speed grow with the effect height (relative to 110 px), so the particle
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
    #   cone: keeps sideways speed proportional to rising speed, so the stream opens up like a cone
    #   apex: (min, max) share of the effect height a particle peaks at - its launch speed is worked out to
    #         hit that, so it always goes high without leaving the area (replaces speed)
    "sparks":   dict(label="Sparks",   burst=10, hold=0.6, speed=(140, 340), ttl=(1.1, 1.9), size=(1.6, 3.4), spread=0.7,
                     gravity=420, buoy=0, drag=0.35, flutter=0.0,
                     floor=True, rest=0.55, fric=0.25, shape="dot"),
    "embers":   dict(label="Embers",   burst=5,  hold=0.4,  speed=(15, 150), ttl=(1.6, 3.0), size=(2.0, 4.2), spread=0.65,
                     gravity=0, buoy=95, drag=0.65, scale_h=True, flutter=0.0, rise_var=(0.4, 1.9), speed_wobble=0.35, rect_chance=0.5,
                     jitter=60.0, linger=0.3, flicker=0.4, fade_top=0.35,
                     clumps=dict(renew=(0.35, 0.7), tight=0.12),
                     floor=False, rest=0.0, fric=0.0, shape="dot"),
    "bubbles":  dict(label="Bubbles",  burst=4,  hold=0.25, speed=(10, 45), ttl=(6.0, 8.0), size=(3.0, 7.0), spread=0.55,
                     gravity=0, buoy=55, drag=1.2, flutter=0.15, cone=12.0, pop=True,
                     floor=False, rest=0.0, fric=0.0, shape="ring"),
    "confetti": dict(label="Confetti", burst=12, hold=0.5,  apex=(0.55, 0.95), ttl=(8.0, 10.0), size=(2.5, 4.5), spread=0.45,
                     gravity=130, buoy=0, drag=0.25, qdrag=0.011, rest_time=(1.0, 2.0), fade=0.6, flutter=1.0,
                     jitter=110.0, jitter_speed=170.0,
                     floor=True, rest=0.2, fric=0.55, shape="rect"),
    "stars":    dict(label="Stars",    burst=5,  hold=0.3,  speed=(60, 150), ttl=(2.2, 3.6), size=(3.0, 6.0), spread=0.5,
                     gravity=20, buoy=25, drag=0.3, flutter=0.0,
                     floor=True, rest=0.6, fric=0.1, shape="star"),
}
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
    color_mode: str = "channel"
    color: str = "#ffffff"     # used by color_mode == "custom"
    height: int = 110          # headroom above the keys (px)

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
        if not QColor(cfg.color).isValid():
            cfg.color = "#ffffff"
        cfg.amount = max(0.0, min(4.0, cfg.amount))
        cfg.size = max(0.3, min(4.0, cfg.size))
        cfg.speed = max(0.2, min(4.0, cfg.speed))
        cfg.life = max(0.2, min(4.0, cfg.life))
        cfg.gravity = max(-100, min(100, cfg.gravity))
        cfg.bounce = max(0.0, min(1.5, cfg.bounce))
        cfg.height = max(20, min(240, cfg.height))
        return cfg


class _Clump:
    """A group of particles launched together in the same direction."""
    __slots__ = ("angle", "expire")


class _Particle:
    __slots__ = ("x", "y", "vx", "vy", "age", "ttl", "size", "color", "rot", "spin", "phase", "bf", "df", "pop", "popt", "slope", "landed", "clump", "rect")

    def __init__(self, x, y, vx, vy, ttl, size, color, rot, spin, phase):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.age, self.ttl, self.size, self.color = 0.0, ttl, size, color
        self.rot, self.spin, self.phase = rot, spin, phase
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
        shown = {**notes, **self._demo}
        if shown == self._active:
            return
        if self.effects_on():
            for pitch, ch in shown.items():
                if pitch not in self._active and LOW <= pitch <= HIGH:
                    self._burst(pitch, ch, round(KINDS[self.fx.kind]["burst"] * self.fx.amount))
        self._notes = notes
        self._active = shown
        self._ensure_timer()
        self.update()

    def _ensure_timer(self):
        if self.effects_on() and (self._active or self._particles) and not self._timer.isActive():
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
    def _color_for(self, pitch: int, ch: int) -> QColor:
        mode = self.fx.color_mode
        if mode == "custom":
            return QColor(self.fx.color)
        if mode == "rainbow":
            return QColor.fromHsvF(((pitch - LOW) / 40.0 + random.uniform(-0.04, 0.04)) % 1.0, 0.75, 1.0)
        return QColor(CHANNEL_COLORS[ch])

    def _burst(self, pitch: int, ch: int, n: int, hold: bool = False):
        span = self._key_span(pitch)
        if n <= 0 or span is None or len(self._particles) > MAX_PARTICLES or not self.effects_on():
            return
        lo, hi = span
        margin = (hi - lo) * EMIT_MARGIN
        lo, hi = lo + margin, hi - margin
        k, fx = KINDS[self.fx.kind], self.fx
        base = self._color_for(pitch, ch)
        y = self._key_top() - 1
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
                qd = k["qdrag"]
                need = math.sqrt(k["gravity"] / qd * (math.exp(2.0 * qd * height * 1.08) - 1.0))
                v = need / max(0.3, math.cos(off)) * fx.speed * (0.55 if hold else 1.0)
            else:
                v = random.uniform(*k["speed"]) * fx.speed * (0.55 if hold else 1.0)
            if k.get("scale_h"):
                v *= max(0.6, min(2.4, y / 110.0))
            c = QColor(base) if fx.color_mode == "rainbow" else QColor(base).lighter(random.randint(100, 150))
            p = _Particle(
                random.uniform(lo, hi), y, math.cos(ang) * v, math.sin(ang) * v,       # (anywhere across the key)
                random.uniform(*k["ttl"]) * fx.life, random.uniform(*k["size"]) * fx.size, c,
                random.uniform(0, math.tau), random.uniform(-6, 6), random.uniform(0, math.tau))
            p.clump = clump
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
            self._particles.append(p)

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

    # -------------------------------------------------------------- physics
    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        self.step(dt)

    def step(self, dt: float):
        """Advance the simulation by *dt* seconds (public so it can be driven from tests)."""
        if not self.effects_on():                  # nothing to animate while the effects are hidden
            self._particles.clear()
            self._timer.stop()
            return
        k, fx = KINDS[self.fx.kind], self.fx
        self._t += dt
        chance = min(1.0, k["hold"] * fx.amount)
        for pitch, ch in self._active.items():
            if random.random() < chance:
                self._burst(pitch, ch, 1, hold=True)

        gravity = k["gravity"] + fx.gravity * 6.0        # the user's gravity slider adds (or removes) pull
        buoy, flutter = k["buoy"], k["flutter"]
        if k.get("scale_h"):
            buoy *= max(0.6, min(2.4, (self._key_top() - 1.0) / 110.0))
        qdrag = k.get("qdrag", 0.0)
        drag = math.exp(-k["drag"] * dt)
        floor = self._key_top() - 1.0
        rest = k["rest"] * fx.bounce
        height, width = self.height(), self.width()
        t_now = self._t
        wobble = k.get("speed_wobble", 0.0)
        jitter, pops = k.get("jitter", 0.0), k.get("pop", False)
        jspeed, cone = k.get("jitter_speed", 0.0), k.get("cone", 0.0)
        rest_time = k.get("rest_time")
        alive = []
        for p in self._particles:
            p.age += dt
            if pops:
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
            lift = buoy * p.bf
            if wobble:                                     # its own lift swells and fades as it rises: speeds keep changing
                lift *= 1.0 + wobble * math.sin(p.age * (1.6 + p.phase * 0.3) + p.phase * 2.0)
            ax, ay = 0.0, gravity - lift
            if jitter:                                     # every particle meanders on its own random curve
                jf = jitter
                if jspeed:                                 # ...but only once it has slowed down
                    jf = jitter / (1.0 + (math.hypot(p.vx, p.vy) / jspeed) ** 2)
                w1 = 2.2 + p.phase * 0.55                  # (own frequencies from its random phase) plus some noise
                ax += jf * math.sin(p.age * w1 + p.phase * 3.0) + random.uniform(-1.0, 1.0) * jf * 0.35
                ay += jf * 0.7 * math.cos(p.age * (w1 + 0.9) + p.phase * 5.0) + random.uniform(-1.0, 1.0) * jf * 0.25
            if flutter:                                    # leaf-like swaying while falling / rising
                ax += math.sin(p.age * (5.0 + p.phase * 0.7) + p.phase * 2.0) * flutter * (abs(p.vy) + 20.0)
            if cone:                                       # hold the stream's shape: sideways speed follows rising speed
                ax += (p.slope * max(0.0, -p.vy) - p.vx) * cone
            if qdrag:                                      # fast = braked hard, slow = barely braked
                sp = math.hypot(p.vx, p.vy)
                ax -= qdrag * sp * p.vx
                ay -= qdrag * sp * p.vy
            pd = drag if p.df == 1.0 else math.exp(-k["drag"] * p.df * dt)
            p.vx = (p.vx + ax * dt) * pd
            p.vy = (p.vy + ay * dt) * pd
            p.x += p.vx * dt
            p.y += p.vy * dt
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
            if k["shape"] == "rect":                       # confetti tumbles faster the faster it moves
                p.rot += (p.spin + p.vx * 0.04) * dt
            else:
                p.rot += p.spin * dt
            if -40 < p.y < height + 20 and -30 < p.x < width + 30:
                alive.append(p)
        self._particles = alive
        if not self._particles and not self._active:
            self._timer.stop()
        self.update()

    # -------------------------------------------------------------- paint
    def paintEvent(self, _e):
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
            p.setBrush(QColor(CHANNEL_COLORS[ch]) if ch is not None else QColor("#e9ecf4"))
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
            p.setBrush(QColor(CHANNEL_COLORS[ch]).darker(115) if ch is not None else QColor("#1a1d26"))
            p.drawRect(QRectF((left + 1) * kw - bw / 2, top, bw, bh))

        if self._particles:
            self._paint_particles(p)
        p.end()

    def _paint_particles(self, p: QPainter):
        fx = self.fx
        shape = KINDS[fx.kind]["shape"]
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setCompositionMode(QPainter.CompositionMode_Plus if fx.glow else QPainter.CompositionMode_SourceOver)
        pops, flicker = KINDS[fx.kind].get("pop", False), KINDS[fx.kind].get("flicker", 0.0)
        fade = KINDS[fx.kind].get("fade", 0.0)
        fade_top, top = KINDS[fx.kind].get("fade_top", 0.0), max(1.0, self._key_top())
        for q in self._particles:
            if pops:                                                        # bubbles stay until they pop
                life = min(1.0, q.age / 0.25)
            elif fade:                                                      # full brightness until the last moment
                life = max(0.0, min(1.0, (q.ttl - q.age) / fade))
            else:
                life = 1.0 - q.age / q.ttl
            r = q.size * (0.4 + 0.6 * life)
            col = QColor(q.color)
            if q.popt >= 0.0:
                self._paint_pop(p, q, col)
                continue
            if shape == "star":                                             # twinkle
                life *= 0.65 + 0.35 * math.sin(q.age * 14 + q.phase)
            if fade_top:                                                    # dissolve into the top edge instead of being cut off
                life *= max(0.0, min(1.0, q.y / (fade_top * top)))
            if flicker:                                                     # embers flicker as they burn
                life *= 1.0 - flicker * (0.5 + 0.5 * math.sin(q.age * 13 + q.phase * 3))
            if fx.glow:
                halo = QColor(col)
                halo.setAlphaF(max(0.0, (0.07 if shape == "ring" else 0.18) * life))
                p.setPen(Qt.NoPen)
                p.setBrush(halo)
                p.drawEllipse(QPointF(q.x, q.y), r * 3.2, r * 3.2)
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
