"""Errores del area RAG de application (tramo A de US3, T049; tramo B: T050-T052)."""

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


class InvalidRetrievalQuery(Exception):
    """La consulta no se puede procesar: vacia o por encima del maximo.

    El maximo es 300 caracteres (rechazo explicito). El rechazo ocurre
    ANTES de enmascarar, de auditar y de calcular cualquier embedding.
    """


class UnknownIndexBuild(Exception):
    """La build de indice pedida no existe.

    El nombre de la build es la clave: el mensaje lo repite para que el
    operador sepa que corregir.
    """


class EmbeddingModelMismatch(Exception):
    """El proveedor de embeddings no coincide con el de la build.

    La build registro proveedor/modelo/version al ingestar; si el que se
    usa para la consulta difiere, los vectores NO son comparables y el
    resultado seria silenciosamente incorrecto. El error se lanza ANTES de
    ejecutar cualquier busqueda en el indice (error claro, sin consultar).
    """

