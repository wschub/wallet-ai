"""Puerto de segmentacion (tramo A de US3, T047/T049).

Contrato de las implementaciones (`infrastructure.chunking`):

- Deterministas: la misma entrada produce siempre la misma salida.
- Cortan en limite de palabra; nunca parten palabras ni bloques de codigo
  a medias, salvo el ultimo recurso documentado (un token mas largo que
  `size` se corta a `size` antes que violar el maximo).
- Ningun fragmento vacio y ninguno por encima del maximo configurado.
"""

from typing import Protocol


class Chunker(Protocol):
    # Version del algoritmo de segmentacion. Toda build la registra en
    # `chunk_params` (las estructurales, tambien `min_size`).
    chunker_version: str

    def chunk(self, text: str) -> list[str]: ...
