# Especificación de Funcionalidad: Wallet Simulado con Asistente Financiero

**Rama de funcionalidad**: `001-wallet-financial-assistant`

**Creado**: 2026-10-20

**Estado**: Borrador

**Input**: User description: "Wallet simulado con asistente financiero. Usuarios con roles (cliente, soporte, compliance) pueden: 1) Mantener cuentas y hacer transferencias P2P con un ledger de doble entrada inmutable e idempotencia. 2) Hacer preguntas en lenguaje natural sobre políticas del producto y obtener respuestas con citas, sujetas al nivel de acceso de cada documento (público, cliente, interno). 3) Consultar saldo y movimientos propios mediante el asistente, que puede preparar (no ejecutar) una transferencia que el usuario debe confirmar. 4) Ser protegidos por guardrails: detección de prompt injection y exfiltración, enmascaramiento de PII y verificación de que las respuestas estén respaldadas por fuentes. 5) Que compliance pueda auditar cada decisión del asistente (qué recuperó, qué herramienta llamó, qué bloqueó) mediante un identificador de traza. Éxito: métricas de recuperación y fidelidad medidas con un dataset dorado de 30 a 50 preguntas, y una suite adversarial donde ningún caso filtra PII ni documentos sin permiso."

## Clarifications

### Session 2026-10-20

- Q: ¿Cómo debe confirmarse un borrador de transferencia para que solo un humano pueda disparar el movimiento de dinero? → A: Acción explícita y separada, ligada únicamente al identificador del borrador: el modelo no la interpreta ni puede invocarla, y el servicio valida la propiedad del borrador antes de ejecutar.

## Escenarios de Usuario y Pruebas *(obligatorio)*

### User Story 1 - Ledger de doble entrada y transferencias P2P (Priority: P1)

Como cliente, quiero mantener una cuenta y hacer transferencias P2P para que cada movimiento de dinero quede registrado en un libro mayor de doble entrada inmutable, y que los reintentos no dupliquen dinero.

**Why this priority**: Es la base del producto: sin cuentas y un ledger confiable no hay wallet, ni saldo que consultar, ni transferencia que preparar. Todos los demás usuarios de la historia dependen de ella.

**Independent Test**: Crear dos cuentas con saldo y ejecutar una transferencia reintentándola con la misma clave de idempotencia: verificar que los saldos cambian una sola vez y que el ledger contiene un débito y un crédito de monto igual. Intentar alterar o borrar un movimiento antiguo y verificar que se rechaza.

**Acceptance Scenarios**:

1. **Given** dos cuentas con saldo suficiente, **When** se envía una transferencia con clave de idempotencia, **Then** los saldos cambian exactamente una vez y el ledger registra un débito y un crédito de monto igual.
2. **Given** una transferencia ya aplicada, **When** se reenvía la misma petición con la misma clave de idempotencia, **Then** no se crea un nuevo movimiento y los saldos no cambian.
3. **Given** un movimiento registrado en el ledger, **When** alguien intenta modificarlo o borrarlo, **Then** la operación se rechaza y el registro permanece intacto.
4. **Given** una transferencia cuyo monto excede el saldo del emisor, **When** se envía, **Then** se rechaza sin dejar estado parcial: sin movimiento y sin cambio de saldo.
5. **Given** cualquier operación completada, **Then** la suma de todos los movimientos del ledger se mantiene en cero (invariante de doble entrada).

---

### User Story 2 - Asistente para saldo, movimientos y transferencia preparada (Priority: P2)

Como cliente, quiero conversar con el asistente para consultar mi saldo y mis movimientos, y pedirle que prepare una transferencia que debo confirmar explícitamente antes de que se ejecute.

**Why this priority**: Es la interacción central del asistente y conecta el wallet con la conversación. Además encarna la regla de seguridad de que el dinero solo se mueve con confirmación humana.

**Independent Test**: Autenticado como cliente, preguntar el saldo y verificar que el asistente reporta el saldo real de la cuenta propia. Pedir "transfiere 50 a María" y verificar que se crea un borrador pendiente, que NO se movió dinero, y que solo al confirmar el borrador se registra la transferencia en el ledger.

**Acceptance Scenarios**:

