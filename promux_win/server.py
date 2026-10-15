"""
Promux Windows HTTP API Server
Lightweight, thread-safe embedded HTTP server for Promux Windows GUI.
Zero external server dependencies (uses standard library http.server).
Features Smart 1-Click Google OAuth flow and concurrent live quota fetching.
"""

from __future__ import annotations

import concurrent.futures
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .core import (
    AntigravityManager,
    OAuthFlowManager,
    OAuthRefresher,
    QuotaClient,
    QuotaSummary,
    SmartSwitchEngine,
    WindowsCredentialBridge,
    WindowsStorageEngine,
    extract_email_from_jwt,
)
from .watcher import AntigravityLogWatcher


def _get_ui_dir() -> Path:
    if getattr(sys, "frozen", False):
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        for candidate in [
            base_dir / "promux_win" / "ui",
            base_dir / "ui",
            Path(sys.executable).parent / "promux_win" / "ui",
            Path(sys.executable).parent / "ui",
        ]:
            if candidate.exists() and candidate.is_dir():
                return candidate
    return Path(__file__).parent / "ui"


UI_DIR = _get_ui_dir()


class PromuxApiHandler(BaseHTTPRequestHandler):
    storage: WindowsStorageEngine
    watcher: AntigravityLogWatcher
    smart_engine: SmartSwitchEngine
    quota_cache: dict[str, dict[str, Any]] = {}
    pending_oauth: dict[str, dict[str, Any]] = {}
    _cache_lock = threading.Lock()

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy standard HTTP logs
        pass

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/status":
            self._handle_get_status(parsed)
        elif path == "/api/events":
            self._send_json({"events": self.watcher.events_history})
        elif path == "/api/live_session":
            self._handle_get_live_session()
        elif path == "/api/oauth/check":
            self._handle_oauth_check(parsed)
        elif path in ["/oauth/callback", "/auth/callback"]:
            self._handle_oauth_callback(parsed)
        else:
            # Serve static files from UI_DIR
            self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        if path == "/api/save":
            self._handle_save_profile(body)
        elif path == "/api/switch":
            self._handle_switch_profile(body)
        elif path == "/api/smart_switch":
            self._handle_smart_switch(body)
        elif path == "/api/account/update":
            self._handle_account_update(body)
        elif path == "/api/restart_antigravity":
            self._handle_restart_antigravity()
        elif path == "/api/alert/confirm":
            self._handle_alert_confirm(body)
        elif path == "/api/alert/dismiss":
            self._handle_alert_dismiss()
        elif path == "/api/toggle_setting":
            self._handle_toggle_setting(body)
        elif path == "/api/refresh_quota":
            self._handle_refresh_quota(body)
        elif path == "/api/refresh_token":
            self._handle_refresh_token(body)
        elif path == "/api/delete":
            self._handle_delete_profile(body)
        elif path == "/api/clear_cooldown":
            self._handle_clear_cooldown(body)
        elif path == "/api/toggle_watcher":
            self._handle_toggle_watcher(body)
        elif path == "/api/restart_server":
            self._handle_restart_language_server()
        elif path == "/api/import_token":
            self._handle_import_token(body)
        elif path == "/api/oauth/start":
            self._handle_oauth_start(body)
        elif path == "/api/exit":
            self._send_json({"success": True})
            threading.Thread(target=lambda: os._exit(0), daemon=True).start()
        else:
            self._send_json({"error": "Endpoint not found"}, status=404)

    def _read_json_body(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                raw = self.wfile.read(content_length) if hasattr(self, "_mock") else self.rfile.read(content_length)
                return json.loads(raw.decode("utf-8"))
        except Exception:
            pass
        return {}

    def _serve_static(self, rel_path: str) -> None:
        clean_path = rel_path.lstrip("/\\").split("?")[0]
        if clean_path in ["", "index.html"]:
            target_file = UI_DIR / "index.html"
        else:
            target_file = UI_DIR / clean_path

        # Fallback for icon if not directly in UI_DIR
        if clean_path == "favicon.ico" and not target_file.exists():
            root_ico = Path(__file__).parent.parent / "promux.ico"
            if root_ico.exists():
                target_file = root_ico

        if not target_file.exists() or not target_file.is_file():
            target_file = UI_DIR / "index.html"

        if target_file.exists() and target_file.is_file():
            ext = target_file.suffix.lower()
            mime_map = {
                ".ico": "image/x-icon",
                ".png": "image/png",
                ".json": "application/json",
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".svg": "image/svg+xml",
            }
            mime_type = mime_map.get(ext)
            if not mime_type:
                mime_type, _ = mimetypes.guess_type(str(target_file))
            mime_type = mime_type or "text/html; charset=utf-8"

            content = target_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(404)
            self.end_headers()

    # =================================================================
    # Handlers
    # =================================================================

    def _handle_get_live_session(self) -> None:
        live = self.storage.get_live_token_data()
        if not live:
            self._send_json({"detected": False})
            return

        email = extract_email_from_jwt(live.get("id_token"))
        acc_token = live.get("token", {}).get("access_token")
        if not email and acc_token:
            try:
                qc = QuotaClient(acc_token)
                email = qc.fetch_email()
            except Exception:
                pass

        accounts = self.storage.list_accounts()
        saved_names = [a.name for a in accounts if a.email == email]

        self._send_json({
            "detected": True,
            "email": email,
            "saved_as": saved_names[0] if saved_names else None,
            "is_saved": len(saved_names) > 0,
        })

    def _handle_get_status(self, parsed: Any = None) -> None:
        query_params = parse_qs(parsed.query) if parsed else {}
        should_refresh = query_params.get("refresh", ["0"])[0] in ["1", "true", "yes"]

        accounts = self.storage.list_accounts()

        # If cache is completely empty or explicit refresh requested, refresh all concurrently
        if accounts and (should_refresh or not self.quota_cache):
            self.refresh_all_quotas_concurrently()

        active_name = self.storage.get_active_account_name()
        account_list = []

        for acct in accounts:
            is_active = (acct.name == active_name)
            data = acct.to_dict()
            data["is_active"] = is_active
            data["state"] = acct.get_state(is_active=is_active).value

            # Attach cached quota if available
            with self._cache_lock:
                cached_quota = self.quota_cache.get(acct.name)
            if cached_quota:
                data["quota"] = cached_quota
            else:
                data["quota"] = None

            account_list.append(data)

        # Live session detection
        live = self.storage.get_live_token_data()
        live_email = extract_email_from_jwt(live.get("id_token")) if live else None

        pending_alert = getattr(self.watcher, "pending_alert", None)
        antigravity_running = AntigravityManager.is_running()
        antigravity_installed = AntigravityManager.find_antigravity_executable() is not None

        self._send_json({
            "active": active_name,
            "accounts": account_list,
            "live_session": {
                "detected": live is not None,
                "email": live_email,
            },
            "watcher_running": self.watcher.running,
            "prompt_on_exhaustion": getattr(self.watcher, "prompt_on_exhaustion", True),
            "auto_restart_antigravity": getattr(self.watcher, "auto_restart_antigravity", True),
            "pending_alert": pending_alert,
            "antigravity_running": antigravity_running,
            "antigravity_installed": antigravity_installed,
            "events": self.watcher.events_history[-15:],
        })

    def _handle_save_profile(self, body: dict[str, Any]) -> None:
        name = body.get("name", "").strip()
        alias = body.get("alias", "").strip() or None
        if not name:
            self._send_json({"error": "Profile name is required"}, status=400)
            return

        try:
            token_data = body.get("token_data")
            acct = self.storage.save_profile(name=name, token_data=token_data, alias=alias)
            self._fetch_and_cache_quota(name)
            self._send_json({"success": True, "account": acct.to_dict()})
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)

    def _handle_account_update(self, body: dict[str, Any]) -> None:
        name = body.get("name", "").strip()
        new_name = body.get("new_name", name).strip()
        alias = body.get("alias")
        if not name:
            self._send_json({"error": "Current account name is required"}, status=400)
            return

        ok, msg = self.storage.rename_account(name, new_name, alias=alias)
        if ok:
            with self._cache_lock:
                if name in self.quota_cache and new_name != name:
                    self.quota_cache[new_name] = self.quota_cache.pop(name)
            self.watcher.log_event("INFO", f"Updated account '{name}' -> '{new_name}' (alias: {alias or 'None'})")
            self._send_json({"success": True, "message": msg, "name": new_name, "alias": alias})
        else:
            self._send_json({"error": msg}, status=400)

    def _handle_restart_antigravity(self) -> None:
        ok, msg = AntigravityManager.restart(force_launch=True)
        if ok:
            self.watcher.log_event("SUCCESS", "Antigravity IDE restarted manually via Promux.")
            self._send_json({"success": True, "message": msg})
        else:
            self.watcher.log_event("ERROR", f"Failed to restart Antigravity IDE: {msg}")
            self._send_json({"error": msg}, status=500)

    def _handle_alert_confirm(self, body: dict[str, Any]) -> None:
        target_account = body.get("target_account")
        restart_ide = body.get("restart_ide", True)
        ok, msg = self.watcher.confirm_alert(target_account=target_account, restart_ide=restart_ide)
        if ok:
            if target_account:
                self._fetch_and_cache_quota(target_account)
            self._send_json({"success": True, "message": msg})
        else:
            self._send_json({"error": msg}, status=500)

    def _handle_alert_dismiss(self) -> None:
        self.watcher.dismiss_alert()
        self.watcher.log_event("INFO", "Exhaustion alert dismissed by user.")
        self._send_json({"success": True})

    def _handle_toggle_setting(self, body: dict[str, Any]) -> None:
        if "prompt_on_exhaustion" in body:
            self.watcher.prompt_on_exhaustion = bool(body["prompt_on_exhaustion"])
        if "auto_restart_antigravity" in body:
            self.watcher.auto_restart_antigravity = bool(body["auto_restart_antigravity"])
        self._send_json({
            "success": True,
            "prompt_on_exhaustion": self.watcher.prompt_on_exhaustion,
            "auto_restart_antigravity": self.watcher.auto_restart_antigravity,
        })

    def _handle_switch_profile(self, body: dict[str, Any]) -> None:
        name = body.get("name", "").strip()
        restart_ide = body.get("restart_ide", True)
        if not name:
            self._send_json({"error": "Account name is required"}, status=400)
            return

        ok, msg = self.storage.switch_profile(name, restart_ide=restart_ide)
        if ok:
            # Check and refresh quota for this newly activated account
            self._fetch_and_cache_quota(name)
            extra = " (Antigravity IDE restarted)" if restart_ide and AntigravityManager.is_running() else ""
            self.watcher.log_event("SUCCESS", f"Switched active account to '{name}'{extra}")
            self._send_json({"success": True, "message": f"{msg}{extra}", "active": name})
        else:
            self._send_json({"error": msg}, status=400)

    def _handle_smart_switch(self, body: dict[str, Any] | None = None) -> None:
        restart_ide = body.get("restart_ide", True) if body else True
        ok, msg, target = self.smart_engine.smart_switch(restart_ide=restart_ide)
        if ok and target:
            self._fetch_and_cache_quota(target)
            extra = " (Antigravity IDE restarted)" if restart_ide and AntigravityManager.is_running() else ""
            self.watcher.log_event("SUCCESS", f"Smart Switch rotated to '{target}'{extra}")
            self._send_json({"success": True, "message": f"{msg}{extra}", "target": target})
        else:
            self._send_json({"error": msg}, status=400)

    def _fetch_and_cache_quota(self, name: str) -> QuotaSummary:
        acct = self.storage.load_state().get("accounts", {}).get(name)
        project_id = acct.get("project_id", "aicode-consumers") if acct else "aicode-consumers"
        token_data = self.storage.read_account_token(name)
        qs = QuotaSummary()

        if token_data:
            OAuthRefresher.refresh(token_data, force=False)
            acc_token = token_data.get("token", {}).get("access_token")
            if acc_token:
                try:
                    qc = QuotaClient(acc_token)
                    qs = qc.fetch_quota(project_id)
                except Exception as e:
                    qs.error = str(e)
            else:
                qs.error = "No access token"
        else:
            qs.error = "Token file not found"

        with self._cache_lock:
            self.quota_cache[name] = qs.to_dict()
        return qs

    @classmethod
    def refresh_all_quotas_concurrently(cls) -> None:
        """Fetches live quotas for all accounts concurrently in background worker threads."""
        accounts = cls.storage.list_accounts()
        if not accounts:
            return

        def _fetch_single(acct_name: str):
            try:
                acct_data = cls.storage.load_state().get("accounts", {}).get(acct_name)
                project_id = acct_data.get("project_id", "aicode-consumers") if acct_data else "aicode-consumers"
                token_data = cls.storage.read_account_token(acct_name)
                qs = QuotaSummary()
                if token_data:
                    OAuthRefresher.refresh(token_data, force=False)
                    acc_token = token_data.get("token", {}).get("access_token")
                    if acc_token:
                        qc = QuotaClient(acc_token)
                        qs = qc.fetch_quota(project_id)
                    else:
                        qs.error = "No access token"
                else:
                    qs.error = "Token not found"
                with cls._cache_lock:
                    cls.quota_cache[acct_name] = qs.to_dict()
            except Exception as err:
                with cls._cache_lock:
                    if acct_name not in cls.quota_cache:
                        cls.quota_cache[acct_name] = QuotaSummary(error=str(err)).to_dict()

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(accounts), 8)) as executor:
            list(executor.map(_fetch_single, [a.name for a in accounts]))

    def _handle_refresh_quota(self, body: dict[str, Any]) -> None:
        target = body.get("name", "all")
        if target == "all":
            self.refresh_all_quotas_concurrently()
            self._send_json({"success": True, "message": "Refreshed quotas for all accounts", "quotas": self.quota_cache})
        else:
            qs = self._fetch_and_cache_quota(target)
            self._send_json({"success": True, "quota": qs.to_dict()})

    def _handle_refresh_token(self, body: dict[str, Any]) -> None:
        name = body.get("name", "").strip()
        token_data = self.storage.read_account_token(name)
        if not token_data:
            self._send_json({"error": f"Token for '{name}' not found"}, status=404)
            return

        ok, msg = OAuthRefresher.refresh(token_data, force=True)
        if ok:
            self.storage.write_account_token(name, token_data)
            # If active, write to Windows Credential Manager as well
            if self.storage.get_active_account_name() == name:
                WindowsCredentialBridge.write_token(token_data)
            self._send_json({"success": True, "message": msg})
        else:
            self._send_json({"error": msg}, status=500)

    def _handle_delete_profile(self, body: dict[str, Any]) -> None:
        name = body.get("name", "").strip()
        ok = self.storage.remove_profile(name)
        with self._cache_lock:
            if name in self.quota_cache:
                del self.quota_cache[name]
        self._send_json({"success": ok})

    def _handle_clear_cooldown(self, body: dict[str, Any]) -> None:
        name = body.get("name", "").strip()
        self.storage.clear_cooldown(name)
        self._send_json({"success": True})

    def _handle_toggle_watcher(self, body: dict[str, Any]) -> None:
        enabled = body.get("enabled", False)
        if enabled:
            self.watcher.start()
        else:
            self.watcher.stop()
        self._send_json({"success": True, "watcher_running": self.watcher.running})

    def _handle_import_token(self, body: dict[str, Any]) -> None:
        name = body.get("name", "").strip()
        raw_token = body.get("token_raw", "").strip()
        if not name or not raw_token:
            self._send_json({"error": "Both name and token payload are required"}, status=400)
            return

        token_dict = None
        # Check if JSON
        try:
            parsed = json.loads(raw_token)
            if isinstance(parsed, dict) and "token" in parsed:
                token_dict = parsed
            elif isinstance(parsed, dict) and "refresh_token" in parsed:
                token_dict = {
                    "token": {
                        "access_token": parsed.get("access_token", ""),
                        "refresh_token": parsed["refresh_token"],
                        "token_type": "Bearer",
                        "expiry": parsed.get("expiry", ""),
                    },
                    "auth_method": "consumer",
                }
        except Exception:
            # Assume raw string is a refresh token
            token_dict = {
                "token": {
                    "access_token": "",
                    "refresh_token": raw_token,
                    "token_type": "Bearer",
                    "expiry": "",
                },
                "auth_method": "consumer",
            }

        try:
            # Auto-refresh to ensure access token is live
            ok, msg = OAuthRefresher.refresh(token_dict, force=True)
            if not ok:
                self._send_json({"error": f"Failed to authenticate with Google: {msg}"}, status=400)
                return

            acct = self.storage.save_profile(name=name, token_data=token_dict)
            self._fetch_and_cache_quota(name)
            self._send_json({"success": True, "account": acct.to_dict()})
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)

    def _handle_restart_language_server(self) -> None:
        try:
            # Terminate language_server.exe cleanly; Antigravity will auto-restart it
            subprocess.run(["taskkill", "/F", "/IM", "language_server.exe"], capture_output=True)
            self.watcher.log_event("INFO", "Restarted Antigravity language server.")
            self._send_json({"success": True, "message": "Language server signaled to restart."})
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)

    # =================================================================
    # Smart Google 1-Click OAuth Handlers
    # =================================================================

    def _handle_oauth_start(self, body: dict[str, Any]) -> None:
        alias = body.get("alias", "").strip() or None
        state = secrets.token_urlsafe(16)
        
        with self._cache_lock:
            self.pending_oauth[state] = {
                "alias": alias,
                "status": "pending",
                "created_at": time.time(),
                "account": None,
                "email": None,
                "error": None,
            }

        port = self.server.server_port
        redirect_uri = f"http://127.0.0.1:{port}/oauth/callback"
        auth_url = OAuthFlowManager.build_auth_url(redirect_uri, state)

        # Open in default browser
        threading.Thread(target=lambda: webbrowser.open(auth_url), daemon=True).start()

        self._send_json({
            "success": True,
            "state": state,
            "auth_url": auth_url,
            "redirect_uri": redirect_uri,
        })

    def _handle_oauth_check(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        state = qs.get("state", [None])[0]
        if not state or state not in self.pending_oauth:
            self._send_json({"status": "not_found", "error": "Invalid or expired session"}, status=404)
            return

        with self._cache_lock:
            session = dict(self.pending_oauth[state])

        self._send_json({
            "status": session["status"],
            "account": session.get("account"),
            "email": session.get("email"),
            "error": session.get("error"),
        })

    def _handle_oauth_callback(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        code = qs.get("code", [None])[0]
        state = qs.get("state", [None])[0]
        error = qs.get("error", [None])[0]

        session = self.pending_oauth.get(state) if state else None

        if error:
            err_msg = qs.get("error_description", [error])[0]
            if session:
                with self._cache_lock:
                    session["status"] = "error"
                    session["error"] = err_msg
            self._serve_oauth_result_page(
                success=False,
                title="Authorization Cancelled",
                message=f"Google sign-in was cancelled or encountered an error: {err_msg}",
            )
            return

        if not code:
            self._serve_oauth_result_page(
                success=False,
                title="Missing Authorization Code",
                message="No authorization code was received in the Google OAuth response.",
            )
            return

        port = self.server.server_port
        redirect_uri = f"http://127.0.0.1:{port}/oauth/callback"

        ok, res = OAuthFlowManager.exchange_code(code, redirect_uri)
        if not ok or not isinstance(res, dict):
            err_text = str(res)
            if session:
                with self._cache_lock:
                    session["status"] = "error"
                    session["error"] = err_text
            self._serve_oauth_result_page(
                success=False,
                title="Token Exchange Failed",
                message=f"Failed to exchange authorization code with Google OAuth servers: {err_text}",
            )
            return

        token_dict = OAuthFlowManager.build_token_dict(res)
        email = extract_email_from_jwt(token_dict.get("id_token"))
        acc_token = token_dict.get("token", {}).get("access_token")
        if not email and acc_token:
            try:
                qc = QuotaClient(acc_token)
                email = qc.fetch_email()
            except Exception:
                pass

        desired_name = session.get("alias") if session else None
        existing_accounts = self.storage.list_accounts()
        existing_by_email = next((a for a in existing_accounts if a.email == email), None)

        if desired_name:
            clean_name = re.sub(r'[^a-zA-Z0-9_\-]', '', desired_name) or "account"
        elif existing_by_email:
            clean_name = existing_by_email.name
        elif email:
            base_name = email.split("@")[0]
            base_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', base_name)
            clean_name = base_name
            existing_names = {a.name for a in existing_accounts}
            counter = 2
            while clean_name in existing_names:
                clean_name = f"{base_name}_{counter}"
                counter += 1
        else:
            clean_name = f"account_{int(time.time())}"

        try:
            acct = self.storage.save_profile(name=clean_name, token_data=token_dict, email=email)
            self._fetch_and_cache_quota(clean_name)
            self.watcher.log_event("SUCCESS", f"Google Login: Connected account '{clean_name}' ({email or 'N/A'})")

            if session:
                with self._cache_lock:
                    session["status"] = "success"
                    session["account"] = clean_name
                    session["email"] = email

            self._serve_oauth_result_page(
                success=True,
                title="Connected Successfully!",
                message=f"Google account <b>{email or clean_name}</b> has been linked to Promux as profile <b>{clean_name}</b>.",
                account_name=clean_name,
            )
        except Exception as e:
            if session:
                with self._cache_lock:
                    session["status"] = "error"
                    session["error"] = str(e)
            self._serve_oauth_result_page(
                success=False,
                title="Vaulting Error",
                message=f"Failed to vault account into Windows Credential Manager: {str(e)}",
            )

    def _serve_oauth_result_page(
        self,
        success: bool,
        title: str,
        message: str,
        account_name: str | None = None,
    ) -> None:
        status_color = "#10b981" if success else "#f43f5e"
        status_icon = """<svg width="60" height="60" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>""" if success else """<svg width="60" height="60" viewBox="0 0 24 24" fill="none" stroke="#f43f5e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>"""
        
        html = f"""<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
  <meta charset="UTF-8">
  <title>{title} — Promux</title>
  <style>
    body {{
      background: #070a12;
      background-image: 
        radial-gradient(circle at 50% 20%, rgba(6, 182, 212, 0.1) 0%, transparent 60%),
        radial-gradient(circle at 80% 80%, rgba(139, 92, 246, 0.08) 0%, transparent 50%);
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      margin: 0;
      text-align: center;
      user-select: none;
    }}
    .card {{
      background: rgba(15, 23, 42, 0.85);
      backdrop-filter: blur(24px);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 20px;
      padding: 42px 36px;
      max-width: 460px;
      box-shadow: 0 25px 60px rgba(0,0,0,0.6);
      animation: pop 0.35s cubic-bezier(0.16, 1, 0.3, 1);
    }}
    @keyframes pop {{
      from {{ opacity: 0; transform: scale(0.92); }}
      to {{ opacity: 1; transform: scale(1); }}
    }}
    .icon {{ margin-bottom: 20px; display: inline-flex; }}
    h1 {{ font-size: 1.35rem; font-weight: 700; margin-bottom: 12px; color: {status_color}; letter-spacing: -0.02em; }}
    p {{ color: #94a3b8; font-size: 0.92rem; line-height: 1.6; margin-bottom: 24px; }}
    .btn {{
      background: linear-gradient(135deg, #06b6d4, #3b82f6);
      color: white;
      border: none;
      padding: 10px 24px;
      border-radius: 10px;
      font-weight: 600;
      cursor: pointer;
      font-size: 0.88rem;
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
      box-shadow: 0 4px 15px rgba(6, 182, 212, 0.3);
    }}
    .btn:hover {{ filter: brightness(1.1); transform: translateY(-1px); box-shadow: 0 6px 20px rgba(6, 182, 212, 0.45); }}
    .countdown {{ font-size: 0.78rem; color: #64748b; margin-top: 18px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{status_icon}</div>
    <h1>{title}</h1>
    <p>{message}</p>
    <button class="btn" onclick="window.close()">Close Tab</button>
    <div class="countdown" id="timer">Closing automatically in 3 seconds...</div>
  </div>
  <script>
    let sec = 3;
    const t = setInterval(() => {{
      sec--;
      const el = document.getElementById('timer');
      if (el) el.innerText = 'Closing automatically in ' + sec + ' seconds...';
      if (sec <= 0) {{
        clearInterval(t);
        window.close();
      }}
    }}, 1000);
  </script>
</body>
</html>"""
        body_bytes = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)


def create_server(host: str = "127.0.0.1", port: int = 18420) -> ThreadingHTTPServer:
    storage = WindowsStorageEngine()
    watcher = AntigravityLogWatcher(storage)
    smart_engine = SmartSwitchEngine(storage)

    PromuxApiHandler.storage = storage
    PromuxApiHandler.watcher = watcher
    PromuxApiHandler.smart_engine = smart_engine

    # Immediate concurrent quota refresh in background thread on server startup
    threading.Thread(target=PromuxApiHandler.refresh_all_quotas_concurrently, daemon=True).start()

    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), PromuxApiHandler)
    return server
