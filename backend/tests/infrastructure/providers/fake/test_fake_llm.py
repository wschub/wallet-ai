import pytest

from wallet_ai.domain.ports.llm import LLMResponse, ToolCallRequest
from wallet_ai.infrastructure.providers.fake.llm import ABSTENTION_MESSAGE, FakeLLM


@pytest.mark.asyncio
async def test_fake_llm_tool_call_then_answer():
    script = [
        {"type": "tool_call", "tool": "search_docs", "args": {"q": "saldo"}},
        {
            "type": "final",
            "content": "Tu saldo es X",
            "citations": ["doc-1"],
        },
    ]
    llm = FakeLLM(script=script)
    r1 = await llm.generate(messages=[{"role": "user", "content": "saldo"}], docs=[{"id": "doc-1"}])
    assert r1["type"] == "tool_call"
    assert r1["tool"] == "search_docs"
    r2 = await llm.generate(messages=[{"role": "user", "content": "saldo"}], docs=[{"id": "doc-1"}])
    assert r2["type"] == "final"
    assert "doc-1" in r2.get("citations", [])


@pytest.mark.asyncio
async def test_fake_llm_deterministic():
    script = [{"type": "final", "content": "ok", "citations": ["a"]}]
    llm1 = FakeLLM(script=script)
    llm2 = FakeLLM(script=script)
    m = [{"role": "u", "content": "q"}]
    d = [{"id": "a"}]
    assert await llm1.generate(m, d) == await llm2.generate(m, d)


@pytest.mark.asyncio
async def test_fake_llm_tool_call_emits_args():
    script = [{"type": "tool_call", "tool": "draft_transfer", "args": {"amount": 100}}]
    llm = FakeLLM(script=script)
    r = await llm.generate(messages=[], docs=[])
    assert r["type"] == "tool_call"
    assert r["tool"] == "draft_transfer"
    assert r["args"]["amount"] == 100


@pytest.mark.asyncio
async def test_fake_llm_final_cites_docs():
    script = [{"type": "final", "content": "res"}]
    llm = FakeLLM(script=script)
    r = await llm.generate(messages=[], docs=[{"id": "d1"}, {"id": "d2"}])
    assert r["type"] == "final"
    assert set(r["citations"]) >= {"d1", "d2"}


@pytest.mark.asyncio
async def test_fake_llm_no_docs_no_invented_citations():
    script = [{"type": "final", "content": "res"}]
    llm = FakeLLM(script=script)
    r = await llm.generate(messages=[], docs=[])
    assert r["type"] == "final"
    assert r["citations"] == []


@pytest.mark.asyncio
async def test_fake_llm_no_network():
    import sys

    script = [{"type": "final", "content": "ok"}]
    llm = FakeLLM(script=script)
    assert "httpx" not in sys.modules
    assert "socket" not in str(llm.generate)
    await llm.generate(messages=[], docs=[])


# --------------------------------------------------------------------------
# chat() modo heuristico (tramo C, T039): determinista y en espanol
# --------------------------------------------------------------------------


def _user(content: str) -> list[dict]:
    return [{"role": "user", "content": content}]


def _tool_envelope(tool: str, data: dict) -> list[dict]:
    import json

    return [{"role": "tool", "content": json.dumps({"tool": tool, "data": data})}]


@pytest.mark.asyncio
async def test_chat_heuristic_saldo_calls_get_balance():
    llm = FakeLLM()
    r = await llm.chat(_user("¿Cuál es mi saldo?"))
    assert isinstance(r, LLMResponse)
    assert r.content == ""
    assert r.tool_calls == (ToolCallRequest(name="get_balance", arguments={}),)


@pytest.mark.asyncio
async def test_chat_heuristic_movimientos_with_optional_limit():
    llm = FakeLLM()
    r = await llm.chat(_user("muéstrame mis últimos 3 movimientos"))
    assert r.tool_calls[0].name == "list_transactions"
    assert r.tool_calls[0].arguments == {"limit": 3}

    r2 = await llm.chat(_user("dame 50 movimientos"))
    assert r2.tool_calls[0].arguments == {"limit": 50}

    r3 = await llm.chat(_user("quiero ver mis transacciones"))
    assert r3.tool_calls[0].arguments == {"limit": 20}


@pytest.mark.asyncio
async def test_chat_heuristic_transfer_calls_find_recipient():
    llm = FakeLLM()
    r = await llm.chat(_user("transfiere 2.50 a María García"))
    assert r.tool_calls[0].name == "find_recipient"
    assert r.tool_calls[0].arguments == {"query": "María García"}

    r2 = await llm.chat(_user("envíale 100 pesos a Juan"))
    assert r2.tool_calls[0].name == "find_recipient"
    assert r2.tool_calls[0].arguments["query"] == "Juan"


@pytest.mark.asyncio
async def test_chat_heuristic_abstains_explicitly():
    llm = FakeLLM()
    r = await llm.chat(_user("¿cuál es la capital de Francia?"))
    assert r.tool_calls == ()
    assert r.content == ABSTENTION_MESSAGE


