# Quickstart: Wallet Simulado con Asistente Financiero

## Requisitos
- Python 3.12+
- Docker / Docker Compose
- `uv`

## Setup (desde backend/)
```bash
cd backend
uv sync
```

## Base de datos (dev, desde backend/)
```bash
cd backend
docker compose up -d
uv run alembic upgrade head
uv run python -m scripts.seed  # usuarios sintéticos: cliente, soporte, compliance
```

## Variables de entorno (ejemplo, sin llaves, desde backend/)
```env
APP_ENV=dev
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/wallet_ai
LLM_PROVIDER=fake
LLM_MODEL=fake
EMBEDDING_PROVIDER=fake
EMBEDDING_MODEL=fake-emb
RERANKER_PROVIDER=fake
AUTH_JWT_SECRET=dev-secret
```

## Ejecutar API (desde backend/)
```bash
cd backend
uv run uvicorn wallet_ai.interfaces.api.main:app --reload
```

## Ejecutar MCP (interfaces/mcp, desde backend/)
```bash
cd backend
# Servidor MCP expuesto según SDK; herramientas: get_balance, list_transactions, find_recipient, draft_transfer
uv run python -m wallet_ai.interfaces.mcp.server  # ejemplo
```

## Tests (desde backend/)
```bash
cd backend
# Unit/integration/contract con proveedor simulado (CI)
uv run pytest tests/unit tests/integration tests/contract -q

# Suite adversarial
uv run pytest tests/adversarial -q
```

## Evaluación (fuera de CI, proveedor real/local, desde backend/)
```bash
cd backend
# Métricas de calidad con dataset dorado 30–50 preguntas
uv run python -m evals.run_quality --dataset evals/golden --out evals/reports/

# Comparación de estrategias (≥3 configuraciones)
uv run python -m evals.run_strategies --dataset evals/golden --out evals/reports/
```

## Verificaciones clave
- Confirmación: `POST /drafts/{id}/confirm` es endpoint separado (modelo/MCP no acceden). Expiración evaluada al confirmar.
- MCP: `get_balance`, `list_transactions`, `find_recipient`, `draft_transfer` (sin ejecutar/confirmar). Identidad del usuario sale de la sesión, nunca de argumentos.
- ACL: filtro por rol antes del ranking; `Chunk.access_level` denormalizado.
- PII: enmascarado antes del LLM, logs y auditoría. Datos de destinatario devueltos como identificador opaco y nombre enmascarado.
- Auditoría: append-only (incluye bloqueo de TRUNCATE), solo compliance consulta `/audit`.
- CI: usa exclusivamente proveedor `fake`, sin servicios externos.
- Idempotencia confirmación: clave = ID del borrador.
- Montos: `NUMERIC(18,2)`; API devuelve decimales como string.
- Pagos/transacciones: paginación por `limit` + `cursor` en listados relevantes.