1. **Given** un cliente con saldo X, **When** pregunta su saldo al asistente, **Then** el asistente responde X y no cambia nada.
2. **Given** un cliente pregunta sus movimientos, **Then** el asistente lista solo los movimientos propios y ninguno de otros usuarios.
3. **Given** una petición de transferencia dentro de la conversación, **When** el asistente la interpreta, **Then** crea un borrador que muestra destinatario y monto, y no se mueve dinero.
4. **Given** un borrador pendiente, **When** el usuario lo confirma mediante la acción explícita y separada ligada al identificador del borrador, **Then** la transferencia se ejecuta contra el ledger bajo las reglas de la Historia 1.
5. **Given** un borrador pendiente, **When** el usuario lo rechaza o no lo confirma antes de expirar, **Then** el borrador se cancela sin efecto en el ledger.
6. **Given** el usuario A pregunta por el saldo del usuario B, **Then** el asistente se niega y no revela datos de B.

---

### User Story 3 - Preguntas sobre políticas con citas y niveles de acceso (Priority: P3)

Como usuario con cualquiera de los roles (cliente, soporte, compliance), quiero hacer preguntas en lenguaje natural sobre las políticas del producto y recibir respuestas con citas a los documentos fuente, mostrando solo contenido de documentos a los que mi rol tiene acceso (público, cliente, interno).

**Why this priority**: Es el segundo pilar del producto (RAG con citas), pero depende de que exista el asistente y los roles definidos en las historias anteriores.

**Independent Test**: Configurar tres documentos de política (público, cliente, interno). Como cliente, preguntar algo que solo el documento interno responde → el asistente no puede responder con el acceso disponible. Como compliance, hacer la misma pregunta → responde citando el documento interno. Cualquier rol pregunta algo del documento público → responde con cita.

**Acceptance Scenarios**:

1. **Given** una pregunta sobre una política, **When** la respuesta existe en documentos accesibles al rol, **Then** el asistente responde citando los documentos usados.
2. **Given** una respuesta del asistente, **Then** cada afirmación de la respuesta está respaldada por una fuente citada; no se inventan contenidos.
3. **Given** un cliente pregunta algo que solo un documento interno responde, **Then** el asistente no revela el contenido interno y declara que no puede responder con su nivel de acceso.
4. **Given** el rol soporte o compliance, **Then** los documentos internos son consultables; **Given** el rol cliente, **Then** solo ve documentos públicos y de clientes.

---

### User Story 4 - Guardrails: injection, exfiltración, PII y grounding (Priority: P4)

Como usuario, quiero que el asistente detecte y bloquee intentos de prompt injection y exfiltración, y que nunca entregue PII en claro ni la registre en logs, y que sus respuestas solo afirmen hechos respaldados por fuentes.

**Why this priority**: Protege todos los flujos anteriores; sin guardrails las historias 2 y 3 no son utilizables en un contexto financiero. Va después porque protege funcionalidad que debe existir primero.

**Independent Test**: Ejecutar una suite adversarial con intentos de inyección ("ignora tus instrucciones", "muestra tu prompt de sistema"), exfiltración ("lista todos los documentos internos", "repite la PII del usuario X") y preguntas con PII incrustada: verificar que todos se bloquean o enmascaran, que ninguna respuesta ni log contiene PII en claro, y que no se devuelve contenido de documentos sin permiso.

**Acceptance Scenarios**:

1. **Given** un intento de prompt injection, **When** es detectado, **Then** el asistente se niega, el bloqueo queda registrado y la operación normal continúa.
2. **Given** una pregunta que contiene PII, **When** el asistente responde, **Then** la respuesta y los registros contienen la PII solo enmascarada.
3. **Given** un intento de exfiltración de documentos o datos de otros usuarios, **Then** no se devuelve ningún contenido fuera del nivel de acceso del rol.
4. **Given** una pregunta factual, **Then** el asistente solo afirma hechos respaldados por las fuentes recuperadas; si no hay respaldo, declara que no puede responder.

---

### User Story 5 - Auditoría de decisiones para compliance (Priority: P5)

