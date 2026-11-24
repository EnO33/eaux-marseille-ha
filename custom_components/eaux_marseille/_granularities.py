"""Which consumption series a contract exposes (#126).

The portal web app learns this from
``/Consommation/getDatesMinMaxConsommations/{contract}``: one entry per
granularity the contract actually has (``MOIS``, ``JOURNEE``...). Knowing it
lets the client skip the ``JOURNEE`` chart on monthly-only contracts, which
answers a 400 on every request.

The ``dateMin`` of each entry is not the start of the contract's data: on
the contracts observed it is "today minus three years", a rolling window,
so it is deliberately not used.

Best-effort throughout: any failure means "unknown", and the client then
asks the daily chart as it did before this endpoint was used.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

# Granularities can change (meter swap, "Suivi Conso+" subscription), but
# not by the hour: one request a day is plenty.
_TTL_S = 24 * 3600

DAILY = "JOURNEE"

Granularities = frozenset[str]


def parse_granularities(payload: dict[str, Any]) -> Granularities | None:
    """Return the granularities listed in ``payload``, or ``None`` if unusable.

    An unexpected shape must not read as "no granularity at all", which
    would silently drop the daily series: it is reported as unknown.
    """
    stats = payload.get("statistiquesConsommation")
    ranges = stats.get("datesParGranularite") if isinstance(stats, dict) else None
    if not isinstance(ranges, list):
        return None
    labels = frozenset(
        label
        for item in ranges
        if isinstance(item, dict) and isinstance(label := item.get("granulariteLibelle"), str)
    )
    return labels or None


class GranularityCache:
    """Hold the contract's granularities for a day, one fetch at a time.

    An unknown result is cached too: the endpoint may not exist on every
    provider, and asking it on every poll would cost more than the probe it
    is meant to save.
    """

    def __init__(self, fetch: Callable[[], Awaitable[Granularities | None]]) -> None:
        self._fetch = fetch
        self._lock = asyncio.Lock()
        self._value: Granularities | None = None
        self._expires = float("-inf")

    async def lacks(self, granularity: str) -> bool:
        """Whether the contract is known not to expose ``granularity``."""
        async with self._lock:
            if time.monotonic() >= self._expires:
                self._value = await self._fetch()
                self._expires = time.monotonic() + _TTL_S
            return self._value is not None and granularity not in self._value
