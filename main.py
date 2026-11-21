"""
main.py - Start the Predator keyboard visualizer.

    python main.py                 tray app + settings window
    python main.py --hidden        tray app only (good for starting with Windows)
    python main.py --headless      no UI at all; Ctrl+C to stop
    python main.py --quit          close a running copy (restores your lighting)
    python main.py --list-devices  show audio outputs you can capture from

Double-click "Predator Visualizer.pyw" to start it without a console window.
"""

import argparse
import logging
import time


def main():
    ap = argparse.ArgumentParser(description="Acer Predator 4-zone keyboard audio visualizer")
    ap.add_argument("--hidden", action="store_true", help="start in the tray without opening the window")
    ap.add_argument("--headless", action="store_true", help="run without any UI using saved settings")
    ap.add_argument("--quit", action="store_true", help="close a running visualizer (restores lighting)")
    ap.add_argument("--list-devices", action="store_true", help="list audio loopback devices and exit")
    args = ap.parse_args()

    if args.list_devices:
        from audio_capture import list_loopback_devices
        for d in list_loopback_devices():
            print(f"[{d['index']:>2}] {d['name']}  ({d['channels']} ch, {d['rate']} Hz)")
        return

    if args.headless:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
        from engine import Engine
        from settings import SettingsStore
        engine = Engine(SettingsStore())
        engine.start()
        print("Running with saved settings. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            engine.stop()
            print("Stopped, keyboard lighting restored.")
        return

    import tray_app
    tray_app.main(start_hidden=args.hidden, quit_running=args.quit)


if __name__ == "__main__":
    main()
