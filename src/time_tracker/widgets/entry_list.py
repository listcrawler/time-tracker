from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, date, timedelta

from textual.app import ComposeResult
from textual.widgets import Label, ListItem

from ..models import Entry as DBEntry
from .common import (
    DATETIME_FORMAT,
    DEFAULT_GROUP_FIELDS,
    TIME_ONLY_FORMAT,
    GroupKey,
    _format_dt,
    entry_group_key,
    fmt_duration,
    group_entries,
)


def _day_total_secs(entries: list[DBEntry]) -> int:
    from datetime import UTC, datetime as _dt
    total = 0
    for e in entries:
        if e.start:
            end = e.end or _dt.now(UTC)
            total += max(0, int((end - e.start).total_seconds()))
    return total

# ── Column widths (visual characters) ────────────────────────────────────────
_W_START = 11  # "MM-DD HH:MM"
_W_END = 11
_W_DUR = 5  # "HH:MM"
_W_CUST = 14
_W_PROJ = 14
_W_DESC = 34
_W_TICKET = 12


def col_header(hidden_cols: frozenset[str] = frozenset()) -> str:
    line = f"  {'Start':{_W_START}}  {'End':{_W_END}}  {'Dur':{_W_DUR}}"
    if "customer" not in hidden_cols:
        line += f"  {'Customer':{_W_CUST}}"
    if "project" not in hidden_cols:
        line += f"  {'Project':{_W_PROJ}}"
    if "description" not in hidden_cols:
        line += f"  {'Description':{_W_DESC}}"
    if "ticket" not in hidden_cols:
        line += f"  {'Ticket':{_W_TICKET}}"
    if "tags" not in hidden_cols:
        line += "  Tags"
    return line


def _trunc(s: str, width: int) -> str:
    return s[: width - 1] + "…" if len(s) > width else s


# ── Display-row types ─────────────────────────────────────────────────────────


@dataclass
class DayHeader:
    day: date
    entries: list[DBEntry] = dc_field(default_factory=list)


@dataclass
class GroupHeader:
    day: date
    key: GroupKey
    entries: list[DBEntry]
    group_fields: frozenset[str] = dc_field(default_factory=lambda: DEFAULT_GROUP_FIELDS)


@dataclass
class EntryRow:
    entry: DBEntry
    in_group: bool = False
    show_date: bool = True


@dataclass
class GapRow:
    duration: timedelta


@dataclass
class OverlapRow:
    duration: timedelta


@dataclass
class CrossDateEndRow:
    entry: DBEntry


@dataclass
class WeekHeader:
    week_start: date  # Monday of the week
    entries: list[DBEntry]


@dataclass
class MonthHeader:
    year: int
    month: int
    entries: list[DBEntry]


@dataclass
class LoadMoreRow:
    """Sentinel row at the bottom of a date-capped list."""


@dataclass
class CurrentPlaceholderRow:
    """Sentinel row shown at the top when no entry is currently running."""


# ── ListItem subclasses ───────────────────────────────────────────────────────


class DayItem(ListItem):
    """Full-width day separator — disabled so keyboard nav skips it."""

    DEFAULT_CSS = """
    DayItem {
        background: $primary-darken-3;
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    DayItem > Label { width: 1fr; text-style: bold; }
    """

    def __init__(self, day: date, entries: list[DBEntry] | None = None) -> None:
        super().__init__(disabled=True)
        self._day = day
        self._entries = entries or []

    def compose(self) -> ComposeResult:
        if self._day == date.today():
            label = self._day.strftime("Today, %B %d, %Y")
        else:
            label = self._day.strftime("%A, %B %d, %Y")
        if self._entries:
            total = _day_total_secs(self._entries)
            label += f"  [dim]{fmt_duration(total)}[/dim]"
        yield Label(label)


