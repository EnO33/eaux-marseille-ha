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
CHART_RE = re.compile(r".*listeConsommationsInstanceAlerteChart.*")
MONTHLY_RE = re.compile(r".*listeConsommationsInstanceAlerteChart.*/MOIS/true")
DAILY_RE = re.compile(r".*listeConsommationsInstanceAlerteChart.*/JOURNEE/true")

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


def register_token(m: aioresponses) -> None:
    """Register the landing page and the temporary token, the steps before login."""
    m.get(PORTAL_URL + "/", body="<html></html>")
    m.post(TOKEN_URL, payload={"token": "fake-temp-token"})


def register_auth_flow(m: aioresponses) -> None:
    """Register the four endpoints of one full auth flow."""
    register_token(m)
    m.post(LOGIN_URL, payload=LOGIN_PAYLOAD)
    m.get(CONTRACT_URL, payload={"numContrat": CONTRACT_ID})
