#!/usr/bin/env python3
"""Ingesta el corpus de politicas en el indice RAG (tramo A de US3, T049).

Solo corre con APP_ENV=dev (igual que seed.py): la ingesta escribe en las
tablas append-only del indice y no debe ejecutarse contra entornos reales.

Opciones:
  --strategy {fijo,estructural}  estrategia de segmentacion (default: fijo)
  --size N                        maximo por fragmento en caracteres
  --overlap N                     solapamiento en caracteres (< size)
  --min-size N                    umbral de union de secciones cortas del
                                  estructural (default: 120; la fija lo
                                  ignora)
  --name NOMBRE                   nombre de la build (default: generado de
                                  los parametros, para re-ingestas
                                  idempotentes con la misma config)

`chunk_params` de TODA build incluye la version del segmentador; las
estructurales incluyen ademas `min_size`. Esos valores son parte de la
identidad de la build: cambiar la segmentacion con el mismo nombre falla
con IngestConflictError, nunca mezcla dos segmentaciones.

El nombre de la build es la clave de idempotencia: re-ejecutar con el
mismo nombre y el mismo corpus no duplica nada; si el corpus o los
parametros cambiaron, la ingesta falla pidiendo otro nombre (o bump de
version en los documentos afectados).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
CORPUS_DIR = BACKEND_DIR / "data" / "corpus"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingresa el corpus de politicas al indice RAG (solo dev).")
    parser.add_argument("--strategy", choices=("fijo", "estructural"), default="fijo")
    parser.add_argument("--size", type=int, default=800, help="maximo de caracteres por fragmento")
    parser.add_argument("--overlap", type=int, default=120, help="solapamiento en caracteres (< size)")
    parser.add_argument(
        "--min-size",
        type=int,
        default=120,
        help="umbral de union de secciones cortas del estructural (default: 120)",
    )
    parser.add_argument("--name", default=None, help="nombre de la build (default: estrategia y parametros)")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    import asyncpg

    from wallet_ai.application.rag.ingest import IngestService
    from wallet_ai.infrastructure.chunking import build_chunker, chunk_params_for
    from wallet_ai.infrastructure.config.factory import build_providers
    from wallet_ai.infrastructure.config.settings import Settings
    from wallet_ai.infrastructure.db.rag_ingest_repository import RagIngestRepository
    from wallet_ai.infrastructure.pii.masker import PIIMasker

    settings = Settings()
    providers = build_providers(settings)
    embeddings = providers.get("embeddings")
    if embeddings is None:
        raise SystemExit(
            "este tramo solo soporta el proveedor fake de embeddings "
            "(EMBEDDING_PROVIDER=fake): los adaptadores reales llegan con el tramo de providers"
        )

    name = args.name or f"{args.strategy}-s{args.size}-o{args.overlap}"
    chunker = build_chunker(args.strategy, args.size, args.overlap, min_size=args.min_size)
    pool = await asyncpg.create_pool(settings.asyncpg_dsn, min_size=1, max_size=2)
    try:
        service = IngestService(
            repo=RagIngestRepository(pool),
            embeddings=embeddings,
            pii=PIIMasker(),
            chunker=chunker,
            strategy=args.strategy,
            chunk_params=chunk_params_for(
                args.strategy, args.size, args.overlap, args.min_size, chunker
            ),
            build_name=name,
            embedding_provider=settings.EMBEDDING_PROVIDER,
            embedding_model=settings.EMBEDDING_MODEL,
            embedding_version=settings.EMBEDDING_VERSION,
        )
        result = await service.ingest(CORPUS_DIR)
    finally:
        await pool.close()

    print(
        f"build '{result.build_name}' ({result.build_id}): "
        f"{result.documents} documentos, {result.chunks} chunks, "
        f"dimension {result.dimension}, "
        f"insertados {result.inserted_documents} docs / {result.inserted_chunks} chunks"
        + (" (ya estaba al dia)" if result.already_present else "")
    )


def main() -> None:
    if os.environ.get("APP_ENV") != "dev":
        raise SystemExit("ingest.py may only run in dev (APP_ENV=dev)")
    args = _parse_args()
    if args.size <= 0:
        raise SystemExit("--size debe ser > 0")
    if args.min_size < 0:
        raise SystemExit("--min-size debe ser >= 0")
    if not 0 <= args.overlap < args.size:
        raise SystemExit("--overlap debe ser >= 0 y menor que --size")
    asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
