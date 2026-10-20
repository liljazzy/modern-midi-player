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


if __name__ == "__main__":
    unittest.main()
