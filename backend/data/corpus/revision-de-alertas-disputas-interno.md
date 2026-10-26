---
id: revision-de-alertas-disputas-interno
title: "Revision de alertas: disputas"
access_level: interno
version: 1.0
---

# Revision de alertas: disputas

Cola de revision para disputas con senales de fraude o abuso del proceso
de reclamo.

## Criterios de entrada a revision

Una disputa entra a esta cola cuando el reclamo menciona un cargo con
patron de fraude, cuando el usuario acumula disputas resueltas en su
contra o cuando la evidencia presentada parece inconsistente con el
historial de la cuenta.

## Flujo de revision

1. Reabrir el reclamo original y el detalle del cargo cuestionado:
   monto, fecha, operacion origen.
2. Validar la evidencia aportada por el usuario contra los registros de
   la cuenta y del destinatario.
3. Resolver a favor o en contra con justificacion escrita; el reembolso
   solo se aprueba con respaldo completo.

## Escalamiento

Si la revision conecta dos o mas disputas de cuentas distintas con el
mismo patron, se escala al equipo de seguridad como posible fraude
organizado.

> Documento sintetico de WalletSim: billetera de demostracion sin datos reales.
