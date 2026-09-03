import json
import urllib.error
import urllib.request
from typing import Dict, Any, Optional
from .constants import (
    CODE_ASSIST_BASE_URL,
    FALLBACK_CODE_ASSIST_BASE_URL,
    LOAD_ENDPOINT,
    QUOTA_ENDPOINT,
    USERINFO_URL,
    USER_AGENT,
    DEFAULT_HTTP_TIMEOUT,
    detect_code_assist_url,
)
from .models import QuotaSummary


class QuotaClient:
    """Client for Cloud Code Assist and Google UserInfo REST endpoints."""

    def __init__(
        self,
        token: str,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT
    ):
        self.token = token
        self.base_url = (base_url or detect_code_assist_url()).rstrip("/")
        self.timeout = timeout

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

        urls_to_try = [f"{self.base_url}{endpoint}"]
        if self.base_url != FALLBACK_CODE_ASSIST_BASE_URL:
            urls_to_try.append(f"{FALLBACK_CODE_ASSIST_BASE_URL}{endpoint}")

        last_error = None
        for url in urls_to_try:
            req = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
                last_error = e
                continue
        if last_error:
            raise last_error
        return {}

    def _get(self, url: str) -> Dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "User-Agent": USER_AGENT,
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def fetch_email(self) -> Optional[str]:
        """Fetch primary email address from Google OAuth2 UserInfo endpoint."""
        try:
            data = self._get(USERINFO_URL)
            if isinstance(data, dict):
                return data.get("email")
            return None
        except Exception:
            return None

    def load_metadata(self) -> Dict[str, Any]:
        """Load companion project metadata and tier information."""
        payload = {
            "metadata": {
                "ideType": "ANTIGRAVITY",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        }
        resp = self._post(LOAD_ENDPOINT, payload)
        project = resp.get("cloudaicompanionProject")
        if isinstance(project, dict):
            project_id = project.get("id", "")
        else:
            project_id = str(project) if project else ""

        tier_info = resp.get("currentTier")
        if not isinstance(tier_info, dict):
            tier_info = {}

        return {
            "project_id": project_id,
            "tier": tier_info.get("id", "free-tier"),
            "plan_name": tier_info.get("name", "Antigravity"),
        }

    def get_quota(self, project_id: str) -> QuotaSummary:
        """Retrieve quota summary across Gemini and third-party models."""
        resp = self._post(QUOTA_ENDPOINT, {"project": project_id})
        qs = QuotaSummary()

        for group in resp.get("groups", []):
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
                else:  # 3p / Claude / GPT
                    if window == "5h" or "short" in window:
                        qs.third_party_5h_remaining = rem_val
                        qs.third_party_5h_reset = reset
                    elif window == "weekly":
                        qs.third_party_weekly_remaining = rem_val
                        qs.third_party_weekly_reset = reset

        return qs
