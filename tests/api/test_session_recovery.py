"""The client caches its login and recovers from an expired session."""

from __future__ import annotations

import asyncio
import re

import pytest
from aioresponses import CallbackResult, aioresponses

from custom_components.eaux_marseille.api import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleClient,
    EauxDeMarseilleSessionExpiredError,
)

from .portal import API_BASE, CHART_RE, HISTORY_URL, LAST_BILLED_URL, register_auth_flow


def _register_empty_charts_and_history(m: aioresponses, *, repeat: bool = False) -> None:
    m.get(CHART_RE, payload={"consommations": []}, repeat=True)
    m.get(HISTORY_URL, payload={"nbTotalResultats": 0, "resultats": []}, repeat=repeat)


async def test_authenticate_is_idempotent(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """The second authenticate() call short-circuits when already auth'd."""
    await client.authenticate()
    assert client._auth.is_authenticated

    # mock_auth registers each auth endpoint ONCE. A second full auth
    # would 404 on the unregistered second hit. The fact that this
    # succeeds without aioresponses raising proves we didn't replay.
    await client.authenticate()
    assert client._auth.is_authenticated


async def test_fetch_skips_redundant_auth(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """Two consecutive fetches share one auth (unless 401 happens)."""
    mock_auth.get(
        LAST_BILLED_URL, payload={"valeurIndex": 1.0, "volumeConsoEnM3": 1.0}, repeat=True
    )
    _register_empty_charts_and_history(mock_auth, repeat=True)

    await client.fetch()
    await client.fetch()
    # If a re-auth had been triggered between the two fetches the
    # mock_auth fixture (single-shot endpoints) would have raised.


@pytest.mark.parametrize("status", [401, 403])
async def test_expired_session_recovers(client: EauxDeMarseilleClient, status: int) -> None:
    """A 401 (or a 403, treated alike) on the first fetch triggers a
    transparent re-auth and retry."""
    with aioresponses() as m:
        register_auth_flow(m)
        m.get(LAST_BILLED_URL, status=status, body="session expired")
        register_auth_flow(m)
        m.get(LAST_BILLED_URL, payload={"valeurIndex": 42.0, "volumeConsoEnM3": 1.0})
        _register_empty_charts_and_history(m)

        data = await client.fetch()
        assert data.index_m3 == 42.0


async def test_repeated_session_expiry_propagates(client: EauxDeMarseilleClient) -> None:
    """If the retry also gets 401, surface the failure (no infinite loop)."""
    with aioresponses() as m:
        register_auth_flow(m)
        m.get(LAST_BILLED_URL, status=401)
        # Recovery auth, then the retried fetch gets 401 again: we give up.
        register_auth_flow(m)
        m.get(LAST_BILLED_URL, status=401)

        with pytest.raises(EauxDeMarseilleSessionExpiredError):
            await client.fetch()


async def test_other_4xx_does_not_trigger_reauth(
    client: EauxDeMarseilleClient, mock_auth: aioresponses
) -> None:
    """A 404 must NOT trigger re-auth (would mask real bugs)."""
    mock_auth.get(LAST_BILLED_URL, status=404)
    # mock_auth only registers ONE auth flow. If we ever re-auth on a
    # non-401, the test would fail because the second auth would 404
    # and produce a different (auth) error.
    with pytest.raises(EauxDeMarseilleApiError) as excinfo:
        await client.fetch()
    assert not isinstance(excinfo.value, EauxDeMarseilleSessionExpiredError)


async def test_concurrent_expiry_reauthenticates_once(client: EauxDeMarseilleClient) -> None:
    """The coordinator poll and the statistics import share the client:
    when both hit an expired token, only one of them logs in again (#79)."""
    client._auth._ael_token = "expired-token"
    both_expired = asyncio.Event()
    expired_calls = 0

    async def _by_token(_url: object, **kwargs: object) -> CallbackResult:
        nonlocal expired_calls
        headers = kwargs.get("headers") or {}
        assert isinstance(headers, dict)
        if headers.get("token") == "expired-token":
            # Hold each stale request until both callers have sent one,
            # so they really see the expiry at the same time.
            expired_calls += 1
            if expired_calls == 2:
                both_expired.set()
            await both_expired.wait()
            return CallbackResult(status=401)
        return CallbackResult(payload={"consommations": [], "valeurIndex": 42.0})

    with aioresponses() as m:
        # A single auth flow: a second login would hit an unmocked URL.
        register_auth_flow(m)
        m.get(
            re.compile(rf"{re.escape(API_BASE)}/(?!Abonnement).*"), callback=_by_token, repeat=True
        )

        data, daily = await asyncio.gather(client.fetch(), client.fetch_daily_range(2026))

    assert data.index_m3 == 42.0
    assert daily == []
    logins = [key for key in m.requests if str(key[1]).endswith("/authentification")]
    assert len(m.requests[logins[0]]) == 1
