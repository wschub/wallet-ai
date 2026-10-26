# README

## Base de datos local (Postgres)

### Con docker compose
```bash
cd backend
docker compose up -d
export DATABASE_URL=postgresql+asyncpg://wallet:wallet@localhost:5432/wallet_ai_test
uv run alembic upgrade head
uv run python scripts/seed.py
```

### Con Postgres local
```bash
createdb wallet_ai_test
export DATABASE_URL=postgresql+asyncpg://$(whoami)@localhost:5432/wallet_ai_test
uv run alembic upgrade head
uv run python scripts/seed.py
```

Se usan dos bases para evitar mezclar datos:
- `wallet_ai_test`: para tests de integración (CI y local). Se recrea/migra con `alembic upgrade head` antes de `pytest`.
- `wallet_ai_dev`: para la demostración local. Configura `DATABASE_URL` apuntando a `wallet_ai_dev` y ejecuta `APP_ENV=dev uv run python -m scripts.seed` (solo permitido en `dev`). 

## Database roles

`DATABASE_URL` must point to the **owner** role: it is used by migrations, the seed and the integration tests. 
`wallet_app` is a NOLOGIN group role with least privilege (INSERT/SELECT on `audit_events`). Running the API with a restricted login role (`CREATE ROLE ... LOGIN IN ROLE wallet_app`) requires a separate runtime URL, which is planned and not implemented yet.

**Seed:** `scripts/seed.py` only runs when `APP_ENV=dev`. Demo passwords are defined in a constant marked **ONLY FOR DEMONSTRATION - DO NOT USE IN PRODUCTION** (`DEMO_PASSWORD`) and must never be used outside local/dev environments.

## Security model

Los triggers `BEFORE UPDATE/DELETE/TRUNCATE` en `audit_events` fuerzan el modo append-only, pero **un superusuario puede desactivarlos**. Por ello la aplicación debe conectarse con un rol sin ese privilegio (`wallet_app`), y esa garantía se verifica con `SET ROLE wallet_app` en los tests.

The output guard that rewrites claims of completed transfers is a heuristic safety net. The guarantee that chat cannot move money is structural: the assistant has no tool or import path to the confirmation use case.

### Recipient handles

Recipient handles are opaque, tied to the requesting user and valid for 15 minutes. They are reusable within that window (the table is append-only, so there is no consumed state). Every draft still requires a separate human confirmation through `POST /drafts/{id}/confirm`, which the assistant and the MCP server cannot reach.

Dev databases created before migration 0008 may contain legacy trace ids; recreate them.

#### Notas PR

Tokens issued by /auth/login last 1 hour by default; use a shorter lifetime for MCP tokens.

issue_token(ttl) debe ser igual a 60 minutos