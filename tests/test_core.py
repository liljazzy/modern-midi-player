import os
import sys
import tempfile
import time
import unittest

# keep the engine's idle-note diagnostic log out of the real profile
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from midiplayer import midifile                      # noqa: E402
from midiplayer.backends import RecordingBackend     # noqa: E402
from midiplayer.engine import Engine                 # noqa: E402
from midiplayer.playlist import Playlist, REPEAT_ALL, REPEAT_OFF, REPEAT_ONE  # noqa: E402
from midiplayer.song import Note, Song, UndoStack    # noqa: E402


def make_song(bpm=120):
    s = Song.new(480, bpm)
    s.tracks[0].notes = [Note(i * 480, i * 480 + 240, 60 + i, 100, 0) for i in range(4)]
    t = s.add_track("Bass")
    s.tracks[t].events.append(midifile.RawEvent(0, midifile.MIDI, bytes([0xC1, 33])))
    s.tracks[t].events.append(midifile.RawEvent(0, midifile.MIDI, bytes([0xB1, 7, 80])))
    s.tracks[t].notes = [Note(i * 480, i * 480 + 400, 36, 90, 1) for i in range(4)]
    s.tracks.append(type(s.tracks[0])(name="Drums"))
    s.tracks[2].notes = [Note(i * 240, i * 240 + 60, 42, 70, 9) for i in range(8)]
    s.invalidate()
    return s


class MidiFileTests(unittest.TestCase):
    def test_roundtrip(self):
        s = make_song()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.mid")
            s.save(p)
            s2 = Song.load(p)
        self.assertEqual(len(s2.tracks), 3)
        self.assertEqual([n.as_tuple() for n in s2.tracks[0].notes], [n.as_tuple() for n in s.tracks[0].notes])
        self.assertEqual(s2.tracks[1].name, "Bass")
        self.assertAlmostEqual(s2.duration(), 2.0 - 0.25 + 0.0 + 0.0, delta=0.3)
        self.assertEqual(s2.initial_programs()[1], 33)

    def test_running_status_and_format0_split(self):
        # format 0, running status, note-on vel 0 as note-off, two channels
        trk = bytes([0x00, 0xFF, 0x51, 0x03, 0x07, 0xA1, 0x20,
                     0x00, 0x90, 60, 100,
                     0x00, 62, 100,             # running status
                     0x00, 0x91, 40, 90,
                     0x83, 0x60, 0x90, 60, 0,   # delta 480
                     0x00, 62, 0,
                     0x00, 0x81, 40, 0,
                     0x00, 0xFF, 0x2F, 0x00])
        data = b"MThd" + (6).to_bytes(4, "big") + bytes([0, 0, 0, 1, 0x01, 0xE0]) + \
            b"MTrk" + len(trk).to_bytes(4, "big") + trk
        s = Song.from_raw(midifile.parse_bytes(data))
        self.assertEqual(len(s.tracks), 3)       # conductor + ch1 + ch2
        self.assertEqual(s.note_count(), 3)
        self.assertAlmostEqual(s.duration(), 0.5, places=3)

    def test_truncated_file_does_not_crash(self):
        s = make_song()
        data = midifile.to_bytes(s.to_raw())
        raw = midifile.parse_bytes(data[:-20])
        Song.from_raw(raw)

    def test_rmid(self):
        data = midifile.to_bytes(make_song().to_raw())
        riff = b"RIFF" + (len(data) + 12).to_bytes(4, "little") + b"RMID" + b"data" + \
            len(data).to_bytes(4, "little") + data
        self.assertEqual(Song.from_raw(midifile.parse_bytes(riff)).note_count(), 16)

    def test_tempo_map(self):
        s = Song.new(480, 120)
        s.tracks[0].events.append(midifile.RawEvent(960, midifile.META, (1_000_000).to_bytes(3, "big"), 0x51))
        s.invalidate()
        tm = s.tempo_map
        self.assertAlmostEqual(tm.tick_to_sec(960), 1.0)
        self.assertAlmostEqual(tm.tick_to_sec(1440), 2.0)
        self.assertAlmostEqual(tm.sec_to_tick(2.0), 1440)

    def test_undo(self):
        s = make_song()
        u = UndoStack(s)
        u.push()
        s.tracks[0].notes.clear()
        self.assertTrue(u.undo())
        self.assertEqual(len(s.tracks[0].notes), 4)
        self.assertTrue(u.redo())
        self.assertEqual(len(s.tracks[0].notes), 0)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.rec = RecordingBackend()
        self.eng = Engine(self.rec)
        self.song = make_song(bpm=480)  # 4x fast: 0.125 s per beat
        self.eng.load(self.song)

    def tearDown(self):
        self.eng.close()

    def notes_on(self, ch=None):
        return [m for m in self.rec.messages if m[0] & 0xF0 == 0x90 and m[2] > 0 and (ch is None or m[0] & 15 == ch)]

    def play_through(self):
        done = []
        self.eng.on_finished = lambda: done.append(1)
        self.eng.play()
        t0 = time.time()
        while not done and time.time() - t0 < 5:
            time.sleep(0.01)
        self.assertTrue(done, "playback did not finish")

    def test_plays_all_notes(self):
        self.play_through()
        self.assertEqual(len(self.notes_on()), 16)
        # every note-on has a note-off
        offs = [m for m in self.rec.messages if m[0] & 0xF0 == 0x80]
        self.assertGreaterEqual(len(offs), 16)

    def test_sustain_released_when_silenced(self):
        self.rec.messages.clear()
        self.eng.pause()
        for ch in range(16):
            pedal = [i for i, m in enumerate(self.rec.messages) if m[0] == 0xB0 | ch and m[1] == 64 and m[2] == 0]
            off = [i for i, m in enumerate(self.rec.messages) if m[0] == 0xB0 | ch and m[1] == 123]
            self.assertTrue(pedal and off and pedal[0] < off[0], f"ch {ch}: pedal up before all-notes-off")

    def test_even_dynamics_keeps_ghost_notes_quiet(self):
        from midiplayer.engine import even_velocity
        self.assertEqual(even_velocity(127), 103)
        self.assertEqual(even_velocity(80), 80)
        self.assertEqual(even_velocity(40), 60)
        self.assertEqual([even_velocity(v) for v in (1, 5, 23)], [1, 5, 23])   # never made audible
        self.eng.even_dynamics = True
        with self.eng.lock:
            self.eng._dispatch(bytes([0x90, 60, 5]))
        self.assertEqual(self.notes_on(0)[-1][2], 5)

    def test_active_notes_for_piano(self):
        self.eng.set_transpose(2)
        with self.eng.lock:
            self.eng._dispatch(bytes([0x91, 60, 90]))
            self.eng._dispatch(bytes([0x99, 42, 90]))      # drums are not shown on the piano
        self.assertEqual(self.eng.active_notes(), {62: 1})
        with self.eng.lock:
            self.eng._dispatch(bytes([0x81, 60, 0]))
        self.assertEqual(self.eng.active_notes(), {})

    def test_note_events_for_the_piano_effects(self):
        """Every note-on is reported with its time - even a note that is over before the piano looks, and quick repeats."""
        self.eng.set_transpose(2)
        with self.eng.lock:
            self.eng._dispatch(bytes([0x91, 60, 90]))
            self.eng._dispatch(bytes([0x81, 60, 0]))        # gone again immediately: active_notes() never sees it
            self.eng._dispatch(bytes([0x91, 60, 70]))       # the same key struck again
            self.eng._dispatch(bytes([0x99, 42, 90]))       # drums are left out
        self.assertEqual(self.eng.active_notes(), {62: 1})
        events = self.eng.take_note_events()
        self.assertEqual([(e[1], e[2], e[3]) for e in events], [(62, 1, 90), (62, 1, 70)])
        self.assertTrue(events[0][0] <= events[1][0])
        self.assertEqual(self.eng.take_note_events(), [])    # taken: not reported twice

    def test_keyboard_notes_are_events_too(self):
        self.eng.live_message(bytes([0x90, 64, 100]))
        self.eng.live_message(bytes([0x90, 64, 0]))
        self.assertEqual([(e[1], e[2], e[3]) for e in self.eng.take_note_events()], [(64, 0, 100)])

    def test_mute_and_solo(self):
        self.eng.set_mute(9, True)
        self.play_through()
        self.assertEqual(len(self.notes_on(9)), 0)
        self.assertEqual(len(self.notes_on(0)), 4)
        self.rec.messages.clear()
        self.eng.set_mute(9, False)
        self.eng.set_solo(1, True)
        self.play_through()
        self.assertEqual(len(self.notes_on()), 4)
        self.assertEqual(len(self.notes_on(1)), 4)

    def test_volume_scales_cc7(self):
        self.eng.set_volume(1, 0.5)
        cc7 = [m for m in self.rec.messages if m[0] == 0xB1 and m[1] == 7]
        self.assertEqual(cc7[-1][2], 40)   # file volume at t=0 (80) * 0.5, chased before play
        self.play_through()
        cc7 = [m for m in self.rec.messages if m[0] == 0xB1 and m[1] == 7]
        self.assertEqual(cc7[-1][2], 40)   # file sets 80 -> 40
        self.eng.set_master(0.5)
        cc7 = [m for m in self.rec.messages if m[0] == 0xB1 and m[1] == 7]
        self.assertEqual(cc7[-1][2], 20)

    def test_seek_chases_program(self):
        self.eng.seek(0.3)
        progs = [m for m in self.rec.messages if m[0] == 0xC1]
        self.assertEqual(progs[-1][1], 33)
        self.assertEqual(self.eng.channels[1].file_volume, 80)

    def test_program_override(self):
        self.eng.set_program(1, 5)
        self.play_through()
        progs = [m for m in self.rec.messages if m[0] == 0xC1]
        self.assertEqual(progs[-1][1], 5)

    def test_transpose_and_drums(self):
        self.eng.set_transpose(2)
        self.play_through()
        self.assertEqual(sorted({m[1] for m in self.notes_on(0)}), [62, 63, 64, 65])
        self.assertEqual({m[1] for m in self.notes_on(9)}, {42})

    def test_pause_resume_position(self):
        self.eng.play()
        time.sleep(0.2)
        self.eng.pause()
        p = self.eng.position()
        time.sleep(0.1)
        self.assertAlmostEqual(self.eng.position(), p, places=4)
        self.assertGreater(p, 0.1)

    def test_timing_accuracy(self):
        self.play_through()


