"""
ec_stall_probe.py - Do keyboard writes freeze the machine, and do they back up when sent fast?

Two watcher processes, pinned to different CPU cores, spin on the high-resolution
clock and record every gap. A gap seen on BOTH cores at the same moment is a
system-wide stall (e.g. an SMI from the embedded controller) - exactly the kind
of thing that starves the audio driver and makes a pop. A gap on one core only
is normal Windows scheduling.

Phases send keyboard writes at different rates/patterns, each followed by a quiet
tail. If stalls keep happening in the tail, writes were queued and still being
worked off after we stopped sending (a backlog).

    python tools/ec_stall_probe.py          (quit the visualizer first)
"""

import ctypes
import json
import multiprocessing as mp
import sys
import time

GAP_NS = 300_000          # record gaps longer than 0.3 ms
PHASE_S = 10.0
TAIL_S = 3.0
CORES = (2, 5)

PHASES = [
    ("PredatorSense own effect",    0,  "native"),
    ("Direct mode, no writes",      0,  "all"),
    ("5/s, all zones change",       5,  "all"),
    ("10/s, all zones change",      10, "all"),
    ("20/s, all zones change",      20, "all"),
    ("40/s, all zones change",      40, "all"),
    ("20/s, one zone changes",      20, "one"),
    ("20/s, same color resent",     20, "same"),
    ("bursts of 10 back-to-back",   -1, "all"),
]


def spinner(core, stop_at_ns, out):
    k32 = ctypes.windll.kernel32
    k32.GetCurrentThread.restype = ctypes.c_void_p
    k32.SetThreadAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k32.SetPriorityClass(k32.GetCurrentProcess(), 0x80)            # HIGH
    k32.SetThreadAffinityMask(k32.GetCurrentThread(), 1 << core)
    k32.SetThreadPriority(k32.GetCurrentThread(), 15)              # TIME_CRITICAL
    pc = time.perf_counter_ns
    gaps = []
    last = pc()
    while last < stop_at_ns:
        t = pc()
        if t - last > GAP_NS:
            gaps.append((last, t - last))
        last = t
    out.put((core, gaps))


def colors_for(i, pattern):
    from openrgb.utils import RGBColor
    if pattern == "same":
        return [RGBColor(0, 120, 255)] * 4
    v = (i * 97) % 256
    if pattern == "one":
        return [RGBColor(v, 0, 255 - v), RGBColor(0, 120, 255), RGBColor(0, 120, 255), RGBColor(0, 120, 255)]
    return [RGBColor(v, (v * 3) % 256, 255 - v) for _ in range(4)]


def main():
    from openrgb import OpenRGBClient
    client = OpenRGBClient("127.0.0.1", 6742, name="StallProbe")
    dev = client.devices[0]
    original = (dev.active_mode, list(dev.colors))

    total = len(PHASES) * (PHASE_S + TAIL_S) + 2.0
    t0 = time.perf_counter_ns()
    stop_at = t0 + int(total * 1e9)
    q = mp.Queue()
    procs = [mp.Process(target=spinner, args=(c, stop_at, q)) for c in CORES]
    for p in procs:
        p.start()
    time.sleep(1.0)

    writes, phases = [], []
    try:
        for name, hz, pattern in PHASES:
            print(f"  {name} ...", flush=True)
            if pattern != "native" and dev.active_mode != 0:
                dev.set_mode(0)
            ps = time.perf_counter_ns()
            end = ps + int(PHASE_S * 1e9)
            i = 0
            if hz > 0:
                nxt = ps
                while (now := time.perf_counter_ns()) < end:
                    if now >= nxt:
                        dev.set_colors(colors_for(i, pattern), fast=True)
                        writes.append(time.perf_counter_ns())
                        i += 1
                        nxt += int(1e9 / hz)
                    time.sleep(0.0005)
            elif hz < 0:
                while time.perf_counter_ns() < end:
                    for _ in range(10):
                        dev.set_colors(colors_for(i, pattern), fast=True)
                        writes.append(time.perf_counter_ns())
                        i += 1
                    time.sleep(1.0)
            else:
                time.sleep(PHASE_S)
            pe = time.perf_counter_ns()
            time.sleep(TAIL_S)
            phases.append({"name": name, "start": ps, "end": pe, "tail_end": time.perf_counter_ns(), "writes": i})
    finally:
        dev.set_colors(original[1], fast=True)
        dev.set_mode(original[0])
        client.disconnect()

    results = dict(q.get() for _ in procs)
    for p in procs:
        p.join()
    data = {"phases": phases, "writes": writes, "gaps": {str(k): v for k, v in results.items()}}
    out = sys.argv[1] if len(sys.argv) > 1 else "stall_results.json"
    with open(out, "w") as f:
        json.dump(data, f)
    print("saved", out)


if __name__ == "__main__":
    main()
