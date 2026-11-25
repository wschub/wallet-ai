from __future__ import annotations

from wallet_ai.infrastructure.providers.fake.embeddings import FakeEmbeddings
from wallet_ai.infrastructure.providers.fake.llm import FakeLLM
from wallet_ai.infrastructure.providers.fake.reranker import FakeReranker
from wallet_ai.infrastructure.providers.local.reranker import LocalReranker

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
    elif s.RERANKER_PROVIDER == "local":
        providers["reranker"] = LocalReranker()
    return providers
