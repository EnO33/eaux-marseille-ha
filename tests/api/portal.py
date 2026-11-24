"""Portal URLs, payloads and aioresponses helpers shared by the API client tests.

Tests target SEM: it is the default provider of a bare
``EauxDeMarseilleClient(...)``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from aioresponses import aioresponses

from custom_components.eaux_marseille.const import PROVIDERS, Provider

PORTAL_URL = PROVIDERS[Provider.SEM].url
API_BASE = PROVIDERS[Provider.SEM].api_base

CONTRACT_ID = "1234567"
LOGIN = "user@example.com"

TOKEN_URL = f"{API_BASE}/Acces/generateToken"
LOGIN_URL = f"{API_BASE}/Utilisateur/authentification"
CONTRACT_URL = f"{API_BASE}/Abonnement/getContratParDefaut/"
LAST_BILLED_URL = f"{API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}"
HISTORY_URL = f"{API_BASE}/Facturation/listeConsommationsFacturees/{CONTRACT_ID}"
GRANULARITIES_URL = f"{API_BASE}/Consommation/getDatesMinMaxConsommations/{CONTRACT_ID}"
CHART_RE = re.compile(r".*listeConsommationsInstanceAlerteChart.*")
MONTHLY_RE = re.compile(r".*listeConsommationsInstanceAlerteChart.*/MOIS/true")
DAILY_RE = re.compile(r".*listeConsommationsInstanceAlerteChart.*/JOURNEE/true")
LAST_BILLED_RE = re.compile(re.escape(LAST_BILLED_URL))
HISTORY_RE = re.compile(re.escape(HISTORY_URL))
GRANULARITIES_RE = re.compile(re.escape(GRANULARITIES_URL))

USER_INFO = {
    "identifiant": LOGIN,
    "nom": "Doe",
    "prenom": "John",
    "email": LOGIN,
    "titre": "M.",
    "userWebId": 42,
    "meta": {},
    "profils": [],
}
LOGIN_PAYLOAD = {"tokenAuthentique": "fake-ael-token", "utilisateurInfo": USER_INFO}

# The portal answers 400 + ``severity: Information`` on a freshly activated
# contract that has no telemetry yet: a "no data" marker, not an error.
SOFT_400_BODY = json.dumps(
    {
        "severity": "Information",
        "message": "Vous n'avez pas les droits nécessaires pour accéder à cette fonctionnalité.",
    }
)
# Keyword arguments for ``aioresponses.get``: the soft 400, and a real one.
SOFT_400: dict[str, Any] = {
    "status": 400,
    "body": SOFT_400_BODY,
    "content_type": "application/json",
}
HARD_400: dict[str, Any] = {
    "status": 400,
    "body": '{"severity": "Error", "message": "not authorised"}',
    "content_type": "application/json",
}


def granularities_payload(*granularities: str) -> dict:
    """A ``getDatesMinMaxConsommations`` body listing ``granularities``."""
    window = {"dateMin": "2023-11-24T00:00:00+01:00", "dateMax": "2026-11-21T00:00:00+01:00"}
    return {
        "statistiquesConsommation": {
            "datesParGranularite": [
                {"intervalleDates": window, "granulariteLibelle": granularity}
                for granularity in granularities
            ],
            "datesConsoFacturees": window,
        }
    }


def register_granularities(m: aioresponses, *granularities: str) -> None:
    """Register the contract's granularities; by default they include the daily series.

    Listing ``JOURNEE`` keeps the client asking the daily chart, which is
    what every test written before this endpoint was used expects.
    """
    listed = granularities or ("MOIS", "JOURNEE")
    m.get(GRANULARITIES_URL, payload=granularities_payload(*listed), repeat=True)


def count_requests(m: aioresponses, pattern: re.Pattern[str]) -> int:
    """Number of requests sent to a URL matching ``pattern``."""
    return sum(len(calls) for (_, url), calls in m.requests.items() if pattern.match(str(url)))


def register_poll(m: aioresponses, *, billed_index: float = 212.0) -> None:
    """The poll's endpoints, answering for as many polls as a test runs."""
    m.get(LAST_BILLED_URL, payload={"valeurIndex": billed_index}, repeat=True)
    m.get(HISTORY_URL, payload={"nbTotalResultats": 0, "resultats": []}, repeat=True)
    m.get(MONTHLY_RE, payload={"consommations": [{"valeurIndex": 212982}]}, repeat=True)
    m.get(DAILY_RE, payload={"consommations": []}, repeat=True)


def register_token(m: aioresponses) -> None:
    """Register the landing page and the temporary token, the steps before login."""
    m.get(PORTAL_URL + "/", body="<html></html>")
    m.post(TOKEN_URL, payload={"token": "fake-temp-token"})


def register_auth_flow(m: aioresponses) -> None:
    """Register the four endpoints of one full auth flow."""
    register_token(m)
    m.post(LOGIN_URL, payload=LOGIN_PAYLOAD)
    m.get(CONTRACT_URL, payload={"numContrat": CONTRACT_ID})
