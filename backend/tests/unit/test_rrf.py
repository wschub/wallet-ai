"""RRF puro y validacion de la configuracion de recuperacion (tramo B de US3, T051).

`rrf_fuse` es una funcion PURA (encargo del tramo B, punto 4): dada una o
mas listas ordenadas (los rankings de cada pata), suma
`1 / (k + rango)` por aparicion y devuelve los items ordenados por score
descendente con desempate ESTABLE por el propio id del item. Aqui se prueban
casos con rangos conocidos, empates y cambio de k (punto d del encargo).

Tambien se valida la construccion de `RetrievalConfig` y `RetrievalContext`
(errores claros en la configuracion, nunca silenciosos).
"""

from __future__ import annotations

import math
from uuid import UUID, uuid4

import pytest

from wallet_ai.application.rag.retrieval import RetrievalConfig, RetrievalContext, rrf_fuse

# ---------------------------------------------------------------------------
# Casos con rangos conocidos
# ---------------------------------------------------------------------------


def test_rrf_single_ranking_orders_by_rank():
    fused = rrf_fuse(["a", "b", "c"])
    assert [item for item, _score in fused] == ["a", "b", "c"]
    assert fused[0][1] == pytest.approx(1.0 / 61.0)  # k=60, rango 1
    assert fused[1][1] == pytest.approx(1.0 / 62.0)
    assert fused[2][1] == pytest.approx(1.0 / 63.0)


def test_rrf_symmetric_rankings_produce_known_scores_and_tiebreak():
    """a y c son simetricos (1/61 + 1/63) y empatan por encima de b (2/62).

    El empate entre a y c se rompe de forma estable por el id del item.
    """
    fused = rrf_fuse(["a", "b", "c"], ["c", "b", "a"])
    assert [item for item, _score in fused] == ["a", "c", "b"]
    assert fused[0][1] == pytest.approx(1.0 / 61.0 + 1.0 / 63.0)
    assert fused[1][1] == pytest.approx(1.0 / 61.0 + 1.0 / 63.0)
    assert fused[2][1] == pytest.approx(2.0 / 62.0)
    # a y c empatan exactamente (simetria) y b queda debajo por muy poco.
    assert fused[2][1] < fused[1][1]


def test_rrf_item_in_both_legs_beats_item_in_one_leg():
    fused = rrf_fuse(["a", "b"], ["b"])
    assert [item for item, _score in fused] == ["b", "a"]
    assert fused[0][1] == pytest.approx(1.0 / 62.0 + 1.0 / 61.0)
    assert fused[1][1] == pytest.approx(1.0 / 61.0)


def test_rrf_item_only_in_second_leg_is_counted():
    fused = rrf_fuse(["a"], ["b", "a"])
    assert [item for item, _score in fused] == ["a", "b"]
    assert fused[0][1] == pytest.approx(1.0 / 61.0 + 1.0 / 62.0)


# ---------------------------------------------------------------------------
# Empates y desempate estable
# ---------------------------------------------------------------------------


def test_rrf_tie_between_legs_breaks_by_item_id():
    """Dos items con el MISMO score (rango 1 en patas distintas) se ordenan
    por el id del item, no por el orden de las patas."""
    low = UUID(int=1)
    high = UUID(int=2)
    fused = rrf_fuse([high], [low])
    assert [item for item, _score in fused] == [low, high]
    assert fused[0][1] == pytest.approx(fused[1][1])


def test_rrf_uuid_items_are_comparable_for_tiebreak():
    a = UUID(int=0)
    b = UUID(int=1)
    fused = rrf_fuse([a, b], [b, a])
    assert [item for item, _score in fused] == [a, b]


# ---------------------------------------------------------------------------
# Cambio de k
# ---------------------------------------------------------------------------


