"""
keyboard.py - Writes zone colors to the Acer EC keyboard through PredatorSense's OpenRGB server.

Key points learned on the real hardware:
* set_colors() only queues a TCP packet (~0.3 ms); it never blocks on the EC.
* By default openrgb-python also asks the server for the full device state after
  every write. We pass fast=True so each frame is exactly one small packet and
  PredatorSense isn't made to read the EC back constantly.
* Measured on the PHN16-71 (see docs/conclusions/keyboard_pops_investigation.md):
  any write wakes AcerLightingService, which then pushes to the keyboard on a fixed
  ~64 ms tick for ~0.45 s. Every tick freezes all CPU cores for ~6 ms (firmware SMI),
  which is what makes audio pop. Writes faster than the tick are merged, so:
    - never write faster than ~15/s (it buys nothing),
    - never write when nothing visibly changed (no keepalive rewrites),
    - when the lights are steady or dark, send nothing at all -> zero freezes.
* Direct mode defaults to hardware brightness 50/100; we raise it to 100.
"""

import logging
import time

from openrgb import OpenRGBClient
from openrgb.utils import DeviceType, RGBColor

log = logging.getLogger("keyboard")

RETRY_SECONDS = 3.0
SERVICE_TICK_HZ = 15.6   # AcerLightingService pushes to the keyboard every ~64 ms
MIN_CHANGE = 10           # largest single-channel change (0-255) worth waking the service for


class Keyboard:
    def __init__(self, host="127.0.0.1", port=6742):
        self.host, self.port = host, port
        self.client = None
        self.dev = None
        self.original = None        # (mode, colors) captured the first time we connect
        self.status = "not connected"
        self.max_hz = SERVICE_TICK_HZ
        self.writes = 0
        self._last = None
        self._held = None           # brightest color per zone since the last send
        self._last_time = 0.0
        self._next_retry = 0.0
        self._paused = False

    @property
    def connected(self):
        return self.dev is not None

    def ensure_connected(self):
        if self.dev is not None:
            return True
        now = time.perf_counter()
        if now < self._next_retry:
            return False
        self._next_retry = now + RETRY_SECONDS
        try:
            self.client = OpenRGBClient(self.host, self.port, name="PredatorAudioVisualizer")
            dev = next((d for d in self.client.devices if "acereckeyboard" in d.name.lower()), None)
            dev = dev or next((d for d in self.client.devices if d.type == DeviceType.KEYBOARD), None)
            if dev is None:
                raise RuntimeError("PredatorSense is running but no keyboard was listed")
            if self.original is None:
                self.original = (dev.active_mode, list(dev.colors))
            self._enter_direct(dev)
            self.dev = dev
            self._last = None
            self.status = f"connected: {dev.name}"
            log.info("Connected to %s", dev.name)
            return True
        except Exception as e:
            self._drop()
            self.status = "PredatorSense not reachable - is it running with lighting on?"
            log.debug("Keyboard connect failed: %s", e)
            return False

    def write(self, colors):
        """
        colors: 4 (r, g, b) ints, called every engine frame. Returns True if a packet was sent.

        Peak-of-group: the keyboard can only show ~15 updates/s, but the engine runs at 60 fps.
        Instead of sending whichever frame happens to be newest, each zone keeps the brightest
        color seen since the last send, so short hits (hats, snares) between sends still show.
        """
        if self._paused or not self.ensure_connected():
            self._held = None
            return False
        if self._held is None:
            self._held = [tuple(c) for c in colors]
        else:
            self._held = [tuple(c) if max(c) >= max(h) else h for c, h in zip(colors, self._held)]
        now = time.perf_counter()
        if now - self._last_time < 1.0 / min(self.max_hz, SERVICE_TICK_HZ):
            return False
        colors, self._held = self._held, None
        if self._last is not None:
            biggest = max(abs(a - b) for ca, cb in zip(colors, self._last) for a, b in zip(ca, cb))
            going_dark = all(max(c) == 0 for c in colors) and any(max(c) > 0 for c in self._last)
            if biggest < MIN_CHANGE and not going_dark:
                return False  # invisible change: don't wake the lighting service
        try:
            self.dev.set_colors([RGBColor(*c) for c in colors[:len(self.dev.leds)]], fast=True)
        except Exception as e:
            log.warning("Keyboard write failed, reconnecting: %s", e)
            self._drop()
            self.status = "lost connection to PredatorSense, retrying..."
            return False
        self._last = [tuple(c) for c in colors]
        self._last_time = now
        self.writes += 1
        return True

    def pause(self):
        """Hand the keyboard back to PredatorSense's own lighting."""
        self._paused = True
        self._restore()

    def resume(self):
        self._paused = False
        if self.dev is not None:
            try:
                self._enter_direct(self.dev)
            except Exception:
                self._drop()
        self._last = None

    def close(self):
        self._restore()
        if self.client is not None:
            try:
                self.client.disconnect()
            except Exception:
                pass
        self._drop()

    def _restore(self):
        if self.dev is None or self.original is None:
            return
        mode, colors = self.original
        try:
            self.dev.set_colors(colors, fast=True)
            self.dev.set_mode(mode)
            log.info("Restored original keyboard lighting")
        except Exception as e:
            log.warning("Could not restore keyboard lighting: %s", e)

    @staticmethod
    def _enter_direct(dev):
        """Switch to 'Direct' (per-LED) mode at full hardware brightness."""
        mode = dev.modes[0]
        if getattr(mode, "brightness_max", None) and mode.brightness != mode.brightness_max:
            mode.brightness = mode.brightness_max
            dev.set_mode(mode)
        elif dev.active_mode != 0:
            dev.set_mode(0)

    def _drop(self):
        self.dev = None
        self.client = None
