---
id: escalamiento-contactos-y-horarios-interno
title: "Escalamiento: contactos y horarios internos"
access_level: interno
version: 1.0
---

# Escalamiento: contactos y horarios internos

## Horario de los equipos de revision

El equipo de revision de alertas opera en el mismo horario de ventanas
de procesamiento publicado por el producto. Los casos que llegan fuera
de la ultima ventana del dia quedan primero en la cola del siguiente
horario habil, sin perder su prioridad.

## Contactos internos de escalamiento

- Fraude en limites y transferencias: cola de revision de alertas, primer
  horario habil de atencion.
- Seguridad de cuenta y accesos: equipo de seguridad, escalamiento
  directo durante todas las ventanas.
- Disputas con reclamo legal: area de compliance, canal reservado
  durante el horario de atencion interno.

## Prioridad por horario

Los casos de escalamiento se ordenan por impacto en el usuario y por la
ventana de procesamiento en la que el monto esta retenido: los que
pausan una transferencia concreta van primero en el horario.

> Documento sintetico de WalletSim: billetera de demostracion sin datos reales.
