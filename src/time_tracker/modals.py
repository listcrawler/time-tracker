from __future__ import annotations

from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import Button, Checkbox, Input, Label, ListItem, ListView, Select, Static

from .backends.base import StorageBackend
from .models import Entry as DBEntry
from .widgets.common import GROUPABLE_FIELDS, _format_dt, fmt_duration


def _parse_filter_values(raw: str) -> list[str]:
    """Split a filter string on commas, respecting double-quoted literals."""
    tokens: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in raw:
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "," and not in_quotes:
            token = "".join(current).strip()
            if token:
                tokens.append(token)
            current = []
        else:
            current.append(ch)
    token = "".join(current).strip()
    if token:
        tokens.append(token)
    return tokens


def _filter_current_token(raw: str) -> tuple[str, str, str]:
    """Split filter input into (prefix_up_to_and_including_comma, space, partial_token).

    Respects double-quoted values containing commas.
    Returns ("", "", raw) when there is no unquoted comma.
    """
    in_quotes = False
    last_comma = -1
    for i, ch in enumerate(raw):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "," and not in_quotes:
            last_comma = i
    if last_comma == -1:
        return "", "", raw
    after = raw[last_comma + 1 :]
    stripped = after.lstrip(" ")
    space = after[: len(after) - len(stripped)]
    return raw[: last_comma + 1], space, stripped


class FilterFieldSuggester(Suggester):
    """Inline completer for comma-separated filter values.

    Completes only the token currently being typed, excluding values
    that have already been entered before the last comma.
    """

    def __init__(self, suggestions: list[str]) -> None:
        super().__init__(use_cache=False, case_sensitive=False)
        self._suggestions = suggestions

    async def get_suggestion(self, value: str) -> str | None:
        prefix, space, current = _filter_current_token(value)
        if not current:
            return None
        entered = {v.casefold() for v in _parse_filter_values(prefix.rstrip(","))}
        lower = current.casefold()
        for s in self._suggestions:
            if s.casefold() in entered:
                continue
            if s.casefold().startswith(lower):
                return prefix + space + s
        return None


