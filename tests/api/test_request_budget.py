"""How many portal requests a poll and a statistics import cost (#98)."""

from __future__ import annotations

import asyncio

from aioresponses import CallbackResult, aioresponses

from custom_components.eaux_marseille.api import EauxDeMarseilleClient
from custom_components.eaux_marseille.const import portal_now

from .portal import (
    CHART_RE,
    DAILY_RE,
    HISTORY_RE,
    HISTORY_URL,
    LAST_BILLED_RE,
    LAST_BILLED_URL,
    MONTHLY_RE,
    count_requests,
    register_poll,
)

_ENTRY = {"dateReleve": "2024-01-31T00:00:00+01:00", "volumeConsoEnM3": 3.0}


async def test_poll_requests_run_concurrently(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """The four poll requests are in flight together, not one after another."""
    in_flight = 0
    all_sent = asyncio.Event()

    async def _held(_url: object, **_kwargs: object) -> CallbackResult:
        nonlocal in_flight
        in_flight += 1
        if in_flight == 4:
            all_sent.set()
        # A sequential poll would never send the fourth: time out instead of hanging.
        await asyncio.wait_for(all_sent.wait(), timeout=2)
        return CallbackResult(payload={"consommations": [], "valeurIndex": 42.0})

    for url in (LAST_BILLED_URL, HISTORY_URL, MONTHLY_RE, DAILY_RE):
        mock_auth.get(url, callback=_held)

    data = await client.fetch()

    assert data.index_m3 == 42.0
    assert in_flight == 4


async def test_closed_year_is_fetched_once(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """A year older than last year is served from memory on the next import."""
    closed = portal_now().year - 2
    mock_auth.get(MONTHLY_RE, payload={"consommations": [_ENTRY]}, repeat=True)
    mock_auth.get(DAILY_RE, payload={"consommations": [_ENTRY]}, repeat=True)

    for _ in range(2):
        assert await client.fetch_monthly_range(closed) == [_ENTRY]
        assert await client.fetch_daily_range(closed) == [_ENTRY]

    assert count_requests(mock_auth, CHART_RE) == 2


async def test_recent_years_are_always_refetched(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """Last year and this year can still be revised, so every import refetches them (#31)."""
    year = portal_now().year
    mock_auth.get(MONTHLY_RE, payload={"consommations": [_ENTRY]}, repeat=True)

    for _ in range(2):
        await client.fetch_monthly_range(year)
        await client.fetch_monthly_range(year - 1)

    assert count_requests(mock_auth, CHART_RE) == 4


async def test_empty_closed_year_is_not_cached(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """An empty answer may be a swallowed transient error: ask again next time."""
    closed = portal_now().year - 2
    mock_auth.get(MONTHLY_RE, payload={"consommations": []})
    mock_auth.get(MONTHLY_RE, payload={"consommations": [_ENTRY]})

    assert await client.fetch_monthly_range(closed) == []
    assert await client.fetch_monthly_range(closed) == [_ENTRY]


async def test_steady_poll_skips_the_billing_endpoints(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """Billed data moves once a billing cycle: polls in between ask the charts only (#127)."""
    register_poll(mock_auth)

    for _ in range(3):
        data = await client.fetch()

    assert data.index_m3 == 212.0
    assert count_requests(mock_auth, LAST_BILLED_RE) == 1
    assert count_requests(mock_auth, HISTORY_RE) == 1
    assert count_requests(mock_auth, MONTHLY_RE) == 3
    assert count_requests(mock_auth, DAILY_RE) == 3


async def test_failed_billing_refresh_keeps_the_last_values(
    expiring_client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """A billing outage does not fail the poll once billed data is known."""
    mock_auth.get(LAST_BILLED_URL, payload={"valeurIndex": 42.0})
    mock_auth.get(LAST_BILLED_URL, status=404)
    register_poll(mock_auth)

    await expiring_client.fetch()
    data = await expiring_client.fetch()

    assert data.index_m3 == 42.0
