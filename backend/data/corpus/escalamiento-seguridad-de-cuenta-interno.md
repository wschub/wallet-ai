---
id: escalamiento-seguridad-de-cuenta-interno
title: "Escalamiento: seguridad de cuenta"
access_level: interno
version: 1.0
---

# Escalamiento: seguridad de cuenta

Ruta de escalamiento cuando la revision de una alerta compromete la
seguridad de una cuenta de usuario.

## Niveles de escalamiento

1. Analista de primera linea: congela la salida de la cuenta, revisa la
   actividad sospechosa del acceso y los dispositivos registrados.
2. Equipo de seguridad: toma el caso si la actividad compromete la
   contrasena o el dispositivo de verificacion del usuario.
3. Respuesta de cuenta: si el acceso ajeno esta confirmado, se bloquea
   la cuenta, se invalida la contrasena y se fuerza la verificacion de
   identidad en la recuperacion.

## Evidencia del escalamiento

El analista adjunta la linea de tiempo: accesos, cambios de contrasena,
dispositivos nuevos y transferencias en ventana. Cada paso queda en la
bitacora con su justificacion y la decision final se notifica al
usuario por el canal de atencion.

## Reloj de escalamiento

Un caso de seguridad de cuenta no puede permanecer mas de dos ventanas
de procesamiento sin decision: al vencer, escala automaticamente al
equipo de seguridad.

> Documento sintetico de WalletSim: billetera de demostracion sin datos reales.
