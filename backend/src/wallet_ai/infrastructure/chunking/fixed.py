"""Segmentador de tamano fijo con solapamiento (tramo A de US3, T049).

Reglas:

- Corta SIEMPRE en limite de palabra: la posicion de corte es un caracter
  de espacio en el original (el fragmento nunca empieza ni termina a
  mitad de una palabra).
- Nunca corta dentro de un bloque de codigo cercado: las posiciones de
  corte solo existen con los cercados balanceados a la izquierda. Un
  bloque que cabe en `size` sale entero en un fragmento.
- El solapamiento se ajusta al limite de palabra mas cercano por debajo
  del maximo, de modo que el solapamiento real nunca supera `overlap`.
- Ultimo recurso documentado: un token (o una region sin espacios) mas
  largo que `size` se corta exactamente a `size`. Preferimos romper el
  token antes que romper el maximo: el techo es el limite del backend de
  embeddings.
- Determinista: misma entrada, misma salida. Sin azar ni reloj.
"""

from __future__ import annotations

from bisect import bisect_right

FENCE = "```"


class FixedSizeChunker:
    def __init__(self, size: int, overlap: int) -> None:
        if size <= 0:
            raise ValueError("size debe ser > 0")
        if overlap < 0:
            raise ValueError("overlap debe ser >= 0")
        if overlap >= size:
            raise ValueError("overlap debe ser < size")
        self.size = size
        self.overlap = overlap

    def chunk(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []
        bounds = self._safe_boundaries(text)
        n = len(text)
        chunks: list[str] = []
        start = 0
        while start < n:
            if not text[start:].strip():
                break  # el resto es solo espacios: sin fragmentos vacios
            limit = min(start + self.size, n)
            end = self._last_boundary_within(bounds, start, limit)
            hard = end is None
            if hard:
                # Ultimo recurso: region sin limite seguro en la ventana.
                end = limit
            piece = text[start:end].strip()
            if piece:
                chunks.append(piece)
            if end >= n:
                break
            if hard:
                nxt = max(end - self.overlap, start + 1)
            else:
                nxt = self._last_boundary_between(bounds, end - self.overlap, end)
                if nxt is None:
                    nxt = end
                nxt = max(nxt, start + 1)
            start = nxt
        return chunks

    # ------------------------------------------------------------------
    # Posiciones seguras de corte
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_boundaries(text: str) -> list[int]:
        """Posiciones `p` donde `text[:p]` termina en limite de palabra con
        los bloques de codigo cerrados (cercados balanceados)."""
        bounds: set[int] = set()
        fence_open = False
        pos = 0
        n = len(text)
        for line in text.split("\n"):
            line_len = len(line)
            if line.lstrip().startswith(FENCE):
                fence_open = not fence_open
                if not fence_open:
                    # La linea cierra el bloque: cortar justo despues es seguro.
                    bounds.add(pos + line_len + 1)
            elif not fence_open:
                for j, ch in enumerate(line):
                    if ch.isspace() and j + 1 < line_len:
                        bounds.add(pos + j + 1)
                # Inicio de la siguiente linea (cortar antes de un cercado
                # que abre es seguro: el bloque queda entero a la derecha).
                bounds.add(pos + line_len + 1)
            pos += line_len + 1
        # Fin del texto: aunque queden cercas abiertas (entrada malformada)
        # el algoritmo debe poder terminar; el corte a medias de esa region
        # cae en el ultimo recurso documentado.
        bounds.add(n)
        return sorted(b for b in bounds if 0 < b <= n)

    @staticmethod
    def _last_boundary_within(bounds: list[int], start: int, limit: int) -> int | None:
        """Mayor limite seguro `p` con `start < p <= limit`."""
        i = bisect_right(bounds, limit) - 1
        j = bisect_right(bounds, start) - 1  # ultimo indice con bounds[j] <= start
        if j + 1 > i:
            return None
        return bounds[i]

    @staticmethod
    def _last_boundary_between(bounds: list[int], low: int, high: int) -> int | None:
        """Mayor limite seguro `p` con `low <= p < high`."""
        i = bisect_right(bounds, high - 1) - 1
        if i < 0 or bounds[i] < low:
            return None
        return bounds[i]
