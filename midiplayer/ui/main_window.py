"""Main application window."""
from __future__ import annotations

import os
import wave
from typing import List, Optional

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QLabel, QMainWindow, QMessageBox,
                               QProgressDialog, QTabWidget, QVBoxLayout, QWidget)

from .. import HOMEPAGE, __version__, updater
from ..backends import (BackendError, FluidSynthBackend, NullBackend, OutputInfo, available_outputs,
                        find_fluidsynth, find_soundfonts, open_output)
from ..engine import Engine
from ..playlist import Playlist, is_midi_file, scan_folder
from ..song import Song
from .controller import Controller
from .editor_view import EditorView
from .mixer_view import MixerView
from .player_view import PlayerView
from .playlist_view import MIDI_FILTER, PlaylistView
from .transport import TransportBar
from .updates import UpdateController

ORG, APP = "ModernMidi", "MidiPlayer"


class MainWindow(QMainWindow):
    def __init__(self, files: Optional[List[str]] = None):
        super().__init__()
        self.settings = QSettings(ORG, APP)
        self.engine = Engine(NullBackend())
        self.ctl = Controller(self.engine, self)
        self.playlist = Playlist()
        self.song: Optional[Song] = None
        self.dirty = False
        self.from_playlist = False
        self._fail_streak = 0
        self.outputs: List[OutputInfo] = []
        self.current_output: Optional[OutputInfo] = None
        self.skip_save_prompt = False
        self.updates = UpdateController(self, self)

        self.setWindowTitle("Modern MIDI Player")
        self.resize(1360, 860)
        self.setAcceptDrops(True)

        # ---- central area
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.player_view = PlayerView(self.ctl)
        self.mixer_view = MixerView(self.ctl)
        self.editor_view = EditorView(self.ctl)
        self.tabs.addTab(self.player_view, "Player")
        self.tabs.addTab(self.mixer_view, "Mixer")
        self.tabs.addTab(self.editor_view, "Editor")
        cl.addWidget(self.tabs, 1)
        self.transport = TransportBar(self.ctl)
        cl.addWidget(self.transport)
        self.setCentralWidget(central)

        # ---- playlist dock
        self.playlist_view = PlaylistView(self.playlist)
        self.dock = QDockWidget("Playlist", self)
        self.dock.setObjectName("playlistDock")
        self.dock.setWidget(self.playlist_view)
        self.dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable |
                              QDockWidget.DockWidgetClosable)
        self.dock.setTitleBarWidget(QWidget())
        self.dock.setMinimumWidth(300)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)

        # ---- status bar
        self.out_label = QLabel("")
        self.out_label.setObjectName("dim")
        self.statusBar().addPermanentWidget(self.out_label)

        self._build_menus()
        self._wire()
        self._restore_settings()
        self._init_output()
        self._update_title()
        self.updates.statusMessage.connect(lambda m: self.statusBar().showMessage(m, 8000))
        QTimer.singleShot(0, self.updates.startup)

        if files:
            QTimer.singleShot(0, lambda: self.open_paths(files))
        elif self.playlist.current_item() is not None:
            QTimer.singleShot(0, lambda: self.play_index(self.playlist.current, autoplay=False))

    # ================================================================ menus
    def _build_menus(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        self._act(f, "&New song", QKeySequence.New, self.new_song)
        self._act(f, "&Open…", QKeySequence.Open, self.open_dialog)
        self._act(f, "Open &folder…", "Ctrl+Shift+O", self.open_folder_dialog)
        self.recent_menu = f.addMenu("Open &recent")
        f.addSeparator()
        self._act(f, "&Save", QKeySequence.Save, self.save)
        self._act(f, "Save &as…", QKeySequence.SaveAs, self.save_as)
        self.export_act = self._act(f, "&Export to WAV…", "Ctrl+E", self.export_wav)
        f.addSeparator()
        self._act(f, "&Quit", QKeySequence.Quit, self.close)

        p = mb.addMenu("&Playback")
        self._act(p, "Play / Pause", None, self.ctl.toggle)
        self._act(p, "Stop", None, self.ctl.stop)
        self._act(p, "Next song", "Ctrl+Right", self.next_song)
        self._act(p, "Previous song", "Ctrl+Left", self.prev_song)
        p.addSeparator()
        self._act(p, "Forward 5 s", ".", lambda: self.ctl.seek(self.engine.position() + 5))
        self._act(p, "Back 5 s", ",", lambda: self.ctl.seek(self.engine.position() - 5))
        p.addSeparator()
        self._act(p, "Clear mute / solo", "Ctrl+Shift+M", self.ctl.clear_mute_solo)
        self._act(p, "Reset mixer", None, self.ctl.reset_mixer)
        p.addSeparator()
        self._act(p, "Panic (all notes off)", "Ctrl+.", self.panic)

        self.output_menu = mb.addMenu("&Output")

        v = mb.addMenu("&View")
        self._act(v, "Player", "Ctrl+1", lambda: self.tabs.setCurrentIndex(0))
        self._act(v, "Mixer", "Ctrl+2", lambda: self.tabs.setCurrentIndex(1))
        self._act(v, "Editor", "Ctrl+3", lambda: self.tabs.setCurrentIndex(2))
        v.addSeparator()
        tog = self.dock.toggleViewAction()
        tog.setText("Show playlist")
        tog.setShortcut("Ctrl+L")
        v.addAction(tog)

        h = mb.addMenu("&Help")
        self._act(h, "Keyboard shortcuts", "F1", self.show_shortcuts)
        h.addSeparator()
        self._act(h, "Check for updates…", None, lambda: self.updates.check(manual=True))
        self._act(h, "Install update from file…", None, self.updates.install_from_file)
        self._act(h, "Update source…", None, self.updates.choose_source)
        auto = QAction("Check for updates automatically", self)
        auto.setCheckable(True)
        auto.setChecked(self.updates.auto_check)
        auto.toggled.connect(self.updates.set_auto_check)
        h.addAction(auto)
        h.addSeparator()
        self._act(h, "Project on GitHub", None, self.open_homepage)
        self._act(h, "About", None, self.show_about)

        # Space = play/pause anywhere (text fields still receive spaces)
        sc = QShortcut(QKeySequence("Space"), self)
        sc.activated.connect(self.ctl.toggle)

    def _act(self, menu, text, shortcut, fn):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut) if isinstance(shortcut, str) else shortcut)
        a.triggered.connect(fn)
        menu.addAction(a)
        return a

    def _build_output_menu(self):
        m = self.output_menu
        m.clear()
        grp = QActionGroup(self)
        grp.setExclusive(True)
        for info in self.outputs:
            a = QAction(info.label, self)
            a.setCheckable(True)
            a.setChecked(self.current_output is not None and (info.kind, info.ident) ==
                         (self.current_output.kind, self.current_output.ident))
            a.triggered.connect(lambda _=False, i=info: self.select_output(i))
            grp.addAction(a)
            m.addAction(a)
        m.addSeparator()
        m.addAction("Load SoundFont (.sf2)…", self.choose_soundfont)
        m.addAction("Locate FluidSynth library…", self.choose_fluid_lib)
        m.addAction("Refresh devices", self.refresh_outputs)
        m.addSeparator()
        info = m.addAction("Output help…")
        info.triggered.connect(self.show_output_help)

    # ================================================================ wiring
    def _wire(self):
        self.ctl.finished.connect(self._on_finished)
        self.ctl.engineError.connect(lambda msg: self.statusBar().showMessage(msg, 5000))
        self.transport.prevRequested.connect(self.prev_song)
        self.transport.nextRequested.connect(self.next_song)
        self.playlist_view.playRequested.connect(lambda i: self.play_index(i, autoplay=True))
        self.playlist_view.changed.connect(self._save_playlist)
        self.editor_view.edited.connect(self._on_edited)

    # ================================================================ output
    def _soundfont_list(self) -> List[str]:
        user = [p for p in self.settings.value("soundfonts", [], list) or [] if os.path.exists(p)]
        found = find_soundfonts()
        return user + [p for p in found if p not in user]

    def _fluid_lib(self) -> str:
        return self.settings.value("fluidLib", "", str) or ""

    def refresh_outputs(self):
        self.outputs = available_outputs(self._soundfont_list(), self._fluid_lib())
        self._build_output_menu()

    def _init_output(self):
        self.refresh_outputs()
        kind = self.settings.value("outputKind", "", str)
        ident = self.settings.value("outputIdent", "", str)
        order = sorted(self.outputs, key=lambda o: 0 if (o.kind, o.ident) == (kind, ident) else 1)
        for info in order:
            if self.select_output(info, quiet=True):
                break
        if self.current_output and self.current_output.kind == "null":
            QTimer.singleShot(400, lambda: self.statusBar().showMessage(
                "No sound output available — see Output ▸ Output help…", 15000))

    def select_output(self, info: OutputInfo, quiet: bool = False) -> bool:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            backend = open_output(info, self._fluid_lib())
        except (BackendError, OSError) as e:
            QApplication.restoreOverrideCursor()
            if not quiet:
                QMessageBox.warning(self, "Output", f"Could not open “{info.label}”:\n{e}")
                self._build_output_menu()
            return False
        QApplication.restoreOverrideCursor()
        self.engine.set_backend(backend)
        self.engine.set_master(self.engine.master)
        self.current_output = info
        self.settings.setValue("outputKind", info.kind)
        self.settings.setValue("outputIdent", info.ident)
        self.out_label.setText(f"🔈 {backend.name}")
        self._build_output_menu()
        self.export_act.setEnabled(find_fluidsynth(self._fluid_lib()) is not None and bool(self._soundfont_list()))
        return True

    def choose_soundfont(self):
        p, _ = QFileDialog.getOpenFileName(self, "Choose SoundFont", os.path.expanduser("~"), "SoundFonts (*.sf2 *.SF2)")
        if not p:
            return
        user = [x for x in (self.settings.value("soundfonts", [], list) or []) if x != p]
        self.settings.setValue("soundfonts", [p] + user)
        self.refresh_outputs()
        if not find_fluidsynth(self._fluid_lib()):
            QMessageBox.information(self, "SoundFont added",
                                    "The SoundFont was added, but the FluidSynth library was not found.\n"
                                    "See Output ▸ Output help… to install it.")
            return
        for info in self.outputs:
            if info.kind == "fluidsynth" and info.ident == p:
                self.select_output(info)
                break

    def choose_fluid_lib(self):
        p, _ = QFileDialog.getOpenFileName(self, "Locate FluidSynth library", os.path.expanduser("~"),
                                           "Libraries (*.dll *.so *.so.* *.dylib);;All files (*)")
        if p:
            if not find_fluidsynth(p):
                QMessageBox.warning(self, "FluidSynth", "That file could not be loaded as the FluidSynth library.")
                return
            self.settings.setValue("fluidLib", p)
            self.refresh_outputs()

    def show_output_help(self):
        QMessageBox.information(self, "Sound output", (
            "Modern MIDI Player can play through:\n\n"
            "• FluidSynth (best quality, all platforms) — needs the FluidSynth library and a .sf2 SoundFont.\n"
            "   Windows: download FluidSynth from github.com/FluidSynth/fluidsynth/releases and put the\n"
            "   DLLs from its 'bin' folder in a 'fluidsynth' folder next to this app (or use Locate FluidSynth\n"
            "   library…).  macOS: brew install fluid-synth.  Linux: install libfluidsynth3 / fluidsynth.\n"
            "   SoundFonts in a 'soundfonts' folder next to the app are found automatically.\n\n"
            "• Microsoft GS Wavetable Synth — built into Windows, works out of the box.\n\n"
            "• Any hardware or virtual MIDI port — install python-rtmidi (pip install python-rtmidi)."))

    def panic(self):
        self.engine.pause()
        with self.engine.lock:
            try:
                self.engine.backend.reset()
            except Exception:
                pass
        self.engine.seek(self.engine.position())

    # ================================================================ songs
    def maybe_save(self) -> bool:
        if not self.dirty or self.song is None:
            return True
        r = QMessageBox.question(self, "Unsaved changes", f"Save changes to “{self.song.title}”?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save()
        return True

    def set_song(self, song: Optional[Song]):
        self.song = song
        self.dirty = False
        self.ctl.set_song(song)
        self._update_title()

    def load_path(self, path: str, autoplay: bool = True) -> bool:
        try:
            song = Song.load(path)
        except Exception as e:
            self.statusBar().showMessage(f"Could not open {os.path.basename(path)}: {e}", 8000)
            return False
        self.set_song(song)
        self._add_recent(path)
        if autoplay:
            self.ctl.play()
        self.statusBar().showMessage(f"Loaded {os.path.basename(path)}", 3000)
        return True

    def play_index(self, idx: int, autoplay: bool = True):
        if not (0 <= idx < len(self.playlist)):
            return
        if not self.maybe_save():
            return
        self.playlist.set_current(idx)
        self.from_playlist = True
        ok = self.load_path(self.playlist.items[idx].path, autoplay)
        self.playlist_view.highlight_current()
        self._save_playlist()
        if not ok:
            self._fail_streak += 1
            if autoplay and self._fail_streak < len(self.playlist):
                QTimer.singleShot(50, lambda: self._advance(auto=True))
        else:
            self._fail_streak = 0

    def _advance(self, auto: bool):
        nxt = self.playlist.next_index(auto=auto)
        if nxt >= 0:
            self.play_index(nxt, autoplay=True)
        else:
            self.ctl.stop()

    def next_song(self):
        if len(self.playlist):
            self._advance(auto=False)

    def prev_song(self):
        if self.engine.position() > 3 or not len(self.playlist):
            self.ctl.seek(0)
            return
        i = self.playlist.prev_index()
        if i >= 0:
            self.play_index(i, autoplay=True)

    def _on_finished(self):
        if self.engine.loop:
            return
        if self.from_playlist and len(self.playlist) and not self.dirty:
            self._advance(auto=True)
        else:
            self.ctl.stop()

    def open_paths(self, paths: List[str]):
        files: List[str] = []
        for p in paths:
            if os.path.isdir(p):
                files.extend(scan_folder(p))
            elif is_midi_file(p) or os.path.isfile(p):
                files.append(p)
        if not files:
            return
        if not self.maybe_save():
            return
        if len(files) == 1 and not is_midi_file(files[0]):
            self.from_playlist = False
            self.load_path(files[0])
            return
        existing = {os.path.normcase(os.path.abspath(it.path)): i for i, it in enumerate(self.playlist.items)}
        if len(files) == 1 and os.path.normcase(os.path.abspath(files[0])) in existing:
            self.play_index(existing[os.path.normcase(os.path.abspath(files[0]))], autoplay=True)
            return
        start = len(self.playlist)
        self.playlist_view.add_paths(files)
        self.play_index(start, autoplay=True)

    def new_song(self):
        if not self.maybe_save():
            return
        self.from_playlist = False
        self.set_song(Song.new())
        self.tabs.setCurrentWidget(self.editor_view)
        self.editor_view.set_tool(1)

    def open_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Open MIDI files", self.settings.value("lastDir", "", str),
                                                MIDI_FILTER)
        if paths:
            self.settings.setValue("lastDir", os.path.dirname(paths[0]))
            self.open_paths(paths)

    def open_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Open folder", self.settings.value("lastDir", "", str))
        if d:
            self.settings.setValue("lastDir", d)
            self.open_paths([d])

    def save(self) -> bool:
        if self.song is None:
            return False
        if not self.song.path:
            return self.save_as()
        try:
            self.song.save(self.song.path)
        except OSError as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return False
        self.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved {self.song.path}", 4000)
        return True

    def save_as(self) -> bool:
        if self.song is None:
            return False
        start = self.song.path or os.path.join(self.settings.value("lastDir", "", str) or os.path.expanduser("~"),
                                               "untitled.mid")
        p, _ = QFileDialog.getSaveFileName(self, "Save MIDI file", start, "MIDI files (*.mid)")
        if not p:
            return False
        if not p.lower().endswith((".mid", ".midi")):
            p += ".mid"
        try:
            self.song.save(p)
        except OSError as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return False
        self.dirty = False
        self._add_recent(p)
        self.player_view.on_song(self.song)
        self.transport._on_song(self.song)
        self._update_title()
        return True

    def export_wav(self):
        if self.song is None:
            return
        sfs = self._soundfont_list()
        sf = self.current_output.ident if self.current_output and self.current_output.kind == "fluidsynth" else \
            (sfs[0] if sfs else "")
        base = os.path.splitext(self.song.path or "untitled.mid")[0] + ".wav"
        p, _ = QFileDialog.getSaveFileName(self, "Export to WAV", base, "WAV audio (*.wav)")
        if not p:
            return
        try:
            synth = FluidSynthBackend(sf, self._fluid_lib(), render_only=True)
        except BackendError as e:
            QMessageBox.warning(self, "Export", f"Export needs FluidSynth and a SoundFont:\n{e}")
            return
        rate = 44100
        events = self.song.playback_events()
        total = self.song.duration() + 2.0
        # A private engine applies the current mixer (mute/solo/volume/pan…)
        eng = Engine(synth)
        try:
            with eng.lock:
                for src, dst in zip(self.engine.channels, eng.channels):
                    for attr in ("mute", "solo", "volume", "pan_override", "reverb_override", "chorus_override",
                                 "program_override"):
                        setattr(dst, attr, getattr(src, attr))
                eng.master = self.engine.master
                synth.set_gain(eng.master)
                eng.transpose = self.engine.transpose
                eng.events, eng.times = events, [e.time for e in events]
                eng.duration = total
                eng._seek(0.0)
            dlg = QProgressDialog("Rendering audio…", "Cancel", 0, 1000, self)
            dlg.setWindowModality(Qt.WindowModal)
            dlg.setMinimumDuration(200)
            with wave.open(p, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(rate)
                frame = 0
                idx = 0
                block = 512
                while frame / rate < total:
                    t = frame / rate
                    with eng.lock:
                        while idx < len(events) and events[idx].time <= t:
                            eng._dispatch(events[idx].data)
                            idx += 1
                    w.writeframes(synth.render(block))
                    frame += block
                    if frame % (block * 64) == 0:
                        dlg.setValue(int(1000 * t / total))
                        QApplication.processEvents()
                        if dlg.wasCanceled():
                            break
            dlg.setValue(1000)
            self.statusBar().showMessage(f"Exported {p}", 6000)
        finally:
            eng.close()

    # ================================================================ misc
    def _on_edited(self):
        self.dirty = True
        self._update_title()

    def _update_title(self):
        t = "Modern MIDI Player"
        if self.song is not None:
            t = f"{'● ' if self.dirty else ''}{self.song.title} — {t}"
        self.setWindowTitle(t)

    def _add_recent(self, path: str):
        rec = [p for p in (self.settings.value("recent", [], list) or []) if p != path]
        rec.insert(0, path)
        self.settings.setValue("recent", rec[:12])
        self._build_recent()

    def _build_recent(self):
        self.recent_menu.clear()
        rec = [p for p in (self.settings.value("recent", [], list) or []) if os.path.exists(p)]
        for p in rec:
            self.recent_menu.addAction(os.path.basename(p), lambda _=False, x=p: self._open_recent(x))
        self.recent_menu.setEnabled(bool(rec))

    def _open_recent(self, path: str):
        if self.maybe_save():
            self.from_playlist = False
            self.load_path(path)

    def _save_playlist(self):
        self.settings.setValue("playlist", self.playlist.to_json())

    def _restore_settings(self):
        geo = self.settings.value("geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        st = self.settings.value("windowState")
        if isinstance(st, QByteArray):
            self.restoreState(st)
        pl = self.settings.value("playlist", "", str)
        if pl:
            self.playlist.from_json(pl)
            self.playlist_view.rebuild()
        try:
            master = float(self.settings.value("master", 1.0))
        except (TypeError, ValueError):
            master = 1.0
        self.ctl.set_master(master)
        self._build_recent()

    def show_shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts", (
            "Space\tPlay / pause\n"
            "Ctrl+← / →\tPrevious / next song\n"
            ", / .\tBack / forward 5 seconds\n"
            "Ctrl+1/2/3\tPlayer / Mixer / Editor\n"
            "Ctrl+L\tShow / hide playlist\n"
            "Ctrl+Shift+M\tClear mute / solo\n"
            "Ctrl+.\tPanic (all notes off)\n\n"
            "Editor\n"
            "1 / 2 / 3\tSelect / Draw / Erase tool\n"
            "Double-click\tAdd note   ·   Right-click: delete note\n"
            "Drag\tMove notes (edge: resize, Alt: copy)\n"
            "↑ / ↓\tTranspose selection (Shift: octave)\n"
            "← / →\tMove selection by grid\n"
            "Del\tDelete selection\n"
            "Ctrl+A / C / X / V / D\tSelect all / copy / cut / paste / duplicate\n"
            "Ctrl+Z / Ctrl+Y\tUndo / redo\n"
            "Q\tQuantize\n"
            "Ctrl+wheel / Alt+wheel\tZoom time / key height\n\n"
            "Mixer / channels\n"
            "Right-click S\tSolo only this channel\n"
            "Double-click fader/knob\tReset"))

    def open_homepage(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(HOMEPAGE))

    def show_about(self):
        QMessageBox.about(self, "About", f"<b>Modern MIDI Player</b> {__version__}<br>"
                                         "Player · Mixer · Playlist · Piano-roll editor<br><br>"
                                         "Built with Python and Qt (PySide6).<br>"
                                         f'<a href="{HOMEPAGE}">{HOMEPAGE}</a><br><br>'
                                         + (f"Updates: github.com/{updater.update_repo()}"
                                            if updater.update_repo() else "Updates: not configured"))

    # ================================================================ events
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.open_paths(paths)

    def closeEvent(self, e):
        if not self.skip_save_prompt and not self.maybe_save():
            e.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.setValue("master", self.engine.master)
        self._save_playlist()
        self.engine.close()
        e.accept()
