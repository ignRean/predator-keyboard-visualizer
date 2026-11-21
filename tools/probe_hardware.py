"""
Phase 0: Hardware Probe for Acer Predator PHN16-71 Keyboard RGB
Verified device: 'AcerECKeyboard Device' via PredatorSense embedded OpenRGB SDK server.

Configuration:
- Device Name: AcerECKeyboard Device
- Mode 0: 'Direct' mode (supports direct real-time per-LED/zone color updates)
- 4 Addressable Zones (LED 0 to LED 3):
    * LED 0: Keyboard Backlight zone 1 (Left / Zone 1)
    * LED 1: Keyboard Backlight zone 2 (Middle-Left / Zone 2)
    * LED 2: Keyboard Backlight zone 3 (Middle-Right / Zone 3)
    * LED 3: Keyboard Backlight zone 4 (Numpad-Right / Zone 4)
"""

import sys
import time
from openrgb import OpenRGBClient
from openrgb.utils import RGBColor, DeviceType

def run_probe():
    print("=" * 65)
    print(" Acer Predator PHN16-71 - RGB Hardware Probe (Phase 0)")
    print("=" * 65)
    print("[*] Connecting to OpenRGB SDK server at 127.0.0.1:6742...")

    try:
        client = OpenRGBClient("127.0.0.1", 6742, name="PredatorProbe")
    except Exception as e:
        print(f"[!] Failed to connect to OpenRGB server: {e}")
        print("    Ensure PredatorSense is running with lighting active.")
        return False

    print(f"[+] Connected successfully! Protocol Version: {client.protocol_version}")
    dev = client.devices[0]
    print(f"[+] Target Device: '{dev.name}'")
    print(f"    Initial Mode: {dev.modes[dev.active_mode].name} (ID: {dev.active_mode})")
    print(f"    Total LEDs/Zones: {len(dev.leds)}")

    for i, led in enumerate(dev.leds):
        print(f"    - LED {i}: '{led.name}' | Color: {dev.colors[i]}")

    initial_mode = dev.active_mode
    initial_colors = list(dev.colors)

    print("\n" + "=" * 65)
    print(" Testing Real-Time 4-Zone Lighting")
    print("=" * 65)

    # Switch to Direct mode (ID 0) so direct color arrays apply immediately
    if dev.active_mode != 0:
        print("[*] Switching keyboard to Mode 0 ('Direct')...")
        dev.set_mode(0)

    test_palette = [
        RGBColor(255, 0, 0),     # Zone 1 (Left): Red
        RGBColor(0, 255, 0),     # Zone 2 (Mid-Left): Green
        RGBColor(0, 120, 255),   # Zone 3 (Mid-Right): Blue
        RGBColor(255, 200, 0),   # Zone 4 (Numpad): Amber / Yellow
    ]

    print("[*] Setting distinct colors across all 4 keyboard zones:")
    print("    Zone 1 (Left):         RED      (255, 0, 0)")
    print("    Zone 2 (Mid-Left):     GREEN    (0, 255, 0)")
    print("    Zone 3 (Mid-Right):    BLUE     (0, 120, 255)")
    print("    Zone 4 (Numpad/Right): AMBER    (255, 200, 0)")

    dev.set_colors(test_palette)
    print("\n[+] 4-Zone colors dispatched! Please look at your laptop keyboard.")
    print("[*] Holding for 3 seconds...")
    time.sleep(3)

    print("\n[*] Running smooth wave test across 4 zones...")
    for _ in range(3):
        for step in range(4):
            wave = [RGBColor(10, 10, 10)] * 4
            wave[step] = RGBColor(0, 255, 255)  # Cyan sweep
            dev.set_colors(wave)
            time.sleep(0.15)

    print("\n[*] Restoring original settings...")
    dev.set_colors(initial_colors)
    dev.set_mode(initial_mode)
    time.sleep(0.5)

    client.disconnect()
    print("[+] Phase 0 Probe successfully verified!")
    return True

if __name__ == "__main__":
    success = run_probe()
    sys.exit(0 if success else 1)
