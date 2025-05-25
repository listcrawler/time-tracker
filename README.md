# time-tracker

A terminal-based time tracking application built with [Textual](https://textual.textualize.io/). Track time entries, tag them, filter by customer/project, and generate reports — all from the terminal.

## Features

- Full TUI interface with keyboard navigation
- Start/stop live time tracking sessions
- Create and edit entries with customer, project, description, and tags
- Flexible grouping — choose which fields to group entries by (date, customer, project, description, ticket, tags)
- Filter entries by date range and text fields, with recency-ranked suggestions
- Pluggable storage backends: JSON, SQLite, PostgreSQL
- Smart recency-ranked suggestions for fast re-selection
- Configurable dark and light themes with system color-scheme detection

## Installation

Requires Python 3.12+. Uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install with uv
uv sync

# With PostgreSQL support
uv sync --extra postgres

# Run
uv run time-tracker
```

## Usage

```
time-tracker [--backend {json|sqlite|postgres}] [--db PATH] [--postgres-url URL]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--backend` | Storage backend (`json`, `sqlite`, `postgres`) | `json` |
| `--db` | Path to JSON or SQLite database file | `~/.local/share/time-tracker/entries.json` |
| `--postgres-url` | PostgreSQL connection URL (overrides config and keyring) | `postgresql://time-tracker@localhost:5432/time-tracker` |
| `--set-postgres-password` | Store the Postgres password in the system keyring | — |

### Keyboard shortcuts

Press `?` in the app at any time to see the full keybinding reference. The most common ones:

| Key | Action |
|-----|--------|
| `n` | New entry |
| `s` | Start tracking (opens entry form, begins live session) |
| `p` | Stop tracking (ends current session, saves to history) |
| `e` | Edit selected entry or group (group edit preserves individual times) |
| `c` | Continue — start a new session pre-filled from the selected entry or group |
| `d` | Delete selected entry |
| `o` | Open ticket URL in browser |
| `t` | Toggle timeline mode (flat chronological view with gap/overlap markers) |
| `f` | Filter entries (by date range, customer, project, description, tag, ticket) |
| `g` | Open grouping dialog — choose which fields to group entries by |
| `w` / `W` | Write (commit) current entry / all pending changes |
| `u` / `U` | Undo current / all pending changes |
| `T` | Toggle dark/light theme |
| `?` | Show full keybinding help |
| `q` | Quit |

#### Filtering (`f`)

Opens a dialog with inputs for date range (From / To) and text fields (customer, project, description, tag, ticket). A suggestions panel on the right shows ranked completions for text fields — navigate with Alt+↑↓ and pick with `→`. Typing also activates inline completion. Press Enter or **Apply** to apply, **Clear** to reset all filters, Escape to cancel. Active filters are shown in the mode line as `Filter (N active)`.

#### Grouping (`g`)

Entries that share the same values across all selected fields are collapsed into a single group row (shown with `▶`/`▼` indicators and an entry count). Uncheck a field to ignore differences in that field when grouping — the column displays `(~)` in the group row to indicate mixed values. The subtitle bar shows the active grouping when it differs from the default (all fields).

When **Date** is unchecked, the day-separator headers are removed entirely and entries are grouped across all dates (timestamps show the full date+time). When **Date** is checked (default), entries are separated by date headers and show time-only within each day.

`c` on a group header starts a new session pre-filled with whichever fields are identical across all entries in the group; fields that vary between entries are left blank.

## Data model

```python
class Entry(BaseModel):
    start: datetime | None
    end: datetime | None
    customer: str | None
    project: str | None
    description: str | None
    tags: list[Tag] | None

class Tag(BaseModel):
    name: str
```

Entries are the core unit. A "current" entry represents an in-progress session and is displayed with a green dot in the main table. Tags are stored as a list of `Tag` objects.

## Storage backends

All backends implement the abstract `StorageBackend` interface in [src/time_tracker/backends/base.py](src/time_tracker/backends/base.py).

### JSON (default)
Single file at `~/.local/share/time-tracker/entries.json`. Stores a list of completed entries plus a separate `current` entry. No dependencies beyond the standard library.

### SQLite
Local SQLite database. Two tables: `entries` and `current_entry`. Tags stored as a JSON string column. Supports automatic schema migration.

### PostgreSQL
Remote PostgreSQL database. Same logical schema as SQLite but uses `TIMESTAMPTZ` columns and stores tags as `TEXT[]`. Requires `uv sync --extra postgres`.

**Authentication** — the connection URL is stored in config without a password. The password is kept separately in the system keyring (Gnome Keyring, KWallet, macOS Keychain, Windows Credential Locker) and injected at connect time:

```bash
# Store the password once
uv run time-tracker --set-postgres-password

# Run — password is fetched from the keyring automatically
uv run time-tracker --backend postgres --postgres-url "postgresql://user@host:5432/db"
```

The URL is saved to config so `--postgres-url` only needs to be passed once. On subsequent runs just `--backend postgres` (or set `backend` in config) is enough.

If you pass a full URL including the password via `--postgres-url`, that takes precedence over the keyring and the config value.

A `docker-compose.yml` is included for local dev (no password needed):

```bash
docker compose up -d
uv run time-tracker --backend postgres
```

If startup fails with `could not translate host name ... to address`, the PostgreSQL hostname is not resolvable from your current DNS setup. The Postgres client connects directly and does not use `http_proxy` or `https_proxy`, so those variables do not help with this class of failure.

## Configuration

Config is stored at `~/.config/time-tracker/config.json`. The backend, path/URL, and UI preferences are persisted there so you don't need to pass flags every run.

### Theme

| Field | Default | Description |
|-------|---------|-------------|
| `color_scheme` | `"auto"` | Active mode: `"dark"`, `"light"`, or `"auto"` (detect from system) |
| `dark_theme` | `"textual-dark"` | Textual theme name to use in dark mode |
| `light_theme` | `"textual-light"` | Textual theme name to use in light mode |

`T` toggles between dark and light and saves the choice. `"auto"` is only resolved at startup — once you press `T`, the preference becomes explicit.

Built-in Textual theme names include: `textual-dark`, `textual-light`, `nord`, `gruvbox`, `dracula`, `catppuccin-mocha`, `catppuccin-latte`, `tokyo-night`, `monokai`, `solarized-light`.

## Project structure

```
src/time_tracker/
├── __init__.py          # CLI entry point (argument parsing, main())
├── app.py               # TimeTrackerApp: bindings, CRUD actions, init/compose
├── app_filter.py        # FilterMixin: _apply_filter, _hidden_cols, action_filter_entries
├── app_rows.py          # RowsMixin: _build_rows, _refresh_list, status/totals display
├── app_timeline.py      # TimelineMixin: snap, _move_start/end, align, join
├── models.py            # Pydantic models: Entry, Tag
├── pending.py           # PendingChanges: staged edits/deletes, commit/cancel logic
├── config.py            # Config load/save (~/.config/time-tracker/)
├── about_modal.py       # AboutModal
├── help_modal.py        # HelpModal (keybinding reference, shown with ?)
├── modals.py            # ConfirmModal, FilterModal, GroupingModal, BulkEditModal, SearchModal, CopyToBackendModal
├── time_tracker.tcss    # Textual CSS styles
├── backends/
│   ├── base.py          # Abstract StorageBackend
│   ├── json_backend.py  # JSON file backend
│   ├── sqlite_backend.py
│   └── postgres_backend.py
├── integrations/
│   ├── base.py          # TicketProvider ABC, TicketRef, resolve_token
│   └── github.py        # GitHubProvider (PyGithub)
└── widgets/
    ├── common.py        # fmt_duration, group_entries, rank_options, GroupKey
    ├── entry_list.py    # Row/Item dataclasses and list widget types
    ├── entry_modal.py   # EntryModal (create/edit form)
    └── ticket_picker.py # TicketPickerModal (GitHub issue search)
```

## GitHub Issues integration

Install the optional dependency:

```bash
uv sync --extra github
```

**Authentication** — no manual token setup is needed if the [GitHub CLI](https://cli.github.com/) is installed and authenticated (`gh auth login`). The app reads the token automatically via `gh auth token`. Alternatively, set `github_token` in `~/.config/time-tracker/config.json`.

**Default repository** — set `github_repo` (e.g. `"owner/repo"`) in config to scope searches to one repo. Without it, searches are global across GitHub.

Once configured, a **Browse…** button appears next to the Ticket field in the entry form. It opens an issue picker with a live search (open issues by default; toggle **[ ] closed** to include closed ones). Selecting an issue fills the ticket field with `owner/repo#number`.

> **Note:** This integration uses the GitHub CLI (`gh`) to obtain the auth token if `github_token` is not set in config. The `gh` CLI must be installed and authenticated for this to work.

## Development

```bash
# Format
uv run black src/

# Run with Textual dev console (live reload, inspector)
uv run textual run --dev src/time_tracker/__init__.py
```

No automated tests currently exist. Textual's testing framework (`textual.testing`) is available via the `textual-dev` dependency group if tests are added.
