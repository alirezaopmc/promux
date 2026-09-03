import json
import os
import shutil
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Any, List, Generator

from .constants import PROMUX_HOME, GEMINI_CLI_HOME, LIVE_TOKEN, ACCOUNTS_DIR, STATE_FILE, LOCK_FILE
from .lock import file_lock
from .models import AccountMeta, AccountState


class StorageEngine:
    def __init__(self, promux_home: Optional[Path] = None, gemini_home: Optional[Path] = None):
        self.home = Path(promux_home or PROMUX_HOME)
        self.gemini_home = Path(gemini_home or GEMINI_CLI_HOME)
        self.accounts_dir = self.home / "accounts"
        self.state_file = self.home / "state.json"
        self.lock_file = self.home / "manager.lock"
        self.live_token = self.gemini_home / "antigravity-oauth-token"
        self._local = threading.local()

        self.accounts_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _tx_state(self) -> Optional[Dict[str, Any]]:
        return getattr(self._local, "tx_state", None)

    @_tx_state.setter
    def _tx_state(self, val: Optional[Dict[str, Any]]):
        self._local.tx_state = val

    def _atomic_write_json(self, target_path: Path, data: Dict[str, Any]):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target_path)

    @contextmanager
    def transaction(self) -> Generator[Dict[str, Any], None, None]:
        if self._tx_state is not None:
            yield self._tx_state
            return

        with file_lock(self.lock_file):
            state = self.load_state()
            self._tx_state = state
            try:
                yield state
            finally:
                self._tx_state = None
            self._atomic_write_json(self.state_file, state)

    def load_state(self) -> Dict[str, Any]:
        if self._tx_state is not None:
            return self._tx_state
        if not self.state_file.exists():
            return {"active": None, "accounts": {}}
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"active": None, "accounts": {}}

    def save_state(self, state: Dict[str, Any]):
        if self._tx_state is not None:
            self._tx_state.clear()
            self._tx_state.update(state)
            return
        with file_lock(self.lock_file):
            self._atomic_write_json(self.state_file, state)

    def save_profile(
        self,
        name: str,
        email: Optional[str] = None,
        project_id: Optional[str] = None,
        plan_type: str = "STANDARD",
    ) -> AccountMeta:
        if not self.live_token.exists():
            raise FileNotFoundError(f"Active token file not found at {self.live_token}")

        with self.transaction() as state:
            acct_dir = self.accounts_dir / name
            acct_dir.mkdir(parents=True, exist_ok=True)
            dest_token = acct_dir / "antigravity-oauth-token"
            tmp_dest = dest_token.with_suffix(".tmp")
            shutil.copy2(self.live_token, tmp_dest)
            tmp_dest.chmod(0o600)
            os.replace(tmp_dest, dest_token)

            now = datetime.now(timezone.utc)
            acct = AccountMeta(
                name=name,
                enabled=True,
                saved_at=now,
                last_used_at=now,
                email=email,
                project_id=project_id,
                plan_type=plan_type,
            )
            state["accounts"][name] = acct.to_dict()
            if not state.get("active"):
                state["active"] = name
            return acct

    def switch_profile(self, name: str) -> bool:
        with self.transaction() as state:
            if name not in state.get("accounts", {}):
                return False

            src_token = self.accounts_dir / name / "antigravity-oauth-token"
            if not src_token.exists():
                return False

            # I4: Sync refreshed live token back to outgoing vault on switch
            prev_active = state.get("active")
            if prev_active and prev_active != name and self.live_token.exists():
                prev_vault = self.accounts_dir / prev_active
                if prev_vault.exists():
                    prev_vault_token = prev_vault / "antigravity-oauth-token"
                    tmp_prev = prev_vault_token.with_suffix(".tmp")
                    shutil.copy2(self.live_token, tmp_prev)
                    tmp_prev.chmod(0o600)
                    os.replace(tmp_prev, prev_vault_token)

            # I2: Atomic Hot-Swapping of live_token
            self.live_token.parent.mkdir(parents=True, exist_ok=True)
            tmp_token = self.live_token.with_suffix(".tmp")
            shutil.copy2(src_token, tmp_token)
            tmp_token.chmod(0o600)
            os.replace(tmp_token, self.live_token)

            state["active"] = name
            state["accounts"][name]["last_used_at"] = datetime.now(timezone.utc).isoformat()
            return True

    def remove_profile(self, name: str) -> bool:
        with self.transaction() as state:
            if name not in state.get("accounts", {}):
                return False

            acct_dir = self.accounts_dir / name
            if acct_dir.exists():
                shutil.rmtree(acct_dir, ignore_errors=True)

            del state["accounts"][name]
            if state.get("active") == name:
                state["active"] = None
            return True

    def get_active_profile(self) -> Optional[str]:
        state = self.load_state()
        return state.get("active")

    def get_account(self, name: str) -> Optional[AccountMeta]:
        state = self.load_state()
        data = state.get("accounts", {}).get(name)
        return AccountMeta.from_dict(data) if data else None

    def list_accounts(self) -> List[AccountMeta]:
        state = self.load_state()
        return [AccountMeta.from_dict(d) for d in state.get("accounts", {}).values()]
