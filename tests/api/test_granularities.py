"""Contract granularities: skip the daily chart a contract does not have (#126)."""

from __future__ import annotations

import re
from typing import Any

import pytest
from aioresponses import aioresponses

from custom_components.eaux_marseille._granularities import parse_granularities
from custom_components.eaux_marseille.api import EauxDeMarseilleClient
from custom_components.eaux_marseille.const import portal_now

from .portal import (
    CONTRACT_ID,
    DAILY_RE,
    GRANULARITIES_URL,
    HISTORY_URL,
    LAST_BILLED_URL,
    LOGIN,
    MONTHLY_RE,
    count_requests,
    granularities_payload,
    register_granularities,
)

_GRANULARITIES_RE = re.compile(re.escape(GRANULARITIES_URL))
_MONTHLY_ONLY = pytest.mark.parametrize("granularities", [("MOIS", "TRIMESTRE")])


@pytest.fixture(autouse=True)
def _poll_endpoints(mock_auth: aioresponses) -> None:
    """The poll's endpoints, answering for as many polls as a test runs."""
    mock_auth.get(LAST_BILLED_URL, payload={"valeurIndex": 212.0}, repeat=True)
    mock_auth.get(MONTHLY_RE, payload={"consommations": [{"valeurIndex": 212982}]}, repeat=True)
    mock_auth.get(HISTORY_URL, payload={"nbTotalResultats": 0, "resultats": []}, repeat=True)
    mock_auth.get(DAILY_RE, payload={"consommations": []}, repeat=True)


class TestParse:
    def test_reads_the_listed_granularities(self) -> None:
        payload = granularities_payload("DEFAUT", "MOIS", "TRIMESTRE", "ANNEE")
        assert parse_granularities(payload) == {"DEFAUT", "MOIS", "TRIMESTRE", "ANNEE"}

    def test_skips_malformed_items(self) -> None:
        payload = granularities_payload("MOIS")
        payload["statistiquesConsommation"]["datesParGranularite"] += [None, {"granularite": 1}]
        assert parse_granularities(payload) == {"MOIS"}

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"statistiquesConsommation": None},
            {"statistiquesConsommation": {"datesParGranularite": None}},
            {"statistiquesConsommation": {"datesParGranularite": []}},
            {"statistiquesConsommation": {"datesParGranularite": [None, {"granularite": 3}]}},
        ],
        ids=["empty", "null-stats", "null-list", "empty-list", "no-label"],
    )
    def test_unusable_payload_is_unknown(self, payload: dict[str, Any]) -> None:
        """An odd shape must not read as "no daily series"."""
        assert parse_granularities(payload) is None


@_MONTHLY_ONLY
async def test_monthly_only_contract_never_asks_the_daily_chart(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    for _ in range(2):
        data = await client.fetch()
    assert await client.fetch_daily_range(portal_now().year) == []

    assert data.index_precise_m3 == 212.982
    assert count_requests(mock_auth, DAILY_RE) == 0
    assert count_requests(mock_auth, _GRANULARITIES_RE) == 1


async def test_daily_contract_still_gets_its_series(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    await client.fetch()

    assert count_requests(mock_auth, DAILY_RE) == 1


@pytest.mark.parametrize("granularities", [None])
@pytest.mark.parametrize(
    "answer",
    [{"status": 404}, {"payload": {"unexpected": True}}],
    ids=["missing-endpoint", "odd-payload"],
)
async def test_unknown_granularities_keep_probing_and_are_asked_once(
    client: EauxDeMarseilleClient, mock_auth: aioresponses, answer: dict[str, Any]
) -> None:
    """A provider without the endpoint behaves as before, at no extra cost per poll."""
    mock_auth.get(GRANULARITIES_URL, repeat=True, **answer)

    for _ in range(2):
        await client.fetch()

    assert count_requests(mock_auth, DAILY_RE) == 2
    assert count_requests(mock_auth, _GRANULARITIES_RE) == 1


@pytest.mark.parametrize("granularities", [None])
async def test_granularities_are_refreshed_once_expired(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """A meter swap can add the daily series: the answer does not live forever."""
    mock_auth.get(GRANULARITIES_URL, payload=granularities_payload("MOIS"))
    register_granularities(mock_auth, "MOIS", "JOURNEE")

    await client.fetch()
    client._granularities._expires = float("-inf")
    await client.fetch()

    assert count_requests(mock_auth, _GRANULARITIES_RE) == 2
    assert count_requests(mock_auth, DAILY_RE) == 1


@_MONTHLY_ONLY
async def test_monthly_only_contract_goes_straight_to_mobility(mock_auth: aioresponses) -> None:
    """The mobility fallback still fills the daily series when the web has none."""
    client = EauxDeMarseilleClient(login=LOGIN, password="password", contract_id=CONTRACT_ID)
    assert client._mobile is not None

    async def _recent_daily() -> list[dict[str, Any]]:
        return [{"dateReleve": "2026-11-23T00:00:00", "valeurIndex": 500}]

    client._mobile.recent_daily = _recent_daily
    try:
        data = await client.fetch()
    finally:
        await client.close()

    assert data.index_precise_m3 == 0.5
    assert count_requests(mock_auth, DAILY_RE) == 0
