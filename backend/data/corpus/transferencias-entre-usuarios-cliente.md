---
id: transferencias-entre-usuarios-cliente
title: Transferencias entre usuarios
access_level: cliente
version: 1.0
---

# Transferencias entre usuarios

## Preparar la transferencia

Toda transferencia nace como borrador: eliges destinatario, monto y
concepto, y revisas la comision antes de confirmar. El dinero no se mueve
con el borrador: solo cuando tu confirmas la operacion con el
identificador del borrador en la app.

## Destinatario por handle

Para protegerte de errores, el asistente entrega un handle opaco del
destinatario en lugar de datos de su cuenta. El handle vence a los 15
minutos y solo sirve para preparar el borrador, nunca para confirmarlo.

## Formato de referencia

Los conceptos de transferencia aceptan el formato interno de referencia
del producto:

```
REF-{nivel}-{secuencia}
```

donde el nivel es el tuyo (Bronce, Plata u Oro) y la secuencia es el
numero de operacion del mes. La referencia viaja en el historial para
facilitar tus reclamos.

## Confirmacion y estado

Al confirmar, la transferencia entra en la ventana de procesamiento y el
saldo se actualiza al aplicarse. Puedes seguir el estado desde el
historial de movimientos.

> Documento sintetico de WalletSim: billetera de demostracion sin datos reales.
