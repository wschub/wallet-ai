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
createdb wallet
export DATABASE_URL=postgresql+asyncpg://$(whoami)@localhost:5432/wallet_ai_test
uv run alembic upgrade head  # crea tablas, triggers y roles wallet_app/wallet_migrator
uv run python scripts/seed.py

# Crear rol de login para la aplicación (sin contraseña en repo)
export APP_DB_PASSWORD='changeme'
psql "$DATABASE_URL" -c "CREATE ROLE wallet_app_login LOGIN IN ROLE wallet_app PASSWORD '$APP_DB_PASSWORD';"
export DATABASE_URL=postgresql+asyncpg://wallet_app_login:${APP_DB_PASSWORD}@localhost:5432/wallet_ai_test
```

Las migraciones leen `DATABASE_URL` del entorno (obligatorio) y muestran el destino sin contraseña.
