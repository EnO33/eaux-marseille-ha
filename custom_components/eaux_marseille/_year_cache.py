"""In-memory cache for the per-year history of closed years.

The statistics import re-derives the whole series from the portal on every
run, so revised recent months self-heal (#31). Only the current year and
the previous one can still be revised (a quarterly reading reconciles the
months before it, across New Year): older years are fetched once per
Home Assistant run and served from memory afterwards.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .const import portal_now

Entries = list[dict[str, Any]]


class ClosedYearCache:
    """Keep the entries of years the portal no longer revises."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, int], Entries] = {}

    async def get(self, series: str, year: int, fetch: Callable[[], Awaitable[Entries]]) -> Entries:
        """Return the ``series`` entries of ``year``, fetching them unless cached.

        An empty result is never cached: the fetchers swallow transient
        errors into an empty list, and caching one would drop that year from
        the statistics until the next restart.
        """
        key = (series, year)
        if (cached := self._entries.get(key)) is not None:
            return list(cached)
        entries = await fetch()
        if entries and year < portal_now().year - 1:
            self._entries[key] = list(entries)
        return entries
