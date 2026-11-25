"""Reordenador local determinista por solapamiento lexico (tipo BM25 simple)."""

from __future__ import annotations

import re
import unicodedata


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.findall(r"[a-z0-9]+", stripped.lower())


class LocalReranker:
    provider_name = "local"

    def rerank(self, query: str, docs: list[str], top_k: int) -> list[int]:
        q_tokens = _tokens(query)
        # frecuencia en consulta
        from collections import Counter

        qf = Counter(q_tokens)
        scored: list[tuple[float, int]] = []
        for index, doc in enumerate(docs):
            d_tokens = _tokens(doc)
            df = Counter(d_tokens)
            score = 0.0
            for tok, qcount in qf.items():
                if tok in df:
                    # tf simple: min(qcount, df[tok]) + idf-ish constante
                    score += (1.0 + df[tok]) * qcount
            # normalizar por longitud del doc para evitar sesgo
            denom = len(d_tokens) + 1
            score = score / denom
            scored.append((score, index))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [idx for _, idx in scored[:top_k]]
