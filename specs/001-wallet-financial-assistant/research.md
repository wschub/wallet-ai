# Research: Wallet Simulado con Asistente Financiero

## 1) Streaming SSE
- **Decision**: `sse-starlette` (integrado con FastAPI/Starlette). Emitir eventos por `trace_id`. El `trace_id` se devuelve en el **primer evento SSE** y también en un **header de respuesta**.
- **Rationale**: Ligero, asíncrono, compatible con el stack async del proyecto y fácil de cancelar.
- **Alternatives**: `fastapi-sse` (menos mantenido), WebSocket (no necesario para un stream unidireccional del asistente).

## 2) SDK MCP
- **Decision**: `mcp` (Python SDK oficial) en `interfaces/mcp` (adaptador de entrada). 
- **Rationale**: Ubicación hexagonal correcta (entrada), control total sobre herramientas expuestas. 
- **Restricción**: Solo herramientas de lectura (`get_balance`, `list_transactions`), `draft_transfer` (prepara borrador) y `find_recipient` (busca destinatario con resultados opacos/enmascarados). **Sin** herramientas para ejecutar o confirmar transferencias.
- **Regla**: La identidad del usuario sale de la **sesión/autenticación**, nunca de argumentos del modelo.
- **Alternatives**: Servidor MCP en infraestructura (rechazado por arquitectura).

## 3) Índice pgvector
- **Decision**: HNSW (`vector_cosine_ops`) como por defecto. IVFFlat como alternativa configurable si el volumen crece. 
- **Filtro ACL**: Aplicar filtro de `access_level` **antes** del ranking/vector search (usando `Chunk.access_level` denormalizado). 
- **Búsqueda exacta vs escaneo iterativo con filtro**: Preferir **filtrado por predicado sobre columnas** (con índice B-tree sobre `access_level` y FK) antes de generar candidatos; para búsquedas vectoriales con filtro restrictivo, evaluar **`iterative_scan`** (pgvector) o pre-filtrado por conjuntos de documentos permitidos para evitar escaneos ineficientes. El plan concreto se valida en integración.
- **Rationale**: Evita filtrar después del ranking (seguridad) y controla costo con ACL restrictivo.
- **Alternatives**: solo búsqueda léxica (insuficiente), otros motores vectoriales (fuera de alcance).

## 4) Fusión RRF (Reciprocal Rank Fusion)
- **Decision**: RRF con `k=60` fusionando resultados léxicos (PostgreSQL full-text) y vectoriales (pgvector), siempre tras aplicar filtro ACL.
- **Rationale**: Simple, robusto, no requiere normalización compleja entre puntajes; fácil de comparar entre estrategias.
- **Alternatives**: CombSUM/normalización min-max (más frágil entre proveedores/distancias).

## 5) Detección de PII
- **Decision**: Componente propio en `infrastructure/pii/` tras puerto `PIIService` en `domain/ports`. Patrones + validación Luhn para tarjetas, aplicado **antes** del LLM, sobre contenido recuperado, logs y auditoría.
- **Rationale**: Requisito NO-NEGOTIABLE, sin depender de un proveedor externo; determinista y testeable.
- **Alternatives**: Servicios de terceros (prohibidos por offline/CI y privacidad).

## 6) Adaptador local de re-ranking
- **Decision**: Puerto `Reranker` en dominio; adaptador local opcional como **dependencia extra** (p. ej. `sentence-transformers`), con adaptador `fake` por defecto.
- **Rationale**: Permite comparar con/sin re-ranking sin forzar dependencias pesadas; seleccionable por variables de entorno.
- **Alternatives**: Solo re-ranking vía API (no offline-friendly).

## 7) RAGAS con juez configurable (no atado a un proveedor)
- **Decision**: Tratar **RAGAS como dependencia opcional (`eval`) fuera del runtime**. Puede traer dependencias transitivas (incluido LangChain) pero **NO debe importarse en rutas de producción**. El juez se inyecta vía `LLMProvider` (puerto). Alternativa: **juez propio** basado en prompts y checklist (sin RAGAS) también soportado. Las métricas de calidad se ejecutan fuera de CI con proveedor real o local. Cada ejecución registra `provider` y `model`.
- **Rationale**: Cumple "evaluación sin llaves" (CI usa fake), evita contaminar runtime con deps opcionales pesadas, permite juez no atado a un único vendedor.
- **Alternatives**: Juez fijo (rechazado), solo métricas léxicas (insuficientes para fidelidad).

## 8) Inmutabilidad en PostgreSQL
- **Decision**: Tablas `ledger_entries`, `ledger_transactions`, `audit_events` de **solo inserción**.
  - **Triggers** `BEFORE UPDATE OR DELETE` que lanzan excepción (`RAISE EXCEPTION`) bloqueando ambas operaciones.
  - **TRUNCATE**: bloquear `TRUNCATE` sobre `ledger_entries`, `ledger_transactions` y `audit_events` (trigger `BEFORE TRUNCATE` o permiso/DDL restrictivo).
  - **Permisos**: usuario de aplicación sin `UPDATE`/`DELETE`/`TRUNCATE` sobre esas tablas; migraciones con rol privilegiado solo para creación.
  - **Integridad**: `ledger_entries.transaction_id` obligatorio; **trigger diferido** para validar suma de DEBIT = suma de CREDIT por `transaction_id`.
- **Expiración de borradores**: La expiración (`expires_at`) se **evalúa al confirmar** (`POST /drafts/{id}/confirm`). Si está expirado → rechazar con `409`, cancelar/actualizar estado a `expirada` sin mover dinero.
- **Idempotencia**: Clave de idempotencia de la transferencia confirmada es el **id del borrador**. `ledger_transactions.idempotency_key` único por `(from_user_id, from_account_id, idempotency_key)`.
- **Rationale**: Defensa en profundidad, auditable y alineado con los requisitos.
- **Alternatives**: Solo permisos (más fácil de eludir con rol incorrecto), solo lógica (insuficiente como garantía).
