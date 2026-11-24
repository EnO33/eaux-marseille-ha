"""Daily consumption: the web portal's JOURNEE chart, else the mobility API.

The mobility API (phone-app backend) exposes daily readings the web portal
keeps monthly-only, for the providers whose mobility config is known. It is
only a fallback and best-effort throughout: any error yields no entries, so
the web path never breaks.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from ._granularities import DAILY
from ._mobile import MobileClient
from ._portal_reads import PortalReader
from ._year_cache import is_revisable
from .const import MOBILE_ENDPOINTS, Provider
from .exceptions import EauxDeMarseilleError

_LOGGER = logging.getLogger(__name__)


class DailySource:
    """The contract's daily entries, from the web portal or the mobility API.

    Callers hold a portal session (:meth:`.SessionGuard.run`) around each read.
    """

    def __init__(
        self,
        reader: PortalReader,
        mobile: MobileClient | None,
        redact: Callable[[str], str],
    ) -> None:
        self._reader = reader
        self._mobile = mobile
        self._redact = redact

    async def recent(self, year: int) -> list[dict[str, Any]]:
        """Daily entries for the poll.

        When the contract exposes the daily series (communicating meter) its
        latest entry carries a fresher index (D-1) than the monthly chart.
        """
        entries = await self._reader.chart_entries(year, DAILY, optional=True)
        if entries or self._mobile is None:
            return entries
        return await self._best_effort(self._mobile.recent_daily(), "daily fallback")

    async def year_range(self, year: int) -> list[dict[str, Any]]:
        """Daily entries for ``year``, for the statistics import."""
        entries = await self._reader.chart_entries(year, DAILY, optional=True)
        # The mobility API only carries recent history, so older years stay
        # web-only to avoid pointless month-by-month requests.
        if entries or self._mobile is None or not is_revisable(year):
            return entries
        return await self._best_effort(self._mobile.daily_range(year), f"daily range {year}")

    async def _best_effort(
        self, mobile_entries: Awaitable[list[dict[str, Any]]], label: str
    ) -> list[dict[str, Any]]:
        """Entries from the mobility API; ``[]`` on any error."""
        try:
            return await mobile_entries
        except EauxDeMarseilleError as err:
            # Mobility error messages carry the request URL, so the contract.
            _LOGGER.debug("Mobile %s unavailable: %s", label, self._redact(str(err)))
            return []


def mobile_client(
    session: aiohttp.ClientSession,
    timeout: aiohttp.ClientTimeout,
    provider: Provider,
    *,
    login: str,
    password: str,
    contract_id: str,
) -> MobileClient | None:
    """The mobility client for ``provider``, ``None`` when its config is unknown."""
    endpoints = MOBILE_ENDPOINTS.get(provider)
    if endpoints is None:
        return None
    return MobileClient(
        session, endpoints, timeout, login=login, password=password, contract_id=contract_id
    )
