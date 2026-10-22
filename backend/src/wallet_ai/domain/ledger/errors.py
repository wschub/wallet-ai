from __future__ import annotations


class LedgerError(Exception):
    pass


class InvalidAmount(LedgerError):
    pass


class AccountNotOwned(LedgerError):
    pass


class InsufficientFunds(LedgerError):
    pass


class IdempotencyConflict(LedgerError):
    pass


class SameAccountTransfer(LedgerError):
    pass


class AccountNotFound(LedgerError):
    pass


class SystemAccountNotAllowed(LedgerError):
    pass
