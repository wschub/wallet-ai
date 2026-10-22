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

## Database roles

`DATABASE_URL` must point to the **owner** role: it is used by migrations, the seed and the integration tests. 
`wallet_app` is a NOLOGIN group role with least privilege (INSERT/SELECT on `audit_events`). Running the API with a restricted login role (`CREATE ROLE ... LOGIN IN ROLE wallet_app`) requires a separate runtime URL, which is planned and not implemented yet.

## Security model

Los triggers `BEFORE UPDATE/DELETE/TRUNCATE` en `audit_events` fuerzan el modo append-only, pero **un superusuario puede desactivarlos**. Por ello la aplicación debe conectarse con un rol sin ese privilegio (`wallet_app`), y esa garantía se verifica con `SET ROLE wallet_app` en los tests.
