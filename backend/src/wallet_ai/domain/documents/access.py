"""Control de acceso por rol sobre los niveles de documento (tramo B de US3).

Este modulo es el UNICO lugar del sistema donde vive el mapa rol -> niveles
permitidos (encargo del tramo B, punto 1). Todo lo demas (recuperacion,
repositorios, API) debe consumir `allowed_levels_for_role` en vez de repetir
la tabla: el acceso se evalua por rol EN CADA PETICION, no por sesion.

- cliente    -> {publico, cliente}
- soporte    -> {publico, cliente, interno}
- compliance -> {publico, cliente, interno}
- cualquier otro rol -> conjunto VACIO: no recupera nada (fail closed,
  nunca fail open).

El mapa es de SOLO LECTURA (`types.MappingProxyType`): la politica de
acceso no se puede alterar en tiempo de ejecucion desde ningun consumidor.

`AclViolation` es la defensa en profundidad: si despues de filtrar aparece
un fragmento fuera de nivel, o si el propio mapa contiene niveles
desconocidos, se lanza este error y NO se devuelve nada.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

ACCESS_LEVELS = ("publico", "cliente", "interno")

ROLE_ACCESS_LEVELS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "cliente": frozenset({"publico", "cliente"}),
        "soporte": frozenset({"publico", "cliente", "interno"}),
        "compliance": frozenset({"publico", "cliente", "interno"}),
    }
)

_ALL_LEVELS = frozenset(ACCESS_LEVELS)


class AclViolation(Exception):
    """Defensa en profundidad del control de acceso por niveles.

    Dos sitios la lanzan:

    - `application.rag.retrieval`, cuando el repositorio devolvio chunks
      con un nivel no permitido A PESAR del filtro previo (bug del
      adaptador o de la base). Al lanzarse no se devuelve nada y el
      bloqueo queda auditado como guardrail_block.
    - `allowed_levels_for_role`, cuando el mapa rol -> niveles contiene
      niveles fuera de ACCESS_LEVELS (bug de este mismo modulo). La
      comprobacion es explicita, no un `assert`, para que la invarianta
      siga activa aunque el interprete corra optimizado (`python -O`).
    """


def allowed_levels_for_role(role: str) -> frozenset[str]:
    """Niveles de documento que el rol puede ver.

    Un rol no reconocido devuelve un conjunto VACIO: sin acceso a nada,
    nunca acceso a todo. El resultado es inmutable (frozenset) para que
    ningun consumidor pueda ampliarlo por accidente.

    La invarianta del mapa (todo nivel otorgado esta dentro de
    ACCESS_LEVELS) se comprueba de forma EXPLICITA y lanza AclViolation
    si se rompe: es una garantia de seguridad, no una ayuda de
    depuracion, y no debe desaparecer cuando el interprete corre
    optimizado (`python -O`).
    """
    levels = ROLE_ACCESS_LEVELS.get(role)
    if levels is None:
        return frozenset()
    if not levels <= _ALL_LEVELS:
        raise AclViolation(
            "el mapa rol -> niveles contiene niveles fuera de ACCESS_LEVELS "
            f"para el rol {role!r}: " + ", ".join(sorted(levels - _ALL_LEVELS))
        )
    return levels
