"""
ui_window.py - The settings window: live keyboard preview + Lighting / Sound / Setup tabs.

Design rules that keep clicks reliable:
* No scrolling container; content is split into tabs that each fit the window.
* Every clickable thing is a single CTk widget (no frames with hand-made click bindings).
* The window never re-applies settings while you're using it. It only re-reads them when
  something outside changes them (tray menu), via apply_external_change().
"""

import logging
import tkinter as tk
from tkinter import colorchooser

import customtkinter as ctk
from PIL import Image, ImageDraw

from audio_capture import list_loopback_devices
from lighting import MODES, THEMES, hex_to_rgb

log = logging.getLogger("ui")

# ---- design tokens
BG = "#0E1014"
CARD = "#161920"
CONTROL = "#21252F"
CONTROL_HOVER = "#2A2F3B"
BORDER = "#2A2F3B"
TEXT = "#E9EBF1"
MUTED = "#8B90A0"
FAINT = "#5C6170"
ACCENT = "#FF3355"
ACCENT_HOVER = "#E02A49"
ACCENT_SOFT = "#3A1820"
GREEN = "#3DDC84"
AMBER = "#F5B942"
RED = "#FF5A5A"
FONT = "Segoe UI"

ZONE_NAMES = ["Sub-bass", "Bass", "Mids", "Treble"]
AUTO_DEVICE = "Auto (follow the sound)"
PRESETS = ["#FF0055", "#FF2D2D", "#FF7A00", "#FFD000", "#3DFF6E", "#00E5FF",
           "#2D6BFF", "#8A2BFF", "#FF3DF2", "#FFFFFF"]
OWN_COLOR_MODES = {"spectrum", "heat"}


def font(size=13, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


def label(parent, text="", size=12, color=TEXT, weight="normal", **kw):
    """CTkLabel with a height that matches its font (the default 28 px bloats rows)."""
    kw.setdefault("anchor", "w")
    return ctk.CTkLabel(parent, text=text, font=font(size, weight), text_color=color,
                        height=size + 8, **kw)


def mode_icon(key, size=22, color=(233, 235, 241)):
    """Small icon for each mode, drawn at 3x and scaled down for smooth edges."""
    import math
    S = size * 3
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = color + (255,)
    if key == "spectrum":
        for i, h in enumerate((0.35, 0.65, 1.0, 0.5)):
            x = 6 + i * 16
            d.rounded_rectangle((x, S - 6 - h * (S - 12), x + 10, S - 6), radius=4, fill=c)
    elif key == "solid":
        d.ellipse((10, 10, S - 10, S - 10), fill=(255, 51, 85, 255))
    elif key == "bands":
        for i, cc in enumerate(((255, 0, 100), (0, 255, 220), (255, 220, 0), (160, 0, 255))):
            x = 4 + i * 15
            d.rounded_rectangle((x, 12, x + 12, S - 12), radius=4, fill=cc + (255,))
    elif key == "pulse":
        m = S // 2
        d.ellipse((m - 9, m - 9, m + 9, m + 9), fill=c)
        d.ellipse((m - 22, m - 22, m + 22, m + 22), outline=c, width=5)
        d.ellipse((m - 31, m - 31, m + 31, m + 31), outline=color + (110,), width=4)
    elif key == "wave":
        pts = [(x, S / 2 - math.sin(x / S * 2 * math.pi) * S * 0.28) for x in range(4, S - 3, 2)]
        d.line(pts, fill=c, width=6, joint="curve")
    elif key == "hop":
        for i, (cx, cy, r) in enumerate(((22, 44, 14), (44, 22, 14))):
            d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)],
                      fill=c if i else color + (120,))
    elif key == "heat":
        grad = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        for y in range(S):
            t = y / S
            gd.line((0, y, S, y), fill=(255, int(70 + 180 * (1 - t) ** 2), int(30 * (1 - t)), 255))
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).polygon([(S / 2, 6), (S - 10, S - 8), (10, S - 8)], fill=255)
        img.paste(grad, (0, 0), mask)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