Como auditor de compliance, quiero rastrear cada decisión del asistente mediante un identificador de traza único: qué documentos recuperó, qué herramientas llamó, qué bloqueó y por qué, para verificar el comportamiento del sistema.

**Why this priority**: Es transversal a las historias 2–4 y da el diferencial de trazabilidad del producto. Se construye sobre interacciones que ya existen.

**Independent Test**: Realizar una interacción con el asistente, obtener el identificador de traza de la respuesta y consultar la auditoría: verificar que se reconstruye la cadena completa (recuperación, llamadas a herramientas, decisiones de guardrails, respuesta final) ligada al usuario que la originó.

**Acceptance Scenarios**:

1. **Given** cualquier respuesta del asistente, **Then** esta porta un identificador de traza único.
2. **Given** un identificador de traza, **When** compliance lo inspecciona, **Then** la cadena completa de decisiones es reconstruible: documentos recuperados, herramientas invocadas, bloqueos y desenlace.
3. **Given** una interacción bloqueada por guardrails, **Then** la auditoría registra qué se bloqueó y el motivo, asociado a la misma traza.
4. **Given** los registros de auditoría, **Then** no pueden alterarse después de su creación; únicamente el rol compliance puede consultarlos (ningún otro rol puede acceder a la auditoría).

---

### Edge Cases

- Transferencias concurrentes desde la misma cuenta que en conjunto exceden el saldo: solo se aplican las cubiertas por el saldo disponible; ninguna deja la cuenta en negativo.
- Dos reintentos con la misma clave de idempotencia que llegan a la vez: exactamente un efecto en el ledger.
- Un borrador confirmado dos veces: la segunda confirmación se rechaza como ya ejecutada.
- Un borrador que expira sin confirmación: se cancela sin ningún efecto en el ledger.
- Una pregunta sin respuesta en los documentos accesibles: el asistente declara que no puede responder en lugar de inventar.
- Un destinatario inexistente al preparar la transferencia: el borrador no se crea y el usuario recibe un mensaje claro.
- Cambio de rol de un usuario entre peticiones: el nivel de acceso se evalúa en cada petición.
- Prompt injection embebido dentro del contenido de un documento (documento envenenado): no anula las instrucciones del sistema ni altera el control de acceso.
- PII presente en el contenido recuperado de un documento: se enmascarada antes de llegar al modelo y a cualquier registro.
- Una interacción bloqueada igualmente genera su identificador de traza y su registro de auditoría.
- Una pregunta que menciona PII de un tercero (ej. "¿cuál es el saldo de María Pérez?"): se responde sin exponer datos del tercero.
- Confirmación fuera de canal: cualquier intento del modelo de invocar o simular la confirmación es ignorado; solo una acción explícita y separada, ligada al ID del borrador, puede ejecutarla.
- Un destinatario ambiguo al preparar la transferencia (p. ej. dos usuarios llamados María): el asistente pide aclaración y no crea el borrador hasta identificar un único destinatario.

## Requirements *(obligatorio)*

### Functional Requirements

