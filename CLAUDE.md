# CLAUDE.md

Project context for Claude Code. See README.md for full documentation.

## Commands

```bash
# Run the app
uv run time-tracker

# Run with a specific backend
uv run time-tracker --backend sqlite
uv run time-tracker --backend postgres

# Store Postgres password in the system keyring (run once)
uv run time-tracker --set-postgres-password

# With GitHub Issues integration
uv sync --extra github
uv run time-tracker

# Format code
uv run black src/

# Textual dev mode (live reload + inspector)
uv run textual run --dev --command "uv run python -m time_tracker"

# Start PostgreSQL dev container
docker compose up -d
```

Always use `uv run` — never invoke `python` or `pytest` directly.

## Architecture

- **Entry point**: `src/time_tracker/__init__.py` — argument parsing, config loading, backend selection, optional GitHub provider, launches `TimeTrackerApp`
- **Main TUI**: `src/time_tracker/app.py` — `TimeTrackerApp(FilterMixin, TimelineMixin, RowsMixin, App)` — bindings, `__init__`, `compose`, CRUD actions, commit/undo, grouping, theme, help, quit
- **Filter mixin**: `src/time_tracker/app_filter.py` — `FilterMixin`: `_apply_filter`, `_hidden_cols`, `action_filter_entries`; also exports `_parse_filter_values` and `_FILTER_TO_COL`
- **Row/display mixin**: `src/time_tracker/app_rows.py` — `RowsMixin`: `_build_rows`, `_build_timeline_rows`, `_refresh_list`, `_update_totals`, `_update_status`, `_highlighted_row`, `_selected_entry`; also exports the `_RowT` type alias
- **Timeline mixin**: `src/time_tracker/app_timeline.py` — `TimelineMixin`: `_snap`, `_prev/next_entry_in_timeline`, `_move_start`, `_move_end`, `action_align_entry`, `action_join_with_prev`, `action_toggle_timeline`
- **Pending changes**: `src/time_tracker/pending.py` — `PendingChanges` (staged edits/deletes, commit/cancel logic)
- **Modals**: `src/time_tracker/modals.py` — `ConfirmModal` (quit-with-pending-changes dialog), `FilterModal` (filter entries by date range and text fields), `GroupingModal` (field selection), `BulkEditModal` (batch field update), `SearchModal` (full-text entry search with collapsible identical-entry groups), `CopyToBackendModal` (copy entries between backends)
- **Help modal**: `src/time_tracker/help_modal.py` — `HelpModal` (generic keybinding reference modal; `MAIN_HELP_LEFT` / `MAIN_HELP_RIGHT` constants for the main screen's keybinding reference)
- **Widgets**: `src/time_tracker/widgets/`
  - `common.py` — shared helpers: `fmt_duration`, `entry_group_key`, `group_entries`, `entry_label`, `group_label`
  - `entry_list.py` — column constants, row dataclasses (`DayHeader`, `WeekHeader`, `MonthHeader`, `GroupHeader`, `EntryRow`, `GapRow`, `OverlapRow`, `CrossDateEndRow`, `CurrentPlaceholderRow`, `LoadMoreRow`), list items
  - `entry_modal.py` — `EntryModal` (create/edit form, optional ticket browse button)
  - `ticket_picker.py` — `TicketPickerModal` (GitHub issue search with host/owner/repo/query fields)
- **Models**: `src/time_tracker/models.py` — `Entry`, `Tag` (Pydantic v2)
- **Config**: `src/time_tracker/config.py` — `Config` model (loads/saves `~/.config/time-tracker/config.json`), `detect_system_dark()` (OS light/dark preference probe)
- **Backends**: `src/time_tracker/backends/` — pluggable storage; all implement `StorageBackend` from `base.py`
- **Integrations**: `src/time_tracker/integrations/`
  - `base.py` — `TicketProvider` ABC, `TicketRef` dataclass, `resolve_token` (reads from config or `gh auth token`), `ticket_url_from_ref` (derives browser URL from `owner/repo#N`)
  - `github.py` — `GitHubProvider` using PyGithub; supports GitHub Enterprise via `host` parameter

## Adding a storage backend

Implement the abstract `StorageBackend` interface in `src/time_tracker/backends/base.py`, then register it in `__init__.py` where the backend is selected from `--backend`. The interface covers: `load_entries`, `save_entries`, `get_current`, `set_current`, `clear_current`.

## Adding a ticket provider

Implement `TicketProvider` from `src/time_tracker/integrations/base.py` (two methods: `search`, `get`; three properties: `default_host`, `default_owner`, `default_repo`). Wire it up in `__init__.py` alongside the GitHub provider. The provider is passed through `TimeTrackerApp` → `EntryModal` → `TicketPickerModal`.

## Data model

`Entry` fields: `start`, `end`, `customer`, `project`, `description`, `ticket`, `ticket_url`, `tags`.

- **ticket**: free-form string, conventionally `owner/repo#number` for GitHub issues.
- **ticket_url**: optional browser URL. Auto-derived from `ticket` if it matches `owner/repo#N` (using the ticket provider's host, defaulting to `github.com`). Set automatically when picking a ticket via the GitHub browser (which supplies the canonical URL from the API). Users can also fill it in manually for other issue trackers.
- **Tags**: stored as `list[Tag]`. In SQLite serialised to JSON string; in PostgreSQL as `TEXT[]`; in JSON as nested objects.
- **Group key** (for collapsing identical rows): `(customer, project, description, ticket, frozenset[tag_names])` — time fields and `ticket_url` are intentionally excluded.
- **Dynamic grouping**: `_group_fields: frozenset[str]` on `TimeTrackerApp` controls which fields are used when building group keys. `make_group_key(entry, fields)` in `widgets/common.py` produces the key with omitted fields set to `None`/empty. `group_entries(entries, fields)` accepts the same set. `GroupHeader.group_fields` carries the active set so `GroupItem` can render `(~)` in columns that are not part of the grouping. Note: `"date"` is a groupable field but is not part of `GroupKey` — date separation is handled at the `_build_rows` level via `DayHeader` rows.

## Non-obvious details

- **Current entry**: An in-progress tracking session is stored separately from completed entries. It shows in the main table with a green dot and "running..." as end time.
- **Starting a new entry while one is running**: pressing `s` automatically stops the current entry before opening the new-entry form.
- **Group editing**: pressing `e` on a `GroupHeader` row opens `EntryModal(show_times=False)` which edits all entries in the group at once, preserving individual start/end times.
- **Pending / commit / undo system**: Edits (including time adjustments) and deletes are staged in `_pending` / `_pending_deletes` dicts rather than written to the DB immediately. `w` commits the current entry, `W` commits all, `u` undoes the current entry's pending change, `U` undoes all. Pending rows are visually highlighted. The subtitle shows `◆ N uncommitted` when anything is staged.
- **Delete staging**: `d` on an entry with a DB record marks it in `_pending_deletes` and shows it as struck-through until committed; pressing `d` on a pending-new entry (never saved) immediately discards it. Quitting with staged deletes triggers the same `ConfirmModal` as quitting with pending edits.
- **Timeline mode**: `t` toggles a flat chronological view that replaces the grouped view. In timeline mode `GapRow` / `OverlapRow` rows appear between entries to show gaps or overlaps. `h`/`l` in timeline mode move the selected entry's end time earlier/later (same as `H`/`L` shift end time; `h`/`l` collapse/expand groups in normal mode).
- **Filter modal**: `f` opens `FilterModal` — a two-column dialog with date-range and text inputs on the left, and a ranked suggestions panel on the right. Navigation: Ctrl+↑↓/Home/End moves between fields; Alt+↑↓ moves through suggestions; `→` picks the highlighted suggestion (falls through to Input cursor when no suggestion is selected); Enter applies; Escape cancels. Suggestions are ranked by `count × exp(−days_since_last_use / 30)` and filtered as you type. A `Clear` button dismisses with `{}` to reset all filters. The mode line shows `Filter (N active)` when any filters are set.
- **Grouping modal**: `g` opens `GroupingModal` (checkboxes for each of the 6 groupable fields: date, customer, project, description, ticket, tags). Space toggles a checkbox; Enter applies (priority binding so it fires before the Checkbox widget); Escape cancels; Ctrl+↑↓/Home/End navigates between checkboxes. Applying a new selection resets `_collapsed` and `_initialized`, forcing regrouping. Disabled in timeline mode. The subtitle shows the active field list when it differs from the default (all fields). Columns for non-grouped fields show `(~)` in group header rows; the status panel shows `(mixed)` for those fields.
- **Date grouping**: when `"date"` is unchecked in the grouping modal, `DayHeader` separators are removed entirely. All entries are grouped together across all dates; individual rows display the full `YYYY-MM-DD HH:MM` timestamp. When `"date"` is checked (default), entries are separated by `DayHeader` rows and timestamps show time-only (`HH:MM`) within each day section.
- **Continue on group header**: `c` on a `GroupHeader` starts a new tracking session pre-filled with field values that are identical across all entries in the group; fields that differ between entries are left blank.
- **Time editing keys** (timeline mode): `H` = end time −1 min, `L` = end time +1 min, `a` = align entry's start to the previous entry's end.
- **Vim-style navigation**: `j`/`k` move the cursor down/up (same as arrow keys).
- **ListView highlight restore**: `_refresh_list` saves and restores `lv.index` via `call_after_refresh` to avoid highlight disappearing after collapse/expand. Pass `focus_group=gid` to jump to a specific group header after collapsing.
- **Recency ranking**: Suggestions in `FilterModal` are ranked by `count × exp(−days_since_last_use / 30)` via `rank_options()` in `widgets/common.py`. Recent high-frequency values float to the top. Applied to customer, project, description, tag, and ticket fields.
- **PostgreSQL column name**: Uses `end_time` instead of `end` because `end` is a reserved word in SQL.
- **PostgreSQL password / keyring**: `config.postgres_url` stores the connection URL without a password (e.g. `postgresql://user@host:5432/db`). The password is stored separately in the system keyring under service `"time-tracker"`, key `"postgres-password"`, and injected at connect time by `_inject_keyring_password()` in `__init__.py`. `--set-postgres-password` stores the password interactively. `--postgres-url` bypasses both config and keyring. `keyring` is an optional dependency under the `postgres` extra.
- **SQLite migrations**: handled by catching `OperationalError` on each `ALTER TABLE` statement in `_MIGRATIONS`. Add new columns there when extending the schema. `ticket_url` was added this way.
- **PostgreSQL migrations**: use `ADD COLUMN IF NOT EXISTS` in the `_DDL` string.
- **Config persistence**: The chosen backend, path/URL, GitHub settings, and theme preference are saved to config so they don't need to be repeated on each run.
- **Theme**: `Config` has three theme fields: `color_scheme` (`"dark"` | `"light"` | `"auto"`), `dark_theme` (Textual theme name, default `"textual-dark"`), and `light_theme` (default `"textual-light"`). On startup `"auto"` probes the OS via `_detect_system_dark()` (macOS `defaults`, Linux `gsettings`/`kreadconfig5`, Windows registry) and falls back to dark if undetectable. `T` toggles between dark and light and writes the explicit choice back to config — it never cycles back through `"auto"`.
- **GitHub auth**: `resolve_token` in `integrations/base.py` tries `config.github_token` first, then falls back to `gh auth token` (GitHub CLI). If neither is available, the ticket provider is silently disabled.
- **Ticket picker last-used state**: `TicketProvider` stores `last_host`, `last_owner`, `last_repo` as mutable instance attributes, updated on every search. The picker modal reads these on open when no existing ticket is being edited.
- **Ticket picker return value**: `TicketPickerModal` dismisses a `TicketRef` (not just an id string), so `EntryModal` can populate both the ticket id and ticket URL fields in one step.
- **Open ticket**: `o` opens `entry.ticket_url` in a new browser window (`webbrowser.open_new`). If `ticket_url` is blank but `ticket` matches `owner/repo#N`, the URL is derived on the fly. Shows a warning notification if no URL can be determined.
- **Cross-date entries**: entries whose start and end fall on different dates render as two rows — the start line in the start-date section, and a separate `CrossDateEndItem` (disabled, showing only the end time) at the bottom of the end-date section.
- **List view layout**: timestamps and durations omit seconds (`HH:MM`). Entries under a `DayHeader` show time-only (`HH:MM`); the current running entry at the top keeps the full `YYYY-MM-DD HH:MM`. A `DayHeader` is always prepended for the most recent date; if that date is today it reads "Today, Month DD, YYYY". A 5-line status panel at the bottom shows full (untruncated) details for the highlighted entry or group.
- **Daily/weekly stats in list**: `DayHeader` carries an `entries` field; `DayItem` renders the day's total duration in dim text after the date label (includes the running entry). Week and month headers show only the total duration, not entry counts. `_collapsed_weeks` is initialised to collapse all weeks *except* the current Monday, so today's entries are immediately visible.
- **SearchModal collapsible groups**: entries with identical `(customer, project, description, ticket, tags)` are collapsed into one row with a `▶`/`▼` prefix. `_results: list[list[DBEntry]]`, `_display: list[(gi, ei|None)]`, `_expanded: set[int]`. Use **in-place DOM updates** for single expand/collapse (`lv.mount(*items, after=header_item)` / `item.remove()`); never `lv.clear()` + rebuild for these — it resets scroll and loses highlight. `→`/`←` (or `l`/`h`) expand/collapse; `Shift+→`/`Shift+←` (or `L`/`H`) do all groups. `e` on a group header with multiple entries goes directly to bulk edit.
- **Rich markup `escape()` misses uppercase tags**: `rich.markup.escape` only escapes `[` characters matching `[a-z#/@...]`. A string like `[Acme Corp/Backend]` is not escaped and causes `MarkupError` at render time. Use `f"\\[{escape(value)}]"` to get a literal `[` via the `\[` escape sequence instead.
- **BulkEdit sentinel**: `_NO_CHANGE = "[no-change]"` (hyphen required — a space makes it invalid Rich markup). Guard the sentinel-clearing key handler with `event.character.isprintable()`, not just `event.character is not None`, otherwise Tab clears the sentinel.
- **GitHub host normalisation**: `GitHubProvider.__init__` strips any `https://` or `http://` scheme prefix and trailing slash from the host string, so a misconfigured `host = "https://my.ghe.com"` still works instead of producing `https://https://my.ghe.com/api/v3`.

## Conventions

- Python 3.12+, Pydantic v2, Textual 3+
- No automated tests exist yet. Textual's `textual.testing` module is available via `textual-dev` if tests are added.
- Styling lives in `src/time_tracker/time_tracker.tcss` — Textual CSS, not standard CSS.
- Data files default to `~/.local/share/time-tracker/`; config to `~/.config/time-tracker/`.
- Optional dependencies: `postgres` (`psycopg2-binary`), `github` (`PyGithub>=2.0`).
