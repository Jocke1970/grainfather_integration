from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    GrainfatherFermentationDevice,
    GrainfatherSnapshot,
    brew_session_device_identifier,
)
from .const import DOMAIN
from .coordinator import GrainfatherDataUpdateCoordinator


@dataclass(frozen=True, slots=True)
class GrainfatherLiveBinaryDescription:
    key: str
    name: str
    entity_category: EntityCategory | None = None


LIVE_BINARY_SENSORS: tuple[GrainfatherLiveBinaryDescription, ...] = (
    GrainfatherLiveBinaryDescription(
        key="controller_online",
        name="Controller Online",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    GrainfatherLiveBinaryDescription(key="heating", name="Heating"),
    GrainfatherLiveBinaryDescription(key="cooling", name="Cooling"),
    GrainfatherLiveBinaryDescription(key="control_active", name="Control Active"),
    GrainfatherLiveBinaryDescription(
        key="managed_mode",
        name="Managed Mode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    GrainfatherLiveBinaryDescription(
        key="lower_temp_alert_enabled",
        name="Lower Temperature Alert Enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    GrainfatherLiveBinaryDescription(
        key="controller_ota_available",
        name="OTA Update Available",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GrainfatherDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_unique_ids: set[str] = set()

    entities = _build_binary_entities(coordinator, entry, known_unique_ids)
    async_add_entities(entities)

    def _async_handle_coordinator_update() -> None:
        new_entities = _build_binary_entities(coordinator, entry, known_unique_ids)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        coordinator.async_add_listener(_async_handle_coordinator_update)
    )


def _build_binary_entities(
    coordinator: GrainfatherDataUpdateCoordinator,
    entry: ConfigEntry,
    known_unique_ids: set[str],
) -> list[BinarySensorEntity]:
    entities: list[BinarySensorEntity] = []
    for device in coordinator.data.fermentation_devices:
        if device.device_id is None or not device.esp_chip_id:
            continue
        for description in LIVE_BINARY_SENSORS:
            unique_id = (
                f"{entry.entry_id}_fermdevice_{device.device_id}_"
                f"{description.key}"
            )
            if unique_id in known_unique_ids:
                continue
            known_unique_ids.add(unique_id)
            entities.append(
                GrainfatherFermDeviceLiveBinarySensor(
                    coordinator,
                    entry,
                    device.device_id,
                    description,
                )
            )
    return entities


def _ferm_device_info(
    device: GrainfatherFermentationDevice,
    snapshot: GrainfatherSnapshot,
) -> DeviceInfo:
    kwargs: dict[str, Any] = {
        "identifiers": {(DOMAIN, f"fermdevice_{device.device_id}")},
        "name": device.name or f"Fermentation Device {device.device_id}",
        "manufacturer": "fidley",
        "model": "Fermentation Device",
        "entry_type": DeviceEntryType.SERVICE,
    }
    linked_session = next(
        (
            session
            for session in snapshot.brew_sessions
            if device.linked_brew_session_id is not None
            and str(session.batch_id) == str(device.linked_brew_session_id)
        ),
        None,
    )
    if linked_session is not None:
        kwargs["via_device"] = (DOMAIN, brew_session_device_identifier(linked_session))
    return DeviceInfo(**kwargs)


class GrainfatherFermDeviceLiveBinarySensor(
    CoordinatorEntity[GrainfatherDataUpdateCoordinator],
    BinarySensorEntity,
):
    def __init__(
        self,
        coordinator: GrainfatherDataUpdateCoordinator,
        entry: ConfigEntry,
        device_id: int,
        description: GrainfatherLiveBinaryDescription,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._key = description.key
        self._attr_name = description.name
        self._attr_has_entity_name = True
        self._attr_entity_category = description.entity_category
        self._attr_unique_id = (
            f"{entry.entry_id}_fermdevice_{device_id}_{description.key}"
        )

    @property
    def _device(self) -> GrainfatherFermentationDevice | None:
        return next(
            (
                device
                for device in self.coordinator.data.fermentation_devices
                if device.device_id == self._device_id
            ),
            None,
        )

    @property
    def available(self) -> bool:
        device = self._device
        return bool(device and device.esp_chip_id)

    @property
    def device_info(self) -> DeviceInfo | None:
        device = self._device
        if device is None:
            return None
        return _ferm_device_info(device, self.coordinator.data)

    @property
    def is_on(self) -> bool | None:
        device = self._device
        if device is None or not device.esp_chip_id:
            return None
        live = self.coordinator.esp_runtime.get(device.esp_chip_id)
        if live is None:
            return None

        event = live.event
        meta = live.meta

        if self._key == "controller_online":
            return live.device_online
        if self._key == "heating":
            return event.heating if event else None
        if self._key == "cooling":
            return event.cooling if event else None
        if self._key == "control_active":
            return event.control_active if event else None
        if self._key == "managed_mode":
            return event.managed_mode if event else None
        if self._key == "lower_temp_alert_enabled":
            return event.lower_temp_alert_enabled if event else None
        if self._key == "controller_ota_available":
            return meta.ota_available if meta else None
        return None