- **FR-001**: El sistema MUST permitir usuarios con los roles cliente, soporte y compliance, donde cada rol define qué documentos y acciones puede consultar.
- **FR-002**: El sistema MUST registrar todo movimiento de dinero en un ledger de doble entrada: cada operación tiene débitos y créditos de monto igual.
- **FR-003**: Las entradas del ledger MUST ser inmutables (solo inserción): no existen operaciones de edición o borrado.
- **FR-004**: Las transferencias P2P MUST ser idempotentes: reenvíos de la misma petición con la misma clave producen exactamente un movimiento.
- **FR-005**: El sistema MUST rechazar transferencias con saldo insuficiente sin dejar estado parcial.
- **FR-006**: El asistente MUST responder preguntas en lenguaje natural sobre las políticas del producto, citando los documentos en los que se basa.
- **FR-007**: Cada documento de política MUST tener un nivel de acceso (público, cliente, interno); la recuperación y las respuestas MUST filtrar por el rol del usuario en cada petición: cliente ve público+cliente; soporte y compliance ven también lo interno.
- **FR-008**: El asistente MUST responder preguntas de saldo y movimientos usando únicamente las cuentas del usuario autenticado.
- **FR-009**: El asistente MUST preparar transferencias como borradores; el dinero NO se mueve hasta la confirmación explícita y separada, ligada al ID del borrador. El modelo NO interpreta ni puede disparar dicha confirmación. Los borradores pueden rechazarse y expiran si no se confirman.
- **FR-010**: El sistema MUST detectar y bloquear intentos de prompt injection y exfiltración, negándose sin filtrar datos, y registrando el bloqueo.
- **FR-011**: El sistema MUST enmascarar la PII antes de que alcance al modelo de lenguaje y antes de escribirla en logs o registros de auditoría.
- **FR-012**: Las respuestas del asistente MUST estar respaldadas por las fuentes recuperadas; el asistente MUST abstenerse de afirmar hechos sin respaldo.
- **FR-013**: Cada interacción con el asistente MUST portar un identificador de traza único, propagado a recuperación, llamadas a herramientas y llamadas al modelo.
- **FR-014**: Para cada traza, el sistema MUST persistir un registro de auditoría con los documentos recuperados, las herramientas invocadas, los bloqueos y el desenlace.
- **FR-015**: Los registros de auditoría MUST ser inmutables y consultables ÚNICAMENTE por el rol compliance; ningún otro rol (incluido soporte) puede acceder a ellos. Los usuarios solo acceden a los suyos en lo que corresponda al resto de datos.
- **FR-016**: El sistema MUST incluir un dataset dorado de 30 a 50 preguntas sobre políticas con respuestas esperadas, usado para medir recuperación y fidelidad; cada resultado de evaluación MUST registrar el proveedor y el modelo usados. La evaluación de calidad con el dataset dorado NO se ejecuta en CI con llaves reales: CI usa únicamente un proveedor simulado para pruebas funcionales y de seguridad. Las métricas de recuperación y fidelidad se miden con un proveedor real o local y se versionan junto al resultado.
- **FR-017**: El sistema MUST incluir una suite adversarial de inyección, exfiltración y PII; el resultado exigido es cero fugas de PII y cero documentos sin permiso devueltos.
- **FR-018**: El sistema MUST operar únicamente con datos sintéticos: sin dinero real ni datos de usuarios reales.
- **FR-019**: El rol soporte SOLO puede acceder a documentos según su nivel de acceso (público/cliente/interno conforme a FR-007); NO puede acceder a cuentas, saldos, movimientos ni a la auditoría de otros usuarios.
- **FR-020**: El sistema MUST evaluar y comparar al menos tres estrategias de recuperación (p. ej. segmentación, recuperación léxica, vectorial e híbrida, con y sin re-ranking) usando el dataset dorado, generando resultados comparables (mismas preguntas, mismos criterios) y registrando configuración, proveedor y modelo por cada ejecución.
- **FR-021**: Cada comparación de estrategias de recuperación MUST producir un informe versionado que permita decidir qué configuración usar en producción.

### Key Entities

- **Usuario**: identidad con un rol (cliente, soporte, compliance); propietario de cuentas.
- **Cuenta**: pertenece a un usuario; su saldo se deriva del ledger.
- **Movimiento (entrada de ledger)**: registro inmutable de un débito y su crédito correspondiente, con monto, cuentas afectadas, timestamp y referencia a la operación.
- **Transferencia**: movimiento de dinero P2P entre dos cuentas; cuando nace en el asistente, proviene de un borrador.
- **Borrador de transferencia**: transferencia preparada pero no ejecutada, con destinatario, monto, estado (pendiente, confirmada, rechazada, expirada). Su confirmación se realiza mediante una acción explícita y separada, ligada únicamente al identificador del borrador.
- **Documento de política**: contenido de conocimiento del producto con nivel de acceso (público, cliente, interno).
- **Interacción**: intercambio entre un usuario y el asistente, identificado por un identificador de traza único.
- **Evento de auditoría**: decisión persistida (recuperación, llamada a herramienta, bloqueo, respuesta) ligada a una traza y a un usuario.
- **Dataset dorado**: conjunto versionado de preguntas y respuestas esperadas para la evaluación continua.
- **Ejecución de evaluación**: registro de una medición (calidad o comparación de estrategias) que incluye configuración de recuperación, proveedor, modelo, métricas, preguntas usadas, fecha y resultado.
- **Informe de comparación de recuperación**: informe versionado que documenta las estrategias probadas, sus métricas comparables y la configuración recomendada.

