"""Reads on the web portal's API for one contract.

:class:`PortalReader` knows the contract's endpoint paths and how each kind
of data tolerates the portal's errors: required data survives the soft
"no data yet" 400 of a fresh contract, optional data (the daily chart, the
granularities, the meter detail) survives any API error. Session expiries
always propagate, so :class:`.SessionGuard` can re-authenticate.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ._auth import PortalAuth
from ._granularities import Granularities, lacks, parse_granularities
from ._ttl_cache import TtlCache
from .const import GRANULARITIES_TTL_S, PORTAL_TZ
from .exceptions import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleNoDataError,
    EauxDeMarseilleSessionExpiredError,
)

_LOGGER = logging.getLogger(__name__)


class PortalReader:
    """The contract's portal endpoints, read through an authenticated :class:`PortalAuth`."""

    def __init__(
        self,
        auth: PortalAuth,
        contract_id: str,
        redact: Callable[[str], str],
    ) -> None:
        self._auth = auth
        self._contract_id = contract_id
        self._redact = redact
        self._granularities = TtlCache(self._fetch_granularities, GRANULARITIES_TTL_S)

    async def last_billed(self) -> dict[str, Any]:
        """The last billed reading (dashboard endpoint)."""
        return await self._safe_get(
            f"/TableauDeBord/derniereConsommationFacturee/{self._contract_id}"
        )

    async def billed_history(self) -> dict[str, Any]:
        """The billed readings, newest first."""
        return await self._safe_get(f"/Facturation/listeConsommationsFacturees/{self._contract_id}")

    async def meter_detail(self) -> dict[str, Any] | None:
        """The subscription detail (#128), sent once: it only feeds the device info."""
        path = f"/Abonnement/detailAbonnement/{self._contract_id}"
        return await self._optional_get(path, "subscription detail", retry=False)

    async def chart(self, year: int, granularity: str) -> dict[str, Any]:
        """A required consumption chart for ``year``, ``{}`` while the contract has no data."""
        return await self._safe_get(self.chart_path(year, granularity))

    async def chart_entries(
        self, year: int, granularity: str, *, optional: bool = False
    ) -> list[dict[str, Any]]:
        """Return the ``consommations`` list for a chart granularity.

        An ``optional=True`` series (the daily JOURNEE chart, which most
        contracts don't expose) is not even asked when the contract's
        granularities lack it (#126); otherwise an error yields an empty
        list.
        """
        if not optional:
            data = await self.chart(year, granularity)
        elif lacks(await self._granularities.get(), granularity):
            return []
        else:
            path = self.chart_path(year, granularity)
            data = await self._optional_get(path, f"{granularity} chart") or {}
        entries: list[dict[str, Any]] = data.get("consommations", [])
        return entries

    def chart_path(self, year: int, granularity: str) -> str:
        """Build the consumption-chart endpoint path for ``year``.

        ``granularity`` is one of the portal's codes (``MOIS``,
        ``JOURNEE``, ``SEMAINE``, ``TRIMESTRE``); which ones a contract
        has is learnt by :meth:`_fetch_granularities`.
        """
        start = int(datetime(year, 1, 1, tzinfo=PORTAL_TZ).timestamp())
        end = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=PORTAL_TZ).timestamp())
        return (
            f"/Consommation/listeConsommationsInstanceAlerteChart/"
            f"{self._contract_id}/{start}/{end}/{granularity}/true"
        )

    async def _fetch_granularities(self) -> Granularities | None:
        """The series this contract exposes, ``None`` when the portal does not say."""
        path = f"/Consommation/getDatesMinMaxConsommations/{self._contract_id}"
        data = await self._optional_get(path, "granularities")
        return None if data is None else parse_granularities(data)

    async def _safe_get(self, path: str) -> dict[str, Any]:
        """Authenticated GET that tolerates the portal's 'no data yet' soft 400.

        Freshly-activated contracts can lack the data behind certain
        endpoints (typically the monthly consumption chart on a 3-week
        old meter). The portal signals that with ``HTTP 400`` carrying
        ``{"severity": "Information", ...}`` rather than an empty list.
        Substituting an empty dict lets the poll keep going across the
        remaining endpoints; ``ConsumptionData.from_api_responses`` reads
        every key defensively, so sensors show as ``unavailable`` until
        the portal starts serving data.
        """
        try:
            return await self._auth.get(path)
        except EauxDeMarseilleNoDataError as err:
            _LOGGER.info(
                "Portal returned no data (likely a fresh contract): %s", self._redact(str(err))
            )
            return {}

    async def _optional_get(
        self, path: str, label: str, *, retry: bool = True
    ) -> dict[str, Any] | None:
        """Authenticated GET for data the contract may not have; ``None`` if unavailable.

        Any API error other than a session expiry is the normal case for
        such data, so it is logged at DEBUG, status only (the URL carries
        the contract number).
        """
        try:
            return await self._auth.get(path, retry=retry)
        except EauxDeMarseilleSessionExpiredError:
            raise
        except EauxDeMarseilleApiError as err:
            _LOGGER.debug("Optional %s unavailable (HTTP status %s)", label, err.status)
            return None
