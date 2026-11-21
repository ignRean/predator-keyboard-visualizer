"""
ui_check.py - Opens the settings window with a stand-in engine (no audio, no keyboard),
checks that every control is clickable where it's drawn, clicks a few, and saves a
picture of each tab.

    python tests/ui_check.py [output_folder]
"""
import ctypes
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()

import customtkinter as ctk  # noqa: E402

from settings import SettingsStore  # noqa: E402
from ui_window import SettingsWindow  # noqa: E402

ctypes.windll.shcore.SetProcessDpiAwareness(2)
TITLE = "UI Check"
failures = []


class FakeEngine:
    state = {"levels": [0.85, 0.55, 0.3, 0.6], "colors": [(255, 0, 100), (0, 255, 220), (255, 220, 0), (160, 0, 255)],
             "beats": 0, "bpm": 124.0, "fps": 60.0, "kb_hz": 14.0, "device": "Headphones (Realtek(R) Audio)",
             "audio_status": "listening", "kb_status": "connected: AcerECKeyboard Device", "silent": False,
             "output": "Headphones (Realtek(R) Audio)", "wave_link": False}


path = os.path.join(tempfile.gettempdir(), "pv_ui_check.json")
if os.path.exists(path):
    os.remove(path)
store = SettingsStore(path)
ctk.set_appearance_mode("dark")
root = ctk.CTk()
win = SettingsWindow(root, store, FakeEngine(), root.destroy)
root.title(TITLE)
root.attributes("-topmost", True)  # hit tests read the screen; other windows on top would block them


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


def owns(widget, hit):
    while hit is not None:
        if hit is widget:
            return True
        hit = hit.master
    return False


def controls(parent):
    out = []
    for w in parent.winfo_children():
        if isinstance(w, (ctk.CTkButton, ctk.CTkSwitch, ctk.CTkSlider, ctk.CTkOptionMenu, ctk.CTkSegmentedButton)):
            out.append(w)
        else:
            out.extend(controls(w))
    return out


def hit_test(label):
    root.update()
    bad = []
    items = [w for w in controls(win.body) if w.winfo_ismapped()]
    for w in items:
        x = w.winfo_rootx() + w.winfo_width() // 2
        y = w.winfo_rooty() + w.winfo_height() // 2
        if not owns(w, root.winfo_containing(x, y)):
            bad.append(f"{type(w).__name__}({w.cget('text') if hasattr(w, 'cget') and isinstance(w, ctk.CTkButton) else ''})")
    check(f"{label}: all {len(items)} controls clickable where drawn", not bad, ", ".join(bad))


def click(widget):
    """Click the widget at the center of where it's drawn, through Tk's own hit test."""
    root.update()
    x = widget.winfo_rootx() + widget.winfo_width() // 2
    y = widget.winfo_rooty() + widget.winfo_height() // 2
    target = root.winfo_containing(x, y)
    lx, ly = x - target.winfo_rootx(), y - target.winfo_rooty()
    target.event_generate("<Enter>", x=lx, y=ly)
    target.event_generate("<Button-1>", x=lx, y=ly)
    target.event_generate("<ButtonRelease-1>", x=lx, y=ly)
    root.update()


def shot(name):
    import win32gui
    import win32ui
    from PIL import Image
    root.update()
    h = win32gui.FindWindow(None, TITLE)
    l, t, r, b = win32gui.GetWindowRect(h)
    wd, ht = r - l, b - t
    hdc = win32gui.GetWindowDC(h)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, wd, ht)
    mem.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(h, mem.GetSafeHdc(), 2)
    info = bmp.GetInfo()
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    # crop to the client area (drops the title bar and borders)
    cx, cy = win32gui.ClientToScreen(h, (0, 0))
    _, _, cw, ch = win32gui.GetClientRect(h)
    img.crop((cx - l, cy - t, cx - l + cw, cy - t + ch)).save(os.path.join(OUT, name))
    win32gui.DeleteObject(bmp.GetHandle())
    mem.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(h, hdc)


def run():
    print("Lighting tab")
    hit_test("Lighting")
    click(win.mode_buttons["wave"])
    check("click Wave tile -> mode=wave", store.snapshot().mode == "wave")
    click(win.theme_buttons["ocean"])
    check("click Ocean chip -> theme=ocean", store.snapshot().theme == "ocean")
    shot("ui_lighting_themes.png")
    click(win.mode_buttons["solid"])
    check("click One Color tile -> mode=solid", store.snapshot().mode == "solid")
    hit_test("Lighting (One Color)")
    click(win.preset_buttons[5][1])
    check("click cyan preset -> solid_color", store.snapshot().solid_color.upper() == "#00E5FF")
    shot("ui_lighting_solid.png")

    print("Sound tab")
    win.tabs.set("Sound")
    win._show_tab("Sound")
    hit_test("Sound")
    s, _, _ = win.sliders["sensitivity"]
    s.set(2.0)
    s._command(2.0)
    root.update()
    check("slider keeps its position after changing a setting", abs(s.get() - 2.0) < 0.01, f"(at {s.get():.2f})")
    shot("ui_sound.png")

    print("Setup tab")
    win.tabs.set("Setup")
    win._show_tab("Setup")
    hit_test("Setup")
    shot("ui_setup.png")

    FakeEngine.state.update(wave_link=True, output="System (Elgato Virtual Audio)")
    win.tabs.set("Lighting")
    win._show_tab("Lighting")
    root.after(200, finish)


def finish():
    win._draw()
    hit_test("Lighting with Wave Link banner")
    shot("ui_wavelink.png")
    print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED"))
    root.destroy()


def safe(fn):
    def wrapped():
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            failures.append(f"{fn.__name__} crashed")
            root.destroy()
    return wrapped


run, finish = safe(run), safe(finish)
root.after(1200, run)
root.after(20000, lambda: (print("TIMEOUT"), failures.append("timeout"), root.destroy()))
root.mainloop()
sys.exit(1 if failures else 0)