@pytest.mark.asyncio
async def test_chat_heuristic_final_text_built_from_tool_results_only():
    llm = FakeLLM()
    r = await llm.chat(
        _tool_envelope("get_balance", {"balance": "150.00", "currency": "COP"})
    )
    assert "150.00" in r.content
    assert "COP" in r.content

    r2 = await llm.chat(
        _tool_envelope(
            "list_transactions",
            {
                "items": [
                    {"id": "1", "amount": "10.00", "type": "CREDIT"},
                    {"id": "2", "amount": "4.00", "type": "DEBIT"},
                ]
            },
        )
    )
    assert "2 movimientos" in r2.content
    assert "10.00" in r2.content
    assert "4.00" in r2.content


@pytest.mark.asyncio
async def test_chat_heuristic_unique_candidate_then_draft():
    llm = FakeLLM()
    messages = _user("transfiere 2.50 a María García") + _tool_envelope(
        "find_recipient",
        {"results": [{"recipient_id": "h1", "name_masked": "María G***", "expires_at": "x"}]},
    )
    r = await llm.chat(messages)
    assert r.tool_calls[0].name == "draft_transfer"
    assert r.tool_calls[0].arguments == {"recipient_id": "h1", "amount": "2.50"}


@pytest.mark.asyncio
async def test_chat_heuristic_multiple_candidates_use_selected_recipient():
    llm = FakeLLM()
    results = {
        "results": [
            {"recipient_id": "h1", "name_masked": "María G***", "expires_at": "x"},
            {"recipient_id": "h2", "name_masked": "María L***", "expires_at": "x"},
        ]
    }
    messages = _user("transfiere 2.50 a María") + _tool_envelope("find_recipient", results)

    # sin seleccion no elige entre candidatos
    r = await llm.chat(messages)
    assert r.tool_calls == ()
    assert "varios" in r.content

    # con selected_recipient_id usa el handle elegido por el usuario
    r2 = await llm.chat(messages, selected_recipient_id="h2")
    assert r2.tool_calls[0].name == "draft_transfer"
    assert r2.tool_calls[0].arguments == {"recipient_id": "h2", "amount": "2.50"}


@pytest.mark.asyncio
async def test_chat_heuristic_draft_result_message_has_no_completion_claims():
    llm = FakeLLM()
    r = await llm.chat(
        _user("transfiere 2.50 a María García")
        + _tool_envelope(
            "draft_transfer",
            {
                "draft_id": "d-1",
                "status": "pendiente",
                "amount": "2.50",
                "expires_at": "mañana",
            },
        )
    )
    lowered = r.content.lower()
    assert "2.50" in r.content
    assert "pendiente" in r.content
    assert "enviad" not in lowered
    assert "completad" not in lowered
    assert "transferí" not in lowered


@pytest.mark.asyncio
async def test_chat_heuristic_error_envelopes_get_safe_answers():
    llm = FakeLLM()
    r = await llm.chat(_tool_envelope("find_recipient", {"error": "permission_denied"}))
    assert r.content and "no puedes" in r.content.lower()

    r2 = await llm.chat(_tool_envelope("draft_transfer", {"error": "tool_failed"}))
    assert r2.content and "pude" in r2.content.lower()


@pytest.mark.asyncio
async def test_chat_script_mode_consumes_steps_typed():
    script = [
        {"type": "tool_call", "tool": "get_balance", "args": {}},
        {
            "type": "tool_call",
            "calls": [
                {"tool": "get_balance", "args": {}},
                {"tool": "list_transactions", "args": {"limit": 2}},
            ],
        },
        {"type": "final", "content": "listo"},
    ]
    llm = FakeLLM(script=script)
    r1 = await llm.chat([])
    assert r1.tool_calls == (ToolCallRequest(name="get_balance", arguments={}),)
    r2 = await llm.chat([])
    assert len(r2.tool_calls) == 2
    r3 = await llm.chat([])
    assert r3.content == "listo"
    # agotado el guion, final vacio
    r4 = await llm.chat([])
    assert r4.content == ""


@pytest.mark.asyncio
async def test_chat_script_mode_last_recipient_substitution():
    import json

    llm = FakeLLM(
        script=[
            {
                "type": "tool_call",
                "tool": "draft_transfer",
                "args": {"recipient_id": "$last_recipient", "amount": "1.00"},
            }
        ]
    )
    messages = [
        {"role": "tool", "content": json.dumps(
            {"tool": "find_recipient", "data": {"results": [
                {"recipient_id": "h-9", "name_masked": "M***", "expires_at": "x"}
            ]}}
        )}
    ]
    r = await llm.chat(messages)
    assert r.tool_calls[0].arguments["recipient_id"] == "h-9"


@pytest.mark.asyncio
async def test_chat_script_mode_selected_recipient_substitution():
    llm = FakeLLM(
        script=[
            {
                "type": "tool_call",
                "tool": "draft_transfer",
                "args": {"recipient_id": "$last_recipient", "amount": "1.00"},
            }
        ]
    )
    r = await llm.chat([], selected_recipient_id="sel-1")
    assert r.tool_calls[0].arguments["recipient_id"] == "sel-1"


@pytest.mark.asyncio
async def test_fake_llm_identity_attributes():
    llm = FakeLLM()
    assert llm.provider_name == "fake"
    assert llm.model_name == "fake-heuristic"
    scripted = FakeLLM(script=[{"type": "final", "content": "x"}])
    assert scripted.model_name == "fake-script"
