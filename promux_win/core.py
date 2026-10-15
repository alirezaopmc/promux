"""
Promux Windows Core Engine
Universal Antigravity Multi-Account Profile Multiplexer for Windows.
Seamlessly integrates with Windows Credential Manager ('gemini:antigravity'),
the filesystem tokens, and Google Cloud Code Assist Quota & OAuth APIs.
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Generator
from contextlib import contextmanager

# =====================================================================
# Constants & Google Official Credentials
# =====================================================================

DEFAULT_OAUTH_CLIENT_ID = "REDACTED_OAUTH_CLIENT_ID"
DEFAULT_OAUTH_CLIENT_SECRET = "REDACTED_OAUTH_CLIENT_SECRET"

OAUTH_TOKEN_URL = os.environ.get("PROMUX_OAUTH_TOKEN_URL", "https://oauth2.googleapis.com/token")
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
CODE_ASSIST_BASE_URL = os.environ.get("PROMUX_CODE_ASSIST_URL", "https://cloudcode-pa.googleapis.com")
FALLBACK_CODE_ASSIST_BASE_URL = "https://daily-cloudcode-pa.googleapis.com"
LOAD_ENDPOINT = "/v1internal:loadCodeAssist"
QUOTA_ENDPOINT = "/v1internal:retrieveUserQuotaSummary"


def get_oauth_credentials() -> tuple[str, str]:
    """Retrieve OAuth client ID and secret from environment, ~/.promux/oauth.json, or Antigravity binary."""
    client_id = os.environ.get("PROMUX_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("PROMUX_OAUTH_CLIENT_SECRET", "")

    if not client_id or not client_secret or client_id.startswith("REDACTED"):
        cfg_file = Path.home() / ".promux" / "oauth.json"
        if cfg_file.exists():
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    client_id = client_id or data.get("client_id", "")
                    client_secret = client_secret or data.get("client_secret", "")
            except Exception:
                pass

    if not client_id or not client_secret or client_id.startswith("REDACTED"):
        # Auto-extract from installed Antigravity language_server binary on Windows
        try:
            ls_path = Path.home() / "AppData" / "Local" / "Programs" / "antigravity" / "resources" / "bin" / "language_server.exe"
            if ls_path.exists():
                content = ls_path.read_bytes()
                m_id = re.search(rb"[0-9]+-[a-z0-9_]{32}\.apps\.googleusercontent\.com", content)
                m_sec = re.search(rb"GOCSPX-[a-zA-Z0-9_-]{28}", content)
                if m_id and m_sec:
                    client_id = client_id or m_id.group(0).decode("utf-8")
                    client_secret = client_secret or m_sec.group(0).decode("utf-8")
        except Exception:
            pass

    if not client_id:
        client_id = DEFAULT_OAUTH_CLIENT_ID
    if not client_secret:
        client_secret = DEFAULT_OAUTH_CLIENT_SECRET

    return client_id, client_secret


USER_AGENT = "antigravity"
DEFAULT_LOCK_TIMEOUT = 10.0
DEFAULT_HTTP_TIMEOUT = 15.0
DEFAULT_COOLDOWN_MINUTES = 60
CREDENTIAL_TARGET = "gemini:antigravity"
CREDENTIAL_USERNAME = "antigravity"


# =====================================================================
# Models
# =====================================================================

class AccountState(str, Enum):
    STANDBY = "standby"
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"


@dataclass
class AccountMeta:
    name: str
    enabled: bool = True
    cooldown_until: datetime | None = None
    saved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = None
    email: str | None = None
    project_id: str | None = None
    plan_type: str = "STANDARD"
    alias: str | None = None

    @property
    def is_cooldown(self) -> bool:
        if self.cooldown_until:
            now = datetime.now(timezone.utc)
            cd = self.cooldown_until
            if cd.tzinfo is None:
                cd = cd.replace(tzinfo=timezone.utc)
            return cd > now
        return False

    def get_state(self, is_active: bool = False) -> AccountState:
        if not self.enabled:
            return AccountState.DISABLED
        if is_active:
            return AccountState.ACTIVE
        if self.is_cooldown:
            return AccountState.COOLDOWN
        return AccountState.STANDBY

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "saved_at": self.saved_at.isoformat() if self.saved_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "email": self.email,
            "project_id": self.project_id,
            "plan_type": self.plan_type,
            "alias": self.alias,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AccountMeta":
        return cls(
            name=data["name"],
            enabled=data.get("enabled", True),
            cooldown_until=datetime.fromisoformat(data["cooldown_until"]) if data.get("cooldown_until") else None,
            saved_at=datetime.fromisoformat(data["saved_at"]) if data.get("saved_at") else datetime.now(timezone.utc),
            last_used_at=datetime.fromisoformat(data["last_used_at"]) if data.get("last_used_at") else None,
            email=data.get("email"),
            project_id=data.get("project_id"),
            plan_type=data.get("plan_type", "STANDARD"),
            alias=data.get("alias"),
        )


@dataclass
class QuotaSummary:
    gemini_5h_remaining: float = 1.0
    gemini_weekly_remaining: float = 1.0
    third_party_5h_remaining: float = 1.0
    third_party_weekly_remaining: float = 1.0
    gemini_5h_reset: str | None = None
    gemini_weekly_reset: str | None = None
    third_party_5h_reset: str | None = None
    third_party_weekly_reset: str | None = None
    last_fetched_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gemini_5h_remaining": self.gemini_5h_remaining,
            "gemini_weekly_remaining": self.gemini_weekly_remaining,
            "third_party_5h_remaining": self.third_party_5h_remaining,
            "third_party_weekly_remaining": self.third_party_weekly_remaining,
            "gemini_5h_reset": self.gemini_5h_reset,
            "gemini_weekly_reset": self.gemini_weekly_reset,
            "third_party_5h_reset": self.third_party_5h_reset,
            "third_party_weekly_reset": self.third_party_weekly_reset,
            "gemini_5h_countdown": format_relative_countdown(self.gemini_5h_reset),
            "gemini_weekly_countdown": format_relative_countdown(self.gemini_weekly_reset),
            "third_party_5h_countdown": format_relative_countdown(self.third_party_5h_reset),
            "third_party_weekly_countdown": format_relative_countdown(self.third_party_weekly_reset),
            "last_fetched_at": self.last_fetched_at or datetime.now(timezone.utc).isoformat(),
            "error": self.error,
        }


# =====================================================================
# Utilities
# =====================================================================

def parse_iso_utc(iso_str: str | None) -> datetime | None:
    if not iso_str:
        return None
    try:
        clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def format_relative_countdown(iso_str: str | None) -> str:
    dt = parse_iso_utc(iso_str)
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    diff = dt - now
    total_seconds = int(diff.total_seconds())
    if total_seconds <= 0:
        return "now"

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts[:2])


def extract_email_from_jwt(id_token: str | None) -> str | None:
    if not id_token or not isinstance(id_token, str):
        return None
    try:
        parts = id_token.split(".")
        if len(parts) >= 2:
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8"))
            return data.get("email")
    except Exception:
        pass
# =====================================================================
# Antigravity Process & Lifecycle Manager
# =====================================================================

class AntigravityManager:
    """Manages Google Antigravity IDE process lifecycle on Windows with high-performance native APIs."""

    @staticmethod
    def find_antigravity_executable() -> Path | None:
        """Locates the primary Antigravity IDE executable on the system."""
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity" / "Antigravity.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "antigravity" / "Antigravity.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "antigravity" / "Antigravity.exe",
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                return c
        return None

    @classmethod
    def get_antigravity_pids(cls) -> list[int]:
        """Ultra-fast process enumeration using Windows Toolhelp32 API (~5ms)."""
        pids: list[int] = []
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESSENTRY32(ctypes.Structure):
                _fields_ = [
                    ('dwSize', wintypes.DWORD),
                    ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
                    ('th32ModuleID', wintypes.DWORD),
                    ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD),
                    ('szExeFile', ctypes.c_char * 260)
                ]

            kernel32 = ctypes.windll.kernel32
            hSnapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
            if hSnapshot and hSnapshot != -1:
                pe = PROCESSENTRY32()
                pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
                if kernel32.Process32First(hSnapshot, ctypes.byref(pe)):
                    while True:
                        exe_name = pe.szExeFile.decode('latin1', 'ignore').lower()
                        if exe_name == 'antigravity.exe':
                            pids.append(pe.th32ProcessID)
                        if not kernel32.Process32Next(hSnapshot, ctypes.byref(pe)):
                            break
                kernel32.CloseHandle(hSnapshot)
                return pids
        except Exception:
            pass

        # Fallback to tasklist if ctypes snapshot fails
        try:
            res = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Antigravity.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=2,
            )
            for line in res.stdout.splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) >= 2 and parts[0].lower() == "antigravity.exe":
                    try:
                        pids.append(int(parts[1]))
                    except ValueError:
                        pass
        except Exception:
            pass
        return pids

    @classmethod
    def is_running(cls) -> bool:
        """Checks if Antigravity.exe is currently active in the process table."""
        return len(cls.get_antigravity_pids()) > 0

    @classmethod
    def terminate(cls) -> bool:
        """Immediately terminates all running Antigravity.exe processes."""
        pids = cls.get_antigravity_pids()
        if not pids:
            return True

        success = False
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_TERMINATE = 0x0001
            for pid in pids:
                hProc = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                if hProc:
                    kernel32.TerminateProcess(hProc, 0)
                    kernel32.CloseHandle(hProc)
                    success = True
        except Exception:
            pass

        # Fallback taskkill
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "Antigravity.exe", "/T"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=3,
            )
            success = True
        except Exception:
            pass

        return success

    @classmethod
    def restart(cls, force_launch: bool = True, async_mode: bool = True) -> tuple[bool, str]:
        """Gracefully closes Antigravity IDE if running, then relaunches it asynchronously or synchronously."""
        was_running = cls.is_running()
        exe_path = cls.find_antigravity_executable()

        if not exe_path and not was_running:
            return False, "Antigravity IDE executable was not found on this system."

        def _do_restart():
            if was_running:
                cls.terminate()
                time.sleep(0.3)

            if (was_running or force_launch) and exe_path and exe_path.exists():
                try:
                    os.startfile(str(exe_path))
                except Exception:
                    try:
                        subprocess.Popen(
                            [str(exe_path)],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200),
                            close_fds=True,
                        )
                    except Exception:
                        pass

        if async_mode:
            threading.Thread(target=_do_restart, daemon=True).start()
            return True, "Antigravity IDE restart initiated"
        else:
            _do_restart()
            return True, "Antigravity IDE restarted"


# =====================================================================
# Windows Credential Manager Bridge (ctypes advapi32)
# =====================================================================

advapi32 = ctypes.windll.advapi32
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialBridge:
    @staticmethod
    def read_token(target_name: str = CREDENTIAL_TARGET) -> dict[str, Any] | None:
        """Read credential from Windows Credential Manager, returning parsed JSON."""
        cred_ptr = ctypes.POINTER(CREDENTIAL)()
        res = advapi32.CredReadW(target_name, CRED_TYPE_GENERIC, 0, ctypes.byref(cred_ptr))
        if not res or not cred_ptr:
            return None
        try:
            cred = cred_ptr.contents
            blob_size = cred.CredentialBlobSize
            if blob_size <= 0 or not cred.CredentialBlob:
                return None
            raw_bytes = bytes(ctypes.cast(cred.CredentialBlob, ctypes.POINTER(ctypes.c_byte * blob_size)).contents)
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = raw_bytes.decode("utf-16-le")
            return json.loads(text)
        except Exception:
            return None
        finally:
            advapi32.CredFree(cred_ptr)

    @staticmethod
    def write_token(
        token_data: dict[str, Any],
        target_name: str = CREDENTIAL_TARGET,
        user_name: str = CREDENTIAL_USERNAME,
    ) -> bool:
        """Write raw UTF-8 JSON token to Windows Credential Manager."""
        raw_bytes = json.dumps(token_data, indent=2).encode("utf-8")
        cred = CREDENTIAL()
        cred.Flags = 0
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = target_name
        cred.Comment = None
        cred.CredentialBlobSize = len(raw_bytes)
        blob_array = (ctypes.c_byte * len(raw_bytes))(*raw_bytes)
        cred.CredentialBlob = ctypes.cast(blob_array, ctypes.POINTER(ctypes.c_byte))
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = user_name

        res = advapi32.CredWriteW(ctypes.byref(cred), 0)
        return bool(res)

    @staticmethod
    def delete_token(target_name: str = CREDENTIAL_TARGET) -> bool:
        res = advapi32.CredDeleteW(target_name, CRED_TYPE_GENERIC, 0)
        return bool(res)


# =====================================================================
# Windows Cross-Process File Lock
# =====================================================================

@contextmanager
def windows_file_lock(lock_path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT) -> Generator[Path, None, None]:
    """Cross-process file lock for Windows using msvcrt / open mode or filelock."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start_time = time.monotonic()
    fd = None

    while True:
        try:
            # Open file for read/write without sharing or with exclusive lock
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT | os.O_BINARY, 0o666)
            try:
                import msvcrt
                # Lock 1 byte from beginning
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except (OSError, ImportError):
                # If msvcrt locking works, break; otherwise try again
                break
        except (OSError, PermissionError) as err:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
            if time.monotonic() - start_time >= timeout:
                raise TimeoutError(f"Timed out after {timeout}s waiting for lock: {lock_path}") from err
            time.sleep(0.05)

    try:
        yield lock_path
    finally:
        if fd is not None:
            try:
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            try:
                os.close(fd)
            except OSError:
                pass


