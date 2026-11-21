"""
settings.py - User settings, saved to %APPDATA%\\PredatorVisualizer\\settings.json.
"""

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field, fields

log = logging.getLogger("settings")

SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PredatorVisualizer")
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")


@dataclass
class Settings:
    enabled: bool = True
    mode: str = "spectrum"          # see lighting.MODES
    theme: str = "cyberpunk"        # see lighting.THEMES; "custom" uses custom_colors
    solid_color: str = "#FF0055"    # used by the One Color mode
    custom_colors: list = field(default_factory=lambda: ["#FF0055", "#00FFFF", "#FFFF00", "#AA00FF"])
    sensitivity: float = 1.0        # 0.5 .. 2.5; higher shows quieter detail
    decay_ms: float = 180.0         # 60 .. 600; how long light lingers after a hit
    noise_gate: float = 0.06        # 0 .. 0.3; levels below this go fully dark
    brightness: float = 1.0         # 0.1 .. 1.0
    idle_glow: float = 0.0          # 0 = pitch black when quiet; up to 0.3 for a faint glow
    contrast: float = 2.0           # 1.0 .. 3.0; LED perception correction, higher = darker between hits
    keyboard_hz: float = 15.0       # 4 .. 15 updates/s; the lighting service can't go faster than ~15.6
    gaming: bool = False            # low priority, 30 fps analysis, keyboard capped at 8 Hz
    device: str = "auto"
    host: str = "127.0.0.1"
    port: int = 6742


class SettingsStore:
    """Thread-safe holder. The UI calls update(); the engine reads snapshot() each frame."""

    def __init__(self, path=SETTINGS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._s = self._load()
        self._listeners = []

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            known = {f.name for f in fields(Settings)}
            s = Settings(**{k: v for k, v in data.items() if k in known})
            s.keyboard_hz = min(float(s.keyboard_hz), 15.0)
            return s
        except FileNotFoundError:
            return Settings()
        except Exception as e:
            log.warning("Settings file unreadable (%s); using defaults", e)
            return Settings()

    def snapshot(self):
        with self._lock:
            return Settings(**asdict(self._s))

    def update(self, **changes):
        with self._lock:
            for k, v in changes.items():
                if not hasattr(self._s, k):
                    raise AttributeError(k)
                setattr(self._s, k, v)
            snap = Settings(**asdict(self._s))
        self._schedule_save()
        for cb in list(self._listeners):
            try:
                cb(snap, changes)
            except Exception as e:
                log.warning("Settings listener failed: %s", e)

    def _schedule_save(self):
        """Slider drags fire many updates; write the file once things settle."""
        if getattr(self, "_timer", None):
            self._timer.cancel()
        self._timer = threading.Timer(0.6, self.save)
        self._timer.daemon = True
        self._timer.start()

    def on_change(self, cb):
        self._listeners.append(cb)

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(asdict(self.snapshot()), f, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            log.warning("Could not save settings: %s", e)
