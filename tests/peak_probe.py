"""
peak_probe.py - How bright do the lights actually get on the music playing right now?

Runs the analyzer on live audio for N seconds using your saved settings (read-only,
no keyboard writes) and reports, per zone:
  * analyzer level peaks (0..1)
  * how often a zone is near full brightness
  * what the keyboard would actually receive, given its ~15 updates/s pacing
Also reruns the same audio at other settings so you can compare.

    python tests/peak_probe.py [seconds]
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer import Analyzer  # noqa: E402
from audio_capture import AudioCapture  # noqa: E402
from settings import SettingsStore  # noqa: E402

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
FPS, KB_HZ = 60, 15


def record(seconds):
    cap = AudioCapture("auto")
    cap.start()
    time.sleep(0.5)
    chunks, last_pos = [], None
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        time.sleep(0.05)
        s = cap.latest(2400)  # 50 ms
        if s is not None:
            chunks.append(s)
    rate, dev = cap.sample_rate, cap.device_name
    cap.stop()
    return (np.concatenate(chunks) if chunks else np.zeros(0, np.float32)), rate, dev


def analyze(sig, rate, **kw):
    a = Analyzer(rate)
    hop = rate // FPS
    lv = []
    for end in range(a.fft_size, len(sig), hop):
        f = a.process(sig[:end][-a.fft_size:], 1 / FPS, now=end / rate, **kw)
        lv.append(f.levels.copy())
    lv = np.array(lv)
    sent = lv[:: FPS // KB_HZ]  # what a ~15/s writer samples
    return lv, sent


def report(name, lv, sent):
    if not len(lv):
        print("  no audio captured")
        return
    p99 = np.percentile(lv, 99, axis=0)
    near = (lv >= 0.9).mean(axis=0) * 100
    sent_max = sent.max(axis=0)
    sent_p95 = np.percentile(sent, 95, axis=0)
    print(f"  {name}")
    print(f"    analyzer 99th pct : {np.round(p99, 2)}")
    print(f"    % frames >= 90%   : {np.round(near, 1)}")
    print(f"    keyboard max/p95  : {np.round(sent_max, 2)} / {np.round(sent_p95, 2)}")
    print(f"    median level      : {np.round(np.median(lv, axis=0), 2)}")
    hits = []
    for z in range(lv.shape[1]):
        x = lv[:, z]
        pk = x[1:-1][(x[1:-1] > x[:-2]) & (x[1:-1] >= x[2:]) & (x[1:-1] > 0.3)]
        hits.append(f"n={len(pk)} med={np.median(pk) if len(pk) else 0:.2f} min={pk.min() if len(pk) else 0:.2f}")
    print(f"    per-hit peaks     : {' | '.join(hits)}")


if __name__ == "__main__":
    s = SettingsStore().snapshot()
    print(f"Saved settings: mode={s.mode} sensitivity={s.sensitivity} decay={s.decay_ms} "
          f"noise_gate={s.noise_gate} brightness={s.brightness}")
    sig, rate, dev = record(SECONDS)
    rms = float(np.sqrt(np.mean(sig ** 2))) if len(sig) else 0
    print(f"Recorded {len(sig) / rate:.1f}s from {dev}, rms={rms:.4f}\n")
    if rms < 1e-4:
        print("Nothing is playing - start some music and rerun.")
        sys.exit(1)
    for name, kw in [
        ("your settings", dict(sensitivity=s.sensitivity, decay_ms=s.decay_ms, noise_gate=s.noise_gate)),
        ("defaults (1.0x, 180 ms, 6%)", dict(sensitivity=1.0, decay_ms=180, noise_gate=0.06)),
    ]:
        report(name, *analyze(sig, rate, **kw))
