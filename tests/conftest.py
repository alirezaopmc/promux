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
