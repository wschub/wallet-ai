"""Recuperacion RAG con ACL previa al ranking (tramo B de US3: T045, T050-T052).

Cubre la recuperacion hibrida sobre las tablas de la migracion 20261024_0009
y el control de acceso por rol. Requisitos del encargo del tramo B:

a) ACL ANTES del ranking: por cada documento interno con gemelo publico,
   una consulta LITERAL del interno como cliente, en los tres modos y las
   dos builds: ningun hit interno y, con top_k=3, exactamente 3 hits
   permitidos (si el filtro fuera posterior al ranking, sobrarian menos;
   el contrafactural lo demuestra el test de soporte: con acceso a internos,
   estos SI ocupan los primeros puestos).
b) soporte y compliance SI recuperan los internos con la misma consulta;
   un rol desconocido no recupera nada (pero audita la llamada).
c) Defensa en profundidad: un repositorio simulado que ignora el filtro
   provoca AclViolation, audita guardrail_block y no devuelve nada.
e) Modos: lexico por palabra exacta, vectorial por texto relacionado con
   embeddings fake, hibrido con fusion coherente con ambas patas.
f) Proveedor de embeddings con modelo/version distintos a los de la build
   -> error claro SIN consultar el indice.
g) Los fragmentos devueltos no contienen el correo ni la tarjeta sinteticos
   (la PII ya viene enmascarada de la ingesta).
h) La auditoria lleva UN evento retrieval por llamada (consulta enmascarada,
   configuracion, rol, ids de chunks/documentos y niveles) y NUNCA texto de
   fragmentos ni la consulta sin enmascarar.
i) Mismos parametros -> mismo orden en dos ejecuciones.
j) Consultas vacias, de 301 caracteres o con caracteres de tsquery
   (' & | ! ( ) :*) no rompen nada.

Reglas de la casa:

- La ingesta del corpus vive en un fixture con builds de NOMBRE FIJO y es
  IDEMPOTENTE (ON CONFLICT DO NOTHING + ids deterministicos): reejecutar
  no duplica filas ni acumula basura. Ningun test crea documentos o chunks
  con ids aleatorios.
- Las tablas del indice son de solo insercion: este modulo no borra,
  no modifica y no toca triggers ni permisos. El test de auditoria contra
  la base inserta un usuario de email FIJO (idempotente) y eventos
  append-only; si eso contamina la base, se recrea.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import asyncpg
import pytest

from wallet_ai.application.rag.errors import (
    EmbeddingModelMismatch,
    InvalidRetrievalQuery,
    UnknownIndexBuild,
)
from wallet_ai.application.rag.ingest import IngestService
from wallet_ai.application.rag.retrieval import (
    RetrievalConfig,
    RetrievalContext,
    retrieve,
)
from wallet_ai.domain.documents.access import AclViolation
from wallet_ai.domain.documents.documents import IndexBuild, document_uuid
from wallet_ai.domain.ports.retrieval import ScoredChunk
from wallet_ai.infrastructure.chunking import build_chunker
from wallet_ai.infrastructure.chunking.structural import StructuralChunker
from wallet_ai.infrastructure.db.document_repository import DocumentRepository
from wallet_ai.infrastructure.db.rag_ingest_repository import RagIngestRepository
from wallet_ai.infrastructure.db.tool_call_audit import ConnectionToolCallAuditor
from wallet_ai.infrastructure.pii.masker import PIIMasker
from wallet_ai.infrastructure.providers.fake.embeddings import FakeEmbeddings
from wallet_ai.infrastructure.providers.fake.reranker import FakeReranker

CORPUS_DIR = Path(__file__).resolve().parents[2] / "data" / "corpus"

# ---------------------------------------------------------------------------
# Builds de nombre FIJO del fixture (idempotentes). Los parametros son parte
# del contrato del fixture: cambiarlos exige otra base.
#
# La build estructural es v2 (chunker_version="2" + min_size): usa un
# NOMBRE NUEVO para no tocar la build v1 ya indexada. Reutilizar el nombre
# viejo con estos parametros dispara IngestConflictError (no se silencia).
# ---------------------------------------------------------------------------
BUILD_FIJO = "test-retrieval-fijo"
BUILD_ESTRUCTURAL = "test-retrieval-estructural-v2"
BUILDS = (BUILD_FIJO, BUILD_ESTRUCTURAL)
MODES = ("lexical", "vector", "hybrid")

CHUNK_SIZE = 600
OVERLAP = 100
EMBEDDING_DIM = 64
EMBEDDING_MODEL = "fake-test-model"
EMBEDDING_VERSION = "1.2.3"

# Documentos con proposito especial del corpus (fuente de verdad:
# tests/unit/test_corpus_design.py).
PII_DOC = "cuenta-actualizacion-de-datos-cliente"
PII_EMAIL = "cliente.sintetico@walletai.example"
PII_CARD = "4000 0000 0000 0002"
PII_CARD_DIGITS = "4000000000000002"

LIM_DOC = "limites-de-transferencia-publico"
NIVEL_DOC = "limites-por-nivel-de-cuenta-cliente"

# Consulta LITERAL de cada documento interno con gemelo publico. Cada frase
# aparece verbatim en el documento interno y comparte su lexico con el gemelo
# permitido (verificado contra el config 'spanish' real: >=3 chunks
# permitidos que matchean en AMBAS builds y >=1 chunk interno, de modo que
# sin filtro previo el interno ocuparia puestos del top-3).
ACL_QUERIES = (
    ("playbook-fraude-limites-interno", "limites de monto por cuenta"),
    ("playbook-fraude-comisiones-interno", "la comision por operacion"),
    ("playbook-fraude-patrones-interno", "la verificacion en dos pasos"),
    ("revision-de-alertas-disputas-interno", "revision para disputas"),
    ("revision-de-alertas-transferencias-interno", "en la ventana de procesamiento"),
    ("revision-de-alertas-congelamientos-interno", "seguridad de la cuenta"),
    ("escalamiento-seguridad-de-cuenta-interno", "verificacion de identidad"),
    ("escalamiento-contactos-y-horarios-interno", "la ventana de procesamiento"),
)

INTERNAL_DOC_IDS = {document_uuid(doc, "1.0") for doc, _ in ACL_QUERIES}
PII_DOC_ID = document_uuid(PII_DOC, "1.0")
LIM_DOC_ID = document_uuid(LIM_DOC, "1.0")
NIVEL_DOC_ID = document_uuid(NIVEL_DOC, "1.0")

# Identidad determinista para contextos con auditor en memoria (no toca la
# base); el test de auditoria persistida usa su propio usuario idempotente.
TEST_USER_ID = uuid5(NAMESPACE_URL, "wallet-ai:tests:rag-retrieval-user")
AUDIT_USER_EMAIL = "rag-retrieval-audit@walletai.example"
AUDIT_USER_ID = uuid5(NAMESPACE_URL, "wallet-ai:tests:rag-retrieval-audit-user")

PII_QUERY = "actualizar el correo de contacto y la tarjeta de prueba"


# ---------------------------------------------------------------------------
# Fixture: ingesta idempotente del corpus en builds de nombre fijo
# ---------------------------------------------------------------------------


async def _ingest_builds() -> None:
    from wallet_ai.infrastructure.config.settings import Settings

    pool = await asyncpg.create_pool(Settings().asyncpg_dsn, min_size=1, max_size=2)
    try:
        for name, strategy in ((BUILD_FIJO, "fijo"), (BUILD_ESTRUCTURAL, "estructural")):
            chunker = build_chunker(strategy, CHUNK_SIZE, OVERLAP)
            chunk_params = {"size": CHUNK_SIZE, "overlap": OVERLAP}
            if isinstance(chunker, StructuralChunker):
                # v2: la version del segmentador y el umbral de union de
                # secciones cortas son parte del contrato de la build.
                chunk_params.update(
                    chunker_version=chunker.CHUNKER_VERSION, min_size=chunker.min_size
                )
            service = IngestService(
                repo=RagIngestRepository(pool),
                embeddings=FakeEmbeddings(dim=EMBEDDING_DIM),
                pii=PIIMasker(),
                chunker=chunker,
                strategy=strategy,
                chunk_params=chunk_params,
                build_name=name,
                embedding_provider="fake",
                embedding_model=EMBEDDING_MODEL,
                embedding_version=EMBEDDING_VERSION,
            )
            await service.ingest(CORPUS_DIR)
    finally:
        await pool.close()


@pytest.fixture(scope="module")
def ingested() -> None:
    """Ingesta del corpus (una vez por modulo) en las dos builds FIJAS.

    Idempotente por construccion: ids deterministicos + ON CONFLICT DO
    NOTHING. Reejecutar este modulo no duplica filas.
    """
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL must be set in environment")
    asyncio.run(_ingest_builds())


@pytest.fixture
async def pool(ingested):
    from wallet_ai.infrastructure.config.settings import Settings

    p = await asyncpg.create_pool(Settings().asyncpg_dsn, min_size=1, max_size=3)
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
async def audit_user_id(pool):
    """Usuario determinista para el test de auditoria persistida.

    INSERT idempotente por email fijo: reejecutar no duplica.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, email, role) VALUES ($1, $2, 'cliente') "
            "ON CONFLICT (email) DO NOTHING",
            AUDIT_USER_ID,
            AUDIT_USER_EMAIL,
        )
        return await conn.fetchval("SELECT id FROM users WHERE email = $1", AUDIT_USER_EMAIL)


