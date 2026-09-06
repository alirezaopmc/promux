import urllib.error
import urllib.request

from promux.constants import (
    DEFAULT_HTTP_TIMEOUT,
    LOAD_ENDPOINT,
    USER_AGENT,
    USERINFO_URL,
)
from promux.quota import QuotaClient

MOCK_METADATA_RESP = {
    "cloudaicompanionProject": "aicode-consumers",
    "currentTier": {"id": "free-tier", "name": "Antigravity"},
}

MOCK_QUOTA_RESP = {
    "groups": [
        {
            "displayName": "Gemini Models",
            "buckets": [
                {
                    "bucketId": "gemini-weekly",
                    "window": "weekly",
                    "remainingFraction": 0.57,
                    "resetTime": "2026-09-08T17:46:10Z",
                },
                {
                    "bucketId": "gemini-5h",
                    "window": "5h",
                    "remainingFraction": 0.85,
                    "resetTime": "2026-09-03T22:55:18Z",
                },
            ],
        },
        {
            "displayName": "Claude and GPT models",
            "buckets": [
                {
                    "bucketId": "3p-weekly",
                    "window": "weekly",
                    "remainingFraction": 0.43,
                    "resetTime": "2026-09-09T01:11:22Z",
                },
                {
                    "bucketId": "3p-5h",
                    "window": "5h",
                    "remainingFraction": 0.25,
                    "resetTime": "2026-09-04T02:36:43Z",
                },
            ],
        },
    ]
}

MOCK_USERINFO_RESP = {"email": "developer@manova.space", "email_verified": True}


def test_quota_client_parsing(monkeypatch):
    client = QuotaClient(token="mock_token")

    def mock_post(endpoint, payload):
        if "loadCodeAssist" in endpoint:
            return MOCK_METADATA_RESP
        if "retrieveUserQuotaSummary" in endpoint:
            return MOCK_QUOTA_RESP
        return {}

    def mock_get(url):
        return MOCK_USERINFO_RESP

    monkeypatch.setattr(client, "_post", mock_post)
    monkeypatch.setattr(client, "_get", mock_get)

    meta = client.load_metadata()
    assert meta["project_id"] == "aicode-consumers"
    assert meta["tier"] == "free-tier"
    assert meta["plan_name"] == "Antigravity"

    quota = client.get_quota("aicode-consumers")
    assert quota.gemini_5h_remaining == 0.85
    assert quota.gemini_weekly_remaining == 0.57
    assert quota.third_party_5h_remaining == 0.25
    assert quota.third_party_weekly_remaining == 0.43
    assert quota.min_short_window == 0.25
    assert quota.gemini_5h_reset == "2026-09-03T22:55:18Z"
    assert quota.gemini_weekly_reset == "2026-09-08T17:46:10Z"
    assert quota.third_party_5h_reset == "2026-09-04T02:36:43Z"
    assert quota.third_party_weekly_reset == "2026-09-09T01:11:22Z"

    email = client.fetch_email()
    assert email == "developer@manova.space"


def test_quota_client_load_metadata_project_dict(monkeypatch):
    client = QuotaClient(token="mock_token")

    def mock_post(endpoint, payload):
        return {
            "cloudaicompanionProject": {"id": "custom-project-123"},
            "currentTier": {"id": "standard-tier", "name": "Antigravity Standard"},
        }

    monkeypatch.setattr(client, "_post", mock_post)
    meta = client.load_metadata()
    assert meta["project_id"] == "custom-project-123"
    assert meta["tier"] == "standard-tier"
    assert meta["plan_name"] == "Antigravity Standard"


def test_quota_client_fetch_email_error(monkeypatch):
    client = QuotaClient(token="mock_token")

    def mock_get_error(url):
        raise urllib.error.URLError("Network unreachable")

    monkeypatch.setattr(client, "_get", mock_get_error)
    assert client.fetch_email() is None


def test_quota_client_empty_quota(monkeypatch):
    client = QuotaClient(token="mock_token")

    def mock_post(endpoint, payload):
        return {"groups": []}

    monkeypatch.setattr(client, "_post", mock_post)
    quota = client.get_quota("custom-proj")
    assert quota.gemini_5h_remaining == 1.0
    assert quota.gemini_weekly_remaining == 1.0
    assert quota.third_party_5h_remaining == 1.0
    assert quota.third_party_weekly_remaining == 1.0
    assert quota.min_short_window == 1.0


class MockHttpResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def test_quota_client_post_request(monkeypatch):
    client = QuotaClient(token="test_token", timeout=12.0)
    captured_req = None
    captured_timeout = None

    def mock_urlopen(req, timeout=None):
        nonlocal captured_req, captured_timeout
        captured_req = req
        captured_timeout = timeout
        return MockHttpResponse(b'{"result": "ok"}')

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    resp = client._post(LOAD_ENDPOINT, {"test": 123})
    assert resp == {"result": "ok"}
    assert captured_req.full_url == f"{client.base_url}{LOAD_ENDPOINT}"
    assert captured_req.get_method() == "POST"
    assert captured_req.headers["Authorization"] == "Bearer test_token"
    assert captured_req.headers["Content-type"] == "application/json"
    assert captured_req.headers["User-agent"] == USER_AGENT
    assert captured_req.data == b'{"test": 123}'
    assert captured_timeout == 12.0


def test_quota_client_get_request(monkeypatch):
    client = QuotaClient(token="test_token")
    captured_req = None
    captured_timeout = None

    def mock_urlopen(req, timeout=None):
        nonlocal captured_req, captured_timeout
        captured_req = req
        captured_timeout = timeout
        return MockHttpResponse(b'{"email": "user@example.com"}')

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    resp = client._get(USERINFO_URL)
    assert resp == {"email": "user@example.com"}
    assert captured_req.full_url == USERINFO_URL
    assert captured_req.get_method() == "GET"
    assert captured_req.headers["Authorization"] == "Bearer test_token"
    assert captured_req.headers["User-agent"] == USER_AGENT
    assert captured_timeout == DEFAULT_HTTP_TIMEOUT