class GroupItem(ListItem):
    """Expandable/collapsible group-header row."""

    def __init__(
        self,
        header: GroupHeader,
        collapsed: bool,
        is_pending: bool = False,
        hidden_cols: frozenset[str] = frozenset(),
        is_selected: bool = False,
    ) -> None:
        super().__init__(classes="selected" if is_selected else "")
        self._header = header
        self._collapsed = collapsed
        self._is_pending = is_pending
        self._hidden_cols = hidden_cols
        self._is_selected = is_selected

    def _col(self, value: str | None, field: str, width: int) -> str:
        """Return a fixed-width column string; show (~) for non-grouped fields."""
        if field not in self._header.group_fields:
            return f"{'(~)':{width}}"
        return f"{_trunc(value or '', width):{width}}"

    def compose(self) -> ComposeResult:
        n = len(self._header.entries)
        total = sum(
            int((e.end - e.start).total_seconds())
            for e in self._header.entries
            if e.start and e.end
        )
        k = self._header.key
        indicator = "▶" if self._collapsed else "▼"
        if self._is_selected:
            dirty = "[cyan]◉[/cyan] "
        elif self._is_pending:
            dirty = "[yellow]◆[/yellow] "
        else:
            dirty = "  "
        count_col = f"{indicator} {n}×"
        dur_col = fmt_duration(total)
        customer = self._col(k[0], "customer", _W_CUST)
        project = self._col(k[1], "project", _W_PROJ)
        description = self._col(k[2], "description", _W_DESC)
        ticket = self._col(k[3], "ticket", _W_TICKET)
        if "tags" not in self._header.group_fields:
            tags_str = "(~)"
        else:
            tags_str = "  ".join(f"#{t}" for t in sorted(k[4]))

        line = (
            f"{dirty}[bold]{count_col:{_W_START}}[/bold]"
            f"  {' ' * _W_END}"
            f"  [bold]{dur_col:{_W_DUR}}[/bold]"
        )
        if "customer" not in self._hidden_cols:
            line += f"  {customer}"
        if "project" not in self._hidden_cols:
            line += f"  {project}"
        if "description" not in self._hidden_cols:
            line += f"  {description}"
        if "ticket" not in self._hidden_cols:
            line += f"  {ticket}"
        if "tags" not in self._hidden_cols:
            line += f"  {tags_str}"
        yield Label(line)