# =====================================================================
# OAuth Refresher
# =====================================================================

class OAuthRefresher:
    client_id: str = DEFAULT_OAUTH_CLIENT_ID
    client_secret: str = DEFAULT_OAUTH_CLIENT_SECRET

    @classmethod
    def get_credentials(cls) -> tuple[str, str]:
        cid, sec = get_oauth_credentials()
        eff_cid = cls.client_id if cls.client_id and not cls.client_id.startswith("REDACTED") else cid
        eff_sec = cls.client_secret if cls.client_secret and not cls.client_secret.startswith("REDACTED") else sec
        return eff_cid, eff_sec

    @classmethod
    def is_expired(cls, token_data: dict[str, Any], buffer_seconds: int = 120) -> bool:
        tok = token_data.get("token", {}) if isinstance(token_data, dict) else {}
        expiry_str = tok.get("expiry")
        if not expiry_str:
            return True
        exp_dt = parse_iso_utc(expiry_str)
        if not exp_dt:
            return True
        now = datetime.now(timezone.utc)
        return now + timedelta(seconds=buffer_seconds) >= exp_dt

    @classmethod
    def refresh(cls, token_data: dict[str, Any], force: bool = False) -> tuple[bool, str]:
        """Refreshes Google OAuth access token using official Antigravity credentials."""
        tok = token_data.get("token")
        if not isinstance(tok, dict):
            return False, "Token structure missing 'token' dict"

        refresh_token = tok.get("refresh_token")
        if not refresh_token:
            return False, "Missing refresh_token in token payload"

        if not force and not cls.is_expired(token_data):
            return True, "Token is still valid"

        eff_client_id, eff_client_secret = cls.get_credentials()
        payload = urllib.parse.urlencode({
            "client_id": eff_client_id,
            "client_secret": eff_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }).encode("utf-8")

        req = urllib.request.Request(OAUTH_TOKEN_URL, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                res = json.loads(resp.read().decode("utf-8"))
            new_acc = res.get("access_token")
            if new_acc:
                tok["access_token"] = new_acc
                expires_in = res.get("expires_in", 3600)
                now = datetime.now(timezone.utc)
                tok["expiry"] = (now + timedelta(seconds=expires_in)).isoformat().replace("+00:00", "Z")
                if "refresh_token" in res and res["refresh_token"]:
                    tok["refresh_token"] = res["refresh_token"]
                return True, "Refreshed successfully"
            return False, "Google did not return access_token"
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
                return False, f"HTTP {e.code}: {err_body.get('error', e.reason)}"
            except Exception:
                return False, f"HTTP Error {e.code}: {e.reason}"
        except Exception as e:
            return False, f"Network error: {str(e)}"


# =====================================================================
# OAuth Flow Manager (Smart Google 1-Click Login)
# =====================================================================

class OAuthFlowManager:
    client_id: str = DEFAULT_OAUTH_CLIENT_ID
    client_secret: str = DEFAULT_OAUTH_CLIENT_SECRET
    oauth_scopes: str = (
        "openid email profile "
        "https://www.googleapis.com/auth/userinfo.email "
        "https://www.googleapis.com/auth/userinfo.profile "
        "https://www.googleapis.com/auth/cloud-platform "
        "https://www.googleapis.com/auth/cclog "
        "https://www.googleapis.com/auth/experimentsandconfigs "
        "https://www.googleapis.com/auth/aicode"
    )

    @classmethod
    def get_credentials(cls) -> tuple[str, str]:
        cid, sec = get_oauth_credentials()
        eff_cid = cls.client_id if cls.client_id and not cls.client_id.startswith("REDACTED") else cid
        eff_sec = cls.client_secret if cls.client_secret and not cls.client_secret.startswith("REDACTED") else sec
        return eff_cid, eff_sec

    @classmethod
    def build_auth_url(cls, redirect_uri: str, state: str) -> str:
        """Constructs Google OAuth authorization URL for browser login."""
        eff_client_id, _ = cls.get_credentials()
        params = {
            "client_id": eff_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": cls.oauth_scopes,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)

    @classmethod
    def exchange_code(cls, code: str, redirect_uri: str) -> tuple[bool, dict[str, Any] | str]:
        """Exchanges authorization code for Google access, refresh, and id tokens."""
        eff_client_id, eff_client_secret = cls.get_credentials()
        payload = urllib.parse.urlencode({
            "client_id": eff_client_id,
            "client_secret": eff_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }).encode("utf-8")

        req = urllib.request.Request(
            OAUTH_TOKEN_URL,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=DEFAULT_HTTP_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return True, data
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
                msg = err_body.get("error_description", err_body.get("error", e.reason))
                return False, f"HTTP {e.code}: {msg}"
            except Exception:
                return False, f"HTTP Error {e.code}: {e.reason}"
        except Exception as e:
            return False, f"Network error during code exchange: {str(e)}"

    @classmethod
    def build_token_dict(cls, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Converts Google token response into official Antigravity token structure."""
        now = datetime.now(timezone.utc)
        expires_in = int(raw_data.get("expires_in", 3600))
        expiry_dt = now + timedelta(seconds=expires_in)
        return {
            "token": {
                "access_token": raw_data.get("access_token", ""),
                "token_type": raw_data.get("token_type", "Bearer"),
                "refresh_token": raw_data.get("refresh_token", ""),
                "expiry": expiry_dt.isoformat().replace("+00:00", "Z"),
            },
            "auth_method": "oauth",
            "id_token": raw_data.get("id_token"),
        }


# =====================================================================
# Quota Client (Cloud Code Assist REST API)
# =====================================================================

class QuotaClient:
    def __init__(self, token: str, timeout: float = DEFAULT_HTTP_TIMEOUT):
        self.token = token
        self.timeout = timeout
        self.base_url = CODE_ASSIST_BASE_URL.rstrip("/")

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        urls = [f"{self.base_url}{endpoint}", f"{FALLBACK_CODE_ASSIST_BASE_URL}{endpoint}"]
        last_err = None
        for u in urls:
            req = urllib.request.Request(u, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                last_err = e
        if last_err:
            raise last_err
        return {}

    def fetch_email(self) -> str | None:
        req = urllib.request.Request(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": USER_AGENT},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("email")
        except Exception:
            return None

    def load_metadata(self) -> dict[str, Any]:
        payload = {
            "metadata": {
                "ideType": "ANTIGRAVITY",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        }
        res = self._post(LOAD_ENDPOINT, payload)
        project = res.get("cloudaicompanionProject")
        project_id = project.get("id", "") if isinstance(project, dict) else str(project or "")
        tier_info = res.get("currentTier", {})
        if not isinstance(tier_info, dict):
            tier_info = {}
        return {
            "project_id": project_id or "aicode-consumers",
            "tier": tier_info.get("id", "free-tier"),
            "plan_name": tier_info.get("name", "Antigravity"),
        }

    def fetch_quota(self, project_id: str = "aicode-consumers") -> QuotaSummary:
        res = self._post(QUOTA_ENDPOINT, {"project": project_id})
        qs = QuotaSummary(last_fetched_at=datetime.now(timezone.utc).isoformat())

        for group in res.get("groups", []):
            group_name = group.get("displayName", "").lower()
            for bucket in group.get("buckets", []):
                window = bucket.get("window", "").lower()
                rem = bucket.get("remainingFraction")
                rem_val = float(rem) if rem is not None else 1.0
                reset = bucket.get("resetTime")

                if "gemini" in group_name:
                    if window == "5h" or "short" in window:
                        qs.gemini_5h_remaining = rem_val
                        qs.gemini_5h_reset = reset
                    elif window == "weekly":
                        qs.gemini_weekly_remaining = rem_val
                        qs.gemini_weekly_reset = reset
                else:  # Claude / GPT 3rd party
                    if window == "5h" or "short" in window:
                        qs.third_party_5h_remaining = rem_val
                        qs.third_party_5h_reset = reset
                    elif window == "weekly":
                        qs.third_party_weekly_remaining = rem_val
                        qs.third_party_weekly_reset = reset

        return qs


# =====================================================================
# Storage Engine (Promux Layout + Windows Credential Bridge)
# =====================================================================

class WindowsStorageEngine:
    def __init__(self, promux_home: Path | None = None):
        self.home = Path(promux_home or os.environ.get("PROMUX_HOME", Path.home() / ".promux"))
        self.tool_dir = self.home / "tools" / "agy"
        self.accounts_dir = self.tool_dir / "accounts"
        self.state_file = self.tool_dir / "state.json"
        self.lock_file = self.tool_dir / "manager.lock"
        self.cache_file = self.tool_dir / "quota_cache.json"

        # Active CLI targets on Windows
        self.cli_target_1 = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        self.cli_target_2 = Path.home() / ".gemini" / "antigravity" / "antigravity-oauth-token"

        self.accounts_dir.mkdir(parents=True, exist_ok=True)

    def _safe_replace(self, src: Path, dst: Path, retries: int = 10) -> None:
        """Windows-safe file replace with retry backoff."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        for i in range(retries):
            try:
                os.replace(src, dst)
                return
            except (PermissionError, OSError):
                if i == retries - 1:
                    # Fallback copy
                    shutil.copy2(src, dst)
                    try:
                        os.remove(src)
                    except Exception:
                        pass
                    return
                time.sleep(0.05 * (i + 1))

    def _atomic_write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp_{os.getpid()}_{int(time.time()*1000)}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self._safe_replace(tmp, path)

    def load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"active": None, "accounts": {}}
        try:
            with open(self.state_file, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {"active": None, "accounts": {}}
        except Exception:
            return {"active": None, "accounts": {}}

    def save_state(self, state: dict[str, Any]) -> None:
        with windows_file_lock(self.lock_file):
            self._atomic_write_json(self.state_file, state)

    def get_live_token_data(self) -> dict[str, Any] | None:
        """Reads currently active token from Windows Credential Manager or CLI files."""
        # 1. Primary: Windows Credential Manager
        cred_token = WindowsCredentialBridge.read_token()
        if cred_token and isinstance(cred_token, dict) and "token" in cred_token:
            return cred_token

        # 2. Secondary: CLI token files
        for p in [self.cli_target_1, self.cli_target_2]:
            if p.exists():
                try:
                    with open(p, encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and "token" in data:
                            return data
                except Exception:
                    pass
        return None

    def get_account_token_path(self, name: str) -> Path:
        return self.accounts_dir / name / "antigravity-oauth-token"

    def read_account_token(self, name: str) -> dict[str, Any] | None:
        p = self.get_account_token_path(name)
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def write_account_token(self, name: str, token_data: dict[str, Any]) -> None:
        p = self.get_account_token_path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(p, token_data)

    def save_profile(
        self,
        name: str,
        token_data: dict[str, Any] | None = None,
        email: str | None = None,
        project_id: str | None = None,
        plan_type: str = "STANDARD",
        alias: str | None = None,
    ) -> AccountMeta:
        """Saves or updates a profile in the vault."""
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Profile name cannot be empty")

        if token_data is None:
            token_data = self.get_live_token_data()
            if not token_data:
                raise ValueError("No active Antigravity session found to capture. Please login or paste token.")

        # Ensure valid access token
        OAuthRefresher.refresh(token_data, force=False)
        self.write_account_token(clean_name, token_data)

        # Infer email from id_token if not provided
        if not email:
            email = extract_email_from_jwt(token_data.get("id_token"))

        # If still no email, query userinfo
        acc_token = token_data.get("token", {}).get("access_token")
        if acc_token and not email:
            try:
                qc = QuotaClient(acc_token)
                email = qc.fetch_email()
            except Exception:
                pass

        if acc_token and not project_id:
            try:
                qc = QuotaClient(acc_token)
                meta = qc.load_metadata()
                project_id = meta.get("project_id")
                plan_type = meta.get("plan_name", plan_type)
            except Exception:
                pass

        with windows_file_lock(self.lock_file):
            state = self.load_state()
            now = datetime.now(timezone.utc)
            acct = AccountMeta(
                name=clean_name,
                enabled=True,
                saved_at=now,
                last_used_at=now,
                email=email,
                project_id=project_id,
                plan_type=plan_type,
                alias=alias.strip() if alias else None,
            )
            state.setdefault("accounts", {})[clean_name] = acct.to_dict()
            if not state.get("active"):
                state["active"] = clean_name
            self._atomic_write_json(self.state_file, state)

        return acct

    def set_account_alias(self, name: str, alias: str | None) -> bool:
        """Sets custom display name/alias for an account."""
        clean_name = name.strip()
        with windows_file_lock(self.lock_file):
            state = self.load_state()
            if clean_name not in state.get("accounts", {}):
                return False
            clean_alias = alias.strip() if alias else None
            state["accounts"][clean_name]["alias"] = clean_alias
            self._atomic_write_json(self.state_file, state)
        return True

    def rename_account(self, old_name: str, new_name: str, alias: str | None = None) -> tuple[bool, str]:
        """Renames account profile directory, updates state, and sets alias."""
        old_clean = old_name.strip()
        new_clean = new_name.strip()
        if not new_clean:
            return False, "New account name cannot be empty."

        if any(c in new_clean for c in r'\/:*?"<>|'):
            return False, "Account name contains invalid filename characters."

        with windows_file_lock(self.lock_file):
            state = self.load_state()
            accts = state.get("accounts", {})
            if old_clean not in accts:
                return False, f"Account '{old_clean}' does not exist."

            if new_clean != old_clean and new_clean in accts:
                return False, f"An account named '{new_clean}' already exists."

            if new_clean != old_clean:
                old_dir = self.accounts_dir / old_clean
                new_dir = self.accounts_dir / new_clean
                if old_dir.exists():
                    new_dir.parent.mkdir(parents=True, exist_ok=True)
                    self._safe_replace(old_dir, new_dir)

                data = accts.pop(old_clean)
                data["name"] = new_clean
                if alias is not None:
                    data["alias"] = alias.strip() if alias else None
                accts[new_clean] = data

                if state.get("active") == old_clean:
                    state["active"] = new_clean
            else:
                if alias is not None:
                    accts[old_clean]["alias"] = alias.strip() if alias else None

            self._atomic_write_json(self.state_file, state)

        return True, f"Account updated to '{new_clean}'"

    def switch_profile(self, name: str, restart_ide: bool = False) -> tuple[bool, str]:
        """Switches active profile to target account, syncing both Credential Manager & files."""
        clean_name = name.strip()
        token_data = self.read_account_token(clean_name)
        if not token_data:
            return False, f"Account '{clean_name}' token file not found."

        with windows_file_lock(self.lock_file):
            state = self.load_state()
            if clean_name not in state.get("accounts", {}):
                return False, f"Account '{clean_name}' not registered in state."

            # Sync current active token back to previous account's vault
            prev_active = state.get("active")
            if prev_active and prev_active != clean_name:
                live_data = self.get_live_token_data()
                if live_data:
                    self.write_account_token(prev_active, live_data)

            # Auto-renew target token if needed
            refreshed, rmsg = OAuthRefresher.refresh(token_data, force=False)
            self.write_account_token(clean_name, token_data)

            # Apply to Windows Credential Manager
            cred_ok = WindowsCredentialBridge.write_token(token_data)

            # Apply to CLI Filesystem locations
            self._atomic_write_json(self.cli_target_1, token_data)
            if self.cli_target_2.parent.exists():
                self._atomic_write_json(self.cli_target_2, token_data)

            # Update state
            state["active"] = clean_name
            state["accounts"][clean_name]["last_used_at"] = datetime.now(timezone.utc).isoformat()
            self._atomic_write_json(self.state_file, state)

        # Automatically restart Antigravity IDE if requested (asynchronous for instant zero-lag UI response)
        if restart_ide:
            AntigravityManager.restart(force_launch=True, async_mode=True)

        return True, f"Successfully switched to '{clean_name}'"

    def remove_profile(self, name: str) -> bool:
        clean_name = name.strip()
        with windows_file_lock(self.lock_file):
            state = self.load_state()
            if clean_name not in state.get("accounts", {}):
                return False

            del state["accounts"][clean_name]
            if state.get("active") == clean_name:
                state["active"] = None
            self._atomic_write_json(self.state_file, state)

            acct_dir = self.accounts_dir / clean_name
            if acct_dir.exists():
                shutil.rmtree(acct_dir, ignore_errors=True)

        return True

    def list_accounts(self) -> list[AccountMeta]:
        state = self.load_state()
        return [AccountMeta.from_dict(d) for d in state.get("accounts", {}).values()]

    def get_active_account_name(self) -> str | None:
        state = self.load_state()
        return state.get("active")

    def apply_cooldown(self, name: str, minutes: int = DEFAULT_COOLDOWN_MINUTES) -> None:
        with windows_file_lock(self.lock_file):
            state = self.load_state()
            if name in state.get("accounts", {}):
                until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
                state["accounts"][name]["cooldown_until"] = until.isoformat()
                self._atomic_write_json(self.state_file, state)

    def clear_cooldown(self, name: str) -> None:
        with windows_file_lock(self.lock_file):
            state = self.load_state()
            if name in state.get("accounts", {}):
                state["accounts"][name]["cooldown_until"] = None
                self._atomic_write_json(self.state_file, state)


# =====================================================================
# Smart Quota Switch Engine
# =====================================================================

class SmartSwitchEngine:
    def __init__(self, storage: WindowsStorageEngine):
        self.storage = storage

    def evaluate_candidates(self) -> list[dict[str, Any]]:
        """Ranks accounts by available quota and eligibility."""
        accounts = self.storage.list_accounts()
        active = self.storage.get_active_account_name()
        results = []

        for acct in accounts:
            token_data = self.storage.read_account_token(acct.name)
            if not token_data:
                continue

            OAuthRefresher.refresh(token_data, force=False)
            acc_token = token_data.get("token", {}).get("access_token")
            quota = QuotaSummary()
            if acc_token:
                try:
                    qc = QuotaClient(acc_token)
                    quota = qc.fetch_quota(acct.project_id or "aicode-consumers")
                except Exception as e:
                    quota.error = str(e)

            # Score formula: 5h quota is heavily weighted (70%), weekly is (30%)
            min_5h = min(quota.gemini_5h_remaining, quota.third_party_5h_remaining)
            min_weekly = min(quota.gemini_weekly_remaining, quota.third_party_weekly_remaining)
            score = (min_5h * 0.70) + (min_weekly * 0.30)

            results.append({
                "account": acct,
                "quota": quota,
                "score": score,
                "is_active": acct.name == active,
                "is_eligible": acct.enabled and not acct.is_cooldown and acct.name != active,
            })

        # Sort eligible candidates by score descending
        results.sort(key=lambda r: (r["is_eligible"], r["score"]), reverse=True)
        return results

    def smart_switch(self, restart_ide: bool = False) -> tuple[bool, str, str | None]:
        candidates = self.evaluate_candidates()
        eligible = [c for c in candidates if c["is_eligible"]]
        if not eligible:
            return False, "No eligible standby accounts available.", None

        best = eligible[0]
        target_name = best["account"].name
        ok, msg = self.storage.switch_profile(target_name, restart_ide=restart_ide)
        return ok, msg, target_name
