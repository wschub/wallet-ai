# Implementation Plan: Wallet Simulado con Asistente Financiero

**Branch**: `001-wallet-financial-assistant` | **Date**: 2026-10-20 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-wallet-financial-assistant/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command; its definition describes the execution workflow.

## Summary

Wallet simulado con asistente financiero que aplica arquitectura hexagonal, proveedor fake determinista para CI/demo, ledger de doble entrada inmutable e idempotente, RAG con ACL por rol y citas, guardrails (prompt injection/exfiltración/PII), trazabilidad con trace_id y auditoría append-only, y borradores con confirmación humana explícita fuera del canal del modelo. Evaluación obligatoria con dataset dorado y suite adversarial; comparación de estrategias de recuperación con informes versionados.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI (async), Uvicorn, asyncpg, httpx, Pydantic v2, Alembic, pgvector, PyJWT, sse-starlette (o equivalente para SSE), mcp (SDK de MCP). Evaluación: RAGAS o equivalente; utilidades propias para métricas hit@k/MRR.

**Storage**: PostgreSQL con extensión pgvector. Tablas de solo inserción (ledger, auditoría). Migraciones con Alembic.

**Testing**: pytest + pytest-asyncio, ruff (lint/format). Suite adversarial en `backend/tests/adversarial/`. Dataset dorado versionado. CI solo con proveedor simulado.

**Target Platform**: Linux server (dev con docker-compose; opcional Cloud Run).

**Project Type**: Web service backend + (opcional) frontend mínimo.

**Performance Goals**: Orientado a portafolio/demo; latencias razonables para un asistente conversacional con streaming SSE. Sin objetivos agresivos de escala.

**Constraints**: Arquitectura hexagonal estricta (dominio sin deps de infraestructura). Sin LangChain/LangGraph. Sin llaves en repo. Confirmación fuera del canal del modelo (endpoint separado). MCP sin herramientas de ejecución/confirmación. ACL por documento aplicado antes del ranking. PII enmascarado antes del LLM/logs/auditoría. Solo datos sintéticos.

**Scale/Scope**: Feature acotada a wallet simulado + asistente + RAG + guardrails + auditoría. 3 roles (cliente/soporte/compliance), ~30–50 preguntas doradas, suite adversarial versionada.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principio | Estado | Notas |
|---|---|---|
| I. Arquitectura Hexagonal | PASS | Layout `backend/src/wallet_ai/{domain,application,infrastructure,interfaces}`; puertos en dominio, adaptadores en infraestructura. |
| II. Offline-First / Fake Determinista | PASS | Proveedor fake determinista; CI solo con fake; ninguna llave en repo. |
| III. Stack Asíncrono Extremo a Extremo | PASS | FastAPI async, asyncpg, httpx, streaming por SSE. |
| IV. Trazabilidad Total | PASS | `trace_id` por petición con contextvars; auditoría persistida. |
| V. Enmascaramiento de PII (NON-NEGOTIABLE) | PASS | Componente propio tras puerto, aplicado antes del LLM, sobre recuperado, logs y auditoría. |
| VI. Confirmación Humana para Movimientos | PASS | Borradores + endpoint separado `POST /drafts/{id}/confirm`; modelo sin acceso; validación de propiedad. |
| VII. Evaluación RAG con Dataset Dorado | PASS | Métricas + proveedor/modelo registrados; comparación de estrategias con informe versionado. |

**Gate result:** PASS (sin violaciones detectadas).

## Project Structure

### Documentation (this feature)

```text
specs/001-wallet-financial-assistant/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
└── tasks.md
```

### Source Code (repository root)

```text
backend/
├── pyproject.toml
├── alembic.ini
├── docker-compose.yml
├── src/
│   └── wallet_ai/
│       ├── domain/
│       │   ├── accounts/
│       │   ├── ledger/
│       │   ├── transfers/
│       │   ├── documents/
│       │   ├── assistant/
│       │   ├── audit/
│       │   └── ports/
│       ├── application/
│       │   ├── accounts/
│       │   ├── ledger/
│       │   ├── transfers/
│       │   ├── rag/
│       │   ├── assistant/
│       │   └── audit/
│       ├── infrastructure/
│       │   ├── db/
│       │   ├── providers/
│       │   ├── pii/
│       │   ├── guardrails/
│       │   └── tracing/
│       └── interfaces/
│           ├── api/
│           ├── deps/
│           ├── schemas/
│           └── mcp/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── adversarial/
└── evals/
    ├── golden/
    └── reports/

frontend/

.github/workflows/ci.yml
```

