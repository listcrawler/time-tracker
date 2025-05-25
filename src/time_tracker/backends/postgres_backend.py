from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Self

try:
    import psycopg2
    import psycopg2.extras
except ImportError as exc:
    raise ImportError(
        "psycopg2 is required for the PostgreSQL backend.\n"
        "Install it with:  uv sync --extra postgres"
    ) from exc

from ..models import Entry, Tag
from .base import StorageBackend

_DDL = """
CREATE TABLE IF NOT EXISTS entries (
    id          SERIAL PRIMARY KEY,
    start       TIMESTAMPTZ,
    end_time    TIMESTAMPTZ,
    customer    TEXT,
    project     TEXT,
    description TEXT,
    ticket      TEXT,
    ticket_url  TEXT,
    tags        TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS current_entry (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    start       TIMESTAMPTZ,
    end_time    TIMESTAMPTZ,
    customer    TEXT,
    project     TEXT,
    description TEXT,
    ticket      TEXT,
    ticket_url  TEXT,
    tags        TEXT[] NOT NULL DEFAULT '{}'
);

ALTER TABLE entries       ADD COLUMN IF NOT EXISTS customer TEXT;
ALTER TABLE entries       ADD COLUMN IF NOT EXISTS project  TEXT;
ALTER TABLE current_entry ADD COLUMN IF NOT EXISTS customer TEXT;
ALTER TABLE current_entry ADD COLUMN IF NOT EXISTS project  TEXT;
ALTER TABLE entries       ADD COLUMN IF NOT EXISTS ticket TEXT;
ALTER TABLE current_entry ADD COLUMN IF NOT EXISTS ticket TEXT;
ALTER TABLE entries       ADD COLUMN IF NOT EXISTS ticket_url TEXT;
ALTER TABLE current_entry ADD COLUMN IF NOT EXISTS ticket_url TEXT;
"""

_UPSERT_CURRENT = """
INSERT INTO current_entry
    (id, start, end_time, customer, project, description, ticket, ticket_url, tags)
VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE
    SET start       = EXCLUDED.start,
        end_time    = EXCLUDED.end_time,
        customer    = EXCLUDED.customer,
        project     = EXCLUDED.project,
        description = EXCLUDED.description,
        ticket      = EXCLUDED.ticket,
        ticket_url  = EXCLUDED.ticket_url,
        tags        = EXCLUDED.tags
"""


class PostgresBackend(StorageBackend):
    """Stores entries in a remote PostgreSQL database."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._conn: psycopg2.extensions.connection | None = None
        self._current: Entry | None = None
        # Maps id(entry_object) -> entries.id; populated by dump_entries().
        self._rowids: dict[int, int] = {}

    def _connect(self) -> None:
        self._conn = psycopg2.connect(
            self.url,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=10,
            keepalives_interval=5,
            keepalives_count=3,
        )
        with self._conn.cursor() as cur:  # _cursor() not available yet; conn is fresh
            cur.execute(_DDL)
        self._conn.commit()

    def _ensure_connected(self) -> None:
        if self._conn is not None and not self._conn.closed:
            try:
                with self._conn.cursor() as cur:
                    cur.execute("SELECT 1")
                self._conn.rollback()
                return
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                pass  # stale connection; reconnect below
        self._connect()

    def __enter__(self) -> Self:
        self._connect()
        self._current = self._load_current()
        return self

    def __exit__(self, *_) -> None:
        if self._conn is None or self._conn.closed:
            return
        self._persist_current()
        self._conn.commit()
        self._conn.close()

    # ------------------------------------------------------------------
    # Helpers

    def _cursor(self):
        self._ensure_connected()
        return self._conn.cursor()

    def _dict_cursor(self):
        self._ensure_connected()
        return self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    @staticmethod
    def _row_to_entry(row: dict) -> Entry:
        tags = [Tag(name=n) for n in row["tags"]] if row["tags"] else None
        return Entry(
            start=row["start"],
            end=row["end_time"],
            customer=row["customer"],
            project=row["project"],
            description=row["description"],
            ticket=row["ticket"],
            ticket_url=row["ticket_url"],
            tags=tags,
        )

    @staticmethod
    def _tag_list(entry: Entry) -> list[str]:
        return [t.name for t in entry.tags] if entry.tags else []

    def _load_current(self) -> Entry | None:
        with self._dict_cursor() as cur:
            cur.execute("SELECT * FROM current_entry WHERE id = 1")
            row = cur.fetchone()
        return self._row_to_entry(row) if row else None

    def _persist_current(self) -> None:
        with self._cursor() as cur:
            if self._current is None:
                cur.execute("DELETE FROM current_entry WHERE id = 1")
            else:
                cur.execute(
                    _UPSERT_CURRENT,
                    (
                        self._current.start,
                        self._current.end,
                        self._current.customer,
                        self._current.project,
                        self._current.description,
                        self._current.ticket,
                        self._current.ticket_url,
                        self._tag_list(self._current),
                    ),
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
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO entries"
                " (start, end_time, customer, project, description, ticket, ticket_url, tags)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    self._current.start,
                    self._current.end,
                    self._current.customer,
                    self._current.project,
                    self._current.description,
                    self._current.ticket,
                    self._current.ticket_url,
                    self._tag_list(self._current),
                ),
            )
            self._rowids[id(self._current)] = cur.fetchone()[0]
            cur.execute("DELETE FROM current_entry WHERE id = 1")
        self._conn.commit()
        self._current = None

    def add_entry(self, entry: Entry) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO entries"
                " (start, end_time, customer, project, description, ticket, ticket_url, tags)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    entry.start,
                    entry.end,
                    entry.customer,
                    entry.project,
                    entry.description,
                    entry.ticket,
                    entry.ticket_url,
                    self._tag_list(entry),
                ),
            )
            self._rowids[id(entry)] = cur.fetchone()[0]
        self._conn.commit()

    def delete_entry(self, entry: Entry) -> None:
        if entry is self._current:
            with self._cursor() as cur:
                cur.execute("DELETE FROM current_entry WHERE id = 1")
            self._conn.commit()
            self._current = None
            return
        rowid = self._rowids.get(id(entry))
        if rowid is not None:
            with self._cursor() as cur:
                cur.execute("DELETE FROM entries WHERE id = %s", (rowid,))
            self._conn.commit()
            del self._rowids[id(entry)]

    # ------------------------------------------------------------------

    def dump_entries(self, *, since: datetime | None = None) -> Iterable[Entry]:
        self._rowids.clear()
        if self._current:
            yield self._current
        with self._dict_cursor() as cur:
            if since is not None:
                cur.execute(
                    "SELECT * FROM entries WHERE start >= %s ORDER BY start DESC NULLS LAST",
                    (since,),
                )
            else:
                cur.execute("SELECT * FROM entries ORDER BY start DESC NULLS LAST")
            rows = cur.fetchall()
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
        params: list = []
        if start:
            conditions.append("start >= %s")
            params.append(start)
        if end:
            conditions.append("end_time <= %s")
            params.append(end)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._dict_cursor() as cur:
            cur.execute(
                f"SELECT * FROM entries {where} ORDER BY start DESC NULLS LAST",
                params,
            )
            rows = cur.fetchall()
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
