from __future__ import annotations

from typing import Any

try:
    from github import Auth, Github, GithubException
except ImportError as exc:
    raise ImportError(
        "PyGithub is required for GitHub integration.\nInstall it with:  uv sync --extra github"
    ) from exc

from .base import TicketProvider, TicketRef

_DEFAULT_HOST = "github.com"


class GitHubProvider(TicketProvider):
    """Ticket provider backed by the GitHub Issues API."""

    def __init__(
        self,
        token: str,
        host: str = _DEFAULT_HOST,
        default_owner: str | None = None,
        default_repo: str | None = None,
    ) -> None:
        super().__init__()
        self._host = host
        self._default_owner = default_owner or ""
        self._default_repo = default_repo or ""
        base_url = f"https://{host}/api/v3" if host != _DEFAULT_HOST else "https://api.github.com"
        self._gh = Github(auth=Auth.Token(token), base_url=base_url, timeout=15)

    # ── TicketProvider properties ────────────────────────────────────────────

    @property
    def default_host(self) -> str:
        return self._host

    @property
    def default_owner(self) -> str:
        return self._default_owner

    @property
    def default_repo(self) -> str:
        return self._default_repo

    # ── TicketProvider interface ─────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        owner: str = "",
        repo: str = "",
        include_closed: bool = False,
    ) -> list[TicketRef]:
        effective_owner = owner or self._default_owner
        effective_repo = repo or self._default_repo

        parts = ["is:issue"]
        if effective_owner and effective_repo:
            parts.append(f"repo:{effective_owner}/{effective_repo}")
        elif effective_owner:
            parts.append(f"user:{effective_owner}")
        if not include_closed:
            parts.append("state:open")
        if query:
            parts.append(query)

        try:
            results: Any = self._gh.search_issues(" ".join(parts))
            return [
                TicketRef(
                    id=f"{issue.repository.full_name}#{issue.number}",
                    title=issue.title,
                    state=issue.state,
                    url=issue.html_url,
                )
                for issue in results[:20]
            ]
        except GithubException:
            return []

    def get(self, ref: str) -> TicketRef | None:
        try:
            if "#" not in ref:
                return None
            repo_part, num_str = ref.rsplit("#", 1)
            repo = self._gh.get_repo(repo_part)
            issue = repo.get_issue(int(num_str))
            return TicketRef(
                id=ref,
                title=issue.title,
                state=issue.state,
                url=issue.html_url,
            )
        except Exception:
            return None
