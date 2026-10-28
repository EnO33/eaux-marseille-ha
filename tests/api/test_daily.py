"""Daily telemetry (JOURNEE): fresher index, year bounds, mobility fallback."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aioresponses import aioresponses

from custom_components.eaux_marseille.api import EauxDeMarseilleClient
from custom_components.eaux_marseille.const import MOBILE_ENDPOINTS, Provider, portal_now

from .portal import (
    CONTRACT_ID,
    DAILY_RE,
    HARD_400,
    HISTORY_URL,
    LAST_BILLED_URL,
    LOGIN,
    MONTHLY_RE,
    SOFT_400,
)

# 2027-01-01T00:00 in Europe/Paris (UTC+1 in winter).
_PARIS_2027_START = int(datetime(2026, 12, 31, 23, tzinfo=UTC).timestamp())


def _register_data_endpoints(m: aioresponses) -> None:
    """Register the three standard consumption endpoints."""
    m.get(LAST_BILLED_URL, payload={"valeurIndex": 212.0, "volumeConsoEnM3": 19.0})
    m.get(
        MONTHLY_RE, payload={"consommations": [{"volumeConsoEnM3": 2.337, "valeurIndex": 212982}]}
    )
    m.get(HISTORY_URL, payload={"nbTotalResultats": 1, "resultats": [{"volumeConsoEnM3": 19.0}]})


async def test_daily_index_preferred_when_available(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """When the JOURNEE series carries data, index_precise comes from
    its freshest entry instead of the monthly chart.

    The portal serves the daily series newest-first, so the freshest
    reading (213105 L, dated latest) is the *first* list item — the
    client must pick by date, not by position.
    """
    _register_data_endpoints(mock_auth)
    mock_auth.get(
        DAILY_RE,
        payload={
            "consommations": [
                {"dateReleve": "2026-06-14T00:00:00+02:00", "valeurIndex": 213105},
                {"dateReleve": "2026-06-13T00:00:00+02:00", "valeurIndex": 212900},
            ]
        },
    )

    data = await client.fetch()

    assert data.index_precise_m3 == 213.105


@pytest.mark.parametrize(
    "daily_400",
    [
        # A contract without daily data (soft 400) keeps working.
        SOFT_400,
        # A hard 400 (unauthorised, not the soft "no data" marker) must not
        # break the poll either: the daily series is optional.
        HARD_400,
    ],
    ids=["soft", "hard"],
)
async def test_daily_400_falls_back_to_monthly_index(
    client: EauxDeMarseilleClient, mock_auth: aioresponses, daily_400: dict
) -> None:
    """A 400 on JOURNEE degrades index_precise to the monthly chart value."""
    _register_data_endpoints(mock_auth)
    mock_auth.get(DAILY_RE, **daily_400)
    data = await client.fetch()
    assert data.index_precise_m3 == 212.982


def test_chart_path_bounds_year_in_portal_time(client: EauxDeMarseilleClient) -> None:
    """The chart window runs from 1 January to 31 December in Paris
    time (UTC+1 in winter), not in UTC."""
    end = int(datetime(2027, 12, 31, 22, 59, 59, tzinfo=UTC).timestamp())
    path = client._chart_path(2027, "MOIS")
    assert path.endswith(f"/{_PARIS_2027_START}/{end}/MOIS/true")


@pytest.mark.usefixtures("new_year_in_paris")
async def test_fetch_queries_the_portal_year_at_new_year(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """Just after midnight on 1 January in Paris (still 31 December in
    UTC), fetch() asks for the new year's charts."""
    _register_data_endpoints(mock_auth)
    mock_auth.get(DAILY_RE, payload={"consommations": []})
    await client.fetch()

    charts = [str(url) for _, url in mock_auth.requests if "AlerteChart" in str(url)]
    assert charts
    assert all(f"/{_PARIS_2027_START}/" in url for url in charts)


async def test_fetch_daily_range_returns_entries(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """fetch_daily_range returns the consommations list."""
    mock_auth.get(
        DAILY_RE,
        payload={
            "consommations": [
                {"dateReleve": "2026-06-09T00:00:00+02:00", "volumeConsoEnM3": 0.2},
            ]
        },
    )
    await client.authenticate()
    entries = await client.fetch_daily_range(2026)
    assert len(entries) == 1
    assert entries[0]["volumeConsoEnM3"] == 0.2


async def test_fetch_daily_range_tolerates_unauthorized(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """fetch_daily_range returns [] when the contract has no daily
    telemetry (hard 400), rather than propagating the error."""
    mock_auth.get(DAILY_RE, **HARD_400)
    await client.authenticate()
    entries = await client.fetch_daily_range(2026)
    assert entries == []


async def test_fetch_daily_range_falls_back_to_mobile(mock_auth: aioresponses) -> None:
    """When the web portal has no daily series, fetch_daily_range fills
    it from the mobility API for recent years."""
    # A mobile-enabled client (the shared fixture disables mobility).
    client = EauxDeMarseilleClient(login=LOGIN, password="password", contract_id=CONTRACT_ID)
    year = portal_now().year
    mock_auth.get(DAILY_RE, **SOFT_400)
    base = MOBILE_ENDPOINTS[Provider.SEM].base_url
    mock_auth.post(f"{base}/connect", payload={"Code": 100, "Token": "tok", "Result": {}})
    mock_auth.post(
        f"{base}/getListeReleves/",
        payload={
            "Code": 100,
            "Result": {
                "Releves": [
                    {"DateReleve": f"05/01/{year}", "ValeurIndex": "500", "Consommation": 50.0}
                ]
            },
        },
        repeat=True,
    )
    try:
        await client.authenticate()
        entries = await client.fetch_daily_range(year)
    finally:
        await client.close()

    assert entries
    assert entries[0]["valeurIndex"] == 500
    assert entries[0]["volumeConsoEnM3"] == 0.05
