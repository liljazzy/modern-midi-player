# Modern MIDI Player

A native desktop MIDI player for Windows, macOS and Linux. It's written in Python with Qt (PySide6), and it has:

- **Channel control**: mute, solo and volume for each of the 16 MIDI channels. Right-click **S** to solo only that channel.
- **Mixer**: a strip per channel with an instrument override, reverb and chorus sends, a pan knob, a fader and a level meter. There's also a master strip with stereo meters.
- **Playlist**: add files or whole folders, drag and drop to reorder, filter, shuffle, repeat (off / all / one), and load or save M3U playlists. The playlist is remembered between sessions.
- **Editor**: a piano-roll editor with select, draw and erase tools. You can move, resize, Alt-drag to copy, rubber-band select, quantize, transpose, copy/cut/paste/duplicate, undo and redo. It also has a velocity lane, track add/rename/delete/instrument, tempo editing, and saves back to `.mid`.
- **Transport**: play/pause, stop, previous/next, a seek bar, loop, speed (10–400 %), transpose (±24, drums aren't affected) and master volume.
- **Export to WAV**: renders offline with FluidSynth and respects your mute, solo and mixer settings.

## Install on Windows (recommended)

Run **`ModernMidiPlayer-Setup-1.1.0.exe`**. It installs for the current user only, with no admin rights needed, and includes:
- a private Python runtime, so you don't need Python installed
- the FluidSynth synthesizer and a General MIDI SoundFont, for high-quality sound with no setup
- Start-menu and desktop shortcuts, and "Open with" registration for .mid, .midi, .kar and .rmi files
- an uninstaller listed under Settings ▸ Apps

Setup downloads the Qt user interface library (PySide6, about 80 MB) once, so it needs an internet connection. If that step fails, the app offers to download it the first time it starts.

Windows SmartScreen may say "Windows protected your PC", because the installer isn't code-signed. Click **More info ▸ Run anyway**.

## Run from source

```bash
pip install -r requirements.txt      # installs PySide6
python run.py                        # or: python -m midiplayer  [files or folders…]
```

On Windows you can double-click `run_windows.bat`. On macOS or Linux, run `./run.sh`.

Try `demos/demo_groove.mid`. It has five tracks (keys, bass, pad, lead, drums), so it's a good way to test solo, mute and the mixer.

## Sound output (Output menu)

| Output | Platforms | Setup |
|---|---|---|
| **FluidSynth** (best) | all | Needs the FluidSynth library and a `.sf2` SoundFont. A GM SoundFont (TimGM6mb) is already in `soundfonts/`. |
| **Microsoft GS Wavetable Synth** | Windows | Built in. It works with no setup. |
| **MIDI ports** (hardware/virtual) | all | `pip install python-rtmidi` |

How to get the FluidSynth library:
- **Windows:** download the latest release zip from https://github.com/FluidSynth/fluidsynth/releases. Copy the DLLs from its `bin` folder into a folder named `fluidsynth` next to `run.py`. Or use **Output ▸ Locate FluidSynth library…**.
- **macOS:** `brew install fluid-synth`
- **Linux:** `sudo apt install libfluidsynth3` (or your distribution's `fluidsynth` package)

For a better sound, put a bigger SoundFont in `soundfonts/`, such as GeneralUser GS or FluidR3_GM, or use **Output ▸ Load SoundFont…**.

## Keyboard shortcuts

| Key | Action |
|---|---|
| Space | Play / pause |
| Ctrl+← / → | Previous / next song |
| `,` / `.` | Back / forward 5 s |
| Ctrl+1 / 2 / 3 | Player / Mixer / Editor tab |
| Ctrl+L | Show/hide playlist |
| Ctrl+Shift+M | Clear all mute/solo |
| Ctrl+. | Panic (all notes off) |
| **Editor** | |
| 1 / 2 / 3 | Select / Draw / Erase tool |
| Double-click / Right-click | Add note / delete note |
| Drag · edge-drag · Alt-drag | Move · resize · copy |
| ↑ ↓ (Shift = octave), ← → | Transpose, move by grid |
| Ctrl+A/C/X/V/D | Select all / copy / cut / paste at playhead / duplicate |
| Ctrl+Z / Ctrl+Y | Undo / redo |
| Q | Quantize (selection, or the whole track) |
| Ctrl+wheel / Alt+wheel / Shift+wheel | Zoom time / zoom keys / scroll horizontally |

Double-click any fader or knob to reset it. Double-clicking pan, reverb or chorus hands control back to the file.

## How it works

```
midiplayer/
  midifile.py   SMF reader/writer with no dependencies. Handles running status, RIFF .rmi, and truncated or malformed files.
  song.py       Editable song model (notes + raw events), tempo map, undo stack.
  engine.py     Real-time playback thread with a channel mixer (mute/solo/volume/pan/program overrides),
                controller chasing on seek, speed, transpose and loop. Timing jitter is under 1 ms.
  backends.py   FluidSynth (ctypes), WinMM (ctypes), rtmidi, null/recording outputs.
  playlist.py   Playlist model: shuffle without repeats, repeat modes, M3U/JSON.
  ui/           Qt views: player, mixer, piano-roll editor, playlist, transport, theme.
```

Mixer volume is applied by scaling each channel's CC7 value from the file. That means it works the same way on every output: synth, Windows GS or hardware.

## Tests

```bash
python -m unittest tests.test_core tests.test_updater     # file I/O, tempo map, engine mute/solo/volume/seek, playlist
python tests/smoke_ui.py               # headless UI wiring test (uses a fake Qt, no display needed)
```

## Build the Windows installer

```bash
pip install pillow
python installer/build_installer.py    # needs NSIS (makensis) → dist/ModernMidiPlayer-Setup-<version>.exe
```

The build script downloads the Python runtime and FluidSynth, stages them with the app, draws the icon and installer artwork, and compiles `installer/launcher.nsi` and `installer/installer.nsi`.

A PyInstaller alternative is also included: `python build_exe.py` builds `dist/ModernMidiPlayer/`.

## Updates

**For users:** open **Help ▸ Check for updates…**. If there's a newer version, you'll see what changed. Click **Update now** and the player downloads the new installer, checks it isn't damaged, closes, installs and restarts. Your settings, playlist and add-on choices are kept. The player also checks by itself when it starts (at most twice a day). You can turn that off under **Help ▸ Check for updates automatically**, or pick **Skip this version**.
You don't need a server for **Help ▸ Install update from file…**: pick a `ModernMidiPlayer-Setup-x.y.z.exe` you downloaded.

**For you (publishing an update)**, after a one-time setup:

1. Put this folder in a GitHub repository and push it. The workflow in `.github/workflows/release.yml` is included.
2. Each time you want to ship a new version, run:

   ```bash
   python tools/release.py 1.2.0 "What's new line 1" "What's new line 2"
   ```

   That script bumps the version, adds the notes to `CHANGELOG.md`, commits, tags `v1.2.0` and pushes. GitHub Actions then runs the tests, builds `ModernMidiPlayer-Setup-1.2.0.exe` with a `.sha256` checksum, and publishes a GitHub Release.
3. Every installed copy offers the update the next time it starts.

Installers built by the workflow point at your repository automatically. For a copy installed some other way, set the repository once under **Help ▸ Update source…**, or build with `python installer/build_installer.py --update-repo owner/repo`.

