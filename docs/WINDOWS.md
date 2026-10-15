# Promux Windows Edition: Complete Architecture & Guide

Promux Windows Edition brings the power of **Promux** to Windows 10/11 developers, providing native integration with **Google Antigravity IDE**, automated quota failover, 1-click Google OAuth authentication, and a modern, minimalist English desktop GUI.

---

## 🌟 Why Promux Windows Edition?

On Linux, Antigravity CLI and Promux operate primarily on filesystem tokens under `~/.gemini/antigravity-cli/antigravity-oauth-token` and rely on POSIX `fcntl.flock` for concurrency control.

On Windows, however, developer workflows present two key architectural differences:
1. **Windows Credential Manager Integration (`advapi32.dll`):**  
   Google Antigravity on Windows stores active IDE credentials inside the Windows Vault (`gemini:antigravity`). Simply modifying filesystem tokens leaves the IDE disconnected or desynchronized. Promux Windows Edition bridges both simultaneously: it updates the Windows Credential Vault via Win32 `advapi32.dll` and writes the CLI filesystem token atomically.
2. **Native Win32 Concurrency (`msvcrt.locking`):**  
   Since POSIX `fcntl` is unavailable on Windows, Promux uses `msvcrt` byte-range locking to provide identical multi-process exclusion guarantees without external dependencies.
3. **Frictionless Desktop UX:**  
   Windows developers benefit from a standalone, dark glassmorphic GUI with 1-click Google OAuth sign-in, live quota progress bars, and countdown reset badges.

---

## 🚀 Key Features

- **Zero External Runtime Dependencies:**  
  Built strictly on Python standard library (`ctypes`, `msvcrt`, `urllib`, `http.server`, `threading`, `json`, `pathlib`). No pip packages required to run!
- **1-Click Google OAuth Integration:**  
  Sign into Google Antigravity accounts directly through your browser. Promux handles the local loopback callback (`http://127.0.0.1:18420/oauth/callback`), securely exchanges the authorization code for tokens, and vaults the profile instantly.
- **Dual-Storage Synchronization:**  
  Synchronizes Windows Credential Manager (`gemini:antigravity`) and CLI filesystem tokens atomically during every switch or refresh.
- **Real-Time Quota Tracking & Relative Countdowns:**  
  Direct proactive queries to Google Cloud Code Assist (`/v1internal:retrieveUserQuotaSummary`) tracking 5-hour and weekly quotas for Gemini and Claude/GPT tiers, with relative countdown badges (`2h 15m`, `now`).
- **Smart Switching Engine:**  
  Evaluates standby accounts based on highest available remaining capacity and lowest recent usage.
- **Modern Minimalist Desktop GUI:**  
  Linear / Raycast inspired dark glassmorphic interface running as a borderless native application window via Chrome/Edge or standard browser.

---

## 📦 Installation & Setup

### Option 1: Automated Windows Installer (Recommended)
From the repository root on Windows:
```cmd
# Double-click or run from Command Prompt / PowerShell:
Install_Promux.bat
```
This script:
1. Deploys Promux to `%LOCALAPPDATA%\Programs\Promux`
2. Creates high-resolution shortcuts with custom icons on your **Desktop** and **Start Menu**
3. Registers Promux in your User `PATH` for immediate CLI access

### Option 2: Python Script Installer
```powershell
python installer.py
```

### Option 3: Run Portably (No Installation Required)
```powershell
# Launch Desktop GUI directly
python promux_launcher.py

# Or run Windows CLI commands
python -m promux_win.cli list
python -m promux_win.cli switch --smart
```

---

## 🖥️ Usage Guide

### 1. Launching the GUI
- Double-click the **Promux** shortcut on your Desktop or Start Menu.
- Or run `promux gui` from any terminal.

### 2. Adding Accounts via 1-Click Google Sign-In
1. Click **+ Add Google Account** in the GUI.
2. Your default browser opens the Google authorization screen.
3. Select your Google account and grant permissions.
4. The browser automatically confirms the login and closes the tab.
5. The new account appears immediately in Promux with live quota metrics!

### 3. Switching Profiles
- **Manual Switch:** Click the **Switch** button next to any standby profile.
- **Smart Switch:** Click **Smart Switch** to automatically evaluate and activate the account with the highest available quota.

### 4. CLI Commands on Windows
Promux Windows Edition provides a dedicated CLI matching standard Promux syntax:
```cmd
promux list              # Display all vaulted accounts, status, and quotas
promux switch <name>     # Switch active profile in Vault and filesystem
promux switch --smart    # Smart switch to account with highest available quota
promux quota             # Query live Google Cloud Code Assist quotas
promux refresh           # Refresh OAuth tokens for all eligible profiles
```

---

## 🤝 Collaborative Roadmap

We envision a unified cross-platform Promux experience:
- **Core Abstraction:** Shared account metadata and quota models across POSIX and Windows.
- **Platform Adapters:** Clean separation between POSIX (`fcntl` / filesystem) and Windows (`msvcrt` / Credential Manager).
- **Co-Maintenance:** Active community co-maintenance for Windows platform updates, bug fixes, and feature additions.
