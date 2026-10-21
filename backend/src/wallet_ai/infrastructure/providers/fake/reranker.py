from __future__ import annotations

import re


class FakeReranker:
    def _tokens(self, s: str) -> set[str]:
        s = s.lower()
        return set(re.findall(r"[a-záéíóúüñ]+", s))

    def rerank(self, query: str, docs: list[str], top_k: int) -> list[int]:
        q = self._tokens(query)
        scored = []
        for i, d in enumerate(docs):
            dt = self._tokens(d)
            scored.append((i, len(q & dt)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [i for i, _ in scored[:top_k]]
