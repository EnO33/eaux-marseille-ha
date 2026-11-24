"""The contract's meter, read once per setup for the device info (#128)."""

from __future__ import annotations

from typing import Any

import pytest
from aioresponses import aioresponses

from custom_components.eaux_marseille.api import EauxDeMarseilleClient, MeterInfo

from .portal import (
    DETAIL_RE,
    DETAIL_URL,
    HARD_400,
    LOGIN_URL,
    count_requests,
    register_token,
)

# Trimmed from a SEM capture: only the fields we read, plus neighbours.
DETAIL_PAYLOAD = {
    "numeroPhysiqueAppareil": "I21LA123456",
    "libelleTypeReleve": "TELE-RELEVEE",
    "libelleTypeAppareil": "COMPTEUR",
    "typeContrat": {"libelle": "ORDINAIRE"},
}
EXPECTED_METER = MeterInfo(serial_number="I21LA123456", reading_type="TELE-RELEVEE")


class TestParse:
    def test_reads_serial_and_reading_type(self) -> None:
        assert MeterInfo.from_api_response(DETAIL_PAYLOAD) == EXPECTED_METER

    def test_numeric_serial_is_kept_as_text(self) -> None:
        meter = MeterInfo.from_api_response({"numeroPhysiqueAppareil": 987654})
        assert meter.serial_number == "987654"

    @pytest.mark.parametrize(
        "detail",
        [
            {},
            {"numeroPhysiqueAppareil": None, "libelleTypeReleve": None},
            {"numeroPhysiqueAppareil": "  ", "libelleTypeReleve": ""},
            {"numeroPhysiqueAppareil": True, "libelleTypeReleve": {"code": "TR"}},
        ],
        ids=["empty", "nulls", "blanks", "wrong-types"],
    )
    def test_unusable_fields_are_unknown(self, detail: dict[str, Any]) -> None:
        assert MeterInfo.from_api_response(detail) == MeterInfo()


class TestFetch:
    async def test_returns_the_meter(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        mock_auth.get(DETAIL_URL, payload=DETAIL_PAYLOAD)
        assert await client.fetch_meter_info() == EXPECTED_METER

    @pytest.mark.parametrize(
        "response",
        [
            {"status": 404},
            HARD_400,
            {"status": 200, "body": "<html></html>", "content_type": "text/html"},
        ],
        ids=["not-found", "bad-request", "not-json"],
    )
    async def test_unavailable_detail_is_unknown(
        self,
        client: EauxDeMarseilleClient,
        mock_auth: aioresponses,
        response: dict[str, Any],
    ) -> None:
        mock_auth.get(DETAIL_URL, **response)
        assert await client.fetch_meter_info() == MeterInfo()

    async def test_server_error_is_not_retried(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """A cosmetic field is not worth holding the setup through the retries."""
        mock_auth.get(DETAIL_URL, status=500, repeat=True)
        assert await client.fetch_meter_info() == MeterInfo()
        assert count_requests(mock_auth, DETAIL_RE) == 1

    async def test_failed_login_is_unknown(self, client: EauxDeMarseilleClient) -> None:
        """A login failure must not block the setup: the device just lacks its meter."""
        with aioresponses() as m:
            register_token(m)
            m.post(LOGIN_URL, status=401)
            assert await client.fetch_meter_info() == MeterInfo()
            assert count_requests(m, DETAIL_RE) == 0
