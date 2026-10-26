# Tasks: Wallet Simulado con Asistente Financiero

**Input**: Design documents from `/specs/001-wallet-financial-assistant/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Tests are included where they validate contracts/guardrails/adversarial/ledger invariants. Follow TDD where noted.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story/EVAL] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4, US5)
- **[EVAL]**: Evaluation/comparison tasks
- Include exact file paths in descriptions

## Path Conventions

- **Backend**: `backend/src/wallet_ai/`, `backend/tests/`, `backend/evals/`, `backend/alembic/`
- **Contracts**: `specs/001-wallet-financial-assistant/contracts/`
- **Docs**: `specs/001-wallet-financial-assistant/`

---

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Create backend structure per plan in `backend/`
- [x] T002 Initialize Python 3.12+ with uv/deps (fastapi, uvicorn, asyncpg, httpx, pydantic v2, alembic, pgvector, pyjwt, sse-starlette, mcp) in `backend/pyproject.toml`
- [x] T003 [P] Configure ruff in `backend/pyproject.toml` and `backend/ruff.toml`
- [x] T004 [P] Configure pytest + pytest-asyncio in `backend/pyproject.toml`
- [x] T005 [P] Add docker-compose (Postgres+pgvector) in `backend/docker-compose.yml`
- [x] T006 Initialize Alembic in `backend/alembic/` and `backend/alembic.ini`
- [x] T007 [P] GitHub Actions CI with only fake provider in `.github/workflows/ci.yml`

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: Blocks all user stories

- [x] T008 Define domain ports in `backend/src/wallet_ai/domain/ports/`
- [x] T009 [P] Fake adapters (llm with scripted tool calls, embeddings, reranker) in `backend/src/wallet_ai/infrastructure/providers/fake/`
- [x] T010 [P] trace_id + contextvars + structured logging in `backend/src/wallet_ai/infrastructure/tracing/`
- [x] T011 [P] PIIService (patterns + Luhn) in `backend/src/wallet_ai/infrastructure/pii/`
- [x] T012 Create migrations for users, accounts, RecipientHandle, interactions, audit_events (append-only) in `backend/alembic/versions/` (triggers BEFORE UPDATE/DELETE, TRUNCATE blocked on audit_events; prepare for ledger). [ADDENDUM US1] Agregar triggers BEFORE TRUNCATE a nivel de sentencia para `ledger_entries` y `ledger_transactions` al crearlos en T023/T024. (Hecho en `20261022_0003_ledger.py`: BEFORE TRUNCATE por sentencia en ambas tablas.)
- [x] T013 Implement append-only AuditRepository in `backend/src/wallet_ai/infrastructure/db/audit_repository.py`

- [x] T014 [P] Auth JWT + roles (cliente/soporte/compliance) en `backend/src/wallet_ai/interfaces/api/deps/auth.py` y `deps/roles.py` (cubierto por tests)
- [x] T015 [P] Config/env in `backend/src/wallet_ai/infrastructure/config/`
- [x] T016 [P] FastAPI skeleton en `backend/src/wallet_ai/interfaces/api/main.py` con trace_id/middleware y tests de auth/API
- [x] T017 [P] MCP skeleton en `backend/src/wallet_ai/interfaces/mcp/server.py` sin herramientas confirm/execute (cubierto por tests)
- [x] T018 [P] Seed script `backend/scripts/seed.py` (usuarios + cuentas)
- [x] T019 [P] Architecture tests (domain no importa infrastructure; confirmación aislada) in `backend/tests/architecture/` — capas + contrato import-linter "Confirmation isolation" (asistente/MCP/providers/guardrails no pueden importar `confirm_draft`, `application.ledger` ni `ledger_repository`) y test `ast` en `tests/architecture/test_confirmation_isolation.py` que exige que solo `interfaces/api/routers/drafts.py` importe `confirm_draft`


**Checkpoint**: Foundation ready

---

## Phase 3: User Story 1 - Ledger (P1) 🎯 MVP

### Tests (TDD - deben fallar)
- [x] T020 [P] [US1] Ledger invariants/idempotency tests in `backend/tests/integration/test_ledger.py` (debe fallar antes de implementar) — 17 tests: balance, desbalance, amount<=0, idempotency_key, UPDATE/DELETE/TRUNCATE "append-only" (incl. TRUNCATE accounts/users CASCADE con rollback) y `SET ROLE wallet_app` con `InsufficientPrivilegeError`
- [ ] T021 [P] [US1] Balance/concurrency tests in `backend/tests/unit/test_ledger.py` (debe fallar antes de implementar)

### Implementation
- [ ] T022 [P] [US1] Domain ledger entities in `backend/src/wallet_ai/domain/ledger/`
- [x] T023 [P] [US1] Migrations ledger_transactions/ledger_entries (NUMERIC(18,2)) in `backend/alembic/versions/` — `20261022_0003_ledger.py` (+ `accounts.is_system` para la cuenta de sistema "fondeo")
- [x] T024 [US1] Immutability: triggers UPDATE/DELETE/TRUNCATE blocks + deferred sum(DEBIT)=sum(CREDIT) in `backend/alembic/versions/` — trigger diferido `DEFERRABLE INITIALLY DEFERRED` por transacción, con downgrade funcional
- [x] T025 [US1] LedgerRepository (locking orden consistente, idempotencia) en `backend/src/wallet_ai/infrastructure/db/ledger_repository.py` (SELECT ... FOR UPDATE ordenado por id, transacción única, ON CONFLICT DO NOTHING + lectura existente, request_hash, Decimal, validaciones)
- [x] T026 [US1] Ledger service en `backend/src/wallet_ai/application/ledger/` (sin endpoints HTTP que muevan dinero; solo usado por lógica)
- [x] T027 [US1] Accounts endpoints + pagination en `backend/src/wallet_ai/interfaces/api/routers/accounts.py` — GET /accounts/me (saldo string decimal) y GET /accounts/me/transactions (limit/cursor, orden estable por created_at,id)
- [ ] T028 [US1] Hacer pasar tests US1

---

## Phase 4: User Story 2 - Asistente + Borradores + Handles (P2)

**Depends on**: US1

### Tests (TDD)
- [x] T029 [P] [US2] Drafts/expiración al confirmar + aislamiento en `backend/tests/integration/test_drafts.py` (debe fallar) — 22 tests: confirma una sola vez, 20 confirmaciones concurrentes -> única transacción y mismo id, vencido -> 'expirada' sin tocar ledger, saldo insuficiente -> sigue 'pendiente', ajeno -> 404, triggers (UPDATE inmutable/transición/confirmada requiere ledger+confirmed_at), DELETE/TRUNCATE "append-only", `wallet_app` UPDATE sí/DELETE `InsufficientPrivilegeError`, endpoints y roles
- [ ] T030 [P] [US2] MCP tools (no confirm/execute, handles) en `backend/tests/contract/test_mcp_tools.py` (debe fallar)
- [x] T031 [P] [US2] Orchestrator con tool calls scripted en `backend/tests/integration/test_assistant_orchestrator.py` (debe fallar)
- [x] T032 [P] [US2] Handles: rechazo de cuenta directa/handle ajeno/expirado en `backend/tests/integration/test_recipients.py` — 15 tests: MISMO error genérico para handle desconocido/vencido/ajeno/UUID de cuenta, búsqueda de 2 caracteres rechazada, dos Marías con handles distintos y sin PII, exclusión del propio solicitante y de cuentas de sistema, máximo 5, `POST /drafts` pendiente sin tocar el ledger, 401/403/400 con `X-Trace-Id`, auditoría `tool_call` enmascarada y privilegios `wallet_app`. El caso "usado" no aplica en B1: `recipient_handles` es append-only (sin `used_at`), la vigencia la define `expires_at`

### Implementation
- [x] T033 [P] [US2] Domain transfers + RecipientHandle en `backend/src/wallet_ai/domain/transfers/` — `draft.py` (entidad `Draft` + máquina de estados pendiente/confirmada/rechazada/expirada) y `errors.py` (`DraftNotFound`, `DraftNotConfirmable`, `DraftExpired`, `InvalidDraft`). `RecipientHandle` vive en `domain/recipients/handle.py` (B1), con `is_expired`, y errores/enmascarado en `domain/recipients/`
- [x] T034 [P] [US2] Migraciones drafts/transferencias (recipient_handle, sin PENDING, draft_id único) en `backend/alembic/versions/` — `20261022_0006_transfer_drafts.py`: SQL explícito, downgrade funcional, trigger BEFORE UPDATE (inmutabilidad + transiciones solo desde 'pendiente' + `ledger_transaction_id`/`confirmed_at` al confirmar), BEFORE INSERT (sin cuentas de sistema), BEFORE DELETE y BEFORE TRUNCATE, y grants `wallet_app` SELECT/INSERT/UPDATE. `recipient_handle` queda para el tramo de handles; la idempotencia va en `ledger_transactions.idempotency_key` = id del borrador
- [x] T035 [US2] DraftRepository + expiración evaluada al confirmar en `backend/src/wallet_ai/infrastructure/db/draft_repository.py` — `get(for_update=True)`, `create`, `list_for_user`, `mark_*`; participa en la transacción abierta por el caso de uso (no abre la suya)
- [x] T036 [US2] RecipientHandleRepository (emisión ligada a solicitante, vencimiento 15m) en `backend/src/wallet_ai/infrastructure/db/recipient_repository.py` — `search`, `issue_handle` (`secrets.token_urlsafe(16)`) y `find`; sin consumo mutable: la tabla es append-only (UPDATE prohibido), la vigencia es `expires_at` y cada uso queda auditado
- [x] T037 [US2] Draft service (recibe handle, valida propiedad/expiración, idempotencia por draft_id) en `backend/src/wallet_ai/application/transfers/` — tramo A (borradores y confirmación separada, sin handles): un archivo por caso `create_draft.py`, `list_drafts.py`, `reject_draft.py`, `confirm_draft.py`. `confirm_draft` en UNA transacción: `FOR UPDATE`, propiedad, idempotencia (ya confirmado -> mismo id), terminal -> 409, vencido -> marca 'expirada' y falla, transfiere con `in_transaction=True` y actualiza el borrador. La validación de handle se añade en B1 con `application/recipients/create_draft_from_handle.py`, que resuelve el handle (mismo error genérico) y delega en `create_draft`; ni el caso de uso ni `application/recipients` importan el ledger ni `confirm_draft`
- [x] T038 [US2] Orchestrator en `backend/src/wallet_ai/application/assistant/orchestrator.py` + registry compartido con MCP
- [x] T039 [US2] Fake LLM scripted tool calls en `backend/src/wallet_ai/infrastructure/providers/fake/llm.py`
- [x] T040 [US2] /recipients GET en `backend/src/wallet_ai/interfaces/api/routers/recipients.py` — `GET /recipients?q=` (mínimo 3 caracteres, máximo 5 resultados, solo rol cliente, `X-Trace-Id` en toda respuesta, 400/401/403); devuelve handle + nombre enmascarado, sin correo ni ids de usuario/cuenta
- [x] T041 [US2] Drafts + /drafts/{id}/confirm (separado) en `backend/src/wallet_ai/interfaces/api/routers/drafts.py` — GET /drafts, GET /drafts/{id}, POST /drafts/{id}/reject y POST /drafts/{id}/confirm (solo rol cliente, `X-Trace-Id`, montos como string decimal, 404/409, confirmación idempotente). `POST /drafts` (creación con handle) se añade en B1: body `{recipient_id: handle, amount: string decimal}`, crea el borrador pendiente en una transacción junto con la auditoría, no publica ids de cuenta y no toca el ledger
- [x] T042 [US2] MCP tools (get_balance,list_transactions,find_recipient,draft_transfer) en `backend/src/wallet_ai/application/assistant/tools/`
- [x] T043 [US2] Chat SSE (X-Trace-Id header + primer evento) en `backend/src/wallet_ai/interfaces/api/routers/chat.py`
- [ ] T044 [US2] Hacer pasar tests US2

---

## Phase 5: US3 - RAG ACL + Corpus + Segmentación (P3)

### Tests (TDD)
- [ ] T045 [P] [US3] ACL antes de ranking + citas en `backend/tests/integration/test_rag_acl.py` (debe fallar)

### Implementation
- [ ] T046 [P] [US3] Corpus sintético 3 niveles en `backend/evals/golden/corpus/`
- [ ] T047 [P] [US3] Domain documents/chunks + access_level denormalizado en `backend/src/wallet_ai/domain/documents/`
- [ ] T048 [P] [US3] Migraciones documents/chunks en `backend/alembic/versions/`
- [ ] T049 [US3] Ingesta segmentación fijo/estructural + embedding_model/version en `backend/src/wallet_ai/application/rag/ingest.py`
- [ ] T050 [US3] DocumentRepository con prefilter ACL en `backend/src/wallet_ai/infrastructure/db/document_repository.py`
- [ ] T051 [US3] Hybrid retrieval: búsqueda exacta con filtro ACL (preferido); iterative_scan documentado en `backend/src/wallet_ai/application/rag/hybrid_retriever.py` (RRF k=60)
- [ ] T052 [US3] Reranker puerto (fake + opcional local) en `backend/src/wallet_ai/application/rag/reranker.py`
- [ ] T053 [US3] RAG service con citas en `backend/src/wallet_ai/application/rag/rag_service.py`
- [ ] T054 [US3] Hacer pasar tests US3

---

## Phase 6: US4 - Guardrails + PII + Adversarial (P4)

### Tests (TDD)
- [ ] T055 [P] [US4] Adversarial (0 fugas PII, 0 docs sin permiso) en `backend/tests/adversarial/test_adversarial.py` (debe fallar)

### Implementation
- [ ] T056 [P] [US4] Reglas guardrails en `backend/src/wallet_ai/infrastructure/guardrails/rules.py`
- [ ] T057 [US4] Integrar guardrails + audit en `backend/src/wallet_ai/application/assistant/guardrails_service.py`
- [ ] T058 [US4] PII masking sobre recuperado/logs/audit en `backend/src/wallet_ai/infrastructure/pii/masker.py`
- [ ] T059 [US4] Suite adversarial versionada en `backend/tests/adversarial/`
- [ ] T060 [US4] Hacer pasar tests US4

---

## Phase 7: US5 - Auditoría (P5)

### Tests (TDD)
- [ ] T061 [P] [US5] Audit access control + trace reconstruction en `backend/tests/integration/test_audit.py` (debe fallar)

### Implementation
- [ ] T062 [P] [US5] AuditQueryService (solo compliance) en `backend/src/wallet_ai/application/audit/audit_query_service.py`
- [ ] T063 [US5] GET /audit limit+cursor+trace_id en `backend/src/wallet_ai/interfaces/api/routers/audit.py`
- [ ] T064 [US5] Logging decisiones con trace_id
- [ ] T065 [US5] Hacer pasar tests US5

---

## Phase 8: Evaluación y Comparación [EVAL] (P6)

### Tests (TDD)
- [ ] T066 [P] [EVAL] Esquema JSON juez + report schema en `backend/tests/contract/test_evals.py` (debe fallar)

### Implementation
- [ ] T067 [P] [EVAL] Dataset dorado 30–50 en `backend/evals/golden/`
- [ ] T068 [EVAL] Juez propio vía LLMProvider con **esquema JSON estricto**, 1 reintento ante inválido, `judge_status = judge_error` sin puntaje inventado, guardar `judge_model`. Calibración manual ~10 ítems en `backend/evals/judge/`
- [ ] T069 [EVAL] Runners (run_quality, run_strategies), RAGAS como extra opcional en `backend/evals/`
- [ ] T070 [EVAL] Comparación estrategias (fijo/estructural, léxica/vectorial/híbrida, con/sin re-ranking) hit@k/MRR/fidelidad en `backend/evals/strategies/`
- [ ] T071 [EVAL] Informes versionados en `backend/evals/reports/`
- [ ] T072 [EVAL] Tabla comparativa en `README.md`
- [ ] T073 [EVAL] Hacer pasar tests evaluación

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T074 [P] README completo (diagrama arquitectura, ADR cortos, LICENSE, demo sin llaves, tabla comparativa eval) en `README.md`
- [ ] T075 [P] Validar contratos (OpenAPI/MCP)
- [ ] T076 [P] Quickstart desde backend/ en `specs/001-wallet-financial-assistant/quickstart.md` (validado)
- [ ] T077 [P] Frontend mínimo en `frontend/`, notas docker/Cloud Run/Cloud SQL, hardening + architecture tests pass

## Dependencies
- Foundational (2) bloquea todo
- US2 depende US1
- US4 depende US2/US3; US5 depende US2/US3/US4
- Eval depende US3/assistant
- Polish tras historias/éval

## Implementation Strategy
MVP: 1→2→3 (US1). Incremental + TDD (tests fallan antes). MCP en interfaces/mcp, confirmación separada, handles opacos, juez estricto.
