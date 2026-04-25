"""Tests for the Eaux de Marseille API client.

These tests mock HTTP calls and do not require Home Assistant.
"""

from __future__ import annotations

import re

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.eaux_marseille.api import (
    _API_BASE,
    _PORTAL_URL,
    ConsumptionData,
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
    EauxDeMarseilleClient,
)

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
            re.compile(r".*listeConsommationsInstanceAlerteChart.*"),
            payload={
                "consommations": [
                    {"volumeConsoEnM3": 5.0},
                    {"volumeConsoEnM3": 5.481},
                ]
            },
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
