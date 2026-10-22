from wallet_ai.infrastructure.providers.fake.embeddings import FakeEmbeddings


def test_fake_embeddings_deterministic():
    emb = FakeEmbeddings(dim=128)
    v1 = emb.embed(["hola mundo"])[0]
    v2 = emb.embed(["hola mundo"])[0]
    assert v1 == v2
    assert len(v1) == 128


def test_fake_embeddings_related_higher():
    emb = FakeEmbeddings(dim=128)
    v1 = emb.embed(["transferir dinero a cuenta"])[0]
    v2 = emb.embed(["transferir dinero cuenta"])[0]
    v3 = emb.embed(["clima soleado hoy"])[0]

    def sim(a, b):
        import math

        na = math.sqrt(sum(x * x for x in a)) or 1
        nb = math.sqrt(sum(x * x for x in b)) or 1
        return sum(x * y for x, y in zip(a, b)) / (na * nb)

    assert sim(v1, v2) > sim(v1, v3)


def test_fake_embeddings_semantic_related():
    emb = FakeEmbeddings(dim=128)
    v1 = emb.embed(["tarjeta de crédito límite"])[0]
    v2 = emb.embed(["límite de la tarjeta"])[0]
    v3 = emb.embed(["horario de atención de la sucursal"])[0]

    def sim(a, b):
        import math

        na = math.sqrt(sum(x * x for x in a)) or 1
        nb = math.sqrt(sum(x * x for x in b)) or 1
        return sum(x * y for x, y in zip(a, b)) / (na * nb)

    assert sim(v1, v2) > sim(v1, v3)


def test_fake_embeddings_empty_no_crash():
    emb = FakeEmbeddings(dim=128)
    v = emb.embed([""])[0]
    assert len(v) == 128


def test_fake_embeddings_normalization_case_accents():
    emb = FakeEmbeddings(dim=128)
    v1 = emb.embed(["TARJETA DE CRÉDITO"])[0]
    v2 = emb.embed(["tarjeta de credito"])[0]
    assert v1 == v2
