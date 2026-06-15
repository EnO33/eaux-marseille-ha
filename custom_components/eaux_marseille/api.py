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
    EauxDeMarseilleNoDataError,
    EauxDeMarseilleSessionExpiredError,
)
from .models import ConsumptionData

__all__ = [
    "ConsumptionData",
    "EauxDeMarseilleApiError",
    "EauxDeMarseilleAuthError",
    "EauxDeMarseilleClient",
    "EauxDeMarseilleError",
    "EauxDeMarseilleNoDataError",
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
        return await self._with_session_recovery(lambda: self._chart_entries(year, "MOIS"))

    async def fetch_daily_range(self, year: int) -> list[dict[str, Any]]:
        """Return the raw daily consumption entries for ``year``.

        Daily data only exists for contracts whose meter exposes it
        (communicating meters with JOURNEE granularity enabled).
        Contracts without it get an empty list — the fetch is tolerant
        of the portal refusing the endpoint (soft 400, hard 4xx), so it
        never breaks callers.
        """
        return await self._with_session_recovery(
            lambda: self._chart_entries(year, "JOURNEE", optional=True)
        )

    async def _fetch_inner(self) -> ConsumptionData:
        last = await self._safe_get(
            f"/TableauDeBord/derniereConsommationFacturee/{self._contract_id}"
        )
        year = datetime.now(UTC).year
        monthly = await self._safe_get(self._chart_path(year, "MOIS"))
        history = await self._safe_get(
            f"/Facturation/listeConsommationsFacturees/{self._contract_id}"
        )
        # Always attempt the daily series: when the contract exposes it
        # (communicating meter) its latest entry carries a fresher index
        # (D-1) than the monthly chart. Tolerant — contracts without
        # daily data just get an empty list. No upfront probe needed: the
        # mere presence of entries tells us whether the meter is daily.
        daily_entries = await self._chart_entries(year, "JOURNEE", optional=True)
        return ConsumptionData.from_api_responses(
            last, monthly, history, daily_entries=daily_entries
        )

    async def _chart_entries(
        self, year: int, granularity: str, *, optional: bool = False
    ) -> list[dict[str, Any]]:
        """Return the ``consommations`` list for a chart granularity.

        For a required series (monthly) this goes through
        :meth:`_safe_get`, which tolerates the portal's soft 400 ("no
        data yet" on a fresh contract) and logs it at INFO.

        For an ``optional=True`` series (the daily JOURNEE chart, which
        most contracts don't expose), any API error other than a session
        expiry is swallowed at DEBUG and an empty list returned — a
        monthly-only contract gets that 400 on *every* poll, so it's the
        normal case, not an anomaly worth an INFO line each time. Session
        expiries are re-raised so :meth:`_with_session_recovery` can
        re-authenticate and retry.
        """
        path = self._chart_path(year, granularity)
        if not optional:
            data = await self._safe_get(path)
            required: list[dict[str, Any]] = data.get("consommations", [])
            return required
        try:
            data = await self._auth.get(path)
        except EauxDeMarseilleSessionExpiredError:
            raise
        except EauxDeMarseilleApiError as err:
            _LOGGER.debug("Optional %s series unavailable: %s", granularity, err)
            return []
        entries: list[dict[str, Any]] = data.get("consommations", [])
        return entries

    async def _safe_get(self, path: str) -> dict[str, Any]:
        """Authenticated GET that tolerates the portal's 'no data yet' soft 400.

        Freshly-activated contracts can lack the data behind certain
        endpoints (typically the monthly consumption chart on a 3-week
        old meter). The portal signals that with ``HTTP 400`` carrying
        ``{"severity": "Information", ...}`` rather than an empty list.
        Substituting an empty dict here lets ``_fetch_inner`` keep going
        across the remaining endpoints; ``ConsumptionData.from_api_responses``
        already tolerates missing keys defensively, so the resulting
        ``ConsumptionData`` has ``None`` for any field whose source
        endpoint was empty — sensors show as ``unavailable`` until the
        portal starts serving data.
        """
        try:
            return await self._auth.get(path)
        except EauxDeMarseilleNoDataError as err:
            _LOGGER.info(
                "Portal returned no data for %s (likely a fresh contract): %s",
                path,
                err,
            )
            return {}

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

    def _chart_path(self, year: int, granularity: str) -> str:
        """Build the consumption-chart endpoint path for ``year``.

        ``granularity`` is one of the portal's codes (``MOIS``,
        ``JOURNEE``, ``SEMAINE``, ``TRIMESTRE``); availability per
        contract is reported by ``/Acces/autorisations``.
        """
        start = int(datetime(year, 1, 1, tzinfo=UTC).timestamp())
        end = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp())
        return (
            f"/Consommation/listeConsommationsInstanceAlerteChart/"
            f"{self._contract_id}/{start}/{end}/{granularity}/true"
        )


# Public re-exports for convenience.
__all__ += ["PROVIDERS", "Provider"]