def test_rrf_k_changes_scores():
    assert rrf_fuse(["a"], k=1)[0][1] == pytest.approx(1.0 / 2.0)
    assert rrf_fuse(["a"], k=60)[0][1] == pytest.approx(1.0 / 61.0)
    # Con k pequeno la diferencia entre rangos pesa mas.
    wide = rrf_fuse(["a", "b"], k=1)
    assert wide[0][1] - wide[1][1] == pytest.approx(1.0 / 2.0 - 1.0 / 3.0)


def test_rrf_k_affects_fusion_order():
    """Con k=1 los rangos pesan mucho mas que con k=60: la misma pareja de
    rankings produce scores distintos (k=1: 1/2 + 1/3; k=60: 1/61 + 1/62)."""
    fused_small = rrf_fuse(["x", "y"], ["y", "x"], k=1)
    # x: 1/2 + 1/3; y: 1/3 + 1/2 -> empate exacto, desempate por id.
    assert [item for item, _score in fused_small] == ["x", "y"]
    assert fused_small[0][1] == pytest.approx(1.0 / 2.0 + 1.0 / 3.0)

    fused_big = rrf_fuse(["x", "y"], ["y", "x"], k=60)
    assert fused_big[0][1] == pytest.approx(1.0 / 61.0 + 1.0 / 62.0)

    z_only_second = rrf_fuse(["a", "b"], ["b"], k=1)
    # b: 1/3 + 1/2 = 5/6; a: 1/2. b gana.
    assert [item for item, _score in z_only_second] == ["b", "a"]


# ---------------------------------------------------------------------------
# Bordes
# ---------------------------------------------------------------------------


def test_rrf_empty_rankings_return_empty():
    assert rrf_fuse() == []
    assert rrf_fuse([]) == []
    assert rrf_fuse([], []) == []


def test_rrf_no_nans_or_infs():
    fused = rrf_fuse(["a", "b"], ["b", "c"], k=60)
    for _item, score in fused:
        assert math.isfinite(score)


# ---------------------------------------------------------------------------
# Validacion de RetrievalConfig
# ---------------------------------------------------------------------------


def test_config_defaults():
    config = RetrievalConfig(index_build="b", mode="hybrid", top_k=3)
    assert config.rrf_k == 60
    assert config.rerank is False
    assert config.candidate_pool >= config.top_k


def test_config_rejects_unknown_mode():
    for mode in ("híbrido", "", "HYBRID", "semantic", None):
        with pytest.raises(ValueError, match="mode"):
            RetrievalConfig(index_build="b", mode=mode, top_k=3)


def test_config_rejects_bad_numbers():
    with pytest.raises(ValueError, match="top_k"):
        RetrievalConfig(index_build="b", mode="hybrid", top_k=0)
    with pytest.raises(ValueError, match="rrf_k"):
        RetrievalConfig(index_build="b", mode="hybrid", top_k=3, rrf_k=0)
    with pytest.raises(ValueError, match="candidate_pool"):
        RetrievalConfig(index_build="b", mode="hybrid", top_k=10, candidate_pool=5)
    with pytest.raises(ValueError, match="index_build"):
        RetrievalConfig(index_build="   ", mode="hybrid", top_k=3)


# ---------------------------------------------------------------------------
# Validacion de RetrievalContext
# ---------------------------------------------------------------------------


class _Stub:
    """Placeholder de puerto: el contexto solo valida presencia, no tipos."""


def _ctx(**overrides):
    kwargs = dict(
        trace_id="trace-1",
        user_id=uuid4(),
        role="cliente",
        repo=_Stub(),
        embeddings=_Stub(),
        pii=_Stub(),
        reranker=None,
        auditor=_Stub(),
    )
    kwargs.update(overrides)
    return RetrievalContext(**kwargs)


def test_context_requires_trace_id_and_auditor():
    """Sin trace_id o sin auditor la configuracion es invalida: la auditoria
    es parte del contrato (un evento retrieval por llamada)."""
    with pytest.raises(ValueError, match="trace_id"):
        _ctx(trace_id="  ")
    with pytest.raises(ValueError, match="auditor"):
        _ctx(auditor=None)
