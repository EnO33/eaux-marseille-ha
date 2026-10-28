"""Fetching consumption: the poll, the monthly range, the contract check."""

from __future__ import annotations

import pytest
from aioresponses import aioresponses

from custom_components.eaux_marseille.api import (
    ConsumptionData,
    EauxDeMarseilleApiError,
    EauxDeMarseilleClient,
    EauxDeMarseilleContractError,
    EauxDeMarseilleNoDataError,
)

from .portal import CHART_RE, DAILY_RE, HISTORY_URL, LAST_BILLED_URL, MONTHLY_RE, SOFT_400


class TestFetch:
    """Test data fetching."""

    async def test_fetch_returns_consumption_data(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """fetch() returns a populated ConsumptionData."""
        mock_auth.get(
            LAST_BILLED_URL,
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
            MONTHLY_RE,
            payload={
                "consommations": [
                    {"volumeConsoEnM3": 5.0, "valeurIndex": 207501},
                    {"volumeConsoEnM3": 5.481, "valeurIndex": 212982},
                ]
            },
        )
        # Monthly-only contract: the daily series is unauthorised (soft 400).
        mock_auth.get(DAILY_RE, payload={"consommations": []})
        mock_auth.get(
            HISTORY_URL,
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
        mock_auth.get(LAST_BILLED_URL, status=404)

        await client.authenticate()
        with pytest.raises(EauxDeMarseilleApiError):
            await client.fetch()

    async def test_fetch_monthly_range(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """fetch_monthly_range returns consumption entries."""
        mock_auth.get(
            CHART_RE,
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


class TestFreshContract:
    """The portal returns 400 + ``severity: Information`` on freshly
    activated contracts that haven't accumulated telemetry yet. This
    must not fail the fetch — substitute an empty result for the
    affected endpoint and let the rest go through.
    """

    async def test_soft_400_on_monthly_endpoint_keeps_other_data(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """The exact scenario reported by the first Vivaigo user: monthly
        chart endpoint returns the soft 400 while the other two endpoints
        carry data."""
        mock_auth.get(LAST_BILLED_URL, payload={"valeurIndex": 5.0, "volumeConsoEnM3": 5.0})
        mock_auth.get(CHART_RE, repeat=True, **SOFT_400)
        mock_auth.get(
            HISTORY_URL, payload={"nbTotalResultats": 1, "resultats": [{"volumeConsoEnM3": 5.0}]}
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
        mock_auth.get(LAST_BILLED_URL, **SOFT_400)
        mock_auth.get(HISTORY_URL, **SOFT_400)
        mock_auth.get(CHART_RE, repeat=True, **SOFT_400)

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
            LAST_BILLED_URL,
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
        mock_auth.get(CHART_RE, **SOFT_400)
        await client.authenticate()
        entries = await client.fetch_monthly_range(2024)
        assert entries == []


class TestCheckContract:
    """``check_contract`` tells a contract outside the account from an outage."""

    async def test_contract_of_the_account_passes(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        mock_auth.get(LAST_BILLED_URL, payload={"valeurIndex": 5.0})
        await client.check_contract()

    async def test_fresh_contract_without_data_passes(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """The soft 400 of a freshly activated contract is not a wrong number."""
        mock_auth.get(LAST_BILLED_URL, **SOFT_400)
        await client.check_contract()

    async def test_contract_outside_the_account_raises(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """SEM answers 404 for a contract number the account does not hold."""
        mock_auth.get(LAST_BILLED_URL, status=404, body="")
        with pytest.raises(EauxDeMarseilleContractError, match="HTTP 404"):
            await client.check_contract()

    async def test_portal_outage_is_not_a_contract_error(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        mock_auth.get(LAST_BILLED_URL, status=500, repeat=True)
        with pytest.raises(EauxDeMarseilleApiError):
            await client.check_contract()
