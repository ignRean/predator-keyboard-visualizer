"""
frame_pick_sim.py - How many hits actually reach the keyboard, depending on how frames are picked?

Feeds the same audio through the analyzer at 60 fps, then simulates:
  * the keyboard writer (which frames get sent, how often)
  * AcerLightingService applying updates on its own ~64 ms tick (latest write wins, random phase)
and counts how many real hits (peaks in the 60 fps levels) show up on the keyboard at >= 80%
of their true brightness within 150 ms.

    python tests/frame_pick_sim.py record 20      record 20 s of what's playing to tests/_rec.npy
    python tests/frame_pick_sim.py                compare strategies on the recording + a synthetic track
"""
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from analyzer import Analyzer  # noqa: E402

REC = os.path.join(HERE, "_rec.npy")
SR, FPS = 48000, 60
TICK_S = 0.064            # measured AcerLightingService push interval
HIT_MIN, SHOW_FRAC, SHOW_WINDOW = 0.5, 0.8, 0.150


def record(seconds):
    import pyaudiowpatch as pa
    from audio_capture import AudioCapture
    cap = AudioCapture("auto")
    dev = cap._resolve("auto")
    cap.p.terminate()
    p = pa.PyAudio()
    blocks = []

    def cb(data, *_):
        blocks.append(np.frombuffer(data, np.float32).reshape(-1, int(dev["maxInputChannels"])).mean(1))
        return (None, pa.paContinue)

    s = p.open(format=pa.paFloat32, channels=int(dev["maxInputChannels"]), rate=int(dev["defaultSampleRate"]),
               input=True, input_device_index=dev["index"], frames_per_buffer=480, stream_callback=cb)
    s.start_stream()
    time.sleep(seconds)
    s.stop_stream()
    s.close()
    p.terminate()
    sig = np.concatenate(blocks).astype(np.float32)
    np.save(REC, sig)
    print(f"recorded {len(sig) / SR:.1f}s from {dev['name']}, rms={np.sqrt(np.mean(sig ** 2)):.4f} -> {REC}")


def synthetic_dense(seconds=12):
    """128 BPM: kick on quarters, snare on 2 & 4, closed hats on 16ths, short sharp transients."""
    rng = np.random.default_rng(3)
    n = int(seconds * SR)
    sig = np.zeros(n, np.float32)
    beat = 60 / 128
    t = np.arange(int(0.2 * SR)) / SR
    kick = np.sin(2 * np.pi * np.cumsum(50 + 90 * np.exp(-t * 30)) / SR) * np.exp(-t * 16) * 0.8
    snare = (rng.standard_normal(len(t)) * 0.35 + np.sin(2 * np.pi * 200 * t) * 0.3) * np.exp(-t * 28)
    hl = int(0.03 * SR)
    hat = np.diff(rng.standard_normal(hl + 1)) * np.exp(-np.arange(hl) / SR * 160) * 0.15
    for k in range(int(seconds / beat)):
        i = int(k * beat * SR)
        sig[i:i + len(kick)] += kick[: n - i]
        if k % 2:
            sig[i:i + len(snare)] += snare[: n - i]
    for k in range(int(seconds / (beat / 4))):
        i = int(k * beat / 4 * SR)
        sig[i:i + hl] += hat[: n - i]
    return sig


def levels_for(sig, decay_ms=110, noise_gate=0.2, sensitivity=1.18):
    a = Analyzer(SR)
    hop = SR // FPS
    out = []
    for end in range(a.fft_size, len(sig), hop):
        f = a.process(sig[end - a.fft_size:end], 1 / FPS, now=end / SR,
                      sensitivity=sensitivity, decay_ms=decay_ms, noise_gate=noise_gate)
        out.append(f.levels.copy())
    return np.array(out)


def simulate(lv, send_hz, pick, seed=0):
    """Returns the brightness actually displayed on the keyboard at each 60 fps frame."""
    rng = np.random.default_rng(seed)
    n = len(lv)
    t = np.arange(n) / FPS
    interval = 1.0 / send_hz
    writes = []                    # (time, values)
    last_send = -1e9
    held = None
    for i in range(n):
        held = lv[i].copy() if held is None else np.maximum(held, lv[i])
        if t[i] - last_send >= interval - 1e-9:
            writes.append((t[i], held.copy() if pick == "peak" else lv[i].copy()))
            last_send = t[i]
            held = None
    # service ticks at a random phase; each tick shows the latest write received before it
    phase = rng.uniform(0, TICK_S)
    shown = np.zeros_like(lv)
    cur = np.zeros(lv.shape[1])
    wi, applied, ticks_with_new = 0, 0, 0
    next_tick = phase
    for i in range(n):
        while next_tick <= t[i]:
            got = False
            while wi < len(writes) and writes[wi][0] <= next_tick:
                cur = writes[wi][1]
                wi += 1
                applied += 1
                got = True
            ticks_with_new += got
            next_tick += TICK_S
        shown[i] = cur
    lost = len(writes) - ticks_with_new  # writes overwritten before a tick picked them up
    return shown, len(writes), lost


def hits_in(lv):
    """True hits: local maxima above HIT_MIN in the 60 fps levels, per zone."""
    out = []
    for z in range(lv.shape[1]):
        x = lv[:, z]
        idx = np.where((x[1:-1] > x[:-2]) & (x[1:-1] >= x[2:]) & (x[1:-1] >= HIT_MIN))[0] + 1
        out.append(idx)
    return out


def score(lv, shown):
    w = int(SHOW_WINDOW * FPS)
    res = []
    for z, idx in enumerate(hits_in(lv)):
        ok = [shown[i:i + w + 1, z].max() >= SHOW_FRAC * lv[i, z] for i in idx]
        caught = [shown[i:i + w + 1, z].max() / lv[i, z] for i in idx]
        res.append((len(idx), 100 * np.mean(ok) if ok else 100.0, np.median(caught) if caught else 1.0))
    return res


def compare(name, lv):
    secs = len(lv) / FPS
    print(f"\n{name}  ({secs:.0f}s, hits per zone: {[len(h) for h in hits_in(lv)]})")
    print(f"  {'strategy':<34}{'hits shown (per zone)':<30}{'brightness caught':<26}{'writes/s':>9}{'lost':>6}")
    for label, hz, pick in [("current: newest frame, 15/s", 15, "newest"),
                            ("newest frame, 12.5/s", 12.5, "newest"),
                            ("peak of group, 15/s", 15, "peak"),
                            ("peak of group, 12.5/s", 12.5, "peak")]:
        shown_pct, caught, lost_tot, writes_tot = [], [], 0, 0
        for seed in range(8):  # average over random service-tick phases
            shown, nw, lost = simulate(lv, hz, pick, seed)
            sc = score(lv, shown)
            shown_pct.append([s[1] for s in sc])
            caught.append([s[2] for s in sc])
            lost_tot += lost
            writes_tot += nw
        sp = np.mean(shown_pct, axis=0)
        cg = np.mean(caught, axis=0)
        print(f"  {label:<34}{str([f'{v:.0f}%' for v in sp]):<30}{str([f'{v:.2f}' for v in cg]):<26}"
              f"{writes_tot / 8 / secs:>9.1f}{100 * lost_tot / max(writes_tot, 1):>5.0f}%")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "record":
        record(float(sys.argv[2]) if len(sys.argv) > 2 else 20)
        sys.exit()
    if os.path.exists(REC):
        compare("Your music (recording)", levels_for(np.load(REC)))
    compare("Synthetic 128 BPM with 16th-note hats", levels_for(synthetic_dense()))
