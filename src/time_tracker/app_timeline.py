from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .models import Entry as DBEntry
from .widgets.entry_list import EntryRow, GapRow, OverlapRow


class TimelineMixin:
    """Mixin providing timeline-mode editing for TimeTrackerApp."""

    _timeline_mode: bool

    @staticmethod
    def _snap(dt: datetime, direction: int) -> datetime:
        """Move dt to the next 5-minute grid boundary in the given direction."""
        total = dt.hour * 60 + dt.minute
        rem = total % 5
        offset = (-rem if rem else -5) if direction < 0 else ((5 - rem) if rem else 5)
        return (dt + timedelta(minutes=offset)).replace(second=0, microsecond=0)

    def _prev_entry_in_timeline(self, entry: DBEntry) -> DBEntry | None:
        """Older adjacent entry (lower in the timeline list)."""
        for i, row in enumerate(self._rows):  # type: ignore[attr-defined]
            if isinstance(row, EntryRow) and row.entry is entry:
                for r in self._rows[i + 1 :]:  # type: ignore[attr-defined]
                    if isinstance(r, EntryRow):
                        return r.entry
                return None
        return None

    def _next_entry_in_timeline(self, entry: DBEntry) -> DBEntry | None:
        """Newer adjacent entry (higher in the timeline list)."""
        for i, row in enumerate(self._rows):  # type: ignore[attr-defined]
            if isinstance(row, EntryRow) and row.entry is entry:
                for r in reversed(self._rows[:i]):  # type: ignore[attr-defined]
                    if isinstance(r, EntryRow):
                        return r.entry
                return None
        return None

    def _move_start(self, direction: int) -> None:
        entry = self._selected_entry()  # type: ignore[attr-defined]
        if entry is None or entry.start is None:
            return

        new_start = self._snap(entry.start, direction)
        prev = self._prev_entry_in_timeline(entry)

        if direction < 0:
            # Moving earlier — prevent crossing own end
            if entry.end is not None and new_start >= entry.end:
                return
            # Push prev entry's end back if we bump into it
            if prev is not None and prev.end is not None and new_start <= prev.end:
                self._replace_entry(prev, prev.model_copy(update={"end": new_start}))  # type: ignore[attr-defined]
        else:
            # Moving later — prevent crossing own end or (for running entries) now
            if entry.end is not None and new_start >= entry.end:
                return
            if entry.end is None and new_start > datetime.now(UTC):
                return
            # If aligned with prev entry's end, drag it along
            if prev is not None and prev.end is not None and entry.start == prev.end:
                self._replace_entry(prev, prev.model_copy(update={"end": new_start}))  # type: ignore[attr-defined]

        self._replace_entry(entry, entry.model_copy(update={"start": new_start}))  # type: ignore[attr-defined]
        self._refresh_list()  # type: ignore[attr-defined]

    def _move_end(self, direction: int) -> None:
        entry = self._selected_entry()  # type: ignore[attr-defined]
        if entry is None or entry.end is None:
            return

        new_end = self._snap(entry.end, direction)
        nxt = self._next_entry_in_timeline(entry)

        if direction > 0:
            # Moving later — prevent crossing own start
            if entry.start is not None and new_end <= entry.start:
                return
            # Push next entry's start forward if we bump into it
            if nxt is not None and nxt.start is not None and new_end >= nxt.start:
                self._replace_entry(nxt, nxt.model_copy(update={"start": new_end}))  # type: ignore[attr-defined]
        else:
            # Moving earlier — prevent crossing own start
            if entry.start is not None and new_end <= entry.start:
                return
            # If aligned with next entry's start, drag it along
            if nxt is not None and nxt.start is not None and entry.end == nxt.start:
                self._replace_entry(nxt, nxt.model_copy(update={"start": new_end}))  # type: ignore[attr-defined]

        self._replace_entry(entry, entry.model_copy(update={"end": new_end}))  # type: ignore[attr-defined]
        self._refresh_list()  # type: ignore[attr-defined]

    def action_align_entry(self) -> None:
        if not self._timeline_mode:
            return
        entry = self._selected_entry()  # type: ignore[attr-defined]
        if entry is None or entry.start is None:
            return
        prev = self._prev_entry_in_timeline(entry)
        if prev is None or prev.end is None:
            return
        if entry.end is not None and prev.end >= entry.end:
            return  # would place start at or past own end
        if entry.end is None and prev.end > datetime.now(UTC):
            return  # running entry cannot start in the future
        self._replace_entry(entry, entry.model_copy(update={"start": prev.end}))  # type: ignore[attr-defined]
        self._refresh_list()  # type: ignore[attr-defined]

    def action_join_with_prev(self) -> None:
        if not self._timeline_mode:
            return
        entry = self._selected_entry()  # type: ignore[attr-defined]
        if entry is None:
            return
        prev = self._prev_entry_in_timeline(entry)
        if prev is None or prev is self.db.current:  # type: ignore[attr-defined]
            return
        if entry is self.db.current:  # type: ignore[attr-defined]
            was_new = self._changes.is_new(prev)  # type: ignore[attr-defined]
            if not was_new:
                self._changes.mark_deleted(prev)  # type: ignore[attr-defined]
            self._changes.discard(prev)  # type: ignore[attr-defined]
            if self._saved_current_for_join is None:  # type: ignore[attr-defined]
                self._saved_current_for_join = self.db.current  # type: ignore[attr-defined]
            self.db.current = prev.model_copy(update={"end": None})  # type: ignore[attr-defined]
            self._refresh_list(focus_entry_start=prev.start)  # type: ignore[attr-defined]
            return
        if entry.end is None:
            return
        # Extend prev's end to cover this entry's end time, keeping prev's fields
        self._replace_entry(prev, prev.model_copy(update={"end": entry.end}))  # type: ignore[attr-defined]
        # Delete the highlighted entry
        was_new = self._changes.is_new(entry)  # type: ignore[attr-defined]
        if not was_new:
            self._changes.mark_deleted(entry)  # type: ignore[attr-defined]
        self._changes.discard(entry)  # type: ignore[attr-defined]
        self._refresh_list(focus_entry_start=prev.start)  # type: ignore[attr-defined]

    def action_toggle_timeline(self) -> None:
        self._timeline_mode = not self._timeline_mode
        self._refresh_list()  # type: ignore[attr-defined]
