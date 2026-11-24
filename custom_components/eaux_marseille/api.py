"""High-level client for the Eaux de Marseille / SEMM customer portals.

This module exposes :class:`EauxDeMarseilleClient`, an asynchronous
client that authenticates against the chosen provider's portal and
fetches the per-contract consumption data.

The auth flow lives in :mod:`._auth`, the contract's endpoints in
:mod:`._portal_reads`, the daily series and its mobility fallback in
:mod:`._daily`, the session and landing page in :mod:`._connect`, the HTTP
transport in :mod:`._http`, the data shapes in :mod:`.models`. This file
is the orchestrator that ties them together.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp

from ._auth import PortalAuth
from ._connect import new_session
from ._daily import DailySource, mobile_client
from ._granularities import DAILY, MONTHLY
from ._meter import MeterInfo
from ._portal_reads import PortalReader
from ._session import SessionGuard, all_or_first_error
from ._ttl_cache import TtlCache
from ._year_cache import ClosedYearCache
from .const import (
    BILLED_TTL_S,
    DEFAULT_PROVIDER,
    PROVIDERS,
    REQUEST_TIMEOUT_S,
    Provider,
    portal_now,
)
from .exceptions import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
    EauxDeMarseilleContractError,
    EauxDeMarseilleError,
    EauxDeMarseilleNoDataError,
    EauxDeMarseilleSessionExpiredError,
)
from .models import ConsumptionData

__all__ = [
    "PROVIDERS",
    "ConsumptionData",
    "EauxDeMarseilleApiError",
    "EauxDeMarseilleAuthError",
    "EauxDeMarseilleClient",
    "EauxDeMarseilleContractError",
    "EauxDeMarseilleError",
    "EauxDeMarseilleNoDataError",
    "EauxDeMarseilleSessionExpiredError",
    "MeterInfo",
    "Provider",
]

_LOGGER = logging.getLogger(__name__)

_REDACTED = "**REDACTED**"


class EauxDeMarseilleClient:
    """Async client for the Eaux de Marseille / SEMM customer portal API.

    Usage::

        client = EauxDeMarseilleClient(login, password, contract_id)
        try:
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
        # Kept only to scrub them from messages bound for the HA log.
        self._identifiers = (contract_id, login)
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._owns_session = session is None
        self._session = session or new_session(self._timeout)
        # Credentials and provider live on PortalAuth, the only component
        # that uses them. The client just orchestrates fetches.
        self._auth = PortalAuth(
            self._session,
            self._timeout,
            provider,
            login=login,
            password=password,
        )
        self._guard = SessionGuard(
            "Portal",
            is_authenticated=lambda: self._auth.is_authenticated,
            authenticate=self._auth.authenticate,
            invalidate=self._auth.invalidate,
        )
        self._reader = PortalReader(self._auth, contract_id, self.redact)
        mobile = mobile_client(
            self._session,
            self._timeout,
            provider,
            login=login,
            password=password,
            contract_id=contract_id,
        )
        self._daily = DailySource(self._reader, mobile, self.redact)
        self._closed_years = ClosedYearCache()
        # The billed reading and history only move once a billing cycle: they
        # are refreshed twice a day, and a failed refresh keeps the last ones.
        self._billed = TtlCache(self._fetch_billed, BILLED_TTL_S, stale_on_error=True)

    async def close(self) -> None:
        """Close the underlying session if we own it."""
        if self._owns_session and not self._session.closed:
            await self._session.close()

    def redact(self, text: str) -> str:
        """Return ``text`` with this client's contract number and login hidden.

        Error messages embed request URLs (which carry the contract number)
        and users paste their logs in public issues: every message bound
        for the Home Assistant log or the diagnostics goes through here.
        """
        for identifier in self._identifiers:
            if identifier:
                text = re.sub(re.escape(identifier), _REDACTED, text, flags=re.IGNORECASE)
        return text

    async def authenticate(self) -> None:
        """Ensure a portal session is established.

        Idempotent: returns immediately if a long-lived AEL token is
        already cached. The fetches authenticate on demand: this is for
        callers that only want to check the credentials (config flow).
        """
        await self._guard.ensure()

    async def check_contract(self) -> None:
        """Raise :class:`EauxDeMarseilleContractError` if the contract is not the account's.

        The portal rejects a contract outside the account with a 4xx (404
        observed on SEM). A fresh contract's soft 400 ("no data yet") is
        tolerated and counts as valid; 5xx and transport errors stay
        :class:`EauxDeMarseilleApiError`.
        """
        try:
            await self._guard.run(self._reader.last_billed)
        except EauxDeMarseilleApiError as err:
            if err.status is not None and 400 <= err.status < 500:
                raise EauxDeMarseilleContractError(
                    f"Contract not found on this account (HTTP {err.status})"
                ) from err
            raise

    async def fetch(self) -> ConsumptionData:
        """Fetch the consumption endpoints and aggregate the result.

        Authenticates on demand, reuses the cached AEL session token across
        calls and transparently re-authenticates once if the portal returns
        401/403 (token expired). Net effect in the steady state: the monthly
        and daily charts, concurrently, plus the two billing endpoints twice
        a day (#127).
        """
        return await self._guard.run(self._fetch_inner)

    async def fetch_meter_info(self) -> MeterInfo:
        """The contract's meter, with unknown fields when the portal does not say (#128).

        Fetched once per setup for the device info: best-effort and sent
        once (no retry), so a slow or failing portal never holds up the setup.
        """
        try:
            detail = await self._guard.run(self._reader.meter_detail)
        except EauxDeMarseilleError as err:
            _LOGGER.debug("Subscription detail unavailable: %s", self.redact(str(err)))
            detail = None
        return MeterInfo.from_api_response(detail or {})

    async def fetch_monthly_range(self, year: int) -> list[dict[str, Any]]:
        """Return the raw monthly consumption entries for ``year``."""
        return await self._closed_years.get(
            MONTHLY,
            year,
            lambda: self._guard.run(lambda: self._reader.chart_entries(year, MONTHLY)),
        )

    async def fetch_daily_range(self, year: int) -> list[dict[str, Any]]:
        """Return the raw daily consumption entries for ``year``.

        Contracts whose web portal exposes JOURNEE get it from there.
        Otherwise, for recent years, fall back to the mobility API (which
        exposes daily readings even for monthly-only web contracts). The
        fetch is tolerant throughout, so callers never break.
        """
        return await self._closed_years.get(
            DAILY, year, lambda: self._guard.run(lambda: self._daily.year_range(year))
        )

    async def _fetch_inner(self) -> ConsumptionData:
        year = portal_now().year
        # The billed data comes from the cache except twice a day (#127): a
        # steady-state poll sends the two chart requests only.
        async with all_or_first_error() as group:
            billed = group.create_task(self._billed.get())
            monthly = group.create_task(self._reader.chart(year, MONTHLY))
            daily = group.create_task(self._daily.recent(year))
        last, history = billed.result()
        return ConsumptionData.from_api_responses(
            last, monthly.result(), history, daily_entries=daily.result()
        )

    async def _fetch_billed(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """The billed reading and the billed history, fetched together."""
        async with all_or_first_error() as group:
            last = group.create_task(self._reader.last_billed())
            history = group.create_task(self._reader.billed_history())
        return last.result(), history.result()
