import pytest


@pytest.fixture
def sample_token_dict():
    return {
        "token": {
            "access_token": "ya29.mock_access_token",
            "token_type": "Bearer",
            "refresh_token": "mock_refresh_token",
            "expiry": "2026-09-03T22:00:00Z",
        },
        "auth_method": "consumer",
    }


@pytest.fixture
def fake_promux_home(tmp_path):
    promux_home = tmp_path / ".promux"
    promux_home.mkdir()
    return promux_home


@pytest.fixture
def storage_with_profiles(tmp_path, sample_token_dict):
    import json
    from promux.adapters.agy import AgyAdapter

    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True, exist_ok=True)
    live_token = gemini_home / "antigravity-oauth-token"
    live_token.write_text(json.dumps(sample_token_dict))
    live_token.chmod(0o600)

    adapter = AgyAdapter(gemini_home=gemini_home)
    storage = adapter.get_storage(promux_home=promux_home)
    storage.save_profile("work", email="work@company.com", project_id="test-proj")
    storage.save_profile("personal", email="personal@home.org", project_id="test-proj-2")
    return storage