class ConfirmModal(ModalScreen[bool]):
    """Ask the user to confirm before discarding uncommitted changes."""

    BINDINGS = [
        Binding("y", "confirm", "Quit", show=False),
        Binding("n", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("You have uncommitted changes.\nQuit anyway?", id="modal-title")
            with Horizontal(id="buttons"):
                yield Button("Quit (y)", variant="error", id="confirm")
                yield Button("Cancel (n)", variant="primary", id="cancel")

    @on(Button.Pressed, "#confirm")
    def do_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def do_cancel(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# ── Navigation mixin used by both GroupingModal and FilterModal ───────────────

_NAV_BINDINGS = [
    Binding("ctrl+down", "next_field", "↓ field", show=False),
    Binding("ctrl+up", "prev_field", "↑ field", show=False),
    Binding("ctrl+home", "first_field", "⇤ field", show=False),
    Binding("ctrl+end", "last_field", "⇥ field", show=False),
]


class GroupingModal(ModalScreen[frozenset[str] | None]):
    """Dialog for selecting which fields entries are grouped by."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("enter", "confirm", "Apply", show=False, priority=True),
        *_NAV_BINDINGS,
    ]

    def __init__(self, current: frozenset[str]) -> None:
        self._current = current
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="grouping-dialog"):
            yield Label("Group by fields", id="grouping-title")
            for f in GROUPABLE_FIELDS:
                yield Checkbox(f.capitalize(), value=(f in self._current), id=f"cb-{f}")
            with Horizontal(id="grouping-buttons"):
                yield Button("Apply", variant="primary", id="grouping-apply")
                yield Button("Cancel", id="grouping-cancel")

    def _selected_fields(self) -> frozenset[str]:
        return frozenset(f for f in GROUPABLE_FIELDS if self.query_one(f"#cb-{f}", Checkbox).value)

    # ── Field navigation ──────────────────────────────────────────────────

    def _cb_ids(self) -> list[str]:
        return [f"cb-{f}" for f in GROUPABLE_FIELDS]

    def _focused_idx(self) -> int:
        focused = self.focused
        if focused and focused.id:
            ids = self._cb_ids()
            try:
                return ids.index(focused.id)
            except ValueError:
                pass
        return 0

    def _focus_cb(self, idx: int) -> None:
        ids = self._cb_ids()
        self.query_one(f"#{ids[idx % len(ids)]}", Checkbox).focus()

    def action_next_field(self) -> None:
        self._focus_cb(self._focused_idx() + 1)

    def action_prev_field(self) -> None:
        self._focus_cb(self._focused_idx() - 1)

    def action_first_field(self) -> None:
        self._focus_cb(0)

    def action_last_field(self) -> None:
        self._focus_cb(-1)

    # ── Apply / cancel ────────────────────────────────────────────────────

    def action_confirm(self) -> None:
        self.dismiss(self._selected_fields())

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#grouping-apply")
    def do_apply(self) -> None:
        self.action_confirm()

    @on(Button.Pressed, "#grouping-cancel")
    def do_cancel(self) -> None:
        self.action_cancel()


# ── Filter modal ──────────────────────────────────────────────────────────────


class _SuggList(ListView):
    """Non-focusable suggestion list — navigated via ↑/↓ from the input."""

    can_focus = False


class FilterModal(ModalScreen[dict[str, str] | None]):
    """Filter dialog with ranked suggestions and inline completion."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("return", "confirm", "Apply", show=False),
        Binding("ctrl+down", "next_field", "↓ field", show=False),
        Binding("ctrl+up", "prev_field", "↑ field", show=False),
        Binding("ctrl+home", "first_field", "⇤ field", show=False),
        Binding("ctrl+end", "last_field", "⇥ field", show=False),
        Binding("down", "sugg_down", "Sugg ↓", show=False),
        Binding("up", "sugg_up", "Sugg ↑", show=False),
        Binding("right", "pick_sugg", "Pick", show=False, priority=True),
    ]

    _FIELDS: list[tuple[str, str]] = [
        ("from", "From date (YYYY-MM-DD)"),
        ("to", "To date   (YYYY-MM-DD)"),
        ("customer", "Customer"),
        ("project", "Project"),
        ("description", "Description"),
        ("tag", "Tag"),
        ("ticket", "Ticket"),
    ]

    # Fields that have ranked suggestions
    _HAS_SUGG: frozenset[str] = frozenset({"customer", "project", "description", "tag", "ticket"})

    def __init__(
        self,
        current: dict[str, str],
        suggestions: dict[str, list[str]],
    ) -> None:
        self._current = current
        self._all_sugg = suggestions
        self._focused_field: str | None = None
        self._filtered_sugg: list[str] = []
        self._sugg_idx: int | None = None
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="filter-dialog"):
            yield Label("Filter entries", id="filter-title")
            with Horizontal(id="filter-main"):
                with Vertical(id="filter-inputs"):
                    for key, label in self._FIELDS:
                        yield Label(label, classes="filter-label")
                        yield Input(value=self._current.get(key, ""), id=f"fi-{key}")
                with Vertical(id="filter-sugg-col"):
                    yield Label(
                        "Suggestions  [dim]alt+↑↓  → pick[/dim]",
                        id="filter-sugg-title",
                    )
                    yield _SuggList(id="filter-sugg-list")
            with Horizontal(id="filter-buttons"):
                yield Button("Apply", variant="primary", id="filter-apply")
                yield Button("Clear", variant="error", id="filter-clear")
                yield Button("Cancel", id="filter-cancel")

    def on_mount(self) -> None:
        for key in self._HAS_SUGG:
            sugg = self._all_sugg.get(key, [])
            if sugg:
                self.query_one(f"#fi-{key}", Input).suggester = FilterFieldSuggester(sugg)
        self.query_one(f"#fi-{self._FIELDS[0][0]}", Input).focus()

    # ── Suggestions panel ─────────────────────────────────────────────────

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        widget_id = event.widget.id or ""
        if isinstance(event.widget, Input) and widget_id.startswith("fi-"):
            field = widget_id[3:]  # strip "fi-"
            if field != self._focused_field:
                self._focused_field = field
                self._sugg_idx = None
                self._update_sugg(event.widget.value)

    @on(Input.Changed)
    def _on_input_changed(self, event: Input.Changed) -> None:
        if (event.input.id or "").startswith("fi-"):
            self._sugg_idx = None
            self._update_sugg(event.value)

    def _update_sugg(self, typed: str = "") -> None:
        lv = self.query_one("#filter-sugg-list", _SuggList)
        lv.clear()
        self._filtered_sugg = []
        field = self._focused_field
        if not field or field not in self._HAS_SUGG:
            return
        prefix, _space, current = _filter_current_token(typed)
        entered = {v.casefold() for v in _parse_filter_values(prefix.rstrip(","))}
        lower = current.lower()
        self._filtered_sugg = [
            s
            for s in self._all_sugg.get(field, [])
            if s.casefold() not in entered and (not lower or lower in s.lower())
        ][:20]
        for s in self._filtered_sugg:
            lv.append(ListItem(Label(s)))

    # ── Suggestion navigation (Alt+↑/↓/→) ────────────────────────────────

    def action_sugg_down(self) -> None:
        if not self._filtered_sugg:
            return
        n = len(self._filtered_sugg)
        self._sugg_idx = 0 if self._sugg_idx is None else min(self._sugg_idx + 1, n - 1)
        self.query_one("#filter-sugg-list", _SuggList).index = self._sugg_idx

    def action_sugg_up(self) -> None:
        if not self._filtered_sugg:
            return
        n = len(self._filtered_sugg)
        self._sugg_idx = n - 1 if self._sugg_idx is None else max(self._sugg_idx - 1, 0)
        self.query_one("#filter-sugg-list", _SuggList).index = self._sugg_idx

    def action_pick_sugg(self) -> None:
        if self._sugg_idx is None or not self._filtered_sugg:
            return
        val = self._filtered_sugg[self._sugg_idx]
        if self._focused_field:
            inp = self.query_one(f"#fi-{self._focused_field}", Input)
            prefix, space, _ = _filter_current_token(inp.value)
            new_val = prefix + (space or "") + val
            inp.value = new_val
            inp.cursor_position = len(new_val)
        self._sugg_idx = None
        self.query_one("#filter-sugg-list", _SuggList).index = None

    # ── Field navigation (Ctrl+↑/↓/Home/End) ─────────────────────────────

    def _input_ids(self) -> list[str]:
        return [f"fi-{key}" for key, _ in self._FIELDS]

    def _focused_field_idx(self) -> int:
        ids = self._input_ids()
        fid = f"fi-{self._focused_field}" if self._focused_field else ids[0]
        try:
            return ids.index(fid)
        except ValueError:
            return 0

    def _focus_field(self, idx: int) -> None:
        ids = self._input_ids()
        self.query_one(f"#{ids[idx % len(ids)]}", Input).focus()

    def action_next_field(self) -> None:
        self._focus_field(self._focused_field_idx() + 1)

    def action_prev_field(self) -> None:
        self._focus_field(self._focused_field_idx() - 1)

    def action_first_field(self) -> None:
        self._focus_field(0)

    def action_last_field(self) -> None:
        self._focus_field(-1)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "pick_sugg":
            return self._sugg_idx is not None
        return True

    # ── Apply / cancel ────────────────────────────────────────────────────

    def _collect(self) -> dict[str, str]:
        return {key: self.query_one(f"#fi-{key}", Input).value.strip() for key, _ in self._FIELDS}

    def action_confirm(self) -> None:
        self.dismiss(self._collect())

    @on(Input.Submitted)
    def _on_submitted(self) -> None:
        self.dismiss(self._collect())

    def action_cancel(self) -> None:
        # First escape: clear suggestion selection if active
        if self._sugg_idx is not None:
            self._sugg_idx = None
            self.query_one("#filter-sugg-list", _SuggList).index = None
            return
        # Second escape: cancel the modal
        self.dismiss(None)

    @on(Button.Pressed, "#filter-apply")
    def _do_apply(self) -> None:
        self.dismiss(self._collect())

    @on(Button.Pressed, "#filter-clear")
    def _do_clear(self) -> None:
        self.dismiss({})

    @on(Button.Pressed, "#filter-cancel")
    def _do_cancel(self) -> None:
        self.dismiss(None)


class CopyToBackendModal(ModalScreen[int | None]):
    """Copy all completed entries to a different storage backend."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("return", "confirm", "Copy", show=False),
        Binding("ctrl+down", "next_field", "↓ field", show=False),
        Binding("ctrl+up", "prev_field", "↑ field", show=False),
    ]

    _LABELS: dict[str, str] = {"json": "JSON", "sqlite": "SQLite", "postgres": "PostgreSQL"}
    _DEFAULTS: dict[str, str] = {
        "json": str(Path.home() / ".local/share/time-tracker/entries.json"),
        "sqlite": str(Path.home() / ".local/share/time-tracker/entries.db"),
        "postgres": "postgresql://time-tracker@localhost:5432/time-tracker",
    }
    _DEFAULT_TARGET: dict[str, str] = {
        "json": "sqlite",
        "sqlite": "json",
        "postgres": "sqlite",
    }

    def __init__(self, db: StorageBackend, source_name: str) -> None:
        self._db = db
        self._source_name = source_name
        super().__init__()

    def compose(self) -> ComposeResult:
        source_label = self._LABELS.get(self._source_name, self._source_name)
        options = [(label, key) for key, label in self._LABELS.items() if key != self._source_name]
        default_target = self._DEFAULT_TARGET.get(self._source_name, options[0][1])
        with Vertical(id="copy-dialog"):
            yield Label("Copy entries to backend", id="copy-title")
            yield Label(f"Source:  [bold]{source_label}[/bold]", id="copy-source")
            yield Label("Target backend")
            yield Select(
                options,
                id="copy-backend",
                allow_blank=False,
                value=default_target,
            )
            yield Label("Path / URL")
            yield Input(value=self._DEFAULTS[default_target], id="copy-path")
            yield Static("", id="copy-status")
            with Horizontal(id="copy-buttons"):
                yield Button("Copy", variant="primary", id="copy-confirm")
                yield Button("Cancel", id="copy-cancel")

    @on(Select.Changed, "#copy-backend")
    def _on_backend_changed(self, event: Select.Changed) -> None:
        val = str(event.value)
        if val in self._DEFAULTS:
            self.query_one("#copy-path", Input).value = self._DEFAULTS[val]

    @on(Button.Pressed, "#copy-confirm")
    def _do_copy(self) -> None:
        backend_name = str(self.query_one("#copy-backend", Select).value)
        path_str = self.query_one("#copy-path", Input).value.strip()
        status = self.query_one("#copy-status", Static)

        if not path_str:
            status.update("[red]Path / URL is required[/red]")
            return

        try:
            dest: StorageBackend
            if backend_name == "json":
                from .backends.json_backend import JsonBackend

                dest = JsonBackend(Path(path_str))
            elif backend_name == "sqlite":
                from .backends.sqlite_backend import SqliteBackend

                dest = SqliteBackend(Path(path_str))
            elif backend_name == "postgres":
                from .backends.postgres_backend import PostgresBackend

                dest = PostgresBackend(path_str)
            else:
                status.update("[red]Unknown backend[/red]")
                return

            entries = [e for e in self._db.dump_entries() if e.end is not None]
            count = 0
            with dest:
                for entry in entries:
                    dest.add_entry(entry)
                    count += 1
            self.dismiss(count)
        except Exception as exc:
            from textual.markup import escape

            status.update(f"[red]{escape(str(exc))}[/red]")

    # ── Field navigation ──────────────────────────────────────────────────

    def _field_ids(self) -> list[str]:
        return ["copy-backend", "copy-path"]

    def _focused_field_idx(self) -> int:
        focused = self.focused
        if focused and focused.id:
            ids = self._field_ids()
            try:
                return ids.index(focused.id)
            except ValueError:
                pass
        return 0

    def _focus_field(self, idx: int) -> None:
        ids = self._field_ids()
        widget = self.query_one(f"#{ids[idx % len(ids)]}")
        widget.focus()

    def action_next_field(self) -> None:
        self._focus_field(self._focused_field_idx() + 1)

    def action_prev_field(self) -> None:
        self._focus_field(self._focused_field_idx() - 1)

    # ── Apply / cancel ────────────────────────────────────────────────────

    def action_confirm(self) -> None:
        self._do_copy()

    @on(Button.Pressed, "#copy-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


# ── Bulk-edit modal ───────────────────────────────────────────────────────────

_NO_CHANGE = "[no change]"


class BulkEditModal(ModalScreen[dict[str, str | None] | None]):
    """Set a new value for any subset of fields across multiple entries at once.

    Fields that keep the [no change] sentinel are left untouched.
    Pressing Backspace / Delete in an empty field restores the sentinel.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+down", "next_field", "↓ field", show=False),
        Binding("ctrl+up", "prev_field", "↑ field", show=False),
        Binding("ctrl+home", "first_field", "⇤ field", show=False),
        Binding("ctrl+end", "last_field", "⇥ field", show=False),
    ]

    _FIELDS: list[tuple[str, str]] = [
        ("customer", "Customer"),
        ("project", "Project"),
        ("description", "Description"),
        ("ticket", "Ticket"),
        ("ticket-url", "Ticket URL"),
        ("tags", "Tags  (comma-separated)"),
    ]

    def __init__(self, entries: list[DBEntry]) -> None:
        self._entries = entries
        super().__init__()

    def compose(self) -> ComposeResult:
        n = len(self._entries)
        with Vertical(id="bulk-dialog"):
            yield Label(
                f"Bulk Edit  ({n} {'entry' if n == 1 else 'entries'})",
                id="bulk-title",
            )
            yield Label(
                "Leave [dim][no change][/dim] to keep each entry's existing value.",
                id="bulk-hint",
            )
            for key, label in self._FIELDS:
                yield Label(label, classes="bulk-label")
                yield Input(value=_NO_CHANGE, id=f"be-{key}")
            yield Label("", id="bulk-error")
            with Horizontal(id="bulk-buttons"):
                yield Button("Apply", variant="primary", id="bulk-apply")
                yield Button("Cancel", id="bulk-cancel")

    def on_mount(self) -> None:
        self.query_one("#be-customer", Input).focus()

    # ── Sentinel key handling ─────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        focused = self.focused
        if not isinstance(focused, Input):
            return
        fid = focused.id or ""
        if not fid.startswith("be-"):
            return
        val = focused.value
        if val == _NO_CHANGE:
            if event.character is not None:
                # Printable key: clear sentinel so the character lands in an empty field
                focused.value = ""
                focused.cursor_position = 0
                # Do NOT prevent_default — Input will insert the character normally
            elif event.key in ("backspace", "delete"):
                event.prevent_default()  # Sentinel stays
        elif val == "" and event.key in ("backspace", "delete"):
            focused.value = _NO_CHANGE
            focused.cursor_position = len(_NO_CHANGE)
            event.prevent_default()

    # ── Field navigation ──────────────────────────────────────────────────────

    def _input_ids(self) -> list[str]:
        return [f"be-{key}" for key, _ in self._FIELDS]

    def _focused_idx(self) -> int:
        focused = self.focused
        if focused and focused.id:
            ids = self._input_ids()
            try:
                return ids.index(focused.id)
            except ValueError:
                pass
        return 0

    def _focus_field(self, idx: int) -> None:
        ids = self._input_ids()
        self.query_one(f"#{ids[idx % len(ids)]}", Input).focus()

    def action_next_field(self) -> None:
        self._focus_field(self._focused_idx() + 1)

    def action_prev_field(self) -> None:
        self._focus_field(self._focused_idx() - 1)

    def action_first_field(self) -> None:
        self._focus_field(0)

    def action_last_field(self) -> None:
        self._focus_field(-1)

    # ── Validation and collection ─────────────────────────────────────────────

    def _try_confirm(self) -> None:
        error = self.query_one("#bulk-error", Label)
        changes: dict[str, str | None] = {}
        for key, label in self._FIELDS:
            inp = self.query_one(f"#be-{key}", Input)
            val = inp.value
            if val == _NO_CHANGE:
                continue
            stripped = val.strip()
            if key in ("customer", "project") and stripped and " " in stripped:
                error.update(f"[red]{label} cannot contain spaces[/red]")
                inp.focus()
                return
            if key == "tags" and stripped:
                tag_names = [t.strip() for t in stripped.split(",") if t.strip()]
                for t in tag_names:
                    if " " in t:
                        error.update("[red]Tag names cannot contain spaces[/red]")
                        inp.focus()
                        return
            changes[key] = stripped or None
        self.dismiss(changes)

    @on(Button.Pressed, "#bulk-apply")
    def _do_apply(self) -> None:
        self._try_confirm()

    @on(Input.Submitted)
    def _on_submitted(self) -> None:
        self._try_confirm()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#bulk-cancel")
    def _do_cancel(self) -> None:
        self.dismiss(None)


