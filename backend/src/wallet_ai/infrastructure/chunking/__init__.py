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


def build_chunker(strategy: str, size: int, overlap: int) -> Chunker:
    if strategy == "fijo":
        return FixedSizeChunker(size, overlap)
    if strategy == "estructural":
        return StructuralChunker(size, overlap)
    raise ValueError(
        f"estrategia de segmentacion desconocida: {strategy!r} (debe ser una de {STRATEGIES})"
    )
