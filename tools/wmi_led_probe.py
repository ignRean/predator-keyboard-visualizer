"""
wmi_led_probe.py - Test setting keyboard zones directly through Acer's firmware (WMI),
bypassing OpenRGB / AcerLightingService and their 64 ms update clock.

Must run as Administrator:
    1. Start menu -> right-click "Terminal" -> "Run as administrator"
    2. cd into the project folder
    3. python tools/wmi_led_probe.py

Only keyboard lighting methods are used (Get/SetGamingRgbKb, GetGamingLED, GetGamingKBBacklight).
Results are saved to wmi_probe_results.txt so Claude can read them.
"""

import ctypes
import json
import multiprocessing as mp
import statistics
import sys
import time

import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from tools.ec_stall_probe import CORES, spinner

RESULTS = "wmi_probe_results.txt"
ZONES = [1, 2, 4, 8]          # bitmask per zone, left -> numpad
log_lines = []


def log(msg=""):
    print(msg, flush=True)
    log_lines.append(str(msg))


def save():
    with open(RESULTS, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")


class AcerWMI:
    def __init__(self):
        import win32com.client
        svc = win32com.client.GetObject("winmgmts:root\\WMI")
        insts = list(svc.InstancesOf("AcerGamingFunction"))
        if not insts:
            raise RuntimeError("AcerGamingFunction has no instance")
        self.obj = insts[0]

    def _call(self, method, **params):
        m = self.obj.Methods_(method)
        ip = m.InParameters.SpawnInstance_()
        for k, v in params.items():
            setattr(ip, k, v)
        return self.obj.ExecMethod_(method, ip)

    def get_led(self, zone_mask):
        out = self._call("GetGamingLED", gmInput=zone_mask)
        return list(out.gmOutput or []), out.gmReturn

    def get_kb_backlight(self, arg=1):
        out = self._call("GetGamingKBBacklight", gmInput=arg)
        return list(out.gmOutput or []), out.gmReturn

    # SetGamingRgbKb (WmiMethodId 6) takes one UInt64 = zone_mask | R<<8 | G<<16 | B<<24.
    # WMI scripting usually wants uint64 as a string; discovered at runtime.
    fmt = None

    def get_rgbkb(self, arg):
        return self._call("GetGamingRgbKb", gmInput=arg).gmOutput

    def set_raw(self, value, how):
        m = self.obj.Methods_("SetGamingRgbKb")
        ip = m.InParameters.SpawnInstance_()
        ip.Properties_.Item("gmInput").Value = str(value) if how == "str" else int(value)
        return self.obj.ExecMethod_("SetGamingRgbKb", ip).gmOutput

    def set_led(self, zone_mask, r, g, b):
        return self.set_raw(zone_mask | (r << 8) | (g << 16) | (b << 24), self.fmt)


def set_kb_backlight(wmi, vals16):
    """SetGamingKBBacklight takes exactly 16 bytes; try COM encodings until one is accepted."""
    import pythoncom
    import win32com.client
    for how in ("variant", "list"):
        try:
            m = wmi.obj.Methods_("SetGamingKBBacklight")
            ip = m.InParameters.SpawnInstance_()
            ip.Properties_.Item("gmInput").Value = (
                win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_UI1, bytes(vals16))
                if how == "variant" else list(vals16))
            t = time.perf_counter()
            ret = wmi.obj.ExecMethod_("SetGamingKBBacklight", ip).gmOutput
            log(f"  SetGamingKBBacklight({vals16[:9]}...) as {how}: return={ret}, "
                f"took {(time.perf_counter() - t) * 1000:.1f} ms")
            return True
        except Exception as e:
            log(f"  SetGamingKBBacklight as {how}: ERROR {e!r}")
    return False


def rgbkb_hex(wmi, z):
    v = int(wmi.get_rgbkb(z))
    return f"{v:#010x} (R={(v >> 8) & 255}, G={(v >> 16) & 255}, B={v >> 24 & 255}, b0={v & 255})"


