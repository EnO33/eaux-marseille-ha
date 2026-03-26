"""Tests for the Eaux de Marseille API client.

These tests mock HTTP calls and do not require Home Assistant.
"""

from __future__ import annotations

import pytest
import requests_mock as rm

from custom_components.eaux_marseille.api import (
    ConsumptionData,
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
    EauxDeMarseilleClient,
    _API_BASE,
    _PORTAL_URL,
)

CONTRACT_ID = "1234567"


@pytest.fixture
def client() -> EauxDeMarseilleClient:
    """Return a client instance with fake credentials."""
    return EauxDeMarseilleClient(
        login="user@example.com",
        password="password",
        contract_id=CONTRACT_ID,
    )


@pytest.fixture
def mock_auth(requests_mock: rm.Mocker) -> rm.Mocker:
    """Register all authentication endpoint mocks."""
    requests_mock.get(_PORTAL_URL + "/", text="<html></html>")
    requests_mock.post(
        f"{_API_BASE}/Acces/generateToken",
        json={"token": "fake-temp-token"},
    )
    requests_mock.post(
        f"{_API_BASE}/Utilisateur/authentification",
        json={
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
    requests_mock.get(
        f"{_API_BASE}/Abonnement/getContratParDefaut/",
        json={"numContrat": CONTRACT_ID},
    )
    return requests_mock


class TestAuthentication:
    """Test the authentication flow."""

    def test_authenticate_success(self, client: EauxDeMarseilleClient, mock_auth: rm.Mocker) -> None:
        """Authentication succeeds with valid responses."""
        client.authenticate()
        assert mock_auth.call_count == 4

    def test_authenticate_token_failure(
        self, client: EauxDeMarseilleClient, requests_mock: rm.Mocker
    ) -> None:
        """Authentication raises on token generation failure."""
        requests_mock.get(_PORTAL_URL + "/", text="<html></html>")
        requests_mock.post(f"{_API_BASE}/Acces/generateToken", status_code=500)

        with pytest.raises(EauxDeMarseilleAuthError, match="Token generation failed"):
            client.authenticate()

    def test_authenticate_login_failure(
        self, client: EauxDeMarseilleClient, requests_mock: rm.Mocker
    ) -> None:
        """Authentication raises on bad credentials."""
        requests_mock.get(_PORTAL_URL + "/", text="<html></html>")
        requests_mock.post(
            f"{_API_BASE}/Acces/generateToken",
            json={"token": "fake-temp-token"},
        )
        requests_mock.post(
            f"{_API_BASE}/Utilisateur/authentification",
            status_code=401,
        )

        with pytest.raises(EauxDeMarseilleAuthError, match="Login failed"):
            client.authenticate()


class TestFetch:
    """Test data fetching."""

    def test_fetch_returns_consumption_data(
        self, client: EauxDeMarseilleClient, mock_auth: rm.Mocker
    ) -> None:
        """fetch() returns a populated ConsumptionData."""
        import re

        mock_auth.get(
            f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
            json={
                "valeurIndex": 193.0,
                "volumeConsoEnM3": 18.0,
                "volumeConsoEnLitres": 18000,
                "dateReleve": "2026-03-05T00:00:00",
                "nbJours": 94,
                "moyenne": 0.1915,
            },
        )
        mock_auth.get(
            re.compile(r"/Consommation/listeConsommationsInstanceAlerteChart/"),
            json={
                "consommations": [
                    {"volumeConsoEnM3": 5.0},
                    {"volumeConsoEnM3": 5.481},
                ]
            },
        )
        mock_auth.get(
            f"{_API_BASE}/Facturation/listeConsommationsFacturees/{CONTRACT_ID}",
            json={
                "nbTotalResultats": 10,
                "resultats": [
                    {"volumeConsoEnM3": 18.0, "dateReleve": "2026-03-05"},
                    {"volumeConsoEnM3": 18.0, "dateReleve": "2025-12-01"},
                ],
            },
        )

        client.authenticate()
        data = client.fetch()

        assert isinstance(data, ConsumptionData)
        assert data.index_m3 == 193.0
        assert data.last_reading_m3 == 18.0
        assert data.last_reading_litres == 18000
        assert data.last_reading_days == 94
        assert data.total_readings == 10
        assert data.previous_reading_m3 == 18.0

    def test_api_error_on_failure(
        self, client: EauxDeMarseilleClient, mock_auth: rm.Mocker
    ) -> None:
        """fetch() raises EauxDeMarseilleApiError on HTTP errors."""
        mock_auth.get(
            f"{_API_BASE}/TableauDeBord/derniereConsommationFacturee/{CONTRACT_ID}",
            status_code=500,
        )

        client.authenticate()
        with pytest.raises(EauxDeMarseilleApiError):
            client.fetch()


class TestFetchMonthlyRange:
    """Test monthly range fetching for statistics."""

    def test_fetch_monthly_range(
        self, client: EauxDeMarseilleClient, mock_auth: rm.Mocker
    ) -> None:
        """fetch_monthly_range returns consumption entries."""
        mock_auth.get(
            rm.ANY,
            json={
                "consommations": [
                    {"dateReleve": "2024-07-15T00:00:00+02:00", "volumeConsoEnM3": 3.0},
                    {"dateReleve": "2024-08-15T00:00:00+02:00", "volumeConsoEnM3": 4.5},
                ]
            },
        )

        client.authenticate()
        entries = client.fetch_monthly_range(2024)

        assert len(entries) == 2
        assert entries[0]["volumeConsoEnM3"] == 3.0
        assert entries[1]["volumeConsoEnM3"] == 4.5


class TestClientLifecycle:
    """Test client creation and teardown."""

    def test_close(self, client: EauxDeMarseilleClient) -> None:
        """close() does not raise."""
        client.close()

    def test_default_timeout(self) -> None:
        """Client uses default timeout of 15s."""
        c = EauxDeMarseilleClient("a", "b", "c")
        assert c._timeout == 15
        c.close()

    def test_custom_timeout(self) -> None:
        """Client accepts a custom timeout."""
        c = EauxDeMarseilleClient("a", "b", "c", timeout=30)
        assert c._timeout == 30
        c.close()
