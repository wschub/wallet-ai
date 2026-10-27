"""Segmentador estructural por encabezados markdown (tramo A de US3, T049).

Cada encabezado ATX (`#`..`######`) abre una seccion que termina en el
siguiente encabezado de cualquier nivel; el texto previo al primer
encabezado (preambulo) es su propia seccion.

Una seccion que cabe en `size` es un unico fragmento (el encabezado viaja
con su seccion). Una seccion que excede el maximo se respalda con el
segmentador de tamano fijo (`FixedSizeChunker` con el mismo `size` y
`overlap`), que garantiza el maximo y no parte palabras ni bloques de
codigo.

Determinista: misma entrada, misma salida.
"""

from __future__ import annotations

import re

from wallet_ai.infrastructure.chunking.fixed import FixedSizeChunker

_HEADER_RE = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")


class StructuralChunker:
    def __init__(self, size: int, overlap: int) -> None:
        if size <= 0:
            raise ValueError("size debe ser > 0")
        if overlap < 0:
            raise ValueError("overlap debe ser >= 0")
        if overlap >= size:
            raise ValueError("overlap debe ser < size")
        self.size = size
        self.overlap = overlap
        self._fallback = FixedSizeChunker(size, overlap)

    def chunk(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []
        chunks: list[str] = []
        for section in self._sections(text):
            if len(section) <= self.size:
                chunks.append(section)
            else:
                chunks.extend(self._fallback.chunk(section))
        return chunks

    @staticmethod
    def _sections(text: str) -> list[str]:
        sections: list[list[str]] = []
        current: list[str] | None = None
        for line in text.split("\n"):
            if _HEADER_RE.match(line):
                if current is not None and any(line_.strip() for line_ in current):
                    sections.append("\n".join(current).strip())
                current = [line]
            elif current is None:
                current = [line]
            else:
                current.append(line)
        if current is not None and any(line_.strip() for line_ in current):
            sections.append("\n".join(current).strip())
        return sections
