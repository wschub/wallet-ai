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

- [ ] T008 Define domain ports in `backend/src/wallet_ai/domain/ports/`
- [ ] T009 [P] Fake adapters (llm with scripted tool calls, embeddings, reranker) in `backend/src/wallet_ai/infrastructure/providers/fake/`
- [ ] T010 [P] trace_id + contextvars + structured logging in `backend/src/wallet_ai/infrastructure/tracing/`
- [ ] T011 [P] PIIService (patterns + Luhn) in `backend/src/wallet_ai/infrastructure/pii/`
- [ ] T012 Create migrations for users, accounts, RecipientHandle, interactions, audit_events (append-only) in `backend/alembic/versions/` (triggers BEFORE UPDATE/DELETE, TRUNCATE blocked on audit_events; prepare for ledger)
- [ ] T013 Implement append-only AuditRepository in `backend/src/wallet_ai/infrastructure/db/audit_repository.py`
- [ ] T014 [P] Auth JWT + roles (cliente/soporte/compliance) in `backend/src/wallet_ai/interfaces/api/deps/auth.py` and `deps/roles.py`
- [ ] T015 [P] Config/env in `backend/src/wallet_ai/infrastructure/config/`
- [ ] T016 [P] FastAPI skeleton in `backend/src/wallet_ai/interfaces/api/main.py`
- [ ] T017 [P] MCP skeleton in `backend/src/wallet_ai/interfaces/mcp/server.py`
- [ ] T018 [P] Seed script `backend/scripts/seed.py` (usuarios + cuentas)
- [ ] T019 [P] Architecture tests (domain no importa infrastructure; confirmación aislada) in `backend/tests/architecture/`

**Checkpoint**: Foundation ready

---

## Phase 3: User Story 1 - Ledger (P1) 🎯 MVP

### Tests (TDD - deben fallar)
- [ ] T020 [P] [US1] Ledger invariants/idempotency tests in `backend/tests/integration/test_ledger.py` (debe fallar antes de implementar)
- [ ] T021 [P] [US1] Balance/concurrency tests in `backend/tests/unit/test_ledger.py` (debe fallar antes de implementar)

### Implementation
- [ ] T022 [P] [US1] Domain ledger entities in `backend/src/wallet_ai/domain/ledger/`
- [ ] T023 [P] [US1] Migrations ledger_transactions/ledger_entries (NUMERIC(18,2)) in `backend/alembic/versions/`
- [ ] T024 [US1] Immutability: triggers UPDATE/DELETE/TRUNCATE blocks + deferred sum(DEBIT)=sum(CREDIT) in `backend/alembic/versions/`
- [ ] T025 [US1] LedgerRepository (locking orden consistente, idempotencia) in `backend/src/wallet_ai/infrastructure/db/ledger_repository.py`
- [ ] T026 [US1] Ledger service in `backend/src/wallet_ai/application/ledger/`
- [ ] T027 [US1] Accounts endpoints + pagination in `backend/src/wallet_ai/interfaces/api/routers/accounts.py`
- [ ] T028 [US1] Hacer pasar tests US1

---

## Phase 4: User Story 2 - Asistente + Borradores + Handles (P2)

**Depends on**: US1

### Tests (TDD)
- [ ] T029 [P] [US2] Drafts/expiración al confirmar + aislamiento en `backend/tests/integration/test_drafts.py` (debe fallar)
- [ ] T030 [P] [US2] MCP tools (no confirm/execute, handles) en `backend/tests/contract/test_mcp_tools.py` (debe fallar)
- [ ] T031 [P] [US2] Orchestrator con tool calls scripted en `backend/tests/integration/test_assistant_orchestrator.py` (debe fallar)
- [ ] T032 [P] [US2] Handles: rechazo de cuenta directa/handle ajeno/expirado/usado en `backend/tests/integration/test_recipients.py` (debe fallar)

### Implementation
- [ ] T033 [P] [US2] Domain transfers + RecipientHandle en `backend/src/wallet_ai/domain/transfers/`
- [ ] T034 [P] [US2] Migraciones drafts/transferencias (recipient_handle, sin PENDING, draft_id único) en `backend/alembic/versions/`
- [ ] T035 [US2] DraftRepository + expiración evaluada al confirmar en `backend/src/wallet_ai/infrastructure/db/draft_repository.py`
- [ ] T036 [US2] RecipientHandleRepository (emisión ligada a solicitante, vencimiento 15m, consumo) en `backend/src/wallet_ai/infrastructure/db/recipient_repository.py`
- [ ] T037 [US2] Draft service (recibe handle, valida propiedad/expiración/consumo, idempotencia por draft_id) en `backend/src/wallet_ai/application/transfers/`
- [ ] T038 [US2] Orchestrator en `backend/src/wallet_ai/application/assistant/orchestrator.py` + registry compartido con MCP
- [ ] T039 [US2] Fake LLM scripted tool calls en `backend/src/wallet_ai/infrastructure/providers/fake/llm.py`
- [ ] T040 [US2] /recipients GET en `backend/src/wallet_ai/interfaces/api/routers/recipients.py`
- [ ] T041 [US2] Drafts + /drafts/{id}/confirm (separado) en `backend/src/wallet_ai/interfaces/api/routers/drafts.py`
- [ ] T042 [US2] MCP tools (get_balance,list_transactions,find_recipient,draft_transfer) en `backend/src/wallet_ai/interfaces/mcp/tools/`
- [ ] T043 [US2] Chat SSE (X-Trace-Id header + primer evento) en `backend/src/wallet_ai/interfaces/api/routers/chat.py`
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
