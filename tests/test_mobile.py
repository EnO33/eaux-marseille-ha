"""Tests for the SOMEI mobility client (:mod:`._mobile`).

Uses ``aioresponses`` to mock the HTTP layer; no Home Assistant needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.eaux_marseille._mobile import MobileClient
from custom_components.eaux_marseille.const import (
    MOBILE_ENDPOINTS,
    REQUEST_TIMEOUT_S,
    Provider,
)
from custom_components.eaux_marseille.exceptions import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
)

_ENDPOINTS = MOBILE_ENDPOINTS[Provider.SEM]
_BASE = _ENDPOINTS.base_url
_CONTRACT = "0000000"
_SYNTHESE_URL = f"{_BASE}/getSyntheseConsoUnite/{_CONTRACT}"
_CONNECT_URL = f"{_BASE}/connect"


def _connect_ok() -> dict:
    return {"Code": 100, "Token": "session-token", "Result": {}}


def _synthese(releves: list[dict]) -> dict:
    return {"Code": 100, "Result": {"SyntheseConso": {"Releves": releves}}}


@pytest.fixture
async def client() -> AsyncIterator[MobileClient]:
    async with aiohttp.ClientSession() as session:
        yield MobileClient(
            session,
            _ENDPOINTS,
            aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S),
            login="user@example.com",
            password="s3cret",
            contract_id=_CONTRACT,
        )


async def test_recent_daily_translates_to_web_shape(client: MobileClient) -> None:
    """Daily readings are translated to the web JOURNEE entry shape, and
    in-progress rows (TypeAgregat "T", null index) are dropped."""
    with aioresponses() as m:
        m.post(_CONNECT_URL, payload=_connect_ok())
        m.get(
            _SYNTHESE_URL,
            payload=_synthese(
                [
                    {
                        "DateReleve": "19/09/2026",
                        "TypeAgregat": "R",
                        "ValeurIndex": "246184",
                        "Consommation": 637.0,
                    },
                    {
                        "DateReleve": "20/09/2026",
                        "TypeAgregat": "R",
                        "ValeurIndex": "246654",
                        "Consommation": 470.0,
                    },
                    {
                        "DateReleve": "21/09/2026",
                        "TypeAgregat": "T",
                        "ValeurIndex": None,
                        "Consommation": 0.0,
                    },
                ]
            ),
        )

        entries = await client.recent_daily()

    # Litres -> m³, "DD/MM/YYYY" -> ISO, index kept in litres (web shape).
    assert entries == [
        {"dateReleve": "2026-09-19T00:00:00", "volumeConsoEnM3": 0.637, "valeurIndex": 246184},
        {"dateReleve": "2026-09-20T00:00:00", "volumeConsoEnM3": 0.47, "valeurIndex": 246654},
    ]


async def test_authenticates_before_fetching(client: MobileClient) -> None:
    """The first fetch performs the /connect login (single-shot mock proves it)."""
    with aioresponses() as m:
        m.post(_CONNECT_URL, payload=_connect_ok())
        m.get(_SYNTHESE_URL, payload=_synthese([]))
        assert await client.recent_daily() == []


async def test_missing_token_raises_auth_error(client: MobileClient) -> None:
    """A /connect response without a token is an authentication failure."""
    with aioresponses() as m:
        m.post(_CONNECT_URL, payload={"Code": 100, "Token": None, "Result": {}})
        with pytest.raises(EauxDeMarseilleAuthError):
            await client.recent_daily()


async def test_connect_error_maps_to_auth_error(client: MobileClient) -> None:
    """Any error from /connect surfaces as an auth error (bad credentials)."""
    with aioresponses() as m:
        m.post(_CONNECT_URL, payload={"Code": 401, "Description": "bad credentials"})
        with pytest.raises(EauxDeMarseilleAuthError):
            await client.recent_daily()


async def test_data_code_error_raises_api_error(client: MobileClient) -> None:
    """A non-100 Code on a data endpoint surfaces as an API error."""
    with aioresponses() as m:
        m.post(_CONNECT_URL, payload=_connect_ok())
        m.get(_SYNTHESE_URL, payload={"Code": 500, "Description": "boom"})
        with pytest.raises(EauxDeMarseilleApiError):
            await client.recent_daily()


async def test_reauthenticates_once_on_session_expiry(client: MobileClient) -> None:
    """A 401 on the data call triggers a transparent re-auth and retry."""
    with aioresponses() as m:
        m.post(_CONNECT_URL, payload=_connect_ok())  # initial login
        m.get(_SYNTHESE_URL, status=401)  # token expired
        m.post(_CONNECT_URL, payload=_connect_ok())  # re-login
        m.get(_SYNTHESE_URL, payload=_synthese([]))  # retry succeeds

        assert await client.recent_daily() == []
