"""The meter behind a contract, from the portal's subscription detail (#128).

``GET /Abonnement/detailAbonnement/{contract}`` describes the contract's
meter. Only the fields the device registry shows are kept: the physical
meter number and how the meter is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MeterInfo:
    """Physical meter number and reading type, each ``None`` when the portal omits it."""

    serial_number: str | None = None
    reading_type: str | None = None

    @classmethod
    def from_api_response(cls, detail: dict[str, Any]) -> MeterInfo:
        """Build a :class:`MeterInfo` from a ``detailAbonnement`` payload."""
        return cls(
            serial_number=_text(detail.get("numeroPhysiqueAppareil")),
            reading_type=_text(detail.get("libelleTypeReleve")),
        )


def _text(value: Any) -> str | None:
    """``value`` as a stripped string; ``None`` for a blank or non-scalar value."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    return str(value).strip() or None