def try_zone(wmi, label, z=1, rgb=(255, 0, 0), question="Is the LEFT zone red now?"):
    """Write one zone, read it back now and after 1 s, ask the user. Returns True if seen."""
    wmi.fmt = "int"
    value = z | (rgb[0] << 8) | (rgb[1] << 16) | (rgb[2] << 24)
    t = time.perf_counter()
    ret = wmi.set_raw(value, "int")
    ms = (time.perf_counter() - t) * 1000
    now = rgbkb_hex(wmi, z)
    time.sleep(1.0)
    later = rgbkb_hex(wmi, z)
    log(f"  {label}: sent {value:#010x}, return={ret}, took {ms:.1f} ms")
    log(f"     read back now:   {now}")
    log(f"     read back +1 s:  {later}")
    log(f"     KBBacklight now: {wmi.get_kb_backlight(1)}")
    ans = input(f"  {label}: {question} (y/n) ").strip().lower()
    log(f"     user saw it: {ans}")
    save()
    return ans == "y"


def find_set_format(wmi, dev):
    """Find a way to make a single zone change color."""
    # A: OpenRGB Direct mode (what the visualizer uses today), then a direct zone write.
    dev.set_mode(0)
    time.sleep(0.5)
    if try_zone(wmi, "A (OpenRGB Direct mode)"):
        return True
    # B: OpenRGB disconnected from the picture: put the firmware itself in static mode.
    #    Byte layout read from this laptop: [mode, speed, brightness, 0, direction, R, G, B, 3, 0...]
    log("\n  B: setting firmware static mode, whole keyboard blue, brightness 100")
    if set_kb_backlight(wmi, [0, 0, 100, 0, 0, 0, 0, 255, 3, 0, 0, 0, 0, 0, 0, 0]):
        ans = input("  B: Is the WHOLE keyboard blue now? (y/n) ").strip().lower()
        log(f"     user saw whole keyboard blue: {ans}")
        log(f"     KBBacklight now: {wmi.get_kb_backlight(1)}")
        if try_zone(wmi, "B (firmware static mode)"):
            return True
        # C: some firmwares only show per-zone colors with byte 8 = 0 (per-zone) instead of 3.
        log("\n  C: static mode with byte 8 = 0")
        set_kb_backlight(wmi, [0, 0, 100, 0, 0, 0, 0, 255, 0, 0, 0, 0, 0, 0, 0, 0])
        if try_zone(wmi, "C (static, byte8=0)"):
            return True
    return False


def overlaps(a, b):
    out, j = [], 0
    b = sorted(b)
    for s, du in sorted(a):
        e = s + du
        while j < len(b) and b[j][0] + b[j][1] < s:
            j += 1
        k = j
        while k < len(b) and b[k][0] < e:
            lo, hi = max(s, b[k][0]), min(e, b[k][0] + b[k][1])
            if hi > lo:
                out.append((lo, hi - lo))
            k += 1
    return out


