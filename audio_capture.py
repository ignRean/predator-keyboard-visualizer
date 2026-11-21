"""
audio_capture.py - Low-latency WASAPI loopback capture.

PortAudio pushes small 10 ms blocks into a callback, which copies them into a
ring buffer. The engine reads the newest N samples whenever it wants, so there
is no polling, no backlog and no bursty 85 ms delivery.

A watchdog thread reopens the stream if the device disappears and, in "auto"
mode, moves to whichever output endpoint is actually carrying sound.
"""

import logging
import threading
import time

import numpy as np
import pyaudiowpatch as pyaudio

log = logging.getLogger("audio")

BLOCK_FRAMES = 480          # 10 ms at 48 kHz
RING_FRAMES = 8192          # ~170 ms of history
STALE_AFTER = 0.12          # no callback for this long = nothing is playing
SEEK_SILENCE_SECONDS = 6.0  # in auto mode, look for another endpoint after this much silence
SEEK_INTERVAL = 15.0        # ...but don't scan more often than this


def list_loopback_devices():
    """Returns [{'index', 'name', 'rate', 'channels'}] for every WASAPI loopback endpoint."""
    p = pyaudio.PyAudio()
    try:
        return [
            {
                "index": d["index"],
                "name": d["name"].replace(" [Loopback]", ""),
                "rate": int(d["defaultSampleRate"]),
                "channels": int(d["maxInputChannels"]),
            }
            for d in p.get_loopback_device_info_generator()
        ]
    finally:
        p.terminate()


class AudioCapture:
    def __init__(self, device="auto"):
        self.device_spec = device or "auto"
        self.p = pyaudio.PyAudio()
        self.stream = None
        self.device_info = None
        self.sample_rate = 48000
        self.channels = 2

        self._ring = np.zeros(RING_FRAMES, dtype=np.float32)
        self._pos = 0
        self._lock = threading.Lock()
        self.last_block_time = 0.0
        self.last_signal_time = time.perf_counter()

        self._stop = threading.Event()
        self._reopen = threading.Event()
        self._watchdog = None
        self.status = "starting"

    # ------------------------------------------------------------------ public

    @property
    def device_name(self):
        return self.device_info["name"].replace(" [Loopback]", "") if self.device_info else "none"

    def start(self):
        self._open(self._resolve(self.device_spec))
        self._watchdog = threading.Thread(target=self._watchdog_loop, name="audio-watchdog", daemon=True)
        self._watchdog.start()

    def set_device(self, spec):
        """Switch device from any thread; the watchdog performs the reopen."""
        self.device_spec = spec or "auto"
        self._reopen.set()

    def latest(self, n):
        """Newest n mono samples, or None if nothing has played recently."""
        if time.perf_counter() - self.last_block_time > STALE_AFTER:
            return None
        with self._lock:
            end = self._pos
            if end >= n:
                return self._ring[end - n:end].copy()
            return np.concatenate((self._ring[end - n:], self._ring[:end]))

    def stop(self):
        self._stop.set()
        if self._watchdog:
            self._watchdog.join(timeout=1.0)
        self._close()
        try:
            self.p.terminate()
        except Exception:
            pass

    # ---------------------------------------------------------------- internals

    def _callback(self, in_data, frame_count, time_info, status):
        data = np.frombuffer(in_data, dtype=np.float32)
        if self.channels > 1:
            data = data.reshape(-1, self.channels).mean(axis=1)
        n = len(data)
        with self._lock:
            end = self._pos + n
            if end <= RING_FRAMES:
                self._ring[self._pos:end] = data
            else:
                k = RING_FRAMES - self._pos
                self._ring[self._pos:] = data[:k]
                self._ring[:n - k] = data[k:]
            self._pos = end % RING_FRAMES
        now = time.perf_counter()
        self.last_block_time = now
        if n and float(np.abs(data).max()) > 1e-4:
            self.last_signal_time = now
        return (None, pyaudio.paContinue)

    def _loopbacks(self):
        return list(self.p.get_loopback_device_info_generator())

    def _candidates(self):
        """Auto-mode preference order: physical Realtek outputs, then the Windows default."""
        devs = self._loopbacks()
        order = []
        for key in ("headphones", "speakers", ""):
            for d in devs:
                name = d["name"].lower()
                if "realtek" in name and key in name and d not in order:
                    order.append(d)
        try:
            default = self.p.get_default_wasapi_loopback()
            if default and all(default["index"] != d["index"] for d in order):
                order.append(default)
        except Exception:
            pass
        return order or devs

    def _resolve(self, spec):
        devs = self._loopbacks()
        if not devs:
            raise RuntimeError("No WASAPI loopback devices found.")
        if spec and str(spec).lower() != "auto":
            s = str(spec).strip().lower()
            for d in devs:
                if (s.isdigit() and d["index"] == int(s)) or s in d["name"].lower():
                    return d
            log.warning("Device %r not found, using auto", spec)
        cands = self._candidates()
        live = self._probe_for_signal(cands)
        return live or cands[0]

    def _probe_for_signal(self, devices, seconds=0.25):
        """Briefly listen on each device and return the first that is carrying audio."""
        for d in devices:
            got = {"sig": False}

            def cb(in_data, *_):
                if np.abs(np.frombuffer(in_data, dtype=np.float32)).max(initial=0) > 1e-4:
                    got["sig"] = True
                return (None, pyaudio.paContinue)

            try:
                s = self.p.open(format=pyaudio.paFloat32, channels=int(d["maxInputChannels"]),
                                rate=int(d["defaultSampleRate"]), input=True,
                                input_device_index=d["index"], frames_per_buffer=BLOCK_FRAMES,
                                stream_callback=cb)
                time.sleep(seconds)
                s.stop_stream()
                s.close()
            except Exception:
                continue
            if got["sig"]:
                return d
        return None

    def _open(self, dev):
        self._close()
        self.device_info = dev
        self.sample_rate = int(dev["defaultSampleRate"])
        self.channels = int(dev["maxInputChannels"])
        self.stream = self.p.open(
            format=pyaudio.paFloat32,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=dev["index"],
            frames_per_buffer=BLOCK_FRAMES,
            stream_callback=self._callback,
        )
        self.stream.start_stream()
        self.last_signal_time = time.perf_counter()
        self.status = "listening"
        log.info("Capturing from %s (%d Hz, %d ch)", self.device_name, self.sample_rate, self.channels)

    def _close(self):
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _watchdog_loop(self):
        last_seek = time.perf_counter()
        while not self._stop.wait(0.5):
            try:
                if self._reopen.is_set():
                    self._reopen.clear()
                    self._open(self._resolve(self.device_spec))
                    continue

                if self.stream is None or not self.stream.is_active():
                    self.status = "reconnecting"
                    self._open(self._resolve(self.device_spec))
                    continue

                now = time.perf_counter()
                silent_for = now - self.last_signal_time
                if (str(self.device_spec).lower() == "auto"
                        and silent_for > SEEK_SILENCE_SECONDS
                        and now - last_seek > SEEK_INTERVAL):
                    last_seek = now
                    others = [d for d in self._candidates() if d["index"] != self.device_info["index"]]
                    live = self._probe_for_signal(others)
                    if live:
                        log.info("Sound found on %s, switching", live["name"])
                        self._open(live)
            except Exception as e:
                self.status = f"error: {e}"
                log.warning("Audio watchdog: %s", e)
                self._close()
                time.sleep(2.0)
