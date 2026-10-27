"""Entidades del indice de politicas (tramo A de US3, T047).

`Document` y `Chunk` espejan las tablas `documents`/`chunks` de la
migracion 20261024_0009; `access_level` viaja denormalizado en el chunk
para poder filtrar por ACL antes del ranking sin tocar la tabla de
documentos (decision de research.md).

Los ids son UUIDs deterministicos derivados con uuid5: la misma version del
mismo documento produce SIEMPRE el mismo id. Eso hace a la ingesta
idempotente (re-ingestar no duplica) y permite que "nueva version = nueva
fila" sea solo insertar, como exige el esquema append-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

ACCESS_LEVELS = ("publico", "cliente", "interno")

# Namespace estable del indice: cambiarlo cambiaria todos los ids
# deterministicos, asi que NO se toca una vez creado un indice.
_INDEX_NAMESPACE = uuid5(NAMESPACE_URL, "wallet-ai:rag-index")


def _validate_access_level(value: str) -> None:
    if value not in ACCESS_LEVELS:
        raise ValueError(f"access_level invalido: {value!r} (debe ser uno de {ACCESS_LEVELS})")


def document_uuid(stable_id: str, version: str) -> UUID:
    """Id estable del documento: uuid5(stable_id, version).

    Misma version del mismo documento -> mismo id (idempotencia); version
    nueva -> id nuevo (nueva fila en `documents`).
    """
    return uuid5(_INDEX_NAMESPACE, f"document:{stable_id}:{version}")


def index_build_uuid(name: str) -> UUID:
    """Id estable de la build: uuid5(name). Reusar el nombre reusa la build."""
    return uuid5(_INDEX_NAMESPACE, f"index-build:{name}")


def chunk_uuid(build_id: UUID, document_id: UUID, chunk_index: int) -> UUID:
    """Id estable del chunk dentro de su build (clave de idempotencia)."""
    return uuid5(_INDEX_NAMESPACE, f"chunk:{build_id}:{document_id}:{chunk_index}")


@dataclass(frozen=True)
class Document:
    """Fila de `documents`: una version concreta de un documento del corpus."""

    id: UUID
    title: str
    access_level: str
    version: str
    content_sha256: str
    source_path: str

    def __post_init__(self) -> None:
        _validate_access_level(self.access_level)
        if not self.title.strip():
            raise ValueError("title no puede estar vacio")
        if not self.version.strip():
            raise ValueError("version no puede estar vacia")
        if not self.source_path.strip():
            raise ValueError("source_path no puede estar vacio")


@dataclass(frozen=True)
class Chunk:
    """Fila de `chunks`: un fragmento de documento dentro de una build.

    `access_level` esta denormalizado y debe coincidir con el del documento
    (lo exige un trigger en base). `embedding` viaja con el chunk para que
    el repositorio persista texto y vector en la misma insercion.
    """

    id: UUID
    index_build_id: UUID
    document_id: UUID
    chunk_index: int
    text: str
    access_level: str
    embedding: list[float]

    def __post_init__(self) -> None:
        _validate_access_level(self.access_level)
        if self.chunk_index < 0:
            raise ValueError("chunk_index no puede ser negativo")
        if not self.text.strip():
            raise ValueError("el texto del chunk no puede estar vacio")


@dataclass(frozen=True)
class IndexBuild:
    """Fila de `index_builds`: un indice completo de una estrategia.

    Varias builds coexisten para comparar estrategias de segmentacion sin
    mezclar chunks (cada chunk pertenece a exactamente una build).
    """

    id: UUID
    name: str
    chunking_strategy: str
    chunk_params: dict
    embedding_provider: str
    embedding_model: str
    embedding_version: str
    dimension: int

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name no puede estar vacio")
        if not self.chunking_strategy.strip():
            raise ValueError("chunking_strategy no puede estar vacia")
        if self.dimension <= 0:
            raise ValueError("dimension debe ser > 0")
