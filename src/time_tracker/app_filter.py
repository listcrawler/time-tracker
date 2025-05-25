from __future__ import annotations

from datetime import UTC, datetime

from .modals import FilterModal
from .models import Entry as DBEntry
from .widgets.common import rank_options

# Maps filter field keys to the column name used in entry_list hidden_cols sets.
_FILTER_TO_COL: dict[str, str] = {
    "customer": "customer",
    "project": "project",
    "description": "description",
    "ticket": "ticket",
    "tag": "tags",
}


def _parse_filter_values(raw: str) -> list[str]:
    """Split a filter string on commas, respecting double-quoted literals.

    ``foo, bar``        → [``foo``, ``bar``]   (OR match)
    ``"foo, bar"``      → [``foo, bar``]        (literal comma)
    ``"foo, bar", baz`` → [``foo, bar``, ``baz``]
    """
    tokens: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in raw:
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "," and not in_quotes:
            token = "".join(current).strip()
            if token:
                tokens.append(token)
            current = []
        else:
            current.append(ch)
    token = "".join(current).strip()
    if token:
        tokens.append(token)
    return tokens


class FilterMixin:
    """Mixin providing filter logic for TimeTrackerApp."""

    _filter: dict[str, str]
    _timeline_mode: bool

    def _apply_filter(self, entries: list[DBEntry]) -> list[DBEntry]:
        f = self._filter
        if not f or not any(f.values()):
            return entries
        from_dt = to_dt = None
        if f.get("from"):
            try:
                from_dt = datetime.strptime(f["from"], "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                pass
        if f.get("to"):
            try:
                to_dt = datetime.strptime(f["to"], "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, tzinfo=UTC
                )
            except ValueError:
                pass
        cust_terms = [v.lower() for v in _parse_filter_values(f.get("customer") or "")]
        proj_terms = [v.lower() for v in _parse_filter_values(f.get("project") or "")]
        desc_terms = [v.lower() for v in _parse_filter_values(f.get("description") or "")]
        tag_terms = [v.lower() for v in _parse_filter_values(f.get("tag") or "")]
        tick_terms = [v.lower() for v in _parse_filter_values(f.get("ticket") or "")]
        out: list[DBEntry] = []
        for e in entries:
            if from_dt and (not e.start or e.start < from_dt):
                continue
            if to_dt and (not e.start or e.start > to_dt):
                continue
            if cust_terms and not any(t in (e.customer or "").lower() for t in cust_terms):
                continue
            if proj_terms and not any(t in (e.project or "").lower() for t in proj_terms):
                continue
            if desc_terms and not any(t in (e.description or "").lower() for t in desc_terms):
                continue
            if tag_terms:
                tag_names = {t.name.lower() for t in e.tags} if e.tags else set()
                if not any(any(term in tn for tn in tag_names) for term in tag_terms):
                    continue
            if tick_terms and not any(t in (e.ticket or "").lower() for t in tick_terms):
                continue
            out.append(e)
        return out

    def _hidden_cols(self) -> frozenset[str]:
        """Column names to hide because their filter has exactly one value."""
        if self._timeline_mode:
            return frozenset()
        hidden: set[str] = set()
        for fkey, col in _FILTER_TO_COL.items():
            raw = self._filter.get(fkey, "")
            if raw and len(_parse_filter_values(raw)) == 1:
                hidden.add(col)
        return frozenset(hidden)

    def action_filter_entries(self) -> None:
        entries = self._changes.apply(list(self.db.dump_entries()))  # type: ignore[attr-defined]
        suggestions = {
            "customer": rank_options(entries, lambda e: e.customer),
            "project": rank_options(entries, lambda e: e.project),
            "description": rank_options(entries, lambda e: e.description),
            "tag": rank_options(entries, lambda e: [t.name for t in e.tags] if e.tags else []),
            "ticket": rank_options(entries, lambda e: e.ticket),
        }

        def handle(result: dict[str, str] | None) -> None:
            if result is None:
                return
            self._filter = result
            self._refresh_list()  # type: ignore[attr-defined]

        self.push_screen(FilterModal(self._filter, suggestions), handle)  # type: ignore[attr-defined]
