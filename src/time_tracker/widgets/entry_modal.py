from __future__ import annotations

import os
import re
import time as _time
from datetime import UTC, date, datetime, timedelta, timezone

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.suggester import Suggester, SuggestFromList
from textual.widgets import Button, Input, Label, ListItem, ListView

from ..integrations.base import TicketProvider, TicketRef, ticket_url_from_ref
from ..models import Entry as DBEntry
from ..models import Tag as DBTag
from .ticket_picker import TicketPickerModal

_MAX_RECENT = 8

DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"

_MONTH_NAMES: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _local_tz() -> timezone:
    """Return the local timezone, trying multiple sources in order of reliability.

    This is resilient to environments where a single source gives wrong results
    (e.g. TZ=UTC set by Git Bash on Windows, or an unconfigured container).
    """
    # 1. Python's astimezone — uses OS API on Windows, TZ env / localtime on Linux.
    try:
        offset = datetime.now().astimezone().utcoffset()
        if offset is not None:
            return timezone(offset)
    except Exception:
        pass
    # 2. /etc/localtime symlink → named zone (Linux/macOS, survives TZ env issues).
    try:
        from zoneinfo import ZoneInfo

        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            tz_name = link.split("zoneinfo/", 1)[1]
            offset = datetime.now(ZoneInfo(tz_name)).utcoffset()
            if offset is not None:
                return timezone(offset)
    except Exception:
        pass
    # 3. /etc/timezone text file (Debian/Ubuntu).
    try:
        from zoneinfo import ZoneInfo

        with open("/etc/timezone") as f:
            tz_name = f.read().strip()
        offset = datetime.now(ZoneInfo(tz_name)).utcoffset()
        if offset is not None:
            return timezone(offset)
    except Exception:
        pass
    # 4. C runtime timezone values (Windows registry-backed; also on Linux/macOS).
    try:
        is_dst = bool(_time.daylight and _time.localtime().tm_isdst > 0)
        secs = -(int(_time.altzone) if is_dst else int(_time.timezone))
        return timezone(timedelta(seconds=secs))
    except Exception:
        pass
    return UTC


def _local_tz_offset() -> str:
    """Return the local UTC offset as '+HH:MM'."""
    total = int(_local_tz().utcoffset(None).total_seconds())
    sign = "+" if total >= 0 else "-"
    h, m = divmod(abs(total) // 60, 60)
    return f"{sign}{h:02d}:{m:02d}"


def _parse_date(s: str) -> date:
    """Parse a date string in various forgiving formats.

    Accepted formats (current year assumed when omitted):
    - ``YYYY-MM-DD``          ISO format
    - ``MMDD``                4-digit month+day  (e.g. 0409 → April 9)
    - ``D/M``, ``D.M``, ``D-M``  day/month, 1–2 digits (e.g. 9/4 → April 9)
    - ``Month D``             name + day  (e.g. April 9, Apr 9)
    - ``D Month``             day + name  (e.g. 9 April)
    """
    s = s.strip()
    today = date.today()
    # ISO
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        pass
    # 4-digit MMDD
    m = re.fullmatch(r"(\d{2})(\d{2})", s)
    if m:
        mn, dy = int(m.group(1)), int(m.group(2))
        if 1 <= mn <= 12 and 1 <= dy <= 31:
            try:
                return date(today.year, mn, dy)
            except ValueError:
                pass
    # D/M (or D.M, D-M)
    m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})", s)
    if m:
        dy, mn = int(m.group(1)), int(m.group(2))
        if 1 <= mn <= 12 and 1 <= dy <= 31:
            try:
                return date(today.year, mn, dy)
            except ValueError:
                pass
    # "Month Day"
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2})", s)
    if m:
        mn_num = _MONTH_NAMES.get(m.group(1).lower())
        if mn_num:
            try:
                return date(today.year, mn_num, int(m.group(2)))
            except ValueError:
                pass
    # "Day Month"
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)", s)
    if m:
        mn_num = _MONTH_NAMES.get(m.group(2).lower())
        if mn_num:
            try:
                return date(today.year, mn_num, int(m.group(1)))
            except ValueError:
                pass
    raise ValueError(f"Cannot parse date: {s!r}")


