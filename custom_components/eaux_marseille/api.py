"""
Eaux de Marseille API client.

Handles authentication and data retrieval from the
espaceclients.eauxdemarseille.fr customer portal.
"""

from __future__ import annotations

import logging
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

_PORTAL_URL = "https://espaceclients.eauxdemarseille.fr"
_API_BASE = f"{_PORTAL_URL}/webapi"
_DOMAIN = "espaceclients.eauxdemarseille.fr"

# Static application credentials embedded in the portal JavaScript bundle.
# These identify the web client to the API and are not user credentials.
_CLIENT_ID = "SOMEI-GSEM-PRD"
_ACCESS_KEY = "XX_ma2DD-2017-GSEM-PRD!"


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
    """
    Client for the Eaux de Marseille customer portal API.

    Usage::

        client = EauxDeMarseilleClient(login, password, contract_id)
        client.authenticate()
        data = client.fetch()
    """

    def __init__(self, login: str, password: str, contract_id: str, timeout: int = 15) -> None:
        self._login = login
        self._password = password
        self._contract_id = contract_id
        self._timeout = timeout
        self._session = self._build_session()

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def authenticate(self) -> None:
        """Perform the full authentication flow."""
        self._acquire_session_cookie()
        temp_token = self._generate_token()
        ael_token, user_info = self._login_user(temp_token)
        contract = self._get_default_contract()
        self._set_context_cookie(contract, user_info, ael_token)
        _LOGGER.debug("Authentication successful")

    def fetch(self) -> ConsumptionData:
        """Fetch and return all consumption data."""
        last = self._fetch_last_billed()
        monthly = self._fetch_monthly()
        history = self._fetch_history()

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

    def fetch_monthly_range(self, year: int) -> list[dict[str, Any]]:
        """Fetch monthly consumption entries for a given calendar year."""
        start = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
        end = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        path = (
            f"/Consommation/listeConsommationsInstanceAlerteChart"
            f"/{self._contract_id}/{start}/{end}/MOIS/true"
        )
        return self._get(path).get("consommations", [])

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
        self._session.headers.pop("token", None)
        self._session.get(f"{_PORTAL_URL}/", timeout=self._timeout, allow_redirects=True)

    def _generate_token(self) -> str:
        cid = _conversation_id()
        self._session.headers["ConversationId"] = cid
        self._session.headers["token"] = _ACCESS_KEY
        payload = {"ConversationId": cid, "ClientId": _CLIENT_ID, "AccessKey": _ACCESS_KEY}
        response = self._session.post(
            f"{_API_BASE}/Acces/generateToken", json=payload, timeout=self._timeout
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise EauxDeMarseilleAuthError(f"Token generation failed: {exc}") from exc
        return response.json()["token"]

    def _login_user(self, temp_token: str) -> tuple[str, dict]:
        self._session.headers["ConversationId"] = _conversation_id()
        self._session.headers["token"] = temp_token
        payload = {"identifiant": self._login, "motDePasse": self._password}
        response = self._session.post(
            f"{_API_BASE}/Utilisateur/authentification", json=payload, timeout=self._timeout
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise EauxDeMarseilleAuthError(f"Login failed: {exc}") from exc
        data = response.json()
        ael_token: str = data["tokenAuthentique"]
        user_info: dict = data["utilisateurInfo"]
        self._session.headers["token"] = ael_token
        self._session.cookies.set("aelToken", ael_token, domain=_DOMAIN)
        return ael_token, user_info

    def _get_default_contract(self) -> dict:
        return self._get("/Abonnement/getContratParDefaut/")

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
        self._session.cookies.set(
            "AEL_CONTEXT",
            urllib.parse.quote_plus(str(context).replace("'", '"')),
            domain=_DOMAIN,
        )

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _fetch_last_billed(self) -> dict:
        return self._get(f"/TableauDeBord/derniereConsommationFacturee/{self._contract_id}")

    def _fetch_monthly(self) -> dict:
        now = datetime.now(timezone.utc)
        start = int(datetime(now.year, 1, 1, tzinfo=timezone.utc).timestamp())
        end = int(datetime(now.year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        return self._get(
            f"/Consommation/listeConsommationsInstanceAlerteChart"
            f"/{self._contract_id}/{start}/{end}/MOIS/true"
        )

    def _fetch_history(self) -> dict:
        return self._get(f"/Facturation/listeConsommationsFacturees/{self._contract_id}")

    def _get(self, path: str) -> dict:
        self._session.headers["ConversationId"] = _conversation_id()
        response = self._session.get(f"{_API_BASE}{path}", timeout=self._timeout)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise EauxDeMarseilleApiError(f"API request failed [{path}]: {exc}") from exc
        return response.json()
