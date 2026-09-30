"""Build a standalone app folder with PyInstaller:
    pip install pyinstaller
    python build_exe.py
Result: dist/ModernMidiPlayer/  (copy a 'fluidsynth' folder with the FluidSynth
DLLs next to the .exe for best sound; the Windows GS synth works without it)."""
import os
import PyInstaller.__main__

sep = ";" if os.name == "nt" else ":"
PyInstaller.__main__.run([
    "run.py", "--name", "ModernMidiPlayer", "--windowed", "--noconfirm",
    "--add-data", f"soundfonts{sep}soundfonts",
    "--add-data", f"demos{sep}demos",
])