def _parse_tz_offset(s: str) -> timezone:
    """Parse a timezone offset string like '+02:00' or '-05' into a timezone object."""
    s = s.strip()
    if not s:
        return _local_tz()

    # Handle formats: +2, +02, +0200, +02:00, -5:30, etc.
    m = re.fullmatch(r"([+-])(\d{1,2})(?::?(\d{2}))?", s)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        h_off = int(m.group(2))
        m_off = int(m.group(3)) if m.group(3) else 0
        return timezone(timedelta(hours=sign * h_off, minutes=sign * m_off))

    raise ValueError(f"Cannot parse timezone offset: {s!r}")


def _parse_time(s: str, default_tz: timezone) -> tuple[int, int, timezone]:
    """Parse a time string, returning ``(hour, minute, tz)``.

    Accepted formats (spaces optional, case-insensitive AM/PM):
    - ``HH:MM``, ``H:MM``
    - ``HHMM``  (4 digits), ``HMM``  (3 digits)
    - Any of the above with a trailing AM/PM
    - Any of the above with a trailing UTC offset: ``+2``, ``+02``, ``+0200``, ``+02:00``
    """
    s = s.strip()
    tz = default_tz

    # Strip trailing timezone offset: +2, +02, +0200, +02:00, -5:30, etc.
    tz_m = re.search(r"\s*([+-]\d{1,2}(?::?\d{2})?)\s*$", s)
    if tz_m:
        raw = tz_m.group(1)
        s = s[: tz_m.start()].strip()
        sign = 1 if raw[0] == "+" else -1
        digits = raw[1:].replace(":", "")
        h_off = int(digits[:2]) if len(digits) >= 2 else int(digits)
        m_off = int(digits[2:4]) if len(digits) >= 4 else 0
        tz = timezone(timedelta(hours=sign * h_off, minutes=sign * m_off))

    # Strip trailing AM/PM
    ampm: str | None = None
    ap_m = re.search(r"\s*(AM|PM)\s*$", s, re.IGNORECASE)
    if ap_m:
        ampm = ap_m.group(1).upper()
        s = s[: ap_m.start()].strip()

    # Parse H/M digits
    m2 = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m2:
        hour, minute = int(m2.group(1)), int(m2.group(2))
    else:
        m2 = re.fullmatch(r"(\d{3,4})", s)
        if m2:
            d = m2.group(1)
            hour, minute = (int(d[0]), int(d[1:])) if len(d) == 3 else (int(d[:2]), int(d[2:]))
        else:
            m2 = re.fullmatch(r"(\d{1,2})", s)
            if m2:
                hour, minute = int(m2.group(1)), 0
            else:
                raise ValueError(f"Cannot parse time: {s!r}")

    if ampm == "AM":
        hour = 0 if hour == 12 else hour
    elif ampm == "PM":
        hour = hour if hour == 12 else hour + 12

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time: {hour:02d}:{minute:02d}")

    return hour, minute, tz


def _parse_dt(date_str: str, time_str: str, tz: timezone | None = None) -> datetime:
    """Parse separate date and time strings into an aware datetime."""
    if tz is None:
        tz = _local_tz()
    d = _parse_date(date_str)
    h, m, parsed_tz = _parse_time(time_str, tz)
    return datetime(d.year, d.month, d.day, h, m, 0, tzinfo=parsed_tz)


class TagSuggester(Suggester):
    """Completion suggester for comma-separated tag lists.

    Completes only the last token, excluding tags already entered.
    """

    def __init__(self, suggestions: list[str]) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self._suggestions = suggestions

    async def get_suggestion(self, value: str) -> str | None:
        if "," in value:
            comma_idx = value.rfind(",")
            prefix = value[: comma_idx + 1]
            after_comma = value[comma_idx + 1 :]
            stripped = after_comma.lstrip(" ")
            leading_space = after_comma[: len(after_comma) - len(stripped)]
        else:
            prefix = ""
            leading_space = ""
            stripped = value

        entered = {p.strip().casefold() for p in value.split(",")[:-1]}

        for suggestion in self._suggestions:
            if suggestion.casefold() in entered:
                continue
            if suggestion.casefold().startswith(stripped.casefold()):
                return prefix + leading_space + suggestion
        return None


