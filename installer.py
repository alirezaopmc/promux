"""
Promux Windows Installer
Installs Promux to %LOCALAPPDATA%\\Programs\\Promux,
creates Desktop and Start Menu shortcuts with icon,
and adds Promux to the Windows User PATH.
"""

from __future__ import annotations

import os
import shutil
import sys
import winreg
from pathlib import Path

# Paths
SOURCE_DIR = Path(__file__).parent / "dist" / "Promux"
ROOT_DIR = Path(__file__).parent
ICON_FILE = ROOT_DIR / "promux.ico"

INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / "Promux"
DESKTOP_DIR = Path.home() / "Desktop"
START_MENU_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def create_windows_shortcut(target_exe: Path, shortcut_path: Path, icon_path: Path, description: str = "Promux Windows"):
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(shortcut_path))
        shortcut.TargetPath = str(target_exe)
        shortcut.WorkingDirectory = str(target_exe.parent)
        shortcut.IconLocation = f"{icon_path},0"
        shortcut.Description = description
        shortcut.Save()
        return True
    except Exception:
        # PowerShell fallback
        ps_cmd = f"""
        $WshShell = New-Object -ComObject WScript.Shell
        $Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
        $Shortcut.TargetPath = "{target_exe}"
        $Shortcut.WorkingDirectory = "{target_exe.parent}"
        $Shortcut.IconLocation = "{icon_path},0"
        $Shortcut.Description = "{description}"
        $Shortcut.Save()
        """
        import subprocess
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True)
        return res.returncode == 0


def add_to_user_path(directory: Path) -> bool:
    dir_str = str(directory)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_ALL_ACCESS) as key:
            try:
                curr_path, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                curr_path = ""

            paths = [p for p in curr_path.split(";") if p]
            if dir_str.lower() not in [p.lower() for p in paths]:
                paths.append(dir_str)
                new_path = ";".join(paths)
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
                # Notify Windows about environment change
                import ctypes
                HWND_BROADCAST = 0xFFFF
                WM_SETTINGCHANGE = 0x001A
                ctypes.windll.user32.SendMessageTimeoutW(
                    HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 2, 5000, None
                )
                return True
    except Exception as e:
        print(f"[!] Warning: Could not add to PATH registry: {e}")
    return False


def install() -> int:
    print("=" * 60)
    print("  Promux Windows Installer")
    print("=" * 60)

    if not SOURCE_DIR.exists():
        print(f"[-] Error: Compiled distribution not found at {SOURCE_DIR}")
        return 1

    print(f"[*] Target installation directory: {INSTALL_DIR}")
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    # Copy files
    print("[*] Copying application files...")
    for item in SOURCE_DIR.iterdir():
        dest = INSTALL_DIR / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)

    # Copy icon and CLI batch helper
    target_icon = INSTALL_DIR / "promux.ico"
    if ICON_FILE.exists():
        shutil.copy2(ICON_FILE, target_icon)

    # Create promux.cmd for instant CLI anywhere
    cli_cmd = INSTALL_DIR / "promux.cmd"
    cli_cmd.write_text(f'@echo off\n"{INSTALL_DIR}\\Promux.exe" %*\n', encoding="utf-8")

    # Target executable
    target_exe = INSTALL_DIR / "Promux.exe"

    # 1. Desktop Shortcut
    print("[*] Creating Desktop shortcut...")
    desktop_shortcut = DESKTOP_DIR / "Promux.lnk"
    create_windows_shortcut(
        target_exe=target_exe,
        shortcut_path=desktop_shortcut,
        icon_path=target_icon,
        description="Promux Windows — Antigravity Account Multiplexer",
    )
    print(f"[+] Desktop shortcut created: {desktop_shortcut}")

    # 2. Start Menu Shortcut
    print("[*] Creating Start Menu shortcut...")
    start_menu_shortcut = START_MENU_DIR / "Promux.lnk"
    create_windows_shortcut(
        target_exe=target_exe,
        shortcut_path=start_menu_shortcut,
        icon_path=target_icon,
        description="Promux Windows — Antigravity Account Multiplexer",
    )
    print(f"[+] Start Menu shortcut created: {start_menu_shortcut}")

    # 3. Add to User PATH
    print("[*] Registering Promux in Windows User PATH...")
    if add_to_user_path(INSTALL_DIR):
        print("[+] Added to User PATH. You can now run 'promux' from any Command Prompt or PowerShell.")

    # 4. Create Uninstaller script
    uninstaller = INSTALL_DIR / "uninstall.bat"
    uninstaller_content = f"""@echo off
title Uninstalling Promux
echo Removing shortcuts...
del /f /q "{desktop_shortcut}" 2>nul
del /f /q "{start_menu_shortcut}" 2>nul
echo Removing Promux installation files...
cd /d "%TEMP%"
rmdir /s /q "{INSTALL_DIR}"
echo Promux has been uninstalled successfully.
pause
"""
    uninstaller.write_text(uninstaller_content, encoding="utf-8")

    print("\n" + "=" * 60)
    print("  [SUCCESS] Promux Windows has been installed successfully!")
    print("  - Desktop shortcut: Promux")
    print("  - Start Menu shortcut: Promux")
    print(f"  - Install location: {INSTALL_DIR}")
    print("=" * 60 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(install())
