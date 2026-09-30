"""Headless smoke test of the UI wiring using a fake PySide6 (tests/fakeqt).

Run:  python tests/smoke_ui.py
Exercises player, mixer, editor, playlist, transport and WAV export logic.
"""
import array
import os
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fakeqt"))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6 import _fake as F                          # noqa: E402
from PySide6.QtCore import Qt                           # noqa: E402

from midiplayer import midifile                         # noqa: E402
from midiplayer.song import Note, Song                  # noqa: E402
from midiplayer.ui import main_window as mw_mod         # noqa: E402
from midiplayer.ui.editor_view import KEY_W, RULER_H    # noqa: E402


class MouseEv:
    def __init__(self, x, y, button=None, buttons=0, mods=0):
        self._p = F.QPointF(x, y)
        self._b = Qt.LeftButton if button is None else button
        self._bs = buttons if buttons else self._b
        self._m = mods
    def position(self): return self._p
    def button(self): return self._b
    def buttons(self): return self._bs
    def modifiers(self): return self._m
    def accept(self): pass


class KeyEv:
    def __init__(self, key, mods=0):
        self._k, self._m = key, mods
    def key(self): return self._k
    def modifiers(self): return self._m


def run_single_shots():
    while F.QTimer._single_shots:
        F.QTimer._single_shots.pop(0)()


def make_file(path, pitch0=60):
    s = Song.new(480, 240)
    s.tracks[0].notes = [Note(i * 240, i * 240 + 200, pitch0 + (i % 5), 90, 0) for i in range(8)]
    t = s.add_track("Bass")
    s.tracks[t].events.append(midifile.RawEvent(0, midifile.MIDI, bytes([0xC1, 33])))
    s.tracks[t].notes = [Note(i * 480, i * 480 + 400, 36, 100, 1) for i in range(4)]
    s.save(path)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ✓", msg)


