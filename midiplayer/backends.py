"""MIDI output backends.

* FluidSynthBackend - software synth through libfluidsynth (ctypes) + a .sf2
* WinMMBackend      - Windows built-in "Microsoft GS Wavetable Synth" or any
                      Windows MIDI out device, via winmm.dll (ctypes)
* RtMidiBackend     - any system MIDI output port (needs python-rtmidi)
* NullBackend       - silent, used when nothing else is available

All backends accept raw MIDI byte messages through ``send``.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import glob
import os
import sys
import threading
from dataclasses import dataclass
from typing import List, Optional


class BackendError(Exception):
    pass


@dataclass
class OutputInfo:
    kind: str      # "fluidsynth" | "winmm" | "rtmidi" | "null"
    ident: str     # device id / port name / soundfont path
    label: str


class Backend:
    kind = "null"
    name = "No output"

    def send(self, msg: bytes) -> None:
        pass

    def set_gain(self, gain: float) -> None:
        """Optional hardware/synth master gain. Default: no-op."""

    def reset(self) -> None:
        for ch in range(16):
            self.send(bytes([0xB0 | ch, 123, 0]))   # all notes off
            self.send(bytes([0xB0 | ch, 120, 0]))   # all sound off
            self.send(bytes([0xB0 | ch, 121, 0]))   # reset controllers

    def close(self) -> None:
        pass


class NullBackend(Backend):
    pass


class RecordingBackend(Backend):
    """Stores sent messages; handy for tests."""
    kind = "record"
    name = "Recorder"

    def __init__(self):
        self.messages: List[bytes] = []
        self.lock = threading.Lock()

    def send(self, msg: bytes) -> None:
        with self.lock:
            self.messages.append(bytes(msg))


# --------------------------------------------------------------- FluidSynth
_FLUID_NAMES = {
    "win32": ["libfluidsynth-3.dll", "libfluidsynth-2.dll", "fluidsynth.dll", "libfluidsynth.dll"],
    "darwin": ["libfluidsynth.3.dylib", "libfluidsynth.dylib", "libfluidsynth.2.dylib"],
}
_FLUID_DEFAULT = ["libfluidsynth.so.3", "libfluidsynth.so.2", "libfluidsynth.so"]


def _app_dirs() -> List[str]:
    """Folders where bundled resources may live (source tree or frozen app)."""
    if getattr(sys, "frozen", False):
        dirs = [os.path.dirname(sys.executable)]
        mei = getattr(sys, "_MEIPASS", None)
        if mei and mei not in dirs:
            dirs.append(mei)
        return dirs
    return [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]


_DLL_DIRS: list = []
_FLUID_CACHE: dict = {}


def find_fluidsynth(custom_path: str = "") -> Optional[str]:
    """Return a loadable path/name for libfluidsynth, or None."""
    candidates: List[str] = []
    if custom_path:
        if os.path.isdir(custom_path):
            for n in _FLUID_NAMES.get(sys.platform, _FLUID_DEFAULT):
                candidates.append(os.path.join(custom_path, n))
        else:
            candidates.append(custom_path)
    names = _FLUID_NAMES.get(sys.platform, _FLUID_DEFAULT)
    for base in _app_dirs():
        for sub in ("", "fluidsynth", os.path.join("fluidsynth", "bin"), "lib"):
            for n in names:
                candidates.append(os.path.join(base, sub, n))
    if sys.platform == "darwin":
        for prefix in ("/opt/homebrew/lib", "/usr/local/lib", "/opt/local/lib"):
            for n in names:
                candidates.append(os.path.join(prefix, n))
    if sys.platform == "win32":
        for prefix in (r"C:\tools\fluidsynth\bin", r"C:\Program Files\fluidsynth\bin",
                       r"C:\fluidsynth\bin", os.path.expanduser(r"~\fluidsynth\bin")):
            for n in names:
                candidates.append(os.path.join(prefix, n))
    candidates.extend(names)
    found = ctypes.util.find_library("fluidsynth") or ctypes.util.find_library("libfluidsynth-3")
    if found:
        candidates.append(found)
    for c in candidates:
        if os.path.isabs(c) and not os.path.exists(c):
            continue
        try:
            if sys.platform == "win32" and os.path.isabs(c):
                _DLL_DIRS.append(os.add_dll_directory(os.path.dirname(c)))  # deps next to the DLL
            ctypes.CDLL(c)
            return c
        except (OSError, AttributeError):
            continue
    return None


def find_soundfonts(extra_dirs: Optional[List[str]] = None) -> List[str]:
    dirs = list(extra_dirs or [])
    for base in _app_dirs():
        dirs += [os.path.join(base, "soundfonts"), base]
    home = os.path.expanduser("~")
    dirs += [os.path.join(home, "soundfonts"), os.path.join(home, "SoundFonts"),
             os.path.join(home, "Documents", "SoundFonts")]
    if sys.platform.startswith("linux"):
        dirs += ["/usr/share/sounds/sf2", "/usr/share/soundfonts", "/usr/local/share/soundfonts"]
    elif sys.platform == "darwin":
        dirs += ["/opt/homebrew/share/soundfonts", "/usr/local/share/soundfonts",
                 os.path.join(home, "Library", "Audio", "Sounds", "Banks")]
    elif sys.platform == "win32":
        dirs += [r"C:\soundfonts", r"C:\tools\fluidsynth\share\soundfonts"]
    out: List[str] = []
    for d in dirs:
        if os.path.isdir(d):
            for p in sorted(glob.glob(os.path.join(d, "*.sf2")) + glob.glob(os.path.join(d, "*.SF2"))):
                if p not in out:
                    out.append(p)
    # prefer the bigger, nicer General MIDI banks first
    pref = ["fluidr3", "generaluser", "musescore", "timgm", "default-gm"]
    out.sort(key=lambda p: next((i for i, k in enumerate(pref) if k in os.path.basename(p).lower()), len(pref)))
    return out


class FluidSynthBackend(Backend):
    kind = "fluidsynth"

    def __init__(self, soundfont: str, lib_path: str = "", audio_driver: str = "",
                 sample_rate: float = 44100.0, render_only: bool = False):
        lib = find_fluidsynth(lib_path)
        if not lib:
            raise BackendError("FluidSynth library not found")
        if not soundfont or not os.path.exists(soundfont):
            raise BackendError("No SoundFont (.sf2) selected")
        self.lib = ctypes.CDLL(lib)
        L = self.lib
        vp, ci, cd, cc = ctypes.c_void_p, ctypes.c_int, ctypes.c_double, ctypes.c_char_p
        L.new_fluid_settings.restype = vp
        L.new_fluid_synth.restype = vp
        L.new_fluid_synth.argtypes = [vp]
        L.new_fluid_audio_driver.restype = vp
        L.new_fluid_audio_driver.argtypes = [vp, vp]
        L.fluid_settings_setstr.argtypes = [vp, cc, cc]
        L.fluid_settings_setnum.argtypes = [vp, cc, cd]
        L.fluid_settings_setint.argtypes = [vp, cc, ci]
        L.fluid_synth_sfload.argtypes = [vp, cc, ci]
        for fn in ("fluid_synth_noteon", "fluid_synth_cc", "fluid_synth_key_pressure"):
            if hasattr(L, fn):
                getattr(L, fn).argtypes = [vp, ci, ci, ci]
        L.fluid_synth_noteoff.argtypes = [vp, ci, ci]
        L.fluid_synth_program_change.argtypes = [vp, ci, ci]
        L.fluid_synth_pitch_bend.argtypes = [vp, ci, ci]
        L.fluid_synth_channel_pressure.argtypes = [vp, ci, ci]
        L.fluid_synth_set_gain.argtypes = [vp, ctypes.c_float]
        L.fluid_synth_sysex.argtypes = [vp, cc, ci, cc, ctypes.POINTER(ci), ctypes.POINTER(ci), ci]
        L.fluid_synth_system_reset.argtypes = [vp]
        L.fluid_synth_all_notes_off.argtypes = [vp, ci]
        L.fluid_synth_all_sounds_off.argtypes = [vp, ci]
        L.delete_fluid_audio_driver.argtypes = [vp]
        L.delete_fluid_synth.argtypes = [vp]
        L.delete_fluid_settings.argtypes = [vp]
        L.fluid_synth_write_s16.argtypes = [vp, ci, vp, ci, ci, vp, ci, ci]

        self.settings = L.new_fluid_settings()
        L.fluid_settings_setnum(self.settings, b"synth.sample-rate", sample_rate)
        L.fluid_settings_setint(self.settings, b"synth.polyphony", 512)
        L.fluid_settings_setint(self.settings, b"synth.midi-channels", 16)
        L.fluid_settings_setnum(self.settings, b"synth.gain", 0.7)
        # Smaller buffers for low latency where supported
        L.fluid_settings_setint(self.settings, b"audio.period-size", 256)
        L.fluid_settings_setint(self.settings, b"audio.periods", 4)
        driver = audio_driver or {"win32": "wasapi", "darwin": "coreaudio"}.get(sys.platform, "pulseaudio")
        self.synth = L.new_fluid_synth(self.settings)
        if not self.synth:
            raise BackendError("Could not create FluidSynth synthesizer")
        sfid = L.fluid_synth_sfload(self.synth, os.fsencode(soundfont), 1)
        if sfid < 0:
            self._cleanup()
            raise BackendError(f"Could not load SoundFont: {soundfont}")
        self.soundfont = soundfont
        self.driver = None
        if not render_only:
            tried = [driver] + [d for d in ("wasapi", "dsound", "waveout", "coreaudio", "pulseaudio",
                                            "pipewire", "alsa", "jack", "sdl2", "portaudio") if d != driver]
            for d in tried:
                L.fluid_settings_setstr(self.settings, b"audio.driver", d.encode())
                self.driver = L.new_fluid_audio_driver(self.settings, self.synth)
                if self.driver:
                    self.driver_name = d
                    break
            if not self.driver:
                self._cleanup()
                raise BackendError("FluidSynth could not open an audio device")
        self.name = f"FluidSynth — {os.path.basename(soundfont)}"
        self._lock = threading.Lock()

    def send(self, msg: bytes) -> None:
        if not msg:
            return
        L, s = self.lib, self.synth
        st = msg[0]
        with self._lock:
            if st == 0xF0:
                body = bytes(msg[1:-1] if msg.endswith(b"\xF7") else msg[1:])
                L.fluid_synth_sysex(s, body, len(body), None, None, None, 0)
                return
            hi, ch = st & 0xF0, st & 0x0F
            if hi == 0x90:
                if msg[2]:
                    L.fluid_synth_noteon(s, ch, msg[1], msg[2])
                else:
                    L.fluid_synth_noteoff(s, ch, msg[1])
            elif hi == 0x80:
                L.fluid_synth_noteoff(s, ch, msg[1])
            elif hi == 0xB0:
                if msg[1] == 123:
                    L.fluid_synth_all_notes_off(s, ch)
                elif msg[1] == 120:
                    L.fluid_synth_all_sounds_off(s, ch)
                else:
                    L.fluid_synth_cc(s, ch, msg[1], msg[2])
            elif hi == 0xC0:
                L.fluid_synth_program_change(s, ch, msg[1])
            elif hi == 0xE0:
                L.fluid_synth_pitch_bend(s, ch, msg[1] | (msg[2] << 7))
            elif hi == 0xD0:
                L.fluid_synth_channel_pressure(s, ch, msg[1])
            elif hi == 0xA0 and hasattr(L, "fluid_synth_key_pressure"):
                L.fluid_synth_key_pressure(s, ch, msg[1], msg[2])

    def set_gain(self, gain: float) -> None:
        with self._lock:
            self.lib.fluid_synth_set_gain(self.synth, ctypes.c_float(max(0.0, min(2.0, gain * 0.7))))

    def reset(self) -> None:
        with self._lock:
            self.lib.fluid_synth_system_reset(self.synth)

    def render(self, frames: int) -> bytes:
        """Offline render (interleaved stereo s16) - used by tests/export."""
        buf = (ctypes.c_int16 * (frames * 2))()
        with self._lock:
            self.lib.fluid_synth_write_s16(self.synth, frames, buf, 0, 2, buf, 1, 2)
        return bytes(buf)

    def _cleanup(self):
        L = self.lib
        if getattr(self, "driver", None):
            L.delete_fluid_audio_driver(self.driver)
            self.driver = None
        if getattr(self, "synth", None):
            L.delete_fluid_synth(self.synth)
            self.synth = None
        if getattr(self, "settings", None):
            L.delete_fluid_settings(self.settings)
            self.settings = None

    def close(self) -> None:
        with self._lock:
            self._cleanup()


# ------------------------------------------------------------------- WinMM
class WinMMBackend(Backend):
    kind = "winmm"

    def __init__(self, device_id: int = -1):
        if sys.platform != "win32":
            raise BackendError("WinMM is only available on Windows")
        self.winmm = ctypes.WinDLL("winmm")
        w = self.winmm
        w.midiOutOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint, ctypes.c_void_p,
                                  ctypes.c_void_p, ctypes.c_uint]
        w.midiOutShortMsg.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        w.midiOutReset.argtypes = [ctypes.c_void_p]
        w.midiOutClose.argtypes = [ctypes.c_void_p]
        self.handle = ctypes.c_void_p()
        dev = ctypes.c_uint(0xFFFFFFFF if device_id < 0 else device_id)
        res = self.winmm.midiOutOpen(ctypes.byref(self.handle), dev, None, None, 0)
        if res != 0:
            raise BackendError(f"midiOutOpen failed (error {res})")
        names = dict(list_winmm_devices())
        self.name = names.get(device_id, "Windows MIDI Mapper") if device_id >= 0 else "Microsoft GS Wavetable Synth"
        self._lock = threading.Lock()

    def send(self, msg: bytes) -> None:
        if not msg or msg[0] == 0xF0:
            return  # SysEx not supported by the short-message API
        word = msg[0] | ((msg[1] if len(msg) > 1 else 0) << 8) | ((msg[2] if len(msg) > 2 else 0) << 16)
        with self._lock:
            if self.handle:
                self.winmm.midiOutShortMsg(self.handle, word)

    def reset(self) -> None:
        super().reset()
        with self._lock:
            if self.handle:
                self.winmm.midiOutReset(self.handle)

    def close(self) -> None:
        with self._lock:
            if self.handle:
                self.winmm.midiOutReset(self.handle)
                self.winmm.midiOutClose(self.handle)
                self.handle = ctypes.c_void_p()


def list_winmm_devices():
    if sys.platform != "win32":
        return []

    class MIDIOUTCAPSW(ctypes.Structure):
        _fields_ = [("wMid", ctypes.c_ushort), ("wPid", ctypes.c_ushort), ("vDriverVersion", ctypes.c_uint),
                    ("szPname", ctypes.c_wchar * 32), ("wTechnology", ctypes.c_ushort),
                    ("wVoices", ctypes.c_ushort), ("wNotes", ctypes.c_ushort),
                    ("wChannelMask", ctypes.c_ushort), ("dwSupport", ctypes.c_uint)]
    try:
        winmm = ctypes.WinDLL("winmm")
        n = winmm.midiOutGetNumDevs()
        out = []
        for i in range(n):
            caps = MIDIOUTCAPSW()
            if winmm.midiOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
                out.append((i, caps.szPname))
        return out
    except OSError:
        return []


# ------------------------------------------------------------------ rtmidi
def list_rtmidi_ports() -> List[str]:
    try:
        import rtmidi  # type: ignore
    except ImportError:
        return []
    try:
        m = rtmidi.MidiOut()
        ports = m.get_ports()
        del m
        return ports
    except Exception:
        return []


class RtMidiBackend(Backend):
    kind = "rtmidi"

    def __init__(self, port_name: str):
        try:
            import rtmidi  # type: ignore
        except ImportError as e:
            raise BackendError("python-rtmidi is not installed") from e
        self.out = rtmidi.MidiOut()
        ports = self.out.get_ports()
        if port_name not in ports:
            raise BackendError(f"MIDI port not found: {port_name}")
        self.out.open_port(ports.index(port_name))
        self.name = port_name
        self._lock = threading.Lock()

    def send(self, msg: bytes) -> None:
        with self._lock:
            if self.out:
                self.out.send_message(list(msg))

    def close(self) -> None:
        with self._lock:
            if self.out:
                self.out.close_port()
                self.out = None


# ------------------------------------------------------------------ helpers
def available_outputs(soundfonts: List[str], fluid_lib: str = "") -> List[OutputInfo]:
    outs: List[OutputInfo] = []
    if find_fluidsynth(fluid_lib):
        for sf in soundfonts:
            outs.append(OutputInfo("fluidsynth", sf, f"FluidSynth — {os.path.basename(sf)}"))
    if sys.platform == "win32":
        outs.append(OutputInfo("winmm", "-1", "Microsoft GS Wavetable Synth (MIDI Mapper)"))
        for i, name in list_winmm_devices():
            outs.append(OutputInfo("winmm", str(i), f"Windows MIDI: {name}"))
    for p in list_rtmidi_ports():
        outs.append(OutputInfo("rtmidi", p, f"MIDI port: {p}"))
    outs.append(OutputInfo("null", "", "No output (silent)"))
    return outs


def open_output(info: OutputInfo, fluid_lib: str = "") -> Backend:
    if info.kind == "fluidsynth":
        return FluidSynthBackend(info.ident, fluid_lib)
    if info.kind == "winmm":
        return WinMMBackend(int(info.ident))
    if info.kind == "rtmidi":
        return RtMidiBackend(info.ident)
    return NullBackend()
