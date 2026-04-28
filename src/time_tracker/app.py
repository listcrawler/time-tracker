from __future__ import annotations

import webbrowser
from datetime import UTC, date, datetime, timedelta

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, ListView, Static

from .about_modal import AboutModal
from .app_filter import FilterMixin
from .app_rows import RowsMixin, _RowT
from .app_timeline import TimelineMixin
from .backends.base import StorageBackend
from .config import Config, detect_system_dark
from .help_modal import MAIN_HELP_LEFT, MAIN_HELP_RIGHT, HelpModal
from .integrations.base import TicketProvider, ticket_url_from_ref
from .modals import BulkEditModal, ConfirmModal, CopyToBackendModal, GroupingModal, SearchModal
from .models import Entry as DBEntry
from .models import Tag as DBTag
from .pending import PendingChanges
from .widgets.common import GroupKey, rank_options
from .widgets.entry_list import (
    DEFAULT_GROUP_FIELDS,
    EntryRow,
    GroupHeader,
    WeekHeader,
    MonthHeader,
    col_header,
)
from .widgets.entry_modal import EntryModal


class TimeTrackerApp(FilterMixin, TimelineMixin, RowsMixin, App[None]):
    CSS_PATH = "time_tracker.tcss"

    BINDINGS = [
        Binding("n", "new_entry", "New entry"),
        Binding("s", "start_tracking", "Start tracking"),
        Binding("p", "stop_tracking", "Stop tracking"),
        Binding("e", "edit_entry", "Edit"),
        Binding("c", "continue_entry", "Continue", show=False),
        Binding("C", "continue_entry_with_edit", "Continue+Edit", show=False),
        Binding("d", "delete_entry", "Delete"),
        Binding("left", "collapse_group", "Collapse", show=False),
        Binding("right", "expand_group", "Expand", show=False),
        Binding("shift+left", "end_earlier", "End−", show=False),
        Binding("shift+right", "end_later", "End+", show=False),
        Binding("home", "cursor_first", "Top", show=False),
        Binding("end", "cursor_last", "Bottom", show=False),
        Binding("a", "align_entry", "Align", show=False),
        Binding("J", "join_with_prev", "Join", show=False),
        Binding("t", "toggle_timeline", "Timeline", show=False),
        Binding("/", "search", "Search", show=False),
        Binding("f", "filter_entries", "Filter", show=False),
        Binding("g", "grouping", "Grouping", show=False),
        Binding("w", "commit_entry", "Write", show=False),
        Binding("W", "commit_all", "Write all", show=False),
        Binding("u", "cancel_entry", "Undo", show=False),
        Binding("U", "cancel_all", "Undo all", show=False),
        Binding("o", "open_ticket", "Open ticket", show=False),
        Binding("space", "toggle_select", "Select", show=False),
        Binding("escape", "clear_selection", "Clear selection", show=False),
        Binding("E", "bulk_edit", "Bulk edit", show=False),
        Binding("T", "toggle_theme", "Theme", show=False),
        Binding("X", "copy_to_backend", "Copy to backend", show=False),
        Binding("?", "help", "Help"),
        Binding("A", "about", "About", show=False),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        db: StorageBackend,
        ticket_provider: TicketProvider | None = None,
        config: Config | None = None,
    ):
        self.db = db
        self._ticket_provider = ticket_provider
        self._config = config
        super().__init__()
        self._timeline_mode: bool = False
        self._group_fields: frozenset[str] = DEFAULT_GROUP_FIELDS
        self._collapsed: set[tuple[date, GroupKey]] = set()
        self._collapsed_weeks: set[date] = set()
        self._collapsed_months: set[tuple[int, int]] = set()
        self._rows: list[_RowT] = []
        self._initialized = False
        self._changes = PendingChanges()
        self._saved_current_for_join: DBEntry | None = None
        self._saved_current: DBEntry | None = None
        self._load_all: bool = False
        self._filter: dict[str, str] = {}
        self._selected_starts: set[datetime] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(col_header(), id="col-header")
        yield ListView(id="entries")
        yield Static("", id="mode-line")
        with Horizontal(id="bottom-panel"):
            yield Static("", id="entry-status")
            yield Static("", id="time-totals")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_theme()
        self._refresh_list()
        self.set_interval(60, self._tick)

    def _tick(self) -> None:
        """Called every minute to refresh the running entry's display and totals."""
        if self.db.current is None:
            return
        lv = self.query_one(ListView)
        for item in lv._nodes:
            if isinstance(item, EntryRow) and item.entry is self.db.current:
                item.refresh_display()
                break
        since = None if self._load_all else datetime.now(UTC) - timedelta(days=183)
        entries = self._changes.apply(list(self.db.dump_entries(since=since)))
        if not self._timeline_mode:
            entries = self._apply_filter(entries)
        self._update_totals(entries)

    def _apply_theme(self) -> None:
        if self._config is None:
            return
        scheme = self._config.color_scheme
        if scheme == "auto":
            detected = detect_system_dark()
            use_dark = detected if detected is not None else True
        else:
            use_dark = scheme == "dark"
        self.theme = self._config.dark_theme if use_dark else self._config.light_theme

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _replace_entry(self, old: DBEntry, new: DBEntry) -> None:
        if old is self.db.current:
            if self._saved_current_for_join is None and self._saved_current is None:
                self._saved_current = self.db.current
            self.db.current = new
        else:
            self._changes.update(old, new)

    def _recent_values(self) -> dict[str, list[str]]:
        entries = list(self.db.dump_entries())
        return {
            "customer": rank_options(entries, lambda e: e.customer)[:8],
            "project": rank_options(entries, lambda e: e.project)[:8],
            "description": rank_options(entries, lambda e: e.description)[:8],
            "ticket": rank_options(entries, lambda e: e.ticket)[:8],
            "ticket-url": rank_options(entries, lambda e: e.ticket_url)[:8],
            "tags": rank_options(entries, lambda e: [t.name for t in e.tags] if e.tags else None)[:8],
        }

    def _recent_prefill(self) -> dict[str, str]:
        """Return customer/project from the most recently used entry."""
        entries = [e for e in self.db.dump_entries() if e.start]
        if not entries:
            return {}
        recent = max(entries, key=lambda e: e.start)  # type: ignore[arg-type, return-value]
        result: dict[str, str] = {}
        if recent.customer:
            result["customer"] = recent.customer
        if recent.project:
            result["project"] = recent.project
        return result

    def _open_entry_modal(
        self,
        entry: DBEntry | None = None,
        show_times: bool = True,
        prefill: dict[str, str] | None = None,
        group_entries: list[DBEntry] | None = None,
    ) -> EntryModal:
        return EntryModal(
            entry,
            ticket_provider=self._ticket_provider,
            show_times=show_times,
            recent_values=self._recent_values(),
            prefill=prefill,
            group_entries=group_entries,
        )

    def _entries_at_cursor(self) -> list[DBEntry]:
        """All entries represented by the currently highlighted row."""
        row = self._highlighted_row()
        if isinstance(row, GroupHeader):
            return list(row.entries)
        if isinstance(row, EntryRow):
            return [row.entry]
        return []

    def _on_write_error(self, msg: str) -> None:
        self.notify(msg, severity="error")

    # ── Navigation ────────────────────────────────────────────────────────────

    def action_cursor_down(self) -> None:
        lvs = self.query(ListView)
        if lvs:
            lvs.first().action_cursor_down()

    def action_cursor_up(self) -> None:
        lvs = self.query(ListView)
        if lvs:
            lvs.first().action_cursor_up()

    def action_cursor_first(self) -> None:
        lvs = self.query(ListView)
        if lvs:
            lvs.first().index = 0

    def action_cursor_last(self) -> None:
        lvs = self.query(ListView)
        if lvs:
            lv = lvs.first()
            lv.index = len(lv) - 1

    def action_end_earlier(self) -> None:
        if self._timeline_mode:
            self._move_end(-1)
        else:
            self.action_collapse_all()

    def action_end_later(self) -> None:
        if self._timeline_mode:
            self._move_end(+1)
        else:
            self.action_expand_all()

    def action_collapse_group(self) -> None:
        if self._timeline_mode:
            self._move_start(-1)
            return
        row = self._highlighted_row()
        if isinstance(row, EntryRow) and row.in_group:
            idx = self._rows.index(row)
            for r in reversed(self._rows[:idx]):
                if isinstance(r, GroupHeader):
                    row = r
                    break
        if isinstance(row, GroupHeader):
            gid = (row.day, row.key)
            self._collapsed.add(gid)
            self._refresh_list(focus_group=gid)
        elif isinstance(row, WeekHeader):
            self._collapsed_weeks.add(row.week_start)
            self._refresh_list()
        elif isinstance(row, MonthHeader):
            self._collapsed_months.add((row.year, row.month))
            self._refresh_list()

    def action_expand_group(self) -> None:
        if self._timeline_mode:
            self._move_start(+1)
            return
        row = self._highlighted_row()
        if isinstance(row, GroupHeader):
            self._collapsed.discard((row.day, row.key))
            self._refresh_list()
        elif isinstance(row, WeekHeader):
            self._collapsed_weeks.discard(row.week_start)
            self._refresh_list()
        elif isinstance(row, MonthHeader):
            self._collapsed_months.discard((row.year, row.month))
            self._refresh_list()

    def action_collapse_all(self) -> None:
        for row in self._rows:
            if isinstance(row, GroupHeader):
                self._collapsed.add((row.day, row.key))
            elif isinstance(row, WeekHeader):
                self._collapsed_weeks.add(row.week_start)
            elif isinstance(row, MonthHeader):
                self._collapsed_months.add((row.year, row.month))
        self._refresh_list()

    def action_expand_all(self) -> None:
        self._collapsed.clear()
        self._collapsed_weeks.clear()
        self._collapsed_months.clear()
        self._refresh_list()

    # ── Pending changes ───────────────────────────────────────────────────────

    def action_commit_entry(self) -> None:
        entries = self._entries_at_cursor()
        if not entries:
            return
        if any(e is self.db.current for e in entries):
            self._saved_current_for_join = None
            self._saved_current = None
        self._changes.commit_entries(entries, self.db, self._on_write_error)
        self._refresh_list()

    def action_commit_all(self) -> None:
        self._saved_current_for_join = None
        self._saved_current = None
        self._changes.commit_all(self.db, self._on_write_error)
        self._refresh_list()

    def action_cancel_entry(self) -> None:
        for entry in self._entries_at_cursor():
            if self._saved_current_for_join is not None and entry is self.db.current:
                self._changes.cancel_delete(entry)  # cancels staged deletion of prev
                self.db.current = self._saved_current_for_join
                self._saved_current_for_join = None
                self._saved_current = None
            elif self._saved_current is not None and entry is self.db.current:
                self.db.current = self._saved_current
                self._saved_current = None
            else:
                self._changes.cancel_delete(entry)
                self._changes.discard(entry)
        self._refresh_list()

    def action_cancel_all(self) -> None:
        if self._saved_current_for_join is not None:
            self.db.current = self._saved_current_for_join
            self._saved_current_for_join = None
        if self._saved_current is not None:
            self.db.current = self._saved_current
            self._saved_current = None
        self._changes.clear()
        self._refresh_list()

    # ── Selection ─────────────────────────────────────────────────────────────

    def action_toggle_select(self) -> None:
        row = self._highlighted_row()
        if isinstance(row, EntryRow):
            key = row.entry.start
            if key is None:
                return
            if key in self._selected_starts:
                self._selected_starts.discard(key)
            else:
                self._selected_starts.add(key)
            self._refresh_list()
            n = len(self._selected_starts)
            self.notify(f"{n} selected" if n else "Selection cleared")
        elif isinstance(row, GroupHeader):
            keys = {e.start for e in row.entries if e.start}
            if keys.issubset(self._selected_starts):
                self._selected_starts.difference_update(keys)
            else:
                self._selected_starts.update(keys)
            self._refresh_list()
            n = len(self._selected_starts)
            self.notify(f"{n} selected" if n else "Selection cleared")

    def action_clear_selection(self) -> None:
        self._selected_starts.clear()
        self._refresh_list()

    # ── Entry CRUD actions ────────────────────────────────────────────────────

    def action_new_entry(self) -> None:
        def handle(entry: DBEntry | None) -> None:
            if entry is not None:
                self._changes.add_new(entry)
                self._refresh_list()

        self.push_screen(self._open_entry_modal(prefill=self._recent_prefill()), handle)

    def action_start_tracking(self) -> None:
        prefill = self._recent_prefill()
        entry = DBEntry(
            start=datetime.now(UTC),
            customer=prefill.get("customer"),
            project=prefill.get("project"),
        )

        def handle(new_entry: DBEntry | None) -> None:
            if new_entry is None:
                return  # Cancelled — state unchanged
            # Confirmed: now stop any running entry, then start the new one
            if self._saved_current_for_join is not None:
                self._changes.cancel_delete(self.db.current)
                self._saved_current_for_join = None
            self._saved_current = None
            if self.db.current:
                self.db.end()
            self.db.current = new_entry
            self._refresh_list()

        self.push_screen(self._open_entry_modal(entry), handle)

    def action_stop_tracking(self) -> None:
        if self._saved_current_for_join is not None:
            self._changes.cancel_delete(self.db.current)
            self._saved_current_for_join = None
        self._saved_current = None
        if not self.db.current:
            self.notify("No active tracking session.", severity="warning")
            return
        stopped_start = self.db.current.start
        self.db.end()
        self._refresh_list(focus_entry_start=stopped_start)

    def _do_edit_entry(self, entry: DBEntry) -> None:
        is_current = entry is self.db.current

        def handle(new_entry: DBEntry | None) -> None:
            if new_entry is None:
                return
            if is_current:
                if new_entry.end is None:
                    if self._saved_current_for_join is None and self._saved_current is None:
                        self._saved_current = entry
                    self.db.current = new_entry
                else:
                    self._saved_current = None
                    self.db.current = None
                    self.db.add_entry(new_entry)
            else:
                self._changes.update(entry, new_entry)
            self._refresh_list()

        self.push_screen(self._open_entry_modal(entry), handle)

    def action_edit_entry(self) -> None:
        row = self._highlighted_row()

        if isinstance(row, GroupHeader):
            customer, project, description, ticket, tag_names = (
                row.key[0], row.key[1], row.key[2], row.key[3], row.key[4],
            )
            template = DBEntry(
                customer=customer,
                project=project,
                description=description,
                ticket=ticket,
                tags=[DBTag(name=n) for n in sorted(tag_names)] or None,
            )
            entries = row.entries

            def handle_group(new_entry: DBEntry | None) -> None:
                if new_entry is None:
                    return
                for old in entries:
                    updated = old.model_copy(
                        update={
                            "customer": new_entry.customer,
                            "project": new_entry.project,
                            "description": new_entry.description,
                            "ticket": new_entry.ticket,
                            "tags": new_entry.tags,
                        }
                    )
                    self._changes.update(old, updated)
                self._refresh_list()

            self.push_screen(
                self._open_entry_modal(template, show_times=False, group_entries=row.entries),
                handle_group,
            )
            return

        entry = self._selected_entry()
        if entry is not None:
            self._do_edit_entry(entry)

    def _do_continue_entry(self, entry: DBEntry) -> None:
        if entry is self.db.current:
            return
        if self.db.current:
            self.db.end()
        self.db.current = DBEntry(
            start=datetime.now(UTC),
            customer=entry.customer,
            project=entry.project,
            description=entry.description,
            ticket=entry.ticket,
            ticket_url=entry.ticket_url,
            tags=entry.tags,
        )
        self._refresh_list()
        self.notify("Continuing…")

    def action_continue_entry(self) -> None:
        # Group header: stop any running entry, start new from common fields (no editor).
        row = self._highlighted_row()
        if isinstance(row, GroupHeader):
            entries = row.entries

            def _common(vals: list[str | None]) -> str | None:
                unique = set(vals)
                return vals[0] if len(unique) == 1 else None

            customer = _common([e.customer for e in entries])
            project = _common([e.project for e in entries])
            description = _common([e.description for e in entries])
            ticket = _common([e.ticket for e in entries])
            # Tags: common only if every entry has exactly the same tag set
            tag_sets = [frozenset(t.name for t in (e.tags or [])) for e in entries]
            common_tags: list[DBTag] | None = None
            if len(set(tag_sets)) == 1 and tag_sets[0]:
                first = entries[0]
                common_tags = list(first.tags) if first.tags else None
            if self.db.current:
                self.db.end()
            self.db.current = DBEntry(
                start=datetime.now(UTC),
                customer=customer,
                project=project,
                description=description,
                ticket=ticket,
                tags=common_tags,
            )
            self._refresh_list()
            self.notify("Continuing group…")
            return

        # Entry row: stop current if running, start new from this entry's fields immediately.
        entry = self._selected_entry()
        if entry is not None:
            self._do_continue_entry(entry)

    def _continue_entry_with_edit(self, source: DBEntry) -> None:
        """Stop any running entry, create a new entry from source's fields, open editor.

        On cancel, the previously-running entry (if any) is restored exactly as it was.
        """
        was_current = self.db.current
        was_snapshot = was_current.model_copy() if was_current else None
        if was_current is not None:
            self.db.end()  # sets was_current.end, saves to DB, sets db.current = None

        new_start = was_current.end if was_current is not None else datetime.now(UTC)
        new_entry = DBEntry(
            start=new_start,
            customer=source.customer,
            project=source.project,
            description=source.description,
            ticket=source.ticket,
            ticket_url=source.ticket_url,
            tags=source.tags,
        )
        self.db.current = new_entry

        def handle(edited_entry: DBEntry | None) -> None:
            if edited_entry is None:
                # Cancel: remove the new entry, restore the previously-running entry.
                self.db.current = None
                if was_current is not None:
                    self.db.delete_entry(was_current)
                    self.db.current = was_snapshot
            else:
                if was_current is not None:
                    # The editor shows HH:MM precision; forcing the exact handoff
                    # timestamp avoids sub-minute overlaps and start-time collisions
                    # with the stopped entry (which shares the same HH:MM bucket).
                    edited_entry = edited_entry.model_copy(update={"start": new_entry.start})
                self.db.current = edited_entry
            self._refresh_list()

        self.push_screen(self._open_entry_modal(new_entry), handle)

    def action_continue_entry_with_edit(self) -> None:
        """Continue the highlighted entry and open the edit modal (C).

        On the currently running entry the modal opens first; the old entry is
        only stopped (and saved) when the user confirms.  Cancelling leaves the
        running entry unchanged.
        """
        entry = self._selected_entry()
        if entry is None:
            return
        if entry is not self.db.current:
            self._continue_entry_with_edit(entry)
            return

        # Running entry: capture the handoff time now but don't stop yet.
        source = entry
        new_start = datetime.now(UTC)
        template = DBEntry(
            start=new_start,
            customer=source.customer,
            project=source.project,
            description=source.description,
            ticket=source.ticket,
            ticket_url=source.ticket_url,
            tags=source.tags,
        )

        def handle(edited: DBEntry | None) -> None:
            if edited is None:
                return  # Cancelled — running entry unchanged
            # Stop the old entry at the captured handoff time
            source.end = new_start
            self.db.add_entry(source)
            self.db.current = None   # clear current slot in DB
            # Start the new entry, forcing its start to the handoff time
            self.db.current = edited.model_copy(update={"start": new_start})
            self._refresh_list()

        self.push_screen(self._open_entry_modal(template), handle)

    def _do_delete_entry(self, entry: DBEntry) -> None:
        if entry.start is None:
            self.db.delete_entry(entry)
            self._refresh_list()
            return
        was_new = self._changes.is_new(entry)
        if not was_new:
            self._changes.mark_deleted(entry)
        self._changes.discard(entry)
        self._refresh_list()

    def action_delete_entry(self) -> None:
        entry = self._selected_entry()
        if entry is not None:
            self._do_delete_entry(entry)

    def _do_open_ticket(self, entry: DBEntry) -> None:
        url = entry.ticket_url
        if not url and entry.ticket:
            host = self._ticket_provider.default_host if self._ticket_provider else "github.com"
            url = ticket_url_from_ref(entry.ticket, host)
        if url:
            webbrowser.open_new(url)
        else:
            self.notify("No ticket URL for this entry.", severity="warning")

    def action_open_ticket(self) -> None:
        entry = self._selected_entry()
        if entry is not None:
            self._do_open_ticket(entry)

    # ── Search ────────────────────────────────────────────────────────────────

    def action_search(self) -> None:
        entries = self._changes.apply(list(self.db.dump_entries()))

        def handle(result: tuple[str, DBEntry | list[DBEntry]] | None) -> None:
            if result is None:
                return
            action, payload = result
            if action == "edit":
                self._do_edit_entry(payload)  # type: ignore[arg-type]
            elif action == "continue":
                self._do_continue_entry(payload)  # type: ignore[arg-type]
            elif action == "continue_edit":
                self._continue_entry_with_edit(payload)  # type: ignore[arg-type]
            elif action == "delete":
                self._do_delete_entry(payload)  # type: ignore[arg-type]
            elif action == "open_ticket":
                self._do_open_ticket(payload)  # type: ignore[arg-type]
            elif action == "bulk_edit":
                self._open_bulk_edit(payload)  # type: ignore[arg-type]

        self.push_screen(SearchModal(entries), handle)

    # ── Bulk edit ─────────────────────────────────────────────────────────────

    def action_bulk_edit(self) -> None:
        since = None if self._load_all else datetime.now(UTC) - timedelta(days=183)
        entries = self._changes.apply(list(self.db.dump_entries(since=since)))
        if not self._timeline_mode:
            entries = self._apply_filter(entries)
        # Use selected entries if any, otherwise all visible non-current entries
        if self._selected_starts:
            target = [
                e for e in entries
                if e.start in self._selected_starts and e is not self.db.current
            ]
        else:
            target = [e for e in entries if e is not self.db.current and e.start is not None]
        if not target:
            self.notify("No entries to bulk edit.", severity="warning")
            return
        self._open_bulk_edit(target)

    def _open_bulk_edit(self, entries: list[DBEntry]) -> None:
        def handle(changes: dict[str, str | None] | None) -> None:
            if not changes:
                return
            self._apply_bulk_edit(entries, changes)
            self._selected_starts.clear()

        self.push_screen(BulkEditModal(entries), handle)

    def _apply_bulk_edit(self, entries: list[DBEntry], changes: dict[str, str | None]) -> None:
        host = self._ticket_provider.default_host if self._ticket_provider else "github.com"
        for entry in entries:
            update: dict[str, object] = {}
            for key, val in changes.items():
                if key == "customer":
                    update["customer"] = val
                elif key == "project":
                    update["project"] = val
                elif key == "description":
                    update["description"] = val
                elif key == "ticket":
                    update["ticket"] = val
                    if "ticket-url" not in changes:
                        update["ticket_url"] = ticket_url_from_ref(val, host) if val else None
                elif key == "ticket-url":
                    update["ticket_url"] = val
                elif key == "tags":
                    if val is None:
                        update["tags"] = None
                    else:
                        update["tags"] = [DBTag(name=t.strip()) for t in val.split(",") if t.strip()] or None
            if update:
                self._changes.update(entry, entry.model_copy(update=update))
        n = len(entries)
        self.notify(f"Staged bulk edit for {n} {'entry' if n == 1 else 'entries'}.")
        self._refresh_list()

    # ── Grouping ──────────────────────────────────────────────────────────────

    def action_grouping(self) -> None:
        def handle(fields: frozenset[str] | None) -> None:
            if fields is not None and fields != self._group_fields:
                self._group_fields = fields
                self._collapsed.clear()
                self._initialized = False
                self._refresh_list()

        self.push_screen(GroupingModal(self._group_fields), handle)

    # ── Misc actions ──────────────────────────────────────────────────────────

    def action_copy_to_backend(self) -> None:
        from .backends.json_backend import JsonBackend
        from .backends.sqlite_backend import SqliteBackend

        if isinstance(self.db, JsonBackend):
            source_name = "json"
        elif isinstance(self.db, SqliteBackend):
            source_name = "sqlite"
        else:
            source_name = "postgres"

        def handle(count: int | None) -> None:
            if count is not None:
                self.notify(f"Copied {count} {'entry' if count == 1 else 'entries'}.")

        self.push_screen(CopyToBackendModal(self.db, source_name), handle)

    def action_toggle_theme(self) -> None:
        if self._config is None:
            self.theme = "textual-light" if self.dark else "textual-dark"  # type: ignore[attr-defined]
            return
        # Determine the current effective mode (auto resolves once here)
        if self._config.color_scheme == "auto":
            detected = detect_system_dark()
            currently_dark = detected if detected is not None else True
        else:
            currently_dark = self._config.color_scheme == "dark"
        if currently_dark:
            self._config.color_scheme = "light"
            self.theme = self._config.light_theme
            label = f"Light ({self._config.light_theme})"
        else:
            self._config.color_scheme = "dark"
            self.theme = self._config.dark_theme
            label = f"Dark ({self._config.dark_theme})"
        self._config.save()
        self.notify(f"Theme: {label}")

    def action_help(self) -> None:
        self.push_screen(HelpModal(MAIN_HELP_LEFT, MAIN_HELP_RIGHT))

    def action_about(self) -> None:
        if self._config is not None:
            self.push_screen(AboutModal(self._config))

    async def action_quit(self) -> None:
        if self._changes or self._saved_current_for_join is not None or self._saved_current is not None:

            def handle(confirmed: bool | None) -> None:
                if confirmed:
                    self.exit()

            await self.push_screen(ConfirmModal(), handle)
        else:
            self.exit()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        _ = parameters
        if action in ("grouping", "filter_entries"):
            return not self._timeline_mode
        if action in ("commit_entry", "commit_all", "cancel_entry", "cancel_all"):
            return bool(self._changes) or self._saved_current_for_join is not None or self._saved_current is not None
        if action == "clear_selection":
            return bool(self._selected_starts)
        return True
