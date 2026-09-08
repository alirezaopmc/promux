from pathlib import Path
import pytest

from promux.adapters.base import BaseToolAdapter
from promux.adapters.registry import ToolRegistry
from promux.adapters.stubs import ClaudeAdapter, CodexAdapter, CursorAdapter
from promux.constants import PROMUX_HOME
from promux.storage import StorageEngine


class DummyAdapter(BaseToolAdapter):
    name = "dummy"
    display_name = "Dummy Tool"

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        home = promux_home or Path("/tmp/dummy")
        return StorageEngine(promux_home=home / "dummy")


def test_tool_registry():
    reg = ToolRegistry()
    dummy = DummyAdapter()
    reg.register(dummy)

    assert reg.has_tool("dummy")
    assert not reg.has_tool("other")
    assert reg.get("dummy") is dummy
    assert "dummy" in [t.name for t in reg.list_all()]


def test_tool_registry_unknown():
    reg = ToolRegistry()
    with pytest.raises(KeyError) as exc_info:
        reg.get("nonexistent")
    assert "Unknown tool 'nonexistent'" in str(exc_info.value)


def test_tool_registry_default():
    reg = ToolRegistry()
    with pytest.raises(RuntimeError) as exc_info:
        reg.default_tool()
    assert "No default tool registered" in str(exc_info.value)

    dummy1 = DummyAdapter()
    reg.register(dummy1)
    assert reg.default_tool() is dummy1

    class OtherAdapter(DummyAdapter):
        name = "other"
        display_name = "Other Tool"

    dummy2 = OtherAdapter()
    reg.register(dummy2, default=True)
    assert reg.default_tool() is dummy2


def test_base_tool_adapter_capabilities():
    class FullAdapter(DummyAdapter):
        @property
        def supports_quota(self) -> bool:
            return True

        @property
        def supports_watch(self) -> bool:
            return True

        @property
        def supports_refresh(self) -> bool:
            return True

    full = FullAdapter()
    assert full.list_capabilities() == ["vault", "quota", "watch", "refresh"]


def test_stub_adapters(tmp_path):
    for adapter_cls in [ClaudeAdapter, CodexAdapter, CursorAdapter]:
        adapter = adapter_cls()
        assert adapter.name in ["claude", "codex", "cursor"]
        assert adapter.supports_quota is False
        assert adapter.supports_watch is False
        assert adapter.supports_refresh is False
        assert adapter.list_capabilities() == ["vault"]

        storage = adapter.get_storage(tmp_path)
        assert storage.home == tmp_path / "tools" / adapter.name

        storage_default = adapter.get_storage()
        assert storage_default.home == PROMUX_HOME / "tools" / adapter.name

        qs, project_id, err = adapter.fetch_quota(storage, "test")
        assert qs is None
        assert project_id is None
        assert err is not None
        assert "not supported" in err.lower()

        ok, msg = adapter.refresh_account(storage, "test")
        assert ok is False
        assert "not supported" in msg.lower()

        with pytest.raises(NotImplementedError):
            adapter.create_watcher(storage)
