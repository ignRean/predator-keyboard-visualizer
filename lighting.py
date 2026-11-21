"""
lighting.py - Lighting modes and color themes.

Every mode turns an analyzer Frame into 4 zone colors as float RGB in 0..1.
The engine applies brightness, the idle glow and conversion to 0-255 afterwards,
so modes only care about how the music should look.
"""

import colorsys
import math

import numpy as np

THEMES = {
    "cyberpunk": ["#FF0064", "#00FFDC", "#FFDC00", "#A000FF"],
    "synthwave": ["#FF3C00", "#FF00A0", "#8C00FF", "#00C8FF"],
    "matrix":    ["#00FF46", "#00C828", "#00FF78", "#32FF96"],
    "ice":       ["#0050FF", "#00DCFF", "#B4F0FF", "#FFFFFF"],
    "fire":      ["#FF0000", "#FF4600", "#FFB400", "#FFFF00"],
    "ocean":     ["#0018FF", "#0078FF", "#00D2C8", "#64FFDC"],
    "sunset":    ["#FF2050", "#FF6020", "#FFB000", "#C040FF"],
    "rainbow":   ["#FF0000", "#FFC800", "#00FF50", "#0064FF"],
    "custom":    ["#FF0055", "#00FFFF", "#FFFF00", "#AA00FF"],  # overwritten by user colors
}

SPECTRUM_COLORS = ["#FF0000", "#FF9600", "#00FF3C", "#0078FF"]  # red, amber, green, azure


def hex_to_rgb(h):
    h = h.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Bad color {h!r}")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def rgb_to_hex(c):
    return "#" + "".join(f"{int(round(max(0, min(1, v)) * 255)):02X}" for v in c)


def scale(c, k):
    return (c[0] * k, c[1] * k, c[2] * k)


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


WHITE = (1.0, 1.0, 1.0)
BLACK = (0.0, 0.0, 0.0)


class Mode:
    key = ""
    label = ""
    description = ""

    def reset(self):
        pass

    def render(self, frame, dt, palette, decay_ms):
        raise NotImplementedError


class SpectrumMode(Mode):
    key, label = "spectrum", "Spectrum"
    description = "Each zone shows one frequency band: bass on the left, treble on the numpad."

    def render(self, frame, dt, palette, decay_ms):
        cols = [hex_to_rgb(h) for h in SPECTRUM_COLORS]
        return [scale(cols[i], float(frame.levels[i])) for i in range(4)]


class BandsMode(Mode):
    key, label = "bands", "Theme Bands"
    description = "Like Spectrum, but using your theme or custom colors. Strong kicks flash white."

    def __init__(self):
        self.flash = 0.0

    def reset(self):
        self.flash = 0.0

    def render(self, frame, dt, palette, decay_ms):
        if frame.beat and frame.beat_strength > 0.8:
            self.flash = (frame.beat_strength - 0.8) / 0.2 * 0.6
        self.flash *= math.exp(-dt * 1000 / 90)
        out = []
        for i in range(4):
            c = scale(palette[i], float(frame.levels[i]))
            if i < 2 and self.flash > 0.01:
                c = mix(c, WHITE, self.flash)
            out.append(c)
        return out


class PulseMode(Mode):
    key, label = "pulse", "Pulse"
    description = "Whole keyboard punches on every beat, stepping through the theme colors."

    def __init__(self):
        self.reset()

    def reset(self):
        self.flash = 0.0
        self.idx = 0
        self.color = None

    def render(self, frame, dt, palette, decay_ms):
        if self.color is None:
            self.color = palette[0]
        if frame.beat:
            self.flash = max(self.flash, frame.beat_strength)
            self.idx = (self.idx + 1) % len(palette)
            self.color = palette[self.idx]  # snap on the hit so the flash is a clean color
        else:
            self.color = palette[self.idx]
        self.flash *= math.exp(-dt * 1000 / max(decay_ms * 1.4, 60))
        body = 0.25 * frame.loudness
        v = max(self.flash, body)
        return [scale(self.color, v)] * 4


class WaveMode(Mode):
    key, label = "wave", "Wave"
    description = "Each kick launches a wave of light that sweeps across the keyboard."

    SPEED = 14.0  # zones per second

    def __init__(self):
        self.reset()

    def reset(self):
        self.waves = []  # [position, amplitude, color]
        self.idx = 0
        self.direction = 1

    def render(self, frame, dt, palette, decay_ms):
        if frame.beat:
            self.idx = (self.idx + 1) % len(palette)
            start = -0.6 if self.direction > 0 else 3.6
            self.waves.append([start, frame.beat_strength, palette[self.idx], self.direction])
            self.direction = -self.direction  # alternate left->right, right->left
        fade = math.exp(-dt * 1000 / max(decay_ms * 2.5, 150))
        for w in self.waves:
            w[0] += w[3] * self.SPEED * dt
            w[1] *= fade
        self.waves = [w for w in self.waves if -1.5 < w[0] < 4.5 and w[1] > 0.02]

        out = []
        for i in range(4):
            acc = [0.0, 0.0, 0.0]
            for pos, amp, col, _ in self.waves:
                k = amp * math.exp(-((i - pos) ** 2) / 0.6)
                for j in range(3):
                    acc[j] += col[j] * k
            body = scale(palette[i], 0.15 * float(frame.levels[i]))
            out.append(tuple(min(1.0, acc[j] + body[j]) for j in range(3)))
        return out


