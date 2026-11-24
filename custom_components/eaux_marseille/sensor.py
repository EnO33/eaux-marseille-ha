"""Sensor platform for Eaux de Marseille.

Entity descriptions live in :mod:`._sensor_descriptions`; this module
only contains the platform plumbing (entity class + setup hook).
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EauxDeMarseilleConfigEntry
from ._sensor_descriptions import SENSOR_DESCRIPTIONS, EauxDeMarseilleEntityDescription
from .const import (
    CONF_CONTRACT_ID,
    CONF_PROVIDER,
    DEFAULT_METER_MODEL,
    DEFAULT_PROVIDER,
    DOMAIN,
    METER_MODELS,
    PROVIDER_MANUFACTURER,
    PROVIDERS,
    Provider,
)
from .coordinator import EauxDeMarseilleCoordinator

# All sensors share a single coordinator that already serialises requests,
# so HA does not need to add an extra concurrency cap on top.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EauxDeMarseilleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eaux de Marseille sensors from a config entry."""
    contract_id: str = entry.data[CONF_CONTRACT_ID]
    # Entries created before 1.9.0 don't store the provider; default to SEM.
    provider = Provider(entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER.value))
    meter = entry.runtime_data.meter
    device_info = DeviceInfo(
        identifiers={(DOMAIN, contract_id)},
        translation_key="contract",
        translation_placeholders={"contract_id": contract_id},
        manufacturer=PROVIDER_MANUFACTURER[provider],
        # Set even when unknown, so the model of an older release is cleared.
        model=_meter_model(meter.reading_type),
        serial_number=meter.serial_number,
        configuration_url=PROVIDERS[provider].url,
    )
    async_add_entities(
        EauxDeMarseilleSensor(entry.runtime_data.coordinator, description, contract_id, device_info)
        for description in SENSOR_DESCRIPTIONS
    )


def _meter_model(reading_type: str | None) -> str | None:
    """The device model for the portal's reading type; ``None`` when unknown."""
    if reading_type is None:
        return None
    return METER_MODELS.get(reading_type.upper(), DEFAULT_METER_MODEL)


class EauxDeMarseilleSensor(CoordinatorEntity[EauxDeMarseilleCoordinator], SensorEntity):
    """Representation of an Eaux de Marseille sensor."""

    entity_description: EauxDeMarseilleEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EauxDeMarseilleCoordinator,
        description: EauxDeMarseilleEntityDescription,
        contract_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{contract_id}_{description.key}"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float | int | str | None:
        return self.entity_description.value_fn(self.coordinator.data)
