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

### RecipientHandle
- `id` (UUID, PK)
- `handle` (texto, único) — identificador opaco emitido por `find_recipient` y usado en `draft_transfer`
- `requester_user_id` (FK -> Usuario) — el handle queda ligado al usuario solicitante
- `to_account_id` (FK -> Cuenta) — se resuelve internamente a esta cuenta
- `name_masked` (texto)
- `expires_at` (timestamptz) — vence en 15 minutos
- `used_at` (timestamptz, nullable) — marcado al crear borrador o al consumirlo
- `created_at` (timestamptz)
- **Reglas**: se valida propiedad/consumo y expiración al resolver para crear borrador. Nunca acepta IDs de cuenta directos.

### LedgerTransaction (solo inserción)
- `id` (UUID, PK)
- `transaction_type` (enum: `TRANSFER` | `ADJUSTMENT` | `REVERSAL`)
- `idempotency_key` (texto, único por usuario/cuenta emisora)
- `from_user_id` (FK -> Usuario, nullable según tipo)
- `from_account_id` (FK -> Cuenta, nullable)
- `to_account_id` (FK -> Cuenta, nullable)
- `amount` (NUMERIC(18,2), positivo)
- `trace_id` (texto, FK -> Interacción.trace_id)
- `created_at` (timestamptz)
- **Reglas**: solo inserción (TRUNCATE bloqueado). Única por `(from_user_id, from_account_id, idempotency_key)`.

### LedgerEntry (solo inserción)
- `id` (UUID, PK)
- `transaction_id` (FK -> LedgerTransaction, obligatorio)
- `account_id` (FK -> Cuenta)
- `entry_type` (enum: `DEBIT` | `CREDIT`)
- `amount` (NUMERIC(18,2), positivo)
- `created_at` (timestamptz)
- **Reglas**: inmutable (trigger + permisos + TRUNCATE bloqueado). Al menos DEBIT y CREDIT por `transaction_id`. Trigger diferido garantiza suma DEBIT = suma CREDIT.

### Transferencia
- `id` (UUID, PK)
- `from_account_id` (FK)
- `to_account_id` (FK)
- `amount` (NUMERIC(18,2))
- `status` (enum: `COMPLETED` | `FAILED`)
- `transaction_id` (FK -> LedgerTransaction)
- `draft_id` (UUID, FK -> Borrador de transferencia, único) — clave de idempotencia = id del borrador
- `confirmed_at` (timestamptz)
- `created_at` (timestamptz)
- **Reglas**: creada únicamente al confirmar borrador (sin PENDING).

### Borrador de transferencia
- `id` (UUID, PK)
- `user_id` (FK -> Usuario)
- `from_account_id` (FK)
- `to_account_id` (FK) — resuelto desde `recipient_id` (handle)
- `recipient_handle` (texto) — handle usado
- `amount` (NUMERIC(18,2))
- `status` (enum: `pendiente` | `confirmada` | `rechazada` | `expirada`)
- `expires_at` (timestamptz, +15 min)
- `confirmed_at` (timestamptz, nullable)
- `trace_id` (texto, FK a interacción)
- `created_at` (timestamptz)
- **Reglas**: recibe `recipient_id` (handle). Nunca id de cuenta directo. Expiración evaluada al confirmar. Propiedad del handle validada (ligado al solicitante). Handle consumido al crear/confirmar.

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
- `embedding_model` (texto) obligatorio
- `embedding_version` (texto) obligatorio
- `access_level` (enum: `publico` | `cliente` | `interno`) denormalizado
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
- **Reglas**: inmutable (trigger + permisos + TRUNCATE bloqueado).

### DatasetDorado
- `id` (UUID, PK)
- `name` (texto)
- `version` (texto)
- `created_at` (timestamptz)

### EjecuciónEvaluación
- `id` (UUID, PK)
- `dataset_id` (FK)
- `run_type` (enum: `quality` | `strategy_comparison`)
- `config` (JSONB)
- `provider` (texto)
- `model` (texto)
- `judge_model` (texto, nullable)
- `metrics` (JSONB)
- `judge_status` (enum: `ok` | `judge_error`, nullable)
- `trace_id` (texto)
- `created_at` (timestamptz)

### InformeComparacionRecuperacion
- `id` (UUID, PK)
- `execution_ids` (UUID[])
- `summary` (JSONB)
- `report_path` (texto)
- `created_at` (timestamptz)
