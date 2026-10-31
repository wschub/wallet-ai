"""Propiedades de los segmentadores (tramo A de US3, T047/T049).

Dos implementaciones tras el puerto `Chunker`:

- `FixedSizeChunker`: tamanho fijo en caracteres con solapamiento, cortando
  siempre en limite de palabra, nunca dentro de un bloque de codigo.
- `StructuralChunker` (v2, `CHUNKER_VERSION = "2"`): por encabezados
  markdown, con union de secciones cortas y respaldo por tamanho cuando
  una seccion (o una union) excede el maximo.

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

Propiedades especificas del estructural v2 (correccion de secciones
demasiado pequenas para el retrieval):

7. Las secciones de menos de `min_size` caracteres (nuevo parametro, por
   defecto 120) se unen a la SIGUIENTE; si la union sigue corta, sigue
   acumulando; el preambulo (con el titulo H1) se une a la primera
   seccion; la ULTIMA seccion corta se une a la anterior. Ningun
   fragmento queda por debajo del umbral salvo que el documento completo
   sea mas corto.
8. La union nunca rompe el maximo (si lo supera, se respeta el respaldo
   por tamanho) y nunca cruza documentos: la union ocurre dentro del
   texto de un unico documento, y el titulo sigue en el primer fragmento.
   `min_size=0` desactiva la union (comportamiento v1). `CHUNKER_VERSION`
   es la version que las builds estructurales registran en `chunk_params`.
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


def _para(target: int, seed: int = 0) -> str:
    """Parrafo de al menos `target` caracteres (hasta la palabra que lo cruza)."""
    rnd = random.Random(seed)
    words: list[str] = []
    length = 0
    while length < target:
        word = rnd.choice(WORDS)
        words.append(word)
        length += len(word) + 1
    return " ".join(words)


def _sections_doc(seed: int) -> str:
    """Documento tipo corpus: titulo H1 con preambulo y secciones H2 de
    tamanho variado (incluidas secciones muy cortas que obligan a unir)."""
    rnd = random.Random(seed)
    parts = ["# Titulo del documento de prueba"]
    parts.append(_para(rnd.randint(30, 280), seed))
    for n in range(1, rnd.randint(3, 6)):
        parts.append(f"## Seccion {n} de la politica")
        parts.append(_para(rnd.randint(30, 280), seed * 31 + n))
    return "\n\n".join(parts)


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
    # estres: min_size mayor que size fuerza uniones que desbordan el maximo
    ("estructural-150-30-min200", lambda: StructuralChunker(150, 30, min_size=200)),
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
    """Con secciones que alcanzan min_size, cada encabezado conserva su
    propia seccion y viaja con ella (particion por encabezados)."""
    text = (
        "# Politica de limites\n\n" + _para(200, 41) + "\n\n"
        "## Comisiones\n\n" + _para(200, 42) + "\n\n"
        "### Disputas\n\n" + _para(200, 43)
    )
    chunks = StructuralChunker(600, 0).chunk(text)
    assert chunks == [
        "# Politica de limites\n\n" + _para(200, 41),
        "## Comisiones\n\n" + _para(200, 42),
        "### Disputas\n\n" + _para(200, 43),
    ]
    assert all(c.startswith("#") for c in chunks)


def test_structural_merges_short_sections_forward():
    """Las secciones de menos de min_size se unen a la SIGUIENTE; si la
    union sigue corta, sigue acumulando (determinista)."""
    text = (
        "# Politica de limites\n\n" + _para(60, 1) + "\n\n"   # ~90 < 120
        "## Nivel Bronce\n\n" + _para(60, 2) + "\n\n"         # ~90 < 120
        "## Nivel Plata\n\n" + _para(200, 3) + "\n\n"         # ~230
        "## Nivel Oro\n\n" + _para(200, 4)                    # ~230
    )
    chunker = StructuralChunker(600, 0)
    chunks = chunker.chunk(text)
    assert len(chunks) == 3
    assert all(len(c) >= chunker.min_size for c in chunks)
    assert chunks[0].startswith("# Politica de limites")
    assert "## Nivel Bronce" in chunks[0]
    assert "## Nivel Plata" not in chunks[0]
    assert chunks[1].startswith("## Nivel Plata")
    assert chunks[2].startswith("## Nivel Oro")
    assert " ".join(chunks).split() == text.split()


def test_structural_short_preamble_merges_with_h1_section():
    """El preambulo (texto previo al primer encabezado) corto se une a la
    seccion del titulo H1: el titulo viaja en el primer fragmento."""
    text = (
        "Parrafo previo corto.\n\n"
        "# Titulo del documento\n\n" + _para(80, 45) + "\n\n"
        "## Seccion\n\n" + _para(200, 46)
    )
    chunks = StructuralChunker(600, 0).chunk(text)
    assert len(chunks) == 2
    assert chunks[0].startswith("Parrafo previo corto.")
    assert "# Titulo del documento" in chunks[0]
    assert chunks[1].startswith("## Seccion")


def test_structural_long_preamble_joins_h1_section_keeping_title_first():
    """El preambulo se une SIEMPRE a la seccion del titulo H1 (no solo
    cuando es corto): aunque alcance `min_size`, no forma un fragmento
    propio que dejaria el titulo fuera del primer fragmento."""
    preamble = _para(200, 47)  # >= min_size (120)
    h1_section = "# Titulo del documento\n\n" + _para(150, 48)
    text = preamble + "\n\n" + h1_section + "\n\n## Seccion\n\n" + _para(200, 49)
    chunks = StructuralChunker(600, 0).chunk(text)
    assert chunks[0].startswith(preamble)
    assert "# Titulo del documento" in chunks[0], "el titulo debe seguir en el primer fragmento"
    assert all(len(c) <= 600 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_structural_last_short_section_merges_with_previous():
    """La ultima seccion corta (sin siguiente) se une a la ANTERIOR."""
    text = "# Politica\n\n" + _para(200, 5) + "\n\n## Cierre\n\nNota final breve."
    chunks = StructuralChunker(600, 0).chunk(text)
    assert len(chunks) == 1
    assert chunks[0].startswith("# Politica")
    assert chunks[0].endswith("Nota final breve.")


def test_structural_merge_fits_within_max_becomes_one_chunk():
    """La union que CABE en el maximo es un unico fragmento."""
    text = (
        "# Titulo corto\n\n" + _para(60, 6) + "\n\n"   # ~90 < 120
        "## Seccion\n\n" + _para(350, 7)                # ~370
    )
    chunks = StructuralChunker(600, 0).chunk(text)
    assert len(chunks) == 1
    assert "# Titulo corto" in chunks[0]
    assert "## Seccion" in chunks[0]
    assert len(chunks[0]) <= 600


def test_structural_merge_exceeding_max_respects_size_fallback():
    """Si la union supera el maximo, se respeta el respaldo por tamanho:
    ningun fragmento supera `size`, no se pierde texto y el orden se
    conserva (hacia adelante y hacia atras)."""
    chunker = StructuralChunker(600, 0)

    # Hacia adelante: seccion corta + seccion casi llena > 600.
    text = (
        "# Titulo corto\n\n" + _para(60, 8) + "\n\n"   # ~90 < 120
        "## Seccion larga\n\n" + _para(530, 9)          # ~560
    )
    chunks = chunker.chunk(text)
    assert len(chunks) >= 2
    assert all(len(c) <= chunker.size for c in chunks)
    assert chunks[0].startswith("# Titulo corto")
    assert "## Seccion larga" in chunks[0], "la union cayo dentro del respaldo por tamanho"
    assert " ".join(chunks).split() == text.split()

    # Hacia atras: ultima corta unida a una seccion casi llena > 600.
    text = (
        "# Politica\n\n" + _para(537, 10) + "\n\n"      # ~555
        "## Cierre\n\n" + _para(70, 11)                # ~100 < 120
    )
    chunks = chunker.chunk(text)
    assert len(chunks) >= 2
    assert all(len(c) <= chunker.size for c in chunks)
    assert chunks[0].startswith("# Politica")
    assert "## Cierre" in chunks[0], "la union hacia atras cayo dentro del respaldo"
    assert " ".join(chunks).split() == text.split()


def test_structural_title_stays_in_first_chunk():
    """El titulo H1 abre el primer fragmento aunque su seccion sea corta
    (se une a la siguiente) o aunque la unidad resultante exceda el
    maximo (el respaldo conserva el orden)."""
    text = (
        "# Limites de transferencia\n\n" + _para(60, 12) + "\n\n"   # ~95 < 120
        "## Limite diario\n\n" + _para(300, 13) + "\n\n"            # ~330
        "## Limite mensual\n\n" + _para(300, 14)                    # ~340
    )
    chunks = StructuralChunker(600, 0).chunk(text)
    assert chunks[0].startswith("# Limites de transferencia")
    assert "## Limite diario" in chunks[0], "la seccion corta del titulo se une a la siguiente"

    # La unidad del titulo excede el maximo: el respaldo mantiene el titulo primero.
    chunks = StructuralChunker(120, 0).chunk(text)
    assert all(len(c) <= 120 for c in chunks)
    assert chunks[0].startswith("# Limites de transferencia")


def test_structural_no_chunks_below_80_unless_whole_doc_shorter():
    """Sobre documentos tipo corpus: ningun fragmento de menos de 80
    caracteres, se respeta el maximo, todo el texto queda cubierto y el
    titulo viaja en el primer fragmento."""
    chunker = StructuralChunker(600, 0)
    for seed in SEEDS:
        text = _sections_doc(seed)
        chunks = chunker.chunk(text)
        assert all(len(c) <= chunker.size for c in chunks)
        assert all(len(c) >= 80 for c in chunks), (
            f"seed={seed}: fragmentos demasiado pequenos: "
            f"{[len(c) for c in chunks]}"
        )
        assert " ".join(chunks).split() == text.split()
        assert chunks[0].startswith("# Titulo del documento de prueba")

    # Una seccion explicitamente diminuta no sobrevive como fragmento propio.
    text = (
        "# Titulo del documento de prueba\n\n" + _para(200, 21) + "\n\n"
        "## Seccion peque\n\nmini\n\n"
        "## Otra seccion\n\n" + _para(200, 22)
    )
    chunks = chunker.chunk(text)
    assert all(len(c) >= 80 for c in chunks)
    assert " ".join(chunks).split() == text.split()

    # Excepcion: el documento COMPLETO es mas corto.
    tiny = "# Titulo\n\nParrafo minimo del documento."
    chunks = chunker.chunk(tiny)
    assert len(chunks) == 1
    assert len(chunks[0]) < 80


def test_structural_merge_never_crosses_documents():
    """La union de secciones ocurre DENTRO de un unico texto: la ingesta
    segmenta cada documento por separado y ningun fragmento mezcla
    contenido de dos documentos."""
    tail = "## Cierre\n\nNota final breve."  # corta: se une a la ANTERIOR de su documento
    doc_a = "# Politica A\n\n" + _para(200, 15) + "\n\n" + tail
    doc_b = "# Politica B\n\n" + _para(200, 16) + "\n\n" + tail
    chunker = StructuralChunker(600, 0)
    chunks_a, chunks_b = chunker.chunk(doc_a), chunker.chunk(doc_b)
    assert chunks_a == [doc_a.strip()]
    assert chunks_b == [doc_b.strip()]
    for chunk in chunks_a:
        assert "Politica B" not in chunk
    for chunk in chunks_b:
        assert "Politica A" not in chunk


def test_structural_min_size_parameter_and_version():
    """min_size es el umbral de union (por defecto 120); 0 lo desactiva
    (comportamiento v1); CHUNKER_VERSION es la version que las builds
    estructurales registran en chunk_params."""
    text = (
        "# Titulo\n\n" + _para(75, 17) + "\n\n"
        "## Seccion corta\n\n" + _para(75, 18) + "\n\n"
        "## Otra corta\n\n" + _para(75, 19)
    )

    # Por defecto 120: las tres secciones (~90) se acumulan en una unidad.
    chunker = StructuralChunker(600, 0)
    assert chunker.min_size == 120
    assert len(chunker.chunk(text)) == 1

    # Umbral menor: cada seccion (~90) alcanza el minimo y se queda sola.
    assert len(StructuralChunker(600, 0, min_size=80).chunk(text)) == 3

    # min_size=0 desactiva la union (comportamiento v1).
    assert len(StructuralChunker(600, 0, min_size=0).chunk(text)) == 3

    # La version del segmentador es parte del contrato de la build.
    assert StructuralChunker.CHUNKER_VERSION == "2"

    with pytest.raises(ValueError):
        StructuralChunker(600, 0, min_size=-1)


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


def test_chunkers_expose_chunker_version():
    """TO-DO: cada segmentador expone la version de su algoritmo; la build la
    registra para que dos segmentaciones distintas no compartan nombre."""
    fixed = FixedSizeChunker(100, 20)
    structural = StructuralChunker(100, 20)
    assert fixed.chunker_version == FixedSizeChunker.CHUNKER_VERSION
    assert structural.chunker_version == StructuralChunker.CHUNKER_VERSION
    assert fixed.chunker_version != structural.chunker_version
    assert structural.chunker_version == "2"


def test_build_chunker_accepts_min_size_for_structural():
    """La fabrica propaga `min_size` al estructural (default 120). Para el
    fijo el parametro no aplica: se acepta y se ignora."""
    default = build_chunker("estructural", 100, 20)
    assert isinstance(default, StructuralChunker)
    assert default.min_size == 120

    custom = build_chunker("estructural", 100, 20, min_size=80)
    assert isinstance(custom, StructuralChunker)
    assert custom.min_size == 80

    assert isinstance(build_chunker("fijo", 100, 20, min_size=80), FixedSizeChunker)


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
