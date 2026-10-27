"""Adaptador asyncpg del repositorio de ingesta (tramo A de US3, T049).

Implementa el puerto `domain.ports.rag_ingest.RagIngestRepository` sobre
las tablas de la migracion 20261024_0009.

Notas de implementacion:

- Toda escritura usa ON CONFLICT DO NOTHING: el adaptador es idempotente
  por clave natural (id de build / id de documento / triple
  build+documento+indice) y devuelve cuantas filas inserto de verdad.
- Los embeddings viajan con el codec binario de pgvector
  (`pgvector.asyncpg.register_vector`), registrado por conexion.
- `chunk_params` (JSONB) se devuelve ya parseado como dict.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector

from wallet_ai.domain.documents.documents import Chunk, Document, IndexBuild


class RagIngestRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_build_by_name(self, name: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, chunking_strategy, chunk_params, embedding_provider, "
                "embedding_model, embedding_version, dimension, created_at "
                "FROM index_builds WHERE name = $1",
                name,
            )
        if row is None:
            return None
        build = dict(row)
        build["chunk_params"] = json.loads(build["chunk_params"])
        return build

    async def create_build(self, build: IndexBuild) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO index_builds (id, name, chunking_strategy, chunk_params, "
                "embedding_provider, embedding_model, embedding_version, dimension) "
                "VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8) "
                "ON CONFLICT (name) DO NOTHING",
                build.id,
                build.name,
                build.chunking_strategy,
                json.dumps(build.chunk_params),
                build.embedding_provider,
                build.embedding_model,
                build.embedding_version,
                build.dimension,
            )

    async def get_document(self, document_id) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, title, access_level, version, content_sha256, source_path, created_at "
                "FROM documents WHERE id = $1",
                document_id,
            )
        return dict(row) if row is not None else None

    async def add_document(self, document: Document) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO documents (id, title, access_level, version, content_sha256, source_path) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (id) DO NOTHING",
                document.id,
                document.title,
                document.access_level,
                document.version,
                document.content_sha256,
                document.source_path,
            )

    async def has_other_version_in_build(self, build_id, source_path: str, document_id) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchval(
                "SELECT 1 FROM chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE c.index_build_id = $1 AND d.source_path = $2 AND d.id <> $3 LIMIT 1",
                build_id,
                source_path,
                document_id,
            )
        return row is not None

    async def get_chunk_texts(self, build_id, document_id) -> list[str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT text FROM chunks WHERE index_build_id = $1 AND document_id = $2 "
                "ORDER BY chunk_index",
                build_id,
                document_id,
            )
        return [r["text"] for r in rows]

    async def add_chunks(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        inserted = 0
        async with self._pool.acquire() as conn:
            await register_vector(conn)
            for chunk in chunks:
                row_id = await conn.fetchval(
                    "INSERT INTO chunks (id, index_build_id, document_id, chunk_index, "
                    "text, access_level, embedding) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7) "
                    "ON CONFLICT (index_build_id, document_id, chunk_index) DO NOTHING "
                    "RETURNING id",
                    chunk.id,
                    chunk.index_build_id,
                    chunk.document_id,
                    chunk.chunk_index,
                    chunk.text,
                    chunk.access_level,
                    list(chunk.embedding),
                )
                if row_id is not None:
                    inserted += 1
        return inserted
