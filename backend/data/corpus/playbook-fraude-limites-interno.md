---
id: playbook-fraude-limites-interno
title: "Playbook de fraude: limites de transferencia"
access_level: interno
version: 1.0
---

# Playbook de fraude: limites de transferencia

Alertas de fraude asociadas a los limites de monto por cuenta y al
acumulado diario y mensual de transferencias.

## Patron: fraccionamiento de limite

Un emisor fracciona un monto grande en varias transferencias pequeñas
para no exceder el limite diario ni el mensual de su cuenta. Senales:
varias transferencias al mismo destinatario en la misma ventana de
procesamiento, montos que suman el limite disponible restante y concepto
de la operacion generico.

## Acciones del analista

1. Congelar la salida de la cuenta emisora durante la revision del
   acumulado; el saldo no se toca.
2. Comparar el monto total fraccionado contra el limite diario y mensual
   del nivel de la cuenta.
3. Si el patron se confirma, escalar al equipo de seguridad con la
   evidencia y notificar al usuario por el canal de atencion.

## Vocabulario del caso

El caso se documenta con los terminos del producto: limite diario y
limite mensual de la cuenta, monto de cada transferencia, acumulado por
dia calendario y saldo disponible. El limite restante de la cuenta se
recalcula despues de cada transferencia confirmada, igual que ve el
usuario en la aplicacion.

## Cierre

El caso se cierra con la cuenta liberada o bloqueada segun la
confirmacion del patron. Toda decision queda en la bitacora con su
justificacion.

> Documento sintetico de WalletSim: billetera de demostracion sin datos reales.
