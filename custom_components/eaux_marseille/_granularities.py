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

from typing import Any

DAILY = "JOURNEE"
MONTHLY = "MOIS"

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


def lacks(known: Granularities | None, granularity: str) -> bool:
    """Whether ``granularity`` is known to be missing; unknown never lacks anything."""
    return known is not None and granularity not in known
