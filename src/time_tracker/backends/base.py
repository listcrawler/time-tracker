from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime
from typing import Self

from ..models import Entry, Tag


class StorageBackend(ABC):
    """Abstract interface that all storage backends must implement."""

    # ------------------------------------------------------------------
    # Context manager

    @abstractmethod
    def __enter__(self) -> Self: ...

    @abstractmethod
    def __exit__(self, *_: object) -> None: ...

    # ------------------------------------------------------------------
    # Current (in-progress) entry
    # Using _get/_set helpers avoids the Python abstract-property-setter
    # inheritance pitfall.

    @abstractmethod
    def _get_current(self) -> Entry | None: ...

    @abstractmethod
    def _set_current(self, value: Entry | None) -> None: ...

    @property
    def current(self) -> Entry | None:
        return self._get_current()

    @current.setter
    def current(self, value: Entry | None) -> None:
        self._set_current(value)

    # ------------------------------------------------------------------
    # Mutations

    @abstractmethod
    def start(self, description: str, tags: list[Tag] | None = None) -> None:
        """Start a new tracking session (ends any current one first)."""

    @abstractmethod
    def end(self) -> None:
        """End the current tracking session and save it."""

    @abstractmethod
    def add_entry(self, entry: Entry) -> None:
        """Append a completed entry."""

    @abstractmethod
    def delete_entry(self, entry: Entry) -> None:
        """Remove an entry (current or completed)."""

    # ------------------------------------------------------------------
    # Queries

    @abstractmethod
    def dump_entries(self, *, since: datetime | None = None) -> Iterable[Entry]:
        """Yield current entry (if any) then completed entries, newest first.

        If *since* is given, only entries whose start >= since are returned.
        """

    @abstractmethod
    def query(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        tags: set[Tag] | None = None,
    ) -> list[Entry]:
        """Filter completed entries by time range and/or tags."""
