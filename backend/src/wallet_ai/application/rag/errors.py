"""Errores del area RAG de application (tramo A de US3, T049)."""

from __future__ import annotations


class CorpusError(Exception):
    """El corpus no existe o un documento esta mal formado."""


class IngestConflictError(Exception):
    """La ingesta choca con estado persistido.

    Casos: reusar el nombre de una build con otra configuracion; contenido
    de un documento que cambio sin subir su version; fragmentos que difieren
    de los ya indexados (p. ej. cambio de segmentacion o de enmascarado); o
    una build que ya indexo otra version del mismo documento.

    El mensaje explica siempre como resolverlo (nuevo nombre de build o
    bump de version en el front matter).
    """
