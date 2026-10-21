from typing import Protocol


class PIIService(Protocol):
    def mask(self, text: str) -> str: ...
