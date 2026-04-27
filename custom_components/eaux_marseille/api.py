"""High-level client for the Eaux de Marseille / SEMM customer portals.

This module exposes :class:`EauxDeMarseilleClient`, an asynchronous
client that authenticates against the chosen provider's portal and
fetches the per-contract consumption data.

The auth flow lives in :mod:`._auth`, the HTTP transport in
:mod:`._http`, the data shapes in :mod:`.models`, the constants in
:mod:`.const`. This file is the orchestrator that ties them together.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

import aiohttp

from ._auth import PortalAuth
from .const import (
    DEFAULT_PROVIDER,
    PROVIDERS,
    REQUEST_TIMEOUT_S,
    Provider,
)
from .exceptions import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
    EauxDeMarseilleError,
    EauxDeMarseilleSessionExpiredError,
)
from .models import ConsumptionData

__all__ = [
    "ConsumptionData",
    "EauxDeMarseilleApiError",
    "EauxDeMarseilleAuthError",
    "EauxDeMarseilleClient",
    "EauxDeMarseilleError",
    "EauxDeMarseilleSessionExpiredError",
]

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


class EauxDeMarseilleClient:
    """Async client for the Eaux de Marseille / SEMM customer portal API.

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
        provider: Provider = DEFAULT_PROVIDER,
    ) -> None:
        self._contract_id = contract_id
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._owns_session = session is None
        # We deliberately use ThreadedResolver (socket.getaddrinfo via NSS)
        # rather than aiohttp's default AsyncResolver (aiodns/c-ares). aiodns
        # talks UDP/53 directly to the resolvers in /etc/resolv.conf, which
        # fails on some HAOS/Docker setups where the OS resolves names fine
        # via systemd-resolved, NSS or /etc/hosts but UDP to the upstream
        # DNS is blocked or misconfigured. ThreadedResolver goes through the
        # same path the rest of the OS uses and works in all of these cases.
        # Default ``CookieJar`` (i.e. ``unsafe=False``) refuses cookies set
        # for IP literals; we only ever talk to the portal hostnames so the
        # stricter mode adds defence-in-depth at zero cost.
        self._session = session or aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(),
            timeout=self._timeout,
            connector=aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver()),
        )
        # Credentials and provider live on PortalAuth, the only component
        # that uses them. The client just orchestrates fetches.
        self._auth = PortalAuth(
            self._session,
            self._timeout,
            provider,
            login=login,
            password=password,
        )

    async def close(self) -> None:
        """Close the underlying session if we own it."""
        if self._owns_session and not self._session.closed:
            await self._session.close()

    async def authenticate(self) -> None:
        """Ensure a portal session is established.

        Idempotent: returns immediately if a long-lived AEL token is
        already cached. Callers that want to force a fresh handshake
        (e.g. on a config-flow validate) should call
        ``self._auth.invalidate()`` first or just let
        :meth:`fetch` recover from the inevitable 401.
        """
        if self._auth.is_authenticated:
            return
        await self._auth.authenticate()

    async def fetch(self) -> ConsumptionData:
        """Fetch the three consumption endpoints and aggregate the result.

        Reuses the cached AEL session token across calls and
        transparently re-authenticates once if the portal returns 401/403
        (token expired). Net effect: 3 requests/poll in the steady state
        instead of 5 auth + 3 fetch.
        """
        return await self._with_session_recovery(self._fetch_inner)

    async def fetch_monthly_range(self, year: int) -> list[dict[str, Any]]:
        """Return the raw monthly consumption entries for ``year``."""
        return await self._with_session_recovery(lambda: self._fetch_monthly_inner(year))

    async def _fetch_inner(self) -> ConsumptionData:
        last = await self._auth.get(
            f"/TableauDeBord/derniereConsommationFacturee/{self._contract_id}"
        )
        monthly = await self._auth.get(self._monthly_path(datetime.now(UTC).year))
        history = await self._auth.get(
            f"/Facturation/listeConsommationsFacturees/{self._contract_id}"
        )
        return ConsumptionData.from_api_responses(last, monthly, history)

    async def _fetch_monthly_inner(self, year: int) -> list[dict[str, Any]]:
        data = await self._auth.get(self._monthly_path(year))
        entries: list[dict[str, Any]] = data.get("consommations", [])
        return entries

    async def _with_session_recovery(
        self,
        action: Callable[[], Awaitable[_T]],
    ) -> _T:
        """Run ``action`` under a guaranteed portal session.

        * Authenticates first if no AEL token is cached.
        * On :class:`EauxDeMarseilleSessionExpiredError`, invalidates the
          cache, re-authenticates once, and retries the action.
        * Any other error (auth failure, transport, 5xx after retries)
          propagates unchanged.
        """
        if not self._auth.is_authenticated:
            await self._auth.authenticate()
        try:
            return await action()
        except EauxDeMarseilleSessionExpiredError as err:
            _LOGGER.info("Portal session expired (%s); re-authenticating", err)
            self._auth.invalidate()
            await self._auth.authenticate()
            return await action()

    def _monthly_path(self, year: int) -> str:
        """Build the monthly consumption endpoint path for ``year``."""
        start = int(datetime(year, 1, 1, tzinfo=UTC).timestamp())
        end = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp())
        return (
            f"/Consommation/listeConsommationsInstanceAlerteChart/"
            f"{self._contract_id}/{start}/{end}/MOIS/true"
        )


# Public re-exports for convenience.
__all__ += ["PROVIDERS", "Provider"]
