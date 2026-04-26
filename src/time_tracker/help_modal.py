from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group as RichGroup
from rich.console import RenderableType
from rich.rule import Rule
from rich.table import Table
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

if TYPE_CHECKING:
    pass

# Each entry is one of:
#   (None, "Title")          → section header + auto separator
#   ("key", "description")   → keybinding row
#   ("", "note")             → continuation note (empty key)
#   ("", "")                 → blank spacer row
HelpEntry = tuple[str | None, str]


def _render_help(entries: list[HelpEntry]) -> RichGroup:
    """Render help entries as a group of Rich renderables.

    Section headers use ``Rule`` so they span the full column width without
    truncation.  Key-binding rows are collected into a ``Table.grid``.
    """
    renderables: list[RenderableType] = []
    pending_rows: list[tuple[str, str]] = []

    def _flush() -> None:
        if not pending_rows:
            return
        table = Table.grid(padding=(0, 2))
        table.add_column(width=10, no_wrap=True)
        table.add_column()
        for key, desc in pending_rows:
            table.add_row(f"[bold]{key}[/bold]" if key else "", desc)
        renderables.append(table)
        pending_rows.clear()

    for key, desc in entries:
        if key is None:
            _flush()
            renderables.append(Rule(f"[bold]{desc}[/bold]", style="dim"))
        else:
            pending_rows.append((key, desc))

    _flush()
    return RichGroup(*renderables)


MAIN_HELP_LEFT: list[HelpEntry] = [
    (None, "Entry management"),
    ("n", "New entry"),
    ("s", "Start tracking (opens new entry modal)"),
    ("p", "Stop tracking"),
    ("e", "Edit entry or group"),
    ("c", "Continue entry or group"),
    ("C", "Continue entry + open edit modal"),
    ("d", "Delete entry"),
    ("o", "Open ticket in browser"),
    ("", ""),
    (None, "Commit / undo"),
    ("w", "Write (commit current)"),
    ("W", "Write (commit all)"),
    ("u", "Undo current"),
    ("U", "Undo all"),
    ("", ""),
    (None, "Navigation"),
    ("↓", "Move down"),
    ("↑", "Move up"),
    ("Home", "Jump to top"),
    ("End", "Jump to bottom"),
    ("←", "Collapse group"),
    ("→", "Expand group"),
    ("⇧←", "Collapse all groups"),
    ("⇧→", "Expand all groups"),
]

MAIN_HELP_RIGHT: list[HelpEntry] = [
    (None, "Timeline mode"),
    ("←", "Start time −5 min"),
    ("→", "Start time +5 min"),
    ("⇧←", "End time −5 min"),
    ("⇧→", "End time +5 min"),
    ("a", "Align start to previous entry's end"),
    ("J", "Join with previous entry"),
    ("", ""),
    (None, "Search  (/)"),
    ("/", "Open fuzzy search"),
    ("↓", "Next result"),
    ("↑", "Previous result"),
    ("", "Then: e  c  C  d  o  act on entry"),
    ("", "[dim]Prefixes: (none) desc  $ customer[/dim]"),
    ("", "[dim]@ project  # tag  & ticket[/dim]"),
    ("", "[dim]Space-separated: AND across, OR within[/dim]"),
    ("", ""),
    (None, "View"),
    ("t", "Toggle timeline mode"),
    ("f", "Filter entries"),
    ("g", "Grouping settings"),
    ("T", "Toggle dark / light theme"),
    ("", "[dim]Set dark_theme / light_theme in config[/dim]"),
    ("", ""),
    (None, "Miscellaneous"),
    ("X", "Copy entries to another backend"),
    ("?", "This help"),
    ("A", "About (backend / accounts)"),
    ("q", "Quit"),
]


class HelpModal(ModalScreen[None]):
    """Displays keybinding help for the current screen."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=False),
        Binding("?", "dismiss_modal", "Close", show=False),
        Binding("q", "dismiss_modal", "Close", show=False),
    ]

    def __init__(
        self,
        content: list[HelpEntry],
        content2: list[HelpEntry] | None = None,
    ) -> None:
        self._help_content = content
        self._help_content2 = content2
        super().__init__()

    def compose(self) -> ComposeResult:
        classes = "help-wide" if self._help_content2 else ""
        with Vertical(id="help-dialog", classes=classes):
            yield Label("Keybindings", id="help-title")
            if self._help_content2:
                with Horizontal(id="help-columns"):
                    yield Static(_render_help(self._help_content), classes="help-col")
                    yield Static(_render_help(self._help_content2), classes="help-col")
            else:
                yield Static(_render_help(self._help_content), id="help-content")
            yield Button("Close  [dim]esc / ? / q[/dim]", variant="primary", id="help-close")

    @on(Button.Pressed, "#help-close")
    def _close(self) -> None:
        self.dismiss()

    def action_dismiss_modal(self) -> None:
        self.dismiss()