class EntryItem(ListItem):
    """A single time-entry row."""

    def __init__(
        self,
        entry: DBEntry,
        *,
        is_current: bool,
        in_group: bool,
        is_pending: bool = False,
        is_deleted: bool = False,
        is_joined: bool = False,
        show_date: bool = True,
        show_cross_date_end: bool = False,
        hidden_cols: frozenset[str] = frozenset(),
        is_selected: bool = False,
    ) -> None:
        cross_date = (
            not is_current
            and entry.start is not None
            and entry.end is not None
            and entry.start.date() != entry.end.date()
        )
        classes = " ".join(
            filter(
                None,
                [
                    "current" if is_current else "",
                    "cross-date" if (cross_date and show_cross_date_end) else "",
                    "selected" if is_selected else "",
                ],
            )
        )
        super().__init__(classes=classes)
        self.entry = entry
        self._is_current = is_current
        self._in_group = in_group
        self._is_pending = is_pending
        self._is_deleted = is_deleted
        self._is_joined = is_joined
        self._show_date = show_date
        self._cross_date = cross_date
        self._show_cross_date_end = show_cross_date_end
        self._hidden_cols = hidden_cols
        self._is_selected = is_selected

    def _build_line(self) -> str:
        e = self.entry
        fmt = DATETIME_FORMAT if self._show_date else TIME_ONLY_FORMAT

        if self._is_current:
            status = "●"
        elif self._is_deleted:
            status = "[red]✗[/red]"
        elif self._is_selected:
            status = "[cyan]◉[/cyan]"
        elif self._is_pending:
            status = "[yellow]◆[/yellow]"
        elif self._in_group:
            status = "[dim]·[/dim]"
        else:
            status = " "

        start_plain = _format_dt(e.start, fmt) if e.start else " " * _W_START
        join_marker = "←" if self._is_joined else ""
        start_col = f"{start_plain}{join_marker:{_W_START + 1 - len(start_plain)}}"
        if self._is_pending or self._is_joined:
            start_col = f"[yellow]{start_col}[/yellow]"

        if self._is_current:
            end_col = f"{'running...':{_W_END}}"
        elif e.end and not self._cross_date:
            end_str = _format_dt(e.end, fmt)
            end_col = (
                f"[yellow]{end_str:{_W_END}}[/yellow]"
                if self._is_pending
                else f"{end_str:{_W_END}}"
            )
        else:
            end_col = " " * _W_END

        dur_col = " " * _W_DUR
        if e.start and e.end:
            dur_col = fmt_duration(int((e.end - e.start).total_seconds()))
        elif e.start and self._is_current:
            from datetime import UTC, datetime as _dt
            dur_col = fmt_duration(int((_dt.now(UTC) - e.start).total_seconds()))

        customer = f"{_trunc(e.customer or '', _W_CUST):{_W_CUST}}"
        project = f"{_trunc(e.project or '', _W_PROJ):{_W_PROJ}}"
        description = f"{_trunc(e.description or '', _W_DESC):{_W_DESC}}"
        ticket = f"{_trunc(e.ticket or '', _W_TICKET):{_W_TICKET}}"
        tags_str = "  ".join(f"#{t.name}" for t in e.tags) if e.tags else ""

        rest = f"{start_col} {end_col}  {dur_col:{_W_DUR}}"
        if "customer" not in self._hidden_cols:
            rest += f"  {customer}"
        if "project" not in self._hidden_cols:
            rest += f"  {project}"
        if "description" not in self._hidden_cols:
            rest += f"  {description}"
        if "ticket" not in self._hidden_cols:
            rest += f"  {ticket}"
        if "tags" not in self._hidden_cols:
            rest += f"  {tags_str}"
        if self._is_deleted:
            rest = f"[dim strike]{rest}[/dim strike]"
        return f"{status} {rest}"

    def compose(self) -> ComposeResult:
        yield Label(self._build_line())

        e = self.entry
        if self._is_current and e.start:
            start_date = e.start.astimezone(UTC).astimezone().date()
            if start_date != date.today():
                yield Label(f"  [dim]{start_date.strftime('%Y-%m-%d')}[/dim]")

        fmt = DATETIME_FORMAT if self._show_date else TIME_ONLY_FORMAT
        if self._cross_date and self._show_cross_date_end and e.end is not None:
            end_str = _format_dt(e.end, fmt)
            end_col2 = (
                f"[yellow]{end_str:{_W_END}}[/yellow]"
                if self._is_pending
                else f"{end_str:{_W_END}}"
            )
            line2 = f"  {' ' * _W_START}  {end_col2}"
            if self._is_deleted:
                line2 = f"[dim strike]{line2}[/dim strike]"
            yield Label(line2)

    def refresh_display(self) -> None:
        """Rebuild the first Label (the main entry line) in-place."""
        try:
            self.query("Label").first(Label).update(self._build_line())
        except Exception:
            pass


class GapItem(ListItem):
    """Gap marker between two consecutive timeline entries — not selectable."""

    DEFAULT_CSS = """
    GapItem { height: 1; padding: 0 1; }
    GapItem > Label { color: $text-muted; }
    """

    def __init__(self, duration: timedelta) -> None:
        super().__init__(disabled=True)
        self._duration = duration

    def compose(self) -> ComposeResult:
        yield Label(f"  ╌╌╌  gap  {fmt_duration(int(self._duration.total_seconds()))}  ╌╌╌")


class OverlapItem(ListItem):
    """Overlap marker between two consecutive timeline entries — not selectable."""

    DEFAULT_CSS = """
    OverlapItem { height: 1; padding: 0 1; }
    OverlapItem > Label { color: $warning; text-style: bold; }
    """

    def __init__(self, duration: timedelta) -> None:
        super().__init__(disabled=True)
        self._duration = duration

    def compose(self) -> ComposeResult:
        yield Label(f"  ⚠  overlap  {fmt_duration(int(self._duration.total_seconds()))}")


class CurrentPlaceholderItem(ListItem):
    """Empty row shown at the top when no entry is currently running."""

    DEFAULT_CSS = """
    CurrentPlaceholderItem {
        padding: 0 1;
        border-top: tall $success;
        border-bottom: tall $success;
    }
    """

    def __init__(self) -> None:
        super().__init__(disabled=True)

    def compose(self) -> ComposeResult:
        yield Label(f"  [dim]{'no running entry':{_W_START + _W_END + 4}}[/dim]")


