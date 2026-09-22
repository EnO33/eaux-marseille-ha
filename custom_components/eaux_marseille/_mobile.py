"""Client for the SOMEI *mobility* API used by the provider's phone app.

A separate backend from the web portal (:mod:`._auth`): a dedicated host,
a plain ``login``/``pass`` POST to ``/connect`` that returns a session
token, and an app-level ``AuthKey`` header on every request. Its value for
this integration is the **daily** consumption series, which it exposes even
for contracts the web portal keeps monthly-only.

To keep the blast radius minimal, :meth:`MobileClient.recent_daily`
translates the mobility payload into the exact shape the web ``JOURNEE``
chart already produces (``dateReleve`` / ``volumeConsoEnM3`` /
``valeurIndex`` in litres), so :class:`.models.ConsumptionData` and the
statistics importer consume it unchanged.

Transport (retry/backoff, JSON parsing, HTTP-error mapping) is delegated to
:func:`._http.request_with_retry`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import aiohttp

from ._http import request_with_retry
from .const import MobileEndpoints
from .exceptions import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
    EauxDeMarseilleSessionExpiredError,
)

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")

# The mobility API wraps every response in an envelope; this Code means OK.
_SUCCESS_CODE = 100
_LITRES_PER_M3 = 1000


class MobileClient:
    """Minimal async client for the SOMEI mobility API.

    Auth is cached and transparently refreshed once on a session expiry,
    mirroring :meth:`.api.EauxDeMarseilleClient._with_session_recovery`.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        endpoints: MobileEndpoints,
        timeout: aiohttp.ClientTimeout,
        *,
        login: str,
        password: str,
        contract_id: str,
    ) -> None:
        self._session = session
        self._endpoints = endpoints
        self._timeout = timeout
        self._login = login
        self._password = password
        self._contract_id = contract_id
        self._token: str | None = None

    async def recent_daily(self) -> list[dict[str, Any]]:
        """Return the recent daily readings as web ``JOURNEE`` entries."""
        data = await self._with_session_recovery(
            lambda: self._request("GET", f"/getSyntheseConsoUnite/{self._contract_id}")
        )
        releves = (
            data.get("Result", {}).get("SyntheseConso", {}).get("Releves")
            if isinstance(data.get("Result"), dict)
            else None
        )
        return [entry for r in releves or [] if (entry := _to_web_entry(r)) is not None]

    async def _with_session_recovery(self, action: Callable[[], Awaitable[_T]]) -> _T:
        """Authenticate if needed, retrying once on a session expiry."""
        if self._token is None:
            await self._authenticate()
        try:
            return await action()
        except EauxDeMarseilleSessionExpiredError as err:
            _LOGGER.debug("Mobile session expired (%s); re-authenticating", err)
            self._token = None
            await self._authenticate()
            return await action()

    async def _authenticate(self) -> None:
        body = {
            "login": self._login,
            "pass": self._password,
            "rememberMe": True,
            "refreshToken": "",
        }
        try:
            data = await self._request("POST", "/connect", body=body, authed=False)
        except EauxDeMarseilleApiError as err:
            # A failed /connect is an auth problem from the caller's point of
            # view (bad credentials, or the mobility API rejecting the login).
            raise EauxDeMarseilleAuthError(f"mobile login failed: {err}") from err
        token = data.get("Token")
        if not token:
            raise EauxDeMarseilleAuthError("mobile API returned no session token")
        self._token = token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        authed: bool = True,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "AuthKey": self._endpoints.auth_key,
            "User-Agent": self._endpoints.user_agent,
        }
        if authed:
            if self._token is None:  # pragma: no cover - guarded by _with_session_recovery
                raise EauxDeMarseilleAuthError("not authenticated")
            headers["token"] = self._token
        kwargs: dict[str, Any] = {} if body is None else {"json": body}
        data = await request_with_retry(
            self._session,
            method,
            self._endpoints.base_url + path,
            timeout=self._timeout,
            headers=headers,
            allowed_host=self._endpoints.host,
            **kwargs,
        )
        code = data.get("Code")
        if code != _SUCCESS_CODE:
            raise EauxDeMarseilleApiError(f"mobile {path}: Code={code} ({data.get('Description')})")
        return data


def _to_web_entry(releve: dict[str, Any]) -> dict[str, Any] | None:
    """Translate one mobility reading into a web ``JOURNEE`` entry.

    Skips in-progress readings (``TypeAgregat`` ``"T"``, null index) and any
    malformed row. ``valeurIndex`` is kept in litres and consumption
    converted to m³, matching the web chart the rest of the code expects.
    """
    index_litres = _as_int(releve.get("ValeurIndex"))
    iso_date = _mobile_date_to_iso(releve.get("DateReleve"))
    if index_litres is None or iso_date is None:
        return None
    consumption = releve.get("Consommation")
    volume_m3 = (
        round(consumption / _LITRES_PER_M3, 3) if isinstance(consumption, int | float) else None
    )
    return {
        "dateReleve": iso_date,
        "volumeConsoEnM3": volume_m3,
        "valeurIndex": index_litres,
    }


def _mobile_date_to_iso(value: object) -> str | None:
    """Convert a ``"DD/MM/YYYY"`` mobility date to an ISO ``YYYY-MM-DDT00:00:00``."""
    if not isinstance(value, str):
        return None
    try:
        day, month, year = (int(part) for part in value.split("/"))
    except ValueError:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}T00:00:00"


def _as_int(value: str | int | float | None) -> int | None:
    """Coerce a portal value (litre index is served as a string) to ``int``."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
