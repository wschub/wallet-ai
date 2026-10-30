"""Fabrica de segmentadores (tramo A de US3, T049).

Estrategias soportadas:

- `fijo`: tamano fijo en caracteres con solapamiento.
- `estructural`: por encabezados markdown con respaldo por tamano.
"""

from __future__ import annotations

from wallet_ai.domain.ports.chunker import Chunker
from wallet_ai.infrastructure.chunking.fixed import FixedSizeChunker
from wallet_ai.infrastructure.chunking.structural import StructuralChunker

STRATEGIES = ("fijo", "estructural")


def build_chunker(strategy: str, size: int, overlap: int, min_size: int = 120) -> Chunker:
    """Construye el segmentador de la estrategia pedida.

    `min_size` es el umbral de union de secciones cortas del estructural
    (default 120); la fija lo acepta y lo ignora para que los llamadores no
    tengan que ramificar por estrategia.
    """
    if strategy == "fijo":
        return FixedSizeChunker(size, overlap)
    if strategy == "estructural":
        return StructuralChunker(size, overlap, min_size=min_size)
    raise ValueError(
        f"estrategia de segmentacion desconocida: {strategy!r} (debe ser una de {STRATEGIES})"
    )


def chunk_params_for(
    strategy: str, size: int, overlap: int, min_size: int, chunker: Chunker
) -> dict:
    """Parametros que identifican la segmentacion de una build.

    TODA build registra la version de su segmentador; las estructurales
    registran ademas `min_size`. `IngestService` compara `chunk_params`
    completos: cambiar cualquiera de estos valores con el mismo nombre de
    build lanza `IngestConflictError` en vez de mezclar segmentaciones.
    """
    params = {
        "size": size,
        "overlap": overlap,
        "chunker_version": chunker.chunker_version,
    }
    if strategy == "estructural":
        params["min_size"] = min_size
    return params
