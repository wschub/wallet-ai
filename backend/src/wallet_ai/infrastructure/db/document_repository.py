"""Adaptador asyncpg de lectura del indice RAG (tramo B de US3, T050-T052).

Implementa el puerto `domain.ports.retrieval.DocumentRepository` sobre las
tablas de la migracion 20261024_0009 (`index_builds`, `documents`,
`chunks`).

El requisito central del tramo B (punto 3 del encargo): en AMBAS patas el
filtro

    index_build_id = $1 AND access_level = ANY($2)

va en el WHERE, ANTES de ORDER BY y LIMIT. El control de acceso es previo
al ranking: la base nunca ordena ni limita fragmentos que el rol no puede
ver, y ningun puesto del top-k se desperdicia en un fragmento que habria
que descartar despues.

- Pata lexica: `websearch_to_tsquery('spanish', ...)` (nunca lanza con
  caracteres de tsquery como ' & | ! ( ) :*') con `ts_rank_cd` sobre el
  `tsv` GENERADO de `chunks` (config 'spanish'). La consulta llega YA
  ENMASCARADA del caso de uso.
- Pata vectorial: distancia coseno EXACTA (`c.embedding <=> $3::vector`)
  contra el embedding de la consulta. Sin indice aproximado en este tramo:
  la columna `vector` no tiene dimension fija y la decision de research.md
  es preferir la busqueda exacta; si en el tramo EVAL un HNSW resultara
  necesario y el filtro ACL demasiado restrictivo, la alternativa
  documentada es `iterative_scan` (seguir escaneando aplicando el filtro
  hasta llenar el top-k). Se registra el codec binario de pgvector por
  conexion (`pgvector.asyncpg.register_vector`).
- Determinismo: desempate estable por `chunk_id` en ambas patas, de modo
  que el mismo input produce el mismo orden siempre.

Solo SELECT: el asistente nunca escribe en el indice (wallet_app tiene
unicamente SELECT sobre estas tablas).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from uuid import UUID

import asyncpg
from pgvector.asyncpg import register_vector

from wallet_ai.domain.documents.documents import IndexBuild
from wallet_ai.domain.ports.retrieval import ScoredChunk

_LEXICAL_SQL = """
SELECT c.id AS chunk_id, c.document_id, d.title, c.text, c.access_level,
       ts_rank_cd(c.tsv, q.tsq) AS score
FROM chunks c
JOIN documents d ON d.id = c.document_id
CROSS JOIN websearch_to_tsquery('spanish', $3) AS q(tsq)
WHERE c.index_build_id = $1
  AND c.access_level = ANY($2::text[])
  AND c.tsv @@ q.tsq
ORDER BY score DESC, c.id ASC
LIMIT $4
"""

_VECTOR_SQL = """
SELECT c.id AS chunk_id, c.document_id, d.title, c.text, c.access_level,
       (c.embedding <=> $3::vector) AS score
FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE c.index_build_id = $1
  AND c.access_level = ANY($2::text[])
ORDER BY score ASC, c.id ASC
LIMIT $4
"""

_BUILD_SQL = """
SELECT id, name, chunking_strategy, chunk_params, embedding_provider,
       embedding_model, embedding_version, dimension
FROM index_builds
WHERE name = $1
"""


class DocumentRepository:
    """Lectura del indice con prefiltro ACL (T050)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_build(self, name: str) -> IndexBuild | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_BUILD_SQL, name)
        if row is None:
            return None
        return _build_from_row(row)

    async def lexical_search(
        self,
        *,
        build_id: UUID,
        allowed_levels: Sequence[str],
        query: str,
        limit: int,
    ) -> list[ScoredChunk]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _LEXICAL_SQL, build_id, list(allowed_levels), query, limit
            )
        return [_chunk_from_row(row) for row in rows]

    async def vector_search(
        self,
        *,
        build_id: UUID,
        allowed_levels: Sequence[str],
        embedding: Sequence[float],
        limit: int,
    ) -> list[ScoredChunk]:
        async with self._pool.acquire() as conn:
            await register_vector(conn)
            rows = await conn.fetch(
                _VECTOR_SQL, build_id, list(allowed_levels), list(embedding), limit
            )
        return [_chunk_from_row(row) for row in rows]


def _build_from_row(row: asyncpg.Record) -> IndexBuild:
    params = row["chunk_params"]
    return IndexBuild(
        id=row["id"],
        name=row["name"],
        chunking_strategy=row["chunking_strategy"],
        chunk_params=json.loads(params) if isinstance(params, str) else dict(params),
        embedding_provider=row["embedding_provider"],
        embedding_model=row["embedding_model"],
        embedding_version=row["embedding_version"],
        dimension=row["dimension"],
    )


def _chunk_from_row(row: asyncpg.Record) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=row["chunk_id"],
        document_id=row["document_id"],
        title=row["title"],
        text=row["text"],
        access_level=row["access_level"],
        score=float(row["score"]),
    )
