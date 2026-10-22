from __future__ import annotations

import hashlib
import math
import re
import unicodedata


class FakeEmbeddings:
    def __init__(self, dim: int = 128) -> None:
        self.dim = dim

    def _tokenize(self, text: str) -> list[str]:
        text = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in text if not unicodedata.combining(c))
        text = text.lower()
        return re.findall(r"[a-z]+", text)

    def embed(self, texts: list[str]) -> list[list[float]]:
        res = []
        for t in texts:
            tokens = self._tokenize(t)
            vec = [0.0] * self.dim
            for tok in tokens:
                h = hashlib.sha256(tok.encode("utf-8")).digest()
                for i in range(self.dim):
                    vec[i] += (h[i % len(h)] / 255.0) * ((-1.0) ** i)
            # normalize
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            res.append([x / norm for x in vec])
        return res
