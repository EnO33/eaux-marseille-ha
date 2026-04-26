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
    ) -> ConsumptionData:
        """Build a :class:`ConsumptionData` from the three endpoint payloads.

        Defensive: every field is read with :py:meth:`dict.get`, so a
        missing key in the upstream payload yields ``None`` rather than
        crashing the integration.

        :param last_billed: Payload from
            ``/TableauDeBord/derniereConsommationFacturee/{contract}``.
        :param monthly: Payload from
            ``/Consommation/listeConsommationsInstanceAlerteChart/...``.
        :param history: Payload from
            ``/Facturation/listeConsommationsFacturees/{contract}``.
        """
        readings = history.get("resultats", [])
        previous = readings[1] if len(readings) > 1 else {}

        monthly_entries = monthly.get("consommations", [])
        current_month = monthly_entries[-1] if monthly_entries else {}
        year_total = round(
            sum(entry.get("volumeConsoEnM3", 0.0) for entry in monthly_entries),
            3,
        )

        return cls(
            index_m3=last_billed.get("valeurIndex"),
            last_reading_m3=last_billed.get("volumeConsoEnM3"),
            last_reading_litres=last_billed.get("volumeConsoEnLitres"),
            last_reading_date=_iso_date_prefix(last_billed.get("dateReleve")),
            last_reading_days=last_billed.get("nbJours"),
            daily_average_m3=round(last_billed.get("moyenne", 0.0), 4),
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
