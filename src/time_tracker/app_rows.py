from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from itertools import groupby

from textual.widgets import ListItem, ListView, Static

from .models import Entry as DBEntry
from .widgets.common import GROUPABLE_FIELDS, GroupKey, _format_dt, fmt_duration
from .widgets.entry_list import (
    DEFAULT_GROUP_FIELDS,
    CrossDateEndItem,
    CrossDateEndRow,
    CurrentPlaceholderItem,
    CurrentPlaceholderRow,
    DayHeader,
    DayItem,
    EntryItem,
    EntryRow,
    GapItem,
    GapRow,
    GroupHeader,
    GroupItem,
    LoadMoreItem,
    LoadMoreRow,
    MonthHeader,
    MonthItem,
    OverlapItem,
    OverlapRow,
    WeekHeader,
    WeekItem,
    col_header,
    group_entries,
)

type _RowT = (
    DayHeader
    | WeekHeader
    | MonthHeader
    | GroupHeader
    | EntryRow
    | GapRow
    | OverlapRow
    | CrossDateEndRow
    | CurrentPlaceholderRow
    | LoadMoreRow
)


class RowsMixin:
    """Mixin providing row building and display for TimeTrackerApp."""

    _timeline_mode: bool
    _group_fields: frozenset[str]
    _collapsed: set[tuple[date, GroupKey]]
    _collapsed_weeks: set[date]
    _collapsed_months: set[tuple[int, int]]
    _rows: list[_RowT]
    _initialized: bool
    _load_all: bool
    _selected_starts: set[datetime]

    # ── Row builders ──────────────────────────────────────────────────────────

    def _build_rows(self, entries: list[DBEntry]) -> list[_RowT]:
        rows: list[_RowT] = []
        current = self.db.current  # type: ignore[attr-defined]
        today = date.today()

        completed = [e for e in entries if e is not current and e.start]
        completed.sort(key=lambda e: e.start or datetime.min, reverse=True)

        def _start_date(e: DBEntry) -> date:
            return e.start.date() if e.start else date.today()

        if "date" not in self._group_fields:
            # ── No date separation: one flat list grouped by remaining fields ──
            if current is not None:
                rows.append(EntryRow(entry=current, show_date=True))
            sentinel = date(1, 1, 1)
            for k, g in group_entries(completed, self._group_fields):
                if len(g) == 1:
                    rows.append(EntryRow(entry=g[0], show_date=True))
                else:
                    rows.append(
                        GroupHeader(day=sentinel, key=k, entries=g, group_fields=self._group_fields)
                    )
                    if (sentinel, k) not in self._collapsed:
                        for e in g:
                            rows.append(EntryRow(entry=e, in_group=True, show_date=True))
            return rows

        # ── Default: group by day with weekly/monthly folding ─────────────────

        # Cutoffs aligned to Monday boundaries to avoid splitting weeks.
        # recent:      this week (Monday..today)       → individual day headers
        # by_week:     previous 4 complete weeks        → week-header rows
        # by_month:    everything older                 → month-header rows
        week_cutoff = today - timedelta(days=today.weekday())   # this Monday
        month_cutoff = week_cutoff - timedelta(weeks=4)         # 4 Mondays ago

        recent = [e for e in completed if _start_date(e) >= week_cutoff]
        by_week = [e for e in completed if month_cutoff <= _start_date(e) < week_cutoff]
        by_month_list = [e for e in completed if _start_date(e) < month_cutoff]

        # Cross-date entries indexed by their end date
        cross_by_end: dict[date, list[DBEntry]] = {}
        for e in completed:
            if e.start and e.end and e.start.date() != e.end.date():
                cross_by_end.setdefault(e.end.date(), []).append(e)

        # Most-recent date for the top day header (scoped to recent / current)
        top_date: date | None = None
        if current and current.start:
            top_date = current.start.date()
        elif recent:
            top_date = _start_date(recent[0])

        # Current (running) entry — above the day header (always shown)
        if current is not None:
            rows.append(EntryRow(entry=current, show_date=False))
        else:
            rows.append(CurrentPlaceholderRow())

        # Top day header
        if top_date is not None:
            rows.append(DayHeader(day=top_date))

        # Helper: emit groups and cross-date markers for a single day
        def _emit_day(day: date, day_entries: list[DBEntry]) -> None:
            for k, g in group_entries(day_entries, self._group_fields):
                if len(g) == 1:
                    rows.append(EntryRow(entry=g[0], show_date=False))
                else:
                    rows.append(
                        GroupHeader(day=day, key=k, entries=g, group_fields=self._group_fields)
                    )
                    if (day, k) not in self._collapsed:
                        for e in g:
                            rows.append(EntryRow(entry=e, in_group=True, show_date=False))
            for e in cross_by_end.get(day, []):
                rows.append(CrossDateEndRow(entry=e))

        # ── Recent: individual day headers (this week) ───────────────────────
        top_date_visited = False
        for day, day_iter in groupby(recent, key=_start_date):
            if day == top_date:
                top_date_visited = True
            else:
                rows.append(DayHeader(day=day))
            _emit_day(day, list(day_iter))

        # Cross-date end lines for top_date when it has no completed entries
        if top_date is not None and not top_date_visited:
            for e in cross_by_end.get(top_date, []):
                rows.append(CrossDateEndRow(entry=e))

        # ── Weekly: week-header rows (previous 4 complete weeks) ─────────────
        def _week_monday(e: DBEntry) -> date:
            d = _start_date(e)
            return d - timedelta(days=d.weekday())

        for week_start, week_iter in groupby(by_week, key=_week_monday):
            week_entries = list(week_iter)
            rows.append(WeekHeader(week_start=week_start, entries=week_entries))
            if week_start not in self._collapsed_weeks:
                for day, day_iter in groupby(week_entries, key=_start_date):
                    rows.append(DayHeader(day=day))
                    _emit_day(day, list(day_iter))

        # ── Monthly: month-header rows (older than 4 weeks) ──────────────────
        def _month_key(e: DBEntry) -> tuple[int, int]:
            d = _start_date(e)
            return (d.year, d.month)

        for (year, month), month_iter in groupby(by_month_list, key=_month_key):
            month_entries = list(month_iter)
            rows.append(MonthHeader(year=year, month=month, entries=month_entries))
            if (year, month) not in self._collapsed_months:
                for week_start, week_iter in groupby(month_entries, key=_week_monday):
                    week_entries_m = list(week_iter)
                    rows.append(WeekHeader(week_start=week_start, entries=week_entries_m))
                    if week_start not in self._collapsed_weeks:
                        for day, day_iter in groupby(week_entries_m, key=_start_date):
                            rows.append(DayHeader(day=day))
                            _emit_day(day, list(day_iter))

        return rows

    def _build_timeline_rows(self, entries: list[DBEntry]) -> list[_RowT]:
        timed = sorted(
            [e for e in entries if e.start],
            key=lambda e: e.start or datetime.min,
            reverse=True,
        )

        rows: list[_RowT] = []
        if timed:
            rows.append(DayHeader(day=timed[0].start.date()))  # type: ignore[union-attr]
        for i, entry in enumerate(timed):
            rows.append(EntryRow(entry=entry, show_date=False))

            if i < len(timed) - 1:
                newer = entry
                older = timed[i + 1]
                assert newer.start is not None and older.start is not None

                if newer.start.date() != older.start.date():
                    rows.append(DayHeader(day=older.start.date()))
                elif older.end is not None:
                    # Floor to whole minutes for comparison to avoid spurious
                    # sub-minute gaps/overlaps from seconds in stored timestamps.
                    ns = newer.start.replace(second=0, microsecond=0)
                    oe = older.end.replace(second=0, microsecond=0)
                    # Don't align if it would produce a zero-duration entry
                    if newer.end is not None and ns >= newer.end.replace(second=0, microsecond=0):
                        ns = newer.start
                    if oe <= older.start.replace(second=0, microsecond=0):
                        oe = older.end
                    delta_secs = (ns - oe).total_seconds()
                    # Only flag gaps/overlaps larger than 1 minute
                    if delta_secs > 60:
                        rows.append(GapRow(duration=timedelta(seconds=delta_secs)))
                    elif delta_secs < -60:
                        rows.append(OverlapRow(duration=timedelta(seconds=-delta_secs)))

        return rows

    # ── List refresh ─────────────────────────────────────────────────────────

    def _refresh_list(
        self,
        *,
        focus_group: tuple[date, GroupKey] | None = None,
        focus_entry_start: datetime | None = None,
    ) -> None:
        lv = self.query_one(ListView)  # type: ignore[attr-defined]
        old_index = lv.index
        lv.index = None  # clear before removing so watch_index unhighlights cleanly
        lv.remove_children()

        since = None if self._load_all else datetime.now(UTC) - timedelta(days=183)
        entries = self._changes.apply(list(self.db.dump_entries(since=since)))  # type: ignore[attr-defined]
        if self._saved_current_for_join is not None and self.db.current is not None:  # type: ignore[attr-defined]
            # Suppress the duplicate prev entry: db.current already covers its time slot.
            # Exclude by identity so db.current itself (same start, also flagged deleted) is kept.
            cur_start = self.db.current.start  # type: ignore[attr-defined]
            entries = [e for e in entries if e is self.db.current or not (e.start == cur_start and self._changes.is_deleted(e))]  # type: ignore[attr-defined]
        if not self._timeline_mode:
            entries = self._apply_filter(entries)  # type: ignore[attr-defined]
        self._update_totals(entries)

        if self._timeline_mode:
            self._rows = self._build_timeline_rows(entries)
        else:
            self._rows = self._build_rows(entries)
            if not self._initialized:
                self._collapsed.update(
                    (r.day, r.key) for r in self._rows if isinstance(r, GroupHeader)
                )
                self._collapsed_weeks.update(
                    r.week_start for r in self._rows if isinstance(r, WeekHeader)
                )
                self._collapsed_months.update(
                    (r.year, r.month) for r in self._rows if isinstance(r, MonthHeader)
                )
                self._initialized = True
                self._rows = self._build_rows(entries)

        if not self._load_all:
            self._rows.append(LoadMoreRow())

        hidden = self._hidden_cols()  # type: ignore[attr-defined]
        try:
            col_hdr = self.query_one("#col-header", Static)  # type: ignore[attr-defined]
            col_hdr.update(col_header(hidden))
            if self._timeline_mode:
                col_hdr.add_class("timeline-active")
            else:
                col_hdr.remove_class("timeline-active")
        except Exception:
            pass

        try:
            lv = self.query_one(ListView)  # type: ignore[attr-defined]
            if self._timeline_mode:
                lv.add_class("timeline-active")
            else:
                lv.remove_class("timeline-active")
        except Exception:
            pass

        items: list[ListItem] = []
        for row in self._rows:
            if isinstance(row, DayHeader):
                items.append(DayItem(row.day))
            elif isinstance(row, WeekHeader):
                items.append(WeekItem(row, collapsed=row.week_start in self._collapsed_weeks))
            elif isinstance(row, MonthHeader):
                items.append(MonthItem(row, collapsed=(row.year, row.month) in self._collapsed_months))
            elif isinstance(row, GroupHeader):
                group_starts = {e.start for e in row.entries if e.start}
                items.append(
                    GroupItem(
                        row,
                        collapsed=(row.day, row.key) in self._collapsed,
                        is_pending=any(
                            self._changes.is_pending(e) or self._changes.is_deleted(e)  # type: ignore[attr-defined]
                            for e in row.entries
                        ),
                        hidden_cols=hidden,
                        is_selected=bool(group_starts) and group_starts.issubset(self._selected_starts),
                    )
                )
            elif isinstance(row, GapRow):
                items.append(GapItem(row.duration))
            elif isinstance(row, OverlapRow):
                items.append(OverlapItem(row.duration))
            elif isinstance(row, CrossDateEndRow):
                items.append(
                    CrossDateEndItem(
                        row.entry,
                        is_pending=self._changes.is_pending(row.entry),  # type: ignore[attr-defined]
                        is_deleted=self._changes.is_deleted(row.entry),  # type: ignore[attr-defined]
                    )
                )
            elif isinstance(row, CurrentPlaceholderRow):
                items.append(CurrentPlaceholderItem())
            elif isinstance(row, LoadMoreRow):
                items.append(LoadMoreItem())
            else:
                items.append(
                    EntryItem(
                        row.entry,
                        is_current=row.entry is self.db.current,  # type: ignore[attr-defined]
                        in_group=row.in_group,
                        is_pending=self._changes.is_pending(row.entry) or (row.entry is self.db.current and (self._saved_current_for_join is not None or self._saved_current is not None)),  # type: ignore[attr-defined]
                        is_deleted=self._changes.is_deleted(row.entry) and not (self._saved_current_for_join is not None and row.entry is self.db.current),  # type: ignore[attr-defined]
                        is_joined=self._saved_current_for_join is not None and row.entry is self.db.current,  # type: ignore[attr-defined]
                        show_date=row.show_date,
                        show_cross_date_end=self._timeline_mode,
                        hidden_cols=hidden,
                        is_selected=row.entry.start in self._selected_starts if row.entry.start else False,
                    )
                )

        if items:
            lv.mount(*items)

            def restore_index() -> None:
                if not lv._nodes:
                    return
                if focus_group is not None:
                    for i, row in enumerate(self._rows):
                        if isinstance(row, GroupHeader) and (row.day, row.key) == focus_group:
                            lv.index = i
                            lv.scroll_to_widget(lv._nodes[i], animate=False)
                            return
                if focus_entry_start is not None:
                    for i, row in enumerate(self._rows):
                        if isinstance(row, EntryRow) and row.entry.start == focus_entry_start:
                            if not lv._nodes[i].disabled:
                                lv.index = i
                                lv.scroll_to_widget(lv._nodes[i], animate=False)
                                return
                target = min(old_index, len(lv._nodes) - 1) if old_index is not None else 0
                for i in range(target, len(lv._nodes)):
                    if not lv._nodes[i].disabled:
                        lv.index = i
                        return
                for i in range(target - 1, -1, -1):
                    if not lv._nodes[i].disabled:
                        lv.index = i
                        return

            lv.call_after_refresh(restore_index)

        if self.db.current:  # type: ignore[attr-defined]
            desc = self.db.current.description  # type: ignore[attr-defined]
            self.title = f"Time Tracker — {desc}" if desc else "Time Tracker — Tracking"  # type: ignore[attr-defined]
        else:
            self.title = "Time Tracker"  # type: ignore[attr-defined]

        total_pending = len(self._changes) + (1 if self._saved_current_for_join is not None or self._saved_current is not None else 0)  # type: ignore[attr-defined]
        parts: list[str] = []
        if total_pending:
            parts.append(f"◆ {total_pending} uncommitted")
        if self._selected_starts:
            parts.append(f"◉ {len(self._selected_starts)} selected")
        self.sub_title = "  ·  ".join(parts)  # type: ignore[attr-defined]
        self.refresh_bindings()  # type: ignore[attr-defined]

    # ── Totals and mode line ──────────────────────────────────────────────────

    def _update_totals(self, entries: list[DBEntry]) -> None:
        today = date.today()
        week_start = today - timedelta(days=today.weekday())

        def dur(e: DBEntry) -> int:
            if e.start:
                end = e.end or datetime.now(UTC)
                return max(0, int((end - e.start).total_seconds()))
            return 0

        today_secs = sum(dur(e) for e in entries if e.start and e.start.date() == today)
        week_secs = sum(dur(e) for e in entries if e.start and e.start.date() >= week_start)
        month_secs = sum(
            dur(e)
            for e in entries
            if e.start and e.start.year == today.year and e.start.month == today.month
        )

        try:
            self.query_one("#time-totals", Static).update(  # type: ignore[attr-defined]
                f"Today   [bold]{fmt_duration(today_secs)}[/bold]\n"
                f"Week    [bold]{fmt_duration(week_secs)}[/bold]\n"
                f"Month   [bold]{fmt_duration(month_secs)}[/bold]"
            )
        except Exception:
            pass

        try:
            mode = self.query_one("#mode-line", Static)  # type: ignore[attr-defined]
            parts: list[str] = []
            if self._timeline_mode:
                parts.append("Timeline mode  (t to exit)")
                mode.add_class("timeline-active")
            else:
                mode.remove_class("timeline-active")
                if self._group_fields != DEFAULT_GROUP_FIELDS:
                    active = [f.capitalize() for f in GROUPABLE_FIELDS if f in self._group_fields]
                    parts.append(f"Grouping: {', '.join(active) if active else 'none'}")
            if not self._timeline_mode:
                filter_parts = [f"{k}={v}" for k, v in self._filter.items() if v]  # type: ignore[attr-defined]
                if filter_parts:
                    parts.append(", ".join(filter_parts))
            mode.update("  ·  ".join(parts))
        except Exception:
            pass

    # ── Highlighted row helpers ───────────────────────────────────────────────

    def _highlighted_row(self) -> _RowT | None:
        lv = self.query_one(ListView)  # type: ignore[attr-defined]
        item = lv.highlighted_child
        if item is None:
            return None
        idx = lv._nodes.index(item)
        if idx < len(self._rows):
            return self._rows[idx]
        return None

    def on_list_view_highlighted(self, _event: ListView.Highlighted) -> None:
        self._update_status()

    def on_list_view_selected(self, _event: ListView.Selected) -> None:
        if isinstance(self._highlighted_row(), LoadMoreRow):
            self._load_all = True
            self._refresh_list()  # type: ignore[attr-defined]

    def _selected_entry(self) -> DBEntry | None:
        row = self._highlighted_row()
        if isinstance(row, EntryRow):
            return row.entry
        return None

    # ── Status panel ─────────────────────────────────────────────────────────

    def _update_status(self) -> None:
        try:
            panel = self.query_one("#entry-status", Static)  # type: ignore[attr-defined]
        except Exception:
            return
        row = self._highlighted_row()
        _STATUS_FMT = "%Y-%m-%d %H:%M:%S"
        if isinstance(row, EntryRow):
            e = row.entry
            start_str = _format_dt(e.start, _STATUS_FMT) if e.start else "—"
            if e is self.db.current:  # type: ignore[attr-defined]
                end_str = "running…"
            elif e.end:
                end_str = _format_dt(e.end, _STATUS_FMT)
            else:
                end_str = "—"
            dur_str = ""
            if e.start and e.end:
                secs = int((e.end - e.start).total_seconds())
                dur_str = f"  ({fmt_duration(secs)})"
            line1 = f"{start_str} → {end_str}{dur_str}"
            parts = []
            if e.customer:
                parts.append(e.customer)
            if e.project:
                parts.append(f"/{e.project}")
            line2 = "  ".join(parts)
            line3 = f"Description: {e.description or ''}"
            line4 = f"Ticket: {e.ticket or ''}"
            line5 = f"URL: {e.ticket_url or ''}"
            line6 = "  ".join(f"#{t.name}" for t in e.tags) if e.tags else ""
            panel.update("\n".join([line1, line2, line3, line4, line5, line6]))
        elif isinstance(row, GroupHeader):
            k = row.key
            gf = row.group_fields
            n = len(row.entries)
            total = sum(
                int((e.end - e.start).total_seconds()) for e in row.entries if e.start and e.end
            )
            line1 = f"{n}× entries  total {fmt_duration(total)}"
            parts = []
            if "customer" not in gf:
                parts.append("(mixed)")
            elif k[0]:
                parts.append(k[0])
            if "project" not in gf:
                parts.append("/(mixed)")
            elif k[1]:
                parts.append(f"/{k[1]}")
            line2 = "  ".join(parts)
            if "description" not in gf:
                line3 = "Description: (mixed)"
            else:
                line3 = f"Description: {k[2] or ''}"
            if "ticket" not in gf:
                line4 = "Ticket: (mixed)"
            else:
                line4 = f"Ticket: {k[3] or ''}"
            line5 = ""
            if "tags" not in gf:
                line6 = "(mixed)"
            elif k[4]:
                line6 = "  ".join(f"#{t}" for t in sorted(k[4]))
            else:
                line6 = ""
            panel.update("\n".join([line1, line2, line3, line4, line5, line6]))
        elif isinstance(row, WeekHeader):
            ws = row.week_start
            we = ws + timedelta(days=6)
            n = len(row.entries)
            total = sum(
                int((e.end - e.start).total_seconds()) for e in row.entries if e.start and e.end
            )
            panel.update(
                f"Week {ws.strftime('%b %d')} – {we.strftime('%b %d, %Y')}\n"
                f"{n} {'entry' if n == 1 else 'entries'}  total {fmt_duration(total)}"
            )
        elif isinstance(row, MonthHeader):
            month_label = date(row.year, row.month, 1).strftime("%B %Y")
            n = len(row.entries)
            total = sum(
                int((e.end - e.start).total_seconds()) for e in row.entries if e.start and e.end
            )
            panel.update(
                f"{month_label}\n"
                f"{n} {'entry' if n == 1 else 'entries'}  total {fmt_duration(total)}"
            )
        else:
            panel.update("")
