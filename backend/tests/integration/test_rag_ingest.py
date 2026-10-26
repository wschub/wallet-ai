"""Integracion de la ingesta RAG contra Postgres real (tramo A de US3, T048/T049).

Requisitos verificados aqui (definidos en el encargo del tramo A):

- La ingesta es idempotente: misma build y mismo contenido no duplican.
- `chunks.access_level` coincide SIEMPRE con el del documento: un INSERT
  directo con un nivel distinto lo rechaza la base (trigger).
- UPDATE, DELETE y TRUNCATE se rechazan con 'append-only' en documents,
  chunks e index_builds.
- Con `SET ROLE wallet_app`, INSERT/UPDATE/DELETE fallan con
  `InsufficientPrivilegeError` y SELECT funciona (wallet_app solo lectura).
- El texto almacenado no contiene el correo ni la tarjeta del documento
  sintetico (el enmascarado ocurre ANTES de segmentar y de calcular
  embeddings).
- Dos builds con estrategias distintas coexisten sin mezclar chunks.
- La build guarda proveedor, modelo, version y dimension de los embeddings.

Reglas de la casa (ver `test_ledger.py`): no se asume una base vacia; cada
test usa nombres/ids propios; los rechazos se comprueban por clase de error
cuando es posible y por el mensaje estable cuando el error viene de un
trigger propio.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import asyncpg
import pytest

from wallet_ai.application.rag.errors import IngestConflictError
from wallet_ai.application.rag.ingest import IngestService
from wallet_ai.domain.documents.documents import document_uuid
from wallet_ai.infrastructure.chunking import build_chunker
from wallet_ai.infrastructure.db.rag_ingest_repository import RagIngestRepository
from wallet_ai.infrastructure.pii.masker import PIIMasker
from wallet_ai.infrastructure.providers.fake.embeddings import FakeEmbeddings

CORPUS_DIR = Path(__file__).resolve().parents[2] / "data" / "corpus"

# Espejo de los identificadores de diseno del corpus (fuente de verdad:
# tests/unit/test_corpus_design.py). El documento de datos de la cuenta
# contiene PII sintetica que debe quedar enmascarada en los chunks.
PII_DOC_FILE = "cuenta-actualizacion-de-datos-cliente.md"
PII_EMAIL = "cliente.sintetico@walletai.example"
PII_CARD = "4000 0000 0000 0002"
PII_CARD_DIGITS = "4000000000000002"


@pytest.fixture
async def pool():
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL must be set in environment")
    from wallet_ai.infrastructure.config.settings import Settings

    dsn = Settings().asyncpg_dsn
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        yield p
    finally:
        await p.close()


def _service(pool, *, name, strategy="fijo", size=600, overlap=100, dim=64):
    return IngestService(
        repo=RagIngestRepository(pool),
        embeddings=FakeEmbeddings(dim=dim),
        pii=PIIMasker(),
        chunker=build_chunker(strategy, size, overlap),
        strategy=strategy,
        chunk_params={"size": size, "overlap": overlap},
        build_name=name,
        embedding_provider="fake",
        embedding_model="fake-test-model",
        embedding_version="1.2.3",
    )


def _write_doc(directory: Path, stable_id: str, *, body: str, version="1.0", access_level="publico") -> Path:
    path = directory / f"{stable_id}.md"
    path.write_text(
        f"---\nid: {stable_id}\ntitle: Documento de prueba {stable_id}\n"
        f"access_level: {access_level}\nversion: {version}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


async def _ingest_tmp_doc(pool, *, name, stable_id="doc-prueba", body="Contenido de politica de prueba. " * 8, version="1.0"):
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        directory = Path(td)
        _write_doc(directory, stable_id, body=body, version=version)
        result = await _service(pool, name=name).ingest(directory)
    return result


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_repo_writes_survive_replays_on_conflict(pool):
    """ON CONFLICT DO NOTHING: repetir exactamente las mismas escrituras
    (reintento de una ingesta interrumpida, o carrera entre dos procesos
    que ingieren la misma build) no falla, no duplica y `add_chunks`
    reporta cuantas filas inserto de verdad.
    """
    from wallet_ai.domain.documents.documents import Chunk, Document, IndexBuild

    repo = RagIngestRepository(pool)
    build = IndexBuild(
        id=uuid.uuid4(),
        name=f"test-replay-{uuid.uuid4().hex[:10]}",
        chunking_strategy="fijo",
        chunk_params={"size": 600, "overlap": 100},
        embedding_provider="fake",
        embedding_model="fake-test-model",
        embedding_version="1.2.3",
        dimension=4,
    )
    doc = Document(
        id=uuid.uuid4(),
        title="Documento de reintento",
        access_level="publico",
        version="1.0",
        content_sha256="c" * 64,
        source_path="doc-reintento.md",
    )
    chunk = Chunk(
        id=uuid.uuid4(),
        index_build_id=build.id,
        document_id=doc.id,
        chunk_index=0,
        text="Fragmento de reintento de escritura.",
        access_level="publico",
        embedding=[0.1, 0.2, 0.3, 0.4],
    )

    await repo.create_build(build)
    await repo.add_document(doc)
    assert await repo.add_chunks([chunk]) == 1

    # Repetir las mismas escrituras con la misma clave natural: sin error
    # y sin duplicados.
    await repo.create_build(build)
    await repo.add_document(doc)
    assert await repo.add_chunks([chunk]) == 0

    async with pool.acquire() as conn:
        n_builds = await conn.fetchval("SELECT count(*) FROM index_builds WHERE name = $1", build.name)
        n_docs = await conn.fetchval("SELECT count(*) FROM documents WHERE id = $1", doc.id)
        n_chunks = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE index_build_id = $1 AND document_id = $2",
            build.id,
            doc.id,
        )
    assert (n_builds, n_docs, n_chunks) == (1, 1, 1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_is_idempotent(pool):
    name = f"test-idem-{uuid.uuid4().hex[:10]}"
    svc = _service(pool, name=name)

    r1 = await svc.ingest(CORPUS_DIR)
    assert 24 <= r1.documents <= 30
    assert r1.chunks >= r1.documents
    assert not r1.already_present

    r2 = await svc.ingest(CORPUS_DIR)
    assert r2.build_id == r1.build_id
    assert r2.inserted_documents == 0
    assert r2.inserted_chunks == 0
    assert r2.already_present

    async with pool.acquire() as conn:
        n_chunks = await conn.fetchval("SELECT count(*) FROM chunks WHERE index_build_id = $1", r1.build_id)
        n_docs = await conn.fetchval(
            "SELECT count(DISTINCT document_id) FROM chunks WHERE index_build_id = $1", r1.build_id
        )
    assert n_chunks == r1.chunks
    assert n_docs == r1.documents


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_rejects_changed_content_without_version_bump(pool):
    import tempfile

    name = f"test-conflicto-{uuid.uuid4().hex[:10]}"
    stable_id = f"doc-conflicto-{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory() as td:
        directory = Path(td)
        _write_doc(directory, stable_id, body="La politica original dice una cosa sobre limites.")
        r1 = await _service(pool, name=name).ingest(directory)
        assert r1.inserted_documents == 1

        _write_doc(directory, stable_id, body="La politica alterada dice otra cosa completamente distinta.")
        with pytest.raises(IngestConflictError):
            await _service(pool, name=name).ingest(directory)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_rejects_same_name_with_different_params(pool):
    import tempfile

    name = f"test-params-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as td:
        directory = Path(td)
        _write_doc(directory, "doc-params", body="Politica corta para comparar parametros de segmentacion.")
        await _service(pool, name=name, size=600).ingest(directory)
        with pytest.raises(IngestConflictError):
            await _service(pool, name=name, size=300).ingest(directory)
        with pytest.raises(IngestConflictError):
            await _service(pool, name=name, strategy="estructural", size=600).ingest(directory)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_new_version_inserts_new_row_and_guard_builds(pool):
    import tempfile

    name_v1 = f"test-v1-{uuid.uuid4().hex[:10]}"
    name_v2 = f"test-v2-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as td:
        directory = Path(td)
        _write_doc(directory, "doc-version", body="Version uno del cuerpo del documento.", version="1.0")
        await _service(pool, name=name_v1).ingest(directory)

        # Nueva version = nueva fila en documents (la anterior permanece).
        _write_doc(
            directory,
            "doc-version",
            body="Version dos, con cuerpo distinto y mas contenido de politica.",
            version="2.0",
        )
        r2 = await _service(pool, name=name_v2).ingest(directory)

    v2_uuid = document_uuid("doc-version", "2.0")
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, version, content_sha256 FROM documents WHERE source_path = $1", "doc-version.md")
        assert {r["version"] for r in rows} == {"1.0", "2.0"}
        assert len({r["id"] for r in rows}) == 2
        assert len({r["content_sha256"] for r in rows}) == 2
        n = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE index_build_id = $1 AND document_id = $2",
            r2.build_id,
            v2_uuid,
        )
        assert n > 0, "la build v2 debe apuntar a la fila de la version 2"

    # Re-ingestar la build v1 con el corpus ya en v2 debe rechazarse:
    # esa build ya indexo otra version del mismo documento.
    with tempfile.TemporaryDirectory() as td:
        directory = Path(td)
        _write_doc(
            directory,
            "doc-version",
            body="Version dos, con cuerpo distinto y mas contenido de politica.",
            version="2.0",
        )
        with pytest.raises(IngestConflictError):
            await _service(pool, name=name_v1).ingest(directory)


# ---------------------------------------------------------------------------
# ACL denormalizado: chunks.access_level == documents.access_level
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_chunk_with_wrong_access_level_is_rejected_by_db(pool):
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            build_id = uuid.uuid4()
            await conn.execute(
                "INSERT INTO index_builds (id, name, chunking_strategy, chunk_params, "
                "embedding_provider, embedding_model, embedding_version, dimension) "
                "VALUES ($1, $2, 'fijo', '{}', 'fake', 'm', 'v', 1)",
                build_id,
                f"test-build-{uuid.uuid4().hex[:8]}",
            )
            doc_id = uuid.uuid4()
            await conn.execute(
                "INSERT INTO documents (id, title, access_level, version, content_sha256, source_path) "
                "VALUES ($1, 'Doc prueba', 'publico', '1.0', $2, 'doc-acl-prueba.md')",
                doc_id,
                "a" * 64,
            )

            # Cada insercion que debe fallar va en su propio savepoint: tras
            # el error, la transaccion exterior sigue util para la siguiente.
            with pytest.raises(Exception, match="does not match document access_level"):
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO chunks (id, index_build_id, document_id, chunk_index, text, access_level, embedding) "
                        "VALUES ($1, $2, $3, 0, 'texto', 'cliente', '[0.5]'::vector)",
                        uuid.uuid4(),
                        build_id,
                        doc_id,
                    )

            with pytest.raises(Exception, match="does not match document access_level"):
                # Documento inexistente: tampoco hay nivel que case.
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO chunks (id, index_build_id, document_id, chunk_index, text, access_level, embedding) "
                        "VALUES ($1, $2, $3, 0, 'texto', 'publico', '[0.5]'::vector)",
                        uuid.uuid4(),
                        build_id,
                        uuid.uuid4(),
                    )

            await conn.execute(
                "INSERT INTO chunks (id, index_build_id, document_id, chunk_index, text, access_level, embedding) "
                "VALUES ($1, $2, $3, 0, 'texto', 'publico', '[0.5]'::vector)",
                uuid.uuid4(),
                build_id,
                doc_id,
            )
        finally:
            await tx.rollback()


# ---------------------------------------------------------------------------
# Append-only en documents, chunks e index_builds
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_rag_tables_reject_update_delete_truncate(pool):
    result = await _ingest_tmp_doc(pool, name=f"test-append-{uuid.uuid4().hex[:10]}")
    build_id = result.build_id

    async with pool.acquire() as conn:
        doc_id = await conn.fetchval("SELECT document_id FROM chunks WHERE index_build_id = $1 LIMIT 1", build_id)
        chunk_id = await conn.fetchval("SELECT id FROM chunks WHERE index_build_id = $1 LIMIT 1", build_id)

        for table, update_sql, ident in (
            ("documents", "UPDATE documents SET title = 'hack'", doc_id),
            ("chunks", "UPDATE chunks SET text = 'hack'", chunk_id),
            ("index_builds", "UPDATE index_builds SET name = 'hack'", build_id),
        ):
            with pytest.raises(Exception, match="append-only"):
                await conn.execute(update_sql)
            with pytest.raises(Exception, match="append-only"):
                await conn.execute(f"DELETE FROM {table} WHERE id = $1", ident)

        # TRUNCATE en tablas referenciadas por FK (documents, index_builds)
        # debe usar CASCADE para llegar a disparar el trigger append-only de
        # la cascada (TRUNCATE plano se rechaza antes por la FK); todo dentro
        # de una transaccion con rollback.
        for table in ("documents", "chunks", "index_builds"):
            tx = conn.transaction()
            await tx.start()
            try:
                with pytest.raises(Exception, match="append-only"):
                    await conn.execute(f"TRUNCATE TABLE {table} CASCADE")
            finally:
                await tx.rollback()


# ---------------------------------------------------------------------------
# wallet_app: solo lectura sobre las tablas RAG
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_wallet_app_is_read_only_on_rag_tables(pool):
    result = await _ingest_tmp_doc(pool, name=f"test-rol-{uuid.uuid4().hex[:10]}")
    build_id = result.build_id

    async with pool.acquire() as conn:
        await conn.execute("SET ROLE wallet_app")
        try:
            for table in ("documents", "chunks", "index_builds"):
                await conn.fetchval(f"SELECT count(*) FROM {table}")
            await conn.fetchval("SELECT count(*) FROM chunks WHERE index_build_id = $1", build_id)

            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute(
                    "INSERT INTO documents (id, title, access_level, version, content_sha256, source_path) "
                    "VALUES ($1, 'x', 'publico', '1.0', $2, 'x.md')",
                    uuid.uuid4(),
                    "b" * 64,
                )
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute("UPDATE documents SET title = 'x'")
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute("DELETE FROM chunks")
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute("TRUNCATE TABLE index_builds")
        finally:
            await conn.execute("RESET ROLE")


# ---------------------------------------------------------------------------
# PII enmascarada ANTES de segmentar y de embeder
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_stored_chunks_do_not_contain_raw_pii(pool):
    raw = (CORPUS_DIR / PII_DOC_FILE).read_text(encoding="utf-8")
    assert PII_EMAIL in raw, "sanity: el corpus si contiene la PII en claro"
    assert PII_CARD in raw, "sanity: el corpus si contiene la tarjeta en claro"

    result = await _service(pool, name=f"test-pii-{uuid.uuid4().hex[:10]}").ingest(CORPUS_DIR)

    async with pool.acquire() as conn:
        pii_chunks = await conn.fetch(
            "SELECT c.text FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE c.index_build_id = $1 AND d.source_path = $2",
            result.build_id,
            PII_DOC_FILE,
        )
        assert pii_chunks, "el documento con PII sintetica debe quedar indexado"

        all_texts = await conn.fetch("SELECT text FROM chunks WHERE index_build_id = $1", result.build_id)
        joined = "\n".join(r["text"] for r in all_texts)

    for row in pii_chunks:
        assert PII_EMAIL not in row["text"]
        assert PII_CARD not in row["text"]
        assert PII_CARD_DIGITS not in row["text"]
    assert "<EMAIL>" in joined, "el enmascarado debe dejar la marca <EMAIL>"
    assert "<CARD>" in joined, "el enmascarado debe dejar la marca <CARD>"


# ---------------------------------------------------------------------------
# Builds multiples sin mezcla
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_builds_with_different_strategies_coexist(pool):
    name_fijo = f"test-fijo-{uuid.uuid4().hex[:10]}"
    name_estructural = f"test-estructural-{uuid.uuid4().hex[:10]}"
    r_fijo = await _service(pool, name=name_fijo, strategy="fijo", size=500, overlap=50).ingest(CORPUS_DIR)
    r_estructural = await _service(
        pool, name=name_estructural, strategy="estructural", size=500, overlap=50
    ).ingest(CORPUS_DIR)

    async with pool.acquire() as conn:
        ids_fijo = await conn.fetch("SELECT id, document_id, text FROM chunks WHERE index_build_id = $1", r_fijo.build_id)
        ids_estructural = await conn.fetch(
            "SELECT id, document_id, text FROM chunks WHERE index_build_id = $1", r_estructural.build_id
        )

        assert {r["id"] for r in ids_fijo}.isdisjoint({r["id"] for r in ids_estructural})
        assert {r["document_id"] for r in ids_fijo} == {r["document_id"] for r in ids_estructural}, (
            "ambas builds indexan los mismos documentos (filas documents compartidas)"
        )

        texts_fijo = {r["text"] for r in ids_fijo}
        texts_estructural = {r["text"] for r in ids_estructural}
        assert texts_fijo != texts_estructural, "las estrategias deben producir fragmentos distintos"

        builds = await conn.fetch(
            "SELECT name, chunking_strategy, chunk_params FROM index_builds WHERE id = ANY($1::uuid[])",
            [r_fijo.build_id, r_estructural.build_id],
        )
    by_name = {b["name"]: b for b in builds}
    assert by_name[name_fijo]["chunking_strategy"] == "fijo"
    assert by_name[name_estructural]["chunking_strategy"] == "estructural"
    assert json.loads(by_name[name_fijo]["chunk_params"])["size"] == 500
    assert json.loads(by_name[name_estructural]["chunk_params"])["overlap"] == 50


# ---------------------------------------------------------------------------
# Metadatos del embedding en la build
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_build_stores_embedding_model_version_and_dimension(pool):
    result = await _service(pool, name=f"test-meta-{uuid.uuid4().hex[:10]}", dim=64).ingest(CORPUS_DIR)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT embedding_provider, embedding_model, embedding_version, dimension "
            "FROM index_builds WHERE id = $1",
            result.build_id,
        )
        vector_dims = await conn.fetchval(
            "SELECT vector_dims(embedding) FROM chunks WHERE index_build_id = $1 LIMIT 1",
            result.build_id,
        )

    assert row["embedding_provider"] == "fake"
    assert row["embedding_model"] == "fake-test-model"
    assert row["embedding_version"] == "1.2.3"
    assert row["dimension"] == 64
    assert vector_dims == 64
    assert result.dimension == 64
