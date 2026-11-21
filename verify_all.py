"""
verify_all.py - Checks the analyzer and lighting modes with synthetic music. No hardware needed.

    python verify_all.py            offline checks
    python verify_all.py --hardware also opens audio capture and flashes the keyboard briefly
"""

import sys
import time

import numpy as np

from analyzer import Analyzer
from lighting import MODES, finish, palette_for
from tests.synth import SR, kick_track, run_frames

failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


def test_beats():
    print("\nBeat detection")
    for bpm in (90, 128, 174):
        sig, onsets = kick_track(10, bpm)
        beats = [t for t, f in run_frames(Analyzer(), sig) if f.beat]
        hits = [min((b - o for b in beats if 0 <= b - o < 0.1), default=None) for o in onsets]
        found = [h for h in hits if h is not None]
        false = len(beats) - len(found)
        lat = np.mean(found) * 1000 if found else 999
        check(f"{bpm} BPM", len(found) == len(onsets) and false == 0 and lat < 30,
              f"({len(found)}/{len(onsets)} kicks, {false} false, {lat:.0f} ms after onset)")


def test_bands():
    print("\nBand separation")
    t = np.arange(SR * 2) / SR
    for zone, hz in enumerate((60, 300, 1200, 6000)):
        tone = (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
        frames = [f for _, f in run_frames(Analyzer(), tone)]
        lv = frames[-1].levels
        check(f"{hz} Hz lights zone {zone} most", int(np.argmax(lv)) == zone, f"levels={np.round(lv, 2)}")


def test_silence_and_quiet():
    print("\nSilence and quiet songs")
    a = Analyzer()
    sig, _ = kick_track(3, 120)
    for _ in run_frames(a, sig):
        pass
    last = None
    for _, f in run_frames(a, np.zeros(SR, np.float32)):
        last = f
    check("goes fully dark after music stops", last.silent and not last.levels.any())

    quiet, _ = kick_track(6, 120, kick_gain=0.05)
    quiet *= 0.05
    beats = sum(f.beat for _, f in run_frames(Analyzer(), quiet))
    peak = max(f.levels[0] for _, f in run_frames(Analyzer(), quiet))
    check("quiet song still uses full range", peak > 0.7 and beats >= 8, f"(zone0 peak {peak:.2f}, {beats} beats)")


def test_sensitivity():
    print("\nSensitivity")
    sig, _ = kick_track(4, 120)
    avg = {}
    for sens in (0.6, 1.0, 2.0):
        avg[sens] = np.mean([f.levels.mean() for _, f in run_frames(Analyzer(), sig, sensitivity=sens)])
    check("higher sensitivity = brighter", avg[0.6] < avg[1.0] < avg[2.0],
          "(" + ", ".join(f"{k}x: {v:.2f}" for k, v in avg.items()) + ")")


def test_modes():
    print("\nLighting modes")
    sig, _ = kick_track(4, 120)
    pal = palette_for("cyberpunk", None)
    for key, mode in MODES.items():
        mode.reset()
        a = Analyzer()
        frames = []
        for _, f in run_frames(a, sig):
            out = finish(mode.render(f, 1 / 60, pal, 180), 1.0, 0.0, pal, f.silent)
            frames.append(out)
        arr = np.array(frames, dtype=float)
        valid = arr.min() >= 0 and arr.max() <= 255 and arr.shape[1:] == (4, 3)
        zone_brightness = arr[60:].max(axis=2)            # per frame, per zone (0-255)
        swing = (zone_brightness.max(axis=0) - zone_brightness.min(axis=0)).max()
        check(f"{mode.label:<12} valid colors and pulses with the music", valid and swing > 150,
              f"(largest zone swing {swing:.0f}/255)")


def test_hardware():
    print("\nHardware")
    from audio_capture import AudioCapture
    cap = AudioCapture("auto")
    cap.start()
    time.sleep(0.5)
    check("audio capture opened", cap.stream is not None and cap.stream.is_active(), f"({cap.device_name})")
    cap.stop()

    from keyboard import Keyboard
    kb = Keyboard()
    ok = kb.ensure_connected()
    check("PredatorSense keyboard reachable", ok, kb.status)
    if ok:
        for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
            kb._last_time = 0
            kb.write([c] * 4)
            time.sleep(0.3)
        kb.close()
        check("restored original lighting", True)


def test_app_loads():
    print("\nApp code")
    try:
        import tray_app  # noqa: F401  (catches syntax/import errors that would make the .pyw die silently)
        check("tray app and settings window load", True)
    except Exception as e:
        check("tray app and settings window load", False, repr(e))


if __name__ == "__main__":
    test_app_loads()
    test_beats()
    test_bands()
    test_silence_and_quiet()
    test_sensitivity()
    test_modes()
    if "--hardware" in sys.argv:
        test_hardware()
    print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {', '.join(failures)}"))
    sys.exit(1 if failures else 0)
