"""Shared exception types."""


class StoreError(RuntimeError):
    """A request the ledger refuses (unknown customer, invalid state, bad input)."""
