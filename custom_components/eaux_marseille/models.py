"""Data models for the Eaux de Marseille API.

Pure data + a few module-level helpers (:meth:`ConsumptionData.from_api_responses`
and :func:`encode_context_cookie`) that turn the portal's payloads into
the integration's internal representation. Keeping these as
classmethods/free functions (instead of inside the HTTP client) makes
them trivially testable without any HTTP mocking.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ConsumptionData:
    """Aggregated consumption snapshot returned by the customer portal.

    All fields are optional except :attr:`total_readings` because the
    portal can return partial data on a freshly activated contract or
    just after a meter swap.
    """

    index_m3: float | None
    index_precise_m3: float | None
    last_reading_m3: float | None
    last_reading_litres: int | None
    last_reading_date: str | None
    last_reading_days: int | None
    daily_average_m3: float | None
    previous_reading_m3: float | None
    previous_reading_date: str | None
    current_month_m3: float | None
    current_month_litres: int | None
    current_year_m3: float | None
    total_readings: int

    @classmethod
    def from_api_responses(
        cls,
        last_billed: dict[str, Any],
        monthly: dict[str, Any],
        history: dict[str, Any],
        daily_entries: list[dict[str, Any]] | None = None,
    ) -> ConsumptionData:
        """Build a :class:`ConsumptionData` from the endpoint payloads.

        Defensive: every field is read with :py:meth:`dict.get`, so a
        missing key in the upstream payload yields ``None`` rather than
        crashing the integration.

        :param last_billed: Payload from
            ``/TableauDeBord/derniereConsommationFacturee/{contract}``.
        :param monthly: Payload from
            ``/Consommation/listeConsommationsInstanceAlerteChart/.../MOIS/true``.
        :param history: Payload from
            ``/Facturation/listeConsommationsFacturees/{contract}``. Its
            newest entry backfills the last reading when the dashboard
            endpoint returns only a date (seen on some Vivaigo contracts).
        :param daily_entries: Optional ``consommations`` list from the same
            chart endpoint with ``JOURNEE`` granularity — only present on
            contracts with daily telemetry. Its most recent entry carries a
            fresher meter index (typically D-1) than the monthly chart, so
            it is preferred for :attr:`index_precise_m3`. The portal serves
            this series newest-first, hence the explicit max-by-date below
            instead of relying on list position.
        """
        # The history endpoint is served newest-first: resultats[0] is the
        # latest reading, resultats[1] the previous one.
        readings = history.get("resultats") or []
        latest = readings[0] if readings else {}
        previous = readings[1] if len(readings) > 1 else {}

        # The dashboard endpoint is authoritative for the last reading, but on
        # some contracts it returns only a date (null volume/index). The
        # matching history entry carries the full record, so layer the
        # dashboard's *present* values over it — backfilling the gaps without
        # letting missing fields wipe a figure the dashboard did provide.
        last_reading = {**latest, **{k: v for k, v in last_billed.items() if v is not None}}
        index_m3 = last_reading.get("valeurIndex")

        monthly_entries = monthly.get("consommations", [])
        # The monthly chart comes back chronological, so the last entry is
        # the current month.
        current_month = monthly_entries[-1] if monthly_entries else {}
        year_total = round(
            sum(entry.get("volumeConsoEnM3", 0.0) for entry in monthly_entries),
            3,
        )

        latest_daily = _latest_by_date(daily_entries or [])
        index_precise = _litres_to_m3(latest_daily.get("valeurIndex"))
        if index_precise is None:
            index_precise = _litres_to_m3(current_month.get("valeurIndex"))
        if index_precise is None:
            # No chart series for this contract; fall back to the coarser
            # billed index, which is already in m³ (no litre conversion).
            index_precise = index_m3

        return cls(
            index_m3=index_m3,
            index_precise_m3=index_precise,
            last_reading_m3=last_reading.get("volumeConsoEnM3"),
            last_reading_litres=last_reading.get("volumeConsoEnLitres"),
            last_reading_date=_iso_date_prefix(last_reading.get("dateReleve")),
            last_reading_days=last_reading.get("nbJours"),
            daily_average_m3=round(last_reading.get("moyenne") or 0.0, 4),
            previous_reading_m3=previous.get("volumeConsoEnM3"),
            previous_reading_date=_iso_date_prefix(previous.get("dateReleve")),
            current_month_m3=current_month.get("volumeConsoEnM3"),
            current_month_litres=current_month.get("volumeConsoEnLitres"),
            current_year_m3=year_total,
            total_readings=history.get("nbTotalResultats", 0),
        )


def _iso_date_prefix(value: str | None) -> str | None:
    """Return the ``YYYY-MM-DD`` prefix of an ISO datetime, or ``None``."""
    return value[:10] if value else None


def _latest_by_date(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the entry with the most recent ``dateReleve``, or ``{}``.

    Used for the daily (``JOURNEE``) series, which the portal serves
    newest-first — so the freshest reading is *not* the last list item.
    ISO 8601 strings with a fixed UTC offset sort lexicographically,
    which is all we need to find the newest day.
    """
    if not entries:
        return {}
    return max(entries, key=lambda entry: entry.get("dateReleve", ""))


def _litres_to_m3(litres: float | int | None) -> float | None:
    """Convert a litre index to m³, rounded to the litre (3 decimals).

    The monthly-chart endpoint reports ``valeurIndex`` in **litres**
    (e.g. ``212982``), unlike the billed endpoint whose ``valeurIndex``
    is already in m³. This is the precise, frequently-updated meter
    index — exposed as a ``total_increasing`` sensor so Home Assistant
    derives daily/monthly consumption from its deltas.
    """
    if litres is None:
        return None
    return round(float(litres) / 1000, 3)


def encode_context_cookie(
    contract: dict[str, Any],
    user_info: dict[str, Any],
    ael_token: str,
) -> str:
    """Build the value of the portal's ``AEL_CONTEXT`` cookie.

    The portal's JS bundle stores a JSON blob (the same shape it
    produces with ``JSON.stringify`` on the user/context), URL-encoded.
    """
    # Defensive: ``.get(key) or ''`` handles both missing keys and explicit
    # ``None`` values. ``str(dict)`` was previously used here but produced
    # invalid JSON for any value containing apostrophes (and serialised
    # ``None`` as the literal ``"None"``).
    prenom = user_info.get("prenom") or ""
    nom = user_info.get("nom") or ""
    context = {
        "type": "contrat",
        "object": contract,
        "user": {
            "identifiant": user_info["identifiant"],
            "nomComplet": f"{prenom} {nom}".strip(),
            "nom": nom,
            "prenom": prenom,
            "email": user_info.get("email") or "",
            "titre": user_info.get("titre") or "",
            "tokenAuthentique": ael_token,
            "userWebId": user_info.get("userWebId"),
            "meta": user_info.get("meta") or {},
            "profils": user_info.get("profils") or [],
        },
    }
    return urllib.parse.quote_plus(json.dumps(context, ensure_ascii=False))
