"""
tray_app.py - System tray app + settings window for the Predator keyboard visualizer.

* Lives in the Windows tray. Right-click for quick mode/theme switches, pause and quit.
* "Settings" opens a window with a live preview of the 4 zones and every control.
* Closing the window just hides it; Quit (tray or window) restores your PredatorSense lighting.
* Only one copy can run; starting it again just opens the settings window.
"""

import ctypes
import logging
import os
import queue
import sys
import threading
import tempfile

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw

from engine import Engine
from lighting import MODES, THEMES, palette_for
from settings import SETTINGS_DIR, SettingsStore
from ui_window import SettingsWindow

log = logging.getLogger("app")

APP_NAME = "Predator Keyboard Visualizer"
MUTEX_NAME = "PredatorAudioVisualizer.Singleton"
SHOW_EVENT_NAME = "PredatorAudioVisualizer.Show"
QUIT_EVENT_NAME = "PredatorAudioVisualizer.Quit"


# ----------------------------------------------------------------- single instance

class SingleInstance:
    """Named mutex + named event. A second launch signals the first to show its window."""

    def __init__(self):
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateEventW.restype = ctypes.c_void_p
        k32.OpenEventW.restype = ctypes.c_void_p
        self.k32 = k32
        self.mutex = k32.CreateMutexW(None, False, MUTEX_NAME)
        self.already_running = ctypes.GetLastError() == 183  # ERROR_ALREADY_EXISTS
        self.events = None
        if not self.already_running:
            self.events = (ctypes.c_void_p * 2)(k32.CreateEventW(None, False, False, SHOW_EVENT_NAME),
                                                k32.CreateEventW(None, False, False, QUIT_EVENT_NAME))

    def signal_existing(self, name=SHOW_EVENT_NAME):
        h = self.k32.OpenEventW(0x0002, False, name)  # EVENT_MODIFY_STATE
        if h:
            self.k32.SetEvent(ctypes.c_void_p(h))
            self.k32.CloseHandle(ctypes.c_void_p(h))

    def wait_for_signals(self, on_show, on_quit, stop):
        while not stop.is_set():
            r = self.k32.WaitForMultipleObjects(2, self.events, False, 500)
            if r == 0:
                on_show()
            elif r == 1:
                on_quit()


# ----------------------------------------------------------------------- tray icon

def make_icon(palette, paused=False):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=12, fill=(20, 22, 28, 255))
    heights = [40, 28, 34, 22]
    for i, (c, h) in enumerate(zip(palette, heights)):
        rgb = (90, 90, 90) if paused else tuple(int(v * 255) for v in c)
        x = 9 + i * 12
        d.rounded_rectangle((x, 54 - h, x + 9, 54), radius=3, fill=rgb + (255,))
    return img


# ------------------------------------------------------------------------- app

class App:
    def __init__(self, start_hidden=False):
        self.store = SettingsStore()
        self.engine = Engine(self.store)
        self.ui_queue = queue.Queue()
        self.stop_event = threading.Event()

        ctk.set_appearance_mode("dark")
        self.root = ctk.CTk()
        self.win = SettingsWindow(self.root, self.store, self.engine, self.quit)
        self._set_window_icon()
        if start_hidden:
            self.root.withdraw()

        self.icon = pystray.Icon("predator_visualizer", self._icon_image(), APP_NAME, menu=self._menu())
        self.store.on_change(self._on_settings)

    def _icon_image(self):
        s = self.store.snapshot()
        return make_icon(palette_for(s.theme, s.custom_colors, s.mode, s.solid_color), paused=not s.enabled)

    def _set_window_icon(self):
        try:
            path = os.path.join(tempfile.gettempdir(), "predator_visualizer.ico")
            make_icon(palette_for("cyberpunk", None)).save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
            self.root.iconbitmap(path)
            self.root.after(300, lambda: self.root.iconbitmap(path))  # CTk resets the icon shortly after start
        except Exception as e:
            log.debug("window icon: %s", e)

    def _menu(self):
        st = self.store
        def mode_item(key, m):
            return pystray.MenuItem(m.label, lambda: st.update(mode=key),
                                    checked=lambda _: st.snapshot().mode == key, radio=True)
        def theme_item(t):
            return pystray.MenuItem(t.capitalize(), lambda: st.update(theme=t),
                                    checked=lambda _: st.snapshot().theme == t, radio=True)
        return pystray.Menu(
            pystray.MenuItem("Settings...", lambda: self.ui_queue.put("show"), default=True),
            pystray.MenuItem("On", lambda: st.update(enabled=not st.snapshot().enabled),
                             checked=lambda _: st.snapshot().enabled),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Mode", pystray.Menu(*[mode_item(k, m) for k, m in MODES.items()])),
            pystray.MenuItem("Theme", pystray.Menu(*[theme_item(t) for t in THEMES])),
            pystray.MenuItem("Gaming mode", lambda: st.update(gaming=not st.snapshot().gaming),
                             checked=lambda _: st.snapshot().gaming),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.ui_queue.put("quit")),
        )

    def _on_settings(self, s, changes):
        # Called from whichever thread changed the setting.
        if {"theme", "custom_colors", "enabled", "mode", "solid_color"} & set(changes):
            try:
                self.icon.icon = self._icon_image()
            except Exception:
                pass
        # Changes made in the window are already shown there; re-applying them mid-drag
        # is what made sliders jump. Only refresh for changes from elsewhere (tray menu).
        if threading.current_thread() is not threading.main_thread():
            self.ui_queue.put("refresh")

    def _pump(self):
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                if msg == "show":
                    self.win.show()
                elif msg == "refresh":
                    self.win.apply_external_change()
                elif msg == "quit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def run(self, single):
        self.engine.start()
        self.icon.run_detached()
        threading.Thread(target=single.wait_for_signals,
                         args=(lambda: self.ui_queue.put("show"), lambda: self.ui_queue.put("quit"), self.stop_event),
                         daemon=True).start()
        self.root.after(80, self._pump)
        self.root.mainloop()

    def quit(self):
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        try:
            self.icon.stop()
        except Exception:
            pass
        self.engine.stop()          # restores PredatorSense lighting
        self.store.save()
        self.root.after(0, self.root.destroy)


def setup_logging():
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    handlers = [logging.FileHandler(os.path.join(SETTINGS_DIR, "log.txt"), mode="w", encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)


def main(start_hidden=False, quit_running=False):
    single = SingleInstance()
    if quit_running:
        if single.already_running:
            single.signal_existing(QUIT_EVENT_NAME)
        return
    if single.already_running:
        single.signal_existing()
        return
    setup_logging()
    try:
        App(start_hidden).run(single)
    except Exception:
        log.exception("Fatal error")
        ctypes.windll.user32.MessageBoxW(None, f"The visualizer crashed. Details are in\n{SETTINGS_DIR}\\log.txt",
                                         APP_NAME, 0x10)
        raise


if __name__ == "__main__":
    main("--hidden" in sys.argv)
