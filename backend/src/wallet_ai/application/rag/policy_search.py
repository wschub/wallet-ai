"""Caso de uso: busqueda de politicas con evidencia y citas (US3)."""

from __future__ import annotations

from typing import Any

from wallet_ai.application.rag.retrieval import RetrievalConfig, RetrievalContext, retrieve
from wallet_ai.infrastructure.config.settings import Settings

DEFAULT_MAX_PASSAGES = 5
DEFAULT_MAX_CHARS = 600
DEFAULT_EVIDENCE_THRESHOLD = 0.5


def _default_config(settings: Settings | None = None) -> RetrievalConfig:
    s = settings or Settings()
    build = getattr(s, "RAG_INDEX_BUILD", "fijo-s600-o100")
    mode = getattr(s, "RAG_RETRIEVAL_MODE", "hybrid")
    top_k = int(getattr(s, "RAG_TOP_K", DEFAULT_MAX_PASSAGES))
    if top_k < DEFAULT_MAX_PASSAGES:
        top_k = DEFAULT_MAX_PASSAGES
    return RetrievalConfig(index_build=build, mode=mode, top_k=top_k, rerank=False)


async def search_policies(
    ctx: RetrievalContext,
    query: str,
    config: RetrievalConfig | None = None,
    *,
    max_passages: int = DEFAULT_MAX_PASSAGES,
    max_chars: int = DEFAULT_MAX_CHARS,
    evidence_threshold: float = DEFAULT_EVIDENCE_THRESHOLD,
    require_evidence: bool = True,
) -> list[dict[str, Any]]:
    if config is None:
        config = _default_config()

    hits = await retrieve(ctx, query, config)

    passages: list[dict[str, Any]] = []
    for hit in hits[:max_passages]:
        has_lexical = hit.lexical_rank is not None
        cosine_sim = None
        if hit.vector_score is not None:
            cosine_sim = 1.0 - float(hit.vector_score)
        has_vector_evidence = cosine_sim is not None and cosine_sim >= evidence_threshold
        if require_evidence and not (has_lexical or has_vector_evidence):
            continue
        text = hit.text or ""
        if len(text) > max_chars:
            text = text[:max_chars]
        passages.append(
            {
                "chunk_id": str(hit.chunk_id),
                "document_id": str(hit.document_id),
                "title": hit.title,
                "section": hit.section,
                "text": text,
            }
        )

    if require_evidence and not passages:
        return []
    return passages[:max_passages]
