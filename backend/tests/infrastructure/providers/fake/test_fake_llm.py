import pytest

from wallet_ai.infrastructure.providers.fake.llm import FakeLLM


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
