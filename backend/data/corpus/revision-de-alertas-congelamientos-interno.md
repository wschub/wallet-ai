---
id: revision-de-alertas-congelamientos-interno
title: "Revision de alertas: congelamientos"
access_level: interno
version: 1.0
---

# Revision de alertas: congelamientos

Revision de cuentas congeladas por el propio usuario y de congelamientos
aplicados automaticamente por una alerta de seguridad.

## Congelamiento y seguridad de la cuenta

Congelar es la accion de seguridad que el propio usuario ve en la
aplicacion: si nota una actividad sospechosa en su cuenta, congela y el
movimiento de salida se detiene al instante, como recomienda la politica
de seguridad de la cuenta. La revision interna confirma que el
congelamiento fue voluntario: sesion activa del titular, dispositivo de
confianza y sin intentos de descongelar desde otro canal. Descongelar no
requiere escalado, solo la verificacion normal del titular.

## Congelamiento automatico

El sistema congela la cuenta cuando una alerta de seguridad lo amerita:
acceso sospechoso, codigo de verificacion fallido repetido o transferencia
en revision. En este caso el analista revisa la evidencia de la alerta
antes de liberar el movimiento.

## Regla de salida

Ninguna cuenta congelada por alerta se descongela sin decision escrita
del analista con su justificacion. El congelamiento nunca borra saldo ni
historial: solo detiene la salida de transferencias.

> Documento sintetico de WalletSim: billetera de demostracion sin datos reales.
