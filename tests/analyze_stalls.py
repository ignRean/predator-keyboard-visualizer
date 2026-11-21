"""Summarize ec_stall_probe.py output: system-wide stalls per phase and their timing relative to writes."""
import bisect
import json
import sys

import numpy as np

d = json.load(open(sys.argv[1]))
cores = list(d["gaps"].values())
writes = d["writes"]


def overlaps(a, b):
    """Intervals (start, dur) present on both cores at the same time -> system-wide stalls."""
    out, j = [], 0
    b = sorted(b)
    for s, du in sorted(a):
        e = s + du
        while j < len(b) and b[j][0] + b[j][1] < s:
            j += 1
        k = j
        while k < len(b) and b[k][0] < e:
            bs, bd = b[k]
            lo, hi = max(s, bs), min(e, bs + bd)
            if hi > lo:
                out.append((lo, hi - lo))
            k += 1
    return out


stalls = overlaps(cores[0], cores[1])
print(f"per-core gaps: {[len(c) for c in cores]}, system-wide stalls: {len(stalls)}\n")
print(f"{'phase':<30}{'writes':>7}{'stalls':>8}{'/write':>8}{'med ms':>8}{'max ms':>8}{'ms/s':>7}{'tail':>6}{'lag med':>9}{'lag p90':>9}")
for p in d["phases"]:
    inside = [s for s in stalls if p["start"] <= s[0] < p["end"]]
    tail = [s for s in stalls if p["end"] <= s[0] < p["tail_end"]]
    secs = (p["end"] - p["start"]) / 1e9
    durs = np.array([s[1] for s in inside]) / 1e6 if inside else np.array([0.0])
    lags = []
    for s, _ in inside:
        i = bisect.bisect_right(writes, s) - 1
        if i >= 0:
            lags.append((s - writes[i]) / 1e6)
    lag_med = f"{np.median(lags):.1f}" if lags else "-"
    lag_p90 = f"{np.percentile(lags, 90):.1f}" if lags else "-"
    per_write = len(inside) / p["writes"] if p["writes"] else float("nan")
    print(f"{p['name']:<30}{p['writes']:>7}{len(inside):>8}{per_write:>8.2f}{np.median(durs):>8.2f}{durs.max():>8.2f}"
          f"{durs.sum() / secs:>7.1f}{len(tail):>6}{lag_med:>9}{lag_p90:>9}")
