"""Segmentador estructural por encabezados markdown (tramo A de US3, T049).

Cada encabezado ATX (`#`..`######`) abre una seccion que termina en el
siguiente encabezado de cualquier nivel; el texto previo al primer
encabezado (preambulo) es su propia seccion.

Version 2 (`CHUNKER_VERSION = "2"`), con umbral `min_size` (por defecto
120) para evitar fragmentos demasiado pequenos para el retrieval:

- Las secciones de menos de `min_size` caracteres se unen a la SIGUIENTE;
  si la union sigue corta, sigue acumulando secciones siguientes. El
  preambulo (texto previo al primer encabezado) se une SIEMPRE a la
  seccion del titulo H1, sea corto o no: el titulo viaja en el primer
  fragmento. La ULTIMA seccion corta, sin siguiente, se une a la
  anterior. Asi ningun fragmento queda por debajo del umbral, salvo que
  el documento completo sea mas corto.
- `min_size=0` desactiva la union (comportamiento de la version 1).

El maximo NUNCA se rompe por la union: cada unidad (seccion o union de
secciones) que cabe en `size` es un unico fragmento; la que lo excede se
respalda con el segmentador de tamanho fijo (`FixedSizeChunker` con el
mismo `size` y `overlap`), que garantiza el maximo y no parte palabras ni
bloques de codigo.

La union ocurre siempre DENTRO del texto de un documento (la ingesta
segmenta cada documento por separado) y conserva el orden: el titulo
viaja en el primer fragmento.

Determinista: misma entrada, misma salida.
"""

from __future__ import annotations

import re

from wallet_ai.infrastructure.chunking.fixed import FixedSizeChunker

_HEADER_RE = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")


class StructuralChunker:
    # Version del algoritmo: las builds estructurales la registran en
    # `chunk_params` (junto con `min_size`) para que cambiar la
    # segmentacion exija otro nombre de build (IngestConflictError).
    CHUNKER_VERSION = "2"

    def __init__(self, size: int, overlap: int, min_size: int = 120) -> None:
        if size <= 0:
            raise ValueError("size debe ser > 0")
        if overlap < 0:
            raise ValueError("overlap debe ser >= 0")
        if overlap >= size:
            raise ValueError("overlap debe ser < size")
        if min_size < 0:
            raise ValueError("min_size debe ser >= 0")
        self.size = size
        self.overlap = overlap
        self.min_size = min_size
        self._fallback = FixedSizeChunker(size, overlap)

    def chunk(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []
        chunks: list[str] = []
        for unit in self._merged_units(self._sections(text)):
            if len(unit) <= self.size:
                chunks.append(unit)
            else:
                chunks.extend(self._fallback.chunk(unit))
        return chunks

    def _merged_units(self, sections: list[str]) -> list[str]:
        """Une las secciones cortas (< min_size) a la SIGUIENTE; la ultima
        corta, sin siguiente, se une a la ANTERIOR. El preambulo (si lo hay)
        se une SIEMPRE a la seccion del titulo H1."""
        if self.min_size == 0:
            return sections
        if len(sections) >= 2 and not _HEADER_RE.match(sections[0]):
            sections = [f"{sections[0]}\n\n{sections[1]}", *sections[2:]]
        units: list[str] = []
        acc: str | None = None
        for section in sections:
            if acc is None:
                acc = section
            elif len(acc) < self.min_size:
                acc = f"{acc}\n\n{section}"
            else:
                units.append(acc)
                acc = section
        if acc is not None:
            units.append(acc)
        if len(units) >= 2 and len(units[-1]) < self.min_size:
            units[-2] = f"{units[-2]}\n\n{units[-1]}"
            units.pop()
        return units

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
