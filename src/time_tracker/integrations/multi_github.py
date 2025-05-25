from __future__ import annotations

from .base import TicketProvider, TicketRef
from .github import GitHubProvider


class MultiGitHubProvider(TicketProvider):
    """Wraps multiple GitHubProvider instances (different hosts / accounts).

    The active provider is selected by matching ``self.last_host`` (updated by
    the ticket picker as the user types) against each sub-provider's host.
    When no host is supplied the first configured provider is used.
    """

    def __init__(self, providers: list[GitHubProvider]) -> None:
        super().__init__()
        self._providers = providers
        if providers:
            self.last_host = providers[0].default_host
            self.last_owner = providers[0].default_owner
            self.last_repo = providers[0].default_repo

    # ── TicketProvider properties ────────────────────────────────────────────

    @property
    def default_host(self) -> str:
        return self._providers[0].default_host if self._providers else ""

    @property
    def default_owner(self) -> str:
        return self._providers[0].default_owner if self._providers else ""

    @property
    def default_repo(self) -> str:
        return self._providers[0].default_repo if self._providers else ""

    # ── Routing helpers ──────────────────────────────────────────────────────

    def _provider_for_host(self, host: str) -> GitHubProvider | None:
        """Return the sub-provider whose host matches *host*, or the first one."""
        if not self._providers:
            return None
        if not host:
            return self._providers[0]
        for p in self._providers:
            if p.default_host == host:
                return p
        # No exact match — fall back to first provider so the picker isn't dead
        return self._providers[0]

    # ── TicketProvider interface ─────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        owner: str = "",
        repo: str = "",
        include_closed: bool = False,
    ) -> list[TicketRef]:
        provider = self._provider_for_host(self.last_host)
        if provider is None:
            return []
        return provider.search(query, owner=owner, repo=repo, include_closed=include_closed)

    def get(self, ref: str) -> TicketRef | None:
        for p in self._providers:
            result = p.get(ref)
            if result:
                return result
        return None
