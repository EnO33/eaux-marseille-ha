"""
Eaux de Marseille API client.

Async client using aiohttp, with retry logic for transient network errors.
Handles authentication and data retrieval from the
espaceclients.eauxdemarseille.fr customer portal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

_PORTAL_URL = "https://espaceclients.eauxdemarseille.fr"
_API_BASE = f"{_PORTAL_URL}/webapi"
_DOMAIN = "espaceclients.eauxdemarseille.fr"

# Static application credentials embedded in the portal JavaScript bundle.
# These identify the web client to the API and are not user credentials.
_CLIENT_ID = "SOMEI-GSEM-PRD"
_ACCESS_KEY = "XX_ma2DD-2017-GSEM-PRD!"

# Retry settings for transient failures (network errors, timeouts, 5xx).
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds, doubled each attempt (1s, 2s, 4s)

# Maximum number of HTTP redirects we follow manually for a single request.
# We follow redirects ourselves (instead of relying on aiohttp's default) so
# that POST bodies are preserved on 307/308 and so we can log the Location
# header — which is critical for diagnosing portal-side routing changes.
_MAX_REDIRECTS = 5

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": _PORTAL_URL,
    "Referer": f"{_PORTAL_URL}/",
}


def _conversation_id() -> str:
    return f"JS-WEB-Netscape-{uuid.uuid4()}"


class EauxDeMarseilleAuthError(Exception):
    """Raised when authentication fails."""


class EauxDeMarseilleApiError(Exception):
    """Raised when an API call returns an unexpected response."""


@dataclass
class ConsumptionData:
    """Aggregated consumption data returned by the API."""

    index_m3: float | None
    last_reading_m3: float | None
    last_reading_litres: int | None
    last_reading_date: str | None
    last_reading_days: int | None
    daily_average_m3: float | None
    previous_reading_m3: float | None
    previous_reading_date: str | None
    current_month_m3: float | None
    current_month_litres: int | None
    current_year_m3: float | None
    total_readings: int


class EauxDeMarseilleClient:
    """Async client for the Eaux de Marseille customer portal API.

    Uses aiohttp with automatic retry on transient network errors.

    Usage::

        async with aiohttp.ClientSession() as session:
            client = EauxDeMarseilleClient(login, password, contract_id, session=session)
            await client.authenticate()
            data = await client.fetch()
    """

    def __init__(
        self,
        login: str,
        password: str,
        contract_id: str,
        session: aiohttp.ClientSession | None = None,
        timeout: int = 15,
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
        self._token: str | None = None

    async def close(self) -> None:
        """Close the underlying session if we own it."""
        if self._owns_session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def authenticate(self) -> None:
        """Perform the full authentication flow."""
        _LOGGER.info("Authentication: step 1/5 (acquiring session cookie)")
        await self._acquire_session_cookie()
        _LOGGER.info("Authentication: step 2/5 (generating token)")
        temp_token = await self._generate_token()
        _LOGGER.info("Authentication: step 3/5 (logging in user)")
        ael_token, user_info = await self._login_user(temp_token)
        _LOGGER.info("Authentication: step 4/5 (fetching default contract)")
        contract = await self._get_default_contract()
        _LOGGER.info("Authentication: step 5/5 (setting context cookie)")
        self._set_context_cookie(contract, user_info, ael_token)
        _LOGGER.info("Authentication successful")

    async def fetch(self) -> ConsumptionData:
        """Fetch and return all consumption data."""
        last = await self._fetch_last_billed()
        monthly = await self._fetch_monthly()
        history = await self._fetch_history()

        readings = history.get("resultats", [])
        previous = readings[1] if len(readings) > 1 else {}

        monthly_entries = monthly.get("consommations", [])
        current_month = monthly_entries[-1] if monthly_entries else {}
        year_total = round(sum(e["volumeConsoEnM3"] for e in monthly_entries), 3)

        return ConsumptionData(
            index_m3=last.get("valeurIndex"),
            last_reading_m3=last.get("volumeConsoEnM3"),
            last_reading_litres=last.get("volumeConsoEnLitres"),
            last_reading_date=(last.get("dateReleve") or "")[:10] or None,
            last_reading_days=last.get("nbJours"),
            daily_average_m3=round(last.get("moyenne", 0.0), 4),
            previous_reading_m3=previous.get("volumeConsoEnM3"),
            previous_reading_date=(previous.get("dateReleve") or "")[:10] or None,
            current_month_m3=current_month.get("volumeConsoEnM3"),
            current_month_litres=current_month.get("volumeConsoEnLitres"),
            current_year_m3=year_total,
            total_readings=history.get("nbTotalResultats", 0),
        )

    async def fetch_monthly_range(self, year: int) -> list[dict[str, Any]]:
        """Fetch monthly consumption entries for a given calendar year."""
        start = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
        end = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        path = (
            f"/Consommation/listeConsommationsInstanceAlerteChart"
            f"/{self._contract_id}/{start}/{end}/MOIS/true"
        )
        data = await self._get(path)
        return data.get("consommations", [])

    # ------------------------------------------------------------------
    # HTTP helpers with retry
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """HTTP request with retry on transient errors.

        Retries on: network errors, timeouts, 5xx responses.
        Does NOT retry on 4xx responses (they're not transient).
        Always parses the response as JSON.
        """
        headers = {**_DEFAULT_HEADERS, "ConversationId": _conversation_id()}
        if self._token:
            headers["token"] = self._token
        if extra_headers:
            headers.update(extra_headers)

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                current_method = method
                current_url = url
                for redirect_hop in range(_MAX_REDIRECTS + 1):
                    request_kwargs = dict(kwargs)
                    # On 303 (See Other) the redirected request must become a
                    # GET without a body; on 301/302 we follow the historical
                    # browser convention of also dropping the body. 307/308
                    # preserve method and body, which is what the portal needs
                    # for its auth POST.
                    if redirect_hop > 0 and current_method == "GET":
                        request_kwargs.pop("json", None)
                        request_kwargs.pop("data", None)

                    async with self._session.request(
                        current_method,
                        current_url,
                        headers=headers,
                        timeout=self._timeout,
                        allow_redirects=False,
                        **request_kwargs,
                    ) as response:
                        content_type = response.headers.get("Content-Type", "<none>")
                        _LOGGER.debug(
                            "%s %s → HTTP %d (content-type=%s)",
                            current_method, current_url, response.status, content_type,
                        )

                        # 3xx: follow redirect manually so we preserve the POST
                        # body on 307/308 and so we can log the Location header.
                        if 300 <= response.status < 400:
                            location = response.headers.get("Location")
                            if not location:
                                body = await response.text()
                                raise EauxDeMarseilleApiError(
                                    f"HTTP {response.status} at {current_url} "
                                    f"with no Location header. "
                                    f"Body starts with: {body[:200]!r}"
                                )
                            next_url_obj = URL(current_url).join(URL(location))
                            next_url = str(next_url_obj)

                            # Refuse off-portal redirects: forwarding the auth
                            # token (or the credential body on 307/308) to a
                            # third-party host would leak credentials.
                            # See CVE-2018-18074 for the canonical form of this bug.
                            if next_url_obj.host != _DOMAIN:
                                raise EauxDeMarseilleApiError(
                                    f"Refusing to follow {response.status} "
                                    f"redirect to off-portal host "
                                    f"{next_url_obj.host!r} (from {current_url})"
                                )
                            # Refuse https→http downgrade on the same host.
                            if next_url_obj.scheme != "https":
                                raise EauxDeMarseilleApiError(
                                    f"Refusing to follow {response.status} "
                                    f"redirect to non-HTTPS scheme "
                                    f"{next_url_obj.scheme!r} (from {current_url})"
                                )

                            _LOGGER.info(
                                "Following HTTP %d redirect: %s → %s",
                                response.status, current_url, next_url,
                            )
                            if redirect_hop >= _MAX_REDIRECTS:
                                raise EauxDeMarseilleApiError(
                                    f"Too many redirects ({_MAX_REDIRECTS}) "
                                    f"starting from {url}; last hop: {next_url}"
                                )
                            if response.status in (301, 302, 303) and current_method == "POST":
                                current_method = "GET"
                            current_url = next_url
                            continue

                        # 4xx: client error, don't retry
                        if 400 <= response.status < 500:
                            text = await response.text()
                            raise EauxDeMarseilleApiError(
                                f"HTTP {response.status} at {current_url}: {text[:200]}"
                            )
                        # 5xx: server error, will retry
                        if response.status >= 500:
                            text = await response.text()
                            raise aiohttp.ClientResponseError(
                                response.request_info,
                                response.history,
                                status=response.status,
                                message=text[:200],
                            )
                        # Parse JSON with clear error if the body isn't JSON.
                        # The portal sometimes returns HTML (login redirect, WAF block,
                        # CDN error page) with a 200 status — detect and report this.
                        try:
                            return await response.json(content_type=None)
                        except (
                            json.JSONDecodeError,
                            aiohttp.ContentTypeError,
                        ) as err:
                            body = await response.text()
                            raise EauxDeMarseilleApiError(
                                f"Expected JSON from {current_url} but got "
                                f"content-type={content_type}, status={response.status}. "
                                f"Body starts with: {body[:200]!r}"
                            ) from err
            except (asyncio.TimeoutError, aiohttp.ClientError) as err:
                last_error = err
                if attempt < _MAX_RETRIES - 1:
                    delay = _BACKOFF_BASE * (2**attempt)
                    _LOGGER.debug(
                        "Request to %s failed (attempt %d/%d), retrying in %.1fs: %s",
                        url, attempt + 1, _MAX_RETRIES, delay, err,
                    )
                    await asyncio.sleep(delay)

        raise EauxDeMarseilleApiError(
            f"Request to {url} failed after {_MAX_RETRIES} attempts: {last_error}"
        )

    # ------------------------------------------------------------------
    # Authentication helpers
    # ------------------------------------------------------------------

    async def _acquire_session_cookie(self) -> None:
        self._token = None
        try:
            async with self._session.get(
                f"{_PORTAL_URL}/", headers=_DEFAULT_HEADERS, timeout=self._timeout,
                allow_redirects=True,
            ) as response:
                await response.read()
                _LOGGER.debug(
                    "Portal landing page: HTTP %d (final URL: %s)",
                    response.status, response.url,
                )
                if response.status >= 400:
                    raise EauxDeMarseilleApiError(
                        f"Portal returned HTTP {response.status} on landing page "
                        f"(final URL: {response.url})"
                    )
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            raise EauxDeMarseilleApiError(f"Failed to reach portal: {err}") from err

    async def _generate_token(self) -> str:
        cid = _conversation_id()
        payload = {"ConversationId": cid, "ClientId": _CLIENT_ID, "AccessKey": _ACCESS_KEY}
        headers = {"ConversationId": cid, "token": _ACCESS_KEY}
        try:
            data = await self._request(
                "POST",
                f"{_API_BASE}/Acces/generateToken",
                extra_headers=headers,
                json=payload,
            )
        except EauxDeMarseilleApiError as err:
            raise EauxDeMarseilleAuthError(f"Token generation failed: {err}") from err
        if not data or "token" not in data:
            raise EauxDeMarseilleAuthError("Token generation returned unexpected response")
        return data["token"]

    async def _login_user(self, temp_token: str) -> tuple[str, dict]:
        self._token = temp_token
        payload = {"identifiant": self._login, "motDePasse": self._password}
        try:
            data = await self._request(
                "POST",
                f"{_API_BASE}/Utilisateur/authentification",
                json=payload,
            )
        except EauxDeMarseilleApiError as err:
            raise EauxDeMarseilleAuthError(f"Login failed: {err}") from err
        if not data or "tokenAuthentique" not in data:
            keys = sorted(data.keys()) if isinstance(data, dict) else type(data).__name__
            raise EauxDeMarseilleAuthError(
                f"Login returned unexpected response (missing 'tokenAuthentique'); "
                f"got fields: {keys}"
            )
        ael_token: str = data["tokenAuthentique"]
        user_info: dict = data["utilisateurInfo"]
        self._token = ael_token
        self._session.cookie_jar.update_cookies(
            {"aelToken": ael_token}, response_url=URL(_PORTAL_URL)
        )
        return ael_token, user_info

    async def _get_default_contract(self) -> dict:
        return await self._get("/Abonnement/getContratParDefaut/")

    def _set_context_cookie(self, contract: dict, user_info: dict, ael_token: str) -> None:
        context = {
            "type": "contrat",
            "object": contract,
            "user": {
                "identifiant": user_info["identifiant"],
                "nomComplet": f"{user_info.get('prenom', '')} {user_info.get('nom', '')}",
                "nom": user_info.get("nom", ""),
                "prenom": user_info.get("prenom", ""),
                "email": user_info.get("email", ""),
                "titre": user_info.get("titre", ""),
                "tokenAuthentique": ael_token,
                "userWebId": user_info.get("userWebId"),
                "meta": user_info.get("meta", {}),
                "profils": user_info.get("profils", []),
            },
        }
        self._session.cookie_jar.update_cookies(
            {"AEL_CONTEXT": urllib.parse.quote_plus(str(context).replace("'", '"'))},
            response_url=URL(_PORTAL_URL),
        )

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    async def _fetch_last_billed(self) -> dict:
        return await self._get(f"/TableauDeBord/derniereConsommationFacturee/{self._contract_id}")

    async def _fetch_monthly(self) -> dict:
        now = datetime.now(timezone.utc)
        start = int(datetime(now.year, 1, 1, tzinfo=timezone.utc).timestamp())
        end = int(datetime(now.year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        return await self._get(
            f"/Consommation/listeConsommationsInstanceAlerteChart"
            f"/{self._contract_id}/{start}/{end}/MOIS/true"
        )

    async def _fetch_history(self) -> dict:
        return await self._get(f"/Facturation/listeConsommationsFacturees/{self._contract_id}")

    async def _get(self, path: str) -> dict:
        data = await self._request("GET", f"{_API_BASE}{path}")
        return data or {}
