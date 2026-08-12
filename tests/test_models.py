"""Unit tests for ``ConsumptionData.from_api_responses``.

The builder is a pure function over the portal payloads, so these run
without any HTTP mocking or a Home Assistant install.
"""

from __future__ import annotations

from custom_components.eaux_marseille.models import ConsumptionData


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
