"""Caso de uso: ingesta del corpus en el indice RAG (tramo A de US3, T049).

Pipeline por documento (el ORDEN es parte del contrato):

    PIIService.mask -> Chunker.chunk -> EmbeddingProvider.embed -> persistir

El enmascarado ocurre ANTES de segmentar y de calcular embeddings: la PII
en claro jamas llega a los chunks, a la base ni al proveedor de embeddings
(principio V de la constitucion). Los embeddings se calculan en UNA
llamada por lote (el puerto `EmbeddingProvider` es por lotes; el fake es
sincrono y un proveedor real asincrono, por eso se admite ambos).

La build registra proveedor, modelo y version del embedding junto con la
dimension observada del primer vector (dimension de la build, no por
chunk: la decision de research.md es vector SIN dimension fija en la
columna, y asi pueden coexistir builds de distintos modelos).

Idempotencia (misma build + mismo contenido = sin duplicados):

- Los ids son deterministicos (uuid5) y todas las escrituras usan ON
  CONFLICT DO NOTHING, asi que re-ingestar o reintentar una ingesta
  interrumpida solo completa lo que falta.
- Las diferencias NO se silencian: se comparan configuracion, sha del
  contenido y textos de los chunks ya indexados, y ante cualquier
  divergencia se rechaza con `IngestConflictError` (nuevo nombre de build
  o bump de version). Una build jamas mezcla versiones de un documento.

Aislamiento: este modulo no importa el ledger ni `confirm_draft`; lo
exige el contrato 'Confirmation isolation' de import-linter.
"""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from wallet_ai.application.rag.corpus import CorpusDocument, read_corpus
from wallet_ai.application.rag.errors import CorpusError, IngestConflictError
from wallet_ai.domain.documents.documents import (
    Chunk,
    Document,
    IndexBuild,
    chunk_uuid,
    document_uuid,
    index_build_uuid,
)
from wallet_ai.domain.ports.chunker import Chunker
from wallet_ai.domain.ports.embedding import EmbeddingProvider
from wallet_ai.domain.ports.pii_service import PIIService
from wallet_ai.domain.ports.rag_ingest import RagIngestRepository


@dataclass(frozen=True)
class IngestResult:
    build_id: UUID
    build_name: str
    documents: int
    chunks: int
    inserted_documents: int
    inserted_chunks: int
    dimension: int
    already_present: bool


