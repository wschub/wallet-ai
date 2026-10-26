---
id: playbook-fraude-patrones-interno
title: "Playbook de fraude: patrones y verificacion"
access_level: interno
version: 1.0
---

# Playbook de fraude: patrones y verificacion

Catalogo de patrones de fraude que cruzan los limites por cuenta, las
disputas y el acceso, con foco en la capa de verificacion del usuario.

## Patron: robo de codigo de verificacion

El atacante obtiene la contrasena y luego presiona al usuario para
revelar el codigo de verificacion en dos pasos. Senales: acceso desde un
dispositivo nuevo seguido de intentos fallidos de codigo y una
transferencia preparada en minutos.

## Patron: ingenieria social sobre soporte

El atacante contacta a atencion suplantando al usuario para pedir el
restablecimiento del acceso. Regla interna: soporte nunca reenvia
codigos de verificacion ni acepta la contrasena del usuario por ningun
canal; la verificacion de identidad es siempre por el flujo formal.

## Patron: cuenta dormida reactivada

Cuentas sin actividad que de pronto preparan transferencias por el
limite maximo disponible del nivel. La verificacion en dos pasos con
dispositivo nuevo es la senal dominante.

## Respuesta estandar

Congelar salida, forzar verificacion en dos pasos y escalar al equipo de
seguridad con la bitacora del acceso.

> Documento sintetico de WalletSim: billetera de demostracion sin datos reales.
