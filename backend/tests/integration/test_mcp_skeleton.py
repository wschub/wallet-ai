from wallet_ai.interfaces.mcp import server


def test_mcp_no_confirm_or_execute():
    tools = server.list_tools()
    for t in tools:
        name = getattr(t, "name", "") or (t.get("name") if isinstance(t, dict) else "")
        assert "confirm" not in str(name).lower()
        assert "execute" not in str(name).lower()
