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
import traceback
import urllib.request
import webbrowser
from pathlib import Path

# Safeguard stdout/stderr for PyInstaller windowed mode
if sys.stdout is None or getattr(sys.stdout, "closed", False):
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None or getattr(sys.stderr, "closed", False):
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

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


def _log(msg: str) -> None:
    try:
        log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Promux"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "promux.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{os.getpid()}] {msg}\n")
    except Exception:
        pass


def launch_native_app_window(url: str, prefer_edge: bool = False) -> bool:
    """Launch native borderless application window using Chrome or Edge, or fallback to browser."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("alirezaopmc.promux.windows.gui")
    except Exception:
        pass

    edge_candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        shutil.which("msedge"),
    ]
    chrome_candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        shutil.which("chrome"),
    ]

    candidates = (edge_candidates + chrome_candidates) if prefer_edge else (chrome_candidates + edge_candidates)
    _log(f"launch_native_app_window: url={url}, prefer_edge={prefer_edge}")

    for exe in candidates:
        if exe and os.path.exists(exe):
            cmd = [
                exe,
                f"--app={url}",
                "--window-size=1180,780",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            _log(f"launch_native_app_window: launching {exe}")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(0.3)
                poll = proc.poll()
                _log(f"launch_native_app_window: proc.poll()={poll}")
                if poll is not None and poll != 0:
                    continue
                return True
            except Exception as e:
                _log(f"launch_native_app_window: exception {e}")
                continue

    # Fallback to default browser
    _log("launch_native_app_window: falling back to webbrowser.open")
    try:
        webbrowser.open(url)
        return True
    except Exception as e:
        _log(f"launch_native_app_window: webbrowser error {e}")
        return False


def main(argv: list[str] | None = None) -> int:
    _log(f"app.py: main() entered with argv={argv}")
    parser = argparse.ArgumentParser(description="Promux Windows — Antigravity Account Multiplexer")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Server port (default: 18420)")
    parser.add_argument("--browser", action="store_true", help="Launch in default browser tab")
    parser.add_argument("--edge", action="store_true", help="Prefer Microsoft Edge")
    parser.add_argument("--no-window", action="store_true", help="Run background server only without opening UI")
    args = parser.parse_args(argv)
    _log(f"app.py: args={args}")

    # 1. Single Instance Check: If server already active, just bring up / launch window!
    if is_server_alive(args.port):
        _log(f"app.py: server is alive on port {args.port}")
        url = f"http://127.0.0.1:{args.port}"
        if not args.no_window:
            if args.browser:
                webbrowser.open(url)
            else:
                launch_native_app_window(url, prefer_edge=args.edge)
        return 0

    # 2. Bind and Start Background HTTP Server
    _log(f"app.py: binding server on {args.port}")
    port = find_free_port(args.port)
    server = create_server("127.0.0.1", port)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    _log(f"app.py: server thread started on port {port}")

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
        _log(f"app.py: launching UI window...")
        if args.browser:
            webbrowser.open(url)
        else:
            launch_native_app_window(url, prefer_edge=args.edge)

    def _threading_excepthook(args):
        _log(f"app.py: UNCAUGHT THREAD EXCEPTION in {args.thread}: {args.exc_type} {args.exc_value}\n{''.join(traceback.format_tb(args.exc_traceback))}")
    threading.excepthook = _threading_excepthook

    def _sys_excepthook(exctype, value, tb):
        _log(f"app.py: UNCAUGHT MAIN EXCEPTION: {exctype} {value}\n{''.join(traceback.format_tb(tb))}")
    sys.excepthook = _sys_excepthook

    # 4. Keep background server and auto-failover daemon alive
    _log("app.py: entering keep-alive loop")
    try:
        seconds = 0
        while True:
            time.sleep(1)
            seconds += 1
            if seconds % 2 == 0:
                _log(f"app.py: heartbeat alive ({seconds}s)")
    except (KeyboardInterrupt, SystemExit) as ke:
        _log(f"app.py: keep-alive loop interrupted by {ke}")
    except Exception as e:
        _log(f"app.py: keep-alive loop crashed: {e}")
    finally:
        _log("app.py: shutting down server")
        server.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
