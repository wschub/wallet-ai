"""Propiedades de los segmentadores (tramo A de US3, T047/T049).

Dos implementaciones tras el puerto `Chunker`:

- `FixedSizeChunker`: tamanho fijo en caracteres con solapamiento, cortando
  siempre en limite de palabra, nunca dentro de un bloque de codigo.
- `StructuralChunker`: por encabezados markdown, con respaldo por tamanho
  cuando una seccion excede el maximo.

Propiedades exigidas a AMBAS (sin hypothesis: textos pseudoaleatorios con
semilla fija + casos extremos):

1. Ningun fragmento vacio.
2. Ningun fragmento supera el maximo (`size`).
3. El texto queda cubierto: sin solapamiento la union es exacta
   (lossless con espacios simples); con solapamiento ningun token se
   pierde.
4. Nunca se parte una palabra (los tokens del fragmento son subsecuencia
   contigua de los tokens del original) salvo el ultimo recurso
   documentado: un token mas largo que `size` se corta a `size`.
5. Deterministas: misma entrada produce siempre la misma salida.
6. Los bloques de codigo cercados no se parten por la mitad.
"""

from __future__ import annotations

import random

import pytest

from wallet_ai.infrastructure.chunking import build_chunker
from wallet_ai.infrastructure.chunking.fixed import FixedSizeChunker
from wallet_ai.infrastructure.chunking.structural import StructuralChunker

FENCE = "```"

WORDS = [
    "transferencia", "comision", "limite", "cuenta", "disputa", "seguridad", "horario",
    "monto", "reembolso", "verificacion", "documento", "politica", "usuario", "saldo",
    "tarjeta", "soporte", "escalado", "alerta", "fraude", "revision", "borrador",
    "confirmacion", "cargo", "reclamo", "moneda", "billetera", "walletsim", "demostracion",
]

# Semillas fijas: los mismos "casuales" en cada corrida (determinismo del test).
SEEDS = [1, 7, 42, 2026]


def _text(seed: int, n_words: int, sep: str = " ") -> str:
    rnd = random.Random(seed)
    return sep.join(rnd.choice(WORDS) for _ in range(n_words))


def _messy_text(seed: int) -> str:
    """Texto con parrafos, encabezados y un bloque de codigo cercado pequeno.

    El bloque cabe en la ventana del chunker mas pequeno de los casos (37
    caracteres): la garantia verificable es que un bloque que CABE nunca se
    parte; el que NO cabe se prueba aparte con el ultimo recurso.
    """
    rnd = random.Random(seed)
    parts = []
    for para in range(4):
        parts.append(" ".join(rnd.choice(WORDS) for _ in range(12)))
    text = "# Titulo de prueba\n\n"
    text += "\n\n".join(parts) + "\n\n"
    text += "## Seccion con codigo\n\n"
    text += FENCE + "python\n"
    text += "monto(cuenta)\n"
    text += FENCE + "\n\n"
    text += " ".join(rnd.choice(WORDS) for _ in range(10))
    return text


def _fence_marker_count(chunk: str) -> int:
    return sum(1 for line in chunk.split("\n") if line.lstrip().startswith(FENCE))


def _is_contiguous_subsequence(needle: list[str], haystack: list[str]) -> bool:
    m, n = len(needle), len(haystack)
    if m == 0:
        return True
    return any(haystack[i : i + m] == needle for i in range(n - m + 1))


CHUNKER_CASES = [
    ("fijo-60-0", lambda: FixedSizeChunker(60, 0)),
    ("fijo-60-20", lambda: FixedSizeChunker(60, 20)),
    ("fijo-37-11", lambda: FixedSizeChunker(37, 11)),
    ("estructural-120-0", lambda: StructuralChunker(120, 0)),
    ("estructural-120-24", lambda: StructuralChunker(120, 24)),
    ("estructural-90-10", lambda: StructuralChunker(90, 10)),
]


# ---------------------------------------------------------------------------
# Propiedades comunes (textos generados y casos extremos)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,make", CHUNKER_CASES, ids=[c[0] for c in CHUNKER_CASES])
def test_no_empty_chunks(name, make):
    chunker = make()
    for seed in SEEDS:
        for text in (_text(seed, 60), _messy_text(seed)):
            for chunk in chunker.chunk(text):
                assert chunk.strip()
                assert chunk == chunk.strip()


@pytest.mark.parametrize("name,make", CHUNKER_CASES, ids=[c[0] for c in CHUNKER_CASES])
def test_max_size_respected(name, make):
    chunker = make()
    size = chunker.size
    for seed in SEEDS:
        for text in (_text(seed, 200), _messy_text(seed)):
            for chunk in chunker.chunk(text):
                assert len(chunk) <= size


