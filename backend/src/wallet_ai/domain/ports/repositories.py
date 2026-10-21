from dataclasses import dataclass
from typing import Protocol


@dataclass
class Document:
    id: str
    access_level: str


@dataclass
class LedgerEntryRef:
    id: str


@dataclass
class Draft:
    id: str


@dataclass
class AuditEvent:
    id: str


@dataclass
class RecipientHandle:
    handle: str


class DocumentRepository(Protocol):
    async def search(self, query: str, access_levels: list[str]) -> list[Document]: ...


class LedgerRepository(Protocol):
    async def get_balance(self, account_id: str) -> str: ...
    async def append_entries(self, entries: list[LedgerEntryRef]) -> str: ...


class DraftRepository(Protocol):
    async def create(self, draft: Draft) -> Draft: ...
    async def confirm(self, draft_id: str) -> str: ...


class AuditRepository(Protocol):
    async def append(self, event: AuditEvent) -> None: ...


class RecipientRepository(Protocol):
    async def find_for_user(self, requester_user_id: str, query: str) -> list[RecipientHandle]: ...
    async def resolve(self, requester_user_id: str, handle: str) -> str: ...
