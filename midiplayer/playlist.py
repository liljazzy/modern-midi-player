"""Playlist model: ordering, shuffle, repeat modes and M3U/JSON persistence."""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from typing import List, Optional

MIDI_EXTENSIONS = (".mid", ".midi", ".kar", ".rmi", ".smf")

REPEAT_OFF, REPEAT_ALL, REPEAT_ONE = 0, 1, 2


@dataclass
class PlaylistItem:
    path: str
    duration: Optional[float] = None
    title: str = ""

    def __post_init__(self):
        if not self.title:
            self.title = os.path.splitext(os.path.basename(self.path))[0]


def is_midi_file(path: str) -> bool:
    return path.lower().endswith(MIDI_EXTENSIONS)


def scan_folder(folder: str, recursive: bool = True) -> List[str]:
    out = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for f in sorted(files, key=str.lower):
            if is_midi_file(f):
                out.append(os.path.join(root, f))
        if not recursive:
            break
    return out


class Playlist:
    def __init__(self):
        self.items: List[PlaylistItem] = []
        self.current: int = -1
        self.shuffle = False
        self.repeat = REPEAT_OFF
        self._order: List[int] = []     # shuffle order
        self._history: List[int] = []

    def __len__(self):
        return len(self.items)

    # ----------------------------------------------------------- editing
    def add(self, paths: List[str], index: Optional[int] = None) -> int:
        new = [PlaylistItem(os.path.abspath(p)) for p in paths if is_midi_file(p)]
        if index is None or index < 0 or index > len(self.items):
            index = len(self.items)
        self.items[index:index] = new
        if self.current >= index:
            self.current += len(new)
        self._reshuffle()
        return len(new)

    def remove(self, indices: List[int]):
        for i in sorted(set(indices), reverse=True):
            if 0 <= i < len(self.items):
                del self.items[i]
                if self.current == i:
                    self.current = -1
                elif self.current > i:
                    self.current -= 1
        self._reshuffle()

    def clear(self):
        self.items.clear()
        self.current = -1
        self._order.clear()
        self._history.clear()

    def move(self, src: int, dst: int):
        if not (0 <= src < len(self.items)):
            return
        item = self.items.pop(src)
        dst = max(0, min(len(self.items), dst))
        self.items.insert(dst, item)
        if self.current == src:
            self.current = dst
        elif src < self.current <= dst:
            self.current -= 1
        elif dst <= self.current < src:
            self.current += 1
        self._reshuffle()

    def sort_by_title(self):
        cur = self.items[self.current] if 0 <= self.current < len(self.items) else None
        self.items.sort(key=lambda it: it.title.lower())
        self.current = self.items.index(cur) if cur else -1
        self._reshuffle()

    def set_shuffle(self, on: bool):
        self.shuffle = on
        self._reshuffle()

    def _reshuffle(self):
        self._order = list(range(len(self.items)))
        random.shuffle(self._order)
        self._history = [i for i in self._history if i < len(self.items)]

    # --------------------------------------------------------- navigation
    def current_item(self) -> Optional[PlaylistItem]:
        if 0 <= self.current < len(self.items):
            return self.items[self.current]
        return None

    def set_current(self, index: int):
        if 0 <= index < len(self.items):
            if self.current >= 0 and self.current != index:
                self._history.append(self.current)
            self.current = index

    def next_index(self, auto: bool = False) -> int:
        """Index of the next song or -1. ``auto`` = song ended by itself."""
        n = len(self.items)
        if n == 0:
            return -1
        if auto and self.repeat == REPEAT_ONE and self.current >= 0:
            return self.current
        if self.shuffle:
            if n == 1:
                return 0 if (self.repeat != REPEAT_OFF or not auto or self.current < 0) else -1
            played = set(self._history[-(n - 1):]) | {self.current}
            remaining = [i for i in self._order if i not in played]
            if remaining:
                return remaining[0]
            if self.repeat == REPEAT_ALL or not auto:
                self._reshuffle()
                self._history.clear()
                cands = [i for i in self._order if i != self.current]
                return cands[0] if cands else 0
            return -1
        nxt = self.current + 1
        if nxt >= n:
            return 0 if (self.repeat == REPEAT_ALL or not auto) else -1
        return nxt

    def prev_index(self) -> int:
        n = len(self.items)
        if n == 0:
            return -1
        if self.shuffle and self._history:
            return self._history.pop()
        prv = self.current - 1
        return prv if prv >= 0 else (n - 1 if self.repeat == REPEAT_ALL else 0)

    # -------------------------------------------------------- persistence
    def save_m3u(self, path: str):
        base = os.path.dirname(os.path.abspath(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for it in self.items:
                dur = int(round(it.duration)) if it.duration else -1
                f.write(f"#EXTINF:{dur},{it.title}\n")
                try:
                    rel = os.path.relpath(it.path, base)
                except ValueError:  # different drive on Windows
                    rel = it.path
                f.write(rel + "\n")

    def load_m3u(self, path: str) -> int:
        base = os.path.dirname(os.path.abspath(path))
        paths = []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                p = line if os.path.isabs(line) else os.path.normpath(os.path.join(base, line))
                paths.append(p)
        return self.add(paths)

    def to_json(self) -> str:
        return json.dumps({
            "items": [{"path": it.path, "duration": it.duration} for it in self.items],
            "current": self.current, "shuffle": self.shuffle, "repeat": self.repeat,
        })

    def from_json(self, text: str):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return
        self.clear()
        for d in data.get("items", []):
            p = d.get("path")
            if p and os.path.exists(p):
                self.items.append(PlaylistItem(p, d.get("duration")))
        cur = data.get("current", -1)
        self.current = cur if 0 <= cur < len(self.items) else -1
        self.shuffle = bool(data.get("shuffle", False))
        self.repeat = int(data.get("repeat", REPEAT_OFF)) % 3
        self._reshuffle()
