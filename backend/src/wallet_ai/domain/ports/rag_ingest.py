"""Puerto del repositorio de ingesta del indice RAG (tramo A de US3, T049).

Persistencia append-only de `index_builds`, `documents` y `chunks`
(migracion 20261024_0009). El caso de uso `application.rag.ingest` depende
SOLO de este puerto y de los puertos de embeddings/PII/segmentacion; el
adaptador asyncpg vive en `infrastructure.db.rag_ingest_repository`.

Semanticas exigidas al adaptador:

- `create_build` y `add_*` son idempotentes por clave natural
  (ON CONFLICT DO NOTHING): re-ingestar no duplica.
- `get_chunk_texts` ordena por `chunk_index`.
- `has_other_version_in_build` detecta si una build ya indexo otra version
  del mismo documento fuente (por `source_path`).
"""

from __future__ import annotations

from typing import Any, Protocol

from wallet_ai.domain.documents.documents import Chunk, Document, IndexBuild


class RagIngestRepository(Protocol):
    async def get_build_by_name(self, name: str) -> dict[str, Any] | None: ...

    async def create_build(self, build: IndexBuild) -> None: ...

    async def get_document(self, document_id: Any) -> dict[str, Any] | None: ...

    async def add_document(self, document: Document) -> None: ...

    async def has_other_version_in_build(
        self, build_id: Any, source_path: str, document_id: Any
    ) -> bool: ...

    async def get_chunk_texts(self, build_id: Any, document_id: Any) -> list[str]: ...

    async def add_chunks(self, chunks: list[Chunk]) -> int: ...
