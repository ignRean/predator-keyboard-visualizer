"""
Does dimming one solid color via the mode's hardware brightness freeze the PC less
than sending colors? Compares, at 10 updates/s:
  A) Direct mode, solid color, brightness done by scaling RGB (set_colors)
  B) STATIC mode, fixed color, brightness via the mode's brightness value (UPDATEMODE)
Run from the project folder with the visualizer closed:  python tests/brightness_probe.py
"""
import math
import multiprocessing as mp
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.ec_stall_probe import CORES, spinner  # noqa: E402
from tools.wmi_led_probe import overlaps  # noqa: E402

PHASE_S, TAIL_S, HZ = 8.0, 2.0, 10


def send_mode(dev, mode):
    """Raw UPDATEMODE packet: same as dev.set_mode() without its blocking read-back loop."""
    from openrgb import utils
    data = mode.pack(dev.comms._protocol_version)
    dev.comms.send_header(dev.id, utils.PacketType.RGBCONTROLLER_UPDATEMODE, len(data))
    dev.comms.send_data(data)


def main():
    from openrgb import OpenRGBClient
    from openrgb.utils import RGBColor
    client = OpenRGBClient("127.0.0.1", 6742, name="BrightnessProbe")
    dev = client.devices[0]
    original = (dev.active_mode, list(dev.colors))
    static = next(m for m in dev.modes if m.name.upper() == "STATIC")

    stop_at = time.perf_counter_ns() + int((3 * (PHASE_S + TAIL_S) + 3) * 1e9)
    q = mp.Queue()
    procs = [mp.Process(target=spinner, args=(c, stop_at, q)) for c in CORES]
    for p in procs:
        p.start()
    time.sleep(1.0)

    phases = []

    def run(name, setup, step):
        print(f"  {name} ...", flush=True)
        setup()
        time.sleep(TAIL_S)  # let the setup's own activity finish
        ps = time.perf_counter_ns()
        n, nxt = 0, ps
        while (now := time.perf_counter_ns()) < ps + PHASE_S * 1e9:
            if now >= nxt:
                step(0.5 + 0.5 * math.sin(n * 0.9))
                n += 1
                nxt += int(1e9 / HZ)
            time.sleep(0.0005)
        phases.append((name, ps, time.perf_counter_ns(), n))

    try:
        run("Direct, idle (baseline)", lambda: dev.set_mode(0), lambda v: None)
        run("Direct, solid color dimmed by RGB",
            lambda: dev.set_mode(0),
            lambda v: dev.set_colors([RGBColor(int(255 * v), 0, int(120 * v))] * 4, fast=True))

        def static_setup():
            static.colors = [RGBColor(255, 0, 120)]
            dev.set_mode(static)

        def static_step(v):
            static.brightness = int(static.brightness_min + v * (static.brightness_max - static.brightness_min))
            send_mode(dev, static)

        run("STATIC, brightness via mode", static_setup, static_step)
    finally:
        dev.set_mode(original[0])
        if dev.modes[original[0]].name == "Direct":
            dev.set_colors(original[1], fast=True)
        client.disconnect()

    gaps = dict(q.get() for _ in procs)
    for p in procs:
        p.join()
    st = overlaps(*gaps.values())
    print(f"\n  {'phase':<36}{'updates':>8}{'freezes/s':>11}{'frozen ms/s':>13}")
    for name, ps, pe, n in phases:
        inside = [d for s, d in st if ps <= s < pe]
        secs = (pe - ps) / 1e9
        print(f"  {name:<36}{n:>8}{len(inside) / secs:>11.1f}{sum(inside) / 1e6 / secs:>13.1f}")


if __name__ == "__main__":
    main()
