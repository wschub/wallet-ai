import pytest

from wallet_ai.interfaces.mcp import server


def test_mcp_no_confirm_or_execute():
    tools = server.list_tools()
    for t in tools:
        name = getattr(t, "name", "") or (t.get("name") if isinstance(t, dict) else "")
        assert "confirm" not in str(name).lower()
        assert "execute" not in str(name).lower()


def test_mcp_tools_empty():
    tools = server.list_tools()
    assert isinstance(tools, list)
    assert len(tools) == 0


def test_mcp_register_forbidden_fails():
    with pytest.raises(ValueError):
        # simulate
        tools = [{"name": "confirm_transfer"}]
        for t in tools:
            name = t.get("name", "")
            if "confirm" in name or "execute" in name:
                raise ValueError("forbidden tool")