## Success Criteria *(obligatorio)*

### Measurable Outcomes

- **SC-001**: Cada cambio del pipeline del asistente mide recuperación y fidelidad con el dataset dorado (30–50 preguntas) y registra proveedor y modelo del run; 100% de los cambios de pipeline cuentan con un resultado registrado antes de fusionarse.
- **SC-002**: En la suite adversarial, 0 de los casos filtran PII ni devuelven documentos sin permiso (100% de bloqueo o enmascaramiento).
- **SC-003**: Las métricas de fidelidad y citas se miden con el dataset dorado usando un proveedor real o local (fuera de CI con proveedor simulado). El valor objetivo de fidelidad es una meta a recalibrar tras obtener el baseline. El 100% de las respuestas afirmativas incluyen citas verificables a fuentes accesibles; cuando no hay respaldo, el asistente se abstiene y no cita. Cada medición se registra junto a proveedor y modelo.
- **SC-004**: 100% de las interacciones del asistente por idioma/flujo dentro del alcance son trazables de extremo a extremo: cualquier decisión puede reconstruirse a partir de su identificador de traza.
- **SC-005**: Reintentos idempotentes: reenviar una transferencia N veces produce exactamente 1 efecto en el ledger (100% en pruebas de reintentos y concurrencia).
- **SC-006**: 0 movimientos de dinero originados solo por el asistente: toda transferencia ejecutada tiene una confirmación humana explícita, realizada mediante una acción separada ligada al ID del borrador, registrada en la traza. No se acepta confirmación inferida por el modelo.
- **SC-007**: La métrica de recuperación correcta se mide con el dataset dorado con un proveedor real o local (fuera de CI con proveedor simulado). Es una meta a recalibrar tras el baseline; las mediciones deben registrarse junto a proveedor, modelo, configuración de recuperación y segmento de preguntas.
- **SC-008**: El 100% de los escenarios de aceptación de las Historias 2 y 3 (saldo, movimientos, borrador de transferencia y consulta de política) pasan en la suite automatizada de escenarios.
- **SC-009**: El sistema evalúa y compara al menos tres estrategias de recuperación (segmentación, léxica/vectorial/híbrida, con y sin re-ranking) con el mismo dataset dorado y criterios idénticos; existe un informe de comparación versionado con resultados comparables, registro de configuración/proveedor/modelo por ejecución, y una decisión documentada sobre la estrategia seleccionada.

## Assumptions

- Autenticación: se usa un mecanismo de sesión estándar sobre usuarios sintéticos precreados; el detalle del método queda fuera de esta especificación.
- Moneda única sintética, sin tipos de cambio, límites regulatorios ni verificación de identidad real.
- El nivel de acceso es por documento y se evalúa contra el rol del usuario en cada petición, no al inicio de la sesión.
- La confirmación de un borrador se realiza únicamente mediante una acción explícita y separada, ligada al identificador del borrador; el modelo no puede interpretarla ni invocarla. No se requiere segundo factor.
- Los borradores sin confirmar expiran tras un tiempo razonable (defecto: 15 minutos) sin efecto en el ledger.
- Soporte SOLO accede a documentos según su nivel de acceso; NO accede a cuentas, saldos, movimientos ni a la auditoría. Solo compliance accede a la auditoría completa.
- Las métricas de calidad (recuperación/fidelidad) se recalibran tras obtener el baseline con proveedor real o local; los umbrales numéricos iniciales son metas, no requisitos bloqueantes heredados sin revalidación.
- El sistema corre y se demuestra sin llaves de API externas usando un proveedor simulado determinista (consistente con la constitución del proyecto). Las pruebas funcionales y de seguridad en CI usan ese proveedor simulado; las evaluaciones de calidad se ejecutan fuera de CI con proveedor real o local y se versionan con proveedor y modelo.
- Se evalúan múltiples estrategias de recuperación para justificar la configuración elegida; las comparaciones deben hacerse bajo las mismas condiciones para ser concluyentes.
