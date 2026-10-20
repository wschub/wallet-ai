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

**Purpose**: Project initialization, CI and basic structure

- [ ] T001 Create backend structure per plan in `backend/` (pyproject.toml, alembic.ini, docker-compose.yml, src/wallet_ai/{domain,application,infrastructure,interfaces}, tests/, evals/)
- [ ] T002 Initialize Python 3.12+ project with uv and dependencies (fastapi, uvicorn, asyncpg, httpx, pydantic v2, alembic, pgvector, pyjwt, sse-starlette, mcp) in `backend/pyproject.toml`
- [ ] T003 [P] Configure linting/formatting (ruff) in `backend/pyproject.toml` and `backend/ruff.toml`
- [ ] T004 [P] Configure pytest + pytest-asyncio in `backend/pyproject.toml`
- [ ] T005 [P] Add docker-compose for PostgreSQL + pgvector in `backend/docker-compose.yml`
- [ ] T006 Initialize Alembic in `backend/alembic/` and configure `backend/alembic.ini`
- [ ] T007 [P] Setup GitHub Actions CI with only fake provider in `.github/workflows/ci.yml` (tests unit/integration/contract/adversarial, no external secrets)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented. Hexagonal, offline-first, tracing, PII, audit, auth/roles, seeds and architecture tests.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T008 Define domain ports in `backend/src/wallet_ai/domain/ports/` (LLMProvider, EmbeddingProvider, Reranker, PIIService, DocumentRepository, LedgerRepository, DraftRepository, AuditRepository, TraceContext)
- [ ] T009 [P] Implement fake deterministic adapters in `backend/src/wallet_ai/infrastructure/providers/fake/` (llm, embeddings, reranker) selectable by env; LLM fake must support scripted tool calls for the assistant
- [ ] T010 [P] Implement trace_id with contextvars and structured logging in `backend/src/wallet_ai/infrastructure/tracing/` (propagate to retrieval/tools/LLM)
- [ ] T011 [P] Implement PII service (patterns + Luhn) in `backend/src/wallet_ai/infrastructure/pii/` behind PIIService port; apply before LLM/logs/audit
- [ ] T012 Create migrations for users, accounts, interactions and audit_events (append-only) in `backend/alembic/versions/` with triggers BEFORE UPDATE/DELETE on audit_events, permissions to prevent UPDATE/DELETE, and TRUNCATE blocked on audit_events (and prepared for ledger tables)
- [ ] T013 Implement append-only audit repository in `backend/src/wallet_ai/infrastructure/db/audit_repository.py` (only insert)
- [ ] T014 [P] Implement auth JWT and roles (cliente/soporte/compliance) with synthetic users in `backend/src/wallet_ai/interfaces/api/deps/auth.py` and `backend/src/wallet_ai/interfaces/api/deps/roles.py`
- [ ] T015 [P] Add config/env management in `backend/src/wallet_ai/infrastructure/config/` (provider selection, no secrets committed)
- [ ] T016 [P] Setup FastAPI app skeleton in `backend/src/wallet_ai/interfaces/api/main.py` with CORS, error handling, trace middleware
- [ ] T017 [P] Setup MCP server skeleton in `backend/src/wallet_ai/interfaces/mcp/server.py` (adapter de entrada, sin tools de ejecución/confirmación)
- [ ] T018 [P] Create seed script `backend/scripts/seed.py` for synthetic users (cliente, soporte, compliance) and accounts with initial balances
- [ ] T019 [P] Add architecture tests: enforce domain has no imports from infrastructure; only `backend/src/wallet_ai/interfaces/api/routers/drafts.py` and `backend/src/wallet_ai/application/transfers/` may invoke confirmation logic (import-linter or custom test) in `backend/tests/architecture/`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Ledger de doble entrada y transferencias P2P (Priority: P1) 🎯 MVP

**Goal**: Cuentas, ledger de doble entrada inmutable, idempotencia, concurrencia con bloqueo en orden consistente, saldo derivado.

**Independent Test**: Crear dos cuentas, transferir con idempotency_key, reintentar misma clave → un solo efecto. Intentar UPDATE/DELETE/TRUNCATE → rechazado. Saldo insuficiente → sin cambio. Suma débitos=créditos.

### Tests for User Story 1 (TDD - must fail before implementation) ⚠️
- [ ] T020 [P] [US1] Contract/integration test for ledger invariants and idempotency in `backend/tests/integration/test_ledger.py` (debe fallar antes de implementar)
- [ ] T021 [P] [US1] Unit test for balance derivation and concurrency in `backend/tests/unit/test_ledger.py` (debe fallar antes de implementar)