def theme_image(hexes, w=30, h=12):
    img = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    seg = (w * 2) / 4
    for i, hx in enumerate(hexes[:4]):
        rgb = tuple(int(v * 255) for v in hex_to_rgb(hx))
        d.rectangle((i * seg, 0, (i + 1) * seg, h * 2), fill=rgb + (255,))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * 2 - 1, h * 2 - 1), radius=8, fill=255)
    img.putalpha(mask)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(w, h))


def rounded(c, x0, y0, x1, y1, r, **kw):
    """Rounded rectangle on a tk Canvas."""
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


def shade(rgb, k):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v * k))) for v in rgb)


class SettingsWindow:
    def __init__(self, root, store, engine, on_quit):
        self.root, self.store, self.engine, self.on_quit = root, store, engine, on_quit
        self._last_beats = 0
        self._beat_glow = 0.0
        self._warn_shown = False
        self._color_mode_shown = None
        self.sliders = {}
        self._build()
        self.apply_external_change()
        self._tick()

    # ================================================================ layout
    def _build(self):
        r = self.root
        r.title("Predator Keyboard Visualizer")
        r.geometry("460x800")
        r.minsize(460, 760)
        r.resizable(False, True)
        r.configure(fg_color=BG)
        r.protocol("WM_DELETE_WINDOW", self.hide)

        body = ctk.CTkFrame(r, fg_color=BG, corner_radius=0)
        body.pack(fill="both", expand=True, padx=18, pady=(16, 14))
        self.body = body

        # -- header
        head = ctk.CTkFrame(body, fg_color="transparent")
        head.pack(fill="x")
        label(head, "Keyboard Visualizer", 19, weight="bold").pack(side="left")
        self.enabled_var = tk.BooleanVar()
        self.power = ctk.CTkSwitch(head, text="", width=46, variable=self.enabled_var, progress_color=ACCENT,
                                   button_color=TEXT, button_hover_color="#FFFFFF", fg_color=CONTROL,
                                   command=lambda: self._set(enabled=self.enabled_var.get()))
        self.power.pack(side="right")

        st = ctk.CTkFrame(body, fg_color="transparent")
        st.pack(fill="x", pady=(2, 0))
        self.status_dot = label(st, "●", 11, FAINT, width=12, anchor="center")
        self.status_dot.pack(side="left")
        self.status_main = label(st, "", 12)
        self.status_main.pack(side="left", padx=(4, 0))
        self.status_sub = label(body, "", 11, MUTED)
        self.status_sub.pack(fill="x", padx=(16, 0))

        # -- Wave Link warning (packed only when needed)
        self.warn = ctk.CTkFrame(body, fg_color="#2B2112", border_color="#6B4E1A", border_width=1, corner_radius=12)
        ctk.CTkButton(self.warn, text="Pause", width=64, height=30, corner_radius=8, font=font(12, "bold"),
                      fg_color="#4A3818", hover_color="#5C461E", text_color="#F5D08A",
                      command=lambda: self._set(enabled=False)).pack(side="right", padx=(6, 10), pady=10)
        self.warn_text = ctk.CTkLabel(self.warn, text="", text_color="#F5D08A", justify="left", anchor="w",
                                      wraplength=300, font=font(12))
        self.warn_text.pack(side="left", fill="x", expand=True, padx=(12, 0), pady=8)

        # -- live preview card
        prev = ctk.CTkFrame(body, fg_color=CARD, corner_radius=14)
        prev.pack(fill="x", pady=(12, 0))
        self.preview_card = prev
        self.canvas = tk.Canvas(prev, height=118, bg=CARD, highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", padx=14, pady=12)

        # -- tabs
        self.tabs = ctk.CTkSegmentedButton(
            body, values=["Lighting", "Sound", "Setup"], height=34, corner_radius=10, font=font(13, "bold"),
            fg_color=CARD, selected_color=CONTROL_HOVER, selected_hover_color=CONTROL_HOVER,
            unselected_color=CARD, unselected_hover_color=CONTROL, text_color=TEXT,
            command=self._show_tab)
        self.tabs.pack(fill="x", pady=(12, 10))

        self.pages = {name: ctk.CTkFrame(body, fg_color="transparent") for name in ("Lighting", "Sound", "Setup")}
        self._build_lighting(self.pages["Lighting"])
        self._build_sound(self.pages["Sound"])
        self._build_setup(self.pages["Setup"])
        self.tabs.set("Lighting")
        self._show_tab("Lighting")

    def _card(self, parent, title=None, pady=(0, 10)):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14)
        card.pack(fill="x", pady=pady)
        if title:
            label(card, title.upper(), 11, MUTED, "bold").pack(fill="x", padx=14, pady=(12, 6))
        return card

    # ---- Lighting tab
    def _build_lighting(self, page):
        card = self._card(page, "Mode")
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=10)
        self.mode_buttons = {}
        for n, (key, mode) in enumerate(MODES.items()):
            b = ctk.CTkButton(grid, text=mode.label, image=mode_icon(key), compound="top", height=60,
                              corner_radius=10, font=font(12), text_color=TEXT, fg_color=CONTROL,
                              hover_color=CONTROL_HOVER,
                              border_width=2, border_color=CONTROL,
                              command=lambda k=key: self._choose_mode(k))
            b.grid(row=n // 4, column=n % 4, padx=4, pady=4, sticky="nsew")
            self.mode_buttons[key] = b
        for c in range(4):
            grid.grid_columnconfigure(c, weight=1, uniform="modes")
        self.mode_desc = label(card, "", 12, MUTED, wraplength=390, justify="left")
        self.mode_desc.pack(fill="x", padx=14, pady=(4, 12))

        self.color_card = self._card(page, "Color")
        self.color_body = ctk.CTkFrame(self.color_card, fg_color="transparent")
        self.color_body.pack(fill="x", padx=10, pady=(0, 12))

    def _rebuild_colors(self, s):
        for w in self.color_body.winfo_children():
            w.destroy()
        self.theme_buttons, self.swatches, self.preset_buttons = {}, [], []
        self.solid_button = None

        if s.mode in OWN_COLOR_MODES:
            label(self.color_body, f"{MODES[s.mode].label} uses its own built-in colors.", 12, MUTED
                  ).pack(fill="x", padx=4, pady=4)
            return

        if s.mode == "solid":
            row = ctk.CTkFrame(self.color_body, fg_color="transparent")
            row.pack(fill="x", padx=4)
            self.solid_button = ctk.CTkButton(row, text="", width=64, height=40, corner_radius=10,
                                              border_width=2, border_color=BORDER,
                                              command=self._pick_solid)
            self.solid_button.pack(side="left")
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", padx=12)
            self.solid_hex = label(info, "", 14, weight="bold")
            self.solid_hex.pack(anchor="w")
            label(info, "Click the swatch to pick any color", 11, MUTED).pack(anchor="w")
            chips = ctk.CTkFrame(self.color_body, fg_color="transparent")
            chips.pack(fill="x", pady=(10, 0))
            for i, hx in enumerate(PRESETS):
                b = ctk.CTkButton(chips, text="", width=30, height=30, corner_radius=15, fg_color=hx,
                                  hover_color=hx, border_width=2, border_color=CARD,
                                  command=lambda h=hx: self._set(solid_color=h))
                b.grid(row=0, column=i, padx=4, pady=2)
                self.preset_buttons.append((hx, b))
            self._paint_colors(s)
            return

        grid = ctk.CTkFrame(self.color_body, fg_color="transparent")
        grid.pack(fill="x")
        for n, name in enumerate(THEMES):
            hexes = s.custom_colors if name == "custom" else THEMES[name]
            b = ctk.CTkButton(grid, text=name.capitalize(), image=theme_image(hexes), compound="left",
                              anchor="w", height=34, corner_radius=10, font=font(12), text_color=TEXT,
                              fg_color=CONTROL, hover_color=CONTROL_HOVER, border_width=2, border_color=CONTROL,
                              command=lambda t=name: self._set(theme=t))
            b.grid(row=n // 3, column=n % 3, padx=3, pady=3, sticky="ew")
            self.theme_buttons[name] = b
        for c in range(3):
            grid.grid_columnconfigure(c, weight=1, uniform="themes")

        own = ctk.CTkFrame(self.color_body, fg_color="transparent")
        own.pack(fill="x", padx=4, pady=(8, 0))
        label(own, "Custom colors", 12, MUTED).pack(side="left")
        for i in reversed(range(4)):
            b = ctk.CTkButton(own, text="", width=36, height=26, corner_radius=8, border_width=2,
                              border_color=BORDER, command=lambda i=i: self._pick_custom(i))
            b.pack(side="right", padx=3)
            self.swatches.insert(0, b)
        self._paint_colors(s)

    # ---- Sound tab
    def _build_sound(self, page):
        card = self._card(page, "Reaction")
        self._slider(card, "sensitivity", "Sensitivity", "How much quiet detail shows", 0.5, 2.5,
                     lambda v: f"{v:.1f}x")
        self._slider(card, "decay_ms", "Fade after hits", "How long light lingers", 60, 600,
                     lambda v: f"{v:.0f} ms")
        self._slider(card, "noise_gate", "Noise gate", "Quieter than this stays dark", 0.0, 0.3,
                     lambda v: f"{v * 100:.0f}%", last=True)
        card = self._card(page, "Light")
        self._slider(card, "brightness", "Brightness", None, 0.1, 1.0, lambda v: f"{v * 100:.0f}%")
        self._slider(card, "contrast", "Contrast", "Higher = darker between hits, so beats stand out",
                     1.0, 3.0, lambda v: "Off" if v < 1.05 else f"{v:.1f}")
        self._slider(card, "idle_glow", "Glow when quiet", "Off = pitch black between sounds", 0.0, 0.3,
                     lambda v: "Off" if v < 0.005 else f"{v * 100:.0f}%", last=True)

    def _slider(self, parent, key, title, hint, lo, hi, fmt, steps=None, last=False):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", padx=14, pady=(0, 14 if last else 10))
        top = ctk.CTkFrame(wrap, fg_color="transparent")
        top.pack(fill="x")
        label(top, title, 13).pack(side="left")
        val = label(top, "", 12, weight="bold", anchor="e")
        val.pack(side="right")
        if hint:
            label(wrap, hint, 11, MUTED).pack(fill="x")

        def changed(v, key=key, val=val):
            val.configure(text=fmt(v))
            self._set(**{key: round(float(v), 3)})

        s = ctk.CTkSlider(wrap, from_=lo, to=hi, number_of_steps=steps, height=16, button_length=0,
                          button_color=TEXT, button_hover_color="#FFFFFF", progress_color=ACCENT,
                          fg_color=CONTROL, command=changed)
        s.pack(fill="x", pady=(4, 0))
        self.sliders[key] = (s, val, fmt)

    # ---- Setup tab
    def _build_setup(self, page):
        card = self._card(page, "Audio source")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 6))
        self.device_menu = ctk.CTkOptionMenu(
            row, values=[AUTO_DEVICE], height=34, corner_radius=10, dynamic_resizing=False, font=font(12),
            dropdown_font=font(12), fg_color=CONTROL, button_color=CONTROL_HOVER, button_hover_color=ACCENT,
            dropdown_fg_color=CARD, dropdown_hover_color=CONTROL_HOVER, text_color=TEXT,
            command=self._choose_device)
        self.device_menu.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="⟳", width=34, height=34, corner_radius=10, font=font(15), fg_color=CONTROL,
                      hover_color=CONTROL_HOVER, text_color=TEXT, command=self._load_devices).pack(side="left", padx=(6, 0))
        label(card, "Auto follows whichever output is actually playing.", 11, MUTED
              ).pack(fill="x", padx=14, pady=(0, 12))
        self._load_devices()

        card = self._card(page, "Keyboard")
        self._slider(card, "keyboard_hz", "Updates per second", "Acer's lighting service maxes out near 15",
                     4, 15, lambda v: f"{v:.0f}/s", steps=11)
        self.gaming_var = tk.BooleanVar()
        g = ctk.CTkFrame(card, fg_color="transparent")
        g.pack(fill="x", padx=14, pady=(0, 14))
        txt = ctk.CTkFrame(g, fg_color="transparent")
        txt.pack(side="left", fill="x", expand=True)
        label(txt, "Gaming mode", 13).pack(fill="x")
        label(txt, "Low priority, max 8 updates/s", 11, MUTED).pack(fill="x")
        ctk.CTkSwitch(g, text="", width=46, variable=self.gaming_var, progress_color=ACCENT, fg_color=CONTROL,
                      button_color=TEXT, button_hover_color="#FFFFFF",
                      command=lambda: self._set(gaming=self.gaming_var.get())).pack(side="right")

        card = self._card(page, "About pops")
        ctk.CTkLabel(card, text="Acer's lighting service briefly freezes the PC while the keyboard changes. "
                                "Play audio straight to Realtek (not Elgato Wave Link) to avoid pops.",
                     font=font(11), text_color=MUTED, wraplength=390, justify="left", height=32,
                     anchor="w").pack(fill="x", padx=14, pady=(0, 12))

        foot = ctk.CTkFrame(page, fg_color="transparent")
        foot.pack(fill="x", pady=(4, 0))
        ctk.CTkButton(foot, text="Hide to tray", height=38, corner_radius=10, font=font(13, "bold"),
                      fg_color=CONTROL, hover_color=CONTROL_HOVER, text_color=TEXT,
                      command=self.hide).pack(side="left", expand=True, fill="x", padx=(0, 5))
        ctk.CTkButton(foot, text="Quit", height=38, corner_radius=10, font=font(13, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF",
                      command=self.on_quit).pack(side="left", expand=True, fill="x", padx=(5, 0))

    # ================================================================ behaviour
    def _set(self, **changes):
        self.store.update(**changes)
        if {"mode", "theme", "custom_colors", "solid_color"} & set(changes):
            self._paint_selection(self.store.snapshot())

    def _show_tab(self, name):
        for n, p in self.pages.items():
            if n == name:
                p.pack(fill="both", expand=True)
            else:
                p.pack_forget()

    def _choose_mode(self, key):
        self._set(mode=key)

    def apply_external_change(self):
        """Re-read every control from settings. Only for changes made outside this window."""
        s = self.store.snapshot()
        self.enabled_var.set(s.enabled)
        self.gaming_var.set(s.gaming)
        for key, (sl, val, fmt) in self.sliders.items():
            v = getattr(s, key)
            sl.set(v)
            val.configure(text=fmt(v))
        self.device_menu.set(AUTO_DEVICE if s.device == "auto" else s.device)
        self._paint_selection(s)

    def _paint_selection(self, s):
        for key, b in self.mode_buttons.items():
            sel = key == s.mode
            b.configure(fg_color=ACCENT_SOFT if sel else CONTROL, border_color=ACCENT if sel else CONTROL,
                        hover_color=ACCENT_SOFT if sel else CONTROL_HOVER)
        self.mode_desc.configure(text=MODES.get(s.mode, MODES["spectrum"]).description)
        group = "own" if s.mode in OWN_COLOR_MODES else s.mode if s.mode == "solid" else "theme"
        if group != self._color_mode_shown:
            self._color_mode_shown = group
            self._rebuild_colors(s)
        else:
            self._paint_colors(s)

    def _paint_colors(self, s):
        if self.solid_button is not None:
            self.solid_button.configure(fg_color=s.solid_color, hover_color=s.solid_color)
            self.solid_hex.configure(text=s.solid_color.upper())
            for hx, b in self.preset_buttons:
                b.configure(border_color=TEXT if hx.upper() == s.solid_color.upper() else CARD)
        for name, b in self.theme_buttons.items():
            sel = name == s.theme
            b.configure(fg_color=ACCENT_SOFT if sel else CONTROL, border_color=ACCENT if sel else CONTROL,
                        hover_color=ACCENT_SOFT if sel else CONTROL_HOVER)
        if "custom" in self.theme_buttons:
            self.theme_buttons["custom"].configure(image=theme_image(s.custom_colors))
        for i, b in enumerate(self.swatches):
            c = s.custom_colors[i]
            b.configure(fg_color=c, hover_color=c)

    def _pick_solid(self):
        s = self.store.snapshot()
        _, hx = colorchooser.askcolor(color=s.solid_color, parent=self.root, title="Keyboard color")
        if hx:
            self._set(solid_color=hx.upper())

    def _pick_custom(self, i):
        s = self.store.snapshot()
        _, hx = colorchooser.askcolor(color=s.custom_colors[i], parent=self.root, title=f"Zone {i + 1} color")
        if hx:
            cols = list(s.custom_colors)
            cols[i] = hx.upper()
            self._set(custom_colors=cols, theme="custom")

    def _load_devices(self):
        try:
            names = [d["name"] for d in list_loopback_devices()]
        except Exception as e:
            log.warning("Device list failed: %s", e)
            names = []
        self.device_menu.configure(values=[AUTO_DEVICE] + names)

    def _choose_device(self, v):
        self._set(device="auto" if v == AUTO_DEVICE else v)

    # ================================================================ live updates
    def _tick(self):
        try:
            self._draw()
        except Exception as e:
            log.debug("preview: %s", e)
        self.root.after(33, self._tick)

    def _draw(self):
        if self.root.state() == "withdrawn":
            return
        st = self.engine.state
        s = self.store.snapshot()

        # status lines
        if not s.enabled:
            dot, main = FAINT, "Paused - PredatorSense lighting restored"
        elif not str(st["kb_status"]).startswith("connected"):
            dot, main = RED, st["kb_status"]
        elif st["silent"]:
            dot, main = AMBER, f"Waiting for sound · {st['device']}"
        else:
            dot, main = GREEN, f"Playing · {st['device']}"
        self.status_dot.configure(text_color=dot)
        self.status_main.configure(text=main)
        self.status_sub.configure(text=f"Keyboard {st['kb_hz']:.0f} updates/s  ·  {st['fps']:.0f} fps")

        want = bool(st.get("wave_link")) and s.enabled
        if want:
            self.warn_text.configure(
                text="⚠  Audio is going through Elgato Wave Link, which pops while the keyboard "
                     "changes. Switch Windows output to Realtek, or pause.")
        if want != self._warn_shown:
            if want:
                self.warn.pack(fill="x", pady=(12, 0), before=self.preview_card)
            else:
                self.warn.pack_forget()
            self._warn_shown = want

        # preview
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 300)
        if st["beats"] != self._last_beats:
            self._last_beats = st["beats"]
            self._beat_glow = 1.0
        self._beat_glow *= 0.82

        c.create_text(0, 9, text="LIVE KEYBOARD", fill=MUTED, anchor="w", font=(FONT, 9, "bold"))
        bpm = f"{st['bpm']:.0f} BPM" if st["bpm"] else "listening"
        c.create_text(w - 16, 9, text=bpm, fill=MUTED, anchor="e", font=(FONT, 9))
        g = int(70 + 185 * self._beat_glow)
        c.create_oval(w - 10, 4, w, 14, fill="#%02x%02x%02x" % (g, int(g * 0.2), int(g * 0.33)), outline="")

        gap, top, kh = 8, 22, 56
        zw = (w - gap * 3) / 4
        for i in range(4):
            x = i * (zw + gap)
            rgb = st["colors"][i]
            lit = max(rgb) > 8
            rounded(c, x, top, x + zw, top + kh, 10, fill=shade(rgb, 1.0) if lit else "#1B1E26",
                    outline=BORDER if not lit else shade(rgb, 1.15))
            key_fill = shade(rgb, 0.72) if lit else "#22262F"
            kw_, khh = (zw - 16) / 4, (kh - 14) / 3
            for ky in range(3):
                for kx in range(4):
                    x0 = x + 8 + kx * kw_
                    y0 = top + 7 + ky * khh
                    rounded(c, x0 + 1.5, y0 + 1.5, x0 + kw_ - 1.5, y0 + khh - 1.5, 3, fill=key_fill, outline="")
            lv = float(st["levels"][i])
            by = top + kh + 12
            rounded(c, x, by, x + zw, by + 5, 3, fill="#252933", outline="")
            if lv > 0.02:
                rounded(c, x, by, x + max(6, zw * lv), by + 5, 3, fill=ACCENT, outline="")
            c.create_text(x + zw / 2, by + 20, text=ZONE_NAMES[i], fill=MUTED, font=(FONT, 9))

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.apply_external_change()

    def hide(self):
        self.root.withdraw()
