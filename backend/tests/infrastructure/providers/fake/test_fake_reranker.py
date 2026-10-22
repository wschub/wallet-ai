from wallet_ai.infrastructure.providers.fake.reranker import FakeReranker


def test_fake_reranker_overlap():
    r = FakeReranker()
    docs = ["transferir saldo a otro banco", "clima en bogota", "saldo cuenta transferir"]
    idxs = r.rerank("transferir saldo", docs, top_k=2)
    assert len(idxs) == 2


def test_fake_reranker_tie():
    r = FakeReranker()
    docs = ["a b", "a b"]
    idxs = r.rerank("a b", docs, top_k=2)
    assert len(idxs) == 2


def test_fake_reranker_empty_query():
    r = FakeReranker()
    docs = ["hola", "mundo"]
    idxs = r.rerank("", docs, top_k=2)
    assert len(idxs) == 2