class IngestService:
    def __init__(
        self,
        *,
        repo: RagIngestRepository,
        embeddings: EmbeddingProvider,
        pii: PIIService,
        chunker: Chunker,
        strategy: str,
        chunk_params: dict,
        build_name: str,
        embedding_provider: str,
        embedding_model: str,
        embedding_version: str,
    ) -> None:
        if not build_name.strip():
            raise ValueError("build_name no puede estar vacio")
        self._repo = repo
        self._embeddings = embeddings
        self._pii = pii
        self._chunker = chunker
        self._strategy = strategy
        self._chunk_params = dict(chunk_params)
        self._build_name = build_name
        self._embedding_provider = embedding_provider
        self._embedding_model = embedding_model
        self._embedding_version = embedding_version

    async def ingest(self, corpus_dir: Path) -> IngestResult:
        corpus = read_corpus(corpus_dir)

        # 1) Enmascarar PII ANTES de segmentar y de embeder.
        prepared: list[tuple[CorpusDocument, str, list[str]]] = []
        for doc in corpus:
            masked = self._pii.mask(doc.content)
            pieces = [piece for piece in self._chunker.chunk(masked) if piece.strip()]
            if not pieces:
                raise CorpusError(f"{doc.source_path}: el documento no produce fragmentos")
            prepared.append((doc, _sha256(doc.raw), pieces))

        # 2) Embeddings por lote: una sola llamada al proveedor.
        all_pieces = [piece for _, _, pieces in prepared for piece in pieces]
        vectors = await self._embed(all_pieces)
        dimension = len(vectors[0])
        if any(len(v) != dimension for v in vectors):
            raise CorpusError("el proveedor de embeddings devolvio vectores de dimension distinta")

        # 3) Build existente: verificar configuracion y contenido.
        existing = await self._repo.get_build_by_name(self._build_name)
        if existing is not None:
            self._assert_same_config(existing)
            build_id: UUID = existing["id"]
        else:
            build_id = index_build_uuid(self._build_name)
            await self._repo.create_build(
                IndexBuild(
                    id=build_id,
                    name=self._build_name,
                    chunking_strategy=self._strategy,
                    chunk_params=self._chunk_params,
                    embedding_provider=self._embedding_provider,
                    embedding_model=self._embedding_model,
                    embedding_version=self._embedding_version,
                    dimension=dimension,
                )
            )

        # 4) Persistir por documento; sin duplicados y sin silenciar cambios.
        inserted_documents = 0
        inserted_chunks = 0
        offset = 0
        for doc, sha, pieces in prepared:
            doc_id = document_uuid(doc.stable_id, doc.version)

            current = await self._repo.get_document(doc_id)
            if current is not None and current["content_sha256"] != sha:
                raise IngestConflictError(
                    f"el documento '{doc.stable_id}' cambio sin subir su version "
                    f"(version actual {doc.version}): incrementa 'version' en el front matter "
                    "o usa otro nombre de build"
                )

            if existing is not None:
                if await self._repo.has_other_version_in_build(build_id, doc.source_path, doc_id):
                    raise IngestConflictError(
                        f"la build '{self._build_name}' ya indexo otra version de "
                        f"'{doc.source_path}': usa otro nombre de build"
                    )
                if current is not None:
                    stored = await self._repo.get_chunk_texts(build_id, doc_id)
                    if stored != pieces:
                        raise IngestConflictError(
                            f"los fragments de '{doc.source_path}' en la build "
                            f"'{self._build_name}' difieren de los calculados (cambio de "
                            "segmentacion o de enmascarado): usa otro nombre de build"
                        )
                    offset += len(pieces)
                    continue  # ya indexado en esta build: idempotente

            if current is None:
                await self._repo.add_document(
                    Document(
                        id=doc_id,
                        title=doc.title,
                        access_level=doc.access_level,
                        version=doc.version,
                        content_sha256=sha,
                        source_path=doc.source_path,
                    )
                )
                inserted_documents += 1

            chunks = [
                Chunk(
                    id=chunk_uuid(build_id, doc_id, index),
                    index_build_id=build_id,
                    document_id=doc_id,
                    chunk_index=index,
                    text=piece,
                    access_level=doc.access_level,
                    embedding=vectors[offset + index],
                )
                for index, piece in enumerate(pieces)
            ]
            inserted_chunks += await self._repo.add_chunks(chunks)
            offset += len(pieces)

        return IngestResult(
            build_id=build_id,
            build_name=self._build_name,
            documents=len(corpus),
            chunks=len(all_pieces),
            inserted_documents=inserted_documents,
            inserted_chunks=inserted_chunks,
            dimension=dimension,
            already_present=existing is not None,
        )

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    async def _embed(self, texts: list[str]) -> list[list[float]]:
        result = self._embeddings.embed(texts)
        if inspect.isawaitable(result):
            result = await result
        vectors = result
        if len(vectors) != len(texts):
            raise CorpusError(
                f"el proveedor de embeddings devolvio {len(vectors)} vectores para {len(texts)} textos"
            )
        return vectors

    def _assert_same_config(self, existing: dict) -> None:
        expected = {
            "chunking_strategy": self._strategy,
            "embedding_provider": self._embedding_provider,
            "embedding_model": self._embedding_model,
            "embedding_version": self._embedding_version,
        }
        for key, value in expected.items():
            if existing[key] != value:
                raise IngestConflictError(
                    f"la build '{self._build_name}' ya existe con {key}={existing[key]!r} "
                    f"y se pidio {value!r}: usa otro nombre de build"
                )
        if existing["chunk_params"] != self._chunk_params:
            raise IngestConflictError(
                f"la build '{self._build_name}' ya existe con chunk_params="
                f"{existing['chunk_params']!r} y se pidio {self._chunk_params!r}: "
                "usa otro nombre de build"
            )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
