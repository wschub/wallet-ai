"""Caso de uso: recuperacion hibrida con ACL previa al ranking (tramo B de US3, T050-T052).

Pipeline de `retrieve` (el ORDEN es parte del contrato):

    validar consulta -> ACL por rol -> enmascarar PII -> resolver build ->
    verificar modelo de embeddings -> patas (lexica y/o vectorial) ->
    defensa en profundidad -> fusion RRF / orden de pata -> re-ranking
    opcional -> top_k -> auditoria -> hits

Decisiones del encargo del tramo B:

1. El acceso por rol vive en UN unico lugar: `domain.documents.access`
   (`allowed_levels_for_role`). Un rol no reconocido no recupera NADA (ni
   siquiera ejecuta busquedas), pero la llamada SI queda auditada.
2. `RetrievalConfig` fija build, modo (lexical|vector|hybrid), top_k,
   re-ranking, k de RRF y tamano del pool de candidatos.
3. El repositorio aplica el filtro `index_build_id AND access_level` en el
   WHERE, ANTES de ORDER BY y LIMIT: el control de acceso es previo al
   ranking (ningun puesto del top-k se desperdicia en fragmentos
   prohibidos). La pata lexica usa `websearch_to_tsquery('spanish', ...)`
   con `ts_rank_cd`; la vectorial, distancia coseno EXACTA (`<=>`) contra el
   embedding de la consulta (sin indice aproximado en este tramo; la
   comparacion de estrategias del tramo EVAL decidira si hace falta un
   HNSW). Si el filtro llegara a ser demasiado restrictivo para un indice
   aproximado, la alternativa documentada (research.md, decision 4) es
   `iterative_scan`: seguir escaneando por similitud aplicando el filtro
   hasta llenar el top-k. Con la busqueda exacta de este tramo no hace
   falta: el escaneo completo ya respeta el WHERE.
4. La fusion RRF (k=60 por defecto) es una FUNCION PURA (`rrf_fuse`) con
   desempate estable por chunk_id; el re-ranking opcional va sobre el pool
   de candidatos por el puerto `Reranker` (el fake basta).
5. Defensa en profundidad: si algun candidato llega con un nivel no
   permitido pese al filtro, se lanza `AclViolation`, se audita
   `guardrail_block` y NO se devuelve nada.
6. La consulta no puede estar vacia ni exceder 300 caracteres (rechazo
   explicito). La PII se enmascara ANTES de calcular el embedding, de
   auditar o de registrar. El proveedor de embeddings debe coincidir en
   modelo y version con los de la build; si no, error claro SIN consultar
   el indice.
7. Auditoria: UN evento `retrieval` por llamada completada (consulta ya
   enmascarada, configuracion, rol e ids de chunks/documentos con sus
   niveles) y NUNCA texto de fragmentos. Las violaciones de ACL auditan
   `guardrail_block`; los rechazos de validacion (vacia/301) no auditan:
   la llamada nunca llego a ocurrir.

Determinismo: sin reloj ni azar; el desempate es siempre el chunk_id, de
modo que los mismos parametros producen el mismo orden.

Aislamiento: este modulo no importa el ledger ni `confirm_draft`; lo exige
el contrato 'Confirmation isolation' de import-linter.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from wallet_ai.application.rag.errors import (
    EmbeddingModelMismatch,
    InvalidRetrievalQuery,
    UnknownIndexBuild,
)
from wallet_ai.domain.documents.access import AclViolation, allowed_levels_for_role
from wallet_ai.domain.ports.audit import AssistantTurnAuditor
from wallet_ai.domain.ports.embedding import EmbeddingProvider
from wallet_ai.domain.ports.pii_service import PIIService
from wallet_ai.domain.ports.reranker import Reranker
from wallet_ai.domain.ports.retrieval import DocumentRepository, ScoredChunk

MODES = ("lexical", "vector", "hybrid")
MAX_QUERY_CHARS = 300
DEFAULT_RRF_K = 60
DEFAULT_CANDIDATE_POOL = 50

_HEADER_RE = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Configuracion y contexto
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievalConfig:
    """Parametros de una recuperacion.

    - `index_build`: NOMBRE de la build a consultar (la clave bajo la que
      la ingesta registro proveedor/modelo/version/dimension).
    - `mode`: lexical | vector | hybrid.
    - `top_k`: fragmentos a devolver.
    - `rerank`: reordenar el pool de candidatos con el puerto Reranker.
    - `rrf_k`: constante k de la fusion RRF (solo hybrid).
    - `candidate_pool`: cuantos candidatos pedir a cada pata antes de
      fusionar/reordenar (>= top_k, para que el re-ranking tenga margen).
    """

    index_build: str
    mode: str
    top_k: int
    rerank: bool = False
    rrf_k: int = DEFAULT_RRF_K
    candidate_pool: int = DEFAULT_CANDIDATE_POOL

    def __post_init__(self) -> None:
        if not self.index_build.strip():
            raise ValueError("index_build no puede estar vacio")
        if self.mode not in MODES:
            raise ValueError(f"mode invalido: {self.mode!r} (debe ser uno de {MODES})")
        if self.top_k < 1:
            raise ValueError(f"top_k debe ser >= 1, llego {self.top_k}")
        if self.rrf_k < 1:
            raise ValueError(f"rrf_k debe ser >= 1, llego {self.rrf_k}")
        if self.candidate_pool < self.top_k:
            raise ValueError(
                f"candidate_pool ({self.candidate_pool}) debe ser >= top_k ({self.top_k}): "
                "el pool de candidatos no puede ser menor que el resultado"
            )


@dataclass(frozen=True)
class RetrievalContext:
    """Identidad y puertos de una llamada de recuperacion.

    La identidad la construye SIEMPRE quien invoca (el orquestador a
    partir del JWT, un test): aqui no entran tokens. `auditor` es
    obligatorio: la auditoria es parte del contrato de la recuperacion
    (un evento retrieval por llamada).
    """

    trace_id: str
    user_id: UUID
    role: str
    repo: DocumentRepository
    embeddings: EmbeddingProvider
    pii: PIIService
    auditor: AssistantTurnAuditor
    reranker: Reranker | None = None

    def __post_init__(self) -> None:
        if not self.trace_id.strip():
            raise ValueError("trace_id no puede estar vacio")
        if not self.role.strip():
            raise ValueError("role no puede estar vacio")
        if self.auditor is None:
            raise ValueError("auditor es obligatorio: la recuperacion audita cada llamada")


@dataclass(frozen=True)
class RetrievalHit:
    """Fragmento recuperado, ordenado, con sus puntajes/rangos por pata.

    `text` ya viene enmascarado de la ingesta. `section` es el primer
    encabezado markdown del fragmento cuando existe (la estrategia
    estructural hace viajar el encabezado con su seccion); None si el
    fragmento no abre con encabezado. Los rangos son 1-based dentro del
    pool de candidatos de cada pata; `rrf_score` solo en modo hybrid.
    """

    chunk_id: UUID
    document_id: UUID
    title: str
    section: str | None
    text: str
    access_level: str
    lexical_rank: int | None = None
    lexical_score: float | None = None
    vector_rank: int | None = None
    vector_score: float | None = None
    rrf_score: float | None = None


# ---------------------------------------------------------------------------
# Fusion RRF (funcion pura)
# ---------------------------------------------------------------------------


def rrf_fuse(*rankings: Sequence[Any], k: int = DEFAULT_RRF_K) -> list[tuple[Any, float]]:
    """Fusiona rankings ordenados por Reciprocal Rank Fusion.

    Cada item suma `1 / (k + rango)` por cada pata en la que aparece (rango
    1-based). El resultado va por score descendente con desempate ESTABLE
    por el propio item (el chunk_id en el uso real), de modo que la fusion
    es determinista y reproducible.

    Los rankings no deben repetir items (el SQL de cada pata no puede
    devolver el mismo chunk dos veces).
    """
    scores: dict[Any, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


# ---------------------------------------------------------------------------
# Recuperacion
# ---------------------------------------------------------------------------


async def retrieve(
    ctx: RetrievalContext, query: str, config: RetrievalConfig
) -> list[RetrievalHit]:
    """Recupera fragmentos permitidos para el rol, ordenados y auditados.

    Ver el docstring del modulo para el contrato completo. Falla cerrado:
    rol desconocido -> nada; repositorio desobediente -> AclViolation y
    nada; proveedor de embeddings equivocado -> error sin consultar.
    """
    # (6) validacion explicita de la consulta (antes de todo lo demas)
    normalized = (query or "").strip()
    if not normalized:
        raise InvalidRetrievalQuery("la consulta no puede estar vacia")
    if len(normalized) > MAX_QUERY_CHARS:
        raise InvalidRetrievalQuery(
            f"la consulta tiene {len(normalized)} caracteres y el maximo es {MAX_QUERY_CHARS}"
        )

    # (1) ACL por rol: unico lugar de decision. Vacio -> nada (fail closed),
    # pero la llamada se audita igual.
    allowed = allowed_levels_for_role(ctx.role)

    # (6) la PII se enmascara ANTES de embeder, auditar o registrar
    masked_query = ctx.pii.mask(normalized)

    if not allowed:
        await _audit_retrieval(ctx, config, masked_query, [])
        return []

    # build: existe y con QUE embeddings se indexo
    build = await ctx.repo.get_build(config.index_build)
    if build is None:
        raise UnknownIndexBuild(f"la build de indice '{config.index_build}' no existe")

    # (6) el proveedor debe coincidir con la build: si no, error claro
    # SIN consultar el indice
    _assert_provider_matches_build(ctx.embeddings, build)

    # -----------------------------------------------------------------
    # patas (el filtro ACL va en el WHERE de cada una, antes del ranking)
    # -----------------------------------------------------------------
    allowed_sorted = tuple(sorted(allowed))
    lexical_rows: list[ScoredChunk] = []
    vector_rows: list[ScoredChunk] = []
    if config.mode in ("lexical", "hybrid"):
        lexical_rows = await ctx.repo.lexical_search(
            build_id=build.id,
            allowed_levels=allowed_sorted,
            query=masked_query,
            limit=config.candidate_pool,
        )
    if config.mode in ("vector", "hybrid"):
        query_vector = await _embed_query(ctx.embeddings, masked_query)
        if len(query_vector) != build.dimension:
            raise EmbeddingModelMismatch(
                f"el proveedor de embeddings ({ctx.embeddings.model_name!r} "
                f"{ctx.embeddings.model_version!r}) produce vectores de dimension "
                f"{len(query_vector)} y la build '{build.name}' se indexo con "
                f"dimension {build.dimension}"
            )
        vector_rows = await ctx.repo.vector_search(
            build_id=build.id,
            allowed_levels=allowed_sorted,
            embedding=query_vector,
            limit=config.candidate_pool,
        )

    # -----------------------------------------------------------------
    # (5) defensa en profundidad: nadie fuera de nivel pasa de aqui
    # -----------------------------------------------------------------
    violations: list[ScoredChunk] = []
    _seen_violations: set[UUID] = set()
    for row in (*lexical_rows, *vector_rows):
        if row.access_level not in allowed and row.chunk_id not in _seen_violations:
            _seen_violations.add(row.chunk_id)
            violations.append(row)
    if violations:
        await _audit_guardrail_block(ctx, config, allowed_sorted, violations)
        raise AclViolation(
            "el repositorio devolvio fragmentos fuera del nivel permitido del rol: "
            + ", ".join(f"{row.chunk_id} ({row.access_level})" for row in violations)
        )

    # -----------------------------------------------------------------
    # orden: RRF en hybrid, orden de pata en single-mode
    # -----------------------------------------------------------------
    candidates: list[ScoredChunk]
    rrf_scores: dict[UUID, float] | None = None
    if config.mode == "hybrid":
        fused = rrf_fuse(
            [row.chunk_id for row in lexical_rows],
            [row.chunk_id for row in vector_rows],
            k=config.rrf_k,
        )
        rrf_scores = {chunk_id: score for chunk_id, score in fused}
        by_id = {row.chunk_id: row for row in (*lexical_rows, *vector_rows)}
        candidates = [by_id[chunk_id] for chunk_id, _score in fused]
    else:
        candidates = lexical_rows if config.mode == "lexical" else vector_rows

    # -----------------------------------------------------------------
    # (4) re-ranking opcional sobre el pool de candidatos
    # -----------------------------------------------------------------
    if config.rerank:
        if ctx.reranker is None:
            raise ValueError("config.rerank=True pero el contexto no lleva reranker")
        if candidates:
            docs = [row.text for row in candidates]
            order = await _rerank(ctx.reranker, masked_query, docs, config.top_k)
            seen: set[int] = set()
            picked: list[ScoredChunk] = []
            for index in order:
                if index in seen or not 0 <= index < len(candidates):
                    continue
                seen.add(index)
                picked.append(candidates[index])
            candidates = picked

    top = candidates[: config.top_k]

    # -----------------------------------------------------------------
    # hits con puntajes/rangos por pata
    # -----------------------------------------------------------------
    lexical_ranks = {row.chunk_id: rank for rank, row in enumerate(lexical_rows, start=1)}
    lexical_scores = {row.chunk_id: row.score for row in lexical_rows}
    vector_ranks = {row.chunk_id: rank for rank, row in enumerate(vector_rows, start=1)}
    vector_scores = {row.chunk_id: row.score for row in vector_rows}

    hits = [
        RetrievalHit(
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            title=row.title,
            section=_section_of(row.text),
            text=row.text,
            access_level=row.access_level,
            lexical_rank=lexical_ranks.get(row.chunk_id),
            lexical_score=lexical_scores.get(row.chunk_id),
            vector_rank=vector_ranks.get(row.chunk_id),
            vector_score=vector_scores.get(row.chunk_id),
            rrf_score=rrf_scores[row.chunk_id] if rrf_scores is not None else None,
        )
        for row in top
    ]

    # (7) un evento retrieval por llamada, sin texto de fragmentos
    await _audit_retrieval(ctx, config, masked_query, hits)
    return hits


# ---------------------------------------------------------------------------
# Internos
# ---------------------------------------------------------------------------


def _assert_provider_matches_build(provider: EmbeddingProvider, build: Any) -> None:
    """Modelo y version del proveedor == los registrados en la build."""
    if (
        provider.model_name != build.embedding_model
        or provider.model_version != build.embedding_version
    ):
        raise EmbeddingModelMismatch(
            f"el proveedor de embeddings declara model={provider.model_name!r} "
            f"version={provider.model_version!r}, pero la build '{build.name}' se "
            f"indexo con model={build.embedding_model!r} version="
            f"{build.embedding_version!r}: usa el proveedor con el que se creo la "
            "build o crea otra build (los vectores de otro modelo no son comparables)"
        )


async def _embed_query(provider: EmbeddingProvider, masked_query: str) -> list[float]:
    result = provider.embed([masked_query])
    if inspect.isawaitable(result):
        result = await result
    vectors = result
    if len(vectors) != 1:
        raise ValueError(
            f"el proveedor de embeddings devolvio {len(vectors)} vectores para 1 consulta"
        )
    return vectors[0]


async def _rerank(reranker: Reranker, query: str, docs: list[str], top_k: int) -> list[int]:
    result = reranker.rerank(query, docs, top_k)
    if inspect.isawaitable(result):
        result = await result
    return result


def _section_of(text: str) -> str | None:
    """Primer encabezado markdown del fragmento (la seccion donde empieza)."""
    match = _HEADER_RE.search(text)
    return match.group(1).strip() if match else None


async def _audit_retrieval(
    ctx: RetrievalContext,
    config: RetrievalConfig,
    masked_query: str,
    hits: list[RetrievalHit],
) -> None:
    """Evento `retrieval`: consulta enmascarada, configuracion, rol e ids.

    NUNCA texto de fragmentos (ni el crudo ni el enmascarado): solo ids y
    niveles, que son lo que compliance necesita para reconstruir la traza.
    """
    payload = {
        "query": masked_query,
        "index_build": config.index_build,
        "mode": config.mode,
        "top_k": config.top_k,
        "rerank": config.rerank,
        "rrf_k": config.rrf_k,
        "candidate_pool": config.candidate_pool,
        "role": ctx.role,
        "access_levels": sorted({hit.access_level for hit in hits}),
        "chunk_ids": [str(hit.chunk_id) for hit in hits],
        "document_ids": [str(hit.document_id) for hit in hits],
    }
    await ctx.auditor.record_event(
        trace_id=ctx.trace_id,
        user_id=ctx.user_id,
        event_type="retrieval",
        payload=payload,
    )


async def _audit_guardrail_block(
    ctx: RetrievalContext,
    config: RetrievalConfig,
    allowed_levels: tuple[str, ...],
    violations: Sequence[ScoredChunk],
) -> None:
    """Bloqueo de la defensa en profundidad: ids y niveles, nunca texto."""
    payload = {
        "reason": "acl_violation",
        "index_build": config.index_build,
        "mode": config.mode,
        "role": ctx.role,
        "allowed_levels": list(allowed_levels),
        "violations": [
            {"chunk_id": str(row.chunk_id), "access_level": row.access_level}
            for row in violations
        ],
    }
    await ctx.auditor.record_event(
        trace_id=ctx.trace_id,
        user_id=ctx.user_id,
        event_type="guardrail_block",
        payload=payload,
    )
