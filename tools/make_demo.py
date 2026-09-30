"""Generate an original multi-channel demo song (demos/demo_groove.mid) for
trying the mixer, solo/mute and the editor."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from midiplayer.midifile import META, MIDI, RawEvent  # noqa: E402
from midiplayer.song import Note, Song, Track          # noqa: E402

TPB = 480
BAR = TPB * 4


def track(song, name, ch, program, volume=100, pan=64, reverb=40):
    t = Track(name=name)
    t.events += [RawEvent(0, META, name.encode(), 0x03),
                 RawEvent(0, MIDI, bytes([0xC0 | ch, program])),
                 RawEvent(0, MIDI, bytes([0xB0 | ch, 7, volume])),
                 RawEvent(0, MIDI, bytes([0xB0 | ch, 10, pan])),
                 RawEvent(0, MIDI, bytes([0xB0 | ch, 91, reverb]))]
    song.tracks.append(t)
    return t


def main(out):
    s = Song.new(TPB, 104)
    s.tracks[0].name = "Conductor"
    s.tracks[0].events = [e for e in s.tracks[0].events if e.kind == META and e.meta_type != 0x03]
    s.tracks[0].events.append(RawEvent(0, META, bytes([0, 0]), 0x59))  # C major
    chords = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]  # Am F C G
    roots = [45, 41, 36, 43]
    bars = 16

    keys = track(s, "Electric Piano", 0, 4, 92, 50)
    for b in range(bars):
        ch = chords[b % 4]
        for beat in (0, 1.5, 2.5):
            st = int(b * BAR + beat * TPB)
            for p in ch:
                keys.notes.append(Note(st, st + int(TPB * 0.9), p + 12, 70 if beat else 84, 0))

    bass = track(s, "Finger Bass", 1, 33, 110, 64, 20)
    pattern = [(0, 0), (0.75, 0), (1.5, 7), (2, 0), (3, 12), (3.5, 7)]
    for b in range(bars):
        r = roots[b % 4]
        for pos, iv in pattern:
            st = int(b * BAR + pos * TPB)
            bass.notes.append(Note(st, st + TPB // 2 - 20, r + iv, 100 if pos in (0, 2) else 82, 1))

    pads = track(s, "Warm Pad", 2, 89, 70, 90, 80)
    for b in range(4, bars):
        for p in chords[b % 4]:
            pads.notes.append(Note(b * BAR, (b + 1) * BAR - 10, p, 60, 2))

    lead = track(s, "Flute Lead", 3, 73, 95, 40, 60)
    phrase = [(0, 76, 1), (1, 74, 0.5), (1.5, 72, 0.5), (2, 71, 1), (3, 72, 1),
              (4, 69, 1.5), (5.5, 71, 0.5), (6, 72, 1), (7, 74, 1)]
    for rep in range(2):
        base = (8 + rep * 4) * BAR
        for pos, p, ln in phrase + [(pos + 8, p + (2 if rep else 0), ln) for pos, p, ln in phrase[:6]]:
            st = int(base + pos * TPB)
            lead.notes.append(Note(st, st + int(ln * TPB) - 20, p, 96, 3))

    drums = track(s, "Drums", 9, 0, 105, 64, 30)
    for b in range(bars):
        o = b * BAR
        for i in range(8):
            drums.notes.append(Note(o + i * TPB // 2, o + i * TPB // 2 + 60, 42, 72 if i % 2 else 90, 9))
        for k in (0, int(2.5 * TPB)):
            drums.notes.append(Note(o + k, o + k + 60, 36, 110, 9))
        for sn in (TPB, 3 * TPB):
            drums.notes.append(Note(o + sn, o + sn + 60, 38, 100, 9))
        if b % 4 == 3:
            for i, p in enumerate((50, 48, 45, 43)):
                drums.notes.append(Note(o + 3 * TPB + i * TPB // 4, o + 3 * TPB + i * TPB // 4 + 60, p, 95, 9))
    drums.notes.append(Note(bars * BAR, bars * BAR + TPB, 49, 110, 9))
    drums.notes.append(Note(bars * BAR, bars * BAR + TPB, 36, 110, 9))
    s.invalidate()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    s.save(out)
    print(f"wrote {out}: {s.note_count()} notes, {s.duration():.1f}s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "demos", "demo_groove.mid"))
