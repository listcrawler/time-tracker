from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, Input, Label, ListItem, ListView

from ..integrations.base import TicketProvider, TicketRef


def _split_ticket_ref(ref: str, provider: TicketProvider) -> tuple[str, str, str, str]:
    """Split 'owner/repo#number' into (host, owner, repo, query).
    Falls back to provider defaults for any part that can't be parsed."""
    host = provider.default_host
    owner = provider.default_owner
    repo = provider.default_repo
    query = ""
    if "#" in ref:
        repo_part, issue = ref.rsplit("#", 1)
        if "/" in repo_part:
            owner, repo = repo_part.rsplit("/", 1)
        query = f"#{issue}"
    elif ref:
        query = ref
    return host, owner, repo, query


class TicketPickerModal(ModalScreen[TicketRef | None]):
    """Search and select a GitHub issue to fill the ticket field."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel", show=False),
        Binding("return", "select_ticket", "Select", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("space", "select_ticket", "Select", show=False),
        Binding("ctrl+up", "prev_field", "Prev field", show=False),
        Binding("ctrl+down", "next_field", "Next field", show=False),
        Binding("ctrl+left", "prev_repo_col", "Prev repo col", show=False),
        Binding("ctrl+right", "next_repo_col", "Next repo col", show=False),
    ]

    def __init__(self, provider: TicketProvider, initial_ticket: str = "") -> None:
        super().__init__()
        self._provider = provider
        self._include_closed = False
        self._results: list[TicketRef] = []
        self._search_timer: Timer | None = None
        if initial_ticket:
            self._init_host, self._init_owner, self._init_repo, self._init_query = (
                _split_ticket_ref(initial_ticket, provider)
            )
        else:
            self._init_host = provider.last_host or provider.default_host
            self._init_owner = provider.last_owner or provider.default_owner
            self._init_repo = provider.last_repo or provider.default_repo
            self._init_query = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label("Search GitHub Issues", id="picker-title")
            with Horizontal(id="picker-repo-row"):
                with Vertical(classes="picker-field"):
                    yield Label("Host")
                    yield Input(value=self._init_host, placeholder="github.com", id="picker-host")
                yield Label("/", classes="picker-sep")
                with Vertical(classes="picker-field"):
                    yield Label("Owner")
                    yield Input(value=self._init_owner, placeholder="owner", id="picker-owner")
                yield Label("/", classes="picker-sep")
                with Vertical(classes="picker-field"):
                    yield Label("Repo")
                    yield Input(value=self._init_repo, placeholder="repo", id="picker-repo")
            with Horizontal(id="picker-search-row"):
                yield Input(value=self._init_query, placeholder="Search issues…", id="picker-query")
                yield Button("[ ] closed", id="picker-toggle-closed")
            yield ListView(id="picker-list")
            yield Label("", id="picker-status")

    def on_mount(self) -> None:
        self.query_one("#picker-host", Input).focus()
        self._trigger_search()

    @on(Input.Changed, "#picker-host, #picker-owner, #picker-repo, #picker-query")
    def _on_any_input_changed(self, _: Input.Changed) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(0.35, self._trigger_search)

    @on(Input.Submitted)
    def _on_any_submitted(self, _: Input.Submitted) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        self._trigger_search()

    @on(Button.Pressed, "#picker-toggle-closed")
    def _toggle_closed(self) -> None:
        self._include_closed = not self._include_closed
        label = "[x] closed" if self._include_closed else "[ ] closed"
        self.query_one("#picker-toggle-closed", Button).label = label
        if self._search_timer is not None:
            self._search_timer.stop()
        self._trigger_search()

    def _trigger_search(self) -> None:
        host = self.query_one("#picker-host", Input).value.strip()
        owner = self.query_one("#picker-owner", Input).value.strip()
        repo = self.query_one("#picker-repo", Input).value.strip()
        query = self.query_one("#picker-query", Input).value.strip()
        self._provider.last_host = host
        self._provider.last_owner = owner
        self._provider.last_repo = repo
        self.query_one("#picker-status", Label).update("Searching…")
        self._run_search(query, owner, repo, self._include_closed)

    @work(thread=True, exclusive=True)
    def _run_search(self, query: str, owner: str, repo: str, include_closed: bool) -> None:
        try:
            results = self._provider.search(
                query, owner=owner, repo=repo, include_closed=include_closed
            )
            self.app.call_from_thread(self._apply_results, results)
        except Exception as exc:
            self.app.call_from_thread(
                self.query_one("#picker-status", Label).update,
                f"[red]Error: {exc}[/red]",
            )

    def _apply_results(self, results: list[TicketRef]) -> None:
        self._results = results
        lv = self.query_one("#picker-list", ListView)
        lv.remove_children()
        if not results:
            self.query_one("#picker-status", Label).update("No results.")
            return
        items = []
        for ref in results:
            state = "[green]open[/green]" if ref.state == "open" else "[dim]closed[/dim]"
            items.append(ListItem(Label(f"[bold]{ref.id}[/bold]  {ref.title}  {state}")))
        lv.mount(*items)
        lv.call_after_refresh(lambda: setattr(lv, "index", 0))
        n = len(results)
        self.query_one("#picker-status", Label).update(f"{n} result{'s' if n != 1 else ''}")

    @on(ListView.Selected, "#picker-list")
    def _on_selected(self, event: ListView.Selected) -> None:
        try:
            idx = list(self.query_one("#picker-list", ListView)._nodes).index(event.item)
        except ValueError:
            return
        if 0 <= idx < len(self._results):
            self.dismiss(self._results[idx])

    def action_cursor_up(self) -> None:
        lv = self.query_one("#picker-list", ListView)
        lv.action_cursor_up()
        lv.focus()

    def action_cursor_down(self) -> None:
        lv = self.query_one("#picker-list", ListView)
        lv.action_cursor_down()
        lv.focus()

    def action_select_ticket(self) -> None:
        lv = self.query_one("#picker-list", ListView)
        if lv.index is not None and 0 <= lv.index < len(self._results):
            self.dismiss(self._results[lv.index])

    # ── Field navigation ──────────────────────────────────────────────────────

    def _field_ids(self) -> list[str]:
        return ["picker-host", "picker-owner", "picker-repo", "picker-query"]

    def _repo_col_ids(self) -> list[str]:
        """Column fields in repo row: host, owner, repo."""
        return ["picker-host", "picker-owner", "picker-repo"]

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
        widget = self.query_one(f"#{ids[idx % len(ids)]}", Input)
        widget.focus()

    def _repo_col_idx(self) -> int:
        """Get current column index in repo row."""
        focused = self.focused
        if focused and focused.id:
            ids = self._repo_col_ids()
            try:
                return ids.index(focused.id)
            except ValueError:
                pass
        return 0

    def _focus_repo_col(self, idx: int) -> None:
        """Focus a repo row column."""
        ids = self._repo_col_ids()
        widget = self.query_one(f"#{ids[idx % len(ids)]}", Input)
        widget.focus()

    def action_next_field(self) -> None:
        self._focus_field(self._focused_field_idx() + 1)

    def action_prev_field(self) -> None:
        self._focus_field(self._focused_field_idx() - 1)

    def action_next_repo_col(self) -> None:
        if self.focused and self.focused.id in self._repo_col_ids():
            self._focus_repo_col(self._repo_col_idx() + 1)
        else:
            self.query_one("#picker-host", Input).focus()

    def action_prev_repo_col(self) -> None:
        if self.focused and self.focused.id in self._repo_col_ids():
            self._focus_repo_col(self._repo_col_idx() - 1)
        else:
            self.query_one("#picker-repo", Input).focus()

    def action_dismiss_modal(self) -> None:
        # First escape: clear list selection if active
        lv = self.query_one("#picker-list", ListView)
        if lv.index is not None:
            lv.index = None
            return
        # Second escape: cancel the modal
        self.dismiss(None)