**Structure Decision**: Backend en `backend/` con layout hexagonal estricto (`domain` sin deps de `infrastructure`). Puertos en `domain/ports`; adaptadores en `infrastructure/providers` y otros módulos de infraestructura. Interfaces FastAPI en `interfaces/api` (deps/schemas); servidor MCP en `interfaces/mcp` (adaptador de entrada), sin herramientas de ejecución o confirmación. Evaluación en `backend/evals/` (golden/reports), migraciones Alembic en `backend/`, CI solo con proveedor fake.

## Delivery Order

1. **Fundacional**: setup del proyecto (uv, backend layout), CI con proveedor fake, `trace_id` con contextvars + logging estructurado, enmascarado de PII tras puerto, puertos y adaptador fake, auditoría append-only (solo inserción + permisos), auth JWT con usuarios sintéticos (cliente/soporte/compliance) y roles.
2. **US1**: Ledger de doble entrada (solo inserción), inmutabilidad en PostgreSQL (triggers/permisos), idempotencia con clave única, bloqueo de filas en orden consistente, saldo derivado, transferencias P2P.
3. **US2**: Asistente + borradores (estados pendiente/confirmada/rechazada/expirada, expiración 15 min). Endpoint separado `POST /drafts/{id}/confirm` con validación de propiedad; el modelo/MCP no acceden a él. MCP en `interfaces/mcp` con herramientas de solo lectura (`get_balance`, `list_transactions`) y `draft_transfer` (sin ejecutar/confirmar).
4. **US3**: RAG con documentos ACL (público/cliente/interno), filtro por rol **antes** del ranking. Recuperación híbrida PostgreSQL texto + pgvector con fusión RRF. Re-ranking tras puerto (adaptador local opcional como dependencia extra + fake).
5. **US4**: Guardrails (reglas deterministas + verificación opcional con LLM), PII aplicado a recuperado/logs/auditoría, suite adversarial versionada.
6. **US5**: Consulta de auditoría (solo compliance). Trazabilidad completa ligada a `trace_id`.
7. **Comparación de estrategias e informe**: evaluar segmentación (fijo/estructural), léxica/vectorial/híbrida, con/sin re-ranking, generar informe versionado en `backend/evals/reports/` con configuración, proveedor y modelo.
8. **SSE, deploy opcional y frontend mínimo**: respuestas del asistente por SSE, docker-compose, opcional Cloud Run + Cloud SQL, frontend mínimo.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| N/A | — | — |

## Constitution Check (Post-Design)

*Re-evaluado tras Phase 1 (data-model, contracts, quickstart).*

| Principio | Estado | Notas |
|---|---|---|
| I. Arquitectura Hexagonal | PASS | Dominio sin deps de infraestructura; puertos en dominio, adaptadores en infraestructura; MCP en `interfaces/mcp` como adaptador de entrada. |
| II. Offline-First / Fake Determinista | PASS | Proveedor fake determinista; CI solo con fake; ninguna llave en repo. |
| III. Stack Asíncrono Extremo a Extremo | PASS | FastAPI async, asyncpg, httpx, streaming por SSE. |
| IV. Trazabilidad Total | PASS | `trace_id` por petición con contextvars; auditoría persistida en tabla append-only. |
| V. Enmascaramiento de PII (NON-NEGOTIABLE) | PASS | Componente propio tras puerto, aplicado antes del LLM, sobre recuperado, logs y auditoría. |
| VI. Confirmación Humana para Movimientos | PASS | Borradores + endpoint separado `POST /drafts/{id}/confirm`; modelo/MCP sin acceso; validación de propiedad. |
| VII. Evaluación RAG con Dataset Dorado | PASS | Métricas hit@k/MRR + fidelidad (RAGAS con juez configurable), proveedor/modelo registrados; comparación de estrategias con informe versionado. |

**Gate result (Post-Design):** PASS (sin violaciones detectadas).