@pytest.mark.parametrize("name,make", CHUNKER_CASES, ids=[c[0] for c in CHUNKER_CASES])
def test_deterministic(name, make):
    chunker = make()
    other = make()
    for seed in SEEDS:
        for text in (_text(seed, 80), _messy_text(seed)):
            assert chunker.chunk(text) == chunker.chunk(text)
            assert chunker.chunk(text) == other.chunk(text)


def test_empty_and_whitespace_only_texts_produce_no_chunks():
    for make in (lambda: FixedSizeChunker(50, 10), lambda: StructuralChunker(50, 10)):
        chunker = make()
        assert chunker.chunk("") == []
        assert chunker.chunk("   \n\t  \n") == []


@pytest.mark.parametrize("name,make", CHUNKER_CASES, ids=[c[0] for c in CHUNKER_CASES])
def test_code_fences_never_split_in_half(name, make):
    chunker = make()
    text = _messy_text(3)
    chunks = chunker.chunk(text)
    assert chunks, "debe producir fragmentos"
    for chunk in chunks:
        assert _fence_marker_count(chunk) % 2 == 0, f"bloque de codigo partido: {chunk!r}"


# ---------------------------------------------------------------------------
# Cobertura exacta / perdida / solapamiento (texto canonico de espacios simples)
# ---------------------------------------------------------------------------

def test_fixed_lossless_without_overlap():
    chunker = FixedSizeChunker(60, 0)
    for seed in SEEDS:
        text = _text(seed, 120)
        chunks = chunker.chunk(text)
        # La reconstruccion exacta prueba a la vez cobertura total y
        # disyuncion: si hubiera huecos o solapamientos, la union no
        # reproduciria el original caracter a caracter.
        assert " ".join(chunks) == text


def test_fixed_covers_every_token_with_overlap():
    from collections import Counter

    chunker = FixedSizeChunker(60, 20)
    for seed in SEEDS:
        text = _text(seed, 150)
        tokens = Counter(text.split())
        chunks = chunker.chunk(text)
        covered = Counter(tok for chunk in chunks for tok in chunk.split())
        assert not tokens - covered, "faltan tokens por cubrir"
        # Con solapamiento, los pares consecutivos comparten tokens cuando hay
        # material suficiente: al menos un par debe solaparse.
        assert any(set(a.split()) & set(b.split()) for a, b in zip(chunks, chunks[1:]))


def test_fixed_overlap_makes_next_chunk_restart_inside_previous_tail():
    """Solape POSICIONAL, no colision de valores.

    Con overlap > 0 cada fragmento recomienza con material ya cubierto: su
    primera palabra es la ultima del fragmento anterior (el solapamiento se
    ajusta al limite de palabra por debajo del maximo). Comparar conjuntos
    de valores (como arriba) no basta: con un vocabulario pequeno dos
    fragmentos sin solape comparten palabras por puro azar.
    """
    chunker = FixedSizeChunker(60, 20)
    for seed in SEEDS:
        chunks = chunker.chunk(_text(seed, 150))
        assert len(chunks) > 1
        for a, b in zip(chunks, chunks[1:]):
            assert a.split()[-1] == b.split()[0], (
                f"el fragmento siguiente no recomienza dentro del solapamiento: {b!r} tras {a!r}"
            )


@pytest.mark.parametrize("name,make", CHUNKER_CASES, ids=[c[0] for c in CHUNKER_CASES])
def test_chunks_are_contiguous_token_subsequences(name, make):
    chunker = make()
    for seed in SEEDS:
        text = _text(seed, 100)
        tokens = text.split()
        for chunk in chunker.chunk(text):
            needle = chunk.split()
            assert _is_contiguous_subsequence(needle, tokens), f"fragmento no contiguo: {chunk!r}"


def test_fixed_hard_splits_pathological_oversized_word():
    """Ultimo recurso documentado: una palabra mas larga que `size` se corta.

    Se prefiere romper la palabra antes que violar el maximo: la alternativa
    (un fragmento sobredimensionado) romperia los limites del backend de
    embeddings.
    """
    from collections import Counter

    size = 20
    chunker = FixedSizeChunker(size, 0)
    word = "a" * 57
    chunks = chunker.chunk(word)
    assert all(len(c) <= size for c in chunks)
    assert all(c for c in chunks)
    assert "".join(chunks) == word

    # Con texto mezclado tambien se respeta el maximo y no se pierde contenido.
    text = "uno dos " + "b" * 45 + " tres cuatro cinco"
    chunks = chunker.chunk(text)
    assert all(len(c) <= size for c in chunks)
    original_chars = Counter(ch for ch in text if not ch.isspace())
    stored_chars = Counter(ch for chunk in chunks for ch in chunk if not ch.isspace())
    assert not original_chars - stored_chars