class ColorHopMode(Mode):
    key, label = "hop", "Color Hop"
    description = "Each beat jumps the whole keyboard to the next color; treble adds sparkle on the numpad."

    def __init__(self):
        self.reset()

    def reset(self):
        self.idx = 0
        self.flash = 0.0

    def render(self, frame, dt, palette, decay_ms):
        if frame.beat:
            self.idx = (self.idx + 1) % len(palette)
            self.flash = frame.beat_strength
        self.flash *= math.exp(-dt * 1000 / max(decay_ms * 2.0, 80))
        base = palette[self.idx]
        v = max(self.flash, 0.35 * frame.loudness)
        out = [scale(base, v) for _ in range(4)]
        hat = float(frame.levels[3])
        out[3] = mix(out[3], palette[(self.idx + 2) % len(palette)], hat * 0.7)
        return out


class HeatMode(Mode):
    key, label = "heat", "Heat Map"
    description = "Each band glows from deep blue (quiet) through green to red and white (loud)."

    def render(self, frame, dt, palette, decay_ms):
        out = []
        for i in range(4):
            e = float(frame.levels[i])
            if e <= 0:
                out.append(BLACK)
                continue
            hue = 0.66 * (1.0 - e ** 0.85)
            sat = 1.0 if e < 0.85 else max(0.25, 1.0 - (e - 0.85) / 0.15)
            out.append(colorsys.hsv_to_rgb(hue, sat, min(1.0, e * 1.15)))
        return out


class SolidMode(Mode):
    key, label = "solid", "One Color"
    description = "The whole keyboard in one color of your choice. Brightness follows the music and punches on beats."

    def __init__(self):
        self.flash = 0.0

    def reset(self):
        self.flash = 0.0

    def render(self, frame, dt, palette, decay_ms):
        if frame.beat:
            self.flash = max(self.flash, frame.beat_strength)
        self.flash *= math.exp(-dt * 1000 / max(decay_ms * 1.4, 60))
        lv = frame.levels
        # Kicks drive it; sustained bass and overall loudness only keep a softer glow between beats.
        body = max(0.8 * float(lv[0]), 0.3 * float(lv[1]), 0.25 * frame.loudness)
        return [scale(palette[0], max(body, self.flash))] * 4


MODES = {m.key: m for m in (SpectrumMode(), SolidMode(), BandsMode(), PulseMode(), WaveMode(),
                            ColorHopMode(), HeatMode())}


def palette_for(theme, custom_colors, mode=None, solid_color=None):
    if mode == "solid":
        try:
            return [hex_to_rgb(solid_color or "#FF0055")] * 4
        except (ValueError, TypeError):
            return [hex_to_rgb("#FF0055")] * 4
    hexes = custom_colors if theme == "custom" else THEMES.get(theme, THEMES["cyberpunk"])
    try:
        return [hex_to_rgb(h) for h in hexes[:4]]
    except (ValueError, TypeError):
        return [hex_to_rgb(h) for h in THEMES["cyberpunk"]]


def finish(colors, brightness, idle_glow, palette, silent):
    """Applies master brightness and the idle glow, returns 4 (r, g, b) ints."""
    out = []
    for i, c in enumerate(colors):
        if idle_glow > 0:
            glow = scale(palette[i], idle_glow)
            c = tuple(max(c[j], glow[j]) for j in range(3))
        out.append(tuple(int(round(np.clip(v * brightness, 0.0, 1.0) * 255)) for v in c))
    return out


def led_contrast(colors, contrast):
    """
    LEDs look brighter at low values than they are (the eye isn't linear), so dim moments
    between hits don't read as dark. Scale each color by brightness^(contrast-1): full-bright
    stays full, dim gets dimmer, and the hue is kept because all channels scale together.
    contrast 1.0 = off, ~2.0 = typical LED correction.
    """
    if contrast <= 1.0:
        return colors
    out = []
    for c in colors:
        b = max(c) / 255.0
        k = b ** (contrast - 1.0) if b > 0 else 0.0
        out.append(tuple(int(round(v * k)) for v in c))
    return out
