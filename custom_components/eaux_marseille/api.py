"""High-level client for the Eaux de Marseille customer portal.

This module exposes :class:`EauxDeMarseilleClient`, an asynchronous
client that authenticates against ``espaceclients.eauxdemarseille.fr``
and fetches the per-contract consumption data.

The auth flow lives in :mod:`._auth`, the HTTP transport in
:mod:`._http`, the data shapes in :mod:`.models`, the constants in
:mod:`.const`. This file is the orchestrator that ties them together.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import aiohttp

from . import _auth, _http
from .const import API_BASE, DEFAULT_HEADERS, PORTAL_URL, REQUEST_TIMEOUT_S
from .exceptions import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
    EauxDeMarseilleError,
)
from .models import ConsumptionData

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "ConsumptionData",
    "EauxDeMarseilleApiError",
    "EauxDeMarseilleAuthError",
    "EauxDeMarseilleClient",
    "EauxDeMarseilleError",
]

# Aliased so existing test imports keep working.
_PORTAL_URL = PORTAL_URL
_API_BASE = API_BASE


class EauxDeMarseilleClient:
    """Async client for the Eaux de Marseille customer portal API.

    Usage::

        client = EauxDeMarseilleClient(login, password, contract_id)
        try:
            await client.authenticate()
            data = await client.fetch()
        finally:
            await client.close()
    """

    def __init__(
        self,
        login: str,
        password: str,
        contract_id: str,
        session: aiohttp.ClientSession | None = None,
        timeout: int = REQUEST_TIMEOUT_S,
    ) -> None:
        self._login = login
        self._password = password
        self._contract_id = contract_id
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._owns_session = session is None
        self._session = session or aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            timeout=self._timeout,
        )
        self._auth = _auth.AuthState()

    async def close(self) -> None:
        """Close the underlying session if we own it."""
        if self._owns_session and not self._session.closed:
            await self._session.close()

    async def authenticate(self) -> None:
        """Run the full 5-step authentication flow against the portal."""
        s, t, st = self._session, self._timeout, self._auth

        _LOGGER.info("Authentication: step 1/5 (acquiring session cookie)")
        await _auth.acquire_session_cookie(s, t, st)

        _LOGGER.info("Authentication: step 2/5 (generating token)")
        temp = await _auth.generate_token(s, t, st)

        _LOGGER.info("Authentication: step 3/5 (logging in user)")
        ael, info = await _auth.login_user(
            s,
            t,
            st,
            login=self._login,
            password=self._password,
            temp_token=temp,
        )

        _LOGGER.info("Authentication: step 4/5 (fetching default contract)")
        contract = await _auth.get_default_contract(s, t, st)

        _LOGGER.info("Authentication: step 5/5 (setting context cookie)")
        _auth.set_context_cookie(s, contract, info, ael)

        _LOGGER.info("Authentication successful")

    async def fetch(self) -> ConsumptionData:
        """Fetch the three consumption endpoints and aggregate the result."""
        last = await self._get(f"/TableauDeBord/derniereConsommationFacturee/{self._contract_id}")
        monthly = await self._get(self._monthly_path(datetime.now(UTC).year))
        history = await self._get(f"/Facturation/listeConsommationsFacturees/{self._contract_id}")
        return ConsumptionData.from_api_responses(last, monthly, history)

    async def fetch_monthly_range(self, year: int) -> list[dict[str, Any]]:
        """Return the raw monthly consumption entries for ``year``."""
        data = await self._get(self._monthly_path(year))
        entries: list[dict[str, Any]] = data.get("consommations", [])
        return entries

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _monthly_path(self, year: int) -> str:
        """Build the monthly consumption endpoint path for ``year``."""
        start = int(datetime(year, 1, 1, tzinfo=UTC).timestamp())
        end = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp())
        return (
            f"/Consommation/listeConsommationsInstanceAlerteChart/"
            f"{self._contract_id}/{start}/{end}/MOIS/true"
        )

    async def _get(self, path: str) -> dict[str, Any]:
        """Authenticated GET on an :data:`API_BASE`-relative path."""
        headers = {**DEFAULT_HEADERS, "ConversationId": _auth.conversation_id()}
        if self._auth.token:
            headers["token"] = self._auth.token
        return await _http.request_with_retry(
            self._session,
            "GET",
            f"{API_BASE}{path}",
            timeout=self._timeout,
            headers=headers,
        )