### Implementation for User Story 1
- [ ] T022 [P] [US1] Create domain entities/value objects for accounts/ledger in `backend/src/wallet_ai/domain/ledger/`
- [ ] T023 [P] [US1] Create LedgerTransaction/LedgerEntry models/migrations per data-model in `backend/alembic/versions/` (ledger_transactions with idempotency_key único por (from_user_id, from_account_id, idempotency_key), ledger_entries con transaction_id, NUMERIC(18,2))
- [ ] T024 [US1] Add immutability for ledger: triggers BEFORE UPDATE/DELETE and TRUNCATE blocked on ledger_entries and ledger_transactions; deferred trigger to enforce sum(DEBIT)=sum(CREDIT) per transaction_id in `backend/alembic/versions/`
- [ ] T025 [US1] Implement ledger repository with row-level locking in consistent order and idempotency in `backend/src/wallet_ai/infrastructure/db/ledger_repository.py`
- [ ] T026 [US1] Implement ledger application service (balance derivation, transfer validation, no negative balance) in `backend/src/wallet_ai/application/ledger/`
- [ ] T027 [US1] Add API endpoints for accounts/balance/transactions with pagination (limit,cursor) in `backend/src/wallet_ai/interfaces/api/routers/accounts.py` per contracts
- [ ] T028 [US1] Run and make US1 tests pass

**Checkpoint**: US1 functional and testable independently

---

## Phase 4: User Story 2 - Asistente con saldo/movimientos, borradores, confirmación separada y orquestador (Priority: P2)

**Goal**: Consultas propias, orquestador del asistente con herramientas compartidas, borradores, confirmación vía endpoint separado fuera del canal del modelo/MCP, expiración al confirmar.

**Independent Test**: Preguntar saldo/movimientos → solo propios. Orquestador registra llamadas a herramientas. Crear borrador → no mueve dinero. Confirmar vía POST /drafts/{id}/confirm → ejecuta con idempotencia por draft_id. Rechazar/expirar → sin efecto. MCP/modelo no acceden a confirmación.

**Dependencies**: US1

### Tests for User Story 2 (TDD - must fail before implementation) ⚠️
- [ ] T029 [P] [US2] Integration test for drafts, expiration on confirm, and confirmation endpoint isolation in `backend/tests/integration/test_drafts.py` (debe fallar antes de implementar)
- [ ] T030 [P] [US2] Contract test for MCP tools restrictions (no confirm/execute) in `backend/tests/contract/test_mcp_tools.py` (debe fallar antes de implementar)
- [ ] T031 [P] [US2] Integration test for assistant orchestrator tool calls via fake scripted LLM in `backend/tests/integration/test_assistant_orchestrator.py` (debe fallar antes de implementar)

### Implementation for User Story 2
- [ ] T032 [P] [US2] Create draft entities/states in `backend/src/wallet_ai/domain/transfers/` (pendiente/confirmada/rechazada/expirada)
- [ ] T033 [P] [US2] Migrations for drafts/transferencias per data-model in `backend/alembic/versions/` (Transferencia sin PENDING, draft_id único, amount NUMERIC(18,2))
- [ ] T034 [US2] Implement draft repository and expiration logic (evaluated on confirm) in `backend/src/wallet_ai/infrastructure/db/draft_repository.py`
- [ ] T035 [US2] Implement draft application service (create/list/reject/confirm with ownership check, evaluate expiration on confirm, idempotency by draft_id) in `backend/src/wallet_ai/application/transfers/`
- [ ] T036 [US2] Implement assistant orchestrator in `backend/src/wallet_ai/application/assistant/orchestrator.py` (prompt, tool loop, shared tool registry with MCP, response with citations)
- [ ] T037 [US2] Extend fake LLM adapter to emit scripted tool calls deterministically in `backend/src/wallet_ai/infrastructure/providers/fake/llm.py`
- [ ] T038 [US2] Add API routes for drafts and POST /drafts/{id}/confirm (separado) in `backend/src/wallet_ai/interfaces/api/routers/drafts.py` per OpenAPI
- [ ] T039 [US2] Add MCP tools in `backend/src/wallet_ai/interfaces/mcp/tools/` for get_balance, list_transactions, find_recipient (opaco + enmascarado, desambiguación), draft_transfer (identidad desde sesión). Sin confirm/execute.
- [ ] T040 [US2] Add SSE endpoint for chat in `backend/src/wallet_ai/interfaces/api/routers/chat.py` (trace_id en header y primer evento SSE; sin confirmación)
- [ ] T041 [US2] Run and make US2 tests pass

**Checkpoint**: US1 and US2 independently functional

---

## Phase 5: User Story 3 - RAG con ACL, corpus sintético, segmentación configurable y recuperación híbrida (Priority: P3)