# ── Search modal ──────────────────────────────────────────────────────────────


_SEARCH_PREFIXES: frozenset[str] = frozenset(("#", "$", "@", "&"))


def _parse_search_query(query: str) -> dict[str, list[str]]:
    """Split a query into prefix groups.

    "$acme @backend docker" → {"$": ["acme"], "@": ["backend"], "": ["docker"]}
    Same prefix → OR within that group.  Different prefixes → AND across groups.
    A bare prefix with no term (e.g. just "$") means "field is non-empty".
    """
    groups: dict[str, list[str]] = {}
    for token in query.split():
        if token[0] in _SEARCH_PREFIXES:
            prefix, term = token[0], token[1:]
        else:
            prefix, term = "", token
        groups.setdefault(prefix, []).append(term)
    return groups


def _score_field_or(terms: list[str], text: str) -> int:
    """OR across terms: best fuzzy score. Empty term = field non-empty check."""
    best = 0
    for term in terms:
        s = _fuzzy_score(term, text) if term else (1 if text else 0)
        if s > best:
            best = s
    return best


def _score_tags_or(terms: list[str], tags: list) -> int:
    """OR across terms × tags: best fuzzy score. Empty term = has any tag."""
    best = 0
    for term in terms:
        if not term:
            s = 1 if tags else 0
        else:
            s = max((_fuzzy_score(term, tag.name) for tag in tags), default=0)
        if s > best:
            best = s
    return best