class LoadMoreItem(ListItem):
    """Selectable sentinel at the bottom of a date-capped list."""

    DEFAULT_CSS = """
    LoadMoreItem { height: 1; padding: 0 1; }
    LoadMoreItem > Label { color: $text-muted; }
    LoadMoreItem:hover > Label { color: $text; }
    """

    def compose(self) -> ComposeResult:
        yield Label("── showing last 6 months · press Enter to load all ──")


class WeekItem(ListItem):
    """Collapsible week-summary row."""

    DEFAULT_CSS = """
    WeekItem {
        background: $primary-darken-2;
        height: 1;
        padding: 0 1;
    }
    WeekItem > Label { width: 1fr; color: $text-muted; }
    """

    def __init__(self, header: WeekHeader, collapsed: bool) -> None:
        super().__init__()
        self._header = header
        self._collapsed = collapsed

    def compose(self) -> ComposeResult:
        ws = self._header.week_start
        we = ws + timedelta(days=6)
        total = sum(
            int((e.end - e.start).total_seconds())
            for e in self._header.entries
            if e.start and e.end
        )
        indicator = "▶" if self._collapsed else "▼"
        label = (
            f"{indicator} Week {ws.strftime('%b %d')} – {we.strftime('%b %d')}  "
            f"{fmt_duration(total)}"
        )
        yield Label(label)


class MonthItem(ListItem):
    """Collapsible month-summary row."""

    DEFAULT_CSS = """
    MonthItem {
        background: $primary-darken-1;
        height: 1;
        padding: 0 1;
    }
    MonthItem > Label { width: 1fr; color: $text-muted; text-style: bold; }
    """

    def __init__(self, header: MonthHeader, collapsed: bool) -> None:
        super().__init__()
        self._header = header
        self._collapsed = collapsed

    def compose(self) -> ComposeResult:
        from datetime import date as _date
        month_label = _date(self._header.year, self._header.month, 1).strftime("%B %Y")
        total = sum(
            int((e.end - e.start).total_seconds())
            for e in self._header.entries
            if e.start and e.end
        )
        indicator = "▶" if self._collapsed else "▼"
        label = (
            f"{indicator} {month_label}  "
            f"{fmt_duration(total)}"
        )
        yield Label(label)


class CrossDateEndItem(ListItem):
    """End-time line for a cross-date entry, shown in the end-date section."""

    DEFAULT_CSS = """
    CrossDateEndItem { height: 1; padding: 0 1; }
    CrossDateEndItem > Label { color: $text-muted; }
    """

    def __init__(
        self, entry: DBEntry, *, is_pending: bool = False, is_deleted: bool = False
    ) -> None:
        super().__init__(disabled=True)
        self._entry = entry
        self._is_pending = is_pending
        self._is_deleted = is_deleted

    def compose(self) -> ComposeResult:
        e = self._entry
        end_str = _format_dt(e.end, TIME_ONLY_FORMAT) if e.end else " " * _W_END
        end_col = (
            f"[yellow]{end_str:{_W_END}}[/yellow]" if self._is_pending else f"{end_str:{_W_END}}"
        )
        line = f"  {' ' * _W_START}  {end_col}"
        if self._is_deleted:
            line = f"[dim strike]{line}[/dim strike]"
        yield Label(line)


__all__ = [
    "col_header",
    "DayHeader",
    "WeekHeader",
    "MonthHeader",
    "GroupHeader",
    "EntryRow",
    "GapRow",
    "OverlapRow",
    "CrossDateEndRow",
    "LoadMoreRow",
    "CurrentPlaceholderRow",
    "DayItem",
    "WeekItem",
    "MonthItem",
    "GroupItem",
    "EntryItem",
    "GapItem",
    "OverlapItem",
    "CrossDateEndItem",
    "CurrentPlaceholderItem",
    "LoadMoreItem",
    "entry_group_key",
    "group_entries",
    "DEFAULT_GROUP_FIELDS",
]
