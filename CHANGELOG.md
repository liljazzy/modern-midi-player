# Changelog

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
