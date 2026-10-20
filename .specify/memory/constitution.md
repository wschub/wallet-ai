<!--
Sync Impact Report
- Version change: (none) → 1.0.0 (initial ratification)
- Modified principles: N/A (initial version; no prior constitution)
- Added sections:
  - Core Principles (I–VII): Arquitectura Hexagonal, Offline-First y Proveedor
    Fake Determinista, Stack Asíncrono de Extremo a Extremo, Trazabilidad Total
    de Decisiones, Enmascaramiento de PII, Confirmación Humana para Movimientos
    de Dinero, Evaluación del RAG con Dataset Dorado
  - Flujo de Ingeniería
  - Transparencia y Documentación
  - Governance
- Removed sections: none
- Follow-up TODOs: none
-->

# Wallet AI Constitution

## Core Principles

### I. Arquitectura Hexagonal (NON-NEGOTIABLE)

- El núcleo del dominio NO importa tipos, SDKs ni módulos de proveedores
  externos; depende solo de abstracciones propias.
- El LLM, los embeddings y el reranker se consumen exclusivamente mediante
  puertos (interfaces Python definidas en el dominio).
- Cada proveedor es un adaptador tras un puerto, seleccionable y configurable
  por variables de entorno; cambiar de proveedor no toca el dominio.

Rationale: sustituir LLM, base vectorial o reranker sin reescribir el núcleo;
el dominio se prueba con dobles en memoria, sin red.

### II. Offline-First y Proveedor Fake Determinista

- El repo MUST correr completo sin llaves de API usando un proveedor fake
  determinista: las mismas entradas producen siempre las mismas salidas.
- Los tests y el CI MUST ser totalmente offline: ningún test llama a servicios
  externos y la configuración de CI no contiene secretos.

Rationale: reproducibilidad de la demo y del historial de evaluación; CI
predecible y sin costos ni rate-limits de terceros.

### III. Stack Asíncrono de Extremo a Extremo

- Python con FastAPI y async real: asyncpg y httpx, sin bloquear el event loop
  (prohibido `time.sleep`, drivers síncronos o I/O bloqueante en rutas async).
- El asistente MUST poder emitir respuestas en streaming por SSE cuando produce
  tokens o eventos del pipeline.

Rationale: la experiencia de un asistente conversacional exige streaming; un
bloqueo en cualquier capa degrada toda la aplicación.

### IV. Trazabilidad Total de Decisiones

- Cada request genera un `trace_id` único que se propaga explícitamente a
  retrieval, tool calls y llamadas al LLM; no existen flujos sin traza.
- Toda decisión del agente se audita y la auditoría se persiste en
  almacenamiento durable, asociada a su `trace_id`.
- Las piezas del pipeline reciben la traza por parámetro o contexto explícito,
  nunca por estado global.

Rationale: debugging de un sistema no determinista, accountability de cada
acción y prueba concreta del valor del proyecto en portafolio.

### V. Enmascaramiento de PII (NON-NEGOTIABLE)

- Toda PII MUST enmascararse antes de que el dato alcance al LLM y antes de
  escribirse en logs, trazas o auditoría.
- El valor real de la PII solo puede resolverse en el borde de presentación;
  el almacenamiento persistente NO guarda PII en claro.

Rationale: privacidad por defecto; ningún proveedor externo ni log debe contener
datos personales reconocibles.

### VI. Confirmación Humana para Movimientos de Dinero

- Ninguna acción que mueve dinero se ejecuta automáticamente: el agente solo
  crea borradores.
- Un borrador MUST aplicarse únicamente tras confirmación humana explícita, y
  la confirmación se registra en la auditoría junto a su `trace_id`.

Rationale: safety primero; aunque el proyecto usa solo datos sintéticos, el
patrón demostrable (borrador → confirmación → ejecución) es el que un revisor
espera ver en un sistema financiero.

### VII. Evaluación del RAG con Dataset Dorado

- Todo cambio en el pipeline RAG (chunking, embeddings, retrieval, reranking,
  prompts) MUST evaluarse contra el dataset dorado antes de fusionarse.
- Cada resultado de evaluación MUST registrar el proveedor y el modelo usados,
  además de las métricas y la traza del run.

Rationale: sin evaluación registrada no hay evidencia de mejora; permite
comparar proveedores y modelos con criterio, no de memoria.

## Flujo de Ingeniería

- Commits pequeños y progresivos siguiendo Conventional Commits (`feat:`,
  `fix:`, `test:`, `docs:`, `chore:`…); un commit, una idea.
- Cada hito (funcionalidad completa o cambio de pipeline RAG) MUST incluir sus
  tests: primero prueban en rojo, después implementan en verde.
- El historial de git narra la construcción del proyecto; se revisa como parte
  del entregable.

## Transparencia y Documentación

- El README MUST mantener un diagrama de arquitectura vigente (hexagonal:
  dominio, puertos, adaptadores) y una demo ejecutable sin llaves usando el
  proveedor fake.
- Toda decisión técnica no obvia se documenta como ADR corto: contexto,
  decisión, consecuencias; los ADRs vigentes se enlazan desde el README.

## Governance

- Esta constitución suprime cualquier práctica contradictoria; si un ADR la
  contradice, se enmienda la constitución antes que el ADR.
- Enmiendas: PR que modifica este archivo, incrementa la versión según
  versionado semántico (MAJOR: eliminación o redefinición de principios; MINOR:
  principio o sección nuevo; PATCH: clarificaciones) y describe el impacto en
  el reporte de sincronización; requiere aprobación del autor del repo.
- Revisión de cumplimiento: cada PR se contrasta contra estos principios; los
  gates de tests offline, evaluación RAG del pipeline y enmascaramiento de PII
  son bloqueantes para fusionar.
- La guía operativa (cómo ejecutar la demo, cómo correr la evaluación) vive en
  el README y los ADRs; esta constitución gobierna el qué y el porqué.

**Version**: 1.0.0 | **Ratified**: 2026-10-20 | **Last Amended**: 2026-10-20