class EntryModal(ModalScreen[DBEntry | None]):
    """Modal screen for creating or editing a time entry."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel", show=False),
        Binding("return", "save_entry", "Done", show=False),
        Binding("up", "recent_up", "Prev recent", show=False),
        Binding("down", "recent_down", "Next recent", show=False),
        Binding("ctrl+left", "nav_left", "Prev column", show=False),
        Binding("ctrl+right", "nav_right", "Next column", show=False),
        Binding("ctrl+up", "nav_up", "Prev row", show=False),
        Binding("ctrl+down", "nav_down", "Next row", show=False),
        Binding("ctrl+home", "nav_home", "Top of column", show=False),
        Binding("ctrl+end", "nav_end", "Bottom of column", show=False),
    ]

    def __init__(
        self,
        entry: DBEntry | None = None,
        ticket_provider: TicketProvider | None = None,
        show_times: bool = True,
        recent_values: dict[str, list[str]] | None = None,
        prefill: dict[str, str] | None = None,
        group_entries: list[DBEntry] | None = None,
    ):
        super().__init__()
        self._entry = entry
        self._ticket_provider = ticket_provider
        self._show_times = show_times
        self._recent: dict[str, list[str]] = recent_values or {}
        self._prefill: dict[str, str] = prefill or {}
        self._focused_field: str | None = None
        self._recents_values: list[str] = []
        self._desc_from_ticket: str | None = None  # last title auto-filled into description
        # Column-aware navigation
        self._col_fields: dict[str, list[str]] = {
            "primary": [],
            "details": [],
        }
        self._current_col: str = "primary"
        # Group editing: compute common vs. mixed field values
        self._is_group_edit = group_entries is not None
        self._group_common: dict[str, str] = {}
        self._mixed_fields: set[str] = set()
        if group_entries is not None:
            self._group_common, self._mixed_fields = self._compute_group_info(group_entries)

    @staticmethod
    def _compute_group_info(entries: list[DBEntry]) -> tuple[dict[str, str], set[str]]:
        """Return (common_values_by_input_id, mixed_input_ids) for a group of entries."""
        common: dict[str, str] = {}
        mixed: set[str] = set()
        field_map = {
            "customer": "customer",
            "project": "project",
            "description": "description",
            "ticket": "ticket",
            "ticket-url": "ticket_url",
        }
        for input_id, attr in field_map.items():
            values = {getattr(e, attr) or "" for e in entries}
            if len(values) == 1:
                common[input_id] = values.pop()
            else:
                mixed.add(input_id)
        tag_sets = {frozenset(t.name for t in (e.tags or [])) for e in entries}
        if len(tag_sets) == 1:
            common["tags"] = ", ".join(sorted(tag_sets.pop()))
        else:
            mixed.add("tags")
        return common, mixed

    def _val(self, field: str, entry_attr: str | None = None) -> str:
        if self._is_group_edit:
            return self._group_common.get(field, "")
        attr = entry_attr or field
        if self._entry is not None:
            return getattr(self._entry, attr) or ""
        return self._prefill.get(field, "")

    def _input(self, field_id: str, value: str, placeholder: str, mixed: bool = False) -> Input:
        recents = self._recent.get(field_id, [])
        if field_id == "tags":
            suggester: Suggester | None = TagSuggester(recents) if recents else None
        else:
            suggester = SuggestFromList(recents, case_sensitive=False) if recents else None
        return Input(
            value="" if mixed else value,
            placeholder="(mixed)" if mixed else placeholder,
            id=field_id,
            suggester=suggester,
            classes="mixed-field" if mixed else "",
        )

    def compose(self) -> ComposeResult:
        if not self._show_times:
            title = "Edit Group"
        elif self._entry:
            title = "Edit Entry"
        else:
            title = "New Entry"
        with Horizontal(id="dialog"):
            with Vertical(id="form-wrapper"):
                yield Label(title, id="modal-title")
                with Horizontal(id="form-columns"):
                    # ── Left column: Primary fields ──
                    with Vertical(id="primary-col"):
                        if self._show_times:
                            local_tz = _local_tz()
                            tz_hint = _local_tz_offset()
                            today_str = date.today().strftime(DATE_FORMAT)
                            start_local = (
                                self._entry.start.astimezone(local_tz)
                                if self._entry and self._entry.start
                                else None
                            )
                            end_local = (
                                self._entry.end.astimezone(local_tz)
                                if self._entry and self._entry.end
                                else None
                            )
                            start_date_val = (
                                start_local.strftime(DATE_FORMAT) if start_local else today_str
                            )
                            start_time_val = (
                                start_local.strftime(TIME_FORMAT) if start_local else ""
                            )
                            end_date_val = (
                                end_local.strftime(DATE_FORMAT) if end_local else today_str
                            )
                            end_time_val = end_local.strftime(TIME_FORMAT) if end_local else ""
                            yield Label(f"Start date / time  (\\[+HH:MM] defaults to {tz_hint})")
                            with Horizontal(id="start-row"):
                                yield Input(
                                    value=start_date_val, placeholder="YYYY-MM-DD", id="start-date"
                                )
                                yield Input(
                                    value=start_time_val,
                                    placeholder=f"HH:MM [{tz_hint}]",
                                    id="start-time",
                                )
                            self._col_fields["primary"].append("start-date")
                            yield Label("End date / time  (leave time blank if still tracking)")
                            with Horizontal(id="end-row"):
                                yield Input(
                                    value=end_date_val, placeholder="YYYY-MM-DD", id="end-date"
                                )
                                yield Input(
                                    value=end_time_val,
                                    placeholder=f"HH:MM [{tz_hint}]",
                                    id="end-time",
                                )
                            self._col_fields["primary"].append("end-date")
                            yield Label("Timezone  (offset like +02:00 or +2)")
                            with Horizontal(id="tz-row"):
                                yield Input(value=tz_hint, placeholder=tz_hint, id="tz-offset")
                            self._col_fields["primary"].append("tz-offset")
                        yield Label("Description")
                        yield self._input(
                            "description",
                            self._val("description"),
                            "What are you working on?",
                            mixed="description" in self._mixed_fields,
                        )
                        self._col_fields["primary"].append("description")
                    # ── Right column: Details fields ──
                    with Vertical(id="details-col"):
                        yield Label("Customer")
                        yield self._input(
                            "customer",
                            self._val("customer"),
                            "Customer name",
                            mixed="customer" in self._mixed_fields,
                        )
                        self._col_fields["details"].append("customer")
                        yield Label("Project")
                        yield self._input(
                            "project",
                            self._val("project"),
                            "Project name",
                            mixed="project" in self._mixed_fields,
                        )
                        self._col_fields["details"].append("project")
                        yield Label("Ticket")
                        with Horizontal(id="ticket-row"):
                            yield self._input(
                                "ticket",
                                self._val("ticket"),
                                "e.g. owner/repo#123",
                                mixed="ticket" in self._mixed_fields,
                            )
                            if self._ticket_provider is not None:
                                yield Button("Browse…", id="ticket-browse")
                        self._col_fields["details"].append("ticket")
                        yield Label("Ticket URL")
                        yield self._input(
                            "ticket-url",
                            self._val("ticket-url", "ticket_url"),
                            "https://…",
                            mixed="ticket-url" in self._mixed_fields,
                        )
                        self._col_fields["details"].append("ticket-url")
                        if self._is_group_edit:
                            tags_val = self._group_common.get("tags", "")
                        elif self._entry and self._entry.tags:
                            tags_val = ", ".join(sorted(t.name for t in self._entry.tags))
                        else:
                            tags_val = self._prefill.get("tags", "")
                        yield Label("Tags  (comma-separated)")
                        yield self._input(
                            "tags", tags_val, "tag1, tag2", mixed="tags" in self._mixed_fields
                        )
                        self._col_fields["details"].append("tags")
                yield Label("", id="error-msg")
                with Horizontal(id="buttons"):
                    yield Button("Done", variant="primary", id="save")
                    yield Button("Cancel", id="cancel")
            with Vertical(id="recents-col"):
                yield Label("Recent  ↑/↓", id="recents-title")
                yield ListView(id="recents-list")

    def on_mount(self) -> None:
        self._original_values: dict[str, str] = {
            inp.id: inp.value for inp in self.query(Input) if inp.id
        }

    def _handle_key(self, key: str) -> bool:
        """Handle column navigation keys. Returns True if key was handled."""
        if key == "ctrl+left":
            self.action_nav_left()
            return True
        elif key == "ctrl+right":
            self.action_nav_right()
            return True
        elif key == "ctrl+home":
            self.action_nav_home()
            return True
        elif key == "ctrl+end":
            self.action_nav_end()
            return True
        return False

    def on_key(self, event: events.Key) -> None:
        """Handle key press events."""
        if self._handle_key(event.key):
            event.prevent_default()

    # ── Recents panel ─────────────────────────────────────────────────────────

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if isinstance(event.widget, Input):
            self._focused_field = event.widget.id
            # Update current column based on which input is focused
            if event.widget.id in self._col_fields["primary"]:
                self._current_col = "primary"
            elif event.widget.id in self._col_fields["details"]:
                self._current_col = "details"
            self._update_recents(event.widget.id)

    def _update_recents(self, field_id: str | None) -> None:
        lv = self.query_one("#recents-list", ListView)
        lv.clear()
        self._recents_values = []
        if not field_id:
            return
        recents = self._recent.get(field_id, [])[:_MAX_RECENT]
        self._recents_values = recents
        for r in recents:
            lv.append(ListItem(Label(r)))

    def _apply_current_recent(self) -> None:
        lv = self.query_one("#recents-list", ListView)
        idx = lv.index
        if idx is None or idx >= len(self._recents_values) or self._focused_field is None:
            return
        try:
            self.query_one(f"#{self._focused_field}", Input).value = self._recents_values[idx]
        except Exception:
            pass

    @on(ListView.Selected, "#recents-list")
    def _on_recent_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None or idx >= len(self._recents_values) or self._focused_field is None:
            return
        try:
            inp = self.query_one(f"#{self._focused_field}", Input)
            inp.value = self._recents_values[idx]
            inp.focus()
        except Exception:
            pass

    def action_recent_down(self) -> None:
        if not self._recents_values:
            return
        lv = self.query_one("#recents-list", ListView)
        lv.action_cursor_down()
        self._apply_current_recent()

    def action_recent_up(self) -> None:
        if not self._recents_values:
            return
        lv = self.query_one("#recents-list", ListView)
        lv.action_cursor_up()
        self._apply_current_recent()

    def action_nav_left(self) -> None:
        """Move to previous column."""
        if self._current_col == "details":
            self._current_col = "primary"
            self._focus_current_col()

    def action_nav_right(self) -> None:
        """Move to next column."""
        if self._current_col == "primary":
            self._current_col = "details"
            self._focus_current_col()

    def action_nav_up(self) -> None:
        """Move to previous field in column."""
        fields = self._col_fields[self._current_col]
        if not fields or not self._focused_field:
            return
        try:
            idx = fields.index(self._focused_field)
            if idx > 0:
                self.query_one(f"#{fields[idx - 1]}", Input).focus()
        except (ValueError, IndexError):
            if fields:
                self.query_one(f"#{fields[0]}", Input).focus()

    def action_nav_down(self) -> None:
        """Move to next field in column."""
        fields = self._col_fields[self._current_col]
        if not fields or not self._focused_field:
            return
        try:
            idx = fields.index(self._focused_field)
            if idx < len(fields) - 1:
                self.query_one(f"#{fields[idx + 1]}", Input).focus()
        except (ValueError, IndexError):
            if fields:
                self.query_one(f"#{fields[-1]}", Input).focus()

    def action_nav_home(self) -> None:
        """Jump to first field in column."""
        fields = self._col_fields[self._current_col]
        if fields:
            try:
                self.query_one(f"#{fields[0]}", Input).focus()
            except Exception:
                pass

    def action_nav_end(self) -> None:
        """Jump to last field in column."""
        fields = self._col_fields[self._current_col]
        if fields:
            try:
                self.query_one(f"#{fields[-1]}", Input).focus()
            except Exception:
                pass

    def _focus_current_col(self) -> None:
        """Focus the first field in the current column."""
        fields = self._col_fields[self._current_col]
        if fields:
            try:
                self.query_one(f"#{fields[0]}", Input).focus()
            except Exception:
                pass

    # ── Save / Cancel ─────────────────────────────────────────────────────────

    def action_save_entry(self) -> None:
        """Save entry (for Return key binding)."""
        self.save()

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        customer = self.query_one("#customer", Input).value.strip() or None
        project = self.query_one("#project", Input).value.strip() or None
        description = self.query_one("#description", Input).value.strip() or None
        ticket = self.query_one("#ticket", Input).value.strip() or None
        ticket_url = self.query_one("#ticket-url", Input).value.strip() or None
        tags_str = self.query_one("#tags", Input).value.strip()
        tags = [DBTag(name=t) for t in sorted(t.strip() for t in tags_str.split(",") if t.strip())] or None

        error = self.query_one("#error-msg", Label)
        if customer and " " in customer:
            error.update("[red]Customer name cannot contain spaces[/red]")
            return
        if project and " " in project:
            error.update("[red]Project name cannot contain spaces[/red]")
            return
        if tags:
            for tag in tags:
                if " " in tag.name:
                    error.update("[red]Tag names cannot contain spaces[/red]")
                    return

        if ticket_url is None and ticket:
            host = self._ticket_provider.default_host if self._ticket_provider else "github.com"
            ticket_url = ticket_url_from_ref(ticket, host)

        if self._show_times:
            start_date = self.query_one("#start-date", Input).value.strip()
            start_time = self.query_one("#start-time", Input).value.strip()
            end_date = self.query_one("#end-date", Input).value.strip()
            end_time = self.query_one("#end-time", Input).value.strip()
            tz_offset = self.query_one("#tz-offset", Input).value.strip()
            if not start_date or not start_time:
                error.update("[red]Start date and time are required[/red]")
                return
            if self._entry and self._entry.end and (not end_date or not end_time):
                error.update("[red]End date and time are required for a completed entry[/red]")
                return
            try:
                tz = _parse_tz_offset(tz_offset) if tz_offset else _local_tz()
                start = _parse_dt(start_date, start_time, tz)
                end = _parse_dt(end_date, end_time, tz) if end_date and end_time else None
            except ValueError as exc:
                error.update(f"[red]Invalid date/time: {exc}[/red]")
                return
            self.dismiss(
                DBEntry(
                    start=start,
                    end=end,
                    customer=customer,
                    project=project,
                    description=description,
                    ticket=ticket,
                    ticket_url=ticket_url,
                    tags=tags,
                )
            )
        else:
            self.dismiss(
                DBEntry(
                    customer=customer,
                    project=project,
                    description=description,
                    ticket=ticket,
                    ticket_url=ticket_url,
                    tags=tags,
                )
            )

    @on(Button.Pressed, "#ticket-browse")
    def browse_ticket(self) -> None:
        assert self._ticket_provider is not None
        current = self.query_one("#ticket", Input).value.strip()

        def handle(ref: TicketRef | None) -> None:
            if ref is not None:
                self.query_one("#ticket", Input).value = ref.id
                self.query_one("#ticket-url", Input).value = ref.url
                desc_input = self.query_one("#description", Input)
                current_desc = desc_input.value.strip()
                if not current_desc or current_desc == self._desc_from_ticket:
                    desc_input.value = ref.title
                    self._desc_from_ticket = ref.title

        self.app.push_screen(
            TicketPickerModal(self._ticket_provider, initial_ticket=current), handle
        )

    @on(Input.Submitted)
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.save()

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        # First escape: clear recents list selection if active
        lv = self.query_one("#recents-list", ListView)
        if lv.index is not None:
            lv.index = None
            return
        # Otherwise, restore original value if changed
        focused = self.focused
        if isinstance(focused, Input) and focused.id:
            original = self._original_values.get(focused.id, "")
            if focused.value != original:
                focused.value = original
                focused.cursor_position = len(focused.value)
                return
        self.dismiss(None)
