"""
Promux Windows Application Launcher
Single-instance aware desktop launcher with native app window support.
Provides instantaneous, zero-conflict window loading on all Windows systems.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# Safeguard stdout/stderr for PyInstaller windowed mode
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

from .server import create_server

DEFAULT_PORT = 18420


def is_server_alive(port: int = DEFAULT_PORT) -> bool:
    try:
        url = f"http://127.0.0.1:{port}/api/status"
        req = urllib.request.Request(url, headers={"User-Agent": "PromuxLauncher"})
        with urllib.request.urlopen(req, timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def find_free_port(preferred: int = DEFAULT_PORT) -> int:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", preferred))
            return preferred
    except OSError:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def launch_native_app_window(url: str) -> bool:
    """Launch native borderless application window using Chrome or Edge, or fallback to browser."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("alirezaopmc.promux.windows.gui")
    except Exception:
        pass

    user_data_dir = os.path.expandvars(r"%LOCALAPPDATA%\Promux\BrowserProfile")

    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        shutil.which("chrome"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        shutil.which("msedge"),
    ]

    for exe in candidates:
        if exe and os.path.exists(exe):
            cmd = [
                exe,
                f"--app={url}",
                f"--user-data-dir={user_data_dir}",
                "--app-id=promux_desktop_gui",
                "--window-size=1120,740",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            try:
                proc = subprocess.Popen(cmd)
                time.sleep(0.3)
                poll = proc.poll()
                if poll is not None and poll != 0:
                    continue
                return True
            except Exception:
                continue

    # Fallback to default browser
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Promux Windows — Antigravity Account Multiplexer")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Server port (default: 18420)")
    parser.add_argument("--browser", action="store_true", help="Launch in default browser tab")
    parser.add_argument("--no-window", action="store_true", help="Run background server only without opening UI")
    args = parser.parse_args(argv)

    # 1. Single Instance Check: If server already active, just bring up / launch window!
    if is_server_alive(args.port):
        url = f"http://127.0.0.1:{args.port}"
        if not args.no_window:
            if args.browser:
                webbrowser.open(url)
            else:
                launch_native_app_window(url)
        return 0

    # 2. Bind and Start Background HTTP Server
    port = find_free_port(args.port)
    server = create_server("127.0.0.1", port)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{port}"

    # Write status file for CLI / status inspection
    state_file = Path.home() / ".promux" / "server.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        state_file.write_text(json.dumps({"port": port, "url": url, "pid": os.getpid()}), encoding="utf-8")
    except Exception:
        pass

    # 3. Launch UI Window if requested
    if not args.no_window:
        if args.browser:
            webbrowser.open(url)
        else:
            launch_native_app_window(url)

    # 4. Keep background server and auto-failover daemon alive
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        server.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
