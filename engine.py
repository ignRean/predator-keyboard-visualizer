"""
engine.py - The real-time loop: audio -> analyzer -> lighting mode -> keyboard.

Runs on its own thread at 60 fps (30 in gaming mode). The UI never touches the
loop directly: it changes settings through SettingsStore and reads `engine.state`.
"""

import ctypes
import logging
import sys
import threading
import time

from analyzer import Analyzer
from audio_capture import AudioCapture
from audio_output import default_output_name, is_wave_link
from keyboard import Keyboard
from lighting import MODES, finish, led_contrast, palette_for

log = logging.getLogger("engine")

NORMAL_PRIORITY = 0x00000020
BELOW_NORMAL_PRIORITY = 0x00004000


def _set_priority(low):
    if sys.platform != "win32":
        return
    try:
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY if low else NORMAL_PRIORITY)
    except Exception:
        pass


class Engine:
    def __init__(self, store):
        self.store = store
        s = store.snapshot()
        self.audio = AudioCapture(s.device)
        self.keyboard = Keyboard(s.host, s.port)
        self.analyzer = Analyzer()
        self.state = {
            "levels": [0.0] * 4, "colors": [(0, 0, 0)] * 4, "beats": 0, "bpm": 0.0,
            "fps": 0.0, "kb_hz": 0.0, "device": "", "audio_status": "starting",
            "kb_status": "not connected", "silent": True,
            "output": "", "wave_link": False,
        }
        self._stop = threading.Event()
        self._thread = None
        self._mode_key = None
        store.on_change(self._on_settings)

    def start(self):
        if sys.platform == "win32":
            try:
                ctypes.windll.winmm.timeBeginPeriod(1)
            except Exception:
                pass
        _set_priority(self.store.snapshot().gaming)
        self.audio.start()
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._thread.start()
        threading.Thread(target=self._watch_output, name="output-watch", daemon=True).start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.keyboard.close()
        self.audio.stop()
        if sys.platform == "win32":
            try:
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass

    def _watch_output(self):
        """Track the Windows default playback device; Wave Link pops during keyboard updates."""
        while not self._stop.is_set():
            name = default_output_name()
            if name:
                self.state["output"] = name
                self.state["wave_link"] = is_wave_link(name)
            self._stop.wait(3.0)

    def _on_settings(self, s, changes):
        if "device" in changes:
            self.audio.set_device(s.device)
        if "gaming" in changes:
            _set_priority(s.gaming)
        if "enabled" in changes:
            # Keyboard calls happen on the engine thread; just flag it.
            self._pending_enable = s.enabled

    def _run(self):
        self._pending_enable = None
        prev = time.perf_counter()
        frames, writes, beats = 0, 0, 0
        stat_t = prev
        enabled = self.store.snapshot().enabled
        if not enabled:
            self.keyboard._paused = True

        while not self._stop.is_set():
            start = time.perf_counter()
            dt = min(start - prev, 0.1)
            prev = start
            s = self.store.snapshot()

            if self._pending_enable is not None:
                enabled, self._pending_enable = self._pending_enable, None
                (self.keyboard.resume if enabled else self.keyboard.pause)()

            fps = 30 if s.gaming else 60
            self.keyboard.max_hz = min(s.keyboard_hz, 8) if s.gaming else s.keyboard_hz

            self.analyzer.set_sample_rate(self.audio.sample_rate)
            frame = self.analyzer.process(
                self.audio.latest(self.analyzer.fft_size), dt,
                sensitivity=s.sensitivity, decay_ms=s.decay_ms, noise_gate=s.noise_gate,
            )

            mode = MODES.get(s.mode, MODES["spectrum"])
            if mode.key != self._mode_key:
                mode.reset()
                self._mode_key = mode.key
            palette = palette_for(s.theme, s.custom_colors, s.mode, s.solid_color)
            colors = finish(mode.render(frame, dt, palette, s.decay_ms), s.brightness, s.idle_glow,
                            palette, frame.silent)

            # The preview shows `colors` (monitors already handle perception); the keyboard
            # gets the LED-corrected version so dim moments actually look dim.
            if enabled and self.keyboard.write(led_contrast(colors, s.contrast)):
                writes += 1
            frames += 1
            beats += frame.beat

            st = self.state
            st["levels"] = [float(v) for v in frame.levels]
            st["colors"] = colors
            st["silent"] = frame.silent
            st["bpm"] = frame.bpm
            if frame.beat:
                st["beats"] += 1
            now = time.perf_counter()
            if now - stat_t >= 1.0:
                st["fps"] = frames / (now - stat_t)
                st["kb_hz"] = writes / (now - stat_t)
                frames = writes = 0
                stat_t = now
            st["device"] = self.audio.device_name
            st["audio_status"] = self.audio.status
            st["kb_status"] = self.keyboard.status if enabled else "paused - PredatorSense lighting restored"

            sleep = 1.0 / fps - (time.perf_counter() - start)
            if sleep > 0:
                time.sleep(sleep)