def _fuzzy_score(query: str, text: str) -> int:
    """Score how well *query* matches *text*. Returns 0 for no match."""
    if not query:
        return 1
    q = query.lower()
    t = text.lower()
    if q == t:
        return 10000
    if t.startswith(q):
        return 9000
    if q in t:
        return 8000
    # Subsequence match: all query chars appear in order inside text
    qi = 0
    for ch in t:
        if qi < len(q) and ch == q[qi]:
            qi += 1
    return 100 if qi == len(q) else 0


class SearchModal(ModalScreen[tuple[str, DBEntry | list[DBEntry]] | None]):
    """Fuzzy search across entry descriptions and tags.

    Type to filter.  Ctrl+J / Ctrl+K or ↑ / ↓ navigate results.
    Enter selects.  Escape cancels.
    Tab moves focus to the result list to unlock action keys:
    e  edit    c  continue    C  continue+edit    d  delete    o  open ticket
    Ctrl+Space toggles selection.  E bulk-edits selected (or all) results.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("ctrl+j", "cursor_down", "Down", show=False, priority=True),
        Binding("ctrl+k", "cursor_up", "Up", show=False, priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("space", "toggle_select", "Select", show=False, priority=True),
        # Action keys — non-priority: fire when the list (not Input) has focus.
        Binding("e", "act_edit", "Edit", show=False),
        Binding("c", "act_continue", "Continue", show=False),
        Binding("C", "act_continue_edit", "Continue+Edit", show=False),
        Binding("d", "act_delete", "Delete", show=False),
        Binding("o", "act_open", "Open ticket", show=False),
        Binding("E", "act_bulk_edit", "Bulk edit", show=False),
    ]

    def __init__(self, entries: list[DBEntry]) -> None:
        super().__init__()
        self._all_entries = entries
        self._results: list[DBEntry] = []
        self._selected: set[int] = set()  # indices into self._results

    def compose(self) -> ComposeResult:
        with Vertical(id="search-dialog"):
            yield Input(placeholder="Search descriptions  $ customer  @ project  # tag  & ticket", id="search-input")
            yield ListView(id="search-list")
            yield Static("", id="search-status")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()
        self._update_results("")

    @on(Input.Changed, "#search-input")
    def _on_query_changed(self, event: Input.Changed) -> None:
        self._update_results(event.value)


    # ── Scoring ───────────────────────────────────────────────────────────

    def _score_entry(self, entry: DBEntry, query: str) -> int:
        if not query:
            return 1
        total = 0
        for prefix, terms in _parse_search_query(query).items():
            if prefix == "":
                s = _score_field_or(terms, entry.description or "")
            elif prefix == "$":
                s = _score_field_or(terms, entry.customer or "")
            elif prefix == "@":
                s = _score_field_or(terms, entry.project or "")
            elif prefix == "&":
                s = _score_field_or(terms, entry.ticket or "")
            elif prefix == "#":
                s = _score_tags_or(terms, entry.tags or [])
            else:
                continue
            if s == 0:
                return 0  # AND: all prefix groups must match
            total += s
        return total

    # ── Results update ────────────────────────────────────────────────────

    def _update_results(self, query: str) -> None:
        q = query.strip()
        scored: list[tuple[int, float, DBEntry]] = []
        for entry in self._all_entries:
            score = self._score_entry(entry, q)
            if score > 0:
                ts = entry.start.timestamp() if entry.start else 0.0
                scored.append((score, ts, entry))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        self._results = [e for _, _, e in scored[:50]]
        self._selected.clear()

        # Single match — select immediately without extra keypress.
        if len(self._results) == 1 and q:
            self.dismiss(("focus", self._results[0]))
            return

        self._rebuild_list()
        # No auto-highlight — first Ctrl+J highlights first, Ctrl+K highlights last.
        self.query_one("#search-list", ListView).index = None
        self._update_status()

    def _rebuild_list(self, *, preserve_index: bool = False) -> None:
        """Rebuild the results list, reflecting current selection state."""
        lv = self.query_one("#search-list", ListView)
        old_index = lv.index if preserve_index else None
        lv.clear()
        for i, entry in enumerate(self._results):
            marker = "[cyan]◉[/cyan] " if i in self._selected else "  "
            lv.append(ListItem(Static(marker + self._entry_label(entry))))
        if old_index is not None:
            lv.index = min(old_index, len(self._results) - 1) if self._results else None

    @staticmethod
    def _entry_label(entry: DBEntry) -> str:
        from rich.markup import escape

        parts: list[str] = []
        if entry.start:
            parts.append(_format_dt(entry.start, "%Y-%m-%d"))
        ctx = "/".join(p for p in [entry.customer, entry.project] if p)
        if ctx:
            parts.append(escape(f"[{ctx}]"))
        if entry.description:
            parts.append(escape(entry.description))
        if entry.tags:
            parts.append("  ".join(f"#{escape(t.name)}" for t in entry.tags))
        return "  ".join(parts) if parts else "(no description)"

    # ── Status panel ──────────────────────────────────────────────────────

    def _highlighted_entry(self) -> DBEntry | None:
        lv = self.query_one("#search-list", ListView)
        if lv.index is not None and 0 <= lv.index < len(self._results):
            return self._results[lv.index]
        return None

    def on_list_view_highlighted(self, _: ListView.Highlighted) -> None:
        self._update_status()

    def _update_status(self) -> None:
        from rich.markup import escape

        panel = self.query_one("#search-status", Static)
        entry = self._highlighted_entry()
        if entry is None:
            sel_note = f"  [cyan]{len(self._selected)} selected[/cyan]" if self._selected else ""
            panel.update(sel_note)
            return
        _FMT = "%Y-%m-%d %H:%M:%S"
        start_str = _format_dt(entry.start, _FMT) if entry.start else "—"
        end_str = _format_dt(entry.end, _FMT) if entry.end else "running…"
        dur_str = ""
        if entry.start and entry.end:
            secs = int((entry.end - entry.start).total_seconds())
            dur_str = f"  ({fmt_duration(secs)})"
        line1 = f"{start_str} → {end_str}{dur_str}"
        ctx_parts: list[str] = []
        if entry.customer:
            ctx_parts.append(escape(entry.customer))
        if entry.project:
            ctx_parts.append(f"/{escape(entry.project)}")
        line2 = "  ".join(ctx_parts)
        line3 = f"Description: {escape(entry.description or '')}"
        line4 = f"Ticket: {escape(entry.ticket or '')}"
        line5 = f"URL: {escape(entry.ticket_url or '')}"
        tags_part = "  ".join(f"#{escape(t.name)}" for t in entry.tags) if entry.tags else ""
        sel_part = f"[cyan]◉ {len(self._selected)} selected[/cyan]" if self._selected else ""
        line6 = "  ".join(p for p in [tags_part, sel_part] if p)
        panel.update("\n".join([line1, line2, line3, line4, line5, line6]))

    # ── Selection ─────────────────────────────────────────────────────────

    def action_toggle_select(self) -> None:
        lv = self.query_one("#search-list", ListView)
        idx = lv.index
        if idx is None or idx >= len(self._results):
            return
        if idx in self._selected:
            self._selected.discard(idx)
        else:
            self._selected.add(idx)
        self._rebuild_list(preserve_index=True)
        self._update_status()

    # ── Navigation ────────────────────────────────────────────────────────

    def action_cursor_down(self) -> None:
        lv = self.query_one("#search-list", ListView)
        if not self._results:
            return
        if lv.index is None:
            lv.index = 0
        else:
            lv.action_cursor_down()
        lv.focus()

    def action_cursor_up(self) -> None:
        lv = self.query_one("#search-list", ListView)
        if not self._results:
            return
        if lv.index is None:
            lv.index = len(self._results) - 1
        else:
            lv.action_cursor_up()
        lv.focus()

    # ── Dismiss helpers ───────────────────────────────────────────────────

    def _dismiss_with_action(self, action: str) -> None:
        entry = self._highlighted_entry()
        self.dismiss((action, entry) if entry is not None else None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    # ── Entry actions (fire when list has focus, after Tab) ───────────────

    def action_act_edit(self) -> None:
        self._dismiss_with_action("edit")

    def action_act_continue(self) -> None:
        self._dismiss_with_action("continue")

    def action_act_continue_edit(self) -> None:
        self._dismiss_with_action("continue_edit")

    def action_act_delete(self) -> None:
        self._dismiss_with_action("delete")

    def action_act_open(self) -> None:
        self._dismiss_with_action("open_ticket")

    def action_act_bulk_edit(self) -> None:
        if not self._results:
            return
        if self._selected:
            entries = [self._results[i] for i in sorted(self._selected)]
        else:
            entries = list(self._results)
        self.dismiss(("bulk_edit", entries))
