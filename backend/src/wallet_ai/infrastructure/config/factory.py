from __future__ import annotations

from wallet_ai.infrastructure.providers.fake.embeddings import FakeEmbeddings
from wallet_ai.infrastructure.providers.fake.llm import FakeLLM
from wallet_ai.infrastructure.providers.fake.reranker import FakeReranker

from .settings import Settings


def build_providers(settings: Settings | None = None):
    s = settings or Settings()
    providers: dict[str, object] = {}
    if s.LLM_PROVIDER == "fake":
        providers["llm"] = FakeLLM()
    if s.EMBEDDING_PROVIDER == "fake":
        providers["embeddings"] = FakeEmbeddings()
    if s.RERANKER_PROVIDER == "fake":
        providers["reranker"] = FakeReranker()
    return providers
