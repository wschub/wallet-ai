"""Lectura del corpus de politicas (tramo A de US3, T049).

El corpus vive en `backend/data/corpus/*.md`: markdown en espanol con front
matter simple (`id`, `title`, `access_level`, `version`). Este modulo es
voluntariamente minimalista: solo stdlib, sin dependencias de YAML, para
que el contrato del corpus quede legible en un solo archivo.

`raw` conserva el archivo COMPLETO (front matter incluido): el
`content_sha256` de la fila `documents` se calcula sobre el, de modo que
cualquier cambio — incluso de titulo o de nivel — exige subir la version
para poder re-ingestar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from wallet_ai.application.rag.errors import CorpusError
from wallet_ai.domain.documents.documents import ACCESS_LEVELS

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class CorpusDocument:
    stable_id: str
    title: str
    access_level: str
    version: str
    content: str  # cuerpo sin front matter (es lo que se enmascara y segmenta)
    raw: str  # archivo completo; base del content_sha256
    source_path: str  # nombre del archivo dentro del corpus


def read_corpus(corpus_dir: Path) -> list[CorpusDocument]:
    if not corpus_dir.is_dir():
        raise CorpusError(f"el directorio del corpus no existe: {corpus_dir}")
    docs: list[CorpusDocument] = []
    for path in sorted(corpus_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, content = _split_front_matter(raw, path.name)
        doc = CorpusDocument(
            stable_id=meta["id"],
            title=meta["title"],
            access_level=meta["access_level"],
            version=meta["version"],
            content=content,
            raw=raw,
            source_path=path.name,
        )
        _validate(doc)
        docs.append(doc)
    if not docs:
        raise CorpusError(f"corpus vacio: {corpus_dir}")
    return docs


def _split_front_matter(raw: str, filename: str) -> tuple[dict[str, str], str]:
    lines = raw.split("\n")
    if not lines or lines[0].strip() != "---":
        raise CorpusError(f"{filename}: debe abrir con '---' de front matter")
    meta: dict[str, str] = {}
    idx = 1
    while idx < len(lines) and lines[idx].strip() != "---":
        key, sep, value = lines[idx].partition(":")
        if not sep:
            raise CorpusError(f"{filename}: linea de front matter sin formato 'clave: valor': {lines[idx]!r}")
        meta[key.strip()] = value.strip().strip("'\"")
        idx += 1
    if idx >= len(lines):
        raise CorpusError(f"{filename}: front matter sin cerrar")
    return meta, "\n".join(lines[idx + 1 :])


def _validate(doc: CorpusDocument) -> None:
    if not _ID_RE.match(doc.stable_id):
        raise CorpusError(f"{doc.source_path}: id invalido {doc.stable_id!r}")
    if doc.access_level not in ACCESS_LEVELS:
        raise CorpusError(
            f"{doc.source_path}: access_level invalido {doc.access_level!r} (debe ser uno de {ACCESS_LEVELS})"
        )
    if not doc.title.strip():
        raise CorpusError(f"{doc.source_path}: title vacio")
    if not doc.version.strip():
        raise CorpusError(f"{doc.source_path}: version vacia")
    if not doc.content.strip():
        raise CorpusError(f"{doc.source_path}: cuerpo vacio")
