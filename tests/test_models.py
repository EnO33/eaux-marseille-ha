"""Unit tests for ``ConsumptionData.from_api_responses`` and the context cookie.

Both are pure functions over the portal payloads, so these run
without any HTTP mocking or a Home Assistant install.
"""

from __future__ import annotations

import json
import urllib.parse

from custom_components.eaux_marseille.models import ConsumptionData, encode_context_cookie


def test_last_reading_backfilled_from_history_when_dashboard_incomplete() -> None:
    """The dashboard endpoint sometimes returns only the reading date (seen
    on some Vivaigo contracts); the last reading is then filled from the
    newest history entry, which carries the full record."""
    last_billed = {"dateReleve": "2026-06-17T00:00:00+02:00"}  # date only
    history = {
        "nbTotalResultats": 11,
        "resultats": [
            {
                "dateReleve": "2026-06-17T00:00:00+02:00",
                "volumeConsoEnM3": 26.0,
                "volumeConsoEnLitres": 26000,
                "valeurIndex": 220.0,
            },
            {"dateReleve": "2026-05-18T00:00:00+02:00", "volumeConsoEnM3": 48.0},
        ],
    }

    data = ConsumptionData.from_api_responses(last_billed, {}, history)

    assert data.last_reading_date == "2026-06-17"
    assert data.last_reading_m3 == 26.0
    assert data.last_reading_litres == 26000
    assert data.index_m3 == 220.0
    # No chart series for this contract, so the precise index falls back to
    # the (coarser) billed index — already in m³, so no litre conversion.
    assert data.index_precise_m3 == 220.0
    # The previous reading still comes from resultats[1].
    assert data.previous_reading_m3 == 48.0
    assert data.previous_reading_date == "2026-05-18"


def test_dashboard_values_take_precedence_over_history() -> None:
    """A figure the dashboard *did* provide is never overwritten by the
    history entry — the dashboard stays authoritative."""
    last_billed = {
        "dateReleve": "2026-06-17T00:00:00+02:00",
        "volumeConsoEnM3": 26.0,
        "valeurIndex": 220.0,
    }
    history = {
        "resultats": [
            {
                "dateReleve": "2026-06-17T00:00:00+02:00",
                "volumeConsoEnM3": 999.0,
                "valeurIndex": 999.0,
            },
        ],
    }

    data = ConsumptionData.from_api_responses(last_billed, {}, history)

    assert data.last_reading_m3 == 26.0
    assert data.index_m3 == 220.0


def test_zero_dashboard_figure_is_kept_not_backfilled() -> None:
    """0 m³ is a valid reading and must not be mistaken for a missing value
    (guards against an ``or``-style coalesce that would backfill it)."""
    last_billed = {"dateReleve": "2026-06-17T00:00:00+02:00", "volumeConsoEnM3": 0.0}
    history = {
        "resultats": [
            {"dateReleve": "2026-06-17T00:00:00+02:00", "volumeConsoEnM3": 5.0},
        ],
    }

    data = ConsumptionData.from_api_responses(last_billed, {}, history)

    assert data.last_reading_m3 == 0.0


def test_missing_series_and_average_are_unknown_not_zero() -> None:
    """An empty monthly chart and a missing ``moyenne`` yield ``None``
    (``unknown`` in HA), not a 0 that pollutes long-term statistics."""
    data = ConsumptionData.from_api_responses({}, {"consommations": []}, {})

    assert data.current_year_m3 is None
    assert data.daily_average_m3 is None


def test_reported_zeros_are_kept() -> None:
    """A portal that really reports 0 still produces 0, not ``None``."""
    data = ConsumptionData.from_api_responses(
        {"moyenne": 0.0},
        {"consommations": [{"volumeConsoEnM3": 0.0}, {}]},
        {},
    )

    assert data.current_year_m3 == 0.0
    assert data.daily_average_m3 == 0.0


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
