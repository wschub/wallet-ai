from typing import Protocol


class Reranker(Protocol):
    async def rerank(self, query: str, docs: list[str], top_k: int) -> list[int]: ...