# ---------------------------------------------------------------------------
# Dobles en memoria
# ---------------------------------------------------------------------------


class RecordingAuditor:
    """Auditor en memoria: registra (trace_id, user_id, event_type, payload)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def ensure_interaction(self, *, trace_id: str, user_id) -> None:
        pass

    async def record_event(self, *, trace_id: str, user_id, event_type: str, payload: dict) -> None:
        self.events.append(
            {"trace_id": trace_id, "user_id": user_id, "event_type": event_type, "payload": payload}
        )


class RecordingRepo:
    """Repositorio simulado con registro de llamadas.

    Con `respect_filter=False` IGNORA el filtro de niveles (para probar la
    defensa en profundidad); con True lo respeta.
    """

    def __init__(self, build: IndexBuild, rows: list[ScoredChunk], *, respect_filter: bool = False):
        self._build = build
        self._rows = rows
        self._respect = respect_filter
        self.search_calls: list[tuple] = []
        self.build_calls: list[str] = []

    async def get_build(self, name: str) -> IndexBuild:
        self.build_calls.append(name)
        return self._build

    async def lexical_search(self, *, build_id, allowed_levels, query: str, limit: int):
        self.search_calls.append(("lexical", tuple(sorted(allowed_levels)), limit))
        return self._select(allowed_levels, limit)

    async def vector_search(self, *, build_id, allowed_levels, embedding, limit: int):
        self.search_calls.append(("vector", tuple(sorted(allowed_levels)), limit))
        return self._select(allowed_levels, limit)

    def _select(self, allowed_levels, limit: int) -> list[ScoredChunk]:
        if self._respect:
            rows = [row for row in self._rows if row.access_level in allowed_levels]
        else:
            rows = self._rows
        return rows[:limit]


def _fake_build(
    model: str = EMBEDDING_MODEL, version: str = EMBEDDING_VERSION
) -> IndexBuild:
    return IndexBuild(
        id=uuid5(NAMESPACE_URL, "wallet-ai:tests:rag-retrieval-fake-build"),
        name="fake-build-retrieval",
        chunking_strategy="fijo",
        chunk_params={"size": CHUNK_SIZE, "overlap": OVERLAP},
        embedding_provider="fake",
        embedding_model=model,
        embedding_version=version,
        dimension=EMBEDDING_DIM,
    )


def _row(chunk_id, access_level: str, *, text: str = "Texto generico del fragmento de prueba.") -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
        document_id=uuid5(NAMESPACE_URL, f"wallet-ai:tests:rag-retrieval-doc:{access_level}"),
        title="Documento de prueba",
        text=text,
        access_level=access_level,
        score=0.5,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _embeddings(model: str = EMBEDDING_MODEL, version: str = EMBEDDING_VERSION) -> FakeEmbeddings:
    return FakeEmbeddings(dim=EMBEDDING_DIM, model_name=model, model_version=version)


def _ctx(
    pool,
    role: str = "cliente",
    *,
    trace_id: str | None = None,
    auditor=None,
    repo=None,
    embeddings=None,
    reranker=None,
) -> RetrievalContext:
    return RetrievalContext(
        trace_id=trace_id or uuid4().hex,
        user_id=TEST_USER_ID,
        role=role,
        repo=repo if repo is not None else DocumentRepository(pool),
        embeddings=embeddings if embeddings is not None else _embeddings(),
        pii=PIIMasker(),
        reranker=reranker,
        auditor=auditor if auditor is not None else RecordingAuditor(),
    )


def _config(
    build: str = BUILD_FIJO,
    mode: str = "hybrid",
    top_k: int = 3,
    rerank: bool = False,
    rrf_k: int = 60,
    pool_size: int = 50,
) -> RetrievalConfig:
    return RetrievalConfig(
        index_build=build,
        mode=mode,
        top_k=top_k,
        rerank=rerank,
        rrf_k=rrf_k,
        candidate_pool=pool_size,
    )


# ---------------------------------------------------------------------------
# (a) ACL antes del ranking
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cliente_acl_prefilter_exact_topk_and_no_internals(pool):
    """Por cada interno con gemelo publico: consulta literal del interno,
    como cliente, en los tres modos y las dos builds.

    - ningun hit interno (ni por nivel ni por documento);
    - con top_k=3 se obtienen EXACTAMENTE 3 hits permitidos: el filtro va
      antes del ranking, asi que ningun puesto del top-k se desperdicia en
      un fragmento que despues habria que descartar.
    """
    for build in BUILDS:
        for mode in MODES:
            for doc_id, query in ACL_QUERIES:
                hits = await retrieve(_ctx(pool, "cliente"), query, _config(build=build, mode=mode))
                assert len(hits) == 3, (
                    f"build={build} mode={mode} doc={doc_id}: se esperaban 3 hits "
                    f"permitidos, llegaron {len(hits)} (el filtro ACL debe ir ANTES "
                    "del ranking)"
                )
                levels = {hit.access_level for hit in hits}
                assert levels <= {"publico", "cliente"}, (
                    f"build={build} mode={mode} doc={doc_id}: niveles no permitidos {levels}"
                )
                assert not ({hit.document_id for hit in hits} & INTERNAL_DOC_IDS), (
                    f"build={build} mode={mode} doc={doc_id}: fuga de documento interno"
                )


# ---------------------------------------------------------------------------
# (b) soporte/compliance ven internos; rol desconocido no ve nada
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_soporte_and_compliance_retrieve_internals_with_same_query(pool):
    """Con la MISMA consulta literal de los internos, soporte y compliance SI
    los recuperan: el interno ocupa puestos del top-5. Este es el
    contrafactual del test (a): si el filtro fuera posterior al ranking,
    esos puestos le quitarian espacio al cliente."""
    for build in BUILDS:
        for doc_id, query in ACL_QUERIES:
            for role in ("soporte", "compliance"):
                if role == "compliance" and build != BUILD_FIJO:
                    continue  # compliance comparte el mapa de soporte: basta un sondeo
                hits = await retrieve(
                    _ctx(pool, role), query, _config(build=build, mode="hybrid", top_k=5)
                )
                internal_hits = [hit for hit in hits if hit.access_level == "interno"]
                assert internal_hits, (
                    f"build={build} doc={doc_id} role={role}: se esperaba al menos "
                    "un hit interno para un rol con acceso a 'interno'"
                )
                assert {hit.document_id for hit in internal_hits} & INTERNAL_DOC_IDS


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unknown_role_retrieves_nothing_but_audits(pool):
    """Un rol no reconocido no ejecuta NINGUNA busqueda, recibe [] y la
    llamada queda auditada con un unico evento retrieval de cero hits."""
    auditor = RecordingAuditor()
    rows = [_row(uuid5(NAMESPACE_URL, "wallet-ai:tests:rag-retrieval-chunk-pub"), "publico")]
    repo = RecordingRepo(_fake_build(), rows, respect_filter=True)
    ctx = _ctx(pool, "auditor-tribunal", auditor=auditor, repo=repo)

    hits = await retrieve(ctx, "limites de transferencia", _config())

    assert hits == []
    assert repo.search_calls == [], "un rol sin niveles permitidos no debe ejecutar busquedas"
    assert repo.build_calls == [], "el acceso se decide por rol antes de resolver la build"
    assert len(auditor.events) == 1
    event = auditor.events[0]
    assert event["event_type"] == "retrieval"
    assert event["trace_id"] == ctx.trace_id
    assert event["payload"]["role"] == "auditor-tribunal"
    assert event["payload"]["chunk_ids"] == []
    assert event["payload"]["document_ids"] == []


# ---------------------------------------------------------------------------
# (c) Defensa en profundidad
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repo_that_ignores_filter_triggers_acl_violation_and_guardrail_block(pool):
    """Si el repositorio ignora el filtro de niveles, retrieve:

    - lanza AclViolation (no devuelve nada);
    - audita UN guardrail_block con el motivo, los niveles permitidos y los
      chunks infractores (ids y niveles, nunca texto);
    - no escribe evento retrieval.
    """
    auditor = RecordingAuditor()
    publico = _row(uuid5(NAMESPACE_URL, "wallet-ai:tests:rag-retrieval-chunk-0"), "publico")
    interno = _row(
        uuid5(NAMESPACE_URL, "wallet-ai:tests:rag-retrieval-chunk-1"),
        "interno",
        text="Fragmento interno que no debe salir nunca.",
    )
    repo = RecordingRepo(_fake_build(), [publico, interno], respect_filter=False)
    ctx = _ctx(pool, "cliente", auditor=auditor, repo=repo)

    with pytest.raises(AclViolation):
        await retrieve(ctx, "limites de transferencia", _config(mode="hybrid"))

    # el caso de uso SI paso el filtro correcto al repositorio
    assert repo.search_calls, "retrieve debio llamar a las patas"
    for leg, levels, _limit in repo.search_calls:
        assert levels == ("cliente", "publico"), f"la pata {leg} recibio niveles incorrectos: {levels}"

    assert len(auditor.events) == 1, "solo el guardrail_block, sin evento retrieval"
    event = auditor.events[0]
    assert event["event_type"] == "guardrail_block"
    payload = event["payload"]
    assert payload["reason"] == "acl_violation"
    assert payload["role"] == "cliente"
    assert set(payload["allowed_levels"]) == {"publico", "cliente"}
    violations = payload["violations"]
    assert violations == [{"chunk_id": str(interno.chunk_id), "access_level": "interno"}]
    # sin texto de fragmentos en el evento
    assert "Fragmento interno" not in json.dumps(payload, default=str)
    assert "retrieval" not in [e["event_type"] for e in auditor.events]


# ---------------------------------------------------------------------------
# (e) Modos
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lexical_mode_finds_exact_word(pool):
    """Lexico: la palabra exacta 'Bronce' recupera los fragmentos que la
    contienen, encabezados por el documento de niveles de cuenta."""
    for build in BUILDS:
        hits = await retrieve(_ctx(pool), "Bronce", _config(build=build, mode="lexical"))
        assert hits, f"build={build}: la pata lexica debe encontrar la palabra exacta"
        assert hits[0].document_id == NIVEL_DOC_ID
        for hit in hits:
            assert hit.lexical_rank is not None
            assert hit.lexical_score is not None
            assert hit.vector_rank is None and hit.vector_score is None
            assert hit.rrf_score is None
            assert "bronce" in hit.text.lower(), (
                f"build={build}: un match lexico de 'Bronce' debe contener la palabra"
            )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vector_mode_finds_related_text_with_fake_embeddings(pool):
    """Vectorial: la consulta sobre 'correo de contacto y tarjeta' recupera
    el documento de actualizacion de datos SIN compartir literalmente su
    texto (la PII del documento ya viene enmascarada como <EMAIL>/<CARD>):
    es cercania de embeddings, no coincidencia literal."""
    for build in BUILDS:
        hits = await retrieve(_ctx(pool), PII_QUERY, _config(build=build, mode="vector"))
        assert hits, f"build={build}: la pata vectorial siempre devuelve candidatos"
        assert hits[0].document_id == PII_DOC_ID
        for hit in hits:
            assert hit.vector_rank is not None
            assert hit.vector_score is not None
            assert hit.lexical_rank is None and hit.lexical_score is None
            assert hit.rrf_score is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hybrid_mode_fuses_both_legs_coherently(pool):
    """Hibrido: cada hit del top-3 proviene del top-5 de alguna pata, el
    score RRF es exactamente la suma de sus aportes por pata y el orden es
    el de RRF con desempate por chunk_id. Los documentos que encabezan cada
    pata (limites y niveles) son los que encabezan la fusion.

    El top-3 de una pata NO es suficiente: RRF premia aparecer en AMBAS
    patas, de modo que un hit con ranks (4, 19) supera legitimamente a uno
    con (2, 34). La coherencia exigida es que la fusion se nutre de las
    patas, no que replique sus podiums exactos."""
    query = "el limite diario de las transferencias de la cuenta"
    for build in BUILDS:
        leg_lex = await retrieve(_ctx(pool), query, _config(build=build, mode="lexical", top_k=5))
        leg_vec = await retrieve(_ctx(pool), query, _config(build=build, mode="vector", top_k=5))
        hits = await retrieve(_ctx(pool), query, _config(build=build, mode="hybrid"))

        assert len(hits) == 3
        union = {hit.chunk_id for hit in leg_lex} | {hit.chunk_id for hit in leg_vec}
        for hit in hits:
            assert hit.chunk_id in union, (
                f"build={build}: el hit hibrido {hit.chunk_id} no esta en el top-5 "
                "de ninguna pata: la fusion no es coherente con las patas"
            )

        assert {hit.document_id for hit in hits} == {LIM_DOC_ID, NIVEL_DOC_ID}

        for hit in hits:
            expected = 0.0
            if hit.lexical_rank is not None:
                expected += 1.0 / (60 + hit.lexical_rank)
            if hit.vector_rank is not None:
                expected += 1.0 / (60 + hit.vector_rank)
            assert hit.rrf_score == pytest.approx(expected), (
                f"build={build}: el rrf_score de {hit.chunk_id} debe ser la suma "
                f"de sus aportes por pata ({expected})"
            )

        by_score = sorted(hits, key=lambda h: (-h.rrf_score, h.chunk_id))
        assert [h.chunk_id for h in hits] == [h.chunk_id for h in by_score], (
            f"build={build}: el orden debe ser por rrf_score descendente con "
            "desempate estable por chunk_id"
        )


# ---------------------------------------------------------------------------
# (f) Modelo de embeddings distinto al de la build
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_embedding_model_mismatch_fails_clearly_without_querying(pool):
    """El proveedor debe coincidir en modelo Y version con la build; si no,
    error claro y SIN ejecutar ninguna busqueda en el indice."""
    for kwargs in ({"version": "9.9.9"}, {"model": "otro-modelo"}):
        repo = RecordingRepo(_fake_build(), [])
        ctx = _ctx(pool, "cliente", repo=repo, embeddings=_embeddings(**kwargs))

        with pytest.raises(EmbeddingModelMismatch) as excinfo:
            await retrieve(ctx, "limites de transferencia", _config())

        message = str(excinfo.value)
        assert EMBEDDING_MODEL in message and EMBEDDING_VERSION in message
        if "model" in kwargs:
            assert "otro-modelo" in message
        else:
            assert "9.9.9" in message
        assert repo.search_calls == [], "el rechazo debe ocurrir antes de consultar el indice"
        assert ctx.auditor.events == [], "no hay evento de auditoria: la llamada no ocurrio"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unknown_build_fails_clearly(pool):
    with pytest.raises(UnknownIndexBuild, match="build-inexistente"):
        await retrieve(_ctx(pool), "limites de transferencia", _config(build="build-inexistente"))


# ---------------------------------------------------------------------------
# (g) PII: sin correo ni tarjeta en claro
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hits_never_contain_raw_pii(pool):
    """Los fragmentos (ya enmascarados en la ingesta) no contienen el correo
    ni la tarjeta sinteticos en NINGUN modo ni build, aunque la consulta
    apunte directo al documento que los menciona."""
    for build in BUILDS:
        seen_docs: set = set()
        joined = ""
        for mode in MODES:
            hits = await retrieve(
                _ctx(pool, "cliente"), PII_QUERY, _config(build=build, mode=mode, top_k=5)
            )
            for hit in hits:
                assert PII_EMAIL not in hit.text
                assert PII_CARD not in hit.text
                assert PII_CARD_DIGITS not in hit.text
                seen_docs.add(hit.document_id)
                joined += hit.text + "\n"
        assert PII_DOC_ID in seen_docs, (
            f"build={build}: el documento con PII sintetica debe ser recuperable "
            "para que la prueba sea significativa"
        )
        assert "<EMAIL>" in joined or "<CARD>" in joined, (
            f"build={build}: el enmascarado debe dejar sus marcas en los fragmentos"
        )


# ---------------------------------------------------------------------------
# (h) Auditoria: un evento retrieval por llamada, sin texto de fragmentos
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retrieval_audit_event_masks_query_and_has_no_chunk_text(pool):
    """Evento retrieval: consulta ENMASCARADA, configuracion, rol e ids;
    NUNCA el texto de los fragmentos ni la consulta en claro."""
    auditor = RecordingAuditor()
    query = f"mi correo es {PII_EMAIL} y mi tarjeta {PII_CARD}"
    ctx = _ctx(pool, "cliente", auditor=auditor)
    hits = await retrieve(ctx, query, _config(build=BUILD_FIJO, mode="hybrid"))

    assert len(auditor.events) == 1, "exactamente un evento retrieval por llamada"
    event = auditor.events[0]
    assert event["event_type"] == "retrieval"
    assert event["trace_id"] == ctx.trace_id
    assert event["user_id"] == TEST_USER_ID

    payload = event["payload"]
    assert PII_EMAIL not in json.dumps(payload)
    assert PII_CARD not in json.dumps(payload)
    assert "<EMAIL>" in payload["query"] and "<CARD>" in payload["query"]
    assert payload["role"] == "cliente"
    assert payload["index_build"] == BUILD_FIJO
    assert payload["mode"] == "hybrid"
    assert payload["top_k"] == 3
    assert payload["rrf_k"] == 60
    assert payload["candidate_pool"] == 50
    assert payload["rerank"] is False
    assert payload["chunk_ids"] == [str(hit.chunk_id) for hit in hits]
    assert payload["document_ids"] == [str(hit.document_id) for hit in hits]
    assert payload["access_levels"] == sorted({hit.access_level for hit in hits})

    serialized = json.dumps(payload, default=str)
    for hit in hits:
        assert hit.text not in serialized, "el evento de auditoria no debe llevar texto de chunks"
        fragment = hit.text[20:80].strip()
        if fragment:
            assert fragment not in serialized


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retrieval_event_persisted_in_audit_events(pool, audit_user_id):
    """El evento retrieval llega a audit_events (append-only) con la consulta
    enmascarada y sin texto de fragmentos, ligado a trace_id y usuario."""
    trace_id = uuid4().hex
    async with pool.acquire() as conn:
        ctx = RetrievalContext(
            trace_id=trace_id,
            user_id=audit_user_id,
            role="cliente",
            repo=DocumentRepository(pool),
            embeddings=_embeddings(),
            pii=PIIMasker(),
            reranker=None,
            auditor=ConnectionToolCallAuditor(conn),
        )
        hits = await retrieve(
            ctx,
            f"mi correo es {PII_EMAIL} y mi tarjeta {PII_CARD}",
            _config(build=BUILD_FIJO, mode="hybrid"),
        )

        events = await conn.fetch(
            "SELECT event_type, payload, user_id FROM audit_events WHERE trace_id = $1 "
            "ORDER BY created_at",
            trace_id,
        )
        has_interaction = await conn.fetchval(
            "SELECT 1 FROM interactions WHERE trace_id = $1", trace_id
        )

    assert has_interaction is not None, "record_event debe garantizar la fila de interactions"
    assert len(events) == 1
    assert events[0]["event_type"] == "retrieval"
    assert events[0]["user_id"] == audit_user_id

    payload = json.loads(events[0]["payload"])
    assert PII_EMAIL not in events[0]["payload"]
    assert PII_CARD not in events[0]["payload"]
    assert "<EMAIL>" in payload["query"] and "<CARD>" in payload["query"]
    assert payload["chunk_ids"] == [str(hit.chunk_id) for hit in hits]
    serialized = json.dumps(payload, default=str)
    for hit in hits:
        assert hit.text not in serialized


# ---------------------------------------------------------------------------
# (i) Determinismo
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_parameters_same_order_across_runs(pool):
    """Mismos parametros (misma build, modo, top_k y consulta) -> mismo
    orden de chunk_ids en dos ejecuciones independientes."""
    for build in BUILDS:
        for mode in MODES:
            first = await retrieve(_ctx(pool), "limites de transferencia", _config(build=build, mode=mode))
            second = await retrieve(_ctx(pool), "limites de transferencia", _config(build=build, mode=mode))
            assert [hit.chunk_id for hit in first] == [hit.chunk_id for hit in second], (
                f"build={build} mode={mode}: el orden debe ser determinista"
            )


# ---------------------------------------------------------------------------
# (j) Consultas hostiles o invalidas
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_queries_are_rejected_explicitly(pool):
    """Vacia, solo espacios o de 301 caracteres se rechazan explicitamente;
    exactamente 300 caracteres es valida. El rechazo ocurre ANTES de auditar."""
    auditor = RecordingAuditor()
    ctx = _ctx(pool, "cliente", auditor=auditor)

    for bad in ("", "   \n  ", "a" * 301):
        with pytest.raises(InvalidRetrievalQuery):
            await retrieve(ctx, bad, _config())
    assert auditor.events == [], "el rechazo de la consulta no genera eventos"

    with pytest.raises(InvalidRetrievalQuery, match="300"):
        await retrieve(ctx, "a" * 301, _config())

    hits = await retrieve(ctx, "a" * 300, _config(mode="lexical"))
    assert isinstance(hits, list), "301 caracteres se rechaza, 300 no"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tsquery_special_characters_do_not_break_retrieval(pool):
    """Caracteres de tsquery (' & | ! ( ) :*) en la consulta no rompen
    ninguna pata (websearch_to_tsquery nunca lanza)."""
    hostile = "limites & comisiones | !negacion (foo) :bar *baz"
    for mode in MODES:
        hits = await retrieve(_ctx(pool), hostile, _config(mode=mode))
        assert isinstance(hits, list), f"mode={mode}: la consulta hostil no debe romper nada"


# ---------------------------------------------------------------------------
# Re-ranking opcional (puerto Reranker, fake)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rerank_with_fake_reranker_over_candidate_pool(pool):
    """Con rerank=True, el fake reordena el pool de candidatos por solape de
    tokens: los fragmentos que contienen la palabra de la consulta quedan
    primero, el resultado sigue filtrado por ACL y es determinista."""
    for build in BUILDS:
        ctx = _ctx(pool, "cliente", reranker=FakeReranker())
        hits = await retrieve(ctx, "Bronce", _config(build=build, mode="hybrid", rerank=True))
        assert len(hits) == 3
        assert {hit.access_level for hit in hits} <= {"publico", "cliente"}
        assert "bronce" in hits[0].text.lower(), (
            f"build={build}: el reranker debe poner primero un fragmento con la palabra consultada"
        )
        again = await retrieve(_ctx(pool, "cliente", reranker=FakeReranker()), "Bronce",
                               _config(build=build, mode="hybrid", rerank=True))
        assert [hit.chunk_id for hit in hits] == [hit.chunk_id for hit in again]
