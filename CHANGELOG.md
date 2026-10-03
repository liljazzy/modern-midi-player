# Changelog

## 1.1.4
- Piano keyboard that lights up the notes being played, coloured by MIDI channel. Show or hide it with **View ▸ Show piano** (Ctrl+K).
- The piano can be popped out into its own window (drag or double-click its title, or **View ▸ Pop piano out / dock it**) and docked back.

## 1.1.3
- New **Playback ▸ Even out note volumes** (on by default): narrows the gap between very loud and very quiet notes.
- Fixed notes that kept ringing after a song ended or was stopped/paused when the file leaves the sustain pedal down.

## 1.1.2
- Transport bar: emoji replaced with drawn icons, Play and Stop merged into one Play/Pause button (Stop is still in the Playback menu), and the Loop button now shows "Loop on" / "Loop off" with a highlighted state.
- Added a Help ▸ Project on GitHub link.
- Master volume is now applied inside the synthesizer: it is smooth, actually gets louder above 100% (up to about +7 dB), and no longer steps or clips.
- Fewer audio glitches: larger audio buffers (fewer crackles when the PC is busy) and finer timer resolution on Windows for steadier note timing.

## 1.1.1
- Fixed inconsistent master volume: boosting above 100% now raises the whole mix evenly instead of clipping loud channels.
- The installer detects an existing installation and updates it in place (no duplicate installs, shortcuts or Apps entries), including when the old copy is in a different folder.

## 1.1.0
- Windows installer (Setup.exe) with its own Python runtime, FluidSynth and a SoundFont. No administrator rights needed.
- One-click updates: **Help ▸ Check for updates…** downloads the new version, checks it, installs it and restarts the player.
- Automatic update check (at most twice a day, can be switched off in the Help menu), plus "Skip this version".
- **Help ▸ Install update from file…** installs a Setup.exe you downloaded yourself.

## 1.0.0
- First release: player with per-channel mute/solo/volume, 16-channel mixer, playlist, piano-roll editor, WAV export.
