from typing import Protocol


class LLMProvider(Protocol):
    async def generate(self, messages: list[dict], **kwargs) -> str: ...
