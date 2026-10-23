"""Aislamiento de la confirmacion de borradores (T019).

La confirmacion es el unico punto que mueve dinero. Para que un asistente,
una herramienta MCP o un guardrail no puedan dispararla por accidente, se
verifica con `ast` que ningun modulo de `src/` importa `confirm_draft` salvo
`wallet_ai.interfaces.api.routers.drafts`.

Formas detectadas (todas las que permiten traer el simbolo o el modulo):
- `import wallet_ai.application.transfers.confirm_draft`
- `import wallet_ai.application.transfers.confirm_draft as cd`
- `from wallet_ai.application.transfers import confirm_draft`
- `from wallet_ai.application.transfers.confirm_draft import confirm_draft`
- imports relativos (`from ....application.transfers.confirm_draft import ...`),
  resueltos con el nivel y el paquete del modulo que los contiene
- `importlib.import_module("...confirm_draft")` / `__import__("...confirm_draft")`
  con cadenas constantes

El chequeo se prueba a si mismo en `tests/architecture/test_isolation_selftest.py`.

El contrato equivalente de import-linter esta en `pyproject.toml`
("Confirmation isolation").
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
ALLOWED = {Path("wallet_ai/interfaces/api/routers/drafts.py")}
TARGET = "wallet_ai.application.transfers.confirm_draft"
TARGET_SYMBOL = "confirm_draft"
# Paquete desde el que se puede importar el simbolo con un from-import corto.
TARGET_PACKAGE = "wallet_ai.application.transfers"
# Funciones de import dinamico cuyo argumento se inspecciona si es constante.
DYNAMIC_IMPORTERS = {"import_module", "__import__"}


def _is_target_module(name: str) -> bool:
    return name == TARGET or name.startswith(TARGET + ".")


def _module_name_for_path(path: Path, root: Path = SRC) -> str:
    """Nombre de modulo de un archivo bajo `root/` (para resolver relativos)."""
    rel = path.resolve().relative_to(root.resolve())
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts)


def _resolve_relative(module: str | None, level: int, current: str, is_package: bool) -> str:
    """Resuelve un import relativo a su nombre absoluto.

    `level` es el numero de puntos. El primer punto significa "el paquete que
    contiene este modulo"; para un paquete (`__init__.py`) el paquete contenedor
    es el propio paquete. Cada punto adicional sube un nivel.
    """
    if level == 0:
        return module or ""
    parts = current.split(".") if current else []
    if is_package:
        base_parts = parts
    else:
        base_parts = parts[:-1]
    up = level - 1
    if up:
        base_parts = base_parts[: len(base_parts) - up]
    base = ".".join(base_parts)
    if module:
        return f"{base}.{module}" if base else module
    return base


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dynamic_import_arg(node: ast.Call) -> str | None:
    """Devuelve la cadena de un `import_module(...)` / `__import__(...)`."""
    func = node.func
    name = None
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    if name not in DYNAMIC_IMPORTERS:
        return None
    if not node.args:
        return None
    return _const_str(node.args[0])


def imports_confirm_draft(tree: ast.AST, module_name: str, is_package: bool = False) -> bool:
    """True si el arbol importa (de cualquier forma) `confirm_draft`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_target_module(alias.name):
                    return True
        elif isinstance(node, ast.ImportFrom):
            resolved = (
                _resolve_relative(node.module, node.level, module_name, is_package)
                if node.level
                else (node.module or "")
            )
            if _is_target_module(resolved):
                return True
            # `from <paquete> import confirm_draft` (o alias del simbolo).
            if resolved == TARGET_PACKAGE:
                for alias in node.names:
                    if alias.name == TARGET_SYMBOL:
                        return True
        elif isinstance(node, ast.Call):
            arg = _dynamic_import_arg(node)
            if arg is not None and (
                _is_target_module(arg) or arg == TARGET_PACKAGE
            ):
                return True
    return False


def _iter_modules():
    for path in SRC.rglob("*.py"):
        yield path, path.relative_to(SRC), path.name == "__init__.py"


def find_offenders(root: Path = SRC) -> list[str]:
    """Modulos fuera de `ALLOWED` que importan `confirm_draft`."""
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        module_name = _module_name_for_path(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if imports_confirm_draft(tree, module_name, is_package=path.name == "__init__.py"):
            if rel not in ALLOWED:
                offenders.append(str(rel))
    return offenders


def test_only_drafts_router_imports_confirm_draft():
    offenders = find_offenders(SRC)
    assert offenders == [], (
        "Solo wallet_ai/interfaces/api/routers/drafts.py puede importar "
        f"confirm_draft; infractores: {offenders}"
    )


def test_allowed_module_really_imports_confirm_draft():
    """Sanity check: el guardian no pasa por estar mirando el modulo equivocado."""
    router = SRC / "wallet_ai/interfaces/api/routers/drafts.py"
    tree = ast.parse(router.read_text(encoding="utf-8"), filename=str(router))
    assert imports_confirm_draft(tree, _module_name_for_path(router)), (
        "drafts.py deberia importar confirm_draft"
    )
