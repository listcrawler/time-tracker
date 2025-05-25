from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass

_TICKET_REF_RE = re.compile(r"^([^/]+/[^#]+)#(\d+)$")


def ticket_url_from_ref(ticket: str, host: str = "github.com") -> str | None:
    """Derive a browser URL from a ticket ref like 'owner/repo#123'."""
    m = _TICKET_REF_RE.match(ticket.strip())
    if not m:
        return None
    return f"https://{host}/{m.group(1)}/issues/{m.group(2)}"


@dataclass
class TicketRef:
    id: str  # e.g. "owner/repo#123"
    title: str
    state: str  # "open" | "closed"
    url: str


def resolve_token(configured: str | None, host: str = "github.com") -> str | None:
    """Return *configured* if set, otherwise try the GitHub CLI.

    For GitHub Enterprise servers pass the *host* so that
    ``gh auth token --hostname HOST`` is used instead of the plain command.
    """
    if configured:
        return configured
    try:
        cmd = ["gh", "auth", "token"]
        if host and host != "github.com":
            cmd += ["--hostname", host]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


class TicketProvider(ABC):
    def __init__(self) -> None:
        self.last_host: str = ""
        self.last_owner: str = ""
        self.last_repo: str = ""

    @property
    def default_host(self) -> str:
        return ""

    @property
    def default_owner(self) -> str:
        return ""

    @property
    def default_repo(self) -> str:
        return ""

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        owner: str = "",
        repo: str = "",
        include_closed: bool = False,
    ) -> list[TicketRef]: ...

    @abstractmethod
    def get(self, ref: str) -> TicketRef | None: ...
