# Research: Wallet Simulado con Asistente Financiero

## 1) Streaming SSE
- **Decision**: `sse-starlette`. `trace_id` en header `X-Trace-Id` y en primer evento SSE.

## 2) SDK MCP
- **Decision**: `mcp` en `interfaces/mcp`. Herramientas solo lectura + `find_recipient` + `draft_transfer`. Sin confirmar/ejecutar. Identidad del usuario desde sesión.

## 3) RecipientHandle (opaco)
- **Decision**: `find_recipient` emite `recipient_id` (handle) ligado a `requester_user_id`, con `to_account_id` resuelto internamente, `expires_at` +15 min. `GET /recipients` compartido. `draft_transfer` y `POST /drafts` solo aceptan `recipient_id`. Rechazar IDs de cuenta directos y handles ajenos/expirados/usados.

## 4) Índice pgvector / ACL
- **Decision**: HNSW por defecto. Filtro ACL antes del ranking con `Chunk.access_level` denormalizado. Preferir búsqueda exacta + filtro ACL; `iterative_scan` como alternativa documentada si filtro restrictivo.

## 5) RRF
- **Decision**: RRF k=60 tras aplicar ACL.

## 6) PII
- **Decision**: Componente propio tras puerto `PIIService`, aplicado antes de LLM/logs/auditoría.

## 7) Re-ranking
- **Decision**: Puerto `Reranker`, fake por defecto, adaptador local opcional como extra.

## 8) Juez de fidelidad
- **Decision**: Juez propio vía `LLMProvider` (puerto), con **esquema JSON estricto** validado. Ante salida inválida: **1 reintento** con corrección; si sigue inválida → `judge_status = judge_error`, **sin puntaje inventado**, métricas parciales/faltantes registradas. `judge_model` obligatorio en `EjecutiónEvaluación`. **Calibración manual ~10 ítems**. **RAGAS** solo como extra opcional (`eval`), fuera del runtime.

## 9) Inmutabilidad PostgreSQL
- **Decision**: `ledger_entries`, `ledger_transactions`, `audit_events` solo inserción. Triggers BEFORE UPDATE/DELETE y TRUNCATE bloqueados. Trigger diferido para suma DEBIT=CRÉDIT. Expiración de borradores evaluada al confirmar. Idempotencia por draft_id.
