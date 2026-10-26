---
id: playbook-fraude-comisiones-interno
title: "Playbook de fraude: comisiones y cargos"
access_level: interno
version: 1.0
---

# Playbook de fraude: comisiones y cargos

Alertas de fraude sobre la comision por operacion, el porcentaje
aplicado al monto y los cargos del producto.

## Patron: manipulacion de comision

Un actor intenta que el sistema aplique una comision menor o nula a una
transferencia alterando el monto declarado en pantalla o el costo
mostrado al usuario. Senales: discrepancia entre el monto confirmado y
el cargo registrado, o un porcentaje fuera de la tabla de rangos del
nivel de la cuenta.

## Patron: cargo fantasma

Cargos que aparecen en el historial sin una operacion de transferencia
asociada. Toda comision debe trazarse a una operacion origen: si no
existe, se trata como cargo fantasma y se abre revision inmediata.

## Acciones del analista

1. Verificar la operacion origen de cada cargo cuestionado en la
   disputa: monto, comision y fecha.
2. Recalcular el costo con la tabla de rangos vigente y comparar contra
   lo aplicado.
3. Escalar a seguridad si la diferencia es sistematica en varias
   cuentas.

> Documento sintetico de WalletSim: billetera de demostracion sin datos reales.
