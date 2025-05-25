from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from .backends.base import StorageBackend
from .models import Entry as DBEntry


class PendingChanges:
    """Tracks staged edits and deletes that have not yet been written to the DB.

    Entries in the pending state are displayed highlighted in the list.
    They are only persisted when the user explicitly commits (w / W).
    """

    def __init__(self) -> None:
        # orig_start → modified entry (not yet written to DB)
        self._pending: dict[datetime, DBEntry] = {}
        # current start of a pending entry → its original start key
        self._pending_key_for: dict[datetime, datetime] = {}
        # orig_start keys that are brand-new (never saved to DB)
        self._pending_new_starts: set[datetime] = set()
        # orig_start keys marked for deletion (not yet deleted from DB)
        self._pending_deletes: set[datetime] = set()

    def __bool__(self) -> bool:
        return bool(self._pending or self._pending_deletes)

    def __len__(self) -> int:
        return len(self._pending) + len(self._pending_deletes)

    # ── Query helpers ─────────────────────────────────────────────────────────

    def orig_key(self, start: datetime) -> datetime:
        """Resolve a (possibly updated) start time back to its original DB key."""
        return self._pending_key_for.get(start, start)

    def is_pending(self, entry: DBEntry) -> bool:
        return entry.start is not None and self.orig_key(entry.start) in self._pending

    def is_deleted(self, entry: DBEntry) -> bool:
        return entry.start is not None and self.orig_key(entry.start) in self._pending_deletes

    def is_new(self, entry: DBEntry) -> bool:
        """True if entry was never saved to DB (pending-new)."""
        return entry.start is not None and self.orig_key(entry.start) in self._pending_new_starts

    def apply(self, entries: list[DBEntry]) -> list[DBEntry]:
        """Return entries with any pending modifications applied, plus new pending entries."""
        result = [self._pending.get(e.start, e) if e.start else e for e in entries]
        for key in self._pending_new_starts:
            if key in self._pending:
                result.append(self._pending[key])
        return result

    # ── Mutation helpers ──────────────────────────────────────────────────────

    def add_new(self, entry: DBEntry) -> None:
        """Register a brand-new entry that does not yet exist in the DB."""
        if entry.start is None:
            return
        self._pending[entry.start] = entry
        self._pending_new_starts.add(entry.start)

    def update(self, old: DBEntry, new: DBEntry) -> None:
        """Stage a modification to an existing entry."""
        if old.start is None:
            return
        key = self.orig_key(old.start)
        # Remove stale reverse-mapping for the previous pending version
        existing = self._pending.get(key)
        if existing is not None and existing.start is not None and existing.start != key:
            self._pending_key_for.pop(existing.start, None)
        self._pending[key] = new
        if new.start is not None and new.start != key:
            self._pending_key_for[new.start] = key

    def discard(self, entry: DBEntry) -> bool:
        """Remove any staged change for entry. Returns True if one existed."""
        if entry.start is None:
            return False
        key = self.orig_key(entry.start)
        existing = self._pending.pop(key, None)
        if existing is not None and existing.start is not None and existing.start != key:
            self._pending_key_for.pop(existing.start, None)
        self._pending_new_starts.discard(key)
        return existing is not None

    def mark_deleted(self, entry: DBEntry) -> None:
        """Stage entry for deletion."""
        if entry.start is not None:
            self._pending_deletes.add(self.orig_key(entry.start))

    def cancel_delete(self, entry: DBEntry) -> None:
        """Un-stage a pending delete."""
        if entry.start is not None:
            self._pending_deletes.discard(self.orig_key(entry.start))

    def clear(self) -> None:
        self._pending.clear()
        self._pending_key_for.clear()
        self._pending_new_starts.clear()
        self._pending_deletes.clear()

    # ── Commit ────────────────────────────────────────────────────────────────

    def commit_entries(
        self,
        entries: list[DBEntry],
        db: StorageBackend,
        on_error: Callable[[str], None],
    ) -> None:
        """Write the given entries' staged changes (edits and deletes) to *db*."""
        db_by_start = {e.start: e for e in db.dump_entries() if e.start}
        for entry in entries:
            if entry.start is None:
                continue
            key = self.orig_key(entry.start)
            if key in self._pending_deletes:
                orig = db_by_start.get(key)
                if orig is not None:
                    try:
                        db.delete_entry(orig)
                    except Exception as exc:
                        on_error(f"Write failed: {exc}")
                        continue
                self._pending_deletes.discard(key)
                continue
            if key not in self._pending:
                continue
            modified = self._pending[key]
            try:
                if key in self._pending_new_starts:
                    db.add_entry(modified)
                else:
                    orig = db_by_start.get(key)
                    if orig is not None:
                        db.delete_entry(orig)
                        db.add_entry(modified)
            except Exception as exc:
                on_error(f"Write failed: {exc}")
                continue
            self._pending.pop(key)
            if modified.start is not None and modified.start != key:
                self._pending_key_for.pop(modified.start, None)
            self._pending_new_starts.discard(key)

    def commit_all(
        self,
        db: StorageBackend,
        on_error: Callable[[str], None],
    ) -> None:
        """Write all staged changes to *db*."""
        db_by_start = {e.start: e for e in db.dump_entries() if e.start}
        for key in list(self._pending_deletes):
            orig = db_by_start.get(key)
            if orig is not None:
                try:
                    db.delete_entry(orig)
                except Exception as exc:
                    on_error(f"Write failed: {exc}")
                    continue
            self._pending_deletes.discard(key)
        for key, modified in list(self._pending.items()):
            try:
                if key in self._pending_new_starts:
                    db.add_entry(modified)
                else:
                    orig = db_by_start.get(key)
                    if orig is not None:
                        db.delete_entry(orig)
                        db.add_entry(modified)
            except Exception as exc:
                on_error(f"Write failed: {exc}")
                continue
            del self._pending[key]
            if modified.start is not None and modified.start != key:
                self._pending_key_for.pop(modified.start, None)
            self._pending_new_starts.discard(key)
