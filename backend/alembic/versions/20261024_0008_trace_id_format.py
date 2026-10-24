"""trace_id format

Revision ID: 20261024_0008
Revises: 20261022_0007
Create Date: 2026-10-24 00:00:00.000000

Endurece en la base el invariante "el trace_id lo genera SIEMPRE el
servidor" (`uuid4().hex`: 32 caracteres hex minusculas). El middleware ya
ignora el `X-Trace-Id` de la peticion como identificador; con este CHECK,
si un bug futuro vuelve a guardar un valor de cliente (corto, de 500
caracteres, con saltos de linea), la insercion falla.

`audit_events.trace_id` y `transfer_drafts.trace_id` referencian
`interactions(trace_id)`, por lo que heredan el formato via FK.

El `$` de las regex POSIX de Postgres solo casa al final real de la cadena
(a diferencia de Python, no casa antes de un salto de linea final).

El CHECK se anade NOT VALID a proposito: las filas existentes de otros
entornos (p.ej. bases de desarrollo usadas con el middleware antiguo que
hacia echo del header del cliente) pueden contener valores invalidos que no
se pueden borrar porque `audit_events` es append-only, y `alembic upgrade
head` no debe fallar por ellas. NOT VALID no valida las filas existentes,
pero si aplica a toda escritura nueva, que es lo que importa en tablas de
insercion.
"""

from alembic import op

revision = "20261024_0008"
down_revision = "20261022_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE interactions ADD CONSTRAINT interactions_trace_id_format_check "
        "CHECK (trace_id ~ '^[0-9a-f]{32}$') NOT VALID"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE interactions DROP CONSTRAINT IF EXISTS interactions_trace_id_format_check"
    )
