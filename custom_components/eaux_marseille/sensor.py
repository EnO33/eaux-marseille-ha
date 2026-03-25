"""Sensor platform for Eaux de Marseille."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ConsumptionData
from .const import CONF_CONTRACT_ID, DOMAIN, ENTRY_COORDINATOR
from .coordinator import EauxDeMarseilleCoordinator


@dataclass(frozen=True, kw_only=True)
class EauxDeMarseilleEntityDescription(SensorEntityDescription):
    """Describes an Eaux de Marseille sensor."""

    value_fn: Callable[[ConsumptionData], float | int | str | None]


SENSOR_DESCRIPTIONS: tuple[EauxDeMarseilleEntityDescription, ...] = (
    EauxDeMarseilleEntityDescription(
        key="current_month_m3",
        translation_key="current_month_m3",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=3,
        value_fn=lambda d: d.current_month_m3,
    ),
    EauxDeMarseilleEntityDescription(
        key="current_month_litres",
        translation_key="current_month_litres",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        value_fn=lambda d: d.current_month_litres,
    ),
    EauxDeMarseilleEntityDescription(
        key="current_year_m3",
        translation_key="current_year_m3",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=3,
        value_fn=lambda d: d.current_year_m3,
    ),
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
    EauxDeMarseilleEntityDescription(
        key="last_reading_m3",
        translation_key="last_reading_m3",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        value_fn=lambda d: d.last_reading_m3,
    ),
    EauxDeMarseilleEntityDescription(
        key="last_reading_litres",
        translation_key="last_reading_litres",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        value_fn=lambda d: d.last_reading_litres,
    ),
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
    EauxDeMarseilleEntityDescription(
        key="previous_reading_m3",
        translation_key="previous_reading_m3",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        value_fn=lambda d: d.previous_reading_m3,
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eaux de Marseille sensors from a config entry."""
    coordinator: EauxDeMarseilleCoordinator = hass.data[DOMAIN][entry.entry_id][ENTRY_COORDINATOR]
    contract_id: str = entry.data[CONF_CONTRACT_ID]

    async_add_entities(
        EauxDeMarseilleeSensor(coordinator, description, contract_id)
        for description in SENSOR_DESCRIPTIONS
    )


class EauxDeMarseilleeSensor(CoordinatorEntity[EauxDeMarseilleCoordinator], SensorEntity):
    """Representation of an Eaux de Marseille sensor."""

    entity_description: EauxDeMarseilleEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EauxDeMarseilleCoordinator,
        description: EauxDeMarseilleEntityDescription,
        contract_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{contract_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, contract_id)},
            name=f"Eaux de Marseille — Contrat {contract_id}",
            manufacturer="Société des Eaux de Marseille",
            model="Compteur télérelevé",
            configuration_url="https://espaceclients.eauxdemarseille.fr",
        )

    @property
    def native_value(self) -> float | int | str | None:
        return self.entity_description.value_fn(self.coordinator.data)
