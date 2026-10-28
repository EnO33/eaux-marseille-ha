"""Tests for the Eaux de Marseille API client.

These tests mock HTTP calls and do not require Home Assistant.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import urllib.parse
from datetime import UTC, datetime

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey
from aioresponses import CallbackResult, aioresponses

from custom_components.eaux_marseille.api import (
    ConsumptionData,
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
    EauxDeMarseilleClient,
    EauxDeMarseilleContractError,
    EauxDeMarseilleNoDataError,
    EauxDeMarseilleSessionExpiredError,
)
from custom_components.eaux_marseille.const import (
    MOBILE_ENDPOINTS,
    PROVIDERS,
    Provider,
    portal_now,
)
from custom_components.eaux_marseille.models import encode_context_cookie

# The default provider used by EauxDeMarseilleClient when constructed
# without an explicit ``provider`` kwarg. Tests target SEM since that's
# what the bare ``EauxDeMarseilleClient(...)`` fixture uses.
_PORTAL_URL = PROVIDERS[Provider.SEM].url
_API_BASE = PROVIDERS[Provider.SEM].api_base

CONTRACT_ID = "1234567"

# 2027-01-01T00:00 in Europe/Paris (UTC+1 in winter).
_PARIS_2027_START = int(datetime(2026, 12, 31, 23, tzinfo=UTC).timestamp())


# Override pytest-homeassistant-custom-component's autouse fixtures that check
# for lingering threads — our API client uses aiohttp which spawns a background
# shutdown thread that lives beyond test teardown. These tests don't touch HA,
# so the cleanup check is not relevant here.
@pytest.fixture(autouse=True)
def verify_cleanup():
    yield


@pytest.fixture(autouse=True)
def expected_lingering_tasks():
    return True


@pytest.fixture(autouse=True)
def expected_lingering_timers():
    return True


@pytest.fixture
async def client() -> EauxDeMarseilleClient:
    """Return a client instance with fake credentials.

    The mobility fallback is disabled here so these tests exercise the web
    portal in isolation (and don't hit the unmocked mobility API); the
    fallback has its own dedicated test.
    """
    c = EauxDeMarseilleClient(
        login="user@example.com",
        password="password",
        contract_id=CONTRACT_ID,
    )
    c._mobile = None
    yield c
    await c.close()


@pytest.fixture
def mock_auth() -> aioresponses:
    """Register all authentication endpoint mocks."""
    with aioresponses() as m:
        m.get(_PORTAL_URL + "/", body="<html></html>")
        m.post(
            f"{_API_BASE}/Acces/generateToken",
            payload={"token": "fake-temp-token"},
        )
        m.post(
            f"{_API_BASE}/Utilisateur/authentification",
            payload={
                "tokenAuthentique": "fake-ael-token",
                "utilisateurInfo": {
                    "identifiant": "user@example.com",
                    "nom": "Doe",
                    "prenom": "John",
                    "email": "user@example.com",
                    "titre": "M.",
                    "userWebId": 42,
                    "meta": {},
                    "profils": [],
                },
            },
        )
        m.get(
            f"{_API_BASE}/Abonnement/getContratParDefaut/",
            payload={"numContrat": CONTRACT_ID},
        )
        yield m


class TestAuthentication:
    """Test the authentication flow."""

    async def test_authenticate_success(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """Authentication succeeds with valid responses."""
        await client.authenticate()

    async def test_authenticate_token_failure(self, client: EauxDeMarseilleClient) -> None:
        """A 5xx on token generation is an outage, not a credentials problem."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(f"{_API_BASE}/Acces/generateToken", status=500)
            m.post(f"{_API_BASE}/Acces/generateToken", status=500)
            m.post(f"{_API_BASE}/Acces/generateToken", status=500)

            with pytest.raises(EauxDeMarseilleApiError, match="Token generation failed"):
                await client.authenticate()

    async def test_authenticate_login_failure(self, client: EauxDeMarseilleClient) -> None:
        """Authentication raises on bad credentials."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(
                f"{_API_BASE}/Acces/generateToken",
                payload={"token": "fake-temp-token"},
            )
            m.post(
                f"{_API_BASE}/Utilisateur/authentification",
                status=401,
            )

            with pytest.raises(EauxDeMarseilleAuthError, match="Login failed"):
                await client.authenticate()

    @pytest.mark.parametrize("status", [400, 404])
    async def test_authenticate_login_4xx_is_auth_error(
        self, client: EauxDeMarseilleClient, status: int
    ) -> None:
        """Any 4xx on the login POST means the portal rejected the credentials."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(f"{_API_BASE}/Acces/generateToken", payload={"token": "fake-temp-token"})
            m.post(f"{_API_BASE}/Utilisateur/authentification", status=status)

            with pytest.raises(EauxDeMarseilleAuthError, match="Login failed"):
                await client.authenticate()

    async def test_authenticate_login_outage_is_api_error(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A 503 on the login POST after retries must not trigger a reauth."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(f"{_API_BASE}/Acces/generateToken", payload={"token": "fake-temp-token"})
            for _ in range(3):
                m.post(f"{_API_BASE}/Utilisateur/authentification", status=503)

            with pytest.raises(EauxDeMarseilleApiError, match="Login failed") as excinfo:
                await client.authenticate()
            assert not isinstance(excinfo.value, EauxDeMarseilleAuthError)

    async def test_authenticate_login_timeout_is_api_error(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A timeout on the login POST is an outage, not a credentials problem."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(f"{_API_BASE}/Acces/generateToken", payload={"token": "fake-temp-token"})
            for _ in range(3):
                m.post(f"{_API_BASE}/Utilisateur/authentification", exception=TimeoutError())

            with pytest.raises(EauxDeMarseilleApiError, match="Login failed"):
                await client.authenticate()

    async def test_authenticate_contract_rejected_is_api_error(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A 4xx after a successful login is not a credentials rejection."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(f"{_API_BASE}/Acces/generateToken", payload={"token": "fake-temp-token"})
            m.post(
                f"{_API_BASE}/Utilisateur/authentification",
                payload={
                    "tokenAuthentique": "fake-ael-token",
                    "utilisateurInfo": {"identifiant": "user@example.com"},
                },
            )
            m.get(f"{_API_BASE}/Abonnement/getContratParDefaut/", status=403)

            with pytest.raises(EauxDeMarseilleApiError) as excinfo:
                await client.authenticate()
            assert not isinstance(excinfo.value, EauxDeMarseilleAuthError)

    async def test_authenticate_follows_307_redirect(self, client: EauxDeMarseilleClient) -> None:
        """Authentication follows a 307 redirect on the login POST and preserves the body."""
        redirected_url = f"{_API_BASE}/Utilisateur/authentification/v2"
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(
                f"{_API_BASE}/Acces/generateToken",
                payload={"token": "fake-temp-token"},
            )
            m.post(
                f"{_API_BASE}/Utilisateur/authentification",
                status=307,
                headers={"Location": redirected_url},
            )
            m.post(
                redirected_url,
                payload={
                    "tokenAuthentique": "fake-ael-token",
                    "utilisateurInfo": {
                        "identifiant": "user@example.com",
                        "nom": "Doe",
                        "prenom": "John",
                        "email": "user@example.com",
                        "titre": "M.",
                        "userWebId": 42,
                        "meta": {},
                        "profils": [],
                    },
                },
            )
            m.get(
                f"{_API_BASE}/Abonnement/getContratParDefaut/",
                payload={"numContrat": CONTRACT_ID},
            )

            await client.authenticate()

    async def test_authenticate_redirect_without_location(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A 3xx with no Location header surfaces as a clear API error."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(
                f"{_API_BASE}/Acces/generateToken",
                payload={"token": "fake-temp-token"},
            )
            m.post(
                f"{_API_BASE}/Utilisateur/authentification",
                status=307,
                body="redirect without location",
            )

            with pytest.raises(EauxDeMarseilleApiError, match="no Location header"):
                await client.authenticate()

    async def test_authenticate_rejects_cross_origin_redirect(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """Off-portal redirects must be refused (CVE-2018-18074-class leak)."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(
                f"{_API_BASE}/Acces/generateToken",
                payload={"token": "fake-temp-token"},
            )
            # Attacker-controlled redirect to a foreign host. If followed,
            # the auth POST body (containing the user's password) and the
            # token header would be forwarded to the attacker.
            m.post(
                f"{_API_BASE}/Utilisateur/authentification",
                status=307,
                headers={"Location": "https://attacker.example/steal"},
            )

            with pytest.raises(EauxDeMarseilleApiError, match="off-portal host"):
                await client.authenticate()

    async def test_authenticate_rejects_protocol_relative_redirect(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """Protocol-relative redirects to foreign hosts must also be refused."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(
                f"{_API_BASE}/Acces/generateToken",
                payload={"token": "fake-temp-token"},
            )
            m.post(
                f"{_API_BASE}/Utilisateur/authentification",
                status=307,
                headers={"Location": "//attacker.example/steal"},
            )

            with pytest.raises(EauxDeMarseilleApiError, match="off-portal host"):
                await client.authenticate()

    async def test_authenticate_rejects_https_to_http_downgrade(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """HTTPS→HTTP scheme downgrade must be refused even on the same host."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(
                f"{_API_BASE}/Acces/generateToken",
                payload={"token": "fake-temp-token"},
            )
            m.post(
                f"{_API_BASE}/Utilisateur/authentification",
                status=307,
                headers={"Location": "http://espaceclients.eauxdemarseille.fr/x"},
            )

            with pytest.raises(EauxDeMarseilleApiError, match="non-HTTPS scheme"):
                await client.authenticate()

    async def test_authenticate_landing_page_failure(self, client: EauxDeMarseilleClient) -> None:
        """A 4xx/5xx on the landing page is reported with a clear message."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", status=503, body="maintenance")

            with pytest.raises(EauxDeMarseilleApiError, match="HTTP 503 on landing page"):
                await client.authenticate()

    @pytest.mark.parametrize(
        ("location", "match"),
        [
            ("https://attacker.example/", "off-portal host"),
            ("http://espaceclients.eauxdemarseille.fr/", "non-HTTPS scheme"),
        ],
    )
    async def test_authenticate_landing_page_rejects_unsafe_redirect(
        self, client: EauxDeMarseilleClient, location: str, match: str
    ) -> None:
        """The landing page obeys the same redirect policy as API calls."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", status=302, headers={"Location": location})

            with pytest.raises(EauxDeMarseilleApiError, match=match):
                await client.authenticate()
            assert len(m.requests) == 1

    async def test_authenticate_landing_page_follows_same_origin_redirect(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A same-origin redirect on the landing page is still followed."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", status=302, headers={"Location": "/accueil"})
            m.get(_PORTAL_URL + "/accueil", status=503, body="maintenance")

            with pytest.raises(EauxDeMarseilleApiError, match="HTTP 503 on landing page"):
                await client.authenticate()

    async def test_authenticate_landing_page_network_error(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A network error on the landing page is wrapped in EauxDeMarseilleApiError."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", exception=aiohttp.ClientConnectionError("DNS"))

            with pytest.raises(EauxDeMarseilleApiError, match="Failed to reach portal"):
                await client.authenticate()

    async def test_authenticate_landing_page_dns_failure(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A DNS resolution failure surfaces a clear, actionable message."""
        key = ConnectionKey(
            host="espaceclients.eauxdemarseille.fr",
            port=443,
            is_ssl=True,
            ssl=True,
            proxy=None,
            proxy_auth=None,
            proxy_headers_hash=None,
        )
        dns_err = aiohttp.ClientConnectorDNSError(key, socket.gaierror("DNS"))

        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", exception=dns_err)

            with pytest.raises(EauxDeMarseilleApiError, match="DNS resolution failed"):
                await client.authenticate()

    async def test_authenticate_token_response_missing_field(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A 200 with no 'token' field is an API drift, not a credentials problem."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(
                f"{_API_BASE}/Acces/generateToken",
                payload={"unexpected": "shape"},
            )

            with pytest.raises(EauxDeMarseilleApiError, match="missing 'token'"):
                await client.authenticate()

    async def test_authenticate_login_response_missing_field(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A 200 login without 'tokenAuthentique' lists the actual fields received."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(
                f"{_API_BASE}/Acces/generateToken",
                payload={"token": "fake-temp-token"},
            )
            m.post(
                f"{_API_BASE}/Utilisateur/authentification",
                payload={"errorCode": 42, "message": "service down"},
            )

            with pytest.raises(EauxDeMarseilleAuthError) as excinfo:
                await client.authenticate()
            # The error lists which keys we did get, to help diagnose API drift
            assert "errorCode" in str(excinfo.value)
            assert "message" in str(excinfo.value)


class TestRedirects:
    """Edge cases of the manual redirect handling."""

    async def test_too_many_redirects(self, client: EauxDeMarseilleClient) -> None:
        """Hitting _MAX_REDIRECTS surfaces a clear error mentioning the chain."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            # 6 hops > _MAX_REDIRECTS (5)
            for i in range(7):
                m.post(
                    f"{_API_BASE}/Acces/generateToken" + ("" if i == 0 else f"/h{i}"),
                    status=307,
                    headers={"Location": f"{_API_BASE}/Acces/generateToken/h{i + 1}"},
                )

            with pytest.raises(EauxDeMarseilleApiError, match="Too many redirects"):
                await client.authenticate()

    async def test_post_to_get_redirect_drops_body(self, client: EauxDeMarseilleClient) -> None:
        """A 303 redirect on a POST converts the next hop to GET and drops body."""
        target = f"{_API_BASE}/Acces/generateToken/v2"
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(
                f"{_API_BASE}/Acces/generateToken",
                status=303,
                headers={"Location": target},
            )
            # Note: aioresponses requires the redirected verb to be matched.
            # After 303 the request must be GET.
            m.get(target, payload={"token": "redirected-token"})
            m.post(
                f"{_API_BASE}/Utilisateur/authentification",
                payload={
                    "tokenAuthentique": "fake-ael-token",
                    "utilisateurInfo": {"identifiant": "user@example.com"},
                },
            )
            m.get(
                f"{_API_BASE}/Abonnement/getContratParDefaut/",
                payload={"numContrat": CONTRACT_ID},
            )

            await client.authenticate()


class TestJSONErrors:
    """Responses that do not look like the expected JSON shape."""

    async def test_html_response_surfaces_clear_error(self, client: EauxDeMarseilleClient) -> None:
        """A 200 with HTML body (e.g. WAF challenge) is reported with the body excerpt."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(
                f"{_API_BASE}/Acces/generateToken",
                body="<html><body>Cloudflare challenge</body></html>",
                content_type="text/html",
            )

            with pytest.raises(EauxDeMarseilleApiError) as excinfo:
                await client.authenticate()
            assert "Expected JSON" in str(excinfo.value) or "unexpected response" in str(
                excinfo.value
            )

    async def test_json_list_body_is_api_error(self, client: EauxDeMarseilleClient) -> None:
        """A JSON list where an object is expected is an API error, not AttributeError."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(f"{_API_BASE}/Acces/generateToken", payload=["unexpected"])

            with pytest.raises(EauxDeMarseilleApiError, match="Expected a JSON object"):
                await client.authenticate()

    @pytest.mark.parametrize("user_info", [None, [], {}, {"nom": "Doe"}])
    async def test_login_without_user_identifier_is_api_error(
        self, client: EauxDeMarseilleClient, user_info: object
    ) -> None:
        """A login without ``utilisateurInfo.identifiant`` is an API error, not a credentials one."""
        payload: dict[str, object] = {"tokenAuthentique": "fake-ael-token"}
        if user_info is not None:
            payload["utilisateurInfo"] = user_info
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(f"{_API_BASE}/Acces/generateToken", payload={"token": "fake-temp-token"})
            m.post(f"{_API_BASE}/Utilisateur/authentification", payload=payload)

            with pytest.raises(EauxDeMarseilleApiError, match="utilisateurInfo") as excinfo:
                await client.authenticate()
            assert not isinstance(excinfo.value, EauxDeMarseilleAuthError)


class TestFetch:
    """Test data fetching."""

    async def test_fetch_returns_consumption_data(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """fetch() returns a populated ConsumptionData."""
        mock_auth.get(
            f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
            payload={
                "valeurIndex": 193.0,
                "volumeConsoEnM3": 18.0,
                "volumeConsoEnLitres": 18000,
                "dateReleve": "2026-03-05T00:00:00",
                "nbJours": 94,
                "moyenne": 0.1915,
            },
        )
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*/MOIS/true"),
            payload={
                "consommations": [
                    {"volumeConsoEnM3": 5.0, "valeurIndex": 207501},
                    {"volumeConsoEnM3": 5.481, "valeurIndex": 212982},
                ]
            },
        )
        # Monthly-only contract: the daily series is unauthorised (soft 400).
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*/JOURNEE/true"),
            payload={"consommations": []},
        )
        mock_auth.get(
            f"{_API_BASE}/Facturation/listeConsommationsFacturees/{CONTRACT_ID}",
            payload={
                "nbTotalResultats": 10,
                "resultats": [
                    {"volumeConsoEnM3": 18.0, "dateReleve": "2026-03-05"},
                    {"volumeConsoEnM3": 18.0, "dateReleve": "2025-12-01"},
                ],
            },
        )

        await client.authenticate()
        data = await client.fetch()

        assert isinstance(data, ConsumptionData)
        assert data.index_m3 == 193.0
        # No daily series, so the precise index falls back to the LAST
        # monthly entry's valeurIndex (litres -> m³): 212982 L -> 212.982 m³.
        assert data.index_precise_m3 == 212.982
        assert data.last_reading_m3 == 18.0
        assert data.last_reading_litres == 18000
        assert data.last_reading_days == 94
        assert data.total_readings == 10
        assert data.previous_reading_m3 == 18.0

    async def test_api_error_on_failure(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """fetch() raises EauxDeMarseilleApiError on HTTP errors."""
        # 4xx: not retried, fails immediately
        mock_auth.get(
            f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
            status=404,
        )

        await client.authenticate()
        with pytest.raises(EauxDeMarseilleApiError):
            await client.fetch()


class TestFreshContract:
    """The portal returns 400 + ``severity: Information`` on freshly
    activated contracts that haven't accumulated telemetry yet. This
    must not fail the fetch — substitute an empty result for the
    affected endpoint and let the rest go through.
    """

    _SOFT_400_BODY = json.dumps(
        {
            "severity": "Information",
            "message": (
                "Vous n'avez pas les droits nécessaires pour accéder à cette fonctionnalité."
            ),
        }
    )

    async def test_soft_400_on_monthly_endpoint_keeps_other_data(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """The exact scenario reported by the first Vivaigo user: monthly
        chart endpoint returns the soft 400 while the other two endpoints
        carry data."""
        mock_auth.get(
            f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
            payload={"valeurIndex": 5.0, "volumeConsoEnM3": 5.0},
        )
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*"),
            status=400,
            body=self._SOFT_400_BODY,
            content_type="application/json",
            repeat=True,
        )
        mock_auth.get(
            f"{_API_BASE}/Facturation/listeConsommationsFacturees/{CONTRACT_ID}",
            payload={"nbTotalResultats": 1, "resultats": [{"volumeConsoEnM3": 5.0}]},
        )

        await client.authenticate()
        data = await client.fetch()

        # Index/last-reading from endpoint 1 still flows through.
        assert data.index_m3 == 5.0
        assert data.last_reading_m3 == 5.0
        # Monthly endpoint produced no data, so derived fields are empty.
        assert data.current_month_m3 is None
        assert data.current_year_m3 is None
        # History endpoint flowed through too.
        assert data.total_readings == 1

    async def test_soft_400_on_all_endpoints_returns_empty_data(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """Worst case: brand new contract, all three endpoints soft-fail.
        The integration must still load with a valid (mostly-None)
        ConsumptionData rather than raising.
        """
        for path in (
            f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
            f"{_API_BASE}/Facturation/listeConsommationsFacturees/{CONTRACT_ID}",
        ):
            mock_auth.get(
                path,
                status=400,
                body=self._SOFT_400_BODY,
                content_type="application/json",
            )
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*"),
            status=400,
            body=self._SOFT_400_BODY,
            content_type="application/json",
            repeat=True,
        )

        await client.authenticate()
        data = await client.fetch()

        assert isinstance(data, ConsumptionData)
        assert data.index_m3 is None
        assert data.last_reading_m3 is None
        assert data.total_readings == 0

    async def test_hard_400_still_raises(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """A 400 without the ``severity: Information`` marker is a real
        error and must still surface as EauxDeMarseilleApiError.
        """
        mock_auth.get(
            f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
            status=400,
            body='{"severity": "Error", "message": "malformed request"}',
            content_type="application/json",
        )
        await client.authenticate()
        with pytest.raises(EauxDeMarseilleApiError) as excinfo:
            await client.fetch()
        # Specifically NOT the no-data subclass — this is a hard 400.
        assert not isinstance(excinfo.value, EauxDeMarseilleNoDataError)

    async def test_fetch_monthly_range_tolerates_soft_400(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """Statistics import path also tolerates the soft 400 — returns
        an empty list instead of failing the whole import.
        """
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*"),
            status=400,
            body=self._SOFT_400_BODY,
            content_type="application/json",
        )
        await client.authenticate()
        entries = await client.fetch_monthly_range(2024)
        assert entries == []


class TestCheckContract:
    """``check_contract`` tells a contract outside the account from an outage."""

    _LAST_BILLED_URL = f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}"

    async def test_contract_of_the_account_passes(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        mock_auth.get(self._LAST_BILLED_URL, payload={"valeurIndex": 5.0})
        await client.check_contract()

    async def test_fresh_contract_without_data_passes(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """The soft 400 of a freshly activated contract is not a wrong number."""
        mock_auth.get(
            self._LAST_BILLED_URL,
            status=400,
            body=TestFreshContract._SOFT_400_BODY,
            content_type="application/json",
        )
        await client.check_contract()

    async def test_contract_outside_the_account_raises(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """SEM answers 404 for a contract number the account does not hold."""
        mock_auth.get(self._LAST_BILLED_URL, status=404, body="")
        with pytest.raises(EauxDeMarseilleContractError, match="HTTP 404"):
            await client.check_contract()

    async def test_portal_outage_is_not_a_contract_error(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        mock_auth.get(self._LAST_BILLED_URL, status=500, repeat=True)
        with pytest.raises(EauxDeMarseilleApiError):
            await client.check_contract()


class TestDailyTelemetry:
    """Contracts with daily telemetry (JOURNEE) get a fresher index."""

    def _register_data_endpoints(self, m: aioresponses, *, repeat: bool = False) -> None:
        """Register the three standard consumption endpoints."""
        m.get(
            f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
            payload={"valeurIndex": 212.0, "volumeConsoEnM3": 19.0},
            repeat=repeat,
        )
        m.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*/MOIS/true"),
            payload={
                "consommations": [
                    {"volumeConsoEnM3": 2.337, "valeurIndex": 212982},
                ]
            },
            repeat=repeat,
        )
        m.get(
            f"{_API_BASE}/Facturation/listeConsommationsFacturees/{CONTRACT_ID}",
            payload={"nbTotalResultats": 1, "resultats": [{"volumeConsoEnM3": 19.0}]},
            repeat=repeat,
        )

    async def test_daily_index_preferred_when_available(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """When the JOURNEE series carries data, index_precise comes from
        its freshest entry instead of the monthly chart.

        The portal serves the daily series newest-first, so the freshest
        reading (213105 L, dated latest) is the *first* list item — the
        client must pick by date, not by position.
        """
        self._register_data_endpoints(mock_auth)
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*/JOURNEE/true"),
            payload={
                "consommations": [
                    {"dateReleve": "2026-06-14T00:00:00+02:00", "valeurIndex": 213105},
                    {"dateReleve": "2026-06-13T00:00:00+02:00", "valeurIndex": 212900},
                ]
            },
        )

        data = await client.fetch()

        assert data.index_precise_m3 == 213.105

    async def test_monthly_only_contract_falls_back_to_monthly_index(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """A contract without daily data (soft 400 on JOURNEE) keeps
        working: index_precise falls back to the monthly chart value."""
        self._register_data_endpoints(mock_auth)
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*/JOURNEE/true"),
            status=400,
            body=TestFreshContract._SOFT_400_BODY,
            content_type="application/json",
        )
        data = await client.fetch()
        assert data.index_precise_m3 == 212.982

    async def test_daily_hard_400_is_tolerated(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """A hard 400 on the JOURNEE endpoint (unauthorised, not the soft
        'no data' marker) must not break the poll — the daily series is
        optional, so it degrades to the monthly index."""
        self._register_data_endpoints(mock_auth)
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*/JOURNEE/true"),
            status=400,
            body='{"severity": "Error", "message": "not authorised"}',
            content_type="application/json",
        )
        data = await client.fetch()
        assert data.index_precise_m3 == 212.982

    def test_chart_path_bounds_year_in_portal_time(self, client: EauxDeMarseilleClient) -> None:
        """The chart window runs from 1 January to 31 December in Paris
        time (UTC+1 in winter), not in UTC."""
        end = int(datetime(2027, 12, 31, 22, 59, 59, tzinfo=UTC).timestamp())
        path = client._chart_path(2027, "MOIS")
        assert path.endswith(f"/{_PARIS_2027_START}/{end}/MOIS/true")

    @pytest.mark.usefixtures("new_year_in_paris")
    async def test_fetch_queries_the_portal_year_at_new_year(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """Just after midnight on 1 January in Paris (still 31 December in
        UTC), fetch() asks for the new year's charts."""
        self._register_data_endpoints(mock_auth)
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*/JOURNEE/true"),
            payload={"consommations": []},
        )
        await client.fetch()

        charts = [str(url) for _, url in mock_auth.requests if "AlerteChart" in str(url)]
        assert charts
        assert all(f"/{_PARIS_2027_START}/" in url for url in charts)

    async def test_fetch_daily_range_returns_entries(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """fetch_daily_range returns the consommations list."""
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*/JOURNEE/true"),
            payload={
                "consommations": [
                    {"dateReleve": "2026-06-09T00:00:00+02:00", "volumeConsoEnM3": 0.2},
                ]
            },
        )
        await client.authenticate()
        entries = await client.fetch_daily_range(2026)
        assert len(entries) == 1
        assert entries[0]["volumeConsoEnM3"] == 0.2

    async def test_fetch_daily_range_tolerates_unauthorized(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """fetch_daily_range returns [] when the contract has no daily
        telemetry (hard 400), rather than propagating the error."""
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*/JOURNEE/true"),
            status=400,
            body='{"severity": "Error", "message": "not authorised"}',
            content_type="application/json",
        )
        await client.authenticate()
        entries = await client.fetch_daily_range(2026)
        assert entries == []

    async def test_fetch_daily_range_falls_back_to_mobile(self, mock_auth: aioresponses) -> None:
        """When the web portal has no daily series, fetch_daily_range fills
        it from the mobility API for recent years."""
        # A mobile-enabled client (the shared fixture disables mobility).
        client = EauxDeMarseilleClient(
            login="user@example.com", password="password", contract_id=CONTRACT_ID
        )
        year = portal_now().year
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*/JOURNEE/true"),
            status=400,
            body='{"severity": "Information", "message": "no data"}',
            content_type="application/json",
        )
        base = MOBILE_ENDPOINTS[Provider.SEM].base_url
        mock_auth.post(f"{base}/connect", payload={"Code": 100, "Token": "tok", "Result": {}})
        mock_auth.post(
            f"{base}/getListeReleves/",
            payload={
                "Code": 100,
                "Result": {
                    "Releves": [
                        {"DateReleve": f"05/01/{year}", "ValeurIndex": "500", "Consommation": 50.0}
                    ]
                },
            },
            repeat=True,
        )
        try:
            await client.authenticate()
            entries = await client.fetch_daily_range(year)
        finally:
            await client.close()

        assert entries
        assert entries[0]["valeurIndex"] == 500
        assert entries[0]["volumeConsoEnM3"] == 0.05


class TestSessionRecovery:
    """Test that the client caches auth and recovers from session expiry."""

    async def test_authenticate_is_idempotent(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """The second authenticate() call short-circuits when already auth'd."""
        await client.authenticate()
        assert client._auth.is_authenticated

        # mock_auth registers each auth endpoint ONCE. A second full auth
        # would 404 on the unregistered second hit. The fact that this
        # succeeds without aioresponses raising proves we didn't replay.
        await client.authenticate()
        assert client._auth.is_authenticated

    async def test_fetch_skips_redundant_auth(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """Two consecutive fetches share one auth (unless 401 happens)."""
        mock_auth.get(
            f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
            payload={"valeurIndex": 1.0, "volumeConsoEnM3": 1.0},
            repeat=True,
        )
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*"),
            payload={"consommations": []},
            repeat=True,
        )
        mock_auth.get(
            f"{_API_BASE}/Facturation/listeConsommationsFacturees/{CONTRACT_ID}",
            payload={"nbTotalResultats": 0, "resultats": []},
            repeat=True,
        )

        await client.fetch()
        await client.fetch()
        # If a re-auth had been triggered between the two fetches the
        # mock_auth fixture (single-shot endpoints) would have raised.

    async def test_fetch_recovers_from_401(self, client: EauxDeMarseilleClient) -> None:
        """A 401 on the first fetch triggers transparent re-auth + retry."""
        with aioresponses() as m:
            # ---- First auth flow ----
            _register_auth_flow(m)
            # ---- First fetch: 401 on the very first call ----
            m.get(
                f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
                status=401,
                body="session expired",
            )
            # ---- Second auth flow (recovery) ----
            _register_auth_flow(m)
            # ---- Second fetch: succeeds ----
            m.get(
                f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
                payload={"valeurIndex": 42.0, "volumeConsoEnM3": 1.0},
            )
            m.get(
                re.compile(r".*listeConsommationsInstanceAlerteChart.*"),
                payload={"consommations": []},
                repeat=True,
            )
            m.get(
                f"{_API_BASE}/Facturation/listeConsommationsFacturees/{CONTRACT_ID}",
                payload={"nbTotalResultats": 0, "resultats": []},
            )

            data = await client.fetch()
            assert data.index_m3 == 42.0

    async def test_403_also_triggers_recovery(self, client: EauxDeMarseilleClient) -> None:
        """403 is treated like 401 for the purpose of session recovery."""
        with aioresponses() as m:
            _register_auth_flow(m)
            m.get(
                f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
                status=403,
                body="forbidden",
            )
            _register_auth_flow(m)
            m.get(
                f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
                payload={"valeurIndex": 42.0, "volumeConsoEnM3": 1.0},
            )
            m.get(
                re.compile(r".*listeConsommationsInstanceAlerteChart.*"),
                payload={"consommations": []},
                repeat=True,
            )
            m.get(
                f"{_API_BASE}/Facturation/listeConsommationsFacturees/{CONTRACT_ID}",
                payload={"nbTotalResultats": 0, "resultats": []},
            )

            data = await client.fetch()
            assert data.index_m3 == 42.0

    async def test_repeated_session_expiry_propagates(self, client: EauxDeMarseilleClient) -> None:
        """If the retry also gets 401, surface the failure (no infinite loop)."""
        with aioresponses() as m:
            _register_auth_flow(m)
            # First fetch: 401
            m.get(
                f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
                status=401,
            )
            # Recovery auth
            _register_auth_flow(m)
            # Retry fetch: 401 again -> we give up
            m.get(
                f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
                status=401,
            )

            with pytest.raises(EauxDeMarseilleSessionExpiredError):
                await client.fetch()

    async def test_other_4xx_does_not_trigger_reauth(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """A 404 must NOT trigger re-auth (would mask real bugs)."""
        mock_auth.get(
            f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
            status=404,
        )
        # mock_auth only registers ONE auth flow. If we ever re-auth on a
        # non-401, the test would fail because the second auth would 404
        # and produce a different (auth) error.
        with pytest.raises(EauxDeMarseilleApiError) as excinfo:
            await client.fetch()
        assert not isinstance(excinfo.value, EauxDeMarseilleSessionExpiredError)

    async def test_concurrent_expiry_reauthenticates_once(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """The coordinator poll and the statistics import share the client:
        when both hit an expired token, only one of them logs in again (#79)."""
        client._auth._ael_token = "expired-token"
        both_expired = asyncio.Event()
        expired_calls = 0

        async def _by_token(_url: object, **kwargs: object) -> CallbackResult:
            nonlocal expired_calls
            headers = kwargs.get("headers") or {}
            assert isinstance(headers, dict)
            if headers.get("token") == "expired-token":
                # Hold each stale request until both callers have sent one,
                # so they really see the expiry at the same time.
                expired_calls += 1
                if expired_calls == 2:
                    both_expired.set()
                await both_expired.wait()
                return CallbackResult(status=401)
            return CallbackResult(payload={"consommations": [], "valeurIndex": 42.0})

        with aioresponses() as m:
            # A single auth flow: a second login would hit an unmocked URL.
            _register_auth_flow(m)
            m.get(
                re.compile(rf"{re.escape(_API_BASE)}/(?!Abonnement).*"),
                callback=_by_token,
                repeat=True,
            )

            data, daily = await asyncio.gather(client.fetch(), client.fetch_daily_range(2026))

        assert data.index_m3 == 42.0
        assert daily == []
        logins = [key for key in m.requests if str(key[1]).endswith("/authentification")]
        assert len(m.requests[logins[0]]) == 1


def _register_auth_flow(m: aioresponses) -> None:
    """Register the four endpoints of one full auth flow."""
    m.get(_PORTAL_URL + "/", body="<html></html>")
    m.post(
        f"{_API_BASE}/Acces/generateToken",
        payload={"token": "fake-temp-token"},
    )
    m.post(
        f"{_API_BASE}/Utilisateur/authentification",
        payload={
            "tokenAuthentique": "fake-ael-token",
            "utilisateurInfo": {
                "identifiant": "user@example.com",
                "nom": "Doe",
                "prenom": "John",
                "email": "user@example.com",
                "titre": "M.",
                "userWebId": 42,
                "meta": {},
                "profils": [],
            },
        },
    )
    m.get(
        f"{_API_BASE}/Abonnement/getContratParDefaut/",
        payload={"numContrat": CONTRACT_ID},
    )


class TestFetchMonthlyRange:
    """Test monthly range fetching for statistics."""

    async def test_fetch_monthly_range(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """fetch_monthly_range returns consumption entries."""
        mock_auth.get(
            re.compile(r".*listeConsommationsInstanceAlerteChart.*"),
            payload={
                "consommations": [
                    {"dateReleve": "2024-07-15T00:00:00+02:00", "volumeConsoEnM3": 3.0},
                    {"dateReleve": "2024-08-15T00:00:00+02:00", "volumeConsoEnM3": 4.5},
                ]
            },
        )

        await client.authenticate()
        entries = await client.fetch_monthly_range(2024)

        assert len(entries) == 2
        assert entries[0]["volumeConsoEnM3"] == 3.0
        assert entries[1]["volumeConsoEnM3"] == 4.5


class TestRetry:
    """Test retry behavior on transient errors."""

    async def test_retry_on_server_error(self, client: EauxDeMarseilleClient) -> None:
        """5xx errors trigger a retry that eventually succeeds."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            # First attempt fails, second succeeds
            m.post(f"{_API_BASE}/Acces/generateToken", status=503)
            m.post(
                f"{_API_BASE}/Acces/generateToken",
                payload={"token": "fake-temp-token"},
            )
            m.post(
                f"{_API_BASE}/Utilisateur/authentification",
                payload={
                    "tokenAuthentique": "fake-ael-token",
                    "utilisateurInfo": {
                        "identifiant": "user@example.com",
                    },
                },
            )
            m.get(
                f"{_API_BASE}/Abonnement/getContratParDefaut/",
                payload={"numContrat": CONTRACT_ID},
            )

            await client.authenticate()


class TestClientLifecycle:
    """Test client creation and teardown."""

    async def test_close(self, client: EauxDeMarseilleClient) -> None:
        """close() does not raise."""
        await client.close()

    async def test_default_timeout(self) -> None:
        """Client uses default timeout of 15s."""
        c = EauxDeMarseilleClient("a", "b", "c")
        assert c._timeout.total == 15
        await c.close()

    async def test_custom_timeout(self) -> None:
        """Client accepts a custom timeout."""
        c = EauxDeMarseilleClient("a", "b", "c", timeout=30)
        assert c._timeout.total == 30
        await c.close()

    async def test_external_session_not_closed(self) -> None:
        """When an external session is provided, close() does not close it."""
        session = aiohttp.ClientSession()
        c = EauxDeMarseilleClient("a", "b", "c", session=session)
        await c.close()
        assert not session.closed
        await session.close()


class TestEncodeContextCookie:
    """Test that the AEL_CONTEXT cookie is built as valid JSON."""

    def _decode(self, encoded: str) -> dict:
        """Reverse the URL-encoding + JSON parse to inspect the payload."""
        return json.loads(urllib.parse.unquote_plus(encoded))

    def test_round_trip_simple(self) -> None:
        """A trivial user roundtrips cleanly through JSON."""
        encoded = encode_context_cookie(
            contract={"numContrat": "1234567"},
            user_info={
                "identifiant": "user@example.com",
                "nom": "Doe",
                "prenom": "John",
                "email": "user@example.com",
                "titre": "M.",
                "userWebId": 42,
                "meta": {},
                "profils": [],
            },
            ael_token="fake-token",
        )
        payload = self._decode(encoded)
        assert payload["type"] == "contrat"
        assert payload["object"] == {"numContrat": "1234567"}
        assert payload["user"]["nomComplet"] == "John Doe"
        assert payload["user"]["tokenAuthentique"] == "fake-token"

    def test_apostrophe_in_name_preserved(self) -> None:
        """An apostrophe in the user name doesn't corrupt the JSON."""
        encoded = encode_context_cookie(
            contract={"numContrat": "1234567"},
            user_info={
                "identifiant": "obrien@example.com",
                "nom": "O'Brien",
                "prenom": "Sean",
                "email": "obrien@example.com",
                "titre": "M.",
                "userWebId": 1,
                "meta": {},
                "profils": [],
            },
            ael_token="fake-token",
        )
        # Must not raise: previously str(dict).replace("'", '"') would
        # have produced invalid JSON for "O'Brien".
        payload = self._decode(encoded)
        assert payload["user"]["nom"] == "O'Brien"
        assert payload["user"]["nomComplet"] == "Sean O'Brien"

    def test_none_values_become_empty_strings(self) -> None:
        """Explicit None values do not surface as the literal 'None' string."""
        encoded = encode_context_cookie(
            contract={"numContrat": "1234567"},
            user_info={
                "identifiant": "user@example.com",
                "nom": None,
                "prenom": None,
                "email": None,
                "titre": None,
                "userWebId": None,
                "meta": None,
                "profils": None,
            },
            ael_token="fake-token",
        )
        payload = self._decode(encoded)
        # The literal string "None" must not appear anywhere — that was
        # the str(dict) bug.
        assert "None" not in encoded
        assert payload["user"]["nom"] == ""
        assert payload["user"]["nomComplet"] == ""
        assert payload["user"]["meta"] == {}
        assert payload["user"]["profils"] == []


class TestRedact:
    """The client hides its contract number and login from log messages (#85)."""

    def test_redacts_contract_id(self, client: EauxDeMarseilleClient) -> None:
        """A request URL carrying the contract number is scrubbed."""
        scrubbed = client.redact(f"HTTP 500 at {_API_BASE}/foo/{CONTRACT_ID}")
        assert CONTRACT_ID not in scrubbed
        assert "**REDACTED**" in scrubbed

    def test_redacts_login_whatever_its_case(self, client: EauxDeMarseilleClient) -> None:
        """The portal may echo the login in another case: it is scrubbed too."""
        scrubbed = client.redact("Login failed for USER@Example.com")
        assert "example.com" not in scrubbed.lower()

    async def test_passthrough_when_identifiers_are_empty(self) -> None:
        """Empty identifiers leave the text untouched."""
        bare = EauxDeMarseilleClient(login="", password="", contract_id="")
        try:
            assert bare.redact("transport error") == "transport error"
        finally:
            await bare.close()

    async def test_error_messages_do_not_echo_the_response_body(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A rejected login body never reaches the exception message."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(f"{_API_BASE}/Acces/generateToken", payload={"token": "fake-temp-token"})
            m.post(
                f"{_API_BASE}/Utilisateur/authentification",
                status=401,
                body='{"email": "user@example.com", "secret": "leaky"}',
            )

            with pytest.raises(EauxDeMarseilleAuthError) as excinfo:
                await client.authenticate()
        assert "leaky" not in str(excinfo.value)
        assert "user@example.com" not in str(excinfo.value)
