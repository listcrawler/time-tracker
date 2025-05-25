from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from ..models import Entry, Tag
from .base import StorageBackend

_DDL = """
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    start       TEXT,
    end         TEXT,
    customer    TEXT,
    project     TEXT,
    description TEXT,
    ticket      TEXT,
    ticket_url  TEXT,
    tags        TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS current_entry (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    start       TEXT,
    end         TEXT,
    customer    TEXT,
    project     TEXT,
    description TEXT,
    ticket      TEXT,
    ticket_url  TEXT,
    tags        TEXT NOT NULL DEFAULT '[]'
);
"""

# Migrate databases created before customer/project/ticket were added.
_MIGRATIONS = [
    "ALTER TABLE entries      ADD COLUMN customer TEXT",
    "ALTER TABLE entries      ADD COLUMN project  TEXT",
    "ALTER TABLE current_entry ADD COLUMN customer TEXT",
    "ALTER TABLE current_entry ADD COLUMN project  TEXT",
    "ALTER TABLE entries      ADD COLUMN ticket TEXT",
    "ALTER TABLE current_entry ADD COLUMN ticket TEXT",
    "ALTER TABLE entries      ADD COLUMN ticket_url TEXT",
    "ALTER TABLE current_entry ADD COLUMN ticket_url TEXT",
]


class SqliteBackend(StorageBackend):
    """Stores entries in a SQLite database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._current: Entry | None = None
        # Maps id(entry_object) -> entries.id rowid; populated by dump_entries().
        self._rowids: dict[int, int] = {}

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        for stmt in _MIGRATIONS:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()
        self._current = self._load_current()
        return self

    def __exit__(self, *_) -> None:
        self._persist_current()
        self._conn.commit()
        self._conn.close()

    # ------------------------------------------------------------------
    # Helpers

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> Entry:
        tag_names: list[str] = json.loads(row["tags"] or "[]")
        return Entry(
            start=datetime.fromisoformat(row["start"]) if row["start"] else None,
            end=datetime.fromisoformat(row["end"]) if row["end"] else None,
            customer=row["customer"],
            project=row["project"],
            description=row["description"],
            ticket=row["ticket"],
            ticket_url=row["ticket_url"],
            tags=[Tag(name=n) for n in tag_names] or None,
        )

    @staticmethod
    def _entry_params(entry: Entry) -> dict:
        return {
            "start": entry.start.isoformat() if entry.start else None,
            "end": entry.end.isoformat() if entry.end else None,
            "customer": entry.customer,
            "project": entry.project,
            "description": entry.description,
            "ticket": entry.ticket,
            "ticket_url": entry.ticket_url,
            "tags": json.dumps([t.name for t in entry.tags] if entry.tags else []),
        }

    def _load_current(self) -> Entry | None:
        row = self._conn.execute("SELECT * FROM current_entry WHERE id = 1").fetchone()
        return self._row_to_entry(row) if row else None

    def _persist_current(self) -> None:
        if self._current is None:
            self._conn.execute("DELETE FROM current_entry WHERE id = 1")
        else:
            p = self._entry_params(self._current)
            self._conn.execute(
                "INSERT OR REPLACE INTO current_entry "
                "(id, start, end, customer, project, description, ticket, ticket_url, tags) "
                "VALUES "
                "(1, :start, :end, :customer, :project, :description, :ticket, :ticket_url, :tags)",
                p,
            )

    # ------------------------------------------------------------------
    # StorageBackend interface

    def _get_current(self) -> Entry | None:
        return self._current

    def _set_current(self, value: Entry | None) -> None:
        self._current = value
        self._persist_current()
        self._conn.commit()

    def start(self, description: str, tags: list[Tag] | None = None) -> None:
        if self._current:
            self.end()
        self._set_current(
            Entry(description=description, start=datetime.now(UTC), tags=tags)
        )

    def end(self) -> None:
        if not self._current:
            return
        self._current.end = datetime.now(UTC)
        # Insert the finished entry into the entries table.
        p = self._entry_params(self._current)
        cursor = self._conn.execute(
            "INSERT INTO entries "
            "(start, end, customer, project, description, ticket, ticket_url, tags) "
            "VALUES (:start, :end, :customer, :project, :description, :ticket, :ticket_url, :tags)",
            p,
        )
        self._rowids[id(self._current)] = cursor.lastrowid
        # Clear the current slot.
        self._conn.execute("DELETE FROM current_entry WHERE id = 1")
        self._conn.commit()
        self._current = None

    def add_entry(self, entry: Entry) -> None:
        p = self._entry_params(entry)
        cursor = self._conn.execute(
            "INSERT INTO entries "
            "(start, end, customer, project, description, ticket, ticket_url, tags) "
            "VALUES (:start, :end, :customer, :project, :description, :ticket, :ticket_url, :tags)",
            p,
        )
        self._conn.commit()
        self._rowids[id(entry)] = cursor.lastrowid

    def delete_entry(self, entry: Entry) -> None:
        if entry is self._current:
            self._conn.execute("DELETE FROM current_entry WHERE id = 1")
            self._conn.commit()
            self._current = None
            return
        rowid = self._rowids.get(id(entry))
        if rowid is not None:
            self._conn.execute("DELETE FROM entries WHERE id = ?", (rowid,))
            self._conn.commit()
            del self._rowids[id(entry)]

    # ------------------------------------------------------------------

    def dump_entries(self, *, since: datetime | None = None) -> Iterable[Entry]:
        self._rowids.clear()
        if self._current:
            yield self._current
        if since is not None:
            rows = self._conn.execute(
                "SELECT * FROM entries WHERE start >= ? ORDER BY start DESC",
                (since.isoformat(),),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM entries ORDER BY start DESC").fetchall()
        for row in rows:
            entry = self._row_to_entry(row)
            self._rowids[id(entry)] = row["id"]
            yield entry

    def query(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        tags: set[Tag] | None = None,
    ) -> list[Entry]:
        conditions: list[str] = []
        params: list[str] = []
        if start:
            conditions.append("start >= ?")
            params.append(start.isoformat())
        if end:
            conditions.append("end <= ?")
            params.append(end.isoformat())
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM entries {where} ORDER BY start DESC", params
        ).fetchall()
        results = []
        for row in rows:
            entry = self._row_to_entry(row)
            self._rowids[id(entry)] = row["id"]
            if tags:
                tag_names = {t.name for t in tags}
                entry_tags = {t.name for t in entry.tags} if entry.tags else set()
                if not tag_names.issubset(entry_tags):
                    continue
            results.append(entry)
        return results
