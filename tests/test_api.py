"""Tests for the Eaux de Marseille API client.

These tests mock HTTP calls and do not require Home Assistant.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.parse

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey
from aioresponses import aioresponses

from custom_components.eaux_marseille.api import (
    ConsumptionData,
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
    EauxDeMarseilleClient,
    EauxDeMarseilleNoDataError,
    EauxDeMarseilleSessionExpiredError,
)
from custom_components.eaux_marseille.const import PROVIDERS, Provider
from custom_components.eaux_marseille.diagnostics import _scrub_exception
from custom_components.eaux_marseille.models import encode_context_cookie

# The default provider used by EauxDeMarseilleClient when constructed
# without an explicit ``provider`` kwarg. Tests target SEM since that's
# what the bare ``EauxDeMarseilleClient(...)`` fixture uses.
_PORTAL_URL = PROVIDERS[Provider.SEM].url
_API_BASE = PROVIDERS[Provider.SEM].api_base

CONTRACT_ID = "1234567"


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
    """Return a client instance with fake credentials."""
    c = EauxDeMarseilleClient(
        login="user@example.com",
        password="password",
        contract_id=CONTRACT_ID,
    )
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
        """Authentication raises on token generation failure."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(f"{_API_BASE}/Acces/generateToken", status=500)
            m.post(f"{_API_BASE}/Acces/generateToken", status=500)
            m.post(f"{_API_BASE}/Acces/generateToken", status=500)

            with pytest.raises(EauxDeMarseilleAuthError, match="Token generation failed"):
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

            with pytest.raises(EauxDeMarseilleAuthError, match="no Location header"):
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

            with pytest.raises(EauxDeMarseilleAuthError, match="off-portal host"):
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

            with pytest.raises(EauxDeMarseilleAuthError, match="off-portal host"):
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

            with pytest.raises(EauxDeMarseilleAuthError, match="non-HTTPS scheme"):
                await client.authenticate()

    async def test_authenticate_landing_page_failure(self, client: EauxDeMarseilleClient) -> None:
        """A 4xx/5xx on the landing page is reported with a clear message."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", status=503, body="maintenance")

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
        """A 200 with no 'token' field surfaces a clear auth error."""
        with aioresponses() as m:
            m.get(_PORTAL_URL + "/", body="<html></html>")
            m.post(
                f"{_API_BASE}/Acces/generateToken",
                payload={"unexpected": "shape"},
            )

            with pytest.raises(EauxDeMarseilleAuthError, match="missing 'token'"):
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

            with pytest.raises(EauxDeMarseilleAuthError, match="Too many redirects"):
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

            with pytest.raises(EauxDeMarseilleAuthError) as excinfo:
                await client.authenticate()
            assert "Expected JSON" in str(excinfo.value) or "unexpected response" in str(
                excinfo.value
            )


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
        assert data.current_year_m3 == 0.0
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


class TestDiagnosticsScrub:
    """Verify the diagnostics helper redacts contract id / username."""

    def test_scrub_redacts_contract_id_in_repr(self) -> None:
        """An exception whose repr contains the contract ID gets redacted."""
        err = EauxDeMarseilleApiError("Request to https://example.com/foo/1234567 failed: timeout")
        scrubbed = _scrub_exception(err, contract_id="1234567", username="bob@x.com")
        assert "1234567" not in scrubbed
        assert "**REDACTED**" in scrubbed

    def test_scrub_redacts_username(self) -> None:
        """An exception whose repr contains the username gets redacted."""
        err = EauxDeMarseilleAuthError("Login failed for bob@x.com: HTTP 401")
        scrubbed = _scrub_exception(err, contract_id="1234567", username="bob@x.com")
        assert "bob@x.com" not in scrubbed
        assert "**REDACTED**" in scrubbed

    def test_scrub_passthrough_when_empty_id(self) -> None:
        """No-op when contract id / username are empty."""
        err = EauxDeMarseilleApiError("transport error")
        scrubbed = _scrub_exception(err, contract_id="", username="")
        assert "transport error" in scrubbed
