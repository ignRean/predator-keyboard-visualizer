"""Double-click to start the visualizer in the tray (no console window)."""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
os.chdir(here)
sys.path.insert(0, here)

import tray_app  # noqa: E402

tray_app.main(start_hidden="--hidden" in sys.argv)
