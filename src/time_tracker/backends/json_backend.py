from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from pydantic import BaseModel

from ..models import Entry, Tag
from .base import StorageBackend


def _entry_sort_key(e: Entry) -> tuple:
    def _dt(v: datetime | None) -> tuple[int, datetime]:
        return (0, v) if v is not None else (-1, datetime.min.replace(tzinfo=UTC))

    def _s(v: str | None) -> tuple[int, str]:
        return (0, v) if v is not None else (-1, "")

    return (
        _dt(e.start),
        _dt(e.end),
        _s(e.customer),
        _s(e.project),
        _s(e.description),
        _s(e.ticket),
        _s(e.ticket_url),
        sorted(t.name for t in e.tags) if e.tags else [],
    )


class JsonBackend(StorageBackend):
    """Stores all entries in a single JSON file."""

    class _Model(BaseModel):
        entries: list[Entry] = []
        current: Entry | None = None

    def __init__(self, path: Path) -> None:
        self.path = path
        self._db = JsonBackend._Model()

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self._db = JsonBackend._Model.model_validate_json(self.path.read_text())
        else:
            self._db = JsonBackend._Model()
        return self

    def __exit__(self, *_: object) -> None:
        self._db.entries.sort(key=_entry_sort_key)
        self.path.write_text(self._db.model_dump_json(indent=4))

    # ------------------------------------------------------------------

    def _get_current(self) -> Entry | None:
        return self._db.current

    def _set_current(self, value: Entry | None) -> None:
        self._db.current = value

    def start(self, description: str, tags: list[Tag] | None = None) -> None:
        if self._db.current:
            self.end()
        self._db.current = Entry(
            description=description, start=datetime.now(UTC), tags=tags
        )

    def end(self) -> None:
        if self._db.current:
            self._db.current.end = datetime.now(UTC)
            self.add_entry(self._db.current)
            self._db.current = None

    def add_entry(self, entry: Entry) -> None:
        self._db.entries.append(entry)

    def delete_entry(self, entry: Entry) -> None:
        if entry is self._db.current:
            self._db.current = None
        elif entry in self._db.entries:
            self._db.entries.remove(entry)

    # ------------------------------------------------------------------

    def dump_entries(self, *, since: datetime | None = None) -> Iterable[Entry]:
        if entry := self._db.current:
            yield entry
        for entry in self._db.entries:
            if since is None or (entry.start and entry.start >= since):
                yield entry

    def query(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        tags: set[Tag] | None = None,
    ) -> list[Entry]:
        results = list(self._db.entries)
        if start:
            results = [e for e in results if e.start and e.start >= start]
        if end:
            results = [e for e in results if e.end and e.end <= end]
        if tags:
            tag_names = {t.name for t in tags}
            results = [e for e in results if e.tags and tag_names.issubset(t.name for t in e.tags)]
        return results
