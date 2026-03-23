"""
Eaux de Marseille API client.

Handles authentication and data retrieval from the
espaceclients.eauxdemarseille.fr customer portal.
"""

import logging
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any

import requests

from .config import Config

logger = logging.getLogger(__name__)

_PORTAL_URL = "https://espaceclients.eauxdemarseille.fr"
_API_BASE = f"{_PORTAL_URL}/webapi"
_DOMAIN = "espaceclients.eauxdemarseille.fr"

# Static application credentials embedded in the portal JavaScript bundle.
# These identify the web client to the API and are not user credentials.
_CLIENT_ID = "SOMEI-GSEM-PRD"
_ACCESS_KEY = "XX_ma2DD-2017-GSEM-PRD!"


def _conversation_id() -> str:
    """Return a conversation ID matching the format expected by the API."""
    return f"JS-WEB-Netscape-{uuid.uuid4()}"


class AuthenticationError(Exception):
    """Raised when authentication against the portal fails."""


class ApiError(Exception):
    """Raised when an API call returns an unexpected response."""


class EauxDeMarseilleClient:
    """
    Client for the Eaux de Marseille customer portal API.

    Usage::

        config = Config.from_env()
        with EauxDeMarseilleClient(config) as client:
            client.authenticate()
            data = client.fetch_consumption_summary()
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._session = self._build_session()

    def __enter__(self) -> "EauxDeMarseilleClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def authenticate(self) -> None:
        """
        Perform the full authentication flow:

        1. Fetch the portal homepage to acquire the load-balancer session cookie.
        2. Exchange the static application access key for a short-lived token.
        3. Authenticate with user credentials to obtain a session token.
        4. Fetch the default contract and inject the full session context cookie.
        """
        self._acquire_session_cookie()
        temp_token = self._generate_token()
        ael_token, user_info = self._login(temp_token)
        contract = self._get_default_contract()
        self._set_context_cookie(contract, user_info, ael_token)
        logger.debug("Authentication successful for %s", self._config.login)

    def fetch_consumption_summary(self) -> dict[str, Any]:
        """
        Return a flat dictionary containing all relevant consumption metrics.

        Keys
        ----
        index_m3               : Current meter index in cubic metres.
        last_reading_m3        : Last billed consumption in m³.
        last_reading_litres    : Last billed consumption in litres.
        last_reading_date      : ISO date of the last billed reading.
        last_reading_days      : Number of days covered by the last reading.
        daily_average_m3       : Average daily consumption in m³.
        previous_reading_m3    : Previous billed consumption in m³.
        previous_reading_date  : ISO date of the previous reading.
        current_month_m3       : Consumption for the current month in m³.
        current_month_litres   : Consumption for the current month in litres.
        current_year_m3        : Year-to-date consumption in m³.
        total_readings         : Total number of available historical readings.
        """
        last = self._fetch_last_billed()
        monthly = self._fetch_monthly()
        history = self._fetch_history()

        readings = history.get("resultats", [])
        previous = readings[1] if len(readings) > 1 else {}

        monthly_entries = monthly.get("consommations", [])
        current_month = monthly_entries[-1] if monthly_entries else {}
        year_total = round(sum(e["volumeConsoEnM3"] for e in monthly_entries), 3)

        return {
            "index_m3": last.get("valeurIndex"),
            "last_reading_m3": last.get("volumeConsoEnM3"),
            "last_reading_litres": last.get("volumeConsoEnLitres"),
            "last_reading_date": (last.get("dateReleve") or "")[:10] or None,
            "last_reading_days": last.get("nbJours"),
            "daily_average_m3": round(last.get("moyenne", 0.0), 4),
            "previous_reading_m3": previous.get("volumeConsoEnM3"),
            "previous_reading_date": (previous.get("dateReleve") or "")[:10] or None,
            "current_month_m3": current_month.get("volumeConsoEnM3"),
            "current_month_litres": current_month.get("volumeConsoEnLitres"),
            "current_year_m3": year_total,
            "total_readings": history.get("nbTotalResultats", 0),
        }

    # ------------------------------------------------------------------
    # Authentication helpers
    # ------------------------------------------------------------------

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
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
        )
        return session

    def _acquire_session_cookie(self) -> None:
        """Visit the portal to obtain the F5 load-balancer session cookie."""
        self._session.headers.pop("token", None)
        self._session.get(f"{_PORTAL_URL}/", timeout=self._config.timeout, allow_redirects=True)
        logger.debug("Session cookie acquired: %s", dict(self._session.cookies))

    def _generate_token(self) -> str:
        """
        Exchange the static application access key for a short-lived token.

        The access key is a static credential embedded in the portal JS bundle
        that identifies the web application (not the user).
        """
        self._session.headers["ConversationId"] = _conversation_id()
        self._session.headers["token"] = _ACCESS_KEY

        payload = {
            "ConversationId": self._session.headers["ConversationId"],
            "ClientId": _CLIENT_ID,
            "AccessKey": _ACCESS_KEY,
        }

        response = self._session.post(
            f"{_API_BASE}/Acces/generateToken",
            json=payload,
            timeout=self._config.timeout,
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise AuthenticationError(f"Token generation failed: {exc}") from exc

        return response.json()["token"]

    def _login(self, temp_token: str) -> tuple[str, dict]:
        """Authenticate with user credentials and return (ael_token, user_info)."""
        self._session.headers["ConversationId"] = _conversation_id()
        self._session.headers["token"] = temp_token

        payload = {
            "identifiant": self._config.login,
            "motDePasse": self._config.password,
        }

        response = self._session.post(
            f"{_API_BASE}/Utilisateur/authentification",
            json=payload,
            timeout=self._config.timeout,
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise AuthenticationError(f"Login failed: {exc}") from exc

        data = response.json()
        ael_token: str = data["tokenAuthentique"]
        user_info: dict = data["utilisateurInfo"]

        self._session.headers["token"] = ael_token
        self._session.cookies.set("aelToken", ael_token, domain=_DOMAIN)

        return ael_token, user_info

    def _get_default_contract(self) -> dict:
        """Fetch the default contract object required to build the session context."""
        return self._get("/Abonnement/getContratParDefaut/")

    def _set_context_cookie(
        self, contract: dict, user_info: dict, ael_token: str
    ) -> None:
        """Build and inject the AEL_CONTEXT session cookie."""
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
        self._session.cookies.set(
            "AEL_CONTEXT",
            urllib.parse.quote_plus(str(context).replace("'", '"')),
            domain=_DOMAIN,
        )

    # ------------------------------------------------------------------
    # Data fetching helpers
    # ------------------------------------------------------------------

    def _fetch_last_billed(self) -> dict:
        return self._get(f"/TableauDeBord/derniereConsommationFacturee/{self._config.contract_id}")

    def _fetch_monthly(self) -> dict:
        now = datetime.now(timezone.utc)
        start = int(datetime(now.year, 1, 1, tzinfo=timezone.utc).timestamp())
        end = int(datetime(now.year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        path = (
            f"/Consommation/listeConsommationsInstanceAlerteChart"
            f"/{self._config.contract_id}/{start}/{end}/MOIS/true"
        )
        return self._get(path)

    def _fetch_history(self) -> dict:
        return self._get(f"/Facturation/listeConsommationsFacturees/{self._config.contract_id}")

    def _get(self, path: str) -> dict:
        self._session.headers["ConversationId"] = _conversation_id()
        response = self._session.get(
            f"{_API_BASE}{path}",
            timeout=self._config.timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise ApiError(f"API request failed [{path}]: {exc}") from exc
        return response.json()
