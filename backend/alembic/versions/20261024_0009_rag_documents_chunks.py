"""rag index: index_builds, documents, chunks

Revision ID: 20261024_0009
Revises: 20261024_0008
Create Date: 2026-10-24 12:00:00.000000

Tramo A de US3: esquema e ingesta del indice de politicas (corpus, builds,
documentos y chunks con embeddings). Decisiones de research.md aplicadas:

- `embedding vector` SIN dimension fija: se prefiere la busqueda exacta
  (la comparacion de estrategias del tramo EVAL decidira si hace falta un
  HNSW con dimension acotada). Sin typmod no puede crearse un indice
  HNSW, y eso es intencional en este tramo.
- `access_level` denormalizado en `chunks` para filtrar por ACL ANTES del
  ranking sin unir con `documents` en cada consulta.

Reglas en la base:

- `index_builds`: una build es un indice completo (nombre unico); varias
  coexisten para comparar estrategias de segmentacion.
- `documents`: solo insercion. `id` es un UUID determinista calculado por
  la ingesta (uuid5 del id estable del corpus + version), de modo que la
  misma version del mismo documento siempre produce la misma fila y una
  version nueva produce una fila nueva.
- `chunks`: solo insercion, `tsv` tsvector GENERADO con configuracion
  'spanish', `UNIQUE (index_build_id, document_id, chunk_index)` como
  clave de idempotencia de la ingesta.
- Trigger BEFORE INSERT en `chunks`: exige `access_level` igual al del
  documento (y, de paso, que el documento exista).
- Triggers BEFORE UPDATE / DELETE (fila) y BEFORE TRUNCATE (sentencia)
  con mensaje estable 'append-only' en las TRES tablas.
- GIN sobre `tsv` y BTREE sobre `(index_build_id, access_level)`.

Permisos (documentacion del modelo de roles):

- `wallet_app` (rol de la aplicacion en runtime): SOLO SELECT en las tres
  tablas. Nunca inserta documentos ni chunks: el asistente solo lee.
- `wallet_ingest` (NOLOGIN, creado aqui): SELECT + INSERT en las tres
  tablas. Es el rol con el que debe correr la ingesta del corpus cuando
  se ejecute con un login restringido; en dev el script corre como owner
  de la base. Se crea por la misma razon que `wallet_app` en 0002: poder
  verificar con `SET ROLE` que los privilegios son los minimos.

La extension `vector` se crea con IF NOT EXISTS y NO se desinstala en el
downgrade: es un recurso compartido del cluster y otras bases pueden
depender de ella.
"""

from alembic import op

revision = "20261024_0009"
down_revision = "20261024_0008"
branch_labels = None
depends_on = None