class PlaylistTests(unittest.TestCase):
    def test_navigation(self):
        pl = Playlist()
        pl.add(["a.mid", "b.mid", "c.mid", "x.txt"])
        self.assertEqual(len(pl), 3)
        pl.set_current(0)
        self.assertEqual(pl.next_index(auto=True), 1)
        pl.set_current(2)
        self.assertEqual(pl.next_index(auto=True), -1)
        pl.repeat = REPEAT_ALL
        self.assertEqual(pl.next_index(auto=True), 0)
        pl.repeat = REPEAT_ONE
        self.assertEqual(pl.next_index(auto=True), 2)
        self.assertEqual(pl.next_index(auto=False), 0)
        pl.repeat = REPEAT_OFF
        pl.move(2, 0)
        self.assertEqual(pl.current, 0)

    def test_shuffle_visits_all(self):
        pl = Playlist()
        pl.add([f"{i}.mid" for i in range(10)])
        pl.set_shuffle(True)
        pl.set_current(0)
        seen = {0}
        for _ in range(9):
            i = pl.next_index(auto=True)
            self.assertNotIn(i, seen)
            seen.add(i)
            pl.set_current(i)
        self.assertEqual(pl.next_index(auto=True), -1)

    def test_m3u(self):
        with tempfile.TemporaryDirectory() as d:
            for n in "ab":
                open(os.path.join(d, n + ".mid"), "wb").close()
            pl = Playlist()
            pl.add([os.path.join(d, "a.mid"), os.path.join(d, "b.mid")])
            p = os.path.join(d, "list.m3u")
            pl.save_m3u(p)
            pl2 = Playlist()
            self.assertEqual(pl2.load_m3u(p), 2)
            self.assertEqual(pl2.items[1].path, os.path.join(d, "b.mid"))
            pl3 = Playlist()
            pl3.from_json(pl.to_json())
            self.assertEqual(len(pl3), 2)


class _FakeMidiIn:
    """Stands in for rtmidi.MidiIn so the input code can be tested without any MIDI hardware."""
    ports = ["Fake Keyboard", "Other Pad"]
    instances = []

    def __init__(self):
        self.callback = None
        self.opened = None
        self.ignored = None
        _FakeMidiIn.instances.append(self)

    def get_ports(self):
        return list(_FakeMidiIn.ports)

    def ignore_types(self, **kw):
        self.ignored = kw

    def open_port(self, i):
        self.opened = i

    def set_callback(self, cb):
        self.callback = cb

    def cancel_callback(self):
        self.callback = None

    def close_port(self):
        self.opened = None

    def play(self, *msg):
        """What the device's thread does when a key is pressed."""
        if self.callback:
            self.callback((list(msg), 0.0))


class LiveInputTests(unittest.TestCase):
    """A MIDI keyboard: its notes play through the output and are tracked so the piano can show them."""

    def setUp(self):
        self.rec = RecordingBackend()
        self.eng = Engine(self.rec)
        self._saved = sys.modules.get("rtmidi", "absent")
        fake = type(sys)("rtmidi")
        fake.MidiIn = _FakeMidiIn
        sys.modules["rtmidi"] = fake
        _FakeMidiIn.instances.clear()

    def tearDown(self):
        self.eng.close()
        if self._saved == "absent":
            sys.modules.pop("rtmidi", None)
        else:
            sys.modules["rtmidi"] = self._saved

    # ---- the engine
    def test_notes_play_and_are_tracked(self):
        self.eng.live_message(bytes([0x90, 60, 100]))
        self.assertIn(bytes([0x90, 60, 100]), self.rec.messages)
        self.assertEqual(self.eng.active_notes(), {60: 0})
        self.eng.live_message(bytes([0x80, 60, 0]))
        self.assertIn(bytes([0x80, 60, 0]), self.rec.messages)
        self.assertEqual(self.eng.active_notes(), {})

    def test_note_on_with_zero_velocity_is_a_note_off(self):
        self.eng.live_message(bytes([0x91, 64, 90]))
        self.eng.live_message(bytes([0x91, 64, 0]))
        self.assertEqual(self.eng.active_notes(), {})
        self.assertIn(bytes([0x81, 64, 0]), self.rec.messages)

    def test_other_messages_pass_through_and_system_messages_do_not(self):
        for msg in (bytes([0xB0, 64, 127]), bytes([0xE0, 0, 80]), bytes([0xC0, 5])):
            self.eng.live_message(msg)
            self.assertIn(msg, self.rec.messages)
        n = len(self.rec.messages)
        for msg in (bytes([0xF8]), bytes([0xFE]), bytes([0xF0, 1, 2, 0xF7]), b""):
            self.eng.live_message(msg)
        self.assertEqual(len(self.rec.messages), n)

    def test_drums_are_not_shown_on_the_piano(self):
        self.eng.live_message(bytes([0x99, 38, 100]))
        self.assertEqual(self.eng.active_notes(), {})

    def test_muted_channel_stays_quiet_but_can_still_release(self):
        self.eng.set_mute(0, True)
        self.eng.live_message(bytes([0x90, 60, 100]))
        self.assertNotIn(bytes([0x90, 60, 100]), self.rec.messages)
        self.assertEqual(self.eng.active_notes(), {})
        self.eng.live_message(bytes([0x80, 60, 0]))
        self.assertIn(bytes([0x80, 60, 0]), self.rec.messages)

    def test_live_off_releases_everything_held(self):
        self.eng.live_message(bytes([0x90, 60, 100]))
        self.eng.live_message(bytes([0x92, 72, 100]))
        self.eng.live_off()
        self.assertEqual(self.eng.active_notes(), {})
        self.assertIn(bytes([0x80, 60, 0]), self.rec.messages)
        self.assertIn(bytes([0x82, 72, 0]), self.rec.messages)

    def test_changing_the_output_forgets_held_notes(self):
        self.eng.live_message(bytes([0x90, 60, 100]))
        self.eng.set_backend(RecordingBackend())
        self.assertEqual(self.eng.active_notes(), {})

    def test_live_notes_show_alongside_a_playing_song(self):
        self.eng.load(make_song(bpm=480))
        self.eng.live_message(bytes([0x93, 80, 100]))
        self.assertEqual(self.eng.active_notes().get(80), 3)

    def test_live_notes_are_not_logged_as_stray_idle_notes(self):
        log = os.path.join(os.environ["LOCALAPPDATA"], "ModernMidiPlayer", "idle-notes.log")
        before = os.path.getsize(log) if os.path.exists(log) else 0
        self.eng.live_message(bytes([0x90, 60, 100]))
        self.assertEqual(os.path.getsize(log) if os.path.exists(log) else 0, before)

    # ---- the input device
    def test_input_ports_are_listed(self):
        from midiplayer.backends import list_rtmidi_inputs
        self.assertEqual(list_rtmidi_inputs(), ["Fake Keyboard", "Other Pad"])

    def test_without_rtmidi_there_are_no_inputs_and_opening_fails_cleanly(self):
        from midiplayer.backends import BackendError, MidiInput, list_rtmidi_inputs, rtmidi_available
        sys.modules["rtmidi"] = None                         # makes "import rtmidi" fail
        self.assertEqual(list_rtmidi_inputs(), [])
        self.assertFalse(rtmidi_available())
        with self.assertRaises(BackendError):
            MidiInput("Fake Keyboard", lambda m: None)

    def test_opening_an_input_forwards_its_messages(self):
        from midiplayer.backends import MidiInput
        got = []
        inp = MidiInput("Other Pad", got.append)
        dev = _FakeMidiIn.instances[-1]
        self.assertEqual(dev.opened, 1)
        self.assertEqual(dev.ignored, dict(sysex=True, timing=True, active_sense=True))
        dev.play(0x90, 60, 100)
        dev.play(0xB0, 64, 127)
        self.assertEqual(got, [bytes([0x90, 60, 100]), bytes([0xB0, 64, 127])])
        self.assertEqual(inp.name, "Other Pad")

    def test_unknown_input_is_an_error(self):
        from midiplayer.backends import BackendError, MidiInput
        with self.assertRaises(BackendError):
            MidiInput("Not Plugged In", lambda m: None)

    def test_closing_an_input_stops_the_messages(self):
        from midiplayer.backends import MidiInput
        got = []
        inp = MidiInput("Fake Keyboard", got.append)
        dev = _FakeMidiIn.instances[-1]
        inp.close()
        self.assertIsNone(dev.callback)
        self.assertIsNone(dev.opened)
        inp._on_message(([0x90, 60, 100], 0.0))              # (a late message from the device's thread)
        self.assertEqual(got, [])

    def test_a_keyboard_plays_through_the_engine(self):
        from midiplayer.backends import MidiInput
        inp = MidiInput("Fake Keyboard", self.eng.live_message)
        dev = _FakeMidiIn.instances[-1]
        dev.play(0x90, 67, 90)
        self.assertEqual(self.eng.active_notes(), {67: 0})
        self.assertIn(bytes([0x90, 67, 90]), self.rec.messages)
        dev.play(0x80, 67, 0)
        self.assertEqual(self.eng.active_notes(), {})
        inp.close()

    def test_a_failing_callback_does_not_break_the_device(self):
        from midiplayer.backends import MidiInput
        def boom(msg):
            raise RuntimeError("bad")
        MidiInput("Fake Keyboard", boom)
        _FakeMidiIn.instances[-1].play(0x90, 60, 100)         # must not raise


def replace_cfg(cfg, **changes):
    from dataclasses import replace
    return replace(cfg, **changes)


