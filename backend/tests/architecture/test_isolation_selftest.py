"""Test del test de aislamiento (T019).

Construye un arbol `src/` temporal con un modulo ofensor por cada forma de
import y verifica que `find_offenders` detecta cada una. Ademas comprueba que
no hay falsos positivos: un modulo que solo menciona `confirm_draft` en un
comentario o en un docstring no debe marcarse.

Se importa el helper real (`find_offenders`, `imports_confirm_draft`) del test
de aislamiento para que la prueba valide exactamente el mismo codigo que corre
en CI, no una copia.
"""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

_ISOLATION_PATH = Path(__file__).with_name("test_confirmation_isolation.py")
_spec = importlib.util.spec_from_file_location("_isolation_guard", _ISOLATION_PATH)
assert _spec and _spec.loader
_isolation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_isolation)

ALLOWED = _isolation.ALLOWED
find_offenders = _isolation.find_offenders
imports_confirm_draft = _isolation.imports_confirm_draft

# Cada caso: (nombre, ruta relativa dentro de src, codigo).
OFFENDER_CASES = {
    # `import pkg.mod` directo.
    "import_absoluto": (
        "wallet_ai/application/assistant/abs_import.py",
        "import wallet_ai.application.transfers.confirm_draft\n",
    ),
    # `import pkg.mod as alias`.
    "import_absoluto_alias": (
        "wallet_ai/application/assistant/abs_alias.py",
        "import wallet_ai.application.transfers.confirm_draft as cd\n",
    ),
    # `from pkg.mod import simbolo`.
    "from_modulo": (
        "wallet_ai/application/assistant/from_mod.py",
        "from wallet_ai.application.transfers.confirm_draft import confirm_draft\n",
    ),
    # `from pkg import simbolo`.
    "from_paquete": (
        "wallet_ai/application/assistant/from_pkg.py",
        "from wallet_ai.application.transfers import confirm_draft\n",
    ),
    # Relativo: desde ...application.assistant (3 niveles) hacia transfers.
    "relativo": (
        "wallet_ai/application/assistant/rel.py",
        "from ..transfers.confirm_draft import confirm_draft\n",
    ),
    # Relativo con alias del simbolo.
    "relativo_alias": (
        "wallet_ai/application/assistant/rel_alias.py",
        "from ..transfers import confirm_draft as cd\n",
    ),
    # importlib.import_module con cadena constante.
    "importlib": (
        "wallet_ai/infrastructure/providers/dyn.py",
        'import importlib\n'
        'm = importlib.import_module("wallet_ai.application.transfers.confirm_draft")\n',
    ),
    # __import__ con cadena constante.
    "dunder_import": (
        "wallet_ai/infrastructure/guardrails/dyn2.py",
        'm = __import__("wallet_ai.application.transfers.confirm_draft")\n',
    ),
    # Relativo dentro de un paquete (`__init__.py`) que sube al modulo objetivo.
    "relativo_en_init": (
        "wallet_ai/interfaces/mcp/__init__.py",
        "from ...application.transfers.confirm_draft import confirm_draft\n",
    ),
}

# Modulos que NO deben marcarse (falsos positivos).
FALSE_POSITIVE_CASES = {
    # Solo menciona confirm_draft en un comentario.
    "solo_comentario": (
        "wallet_ai/application/assistant/comment.py",
        "# aqui NO importamos confirm_draft nunca\n"
        "x = 1  # confirm_draft\n",
    ),
    # Solo lo menciona en un docstring.
    "solo_docstring": (
        "wallet_ai/application/assistant/docstring.py",
        '"""Modulo que documenta confirm_draft sin importarlo."""\n'
        "y = 2\n",
    ),
    # Import no constante (variable): no debe tratarse como constante.
    "import_dinamico_no_constante": (
        "wallet_ai/infrastructure/providers/dyn_var.py",
        'import importlib\nname = "x"\nm = importlib.import_module(name)\n',
    ),
    # Una cadena que solo contiene el nombre pero no es el modulo objetivo.
    "cadena_parecida": (
        "wallet_ai/application/assistant/lookalike.py",
        'def confirm_draft_help():\n    return "confirm_draft"\n',
    ),
    # Import legitimo de otro modulo del proyecto.
    "import_legitimo": (
        "wallet_ai/application/assistant/legit.py",
        "from wallet_ai.application.transfers.list_drafts import list_drafts\n",
    ),
}


def _write_tree(root: Path, cases: dict) -> None:
    for _name, (rel, code) in cases.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(code), encoding="utf-8")


def _make_clean_tree(root: Path) -> None:
    """Un arbol minimo valido con solo el modulo permitido."""
    allowed = root / next(iter(ALLOWED))
    allowed.parent.mkdir(parents=True, exist_ok=True)
    allowed.write_text(
        "from wallet_ai.application.transfers.confirm_draft import confirm_draft\n",
        encoding="utf-8",
    )


def test_selftest_detects_every_offending_form(tmp_path: Path):
    _make_clean_tree(tmp_path)
    _write_tree(tmp_path, OFFENDER_CASES)

    offenders = set(find_offenders(tmp_path))
    expected = {rel for rel, _ in OFFENDER_CASES.values()}

    missing = expected - offenders
    assert not missing, f"Formas no detectadas: {sorted(missing)}"

    # El modulo permitido nunca debe contarse como infractor.
    assert not (set(ALLOWED) & offenders)


def test_selftest_no_false_positives(tmp_path: Path):
    _make_clean_tree(tmp_path)
    _write_tree(tmp_path, FALSE_POSITIVE_CASES)

    offenders = find_offenders(tmp_path)
    assert offenders == [], f"Falsos positivos detectados: {offenders}"


def test_selftest_each_form_individually(tmp_path: Path):
    """Cada forma, aislada en su propio arbol, se detecta (una por una)."""
    for name, (rel, code) in OFFENDER_CASES.items():
        case_root = tmp_path / name
        _make_clean_tree(case_root)
        path = case_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(code), encoding="utf-8")

        offenders = find_offenders(case_root)
        assert rel in offenders, f"Forma '{name}' no detectada en {rel}"


def test_selftest_helper_on_single_module():
    """El helper de bajo nivel tambien reconoce la forma 'from paquete import'."""
    import ast

    code = "from wallet_ai.application.transfers import confirm_draft\n"
    tree = ast.parse(code)
    assert imports_confirm_draft(tree, "wallet_ai.infrastructure.providers.fake")
