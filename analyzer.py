"""
analyzer.py - Turns raw audio into what the lighting needs: 4 band levels and beats.

* Band levels are measured in dB against a per-band peak that slowly relaxes,
  so loud and quiet songs both use the full brightness range, and the
  sensitivity setting really changes how much dynamic range is shown.
* Beats come from spectral flux in the kick range (40-180 Hz): a sudden jump
  in bass energy above an adaptive threshold.
* All smoothing is time-based (milliseconds), so it behaves the same at any
  frame rate.
"""

from dataclasses import dataclass, field
import time

import numpy as np

BANDS = [
    (30, 160),      # Zone 0: sub-bass, kicks
    (160, 600),     # Zone 1: bass, snare body
    (600, 2500),    # Zone 2: vocals, leads
    (2500, 14000),  # Zone 3: hats, cymbals, air
]
BEAT_BAND = (40, 180)

SILENCE_DB = -75.0      # overall level below this counts as silence
PEAK_FALL_DB_S = 4.0    # how fast the per-band reference relaxes after loud parts
MIN_PEAK_DB = -60.0     # reference never drops below this, so hiss isn't blown up
ATTACK_MS = 12.0
BEAT_REFRACTORY_S = 0.14
CROSS_BAND_DB = 30.0    # bands this far below the loudest band stay dark
AVG_MS = 1200.0         # time constant of the running average used for "dynamic" level
HIT_WINDOW_S = 3.0      # how far back "typical hits" are measured
HIT_PERCENTILE = 92     # this percentile of recent levels is treated as a full-brightness hit
HIT_FLOOR = 0.45        # max boost = 1 / HIT_FLOOR (about 2.2x)
HIT_CONTRAST = 0.3      # hits must stand this far above a band's typical level to reach full


@dataclass
class Frame:
    levels: np.ndarray = field(default_factory=lambda: np.zeros(4, np.float32))  # smoothed 0..1
    beat: bool = False
    beat_strength: float = 0.0   # 0..1, only meaningful when beat is True
    loudness: float = 0.0        # smoothed overall 0..1
    silent: bool = True
    bpm: float = 0.0


def _ease(dt, tau_ms):
    """Fraction to move toward a target this frame for a time constant in ms."""
    return 1.0 - np.exp(-dt * 1000.0 / max(tau_ms, 1.0))


