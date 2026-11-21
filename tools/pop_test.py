"""
pop_test.py - Find out what causes audio pops, by ear, in about 2 minutes.

Play steady music (or a sine-tone video) through your normal output, then run:

    python tools/pop_test.py

It runs 5 phases of 20 seconds each and tells you which one is active.
Write down the phase numbers where you hear pops or clicks.

  1. Nothing running              -> baseline (pops here = not this app at all)
  2. Audio capture only           -> pops here = WASAPI loopback / Wave Link
  3. Keyboard writes, 10 per sec  -> pops here = the EC / PredatorSense path
  4. Keyboard writes, 20 per sec
  5. Keyboard writes, 40 per sec  -> if pops get worse as the rate rises, keep the rate low

Close the visualizer app first (tray icon -> Quit) so it doesn't interfere.
"""

import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import math
import sys
import time

PHASE_SECONDS = 20


def countdown(label, seconds, tick=None):
    end = time.perf_counter() + seconds
    while True:
        left = end - time.perf_counter()
        if left <= 0:
            break
        sys.stdout.write(f"\r   {label}: {left:4.1f}s left   ")
        sys.stdout.flush()
        if tick:
            tick()
        else:
            time.sleep(0.05)
    print()


def keyboard_phase(dev, hz):
    from openrgb.utils import RGBColor
    t0 = time.perf_counter()
    state = {"next": t0}

    def tick():
        now = time.perf_counter()
        if now >= state["next"]:
            ph = (now - t0) * 3
            cols = [RGBColor(int(127 + 127 * math.sin(ph + i)), 40, int(127 + 127 * math.cos(ph + i)))
                    for i in range(4)]
            dev.set_colors(cols, fast=True)
            state["next"] += 1.0 / hz
        time.sleep(0.002)

    return tick


def main():
    print(__doc__)
    input("Start your music, then press ENTER... ")

    print("\n[1/5] Nothing running. Listen for pops.")
    countdown("baseline", PHASE_SECONDS)

    print("\n[2/5] Audio capture only (no keyboard writes).")
    from audio_capture import AudioCapture
    cap = AudioCapture("auto")
    cap.start()
    print(f"   capturing from: {cap.device_name}")
    countdown("capture", PHASE_SECONDS)
    cap.stop()

    from openrgb import OpenRGBClient
    client = OpenRGBClient("127.0.0.1", 6742, name="PopTest")
    dev = client.devices[0]
    original = (dev.active_mode, list(dev.colors))
    dev.set_mode(0)
    try:
        for n, hz in ((3, 10), (4, 20), (5, 40)):
            print(f"\n[{n}/5] Keyboard writes at {hz} per second (no audio capture).")
            countdown(f"{hz}/s", PHASE_SECONDS, keyboard_phase(dev, hz))
    finally:
        dev.set_colors(original[1], fast=True)
        dev.set_mode(original[0])
        client.disconnect()

    print("\nDone. Which phases had pops? (e.g. 'none', '3 4 5', 'all')")


if __name__ == "__main__":
    main()
