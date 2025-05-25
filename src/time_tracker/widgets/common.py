from __future__ import annotations

import math
import os
import time as _time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from ..models import Entry

DATETIME_FORMAT = "%m-%d %H:%M"
DATETIME_FORMAT_TZ = "%Y-%m-%d %H:%M %z"
TIME_ONLY_FORMAT = "%H:%M"
REPORT_DATETIME_FORMAT = "%Y-%m-%d %H:%M"

# 5-tuple that uniquely identifies an entry group (excluding time fields).
type GroupKey = tuple[str | None, str | None, str | None, str | None, frozenset[str]]

# All fields available for grouping, in display order.
GROUPABLE_FIELDS: tuple[str, ...] = ("date", "customer", "project", "description", "ticket", "tags")
DEFAULT_GROUP_FIELDS: frozenset[str] = frozenset(GROUPABLE_FIELDS)


def _local_tz() -> timezone:
    """Return the local timezone, trying multiple sources in order of reliability.

    This is resilient to environments where a single source gives wrong results
    (e.g. TZ=UTC set by Git Bash on Windows, or an unconfigured container).
    """
    # 1. Python's astimezone — uses OS API on Windows, TZ env / localtime on Linux.
    try:
        offset = datetime.now().astimezone().utcoffset()
        if offset is not None:
            return timezone(offset)
    except Exception:
        pass
    # 2. /etc/localtime symlink → named zone (Linux/macOS, survives TZ env issues).
    try:
        from zoneinfo import ZoneInfo

        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            tz_name = link.split("zoneinfo/", 1)[1]
            offset = datetime.now(ZoneInfo(tz_name)).utcoffset()
            if offset is not None:
                return timezone(offset)
    except Exception:
        pass
    # 3. /etc/timezone text file (Debian/Ubuntu).
    try:
        from zoneinfo import ZoneInfo

        with open("/etc/timezone") as f:
            tz_name = f.read().strip()
        offset = datetime.now(ZoneInfo(tz_name)).utcoffset()
        if offset is not None:
            return timezone(offset)
    except Exception:
        pass
    # 4. C runtime timezone values (Windows registry-backed; also on Linux/macOS).
    try:
        is_dst = bool(_time.daylight and _time.localtime().tm_isdst > 0)
        secs = -(int(_time.altzone) if is_dst else int(_time.timezone))
        return timezone(timedelta(seconds=secs))
    except Exception:
        pass
    return UTC


def _format_dt(dt: datetime | None, fmt: str) -> str:
    """Format a timezone-aware datetime in the local timezone."""
    if dt is None:
        return ""
    local = _local_tz()
    local_dt = dt.astimezone(local)
    return local_dt.strftime(fmt)


def fmt_duration(secs: int) -> str:
    h, rem = divmod(secs, 3600)
    m = rem // 60
    return f"{h:02d}:{m:02d}"


def make_group_key(entry: Entry, fields: frozenset[str]) -> GroupKey:
    """Build a GroupKey using only the selected fields; omitted fields become None/empty."""
    customer = entry.customer if "customer" in fields else None
    project = entry.project if "project" in fields else None
    description = entry.description if "description" in fields else None
    ticket = entry.ticket if "ticket" in fields else None
    tags = (
        frozenset(t.name for t in entry.tags) if ("tags" in fields and entry.tags) else frozenset()
    )
    return (customer, project, description, ticket, tags)


def entry_group_key(entry: Entry) -> GroupKey:
    """Non-time key used to group identical entries (all fields)."""
    return make_group_key(entry, DEFAULT_GROUP_FIELDS)


def group_entries(
    entries: list[Entry],
    fields: frozenset[str] | None = None,
) -> list[tuple[GroupKey, list[Entry]]]:
    """Group entries by the selected fields, preserving first-appearance order."""
    if fields is None:
        fields = DEFAULT_GROUP_FIELDS
    groups: dict[GroupKey, list[Entry]] = {}
    key_order: list[GroupKey] = []
    for e in entries:
        k = make_group_key(e, fields)
        if k not in groups:
            groups[k] = []
            key_order.append(k)
        groups[k].append(e)
    return [(k, groups[k]) for k in key_order]


def entry_label(entry: Entry, *, is_pending: bool = False, is_deleted: bool = False) -> str:
    """Single-line summary of an entry for use in the report tree."""
    start = _format_dt(entry.start, REPORT_DATETIME_FORMAT) if entry.start else "?"
    end = _format_dt(entry.end, REPORT_DATETIME_FORMAT) if entry.end else "running"
    parts = [f"{start} → {end}", fmt_duration(_seconds(entry))]
    if entry.customer:
        parts.append(entry.customer)
    if entry.project:
        parts.append(f"/{entry.project}")
    if entry.description:
        parts.append(entry.description)
    if entry.ticket:
        parts.append(entry.ticket)
    if entry.tags:
        parts.append("  ".join(f"#{t.name}" for t in entry.tags))
    label = "  ".join(parts)
    if is_deleted:
        label = f"[strike]{label}[/strike]"
    if is_pending:
        label = f"[bold yellow]◆[/bold yellow] {label}"
    return label


def group_label(entries: list[Entry], key: GroupKey) -> str:
    """Label for a collapsed group node in the report tree."""
    customer, project, description, ticket, tag_names = key
    parts: list[str] = []
    if customer:
        parts.append(customer)
    if project:
        parts.append(f"/{project}")
    if description:
        parts.append(description)
    if ticket:
        parts.append(ticket)
    if tag_names:
        parts.append("  ".join(f"#{t}" for t in sorted(tag_names)))
    total = sum(_seconds(e) for e in entries)
    summary = "  ".join(parts) if parts else "(no metadata)"
    return f"{summary}  {fmt_duration(total)}  ({len(entries)}×)"


def rank_options(
    entries: list[Entry],
    extract: Callable[[Entry], str | None | list[str]],
) -> list[str]:
    """Return unique non-empty values sorted by recency × frequency score.

    Score = count × exp(−days_since_last_use / 30).
    """
    counts: dict[str, int] = defaultdict(int)
    last_seen: dict[str, datetime] = {}

    for e in entries:
        raw = extract(e)
        values: list[Any] = raw if isinstance(raw, list) else [raw]
        for v in values:
            if not v:
                continue
            counts[v] += 1
            if e.start and (v not in last_seen or e.start > last_seen[v]):
                last_seen[v] = e.start

    now = datetime.now(UTC)

    def score(v: str) -> float:
        days = max(0, (now - last_seen.get(v, datetime.min.replace(tzinfo=UTC))).days)
        return counts[v] * math.exp(-days / 30.0)

    return sorted(counts, key=score, reverse=True)


def _seconds(entry: Entry) -> int:
    if entry.start and entry.end:
        return max(0, int((entry.end - entry.start).total_seconds()))
    return 0
