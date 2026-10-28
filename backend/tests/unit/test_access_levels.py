"""Mapa rol -> niveles de documento permitidos (tramo B de US3, T050-T052).

`domain/documents/access.py` es el UNICO lugar del sistema donde se decide
que niveles puede ver cada rol (encargo del tramo B, punto 1):

- cliente        -> {publico, cliente}
- soporte        -> {publico, cliente, interno}
- compliance     -> {publico, cliente, interno}
- cualquier otro -> conjunto VACIO (no recupera nada)

El resto del codigo (retrieval, repositorios, API) debe consumir
`allowed_levels_for_role` en vez de repetir el mapa.
"""

from __future__ import annotations

import types

import pytest

from wallet_ai.domain.documents import access
from wallet_ai.domain.documents.access import (
    ROLE_ACCESS_LEVELS,
    AclViolation,
    allowed_levels_for_role,
)


def test_cliente_sees_publico_and_cliente_only():
    assert allowed_levels_for_role("cliente") == frozenset({"publico", "cliente"})


def test_soporte_sees_all_levels():
    assert allowed_levels_for_role("soporte") == frozenset({"publico", "cliente", "interno"})


def test_compliance_sees_all_levels():
    assert allowed_levels_for_role("compliance") == frozenset({"publico", "cliente", "interno"})


def test_unknown_role_sees_nothing():
    """Roles no reconocidos no recuperan NADA: conjunto vacio, no 'todo'."""
    for unknown in ("auditor", "admin", "", "Cliente", "CLIENTE", "soporte ", "gestor"):
        assert allowed_levels_for_role(unknown) == frozenset(), (
            f"el rol {unknown!r} no deberia tener acceso a ningun nivel"
        )


def test_result_is_immutable():
    levels = allowed_levels_for_role("cliente")
    assert isinstance(levels, frozenset)


def test_acl_violation_is_a_distinct_error():
    """La defensa en profundidad lanza AclViolation (no un error generico)."""
    error = AclViolation("chunk fuera de nivel")
    assert isinstance(error, Exception)
    assert "fuera de nivel" in str(error)


# ---------------------------------------------------------------------------
# El mapa es de solo lectura y su invarianta se comprueba siempre
# ---------------------------------------------------------------------------


def test_role_access_levels_rejects_key_assignment():
    """El mapa rol -> niveles es de solo lectura: asignar una clave lanza TypeError.

    `ROLE_ACCESS_LEVELS` es un `types.MappingProxyType`: la politica de
    acceso solo se define en este modulo y ningun consumidor puede
    ampliarla en ejecucion (un dict mutable permitiria abrir niveles por
    accidente desde cualquier parte del sistema).
    """
    with pytest.raises(TypeError):
        ROLE_ACCESS_LEVELS["intruso"] = frozenset({"publico"})
    # Mecanismo: envoltorio de solo lectura, no una copia defensiva.
    assert isinstance(ROLE_ACCESS_LEVELS, types.MappingProxyType)


def test_corrupted_map_fails_closed_instead_of_asserting(monkeypatch):
    """La invarianta del mapa se comprueba SIEMPRE, tambien con `python -O`.

    Un `assert` desaparece cuando el interprete corre optimizado; la
    comprobacion explicita garantiza que un mapa con niveles fuera de
    ACCESS_LEVELS falle cerrado con AclViolation en vez de devolver
    niveles que no existen en el corpus.
    """
    monkeypatch.setattr(
        access,
        "ROLE_ACCESS_LEVELS",
        {"cliente": frozenset({"publico", "secreto"})},
    )
    with pytest.raises(AclViolation):
        allowed_levels_for_role("cliente")