class Analyzer:
    def __init__(self, sample_rate=48000, fft_size=1024):
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.window = np.hanning(fft_size).astype(np.float32)
        freqs = np.fft.rfftfreq(fft_size, 1.0 / sample_rate)
        self.masks = [(freqs >= lo) & (freqs < hi) for lo, hi in BANDS]
        self.beat_mask = (freqs >= BEAT_BAND[0]) & (freqs < BEAT_BAND[1])

        self.peak_db = np.full(4, MIN_PEAK_DB, np.float32)
        self.levels = np.zeros(4, np.float32)
        self.avg_db = None
        self.loudness = 0.0
        self._hist = []               # (time, raw levels) for hit normalization

        self._bass_hist = []          # recent bass dB values (for flux)
        self._flux_hist = []          # recent flux values (for threshold)
        self._last_beat = 0.0
        self._beat_times = []

    def set_sample_rate(self, sample_rate):
        if sample_rate != self.sample_rate:
            self.__init__(sample_rate, self.fft_size)

    def process(self, samples, dt, sensitivity=1.0, decay_ms=180.0, noise_gate=0.06, now=None):
        now = time.perf_counter() if now is None else now
        frame = Frame()

        raw = np.zeros(4, np.float32)
        if samples is not None and len(samples) >= self.fft_size:
            x = samples[-self.fft_size:] * self.window
            spec = np.abs(np.fft.rfft(x)) / (self.fft_size / 4.0)
            power = spec * spec
            total_db = 10.0 * np.log10(power.sum() + 1e-12)
            frame.silent = total_db < SILENCE_DB
        else:
            power = None
            frame.silent = True

        if not frame.silent:
            band_db = np.array([10.0 * np.log10(power[m].sum() + 1e-12) for m in self.masks], np.float32)

            # Per-band reference: jumps up instantly, relaxes slowly.
            self.peak_db = np.maximum(self.peak_db - PEAK_FALL_DB_S * dt, MIN_PEAK_DB)
            self.peak_db = np.maximum(self.peak_db, band_db)
            # A band far below the loudest one is just spill-over; don't scale it up to full.
            ref_db = np.maximum(self.peak_db, self.peak_db.max() - CROSS_BAND_DB)

            # Running average per band, to tell hits apart from sustained sound.
            if self.avg_db is None:
                self.avg_db = band_db.copy()
            self.avg_db += (band_db - self.avg_db) * _ease(dt, AVG_MS)

            sens = float(np.clip(sensitivity, 0.3, 3.0))
            # How loud, relative to this band's recent peak (sensitivity widens the dB window).
            absolute = np.clip(1.0 + (band_db - ref_db) / (24.0 * sens), 0.0, 1.0)
            # How much louder than the last second or so: sustained ~0.5, hits -> 1.
            dynamic = np.clip(0.5 + (band_db - self.avg_db) / (12.0 * sens), 0.0, 1.0)
            raw = absolute * (0.45 + 0.55 * dynamic)
            raw = raw ** 1.6  # contrast: hits stand out from the body of the mix

            gate = float(np.clip(noise_gate, 0.0, 0.5))
            if gate > 0:
                raw = np.where(raw < gate, 0.0, (raw - gate) / (1.0 - gate)).astype(np.float32)

            # Hit normalization: scale each band so its typical strong hits (92nd percentile
            # of the last few seconds) reach full brightness. The floor caps the boost so
            # faint bands and spill-over are never blown up to full.
            self._hist.append((now, raw.copy()))
            while self._hist and now - self._hist[0][0] > HIT_WINDOW_S:
                self._hist.pop(0)
            if len(self._hist) >= 20:
                hist = np.array([r for _, r in self._hist])
                hits = np.percentile(hist, HIT_PERCENTILE, axis=0)
                typical = np.median(hist, axis=0)
                # A steady held note has hits == typical; requiring hits to clear the typical
                # level by HIT_CONTRAST keeps sustained sound below full and lets real hits peak.
                ref = np.maximum(np.maximum(hits, typical + HIT_CONTRAST), HIT_FLOOR)
                raw = np.clip(raw / ref, 0.0, 1.0).astype(np.float32)

            self._detect_beat(power, frame, now)

        # Time-based envelope: fast attack, user-controlled release.
        up = _ease(dt, ATTACK_MS)
        down = _ease(dt, decay_ms)
        rate = np.where(raw > self.levels, up, down)
        self.levels += (raw - self.levels) * rate
        self.levels[self.levels < 0.01] = 0.0

        target_loud = float(np.dot(raw, [0.4, 0.3, 0.2, 0.1]))
        k = up if target_loud > self.loudness else _ease(dt, decay_ms * 2)
        self.loudness += (target_loud - self.loudness) * k

        frame.levels = self.levels.copy()
        frame.loudness = self.loudness
        frame.bpm = self._bpm()
        return frame

    def _detect_beat(self, power, frame, now):
        bass_db = 10.0 * np.log10(power[self.beat_mask].sum() + 1e-12)
        hist = self._bass_hist
        flux = max(0.0, bass_db - min(hist)) if hist else 0.0
        hist.append(bass_db)
        if len(hist) > 3:
            hist.pop(0)

        fh = self._flux_hist
        fh.append(flux)
        if len(fh) > 90:  # ~1.5 s at 60 fps
            fh.pop(0)
        if len(fh) < 10:
            return

        arr = np.asarray(fh)
        threshold = max(3.0, float(arr.mean() + 1.5 * arr.std()))
        loud_enough = bass_db > self.peak_db[0] - 18.0
        if flux > threshold and loud_enough and now - self._last_beat > BEAT_REFRACTORY_S:
            self._last_beat = now
            frame.beat = True
            frame.beat_strength = float(np.clip(0.55 + 0.45 * (flux - threshold) / threshold, 0.55, 1.0))
            self._beat_times.append(now)
            if len(self._beat_times) > 16:
                self._beat_times.pop(0)

    def _bpm(self):
        t = self._beat_times
        if len(t) < 6:
            return 0.0
        gaps = np.diff(t[-8:])
        gaps = gaps[(gaps > 0.25) & (gaps < 1.5)]
        return float(60.0 / np.median(gaps)) if len(gaps) >= 3 else 0.0
