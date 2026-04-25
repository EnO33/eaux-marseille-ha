"""Sensor entity descriptions for the Eaux de Marseille integration.

Kept in a dedicated module so :mod:`.sensor` only contains the platform
plumbing (entity class + ``async_setup_entry``); the data — which is
the bulk — lives here and is straightforward to scan.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfVolume

from .models import ConsumptionData


@dataclass(frozen=True, kw_only=True)
class EauxDeMarseilleEntityDescription(SensorEntityDescription):
    """Describes an Eaux de Marseille sensor."""

    value_fn: Callable[[ConsumptionData], float | int | str | None]


def _water_total(
    key: str,
    unit: str,
    precision: int,
    value_fn: Callable[[ConsumptionData], float | int | None],
) -> EauxDeMarseilleEntityDescription:
    """Helper: a totals-class water sensor (m³ or L)."""
    return EauxDeMarseilleEntityDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=unit,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=precision,
        value_fn=value_fn,
    )


SENSOR_DESCRIPTIONS: tuple[EauxDeMarseilleEntityDescription, ...] = (
    _water_total("current_month_m3", UnitOfVolume.CUBIC_METERS, 3, lambda d: d.current_month_m3),
    _water_total("current_month_litres", UnitOfVolume.LITERS, 0, lambda d: d.current_month_litres),
    _water_total("current_year_m3", UnitOfVolume.CUBIC_METERS, 3, lambda d: d.current_year_m3),
    EauxDeMarseilleEntityDescription(
        key="index_m3",
        translation_key="index_m3",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda d: d.index_m3,
    ),
    EauxDeMarseilleEntityDescription(
        key="daily_average_m3",
        translation_key="daily_average_m3",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=4,
        value_fn=lambda d: d.daily_average_m3,
    ),
    _water_total("last_reading_m3", UnitOfVolume.CUBIC_METERS, 0, lambda d: d.last_reading_m3),
    _water_total("last_reading_litres", UnitOfVolume.LITERS, 0, lambda d: d.last_reading_litres),
    EauxDeMarseilleEntityDescription(
        key="last_reading_date",
        translation_key="last_reading_date",
        value_fn=lambda d: d.last_reading_date,
    ),
    EauxDeMarseilleEntityDescription(
        key="last_reading_days",
        translation_key="last_reading_days",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.last_reading_days,
    ),
    _water_total(
        "previous_reading_m3", UnitOfVolume.CUBIC_METERS, 0, lambda d: d.previous_reading_m3
    ),
    EauxDeMarseilleEntityDescription(
        key="previous_reading_date",
        translation_key="previous_reading_date",
        value_fn=lambda d: d.previous_reading_date,
    ),
    EauxDeMarseilleEntityDescription(
        key="total_readings",
        translation_key="total_readings",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.total_readings,
    ),
)
