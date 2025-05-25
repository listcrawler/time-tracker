from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

from .app import TimeTrackerApp
from .backends.base import StorageBackend
from .backends.json_backend import JsonBackend
from .backends.sqlite_backend import SqliteBackend
from .config import CONFIG_PATH, Config
from .integrations.base import TicketProvider, resolve_token

_KEYRING_SERVICE = "time-tracker"
_KEYRING_USERNAME = "postgres-password"

_LOG_DIR = Path.home() / ".local" / "share" / "time-tracker"
_LOG_FILE = _LOG_DIR / "crash.log"


def _setup_crash_logger() -> logging.Logger:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("time_tracker.crash")
    logger.setLevel(logging.ERROR)
    handler = logging.FileHandler(_LOG_FILE)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s\n%(message)s\n")
    )
    logger.addHandler(handler)
    return logger


def _inject_keyring_password(url: str) -> str:
    """If the URL has no password, try to fetch one from the keyring."""
    parsed = urlparse(url)
    if parsed.password:
        return url  # already has a password
    try:
        import keyring

        password = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except ImportError:
        return url
    if not password:
        return url
    userinfo = parsed.username or ""
    userinfo = f"{userinfo}:{quote(password, safe='')}"
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    netloc = f"{userinfo}@{host}"
    return urlunparse(parsed._replace(netloc=netloc))


def _set_postgres_password() -> None:
    """Interactively store a Postgres password in the keyring."""
    import getpass

    try:
        import keyring
    except ImportError:
        print("keyring is not installed. Run: uv sync --extra postgres")
        raise SystemExit(1) from None
    password = getpass.getpass("Postgres password: ")
    keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, password)
    print("Password saved to keyring.")
    raise SystemExit(0)


def _print_postgres_connection_error(url: str, exc: Exception) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or "<unknown>"
    message = str(exc).strip()
    print("Failed to connect to PostgreSQL.", file=sys.stderr)
    if "could not translate host name" in message:
        print(
            f"The configured host '{host}' could not be resolved by DNS.",
            file=sys.stderr,
        )
        print(
            "This happens before authentication and is not affected by http_proxy or https_proxy.",
            file=sys.stderr,
        )
        print(
            f"Update 'postgres_url' in {CONFIG_PATH} or override it with --postgres-url.",
            file=sys.stderr,
        )
    print(message, file=sys.stderr)


def main() -> None:
    crash_log = _setup_crash_logger()
    try:
        _main()
    except SystemExit:
        raise
    except Exception:
        crash_log.error(traceback.format_exc())
        raise


def _main() -> None:
    config = Config.load()

    parser = argparse.ArgumentParser(
        prog="time-tracker",
        description="Terminal time tracker",
    )
    parser.add_argument(
        "--backend",
        choices=["json", "sqlite", "postgres"],
        default=None,
        help=f"Storage backend (default from config: {config.backend})",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        type=Path,
        default=None,
        help="Override the storage file path (json/sqlite backends)",
    )
    parser.add_argument(
        "--postgres-url",
        metavar="URL",
        default=None,
        help="Override the PostgreSQL connection URL",
    )
    parser.add_argument(
        "--set-postgres-password",
        action="store_true",
        help="Interactively store the Postgres password in the system keyring",
    )
    args = parser.parse_args()

    if args.set_postgres_password:
        _set_postgres_password()

    backend_name: str = args.backend or config.backend
    config.backend = backend_name

    backend: StorageBackend
    if backend_name == "postgres":
        import psycopg2

        from .backends.postgres_backend import PostgresBackend

        url = args.postgres_url or _inject_keyring_password(config.postgres_url)
        if args.postgres_url:
            config.postgres_url = args.postgres_url
        backend = PostgresBackend(url)
    elif backend_name == "sqlite":
        path = args.db or config.sqlite_path
        if args.db:
            config.sqlite_path = args.db
        backend = SqliteBackend(path)
    else:
        path = args.db or config.json_path
        if args.db:
            config.json_path = args.db
        backend = JsonBackend(path)

    ticket_provider: TicketProvider | None = None
    try:
        from .config import GitHubAccount
        from .integrations.github import GitHubProvider

        # Build the list of accounts to try: prefer github_accounts when set,
        # otherwise fall back to the legacy single-account fields.
        accounts: list[GitHubAccount] = list(config.github_accounts)
        if not accounts:
            leg_owner, _, leg_repo = (config.github_repo or "").partition("/")
            accounts = [
                GitHubAccount(
                    host=config.github_host,
                    token=config.github_token,
                    owner=leg_owner,
                    repo=leg_repo,
                )
            ]

        providers: list[GitHubProvider] = []
        for acct in accounts:
            token = resolve_token(acct.token, host=acct.host)
            if not token:
                continue
            providers.append(
                GitHubProvider(
                    token,
                    host=acct.host,
                    default_owner=acct.owner or None,
                    default_repo=acct.repo or None,
                )
            )

        if len(providers) == 1:
            ticket_provider = providers[0]
        elif len(providers) > 1:
            from .integrations.multi_github import MultiGitHubProvider

            ticket_provider = MultiGitHubProvider(providers)
    except ImportError:
        pass  # PyGithub not installed

    try:
        with backend as db:
            TimeTrackerApp(db, ticket_provider=ticket_provider, config=config).run()
    except psycopg2.OperationalError as exc:
        if backend_name != "postgres":
            raise
        _print_postgres_connection_error(url, exc)
        raise SystemExit(1) from exc