TABLES = ("chunks", "documents", "index_builds")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------
    # index_builds: metadatos de un indice completo
    # ------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS index_builds (
        id UUID PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        chunking_strategy TEXT NOT NULL,
        chunk_params JSONB NOT NULL DEFAULT '{}'::jsonb,
        embedding_provider TEXT NOT NULL,
        embedding_model TEXT NOT NULL,
        embedding_version TEXT NOT NULL,
        dimension INTEGER NOT NULL CHECK (dimension > 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)

    # ------------------------------------------------------------------
    # documents: solo insercion, nueva version = nueva fila
    # ------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        id UUID PRIMARY KEY,
        title TEXT NOT NULL,
        access_level TEXT NOT NULL
            CHECK (access_level IN ('publico','cliente','interno')),
        version TEXT NOT NULL,
        content_sha256 TEXT NOT NULL
            CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
        source_path TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)

    # ------------------------------------------------------------------
    # chunks: texto enmascarado, embedding y tsv generado en espanol
    # ------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS chunks (
        id UUID PRIMARY KEY,
        index_build_id UUID NOT NULL REFERENCES index_builds(id),
        document_id UUID NOT NULL REFERENCES documents(id),
        chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
        text TEXT NOT NULL CHECK (length(text) > 0),
        access_level TEXT NOT NULL
            CHECK (access_level IN ('publico','cliente','interno')),
        embedding vector NOT NULL,
        tsv tsvector GENERATED ALWAYS AS (to_tsvector('spanish', text)) STORED,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (index_build_id, document_id, chunk_index)
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS chunks_build_access_idx ON chunks (index_build_id, access_level)"
    )

    # ------------------------------------------------------------------
    # ACL denormalizado: chunks.access_level == documents.access_level
    # ------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION chunks_assert_access_level_matches_document()
    RETURNS TRIGGER AS $$
    DECLARE
        v_level TEXT;
    BEGIN
        SELECT d.access_level INTO v_level
        FROM documents d
        WHERE d.id = NEW.document_id;
        IF v_level IS DISTINCT FROM NEW.access_level THEN
            RAISE EXCEPTION
                'chunk access_level % does not match document access_level %',
                NEW.access_level, COALESCE(v_level, '(documento inexistente)');
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS chunks_access_level_matches_document ON chunks")
    op.execute(
        "CREATE TRIGGER chunks_access_level_matches_document BEFORE INSERT ON chunks "
        "FOR EACH ROW EXECUTE FUNCTION chunks_assert_access_level_matches_document()"
    )

    # ------------------------------------------------------------------
    # Append-only: UPDATE / DELETE / TRUNCATE bloqueados en las tres tablas
    # ------------------------------------------------------------------
    for table in TABLES:
        op.execute(
            f"""
        CREATE OR REPLACE FUNCTION {table}_block_update()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION '{table} is append-only: UPDATE not allowed';
        END;
        $$ LANGUAGE plpgsql;
        """
        )
        op.execute(f"DROP TRIGGER IF EXISTS {table}_block_update ON {table}")
        op.execute(
            f"CREATE TRIGGER {table}_block_update BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {table}_block_update()"
        )
        op.execute(
            f"""
        CREATE OR REPLACE FUNCTION {table}_block_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION '{table} is append-only: DELETE not allowed';
        END;
        $$ LANGUAGE plpgsql;
        """
        )
        op.execute(f"DROP TRIGGER IF EXISTS {table}_block_delete ON {table}")
        op.execute(
            f"CREATE TRIGGER {table}_block_delete BEFORE DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {table}_block_delete()"
        )
        op.execute(
            f"""
        CREATE OR REPLACE FUNCTION {table}_block_truncate()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION '{table} is append-only: TRUNCATE not allowed';
        END;
        $$ LANGUAGE plpgsql;
        """
        )
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {table}_block_truncate()"
        )

    # ------------------------------------------------------------------
    # Permisos: wallet_app solo lectura; wallet_ingest inserta
    # ------------------------------------------------------------------
    op.execute(
        "DO $$ BEGIN CREATE ROLE wallet_ingest NOLOGIN; "
        "EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )
    for table in TABLES:
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
        op.execute(f"GRANT SELECT ON TABLE {table} TO wallet_app")
        op.execute(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE {table} FROM wallet_app")
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table} TO wallet_ingest")
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON TABLE {table} FROM wallet_ingest")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"REVOKE ALL ON TABLE {table} FROM wallet_ingest")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM wallet_app")
    op.execute(
        "DO $$ BEGIN DROP ROLE wallet_ingest; "
        "EXCEPTION WHEN dependent_objects_still_exist THEN null; WHEN others THEN null; END $$;"
    )

    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_block_truncate()")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_block_delete ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_block_delete()")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_block_update ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_block_update()")

    op.execute("DROP TRIGGER IF EXISTS chunks_access_level_matches_document ON chunks")
    op.execute("DROP FUNCTION IF EXISTS chunks_assert_access_level_matches_document()")
    op.execute("DROP INDEX IF EXISTS chunks_build_access_idx")
    op.execute("DROP INDEX IF EXISTS chunks_tsv_idx")
    # La extension vector NO se desinstala (recurso compartido del cluster).
    op.execute("DROP TABLE IF EXISTS chunks")
    op.execute("DROP TABLE IF EXISTS documents")
    op.execute("DROP TABLE IF EXISTS index_builds")