def main():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        print("Please run this from an Administrator terminal (see the top of this file).")
        sys.exit(1)

    from openrgb import OpenRGBClient
    client = OpenRGBClient("127.0.0.1", 6742, name="WmiProbe")
    dev = client.devices[0]
    original = (dev.active_mode, list(dev.colors))
    log(f"OpenRGB/PredatorSense mode before test: {dev.modes[dev.active_mode].name}")

    wmi = AcerWMI()
    try:
        # ---- 1. read-only
        log("\n[1] Reading current state from firmware")
        for z in ZONES + [15]:
            try:
                log(f"  GetGamingRgbKb({z}) -> {wmi.get_rgbkb(z):#x}" if isinstance(wmi.get_rgbkb(z), int)
                    else f"  GetGamingRgbKb({z}) -> {wmi.get_rgbkb(z)!r}")
            except Exception as e:
                log(f"  GetGamingRgbKb({z}) failed: {e}")
        for a in (0, 1):
            try:
                log(f"  GetGamingKBBacklight({a}) -> {wmi.get_kb_backlight(a)}")
            except Exception as e:
                log(f"  GetGamingKBBacklight({a}) failed: {e}")

        # ---- 2. confirm format, zone by zone
        log("\n[2] Setting zones directly. Watch the keyboard.")
        if not find_set_format(wmi, dev):
            log("  No approach made a single zone change.")
            return
        log(f"  Using format {wmi.fmt}")
        t = time.perf_counter()
        ret = wmi.set_led(15, 0, 0, 0)
        log(f"  all zones off: return={ret}, took {(time.perf_counter() - t) * 1000:.1f} ms")
        names = ["left", "middle-left", "middle-right", "numpad"]
        for i, z in enumerate(ZONES):
            t = time.perf_counter()
            ret = wmi.set_led(z, 255, 0, 0)
            ms = (time.perf_counter() - t) * 1000
            ans = input(f"  Only the {names[i]} zone should be RED now. Correct? (y/n) ").strip().lower()
            log(f"  zone mask {z}: return={ret}, took {ms:.1f} ms, user saw correct: {ans}")
            wmi.set_led(z, 0, 0, 0)
        save()
        if input("\nDid the direct zone colors work? Continue to the timing test? (y/n) ").strip().lower() != "y":
            log("Stopped after format check.")
            return

        # ---- 3. stall + timing test
        log("\n[3] Freeze test with direct firmware writes (about 1 minute, keyboard will flash)")
        phases = [("idle", 0, 1), ("5/s, one call all zones", 5, 1), ("15/s, one call all zones", 15, 1),
                  ("30/s, one call all zones", 30, 1), ("15/s, 4 calls (per zone)", 15, 4)]
        PHASE_S, TAIL_S = 8.0, 2.0
        t0 = time.perf_counter_ns()
        stop_at = t0 + int((len(phases) * (PHASE_S + TAIL_S) + 2.0) * 1e9)
        q = mp.Queue()
        procs = [mp.Process(target=spinner, args=(c, stop_at, q)) for c in CORES]
        for p in procs:
            p.start()
        time.sleep(1.0)
        info = []
        for name, hz, calls in phases:
            print(f"  {name} ...", flush=True)
            ps = time.perf_counter_ns()
            end = ps + int(PHASE_S * 1e9)
            durs, n = [], 0
            if hz:
                nxt = ps
                while (now := time.perf_counter_ns()) < end:
                    if now >= nxt:
                        v = (n * 97) % 256
                        for k in range(calls):
                            mask = 15 if calls == 1 else ZONES[k]
                            a = time.perf_counter()
                            wmi.set_led(mask, v, (v * 3) % 256, 255 - v)
                            durs.append((time.perf_counter() - a) * 1000)
                        n += 1
                        nxt += int(1e9 / hz)
                    time.sleep(0.0005)
            else:
                time.sleep(PHASE_S)
            pe = time.perf_counter_ns()
            time.sleep(TAIL_S)
            info.append((name, ps, pe, n, durs))
        gaps = dict(q.get() for _ in procs)
        for p in procs:
            p.join()
        stalls = overlaps(*gaps.values())
        log(f"  {'phase':<28}{'updates':>8}{'call ms med/max':>18}{'freezes':>9}{'med ms':>8}{'frozen ms/s':>13}")
        for name, ps, pe, n, durs in info:
            inside = [s for s in stalls if ps <= s[0] < pe]
            d = [s[1] / 1e6 for s in inside] or [0.0]
            cm = f"{statistics.median(durs):.1f}/{max(durs):.1f}" if durs else "-"
            log(f"  {name:<28}{n:>8}{cm:>18}{len(inside):>9}{statistics.median(d):>8.2f}"
                f"{sum(d) / ((pe - ps) / 1e9):>13.1f}")
    except Exception:
        import traceback
        log("\nERROR:\n" + traceback.format_exc())
    finally:
        try:
            dev.set_colors(original[1], fast=True)
            dev.set_mode(original[0])
            log(f"\nRestored PredatorSense mode: {dev.modes[original[0]].name}")
        except Exception as e:
            log(f"\nCould not restore lighting automatically ({e}) - pick your effect in PredatorSense.")
        client.disconnect()
        save()
        print(f"\nSaved results to {RESULTS}. Tell Claude it's done.")


if __name__ == "__main__":
    main()