**Goal**: Corpus sintético de políticas con 3 niveles, ingesta con segmentación configurable (fijo/estructural), embeddings con modelo+versión, filtro ACL antes del ranking, híbrida texto+vector con RRF, re-ranking por puerto.

**Independent Test**: Cliente no ve interno; compliance/soporte sí. ACL aplicado antes de ranking. Respuestas con citas. Segmentación configurable produce chunks distintos comparables.

### Tests for User Story 3 (TDD - must fail before implementation) ⚠️
- [ ] T042 [P] [US3] Integration test for ACL before ranking and citations in `backend/tests/integration/test_rag_acl.py` (debe fallar antes de implementar)

### Implementation for User Story 3
- [ ] T043 [P] [US3] Create synthetic policy corpus (3 access levels) in `backend/evals/golden/corpus/` or `backend/data/policies/`
- [ ] T044 [P] [US3] Create document/chunk entities with access_level denormalizado in `backend/src/wallet_ai/domain/documents/`
- [ ] T045 [P] [US3] Migrations for documents/chunks with embedding_model/version and access_level in `backend/alembic/versions/`
- [ ] T046 [US3] Implement ingestion with configurable segmentation (fixed vs structural) in `backend/src/wallet_ai/application/rag/ingest.py` storing embedding_model/version
- [ ] T047 [US3] Implement document/chunk repositories with ACL pre-filter in `backend/src/wallet_ai/infrastructure/db/document_repository.py`
- [ ] T048 [US3] Implement hybrid retrieval: prefer exact search with ACL filter first; if needed evaluate pgvector iterative_scan as documented alternative in `backend/src/wallet_ai/application/rag/hybrid_retriever.py` (RRF k=60)
- [ ] T049 [US3] Implement reranker port usage (fake + optional local adapter as extra dep) in `backend/src/wallet_ai/application/rag/reranker.py`
- [ ] T050 [US3] Implement RAG service with citations and grounding in `backend/src/wallet_ai/application/rag/rag_service.py`
- [ ] T051 [US3] Run and make US3 tests pass

**Checkpoint**: US3 independently functional

---

## Phase 6: User Story 4 - Guardrails, PII, prompt injection/exfiltración (Priority: P4)

**Goal**: Reglas deterministas + verificación opcional, PII enmascarado, suite adversarial sin fugas.

**Independent Test**: Suite adversarial pasa con 0 fugas PII / 0 documentos sin permiso. PII enmascarado en logs/auditoría/recuperado.

### Tests for User Story 4 (TDD - must fail before implementation) ⚠️
- [ ] T052 [P] [US4] Adversarial tests (injection/exfiltration/PII) in `backend/tests/adversarial/test_adversarial.py` (debe fallar antes de implementar)

### Implementation for User Story 4
- [ ] T053 [P] [US4] Implement guardrails rules in `backend/src/wallet_ai/infrastructure/guardrails/rules.py` (injection/exfiltration)
- [ ] T054 [US4] Integrate guardrails into assistant pipeline with audit events in `backend/src/wallet_ai/application/assistant/guardrails_service.py`
- [ ] T055 [US4] Ensure PII masking applied to retrieved content, logs, and audit payloads in `backend/src/wallet_ai/infrastructure/pii/masker.py`
- [ ] T056 [US4] Create/update adversarial suite versionada in `backend/tests/adversarial/`
- [ ] T057 [US4] Run and make US4 tests pass

**Checkpoint**: US4 independently functional

---

## Phase 7: User Story 5 - Auditoría para compliance (Priority: P5)

**Goal**: Consulta de auditoría solo compliance, trazabilidad completa por trace_id, eventos append-only.

**Independent Test**: Cualquier interacción tiene trace_id; compliance reconstruye cadena completa; otros roles sin acceso.

### Tests for User Story 5 (TDD - must fail before implementation) ⚠️
- [ ] T058 [P] [US5] Integration test for audit access control and trace reconstruction in `backend/tests/integration/test_audit.py` (debe fallar antes de implementar)

### Implementation for User Story 5
- [ ] T059 [P] [US5] Add audit query service with role check (solo compliance) in `backend/src/wallet_ai/application/audit/audit_query_service.py`
- [ ] T060 [US5] Add API endpoint GET /audit with pagination (limit,cursor) and trace_id filter in `backend/src/wallet_ai/interfaces/api/routers/audit.py`
- [ ] T061 [US5] Ensure all decisions logged with trace_id in tracing integration
- [ ] T062 [US5] Run and make US5 tests pass

**Checkpoint**: All user stories (US1-US5) independently functional

---

## Phase 8: Comparación de estrategias e informe de evaluación (Priority: P6)

