"""T017 (actualizado al tramo B2): herramientas expuestas por el servidor MCP.

El esqueleto devolvía una lista vacía; con el registro del tramo B2 el
servidor expone exactamente las cuatro herramientas del registro y ninguna
con nombre confirm/execute. La guarda se comprueba en dos niveles: las
definiciones del registro Y el tools/list del protocolo MCP.
"""

from __future__ import annotations

import uuid

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.interfaces.api.deps.auth import create_token
from wallet_ai.interfaces.mcp.server import build_mcp_server, list_tools

TOOL_NAMES = [
    "get_balance",
    "list_transactions",
    "find_recipient",
    "draft_transfer",
]


def test_mcp_no_confirm_or_execute():
    tools = list_tools()
    assert tools  # ya no es un esqueleto vacio
    for t in tools:
        name = getattr(t, "name", "")
        assert "confirm" not in str(name).lower()
        assert "execute" not in str(name).lower()


def test_mcp_exposes_exactly_the_four_registry_tools():
    assert [t.name for t in list_tools()] == TOOL_NAMES


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_protocol_tools_list_has_no_confirm_or_execute():
    """La guarda tambien contra el tools/list del protocolo, no solo del registro."""
    token = create_token(
        sub=str(uuid.uuid4()), role="cliente", secret=Settings().AUTH_JWT_SECRET
    )
    server = build_mcp_server(token=token)

    async with InMemoryTransport(server) as streams:
        read, write = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()

    names = [t.name for t in result.tools]
    assert sorted(names) == sorted(TOOL_NAMES)
    for name in names:
        assert "confirm" not in name.lower()
        assert "execute" not in name.lower()
