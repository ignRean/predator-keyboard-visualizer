"""Synthetic test signals shared by the tests."""
import numpy as np

SR = 48000


def kick_track(seconds=8.0, bpm=120, hats=True, pad=True, kick_gain=0.8, seed=1):
    """Returns (signal, kick_onset_times)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    sig = np.zeros(n, np.float32)
    if pad:
        sig += 0.08 * np.sin(2 * np.pi * 330 * t) + 0.06 * np.sin(2 * np.pi * 523 * t)
    period = 60.0 / bpm
    onsets = np.arange(0.5, seconds - 0.3, period)
    kl = int(0.25 * SR)
    kt = np.arange(kl) / SR
    freq = 50 + 90 * np.exp(-kt * 30)            # pitch drop, like a real kick
    kick = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-kt * 14) * kick_gain
    for o in onsets:
        i = int(o * SR)
        sig[i:i + kl] += kick[: n - i]
    if hats:
        hl = int(0.04 * SR)
        hat = rng.standard_normal(hl) * np.exp(-np.arange(hl) / SR * 120) * 0.12
        hat = np.diff(hat, prepend=0)  # crude high-pass
        for o in np.arange(0.5 + period / 2, seconds - 0.1, period):
            i = int(o * SR)
            sig[i:i + hl] += hat[: n - i]
    return sig.astype(np.float32), onsets


def run_frames(analyzer, sig, fps=60, **kw):
    """Feeds the signal through the analyzer the way the engine does. Yields (time, frame)."""
    hop = SR // fps
    dt = 1.0 / fps
    for end in range(analyzer.fft_size, len(sig), hop):
        now = end / SR
        yield now, analyzer.process(sig[:end], dt, now=now, **kw)
