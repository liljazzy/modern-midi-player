# Changelog

## 1.3.1
- **Effects come out with every note:** each note-on now makes its own burst the moment it plays, even very short notes and quick repeats of the same key (before, effects only noticed keys about 30 times a second, so they appeared in batches and some notes were missed). Short notes still light their key briefly.
- **No more silent stretches in busy music:** when the screen was full of particles, new notes were dropped. A new note now always gets its burst; the oldest particles make way.
- **Particle limit setting** in View > Piano effects (100 to 4000, default 900).
- **Particles leave in their own way:** when particles are cleared to make room they shimmer (stars), pop (bubbles, some sparks), burst (rockets) or fade, one after another on a slow-then-fast curve, instead of all vanishing at once.
- **Click-through particle area:** on the popped-out piano only the keys react to the mouse; the particle area above them lets clicks through to the window behind.
- **Faster everywhere:** the editor uses about a fifth of the CPU while playing a big song (the playhead repaint is 12x cheaper) and repaints faster when scrolling or zooming; the main window starts in about half the time (the sound output opens while the window is built); the mixer builds faster; changing song in a large playlist no longer stalls; song lengths in the playlist are worked out in smaller steps. Nothing looks different.
- **Fix:** a lightning bolt could reach above the top of the effect area.

## 1.3.0
- **Four new piano effects:** **Fireflies** (slow glowing dots that wander within a limited range and blink on and off), **Lightning** (several jagged, branching bolts per key), **Steam** (soft puffs in the key's own colour) and **Fireworks** (a rocket bursts into a pattern). Fireworks have a **Firework pattern** setting: Random, Peony, Ring, Heart, Star, Spiral, Double ring, Willow or Crackle.
- **New effect options** in View > Piano effects: **Held-note glow** (a soft light above a key while it is held), **Pitch-to-colour** (red for low notes through to violet for high notes, on the keys too), **Velocity scaling** (harder notes make more, bigger, faster effects) and **Trails**.
- **Glow strength slider:** 0x to 3x, for the particle glow, the held-note glow and the lightning glow.
- **Sparks** now include long tumbling rectangular pieces as well as round ones.
- **Faster effects:** painting is several times cheaper (steam, bubbles and lightning most of all), and if a computer still struggles the effects shed detail automatically (trails first, then fewer particles per note) and bring it back when there is time to spare. The glow is never dropped.
- **Fixes:** switching between effects while notes are playing could stall the animation; the glow and steam images could be cropped to hard-edged shapes on screens with display scaling above 100%.

## 1.2.0
- **MIDI input:** a new **Input** menu lets you choose a MIDI keyboard or controller. The default is **None**. What you play sounds through the current output, lights up the piano keys and sends up the piano effects. It respects muted channels, is remembered between runs (and falls back to None if the device isn't connected), and **Refresh devices** finds newly plugged-in keyboards. Needs python-rtmidi - the installer's "MIDI port support" option installs it.

## 1.1.9
- **Sparks pop:** about a third of the sparks pop part-way through their flight - a quick flash and a burst of small sparks.
- **Stars shimmer away:** a star now shimmers away (flickers, shrinks and sheds glitter) when it reaches the top of the effect area or at a random point in its life, instead of just fading.
- **Bubbles and confetti are 25% faster**, and there are **25% fewer bubbles**.
- **Taller effect area:** the effect height can now go up to 600 px (was 240). Confetti and embers scale up to fill a tall area; this also fixes confetti being flung out of very tall areas.
- **Resize handles match the piano keys:** the popped-out piano is resized by dragging its first or last key (each handle is one key wide and as tall as the keys). They are completely invisible until you hover over them, when the end key lights up.

## 1.1.8
- **Piano effects now come from the whole key:** particles start anywhere across the width of the key being played (a white key's full width, a black key's narrower width) instead of from a single point, so the effect scales with the size of the piano.
- **The popped-out piano can be resized from both sides.** Each side has a full-height handle (nearly invisible until you hover it); drag the left handle to widen it to the left or the right handle to widen it to the right - the opposite side stays where it is. The window keeps a minimum width, and its new size is remembered.

## 1.1.7
- **Piano effects:** the piano keyboard can show particle effects - Sparks, Embers, Bubbles, Confetti and Stars - each driven by simple physics (gravity, bouncing off the keys, bubbles that float up and pop, confetti that flutters down and lands, embers that rise and linger). Open **View ▸ Piano effects…** to choose the type and tune the amount, size, speed, lifetime, gravity, bounce, glow, colour (channel / rainbow / one colour) and height, with a live preview. **View ▸ Piano particles** switches them on or off.
- The effects are **off while the piano is docked** in the window and **on when it is popped out**. A setting lets you also show them while docked.
- **Popped-out piano is now its own transparent window** - no frame or background, just the keys and the particles - so it can be captured on its own (e.g. in OBS). Drag the keys to move it, drag its left / right edge to resize it, right-click for options, and it remembers where you left it.
- **Docking back:** double-click it, use the right-click menu, or drag it onto the top or bottom of the main window (a hint shows where it will go). The piano can be docked above the tabs or below the transport bar, and remembers the side.
- The docked piano has a **Pop out** button on its title bar.

## 1.1.6
- The popped-out piano is now a normal window titled "Modern MIDI Player - Piano", so screen recorders such as OBS can capture it on its own (Window Capture). It still stays on top.

## 1.1.5
- The popped-out piano stays on top of other windows.
- "Even out note volumes" no longer lifts very quiet ghost notes (velocity below 24), which made hidden notes in some files audible as stray quiet notes.
- Diagnostic: notes that start while nothing is playing are logged to %LOCALAPPDATA%\ModernMidiPlayer\idle-notes.log to help track down stray sounds.

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
