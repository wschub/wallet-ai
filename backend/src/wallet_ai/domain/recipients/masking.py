"""Enmascarado de nombres de destinatarios.

El usuario que busca ve lo minimo para reconocer a quien elige: el nombre de
pila completo y la inicial del apellido. Nunca el correo, ni ids de usuario o
de cuenta. Ejemplo: "María García" -> "María G***".
"""

from __future__ import annotations


def mask_display_name(name: str | None) -> str:
    parts = (name or "").split()
    if not parts:
        return "***"
    if len(parts) == 1:
        return f"{parts[0][0]}***"
    return f"{parts[0]} {parts[1][0]}***"