**Goal**: Evaluar >=3 estrategias con mismo dataset/criterios; juez propio vía LLMProvider; RAGAS opcional posterior; tabla comparativa en README.

**Independent Test**: Comparación genera informe en backend/evals/reports/ con config/proveedor/modelo/métricas comparables.

### Tests for Evaluation (TDD - must fail before implementation) ⚠️
- [ ] T063 [P] [EVAL] Contract test for evaluation report schema in `backend/tests/contract/test_evals.py` (debe fallar antes de implementar)

### Implementation for Evaluation
- [ ] T064 [P] [EVAL] Create golden dataset structure (30–50 preguntas) in `backend/evals/golden/`
- [ ] T065 [EVAL] Implement fidelity judge propio vía LLMProvider (prompt/checklist) in `backend/evals/judge/` (sin depender obligatoriamente de RAGAS)
- [ ] T066 [EVAL] Implement evaluation runners (run_quality, run_strategies) in `backend/evals/` with RAGAS documented as optional extra `eval` dependency
- [ ] T067 [EVAL] Implement strategy comparison (segmentación fijo/estructural, léxica/vectorial/híbrida, con/sin re-ranking) in `backend/evals/strategies/` computing hit@k, MRR y fidelidad
- [ ] T068 [EVAL] Write versioned reports to `backend/evals/reports/` with provider/model/config
- [ ] T069 [EVAL] Generate/update comparison table in `README.md`
- [ ] T070 [EVAL] Run and make evaluation tests pass

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Docs, ADRs, LICENSE, demo sin llaves, hardening

- [ ] T071 [P] Update README with architecture diagram, ADR cortos, LICENSE, demo sin llaves (using fake provider) and eval comparison table in `README.md`
- [ ] T072 [P] Ensure contracts valid and aligned (OpenAPI/MCP) in `specs/001-wallet-financial-assistant/contracts/`
- [ ] T073 [P] Run quickstart validation steps from `specs/001-wallet-financial-assistant/quickstart.md` (from backend/)
- [ ] T074 [P] Add minimal frontend scaffold (optional) in `frontend/`
- [ ] T075 [P] Add docker-compose full stack notes and optional Cloud Run/Cloud SQL guidance in `docs/` or `README.md`
- [ ] T076 [P] Security/architecture hardening: verify MCP no execute/confirm, confirmation endpoint isolated, ACL before ranking, PII masking, audit append-only + TRUNCATE blocks
- [ ] T077 [P] Final architecture tests pass in `backend/tests/architecture/`

---

## Dependencies & Execution Order

### Phase Dependencies
- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup - BLOCKS all user stories
- **User Stories (Phase 3-7)**: Depend on Foundational; US1 is MVP base, US2 depends on US1
- **Evaluation (Phase 8)**: Depends on RAG/assistant infrastructure
- **Polish (Phase 9)**: Depends on desired stories complete

### User Story Dependencies
- **US1**: After Foundational
- **US2**: After Foundational + US1
- **US3**: After Foundational
- **US4**: After US2/US3
- **US5**: After US2/US3/US4

### Parallel Opportunities
- Setup/Foundation [P] tasks
- Tests marked [P] must be written first (TDD)
- Different user stories can proceed in parallel where independent

---

## Implementation Strategy

### MVP First (US1 Only)
1. Phase 1 Setup (+ CI)
2. Phase 2 Foundational (+ seeds + architecture tests)
3. Phase 3 US1 (TDD)
4. Validate ledger invariants

### Incremental Delivery
1. Foundation + US1 → MVP
2. Add US2 → orchestrator + drafts + separated confirmation
3. Add US3 → synthetic corpus + segmentation + ACL + hybrid
4. Add US4 → guardrails + adversarial
5. Add US5 → audit
6. Add evaluation comparison + README table

---

## Notes
- [P] = different files, no dependencies
- [Story]/[EVAL] labels for traceability
- Tests fail before implementation (TDD)
- MCP in `backend/src/wallet_ai/interfaces/mcp/` (input adapter), no execute/confirm tools
- Confirmation via separate endpoint `POST /drafts/{id}/confirm`, expiration evaluated on confirm, idempotency by draft_id
- Ledger immutability via triggers + permissions + TRUNCATE blocks; sums enforced by deferred trigger
- Amounts NUMERIC(18,2), API uses decimal strings
- Pagination with limit+cursor
- trace_id in header + first SSE event
- Fidelity judge propio vía LLMProvider; RAGAS optional extra
- ACL filter before ranking, Chunk.access_level denormalizado
- find_recipient returns opaque id + masked name, identity from session
- Architecture tests enforce hexagonal boundaries and confirmation isolation