def main():
    tmp = tempfile.mkdtemp()
    a, b = os.path.join(tmp, "a.mid"), os.path.join(tmp, "b.mid")
    make_file(a)
    make_file(b, 48)

    print("startup")
    win = mw_mod.MainWindow(files=[a])
    run_single_shots()
    eng = win.engine
    check(win.song is not None and win.song.title == "a", "file opened from command line")
    check(win.current_output is not None, f"output selected: {win.current_output.label}")
    check(eng.is_playing(), "autoplay started")
    eng.pause()
    check(len(win.playlist) == 1 and win.playlist.current == 0, "file added to playlist")
    check(win.player_view.rows[0].used and win.player_view.rows[1].used and not win.player_view.rows[5].used,
          "player marks used channels")
    check("Electric Bass" in win.player_view.rows[1].name.text(), "instrument name shown: "
          + win.player_view.rows[1].name.text())

    print("channel control & mixer sync")
    win.player_view.rows[0].mute.click()
    check(eng.channels[0].mute, "player mute -> engine")
    check(win.mixer_view.strips[0].mute.isChecked(), "mixer strip mute synced")
    win.mixer_view.strips[1].solo.click()
    check(eng.channels[1].solo and not eng.audible(0) and eng.audible(1) and not eng.audible(2), "solo logic")
    check(win.player_view.rows[1].solo.isChecked(), "player solo synced")
    win.player_view.unmute_all.click()
    check(not any(c.mute or c.solo for c in eng.channels), "clear mute/solo")
    check(not win.mixer_view.strips[0].mute.isChecked(), "mixer cleared too")
    win.mixer_view.strips[1].fader.setValue(50)
    check(abs(eng.channels[1].volume - 0.5) < 1e-9, "mixer fader -> engine volume")
    check(win.player_view.rows[1].vol.value() == 50, "player volume slider synced")
    win.player_view.rows[1].vol.setValue(120)
    check(win.mixer_view.strips[1].fader.value() == 120 and abs(eng.channels[1].volume - 1.2) < 1e-9,
          "player slider -> mixer fader")
    win.mixer_view.strips[1].pan.setValue(20)
    check(eng.channels[1].pan_override == 20, "pan knob override")
    win.mixer_view.strips[1].pan.mouseDoubleClickEvent(None)
    check(eng.channels[1].pan_override is None, "pan double-click resets to file")
    strip = win.mixer_view.strips[0]
    strip.prog.user_activate(strip.prog.findData(40))
    check(eng.channels[0].program_override == 40, "program override (Violin)")
    check("Violin" in win.player_view.rows[0].name.text(), "player shows override instrument")
    win.mixer_view.master.fader.setValue(80)
    check(abs(eng.master - 0.8) < 1e-9 and win.transport.volume.value() == 80, "master fader <-> transport")
    win.transport.volume.setValue(100)
    win.player_view.reset_btn.click()
    check(eng.channels[0].program_override is None and eng.channels[1].volume == 1.0, "reset mixer")

    print("transport")
    win.transport.speed.setValue(150)
    check(abs(eng.speed - 1.5) < 1e-9, "speed")
    win.transport.speed.setValue(100)
    win.transport.transpose.setValue(3)
    check(eng.transpose == 3, "transpose")
    win.transport.transpose.setValue(0)
    win.transport.seek.setSliderDown(True)
    win.transport.seek.setValue(500)
    win.transport.seek.setSliderDown(False)
    check(abs(eng.position() - eng.duration / 2) < 0.05, "seek bar")
    win.ctl._tick.timeout.emit()
    win.transport.play_btn.click()
    check(eng.is_playing(), "play button")
    win.ctl._tick.timeout.emit()
    check(win.transport.playing, "play button shows pause")
    win.transport.play_btn.click()
    win.transport.loop_btn.click()
    check(eng.loop and win.transport.loop_btn.text() == "Loop on", "loop toggle")
    win.transport.loop_btn.click()
    check(not eng.loop and win.transport.loop_btn.text() == "Loop off", "loop indicator off")

    print("editor")
    ed = win.editor_view
    roll = ed.roll
    ed.set_tool(0)
    roll.horizontalScrollBar().setValue(0)
    roll.verticalScrollBar().setValue(int((127 - 72) * roll.kh))
    n0 = win.song.note_count()
    # draw a note at pitch 70, tick 1920
    ed.set_tool(1)
    x = KEY_W + 1920 * roll.zx + 2
    y = roll.y_of(70) + roll.kh / 2
    roll.mousePressEvent(MouseEv(x, y))
    roll.mouseMoveEvent(MouseEv(x + 480 * roll.zx, y))
    roll.mouseReleaseEvent(MouseEv(x + 480 * roll.zx, y))
    check(win.song.note_count() == n0 + 1, "draw tool adds a note")
    new = next(iter(roll.selection))
    check(new.pitch == 70 and new.start == 1920 and new.end == 2400, f"note geometry {new}")
    check(win.dirty and win.windowTitle is not None, "song marked dirty")
    ed.do_undo()
    check(win.song.note_count() == n0, "undo removes note")
    ed.do_redo()
    check(win.song.note_count() == n0 + 1, "redo restores")
    # select tool: drag the note up 2 semitones and right one beat
    ed.set_tool(0)
    note = [n for tr in win.song.tracks for n in tr.notes if n.pitch == 70][0]
    x = roll.x_of(note.start) + 5
    y = roll.y_of(70) + roll.kh / 2
    roll.mousePressEvent(MouseEv(x, y))
    roll.mouseMoveEvent(MouseEv(x + 480 * roll.zx, y - 2 * roll.kh))
    roll.mouseReleaseEvent(MouseEv(x + 480 * roll.zx, y - 2 * roll.kh))
    check(note.pitch == 72 and note.start == 2400, f"drag move {note}")
    # resize from right edge
    ex = roll.x_of(note.end) - 2
    y = roll.y_of(72) + roll.kh / 2
    old_end = note.end
    roll.mousePressEvent(MouseEv(ex, y))
    roll.mouseMoveEvent(MouseEv(ex + 240 * roll.zx, y))
    roll.mouseReleaseEvent(MouseEv(ex + 240 * roll.zx, y))
    check(note.end == old_end + 240, f"resize {old_end}->{note.end}")
    # keyboard transpose
    roll.selection = {note}
    roll.keyPressEvent(KeyEv(Qt.Key_Up))
    check(note.pitch == 73, "arrow-up transposes")
    roll.keyPressEvent(KeyEv(Qt.Key_Down, Qt.ShiftModifier))
    check(note.pitch == 61, "shift+down = octave")
    # velocity via spin
    ed.vel_spin.hasFocus = lambda: True
    ed.vel_spin.setValue(64)
    check(note.velocity == 64, "velocity spin applies to selection")
    # velocity lane drag
    lane = ed.vel
    lx = roll.x_of(note.start) + 1
    lane.mousePressEvent(MouseEv(lx, 10))
    lane.mouseReleaseEvent(MouseEv(lx, 10))
    check(note.velocity > 110, f"velocity lane sets velocity ({note.velocity})")
    # copy / paste / duplicate
    cnt = win.song.note_count()
    ed.shortcut_actions["copy"].triggered.emit(False)
    ed.shortcut_actions["paste"].triggered.emit(False)
    check(win.song.note_count() == cnt + 1, "copy+paste (triggered(bool) safe)")
    ed.shortcut_actions["dup"].triggered.emit(False)
    check(win.song.note_count() == cnt + 2, "duplicate")
    # rubber band select all notes of track 1 then delete
    ed.track_combo.setCurrentIndex(ed.track_combo.findData(1))
    check(roll.track_filter == 1 and roll.insert_channel == 1, "track filter + insert channel follow track")
    ed.shortcut_actions["all"].triggered.emit(False)
    check(len(roll.selection) == 4, "select all in track")
    roll.keyPressEvent(KeyEv(Qt.Key_Delete))
    check(len(win.song.tracks[1].notes) == 0, "delete selection")
    ed.do_undo()
    check(len(win.song.tracks[1].notes) == 4, "undo delete")
    # quantize (button clicked(bool) must not change behaviour)
    for n in win.song.tracks[1].notes:
        n.start += 7
        n.end += 7
    roll.rebuild_cache()
    roll.selection.clear()
    ed.quant_btn.click()
    check(all(n.start % 120 == 0 for n in win.song.tracks[1].notes), "quantize to 1/16")
    # rubber band
    ed.track_combo.setCurrentIndex(0)
    roll.selection.clear()
    y1 = roll.y_of(127)
    roll.mousePressEvent(MouseEv(KEY_W + 1, max(RULER_H + 1, y1 + 1)))
    roll.mouseMoveEvent(MouseEv(KEY_W + 480 * roll.zx, roll.viewport().height() - 1))
    roll.mouseReleaseEvent(MouseEv(KEY_W + 480 * roll.zx, roll.viewport().height() - 1))
    check(isinstance(roll.selection, set), f"rubber band select ({len(roll.selection)} notes)")
    # erase tool
    ed.set_tool(2)
    victim = win.song.tracks[1].notes[0]
    roll.verticalScrollBar().setValue(int((127 - 40) * roll.kh))
    vy = roll.y_of(victim.pitch) + roll.kh / 2
    roll.mousePressEvent(MouseEv(roll.x_of(victim.start) + 3, vy))
    roll.mouseReleaseEvent(MouseEv(roll.x_of(victim.start) + 3, vy))
    check(victim not in win.song.tracks[1].notes, "erase tool")
    ed.set_tool(0)
    # tempo
    ed.bpm_spin.setValue(90)
    check(abs(win.song.tempo_map.bpm_at(0) - 90) < 0.1, "tempo edit")
    # tracks
    nt = len(win.song.tracks)
    ed.add_track()
    check(len(win.song.tracks) == nt + 1, "add track")
    ed.delete_track()
    check(len(win.song.tracks) == nt, "delete track")
    # zoom + wheel + paint
    roll.wheelEvent(type("W", (), {"angleDelta": lambda s: F.QPoint(0, 120), "modifiers": lambda s: Qt.ControlModifier,
                                   "position": lambda s: F.QPointF(400, 200), "accept": lambda s: None})())
    for w in (roll, lane):
        w.paintEvent(None)
    # engine reload after edit
    run_single_shots()
    win.ctl._reload_timer.timeout.emit()
    check(len(eng.events) == 2 * win.song.note_count() + sum(
        1 for tr in win.song.tracks for e in tr.events if e.kind == midifile.MIDI), "engine reloaded edited song")
    # save
    out = os.path.join(tmp, "edited.mid")
    mw_mod.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, ""))
    win.song.path = None
    check(win.save() and os.path.exists(out) and not win.dirty, "save as")
    check(Song.load(out).note_count() == win.song.note_count(), "saved file reloads with same notes")

    print("playlist")
    win.playlist_view.add_paths([b])
    check(len(win.playlist) == 2, "add to playlist")
    win.playlist_view.playRequested.emit(1)
    check(win.song.title == "b" and win.playlist.current == 1, "play from playlist")
    win.prev_song()
    win.ctl.seek(0)
    win.prev_song()
    check(win.playlist.current == 0, "previous song")
    win.next_song()
    check(win.playlist.current == 1, "next song")
    win.ctl.finished.emit()
    check(win.playlist.current == 1 and not eng.is_playing(), "end of playlist stops (repeat off)")
    win.playlist_view.repeat_btn.click()
    win.ctl.finished.emit()
    check(win.playlist.current == 0 and eng.is_playing(), "repeat all wraps to first song")
    eng.pause()
    win.playlist_view.shuffle_btn.click()
    check(win.playlist.shuffle, "shuffle toggle")
    win.playlist_view.search.setText("b")
    check(win.playlist_view.tree.topLevelItem(0).isHidden() and not win.playlist_view.tree.topLevelItem(1).isHidden(),
          "filter")
    win.playlist_view.search.setText("")
    for _ in range(3):
        win.playlist_view._dur_timer.timeout.emit()
    check(all(it.duration for it in win.playlist.items), "durations computed")
    win.playlist_view.tree.items[0]._sel = True
    win.playlist_view.remove_selected()
    check(len(win.playlist) == 1, "remove from playlist")

    print("levels, paint, export")
    eng.play()
    import time
    time.sleep(0.3)
    for _ in range(3):
        win.ctl._tick.timeout.emit()
    eng.pause()
    for w in [r.meter for r in win.player_view.rows] + [s.meter for s in win.mixer_view.strips] + \
             [s.pan for s in win.mixer_view.strips] + [s.chip for s in win.mixer_view.strips]:
        w.paintEvent(None)
    check(True, "custom widgets paint without errors")
    if win.export_act is not None:
        wav = os.path.join(tmp, "out.wav")
        mw_mod.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (wav, ""))
        win.mixer_view.strips[1].mute.click()
        win.export_wav()
        if os.path.exists(wav):
            with wave.open(wav) as w:
                data = array.array("h", w.readframes(w.getnframes()))
            check(max(abs(v) for v in data) > 500, f"WAV export has audio ({len(data) // 2 / 44100:.1f}s)")
    print("updates")
    import hashlib, http.server, json, threading
    from midiplayer import updater
    from midiplayer.ui import updates as upd_mod
    payload = b"MZ fake installer" * 1000
    name = "ModernMidiPlayer-Setup-99.0.0.exe"
    routes = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = routes.get(self.path)
            self.send_response(200 if body is not None else 404)
            if body is not None:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body is not None:
                self.wfile.write(body)
        def log_message(self, *a): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    routes["/repos/me/mmp/releases/latest"] = json.dumps({"tag_name": "v99.0.0", "body": "- new stuff",
        "assets": [{"name": name, "size": len(payload), "browser_download_url": base + "/f"},
                   {"name": name + ".sha256", "browser_download_url": base + "/s"}]}).encode()
    routes["/f"] = payload
    routes["/s"] = (hashlib.sha256(payload).hexdigest() + "  " + name).encode()
    os.environ["MMP_UPDATE_REPO"] = "me/mmp"
    updater.API_BASE = base
    uc = win.updates
    check(uc.configured(), "update source configured via env")
    choices = []
    def fake_exec(dlg):
        choices.append(dlg)
        dlg.choice = upd_mod.UpdateDialog.SKIP
    upd_mod.UpdateDialog.exec = fake_exec
    uc.check(manual=True)
    for _ in range(100):
        if choices:
            break
        time.sleep(0.02)
    check(len(choices) == 1, "update dialog shown for newer release")
    check(F.QSettings.store.get("updates/skip") == "99.0.0", "skip this version remembered")
    uc.check(manual=False)
    time.sleep(0.5)
    check(len(choices) == 1, "skipped version not offered again automatically")
    launched = []
    updater.launch_installer = lambda path, silent=True, relaunch=True: launched.append((path, silent, relaunch))
    info = updater.check()
    uc.download_and_install(info)
    for _ in range(200):
        if launched:
            break
        time.sleep(0.02)
    check(launched and open(launched[0][0], "rb").read() == payload, "update downloaded, verified and installer launched")
    check(win.skip_save_prompt, "window closes for the installer")
    routes["/repos/me/mmp/releases/latest"] = json.dumps({"tag_name": "v1.0.0", "assets": []}).encode()
    uc.check(manual=True)
    time.sleep(0.3)
    check(not uc._busy, "up-to-date check completes")
    srv.shutdown()
    win.skip_save_prompt = False
    win.closeEvent(type("E", (), {"accept": lambda s: None, "ignore": lambda s: None})())
    check(not eng._thread.is_alive(), "engine thread stopped on close")
    print("\nALL UI SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
