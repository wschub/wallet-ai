# Data Model: Wallet Simulado con Asistente Financiero

## Entidades principales

### Usuario
- `id` (UUID, PK)
- `email` (único)
- `role` (enum: `cliente` | `soporte` | `compliance`)
- `created_at` (timestamptz)

### Cuenta
- `id` (UUID, PK)
- `user_id` (FK -> Usuario)
- `currency` (texto, sintético)
- `created_at` (timestamptz)
- **Nota**: saldo derivado del ledger (no almacenado como columna mutable).

### LedgerTransaction (solo inserción)
- `id` (UUID, PK)
- `transaction_type` (enum: `TRANSFER` | `ADJUSTMENT` | `REVERSAL`)
- `idempotency_key` (texto, único por usuario/cuenta emisora) — clave de idempotencia de la operación de origen
- `from_user_id` (FK -> Usuario, nullable según tipo)
- `from_account_id` (FK -> Cuenta, nullable)
- `to_account_id` (FK -> Cuenta, nullable)
- `amount` (NUMERIC(18,2), positivo)
- `trace_id` (texto, FK -> Interacción.trace_id)
- `created_at` (timestamptz)
- **Reglas**: solo inserción (TRUNCATE bloqueado). Única por `(from_user_id, from_account_id, idempotency_key)` para evitar duplicados.

### LedgerEntry (solo inserción)
- `id` (UUID, PK)
- `transaction_id` (FK -> LedgerTransaction, obligatorio)
- `account_id` (FK -> Cuenta)
- `entry_type` (enum: `DEBIT` | `CREDIT`)
- `amount` (NUMERIC(18,2), positivo)
- `created_at` (timestamptz)
- **Reglas**: inmutable (trigger + permisos + TRUNCATE bloqueado). Cada `transaction_id` debe tener al menos un DEBIT y un CREDIT. Restricción por trigger diferido: suma de DEBIT = suma de CREDIT por `transaction_id`. Bloqueo de TRUNCATE sobre `ledger_entries`, `ledger_transactions` y `audit_events`.

### Transferencia
- `id` (UUID, PK)
- `from_account_id` (FK)
- `to_account_id` (FK)
- `amount` (NUMERIC(18,2))
- `status` (enum: `COMPLETED` | `FAILED`)
- `transaction_id` (FK -> LedgerTransaction)
- `draft_id` (UUID, FK -> Borrador de transferencia, único) — la clave de idempotencia de la transferencia confirmada es el id del borrador
- `confirmed_at` (timestamptz)
- `created_at` (timestamptz)
- **Reglas**: no se crea con estado `PENDING`; se crea únicamente al confirmar el borrador.

### Borrador de transferencia
- `id` (UUID, PK)
- `user_id` (FK -> Usuario)
- `from_account_id` (FK)
- `to_account_id` (FK) — referencia mediante identificador opaco del destinatario (ver contrato MCP)
- `amount` (NUMERIC(18,2))
- `status` (enum: `pendiente` | `confirmada` | `rechazada` | `expirada`)
- `expires_at` (timestamptz, +15 min)
- `confirmed_at` (timestamptz, nullable)
- `trace_id` (texto, FK a interacción)
- `created_at` (timestamptz)
- **Reglas**: confirmación vía `POST /drafts/{id}/confirm` (endpoint separado). Solo propietario. La expiración se evalúa al momento de confirmar. Confirmación única. Identidad del usuario siempre proviene de la sesión, nunca de argumentos.

### Documento de política
- `id` (UUID, PK)
- `title` (texto)
- `access_level` (enum: `publico` | `cliente` | `interno`)
- `content` (texto)
- `source` (texto/URI)
- `version` (texto)
- `created_at` (timestamptz)

### Chunk
- `id` (UUID, PK)
- `document_id` (FK -> Documento)
- `chunk_index` (int)
- `text` (texto)
- `embedding` (vector, pgvector)
- `embedding_model` (texto)  **obligatorio**
- `embedding_version` (texto) **obligatorio**
- `access_level` (enum: `publico` | `cliente` | `interno`) **denormalizado** (para aplicar filtro de ACL antes del ranking/recuperación)
- `created_at` (timestamptz)

### Interacción
- `id` (UUID, PK)
- `trace_id` (texto, único)
- `user_id` (FK -> Usuario)
- `channel` (texto)
- `created_at` (timestamptz)

### EventoAuditoría (solo inserción)
- `id` (UUID, PK)
- `trace_id` (texto, FK -> Interacción.trace_id)
- `user_id` (FK -> Usuario)
- `event_type` (enum: `retrieval` | `tool_call` | `guardrail_block` | `llm_call` | `response`)
- `payload` (JSONB, PII enmascarado)
- `reason` (texto, nullable)
- `created_at` (timestamptz)
- **Reglas**: inmutable (trigger + permisos + TRUNCATE bloqueado). Solo compliance consulta. Sin PII en claro.

### DatasetDorado
- `id` (UUID, PK)
- `name` (texto)
- `version` (texto)
- `created_at` (timestamptz)
- **Items**: `golden_items(id, dataset_id, question, expected_answer, relevant_doc_ids, ...)`

### EjecuciónEvaluación
- `id` (UUID, PK)
- `dataset_id` (FK)
- `run_type` (enum: `quality` | `strategy_comparison`)
- `config` (JSONB: segmentación, léxica/vectorial/híbrida, rerank true/false, ...)
- `provider` (texto)
- `model` (texto)
- `metrics` (JSONB: hit@k, mrr, faithfulness, citations_ok, etc.)
- `trace_id` (texto)
- `created_at` (timestamptz)
- **Reglas**: registro obligatorio por cambio de pipeline / comparación.

### InformeComparacionRecuperacion
- `id` (UUID, PK)
- `execution_ids` (UUID[])
- `summary` (JSONB)
- `report_path` (texto)  # backend/evals/reports/...
- `created_at` (timestamptz)
- **Reglas**: versionado, resultados comparables con mismas preguntas/criterios.