def test_short_text_is_one_chunk():
    text = "monto limite comision"
    assert FixedSizeChunker(100, 20).chunk(text) == [text]
    assert StructuralChunker(100, 20).chunk(text) == [text]


# ---------------------------------------------------------------------------
# Comportamiento especifico del estructural
# ---------------------------------------------------------------------------

def test_structural_splits_by_headers_keeping_them():
    text = (
        "# Politica de limites\n\n"
        "El limite diario de transferencia es un monto fijo por cuenta.\n\n"
        "## Comisiones\n\n"
        "Las comisiones se calculan sobre el monto de cada transferencia.\n\n"
        "### Disputas\n\n"
        "Una disputa inicia con el reclamo del usuario sobre un cargo.\n"
    )
    chunks = StructuralChunker(400, 50).chunk(text)
    assert chunks == [
        "# Politica de limites\n\nEl limite diario de transferencia es un monto fijo por cuenta.",
        "## Comisiones\n\nLas comisiones se calculan sobre el monto de cada transferencia.",
        "### Disputas\n\nUna disputa inicia con el reclamo del usuario sobre un cargo.",
    ]
    assert all(c.startswith("#") for c in chunks)


def test_structural_preamble_becomes_own_chunk():
    text = "Parrafo previo al primer encabezado.\n\n# Titulo\n\nCuerpo de la seccion."
    chunks = StructuralChunker(400, 0).chunk(text)
    assert chunks[0] == "Parrafo previo al primer encabezado."
    assert chunks[1] == "# Titulo\n\nCuerpo de la seccion."


def test_structural_oversized_section_falls_back_to_size():
    body = "\n".join(f"L{n} comision limite monto cuenta transferencia reclamo {n}" for n in range(40))
    text = "# Seccion enorme\n\n" + body
    chunker = StructuralChunker(120, 0)
    chunks = chunker.chunk(text)
    assert len(chunks) > 1
    assert all(len(c) <= chunker.size for c in chunks)
    assert chunks[0].startswith("# Seccion enorme")
    tokens = text.split()
    for chunk in chunks:
        assert _is_contiguous_subsequence(chunk.split(), tokens)


def test_structural_oversized_code_block_respects_max():
    """Un bloque que NO cabe se parte por el respaldo documentado.

    No se puede exigir cercas balanceadas por fragmento cuando el propio
    bloque excede el maximo: lo verificable es que el maximo se respeta y
    que no se pierde contenido.
    """
    from collections import Counter

    code = FENCE + "python\n" + "\n".join(f"linea_de_codigo_{i}(monto, cuenta)" for i in range(30)) + "\n" + FENCE
    text = "# Seccion con codigo enorme\n\n" + code + "\n\nTexto final de la seccion."
    chunker = StructuralChunker(100, 0)
    chunks = chunker.chunk(text)
    assert len(chunks) > 1
    assert all(len(c) <= chunker.size for c in chunks)
    assert all(c for c in chunks)
    original_chars = Counter(ch for ch in text if not ch.isspace())
    stored_chars = Counter(ch for chunk in chunks for ch in chunk if not ch.isspace())
    assert not original_chars - stored_chars


def test_structural_small_code_block_never_split_even_in_oversized_section():
    """Un bloque que SI cabe nunca se parte, aunque la seccion sea enorme."""
    code = FENCE + "python\nmonto(cuenta)\n" + FENCE
    filler = "\n".join(f"L{n} comision limite monto cuenta transferencia reclamo {n}" for n in range(30))
    text = "# Seccion enorme con codigo pequeno\n\n" + filler + "\n\n" + code + "\n\n" + filler
    chunker = StructuralChunker(120, 0)
    chunks = chunker.chunk(text)
    assert len(chunks) > 1
    assert all(len(c) <= chunker.size for c in chunks)
    assert all(_fence_marker_count(c) % 2 == 0 for c in chunks)
    assert any(_fence_marker_count(c) == 2 for c in chunks), "el bloque pequeño debe salir entero en un fragmento"


# ---------------------------------------------------------------------------
# Fabrica y validacion de parametros
# ---------------------------------------------------------------------------

def test_build_chunker_factory():
    assert isinstance(build_chunker("fijo", 100, 20), FixedSizeChunker)
    assert isinstance(build_chunker("estructural", 100, 20), StructuralChunker)
    with pytest.raises(ValueError, match="estrategia"):
        build_chunker("por-oraciones", 100, 20)


@pytest.mark.parametrize("cls", [FixedSizeChunker, StructuralChunker])
def test_invalid_parameters_rejected(cls):
    with pytest.raises(ValueError):
        cls(0, 0)
    with pytest.raises(ValueError):
        cls(-5, 0)
    with pytest.raises(ValueError):
        cls(100, -1)
    with pytest.raises(ValueError):
        cls(100, 100)
    with pytest.raises(ValueError):
        cls(100, 150)