class HumanizeTests(unittest.TestCase):
    """Playback can nudge notes like a person plays them, without touching the song."""

    @staticmethod
    def piano_song(bars=16, bpm=120, runs=True):
        s = Song.new(480, bpm)
        import random as _r
        rng = _r.Random(5)
        tr = s.tracks[0]
        for b in range(bars):
            t = b * 1920
            tr.notes.append(Note(t, t + 1800, 36 + (b % 4), 80, 0))                        # a bass note under each bar
            for beat in range(4):                                                        # a chord on every beat
                for p in (60, 64, 67, 72):
                    tr.notes.append(Note(t + beat * 480, t + beat * 480 + 400, p, 90, 0))
            for k in range(8 if runs else 0):                                            # and a run of single notes
                tr.notes.append(Note(t + k * 240, t + k * 240 + 200, 76 + rng.randint(0, 7), 70, 0))
        tr.notes.sort(key=lambda n: n.start)
        s.invalidate()
        return s

    @staticmethod
    def note_times(events, ch=0):
        """[(time, pitch, velocity)] of every note-on and [(time, pitch)] of every note-off."""
        on = [(e.time, e.data[1], e.data[2]) for e in events if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0 and e.data[0] & 15 == ch]
        off = [(e.time, e.data[1]) for e in events if e.data[0] & 0xF0 == 0x80 and e.data[0] & 15 == ch]
        return on, off

    def test_off_is_exactly_as_written(self):
        s = self.piano_song()
        base = s.playback_events()
        self.assertEqual([(e.time, e.data) for e in s.playback_events(0.0, 123)], [(e.time, e.data) for e in base])

    def test_same_seed_same_performance_different_seed_differs(self):
        s = self.piano_song()
        a = [(e.time, e.data) for e in s.playback_events(0.7, 11)]
        b = [(e.time, e.data) for e in s.playback_events(0.7, 11)]
        c = [(e.time, e.data) for e in s.playback_events(0.7, 12)]
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_song_itself_is_untouched(self):
        s = self.piano_song()
        before = [n.as_tuple() for n in s.tracks[0].notes]
        s.playback_events(1.0, 3)
        self.assertEqual([n.as_tuple() for n in s.tracks[0].notes], before)

    def test_every_note_on_still_has_its_off_in_order_and_nothing_overlaps_itself(self):
        s = self.piano_song()
        for strength in (0.4, 0.85, 1.7):
            ev = s.playback_events(strength, 7)
            self.assertEqual([e.time for e in ev], sorted(e.time for e in ev))
            on, off = self.note_times(ev)
            self.assertEqual(len(on), len(off))
            held = {}
            for e in ev:
                st = e.data[0] & 0xF0
                if st == 0x90 and e.data[2] > 0:
                    self.assertNotIn(e.data[1], held, "a key was struck again while still held")
                    held[e.data[1]] = e.time
                elif st == 0x80:
                    self.assertIn(e.data[1], held)
                    self.assertGreater(e.time, held.pop(e.data[1]))
            self.assertEqual(held, {})

    def test_timing_is_centred_bounded_and_grows_with_strength(self):
        s = self.piano_song()
        base = s.playback_events()
        ref = [e for e in base if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0]
        by_pitch = {}
        for r in ref:
            by_pitch.setdefault(r.data[1], []).append(r)
        spreads = {}
        for strength in (0.4, 1.0, 1.7):
            ev = s.playback_events(strength, 4)
            new = [e for e in ev if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0]
            self.assertEqual(len(ref), len(new))
            diffs = []
            for e in new:
                nearest = min(by_pitch[e.data[1]], key=lambda r: abs(r.time - e.time))
                diffs.append(e.time - nearest.time)
            mean = sum(diffs) / len(diffs)
            sd = (sum((d - mean) ** 2 for d in diffs) / len(diffs)) ** 0.5
            self.assertLess(abs(mean), 0.012)
            self.assertLessEqual(max(abs(d) for d in diffs), 0.13)
            self.assertGreater(sd, 0.002)
            spreads[strength] = sd
        self.assertLess(spreads[0.4], spreads[1.0] * 0.7)
        self.assertLess(spreads[1.0], spreads[1.7] * 0.8)                      # Loose is clearly looser than Natural

    def test_velocities_vary_but_stay_in_range_and_keep_their_average(self):
        s = self.piano_song()
        base_on, _ = self.note_times(s.playback_events())
        on, _ = self.note_times(s.playback_events(1.0, 9))
        v0 = [v for _, _, v in base_on]
        v1 = [v for _, _, v in on]
        self.assertTrue(all(1 <= v <= 127 for v in v1))
        self.assertLess(abs(sum(v1) / len(v1) - sum(v0) / len(v0)), 5)
        self.assertGreater(len(set(v1)), len(set(v0)) + 10)

    def test_ghost_notes_and_drums_are_left_alone(self):
        s = Song.new(480, 120)
        tr = s.tracks[0]
        tr.notes = [Note(i * 480, i * 480 + 200, 60, 12, 0) for i in range(20)]            # ghost notes (velocity 12)
        tr.notes += [Note(i * 480, i * 480 + 100, 38, 100, 9) for i in range(20)]          # drums
        s.invalidate()
        base = s.playback_events()
        ev = s.playback_events(1.0, 5)
        on_b, _ = self.note_times(base, 0)
        on_h, _ = self.note_times(ev, 0)
        self.assertEqual([v for _, _, v in on_b], [v for _, _, v in on_h])                  # ghost velocity untouched
        drums_b = [(e.time, e.data) for e in base if e.data[0] & 15 == 9]
        drums_h = [(e.time, e.data) for e in ev if e.data[0] & 15 == 9]
        self.assertEqual(drums_b, drums_h)                                                  # drums exactly on the grid

    def test_chords_are_rolled_with_the_melody_leaning_ahead_and_louder(self):
        s = self.piano_song(bars=24, runs=False)                    # (just chords: 72 is the top note of each)
        ev = s.playback_events(1.0, 21)
        base = s.playback_events()
        starts = sorted({round(e.time, 4) for e in base if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0 and e.data[1] in (60, 64, 67, 72)})
        spreads, top_v, inner_v = [], [], []
        on = [e for e in ev if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0]
        for st in starts:
            group = [e for e in on if abs(e.time - st) < 0.06 and e.data[1] in (60, 64, 67, 72)]
            if len(group) == 4:
                spreads.append(max(e.time for e in group) - min(e.time for e in group))
                top_v.append(next(e.data[2] for e in group if e.data[1] == 72))
                inner_v += [e.data[2] for e in group if e.data[1] in (64, 67)]
        self.assertGreater(len(spreads), 10)
        self.assertTrue(all(sp > 0.0005 for sp in spreads))                                  # never struck at the same instant
        self.assertLess(max(spreads), 0.09)                                                  # ...but still one chord
        self.assertGreater(sum(top_v) / len(top_v), sum(inner_v) / len(inner_v) + 4)         # melody note is louder

    def test_a_note_never_starts_before_its_instrument_is_set(self):
        s = Song.new(480, 120)            # Song.new sets program 0 on channel 0 at tick 0
        s.tracks[0].notes = [Note(0, 240, 60, 90, 0), Note(0, 240, 64, 90, 0), Note(0, 240, 67, 90, 0)]
        s.invalidate()
        for seed in range(40):
            ev = s.playback_events(1.0, seed)
            prog = next(e.time for e in ev if e.data[0] == 0xC0)
            first_on = min(e.time for e in ev if e.data[0] & 0xF0 == 0x90)
            self.assertGreaterEqual(first_on, prog)

    def test_released_notes_are_not_cut_below_a_short_tap(self):
        s = self.piano_song()
        ev = s.playback_events(1.0, 2)
        held = {}
        for e in ev:
            if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0:
                held[e.data[1]] = e.time
            elif e.data[0] & 0xF0 == 0x80:
                self.assertGreaterEqual(e.time - held[e.data[1]], 0.0099)

    def test_engine_plays_a_humanized_song_completely(self):
        rec = RecordingBackend()
        eng = Engine(rec)
        try:
            eng.humanize = 0.7
            song = make_song(bpm=480)
            eng.load(song)
            self.assertTrue(eng.humanize_seed)
            seed = eng.humanize_seed
            plain = song.playback_events()
            self.assertEqual(len(eng.events), len(plain))
            self.assertNotEqual([e.time for e in eng.events], [e.time for e in plain])
            self.assertGreaterEqual(eng.duration, eng.events[-1].time)
            eng.load(song, keep_position=True, reset_mixer=False)          # e.g. after an edit: the same performance
            self.assertEqual(eng.humanize_seed, seed)
            done = []
            eng.on_finished = lambda: done.append(1)
            eng.play()
            t0 = time.time()
            while not done and time.time() - t0 < 5:
                time.sleep(0.01)
            self.assertTrue(done)
            ons = [m for m in rec.messages if m[0] & 0xF0 == 0x90 and m[2] > 0]
            offs = [m for m in rec.messages if m[0] & 0xF0 == 0x80]
            self.assertEqual(len(ons), 16)
            self.assertGreaterEqual(len(offs), 16)
            eng.load(song)                                                  # a fresh load is a new performance
            self.assertNotEqual(eng.humanize_seed, seed)
        finally:
            eng.close()

    def test_fast_passages_keep_their_note_order(self):
        """16th notes at 240 BPM are 62 ms apart: the nudges must never swap or smear them."""
        for strength in (0.4, 0.85, 1.7, 2.0):
            for seed in range(25):
                s = Song.new(480, 240)
                s.tracks[0].notes = [Note(i * 120, i * 120 + 100, 60 + (i % 12), 90, 0) for i in range(48)]
                s.invalidate()
                ev = s.playback_events(strength, seed)
                pitches = [e.data[1] for e in ev if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0]
                self.assertEqual(pitches, [60 + (i % 12) for i in range(48)])

    # ------------------------------------------------------------ customisable
    @staticmethod
    def melody_song(n=60, bpm=120):
        s = Song.new(480, bpm)
        s.tracks[0].notes = [Note(i * 480, i * 480 + 400, 60 + (i % 24), 80, 0) for i in range(n)]      # single notes, no repeats in a row
        s.invalidate()
        return s

    @staticmethod
    def chord_song(n=20):
        s = Song.new(480, 120)
        s.tracks[0].notes = [Note(i * 960, i * 960 + 800, p, 90, 0) for i in range(n) for p in (60, 64, 67, 72)]
        s.invalidate()
        return s

    @staticmethod
    def onsets(ev, ch=0):
        return [e for e in ev if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0 and e.data[0] & 15 == ch]

    def offsets(self, song, cfg, seed=3, ch=0):
        base = self.onsets(song.playback_events(), ch)
        new = self.onsets(song.playback_events(cfg, seed), ch)
        return [(n.time - b.time) * 1000 for b, n in zip(base, new)]

    def test_config_defaults_match_the_plain_strength(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.piano_song()
        a = [(e.time, e.data) for e in s.playback_events(0.85, 6)]
        b = [(e.time, e.data) for e in s.playback_events(HumanizeConfig(strength=0.85), 6)]
        self.assertEqual(a, b)

    def test_config_json_clamps_and_names_presets(self):
        from midiplayer.humanize import HumanizeConfig, LEVELS
        c = HumanizeConfig(strength=1.2, timing=0.4, drums=True)
        self.assertEqual(HumanizeConfig.from_json(c.to_json()), c)
        self.assertEqual(HumanizeConfig.from_json('{"strength": 99, "timing": -3, "drift": 40}').strength, 2.0)
        bad = HumanizeConfig.from_json('{"strength": 99, "timing": -3, "drift": 40}')
        self.assertEqual((bad.timing, bad.drift), (0.0, 3.0))
        self.assertEqual(HumanizeConfig.from_json("nonsense"), HumanizeConfig())
        for name in LEVELS:
            self.assertEqual(HumanizeConfig.preset(name).preset_name(), name)
        self.assertIsNone(HumanizeConfig(strength=LEVELS["natural"], chords=2.0).preset_name())
        self.assertIsNone(HumanizeConfig(strength=0.5).preset_name())
        self.assertIsNone(HumanizeConfig(strength=LEVELS["light"], drums=True).preset_name())

    def test_overall_amount_zero_means_exactly_as_written_whatever_the_details(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.piano_song()
        cfg = HumanizeConfig(strength=0.0, timing=3, drift=3, chords=3, melody=3, velocity=3, dynamics=3, release=3, drums=True)
        self.assertEqual([(e.time, e.data) for e in s.playback_events(cfg, 4)], [(e.time, e.data) for e in s.playback_events()])

    def test_timing_and_drift_sliders(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.melody_song()
        none = HumanizeConfig(strength=1.0, timing=0, drift=0)
        self.assertTrue(all(abs(d) < 1e-6 for d in self.offsets(s, none)))                  # no timing nudges at all
        only_timing = [abs(d) for d in self.offsets(s, HumanizeConfig(strength=1.0, timing=1, drift=0))]
        only_drift = self.offsets(s, HumanizeConfig(strength=1.0, timing=0, drift=1))
        self.assertGreater(max(only_timing), 3.0)
        self.assertGreater(max(abs(d) for d in only_drift), 3.0)
        # drift is smooth: neighbouring notes move together (they are correlated); per-note timing is not
        def lag1(x):
            m = sum(x) / len(x)
            num = sum((x[i] - m) * (x[i + 1] - m) for i in range(len(x) - 1))
            return num / sum((v - m) ** 2 for v in x)
        jitter = self.offsets(s, HumanizeConfig(strength=1.0, timing=1, drift=0))
        self.assertGreater(lag1(only_drift), 0.5)
        self.assertLess(lag1(jitter), 0.3)
        big = max(abs(d) for d in self.offsets(s, HumanizeConfig(strength=1.0, timing=3, drift=0)))
        self.assertGreater(big, max(only_timing) * 1.5)                                     # the slider scales it

    def test_chord_and_melody_sliders(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.chord_song()

        def spread(cfg):
            on = self.onsets(s.playback_events(cfg, 8))
            out = []
            for i in range(0, len(on) - 3, 4):
                g = on[i:i + 4]
                out.append(max(e.time for e in g) - min(e.time for e in g))
            return sum(out) / len(out) * 1000
        flat = HumanizeConfig(strength=1.0, timing=0, drift=0, chords=0, melody=0)
        self.assertLess(spread(flat), 0.01)                                                 # struck together
        self.assertGreater(spread(HumanizeConfig(strength=1.0, timing=0, drift=0, chords=1, melody=0)), 3.0)    # rolled
        self.assertGreater(spread(HumanizeConfig(strength=1.0, timing=0, drift=0, chords=2.5, melody=0)),
                           spread(HumanizeConfig(strength=1.0, timing=0, drift=0, chords=1, melody=0)) * 1.4)
        # melody lead: the top note comes earlier and louder than with the slider at 0
        def top_minus_inner(cfg):
            on = self.onsets(s.playback_events(cfg, 8))
            lead, loud = [], []
            for i in range(0, len(on) - 3, 4):
                g = sorted(on[i:i + 4], key=lambda e: e.data[1])
                lead.append(sum(e.time for e in g[:3]) / 3 - g[3].time)
                loud.append(g[3].data[2] - sum(e.data[2] for e in g[1:3]) / 2)
            return sum(lead) / len(lead) * 1000, sum(loud) / len(loud)
        base = HumanizeConfig(strength=1.0, timing=0, drift=0, chords=0, velocity=0, dynamics=0)
        l0, v0 = top_minus_inner(replace_cfg(base, melody=0))
        l1, v1 = top_minus_inner(replace_cfg(base, melody=2))
        self.assertGreater(l1, l0 + 3.0)
        self.assertGreater(v1, v0 + 6.0)

    def test_volume_dynamics_and_length_sliders(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.melody_song(n=80)
        written = [e.data[2] for e in self.onsets(s.playback_events())]
        quiet = HumanizeConfig(strength=1.0, timing=0, drift=0, velocity=0, dynamics=0, melody=0, release=0)
        self.assertEqual([e.data[2] for e in self.onsets(s.playback_events(quiet, 5))], written)         # loudness untouched
        def sd(cfg):
            v = [e.data[2] for e in self.onsets(s.playback_events(cfg, 5))]
            m = sum(v) / len(v)
            return (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5
        per_note = sd(replace_cfg(quiet, velocity=1))
        phrase = sd(replace_cfg(quiet, dynamics=1))
        self.assertGreater(per_note, 3.0)
        self.assertGreater(phrase, 2.0)
        self.assertGreater(sd(replace_cfg(quiet, velocity=3)), per_note * 1.5)
        # the loudness drift is slow: neighbours are close, unlike per-note variation
        v = [e.data[2] for e in self.onsets(s.playback_events(replace_cfg(quiet, dynamics=1), 5))]
        self.assertLess(sum(abs(v[i + 1] - v[i]) for i in range(len(v) - 1)) / len(v), 3.0)
        # note lengths: release 0 keeps every length as written, 1 changes them
        def lengths(cfg):
            ev = s.playback_events(cfg, 5)
            on = {}
            out = []
            for e in ev:
                if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0:
                    on[e.data[1]] = e.time
                elif e.data[0] & 0xF0 == 0x80:
                    out.append(e.time - on[e.data[1]])
            return out
        plain = lengths(0.0)
        self.assertTrue(all(abs(a - b) < 1e-6 for a, b in zip(lengths(replace_cfg(quiet, release=0)), plain)))
        self.assertGreater(max(abs(a - b) for a, b in zip(lengths(replace_cfg(quiet, release=1)), plain)), 0.005)

    def test_drums_can_be_loosened_but_only_when_asked(self):
        from midiplayer.humanize import HumanizeConfig
        s = Song.new(480, 120)
        s.tracks[0].notes = [Note(i * 240, i * 240 + 100, 38 if i % 2 else 36, 100, 9) for i in range(40)]
        s.invalidate()
        base = [(e.time, e.data) for e in s.playback_events() if e.data[0] & 15 == 9]
        off = [(e.time, e.data) for e in s.playback_events(HumanizeConfig(strength=1.0), 2) if e.data[0] & 15 == 9]
        on = [(e.time, e.data) for e in s.playback_events(HumanizeConfig(strength=1.0, drums=True), 2) if e.data[0] & 15 == 9]
        self.assertEqual(off, base)
        self.assertNotEqual(on, base)
        times = [t for t, d in on if d[0] & 0xF0 == 0x90]
        self.assertEqual(times, sorted(times))
        self.assertLess(max(abs(a[0] - b[0]) for a, b in zip(on, base)), 0.06)

    def test_engine_takes_a_config(self):
        from midiplayer.humanize import HumanizeConfig
        rec = RecordingBackend()
        eng = Engine(rec)
        try:
            song = make_song(bpm=480)
            eng.load(song)
            plain = [e.time for e in eng.events]
            eng.humanize = HumanizeConfig(strength=1.0, timing=2.0, chords=0.0)
            eng.load(song)
            self.assertNotEqual([e.time for e in eng.events], plain)
            self.assertTrue(eng.humanize_seed)
            eng.humanize = HumanizeConfig(strength=0.0)
            eng.load(song)
            self.assertEqual([e.time for e in eng.events], plain)
        finally:
            eng.close()

    # ------------------------------------------------------------ chords one after another
    @staticmethod
    def sounding_at_next_chord(song, cfg, seed):
        """How many notes of an earlier chord are still sounding when a later chord begins, and the longest such overlap (s)."""
        ev = song.playback_events(cfg, seed)
        held = {}                                   # (channel, pitch) -> tick of its chord
        bad, worst, started = 0, 0.0, {}
        for e in ev:
            st = e.data[0] & 0xF0
            key = (e.data[0] & 15, e.data[1]) if len(e.data) > 1 else None
            if st == 0x90 and e.data[2] > 0:
                for (ch, p), (tick, t_on) in held.items():
                    if ch == (e.data[0] & 15) and tick < e.tick:
                        bad += 1
                        worst = max(worst, e.time - t_on)
                held[key] = (e.tick, e.time)
            elif st == 0x80:
                held.pop(key, None)
        return bad, worst

    def test_a_chord_is_let_go_before_the_next_one_is_played(self):
        from midiplayer.humanize import HumanizeConfig
        prog = [(60, 64, 67), (62, 65, 69), (59, 62, 67), (60, 64, 67, 72)]
        for strength in (0.4, 0.85, 1.7, 2.0):
            for seed in range(25):
                s = Song.new(480, 120)
                s.tracks[0].notes = [Note(i * 480, (i + 1) * 480, p, 90, 0) for i in range(24) for p in prog[i % 4]]   # each ends as the next starts
                s.invalidate()
                bad, _ = self.sounding_at_next_chord(s, HumanizeConfig(strength=strength), seed)
                self.assertEqual(bad, 0, f"strength {strength} seed {seed}: a chord was still sounding under the next one")

    def test_the_same_holds_for_single_notes_and_for_every_release_setting(self):
        from midiplayer.humanize import HumanizeConfig
        for release in (0.0, 1.0, 3.0):
            for seed in range(15):
                s = Song.new(480, 100)
                s.tracks[0].notes = [Note(i * 240, (i + 1) * 240, 60 + (i * 5) % 17, 85, 0) for i in range(40)]    # legato single notes
                s.invalidate()
                bad, _ = self.sounding_at_next_chord(s, HumanizeConfig(strength=1.7, release=release), seed)
                self.assertEqual(bad, 0)

    def test_written_overlaps_are_left_alone(self):
        """If the music itself holds a note into the next chord (legato, pedal-style writing) that stays."""
        from midiplayer.humanize import HumanizeConfig
        s = Song.new(480, 120)
        s.tracks[0].notes = [Note(i * 480, i * 480 + 700, 60 + (i % 5), 90, 0) for i in range(30)]       # each note runs 220 ticks into the next
        s.invalidate()
        bad, worst = self.sounding_at_next_chord(s, HumanizeConfig(strength=1.0), 3)
        self.assertGreater(bad, 20)                                                                    # still overlapping, as written
        ev = s.playback_events(HumanizeConfig(strength=1.0), 3)
        held = {}
        for e in ev:
            if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0:
                held[e.data[1]] = e.time
            elif e.data[0] & 0xF0 == 0x80:
                self.assertGreater(e.time - held.pop(e.data[1]), 0.2)                                  # and not cut short

    def test_each_hand_is_its_own_part(self):
        """Two tracks on one channel (left and right hand): a right-hand chord is not cut off by the left hand's next note."""
        from midiplayer.humanize import HumanizeConfig
        s = Song.new(480, 120)
        right = s.tracks[0]
        right.notes = [Note(0, 1920, 72, 90, 0), Note(0, 1920, 76, 90, 0)]                             # a long right-hand chord
        left = s.add_track("Left")
        s.tracks[left].notes = [Note(i * 480, (i + 1) * 480, 48 + i, 80, 0) for i in range(4)]         # left hand moves every beat
        s.invalidate()
        for seed in range(20):
            ev = s.playback_events(HumanizeConfig(strength=1.7), seed)
            starts = {e.data[1]: e.time for e in ev if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0}
            ends = {e.data[1]: e.time for e in ev if e.data[0] & 0xF0 == 0x80}
            self.assertGreater(ends[72] - starts[72], 1.6)          # held (nearly) the full 2 s, not cut at the first left-hand note
            self.assertGreater(ends[76] - starts[76], 1.6)

    def test_clamping_never_makes_a_note_vanish_or_start_late(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.piano_song()
        base = {}
        for strength in (0.4, 1.0, 1.7):
            ev = s.playback_events(HumanizeConfig(strength=strength), 9)
            held = {}
            for e in ev:
                if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0:
                    held[(e.data[0] & 15, e.data[1])] = e.time
                elif e.data[0] & 0xF0 == 0x80:
                    self.assertGreater(e.time - held.pop((e.data[0] & 15, e.data[1])), 0.004)
            self.assertEqual(held, {})

    # ------------------------------------------------------------ skill levels
    def test_skill_presets_exist_and_are_recognised(self):
        from midiplayer.humanize import HumanizeConfig, SKILL_NAMES, SKILLS, SKILL_LABELS, SKILL_TEXT
        self.assertEqual(SKILL_NAMES, ("beginner", "intermediate", "advanced", "pianist"))
        for name in SKILL_NAMES:
            cfg = HumanizeConfig.preset(name)
            self.assertEqual(cfg.preset_name(), name)
            self.assertGreater(cfg.strength, 0.0)
            self.assertTrue(SKILL_LABELS[name] and SKILL_TEXT[name])
            self.assertEqual(HumanizeConfig.from_json(cfg.to_json()), cfg)
            custom = HumanizeConfig.from_json(cfg.to_json())
            custom.chords += 0.3
            self.assertIsNone(custom.preset_name())
        self.assertEqual(HumanizeConfig.preset("natural").preset_name(), "natural")

    def test_old_saved_settings_have_no_stumbles(self):
        from midiplayer.humanize import HumanizeConfig
        cfg = HumanizeConfig.from_json('{"strength": 0.85, "timing": 1.0}')
        self.assertEqual(cfg.slips, 0.0)
        self.assertEqual(HumanizeConfig.from_json('{"slips": 99}').slips, 3.0)

    def test_skill_levels_get_steadily_more_precise(self):
        from midiplayer.humanize import HumanizeConfig, SKILL_NAMES
        s = self.piano_song(bars=20)
        base = [e for e in s.playback_events() if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0]
        by_tick = {}
        for r in base:
            by_tick.setdefault(r.tick, []).append(r)                    # (the skill levels can change a note's pitch: match by position)

        def timing_sd(cfg):
            sds = []
            for seed in range(6):
                new = [e for e in s.playback_events(cfg, seed) if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0]
                d = [e.time - min(by_tick[e.tick], key=lambda r: abs(r.time - e.time)).time for e in new]
                m = sum(d) / len(d)
                sds.append((sum((x - m) ** 2 for x in d) / len(d)) ** 0.5)
            return sum(sds) / len(sds)
        sd = [timing_sd(HumanizeConfig.preset(n)) for n in SKILL_NAMES]
        self.assertTrue(sd[0] > sd[1] > sd[2] > sd[3], f"timing spread should fall with skill: {sd}")
        self.assertGreater(sd[0], 2.5 * sd[3])

    def test_skill_levels_differ_in_character_not_just_amount(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.chord_song(n=30)

        def chord_stats(name):
            spreads, lead_loud = [], []
            for seed in range(6):
                on = self.onsets(s.playback_events(HumanizeConfig.preset(name), seed))
                for i in range(0, len(on) - 3, 4):
                    g = sorted(on[i:i + 4], key=lambda e: e.data[1])
                    spreads.append(max(e.time for e in g) - min(e.time for e in g))
                    lead_loud.append(g[3].data[2] - sum(e.data[2] for e in g[1:3]) / 2)       # top note vs the inner ones
            return sum(spreads) / len(spreads) * 1000, sum(lead_loud) / len(lead_loud)
        beg_spread, beg_voice = chord_stats("beginner")
        pro_spread, pro_voice = chord_stats("pianist")
        self.assertGreater(beg_spread, 2.0 * pro_spread)          # a beginner's chords land raggedly, a pianist's almost together
        self.assertGreater(pro_voice, beg_voice + 6.0)             # a pianist brings the melody out; a beginner does not

    def test_stumbles(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.melody_song(n=120)
        written = self.onsets(s.playback_events())

        def far(cfg, seed):
            new = self.onsets(s.playback_events(cfg, seed))
            return sum(1 for a, b in zip(written, new) if abs(b.time - a.time) > 0.05)
        calm = sum(far(HumanizeConfig(strength=1.0, timing=0.5, drift=0.5), seed) for seed in range(8))
        slippy = sum(far(HumanizeConfig(strength=1.0, timing=0.5, drift=0.5, slips=2.0), seed) for seed in range(8))
        self.assertEqual(calm, 0)
        self.assertGreater(slippy, 15)                                      # now and then a note is a long way out
        # mostly late rather than early
        new = self.onsets(s.playback_events(HumanizeConfig(strength=1.0, timing=0, drift=0, chords=0, slips=3.0), 4))
        d = [b.time - a.time for a, b in zip(written, new) if abs(b.time - a.time) > 0.01]
        self.assertGreater(len(d), 5)
        self.assertGreater(sum(1 for x in d if x > 0), 2 * sum(1 for x in d if x < 0))

    def test_every_skill_level_keeps_the_safety_rules(self):
        from midiplayer.humanize import HumanizeConfig, SKILL_NAMES
        prog = [(60, 64, 67), (62, 65, 69), (59, 62, 67), (60, 64, 67, 72)]
        for name in SKILL_NAMES:
            cfg = HumanizeConfig.preset(name)
            for seed in range(12):
                # chords one after another: never overlapping
                s = Song.new(480, 120)
                s.tracks[0].notes = [Note(i * 480, (i + 1) * 480, p, 90, 0) for i in range(16) for p in prog[i % 4]]
                s.invalidate()
                bad, _ = self.sounding_at_next_chord(s, cfg, seed)
                self.assertEqual(bad, 0, f"{name}: chords overlapped")
                # a fast passage keeps its order, every note-on has its note-off
                f = Song.new(480, 240)
                f.tracks[0].notes = [Note(i * 120, i * 120 + 100, 60 + (i % 12), 90, 0) for i in range(48)]
                f.invalidate()
                ev = f.playback_events(cfg, seed)
                ticks = [e.tick for e in ev if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0]
                self.assertEqual(ticks, sorted(ticks), f"{name}: notes swapped places")        # (a wrong note keeps its place in time)
                self.assertEqual([e.time for e in ev], sorted(e.time for e in ev))
            # ghost notes and drums untouched
            g = Song.new(480, 120)
            g.tracks[0].notes = [Note(i * 480, i * 480 + 200, 60, 12, 0) for i in range(16)] + [Note(i * 480, i * 480 + 100, 38, 100, 9) for i in range(16)]
            g.invalidate()
            drums_now = [(e.time, e.data) for e in g.playback_events(cfg, 3) if e.data[0] & 15 == 9]
            drums_was = [(e.time, e.data) for e in g.playback_events() if e.data[0] & 15 == 9]
            if cfg.know == 0.0:
                self.assertEqual(drums_now, drums_was)                                        # exactly on the grid
            else:                                                                             # (they follow the player's own tempo, with the rest)
                self.assertEqual([d for _, d in drums_now], [d for _, d in drums_was])        # the same hits, as loud
                self.assertTrue(all(abs(a - b) < 1.6 for (a, _), (b, _) in zip(drums_now, drums_was)))
            self.assertEqual([e.data[2] for e in self.onsets(g.playback_events(cfg, 3))], [12] * 16)

    # ------------------------------------------------------------ mistakes: fat fingers, wrong notes, missed notes
    @staticmethod
    def long_melody(n=400):
        s = Song.new(480, 120)
        s.tracks[0].notes = [Note(i * 360, i * 360 + 300, 48 + (i * 7) % 30, 80, 0) for i in range(n)]
        s.invalidate()
        return s

    def assert_clean_performance(self, ev, msg=""):
        """Every key goes down and up in order, and no key is struck while it is already down."""
        self.assertEqual([e.time for e in ev], sorted(e.time for e in ev), msg)
        held = {}
        for e in ev:
            st = e.data[0] & 0xF0
            key = (e.data[0] & 15, e.data[1])
            if st == 0x90 and e.data[2] > 0:
                self.assertNotIn(key, held, f"{msg}: key {key} struck while still down")
                held[key] = e.time
            elif st == 0x80:
                self.assertIn(key, held, f"{msg}: key {key} released without being struck")
                held.pop(key)
        self.assertEqual(held, {}, f"{msg}: keys left down")

    def test_mistakes_are_off_unless_asked_for(self):
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        s = self.long_melody(100)
        h = humanize_notes(s, s.tempo_map, HumanizeConfig(strength=1.0), 3)
        self.assertEqual((h.dropped, h.repitch, h.extras), (set(), {}, []))
        for name in ("light", "natural", "loose", "pianist"):
            h = humanize_notes(s, s.tempo_map, HumanizeConfig.preset(name), 3)
            self.assertEqual((h.dropped, h.repitch, h.extras), (set(), {}, []), name)

    def test_missed_notes(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.long_melody(600)
        written = self.onsets(s.playback_events())
        ev = s.playback_events(HumanizeConfig(strength=1.0, missed=3.0), 5)
        on = self.onsets(ev)
        gone = len(written) - len(on)
        self.assertGreater(gone, 0.03 * len(written))                       # about 6% of the notes
        self.assertLess(gone, 0.12 * len(written))
        offs = [e for e in ev if e.data[0] & 0xF0 == 0x80]
        self.assertEqual(len(offs), len(on))                                # a missed note has no release either
        self.assert_clean_performance(ev, "missed")
        pitches = [e.data[1] for e in written]
        for e in on:
            self.assertIn(e.data[1], pitches)

    def test_wrong_notes(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.long_melody(600)
        written = {e.tick: e.data[1] for e in self.onsets(s.playback_events())}
        ev = s.playback_events(HumanizeConfig(strength=1.0, wrong=3.0), 5)
        on = self.onsets(ev)
        self.assertEqual(len(on), len(written))                              # nothing is added or lost
        wrong = [(written[e.tick], e.data[1]) for e in on if e.data[1] != written[e.tick]]
        self.assertGreater(len(wrong), 0.03 * len(written))
        self.assertLess(len(wrong), 0.12 * len(written))
        self.assertTrue(all(1 <= abs(a - b) <= 4 for a, b in wrong), "a wrong note is a near neighbour")
        near = sum(1 for a, b in wrong if abs(a - b) <= 2)
        self.assertGreater(near, len(wrong) * 0.6)                           # mostly the very next key or the one after
        self.assert_clean_performance(ev, "wrong")

    def test_fat_fingers(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.long_melody(600)
        base = self.onsets(s.playback_events())
        ev = s.playback_events(HumanizeConfig(strength=1.0, fat=3.0), 5)
        on = self.onsets(ev)
        extra = len(on) - len(base)
        self.assertGreater(extra, 0.05 * len(base))                          # about 9% of the notes also touch a neighbour
        self.assertLess(extra, 0.16 * len(base))
        self.assert_clean_performance(ev, "fat")
        # every extra key sits right next to a key played at the same moment, softer and for less time
        by_tick = {}
        for e in on:
            by_tick.setdefault(e.tick, []).append(e)
        length = {}
        held = {}
        for e in ev:
            if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0:
                held[e.data[1]] = e.time
            elif e.data[0] & 0xF0 == 0x80:
                t0 = held.pop(e.data[1])
                length[(e.data[1], round(t0, 6))] = e.time - t0
        pairs = 0
        for tick, group in by_tick.items():
            if len(group) == 2:
                pairs += 1
                a, b = sorted(group, key=lambda e: e.data[2], reverse=True)         # the louder one is the real note
                self.assertTrue(1 <= abs(a.data[1] - b.data[1]) <= 2, "the next key along")
                self.assertLess(b.data[2], a.data[2])
                self.assertLessEqual(abs(a.time - b.time), 0.03)
                self.assertLessEqual(length[(b.data[1], round(b.time, 6))], length[(a.data[1], round(a.time, 6))] + 1e-9)
            else:
                self.assertEqual(len(group), 1)
        self.assertEqual(pairs, extra)

    def test_mistakes_leave_drums_and_ghost_notes_alone(self):
        from midiplayer.humanize import HumanizeConfig
        s = Song.new(480, 120)
        s.tracks[0].notes = [Note(i * 480, i * 480 + 200, 60, 12, 0) for i in range(60)] + [Note(i * 480, i * 480 + 100, 38, 100, 9) for i in range(60)]
        s.invalidate()
        cfg = HumanizeConfig(strength=1.0, fat=3.0, wrong=3.0, missed=3.0)
        ev = s.playback_events(cfg, 4)
        self.assertEqual([(e.time, e.data) for e in ev if e.data[0] & 15 == 9], [(e.time, e.data) for e in s.playback_events() if e.data[0] & 15 == 9])
        self.assertEqual(len(self.onsets(ev)), 60)
        self.assertEqual({e.data[1] for e in self.onsets(ev)}, {60})

    def test_adding_mistakes_does_not_change_the_nudging(self):
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        s = self.long_melody(200)
        plain = humanize_notes(s, s.tempo_map, HumanizeConfig(strength=1.0), 7)
        mistaken = humanize_notes(s, s.tempo_map, HumanizeConfig(strength=1.0, fat=2.0, wrong=2.0, missed=2.0), 7)
        self.assertEqual(dict(plain), dict(mistaken))                        # the same timing and loudness for every note
        self.assertTrue(mistaken.dropped or mistaken.repitch or mistaken.extras)

    def test_the_same_seed_makes_the_same_mistakes(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.long_melody(300)
        cfg = HumanizeConfig.preset("beginner")
        a = [(e.time, e.data) for e in s.playback_events(cfg, 9)]
        self.assertEqual(a, [(e.time, e.data) for e in s.playback_events(cfg, 9)])
        self.assertNotEqual(a, [(e.time, e.data) for e in s.playback_events(cfg, 10)])

    def test_skill_levels_make_fewer_mistakes_as_they_improve(self):
        from midiplayer.humanize import HumanizeConfig, SKILL_NAMES, humanize_notes
        s = self.long_melody(500)
        counts = {}
        for name in SKILL_NAMES:
            total = [0, 0, 0]
            for seed in range(6):
                h = humanize_notes(s, s.tempo_map, HumanizeConfig.preset(name), seed)
                total[0] += len(h.extras)
                total[1] += len(h.repitch)
                total[2] += len(h.dropped)
            counts[name] = total
        for k in range(3):                                                   # fat fingers, wrong notes, missed notes
            vals = [counts[n][k] for n in SKILL_NAMES]
            self.assertTrue(vals[0] > vals[1] > vals[2] >= vals[3], f"{('fat', 'wrong', 'missed')[k]}: {vals}")
            self.assertEqual(vals[3], 0)                                     # a concert pianist makes none
        self.assertGreater(counts["beginner"][0], 40)                        # a beginner: roughly 1 note in 20 grazes a neighbour

    def test_every_skill_level_always_gives_a_clean_performance(self):
        from midiplayer.humanize import HumanizeConfig, SKILL_NAMES
        chords = self.chord_song(30)
        mel = self.long_melody(150)
        for name in SKILL_NAMES:
            for seed in range(10):
                for song in (chords, mel, self.piano_song(bars=8)):
                    self.assert_clean_performance(song.playback_events(HumanizeConfig.preset(name), seed), f"{name} seed {seed}")

    def test_mistakes_never_put_a_key_down_that_is_already_down(self):
        """Dense chords and repeated notes give a mistake the most chances to land on a sounding key."""
        from midiplayer.humanize import HumanizeConfig
        s = Song.new(480, 120)
        s.tracks[0].notes = [Note(i * 240, i * 240 + 220, p, 90, 0) for i in range(60) for p in (60, 61, 62, 64, 65, 67)]   # dense clusters of neighbouring keys
        s.invalidate()
        for seed in range(15):
            self.assert_clean_performance(s.playback_events(HumanizeConfig(strength=1.5, fat=3.0, wrong=3.0, missed=1.0), seed), f"seed {seed}")

    # ------------------------------------------------------------ fat fingers settle down on repeated figures
    @staticmethod
    def song_of_chords(chords, gap=480):
        s = Song.new(480, 120)
        s.tracks[0].notes = [Note(i * gap, i * gap + 400, p, 90, 0) for i, ch in enumerate(chords) for p in ch]
        s.invalidate()
        return s

    A, B, C, D = (60, 64, 67), (62, 65, 69), (59, 62, 67), (57, 60, 64)

    def test_repeat_depth_counts_how_often_the_same_chord_has_just_been_played(self):
        from midiplayer.humanize import repeat_depths
        s = self.song_of_chords([self.A] * 6 + [self.B] + [self.A] * 2)
        d = repeat_depths(s, s.tempo_map)
        first = lambda c: d[(0, c * 3)]
        self.assertEqual([first(c) for c in range(6)], [0, 1, 2, 3, 4, 5])        # the same chord again and again
        self.assertEqual(first(6), 0)                                              # a different chord: starts over
        self.assertEqual(first(7), 1)                                              # ...going back to the first after one different chord is a familiar A B A figure
        self.assertGreaterEqual(first(8), 1)                                       # A again straight after A, and in step with the earlier run
        self.assertEqual({d[(0, c * 3 + k)] for c in range(6) for k in range(3)}, {0, 1, 2, 3, 4, 5})
        # every note of a chord has its chord's depth
        self.assertEqual([d[(0, 3 * 4 + k)] for k in range(3)], [4, 4, 4])
        # figures that repeat every two or three chords count as well
        p = self.song_of_chords([self.A, self.B] * 5)
        dp = repeat_depths(p, p.tempo_map)
        self.assertEqual([dp[(0, c * 3)] for c in range(10)], [0, 0, 1, 2, 3, 4, 5, 6, 7, 8])
        q = self.song_of_chords([self.A, self.B, self.C] * 4)
        dq = repeat_depths(q, q.tempo_map)
        self.assertEqual([dq[(0, c * 3)] for c in range(12)], [0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        v = self.song_of_chords([[self.A, self.B, self.C, self.D][i % 4] for i in range(20)])
        self.assertEqual(set(repeat_depths(v, v.tempo_map).values()), {0})          # a new chord every time (figures longer than 3 chords do not count)
        # (and a drum part never counts)
        dr = Song.new(480, 120)
        dr.tracks[0].notes = [Note(i * 240, i * 240 + 100, 38, 100, 9) for i in range(10)]
        dr.invalidate()
        self.assertEqual(repeat_depths(dr, dr.tempo_map), {})

    def test_fat_fingers_are_much_rarer_when_the_same_chord_is_played_over_and_over(self):
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        cfg = HumanizeConfig(strength=1.0, fat=3.0)
        varied = self.song_of_chords([[self.A, self.B, self.C, self.D][i % 4] for i in range(40)])
        same = self.song_of_chords([self.A] * 40)
        figure = self.song_of_chords([[self.A, self.B][i % 2] for i in range(40)])

        def rate(song, seeds=120):
            total = sum(len(humanize_notes(song, song.tempo_map, cfg, seed).extras) for seed in range(seeds))
            return total / (len(song.tracks[0].notes) * seeds)
        r_var, r_same, r_fig = rate(varied), rate(same), rate(figure)
        self.assertGreater(r_var, 0.05)                                            # a normal rate when nothing repeats
        self.assertLess(r_same, r_var * 0.35)                                      # far fewer when it is the same chord again and again
        self.assertLess(r_fig, r_var * 0.35)                                       # ...and for a repeating two-chord figure
        # the first time round is as likely as ever; it then falls away down the run
        by_pos = [0] * 40
        for seed in range(250):
            for ti, ch, p2, a, b, v, tick in humanize_notes(same, same.tempo_map, cfg, seed).extras:
                by_pos[tick // 480] += 1
        self.assertGreater(by_pos[0], 2 * sum(by_pos[10:20]) / 10 * 1.2)
        self.assertGreater(sum(by_pos[:3]), 2.0 * sum(by_pos[20:23]))
        self.assertGreater(sum(by_pos), 0)                                         # still not zero: it does happen now and then

    def test_a_new_chord_after_a_long_run_is_treated_as_new(self):
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        cfg = HumanizeConfig(strength=1.0, fat=3.0)
        s = self.song_of_chords([self.A] * 30 + [self.B] * 1 + [self.C] * 1 + [self.D] * 1)
        hits_new, hits_old = 0, 0
        for seed in range(400):
            for ti, ch, p2, a, b, v, tick in humanize_notes(s, s.tempo_map, cfg, seed).extras:
                c = tick // 480
                hits_new += c >= 30
                hits_old += 20 <= c < 30
        # the 3 fresh chords together are about as likely as the 10 settled ones before them (3 vs 10 chords)
        self.assertGreater(hits_new / 3.0, 2.0 * hits_old / 10.0)

    def test_wrong_and_missed_notes_do_not_depend_on_repetition(self):
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        cfg = HumanizeConfig(strength=1.0, wrong=3.0, missed=3.0)
        varied = self.song_of_chords([[self.A, self.B, self.C, self.D][i % 4] for i in range(40)])
        same = self.song_of_chords([self.A] * 40)

        def rates(song, seeds=80):
            w = m = 0
            for seed in range(seeds):
                h = humanize_notes(song, song.tempo_map, cfg, seed)
                w += len(h.repitch)
                m += len(h.dropped)
            n = len(song.tracks[0].notes) * seeds
            return w / n, m / n
        (w1, m1), (w2, m2) = rates(varied), rates(same)
        self.assertAlmostEqual(w1, w2, delta=0.02)
        self.assertAlmostEqual(m1, m2, delta=0.02)

    def test_the_first_time_round_is_unchanged_by_the_repetition_rule(self):
        """With nothing repeated the fat-finger decisions are exactly what they were: the rule only thins out repeats."""
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        cfg = HumanizeConfig(strength=1.0, fat=3.0)
        v = self.song_of_chords([[self.A, self.B, self.C, self.D][i % 4] for i in range(16)])
        h = humanize_notes(v, v.tempo_map, cfg, 2)
        self.assertGreater(len(h.extras), 3)

    # ------------------------------------------------------------ striking the same key again
    @staticmethod
    def same_key_song(spacing_ticks, n=40, pitch=60, bpm=120):
        """The same key struck n times, each note ending exactly where the next begins (as a MIDI file usually has them)."""
        s = Song.new(480, bpm)
        s.tracks[0].notes = [Note(i * spacing_ticks, (i + 1) * spacing_ticks, pitch, 90, 0) for i in range(n)]
        s.invalidate()
        return s

    @staticmethod
    def restrikes(ev, pitch=60):
        """[(seconds the key was up before it was struck again, velocity of that strike)] for one key."""
        out, last_off = [], None
        for e in ev:
            if (e.data[0] & 0x0F) != 0 or len(e.data) < 3 and e.data[0] & 0xF0 != 0x80 or e.data[1] != pitch:
                continue
            if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0:
                if last_off is not None:
                    out.append((e.time - last_off, e.data[2]))
            elif e.data[0] & 0xF0 == 0x80:
                last_off = e.time
        return out

    def test_a_repeated_key_comes_back_up_before_it_is_struck_again(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.same_key_song(480)
        written = [g for g, _ in self.restrikes(s.playback_events())]
        self.assertTrue(all(g == 0 for g in written))                              # as written: struck again in the same instant
        for strength, lo, hi in ((0.4, 0.010, 0.080), (0.85, 0.020, 0.120), (1.7, 0.040, 0.160)):
            gaps = []
            for seed in range(12):
                gaps += [g for g, _ in self.restrikes(s.playback_events(HumanizeConfig(strength=strength), seed))]
            self.assertGreater(min(gaps), lo, f"strength {strength}")                # never instant
            self.assertLess(max(gaps), hi, f"strength {strength}")                   # but a note is not chopped short (this includes the release jitter)
            self.assertGreater(max(gaps) - min(gaps), 0.003)                         # and not exactly the same every time

    def test_the_repeat_slider(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.same_key_song(480)

        def mean_gap(repeat):
            g = []
            for seed in range(10):
                g += [x for x, _ in self.restrikes(s.playback_events(HumanizeConfig(strength=1.0, repeat=repeat), seed))]
            return sum(g) / len(g)
        off, normal, big = mean_gap(0.0), mean_gap(1.0), mean_gap(2.0)
        self.assertLess(off, 0.015)                                                # 0 = the old behaviour: only the release jitter left
        self.assertGreater(normal, 0.035)
        self.assertGreater(normal, off * 3)
        self.assertGreater(big, normal * 1.3)

    def test_fast_repeats_come_out_later_and_lighter(self):
        from midiplayer.humanize import HumanizeConfig
        quiet = dict(strength=1.0, timing=0, drift=0, velocity=0, dynamics=0, melody=0, release=0)    # (only the repeat rule at work)

        def stats(spacing):
            s = self.same_key_song(spacing)
            base = {e.tick: e.time for e in s.playback_events() if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0}
            vel, lag = [], []
            for seed in range(10):
                ev = s.playback_events(HumanizeConfig(**quiet), seed)
                on = [e for e in ev if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0][1:]
                vel += [e.data[2] for e in on]
                lag += [e.time - base[e.tick] for e in on]
            return sum(vel) / len(vel), sum(lag) / len(lag)
        slow_v, slow_lag = stats(480)                                              # 500 ms apart
        mid_v, mid_lag = stats(120)                                                # 125 ms apart (16ths)
        fast_v, fast_lag = stats(60)                                               # 62 ms apart
        self.assertAlmostEqual(slow_v, 90.0, delta=0.5)                            # a slow repeat is just as loud
        self.assertAlmostEqual(slow_lag, 0.0, delta=0.0005)
        self.assertLess(mid_v, slow_v - 3.0)
        self.assertLess(fast_v, mid_v - 2.0)                                       # the faster, the lighter
        self.assertGreater(mid_lag, 0.001)
        self.assertGreater(fast_lag, mid_lag + 0.001)                              # ...and the later

    def test_very_fast_repeats_still_sound_and_nothing_is_lost(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.same_key_song(60)                                                 # 62 ms between strikes
        for seed in range(10):
            ev = s.playback_events(HumanizeConfig(strength=2.0, repeat=3.0), seed)
            on = [e for e in ev if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0]
            self.assertEqual(len(on), 40)                                          # nothing is lost
            held = {}
            for e in ev:
                if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0:
                    held[e.data[1]] = e.time
                elif e.data[0] & 0xF0 == 0x80:
                    self.assertGreaterEqual(e.time - held.pop(e.data[1]), 0.0099)   # each strike still sounds, if briefly
            self.assert_clean_performance(ev, "very fast repeats")

    def test_repeated_chords_let_every_key_come_up(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.song_of_chords([self.A] * 30, gap=480)                            # the same chord, each ending as the next starts
        s.tracks[0].notes = [Note(n.start, n.start + 480, n.pitch, n.velocity, 0) for n in s.tracks[0].notes]
        s.invalidate()
        for seed in range(6):
            ev = s.playback_events(HumanizeConfig(strength=0.85), seed)
            for pitch in self.A:
                self.assertGreater(min(g for g, _ in self.restrikes(ev, pitch)), 0.015, f"key {pitch}")

    def test_written_overlaps_of_the_same_key_are_left_alone(self):
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        s = Song.new(480, 120)
        s.tracks[0].notes = [Note(i * 480, i * 480 + 700, 60, 90, 0) for i in range(10)]       # the file itself overlaps them
        s.invalidate()
        h = humanize_notes(s, s.tempo_map, HumanizeConfig(strength=1.0), 3)
        self.assertEqual(h.fast_repeats, {})

    def test_fast_repeats_are_the_ones_most_likely_to_be_missed(self):
        from midiplayer.humanize import HumanizeConfig
        cfg = HumanizeConfig(strength=1.0, missed=2.0)

        def missed_share(spacing):
            s = self.same_key_song(spacing, n=60)
            gone = 0
            for seed in range(40):
                gone += 60 - len([e for e in s.playback_events(cfg, seed) if e.data[0] & 0xF0 == 0x90 and e.data[2] > 0])
            return gone / (60 * 40)
        slow, fast = missed_share(480), missed_share(60)
        self.assertGreater(fast, 2.0 * slow)                                       # a very fast re-strike is much harder to get right
        self.assertGreater(slow, 0.0)

    def test_skill_levels_struggle_more_with_repeated_keys(self):
        from midiplayer.humanize import HumanizeConfig
        s = self.same_key_song(240)

        def mean_gap(name):
            g = []
            for seed in range(12):
                g += [x for x, _ in self.restrikes(s.playback_events(HumanizeConfig.preset(name), seed))]
            return sum(g) / len(g)
        gaps = [mean_gap(n) for n in ("beginner", "intermediate", "advanced", "pianist")]
        self.assertTrue(gaps[0] > gaps[1] > gaps[2] > gaps[3], str(gaps))

    # ------------------------------------------------------------ familiar and unfamiliar passages
    @staticmethod
    def passages_song():
        """A tune (easy, comes round three times) and a busy, wide-leaping passage, in 8-bar blocks: A B A B A."""
        import random as _r
        rng = _r.Random(3)
        s = Song.new(480, 120)
        notes = []
        bar = 1920

        def tune(b0):
            for b in range(8):
                for beat in range(4):
                    t = (b0 + b) * bar + beat * 480
                    notes.append(Note(t, t + 440, 64 + [0, 2, 4, 2][beat] + (b % 2), 80, 0))
                notes.append(Note((b0 + b) * bar, (b0 + b) * bar + 1800, 40 + (b % 3), 70, 0))

        def busy(b0):
            for b in range(8):
                for k in range(16):
                    t = (b0 + b) * bar + k * 120
                    notes.append(Note(t, t + 110, 50 + rng.randint(0, 30), 85, 0))
                    if k % 4 == 0:
                        for p in (48, 55, 60):
                            notes.append(Note(t, t + 400, p + rng.randint(-1, 1), 75, 0))
        tune(0); busy(8); tune(16); busy(24); tune(32)
        s.tracks[0].notes = sorted(notes, key=lambda n: n.start)
        s.invalidate()
        return s

    def test_know_is_off_unless_asked_for(self):
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        s = self.passages_song()
        for name in ("light", "natural", "loose", "pianist"):
            h = humanize_notes(s, s.tempo_map, HumanizeConfig.preset(name), 3)
            self.assertIsNone(h.warp, name)
            self.assertIsNone(h.fluency, name)
        self.assertGreater(HumanizeConfig.preset("beginner").know, HumanizeConfig.preset("intermediate").know)
        self.assertGreater(HumanizeConfig.preset("intermediate").know, HumanizeConfig.preset("advanced").know)
        self.assertEqual(HumanizeConfig.preset("pianist").know, 0.0)
        self.assertEqual(HumanizeConfig.from_json('{"strength": 1}').know, 0.0)
        self.assertEqual(HumanizeConfig.from_json('{"know": 50}').know, 3.0)

    def test_known_passages_are_played_faster_and_new_ones_slower(self):
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        s = self.passages_song()
        tm = s.tempo_map
        cfg = HumanizeConfig(strength=1.0, know=1.5)
        corrs = []
        for seed in range(12):
            h = humanize_notes(s, tm, cfg, seed)
            xs, ys = [], []
            for k in range(0, 18):                                          # 4-second windows
                t0, t1 = k * 4.0, (k + 1) * 4.0
                xs.append(h.fluency.at(t0 + 2.0))
                ys.append(4.0 / (4.0 + h.warp.end(t1) - h.warp.start(t0)))     # >1 = faster than written
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
            corrs.append(num / den if den else 0.0)
        self.assertGreater(sum(corrs) / len(corrs), 0.6, "speed should follow how well each passage is known")
        # and the extremes are really faster / slower than written
        h = humanize_notes(s, tm, HumanizeConfig(strength=1.0, know=2.0), 4)
        speeds = []
        for k in range(0, 18):
            t0, t1 = k * 4.0, (k + 1) * 4.0
            speeds.append(4.0 / (4.0 + h.warp.end(t1) - h.warp.start(t0)))
        self.assertGreater(max(speeds), 1.05)
        self.assertLess(min(speeds), 0.92)

    def test_mistakes_are_rarer_in_known_passages(self):
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        s = self.passages_song()
        tm = s.tempo_map
        cfg = HumanizeConfig(strength=1.0, know=1.5, fat=2.0, wrong=2.0, missed=2.0)
        unknown = [0, 0]
        known = [0, 0]
        for seed in range(40):
            h = humanize_notes(s, tm, cfg, seed)
            bad = set(h.dropped) | set(h.repitch)
            for ti, tr in enumerate(s.tracks):
                for i, n in enumerate(tr.notes):
                    f = h.fluency.at(tm.tick_to_sec(n.start))
                    bucket = unknown if f < 0.35 else known if f > 0.65 else None
                    if bucket is not None:
                        bucket[0] += 1
                        bucket[1] += (ti, i) in bad
            for ex in h.extras:
                f = h.fluency.at(tm.tick_to_sec(ex[6]))
                if f < 0.35:
                    unknown[1] += 1
                elif f > 0.65:
                    known[1] += 1
        self.assertGreater(unknown[0], 500)
        self.assertGreater(known[0], 500)
        rate_unknown, rate_known = unknown[1] / unknown[0], known[1] / known[0]
        self.assertGreater(rate_unknown, 2.0 * rate_known, f"unknown {rate_unknown:.3f} vs known {rate_known:.3f}")

    def test_a_part_that_comes_round_again_is_better_known_and_hard_parts_less(self):
        import random as _r
        from midiplayer.humanize import _fluency_map
        s = self.passages_song()
        tm = s.tempo_map
        simple, busy_, first, later = [], [], [], []
        for seed in range(60):
            fl = _fluency_map(s, tm, _r.Random(seed))
            vals = fl.values                                                  # 4 s segments: 8 bars tune, 8 busy, 8 tune, 8 busy, 8 tune
            simple += vals[0:4] + vals[8:12] + vals[16:20]
            busy_ += vals[4:8] + vals[12:16]
            first.append(sum(vals[0:2]) / 2)
            later.append(sum(vals[16:18]) / 2)
        self.assertGreater(sum(simple) / len(simple), sum(busy_) / len(busy_) + 0.15)     # the easy tune is known better than the hard passage
        self.assertGreater(sum(later) / len(later), sum(first) / len(first) + 0.1)        # ...and better the third time round

    def test_everything_moves_with_the_players_tempo(self):
        """Pedal, drums and notes all follow the same slowing down and speeding up."""
        from midiplayer import midifile
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        s = self.passages_song()
        s.tracks[0].events.append(midifile.RawEvent(7680, midifile.MIDI, bytes([0xB0, 64, 127])))
        s.tracks[0].events.append(midifile.RawEvent(15360, midifile.MIDI, bytes([0xB0, 64, 0])))
        s.tracks[0].notes += [Note(i * 480, i * 480 + 100, 38, 100, 9) for i in range(0, 200)]
        s.invalidate()
        cfg = HumanizeConfig(strength=1.0, know=2.0)
        h = humanize_notes(s, s.tempo_map, cfg, 6)
        ev = s.playback_events(cfg, 6)
        self.assertEqual([e.time for e in ev], sorted(e.time for e in ev))
        pedal = [e for e in ev if e.data[0] == 0xB0 and e.data[1] == 64]
        self.assertEqual(len(pedal), 2)
        for e in pedal:
            written = s.tempo_map.tick_to_sec(e.tick)
            self.assertAlmostEqual(e.time, written + h.warp.start(written), places=9)
        drums = [e for e in ev if e.data[0] & 15 == 9 and e.data[0] & 0xF0 == 0x90]
        self.assertGreater(len(drums), 100)
        for e in drums[:60]:
            written = s.tempo_map.tick_to_sec(e.tick)
            self.assertAlmostEqual(e.time, written + h.warp.start(written), places=9)
        self.assertTrue(any(abs(h.warp.start(t)) > 0.1 for t in (10.0, 30.0, 50.0, 70.0)))     # and it is a real shift

    def test_the_players_tempo_stays_within_limits_and_never_runs_backwards(self):
        from midiplayer.humanize import HumanizeConfig, humanize_notes, KNOW_SLOW_LIMIT, KNOW_FAST_LIMIT
        s = self.passages_song()
        for know in (0.5, 1.5, 3.0):
            for seed in range(6):
                h = humanize_notes(s, s.tempo_map, HumanizeConfig(strength=1.0, know=know), seed)
                self.assertLessEqual(max(h.warp.offsets), KNOW_SLOW_LIMIT * know + 1e-9)
                self.assertGreaterEqual(min(h.warp.offsets), -KNOW_FAST_LIMIT * know - 1e-9)
                prev = None
                for k in range(0, 4000):
                    t = k * 0.02
                    moved = t + h.warp.start(t)
                    if prev is not None:
                        self.assertGreaterEqual(moved, prev - 1e-9, "time ran backwards")
                    prev = moved

    def test_hesitations_only_happen_in_passages_that_are_not_known(self):
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        s = self.passages_song()
        jumps = 0
        for seed in range(25):
            h = humanize_notes(s, s.tempo_map, HumanizeConfig(strength=1.0, know=2.0), seed)
            T, O = h.warp.times, h.warp.offsets
            for k in range(1, len(T)):
                if T[k] == T[k - 1] and O[k] - O[k - 1] > 0.03:               # a pause: the offset jumps at one instant
                    jumps += 1
                    self.assertLess(h.fluency.at(T[k]), 0.45, "a hesitation in a passage the player knows")
        self.assertGreater(jumps, 5)

    def test_unfamiliar_passages_are_played_less_cleanly(self):
        from midiplayer.humanize import HumanizeConfig, humanize_notes
        s = self.passages_song()
        tm = s.tempo_map
        sd = {"unknown": [], "known": []}
        cfg = HumanizeConfig(strength=1.0, know=2.0, drift=0.0, timing=2.0)       # (just the per-note timing noise)
        for seed in range(30):
            h = humanize_notes(s, tm, cfg, seed)
            for (ti, i), (a, b, v) in h.items():
                n = s.tracks[ti].notes[i]
                w = tm.tick_to_sec(n.start)
                f = h.fluency.at(w)
                off = (a - w - h.warp.start(w)) * 1000
                if f < 0.3:
                    sd["unknown"].append(off)
                elif f > 0.7:
                    sd["known"].append(off)
        spread = lambda x: (sum(v * v for v in x) / len(x)) ** 0.5
        self.assertGreater(len(sd["unknown"]), 300)
        self.assertGreater(len(sd["known"]), 300)
        self.assertGreater(spread(sd["unknown"]), 1.6 * spread(sd["known"]))

    def test_levels(self):
        from midiplayer.humanize import LEVELS
        self.assertEqual(LEVELS["off"], 0.0)
        self.assertTrue(0.0 < LEVELS["light"] < LEVELS["natural"] < LEVELS["loose"] <= 2.0)
        self.assertGreater(LEVELS["loose"], 1.5 * LEVELS["natural"])          # Loose is clearly looser than Natural


if __name__ == "__main__":
    unittest.main()
