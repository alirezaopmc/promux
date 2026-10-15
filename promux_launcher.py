#!/usr/bin/env python3
"""
Promux Windows Root Launcher
Safeguards stdout/stderr for frozen/windowed PyInstaller execution.
"""
import sys
import io

# Safeguard against None stdout/stderr in GUI/windowed mode
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

from promux_win.cli import main

if __name__ == "__main__":
    sys.exit(main())
