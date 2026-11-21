# Keyboard lighting and audio pops: what we measured (2026-11-22)

Laptop: Acer Predator Helios Neo 16 (PHN16-71), PredatorSense with its bundled OpenRGB server.
Everything below was measured on the real hardware.

## The path a color takes
```
visualizer -> OpenRGB.exe (TCP 6742) -> AcerLightingService (SYSTEM) -> Acer driver -> keyboard
```

## Test 1: does writing freeze the machine? (`tools/ec_stall_probe.py`)
Two processes pinned to different CPU cores spin on the clock. A gap seen on both cores at the
same moment is a system-wide freeze (firmware SMI). That kind of freeze starves the audio path and pops.

| Phase (10 s each) | Freezes | Frozen time |
|---|---|---|
| PredatorSense running its own effect | 0 | 0 ms/s |
| Direct mode, no writes | ~0 | ~4 ms/s |
| 5, 10, 20 or 40 writes/s | ~16/s, ~6 ms each | ~90 ms/s |
| Same color resent 20/s | ~16/s | ~89 ms/s |
| 10-write burst, then 1 s pause | ~7 per burst | ~37 ms/s |
| 3 s after writes stop | ~0 | none |

Raw data: `docs/findings/stall_probe_clean.json`.

**Conclusions**
- Any write wakes AcerLightingService. For about 0.45 s it then pushes to the keyboard on a fixed
  ~64 ms tick (about 15.6/s), and **every tick freezes all cores for about 6 ms**.
- The freeze cost doesn't depend on how fast we write. Writes faster than the tick are merged
  into the next tick. They don't pile up and nothing lags behind (no backlog after stopping).
- With the visualizer reacting to music, the machine is frozen about 9% of the time.
  Measured with the real engine: 14.3 writes/s gave 14.8 freezes/s, 88 ms/s.
- Steady or dark lights cost nothing, as long as nothing is written.

## Test 1b: does a single solid color dimmed by brightness cost less? (`tests/brightness_probe.py`)
10 updates/s for 8 s each:

| Method | Freezes/s | Frozen ms/s |
|---|---|---|
| Direct mode, idle | 0.4 | 0.1 |
| Direct mode, one solid color dimmed by scaling RGB | 18.5 | 93.8 |
| STATIC mode, fixed color, hardware brightness changed via the mode | 18.7 | 94.5 |

No. The cost is in waking the service, not in what you send: color, brightness, one zone or
four all cost the same.

## Test 2: can we bypass the service? (`tools/wmi_led_probe.py`, needs admin)
`root\WMI\AcerGamingFunction` exists. `SetGamingRgbKb` (WmiMethodId 6, UInt64 =
zone | R<<8 | G<<16 | B<<24) is accepted (return 0, 14-24 ms per call), and the value reads back
correctly. **But the LEDs never change**: on this model the firmware only stores the state, and
Acer's service and driver do the actual push. `SetGamingKBBacklight` rejected our 16-byte static
mode (return 1). `SetGamingLED` is for other LEDs (12-byte array). Raw logs: `docs/findings/wmi_probe_results*.txt`.

So there is no supported way to drive the keyboard without the service and its freezes.

## Other findings
- openrgb-python `set_colors()` asks the server for the full device state after every write
  unless `fast=True`. The old code doubled its traffic this way.
- OpenRGB's Direct mode starts at hardware brightness 50 of 100; the visualizer now raises it to 100.
- The old auto-detect picked the silent Headphones endpoint while audio played on Speakers.

## What the app does now (`keyboard.py`)
- At most one write per service tick (15/s); the slider range is 4-15.
- No keepalive rewrites; changes smaller than 10/255 per channel are skipped.
- Dark or steady lights mean no writes at all, so no freezes.

## Result: Wave Link is the weak spot (confirmed by ear, both directions)
6 ms freezes only pop if the playback path has less than about 6 ms of slack.
- Windows output set to **Headphones (Realtek)** directly, visualizer running: **no pops**.
- Switched back to **Elgato Wave Link**, visualizer running: **pops return**.

The freezes happen either way; Wave Link's virtual mixer just can't ride through them.
To avoid pops, either play music straight to the Realtek output, or pause the visualizer
(tray -> On) while audio goes through Wave Link. A paused visualizer sends nothing, so there are no freezes.
