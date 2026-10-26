---
id: revision-de-alertas-transferencias-interno
title: "Revision de alertas: transferencias"
access_level: interno
version: 1.0
---

# Revision de alertas: transferencias

Revision de transferencias confirmadas o en borrador que disparan
senales de riesgo antes de aplicarse en la ventana de procesamiento.

## Senales de riesgo

- Borradores repetidos al mismo destinatario con montos que escalan
  rapido entre ventanas.
- Confirmacion de la transferencia desde un dispositivo nuevo sin
  verificacion en dos pasos completada.
- Handle de destinatario usado por multiples emisores en la misma
  ventana de procesamiento.

## Acciones del analista

1. Pausar la transferencia en la ventana antes de que el monto se aplique
   al saldo; la pausa no rechaza la operacion, la retiene para revision.
2. Confirmar con el usuario el destinatario y el monto por el canal de
   atencion registrado.
3. Liberar o cancelar la operacion con la justificacion en la bitacora.

## Coordinacion con limites

Si la transferencia pausada excede el limite del nivel de la cuenta, el
analista coordina con el playbook de limites antes de liberar el monto.

> Documento sintetico de WalletSim: billetera de demostracion sin datos reales.
