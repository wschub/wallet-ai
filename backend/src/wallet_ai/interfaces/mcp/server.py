from __future__ import annotations

ALLOWED_TOOLS = []


def list_tools():
    # must not contain confirm or execute
    tools = []
    for t in tools:
        name = t.get("name", "")
        if "confirm" in name or "execute" in name:
            raise ValueError("forbidden tool")
    return tools
