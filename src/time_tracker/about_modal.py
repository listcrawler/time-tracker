from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import HorizontalScroll, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from .config import CONFIG_PATH, Config, GitHubAccount


def _redact_token(token: str | None) -> str:
    """Show the first 8 characters of a token followed by '…', or a placeholder."""
    if token is None:
        return "[dim](from gh CLI)[/dim]"
    if len(token) <= 8:
        return "[dim](set)[/dim]"
    return f"{token[:8]}[dim]…[/dim]"


def _redact_postgres_url(url: str) -> str:
    """Replace the password in a postgres URL with '***'."""
    parsed = urlparse(url)
    if not parsed.password:
        return url
    userinfo = parsed.username or ""
    userinfo = f"{userinfo}:***"
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=f"{userinfo}@{host}"))


def _backend_detail(config: Config) -> str:
    backend = config.backend
    if backend == "json":
        return str(config.json_path)
    if backend == "sqlite":
        return str(config.sqlite_path)
    if backend == "postgres":
        return _redact_postgres_url(config.postgres_url)
    return ""


def _github_accounts(config: Config) -> list[GitHubAccount]:
    if config.github_accounts:
        return list(config.github_accounts)
    # Legacy single-account fields
    owner, _, repo = (config.github_repo or "").partition("/")
    return [
        GitHubAccount(
            host=config.github_host,
            token=config.github_token,
            owner=owner,
            repo=repo,
        )
    ]


def _row(label: str, value: str) -> str:
    return f"[dim]{label:<10}[/dim]  {value}"


def _build_content(config: Config) -> str:
    sep = f"[dim]{'─' * 40}[/dim]"
    lines: list[str] = []

    # ── Config file ──────────────────────────────────────────────────────────
    lines.append("[bold]Config[/bold]")
    lines.append(sep)
    lines.append(_row("File", str(CONFIG_PATH)))
    lines.append("")

    # ── Backend ──────────────────────────────────────────────────────────────
    lines.append("[bold]Backend[/bold]")
    lines.append(sep)
    lines.append(_row("Type", f"[bold]{config.backend}[/bold]"))
    lines.append(_row("Location", _backend_detail(config)))
    lines.append("")

    # ── GitHub accounts ───────────────────────────────────────────────────────
    accounts = _github_accounts(config)
    has_any = any(
        acct.token is not None or acct.owner or acct.repo or acct.host != "github.com"
        for acct in accounts
    )
    lines.append("[bold]GitHub[/bold]")
    lines.append(sep)

    if not has_any and not config.github_accounts:
        lines.append("[dim]No accounts configured[/dim]")
    else:
        for acct in accounts:
            lines.append(_row("Host", f"[bold]{acct.host}[/bold]"))
            lines.append(_row("Owner", acct.owner or "[dim](none)[/dim]"))
            lines.append(_row("Repo", acct.repo or "[dim](none)[/dim]"))
            lines.append(_row("Token", _redact_token(acct.token)))
            lines.append("")

    return "\n".join(lines)


class AboutModal(ModalScreen[None]):
    """Displays backend and GitHub account information."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=False),
        Binding("A", "dismiss_modal", "Close", show=False),
        Binding("q", "dismiss_modal", "Close", show=False),
    ]

    def __init__(self, config: Config) -> None:
        self._config = config
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="about-dialog"):
            yield Label("About", id="about-title")
            with HorizontalScroll(id="about-scroll"):
                yield Static(_build_content(self._config), id="about-content")
            yield Button("Close  [dim]esc / q[/dim]", variant="primary", id="about-close")

    @on(Button.Pressed, "#about-close")
    def _close(self) -> None:
        self.dismiss()

    def action_dismiss_modal(self) -> None:
        self.dismiss()
