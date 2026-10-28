"""Puerto del repositorio de recuperacion (tramo B de US3, T050-T052).

El caso de uso `application.rag.retrieval` depende SOLO de este puerto (y de
los de embeddings/PII/reranker/auditoria); el adaptador asyncpg vive en
`infrastructure.db.document_repository`.

Contrato exigido al adaptador (encargo del tramo B, punto 3):

- En AMBAS patas el filtro `index_build_id = $1 AND access_level = ANY($2)`
  va en el WHERE, ANTES de ORDER BY y LIMIT: el control de acceso es previo
  al ranking. `allowed_levels` ES la ACL: el adaptador no decide nada por su
  cuenta.
- Pata lexica: `websearch_to_tsquery('spanish', ...)` con `ts_rank_cd`
  sobre el `tsv` generado de `chunks`.
- Pata vectorial: distancia coseno EXACTA (`<=>`) contra el embedding de la
  consulta (busqueda exacta; sin indice aproximado en este tramo).
- Orden determinista: score y desempate estable por `chunk_id`, de modo
  que el ranking devuelto es reproducible.
- `ScoredChunk.text` ya llega enmascarado de la ingesta; el puerto no
  devuelve embeddings ni tsqueries.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from wallet_ai.domain.documents.documents import IndexBuild


@dataclass(frozen=True)
class ScoredChunk:
    """Fragmento recuperado con su puntaje de pata.

    `score` es el puntaje CRUDO de la pata que lo encontro: ts_rank_cd en
    la lexica, distancia coseno en la vectorial (menor = mas cercano).
    """

    chunk_id: UUID
    document_id: UUID
    title: str
    text: str
    access_level: str
    score: float


class DocumentRepository(Protocol):
    """Lectura del indice con prefiltro ACL (T050)."""

    async def get_build(self, name: str) -> IndexBuild | None:
        """Metadatos de la build por nombre (proveedor/modelo/version/dimension)."""
        ...

    async def lexical_search(
        self,
        *,
        build_id: UUID,
        allowed_levels: Sequence[str],
        query: str,
        limit: int,
    ) -> list[ScoredChunk]:
        """Top `limit` fragmentos por ts_rank_cd, solo entre los niveles permitidos."""
        ...

    async def vector_search(
        self,
        *,
        build_id: UUID,
        allowed_levels: Sequence[str],
        embedding: Sequence[float],
        limit: int,
    ) -> list[ScoredChunk]:
        """Top `limit` fragmentos por distancia coseno exacta, solo entre los
        niveles permitidos."""
        ...
