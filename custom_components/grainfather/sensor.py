from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    GrainfatherHistoryPoint,
    GrainfatherBrewSession,
    GrainfatherFermentationDevice,
    GrainfatherSnapshot,
    brew_session_device_identifier,
    brew_session_display_name,
    brew_session_unique_fragment,
)
from .const import BREW_SESSION_STATUS_NAME_BY_CODE, DOMAIN
from .const import CONF_DEFAULT_DENSITY_UNIT, DEFAULT_DENSITY_UNIT
from .coordinator import GrainfatherDataUpdateCoordinator

_MAX_EXPOSED_BATCH_HISTORY_POINTS = 20
_MAX_EXPOSED_DEVICE_HISTORY_POINTS = 5
_MAX_EXPOSED_NOTES_CHARS = 400


@dataclass(frozen=True, kw_only=True)
class GrainfatherSessionSensorDescription(SensorEntityDescription):
    value_fn: Callable[[GrainfatherBrewSession], Any]
    attributes_fn: Callable[[GrainfatherBrewSession, GrainfatherSnapshot], dict[str, Any] | None] | None = None


def _calc_abv(og: float | None, fg: float | None) -> float | None:
    if og is None or fg is None:
        return None
    return round((og - fg) * 131.25, 2)


SESSION_SENSORS: tuple[GrainfatherSessionSensorDescription, ...] = (
    GrainfatherSessionSensorDescription(
        key="batch_number",
        translation_key="session_batch_number",
        value_fn=lambda s: s.batch_number,
        attributes_fn=lambda s, snapshot: _session_batch_number_attributes(s, snapshot),
    ),
    GrainfatherSessionSensorDescription(
        key="abv",
        translation_key="session_abv",
        native_unit_of_measurement="%vol",
        suggested_display_precision=1,
        value_fn=lambda s: _calc_abv(s.original_gravity, s.final_gravity),
    ),
    GrainfatherSessionSensorDescription(
        key="style",
        translation_key="session_style",
        value_fn=lambda s: s.style_name,
    ),
    GrainfatherSessionSensorDescription(
        key="original_gravity",
        translation_key="session_original_gravity",
        suggested_display_precision=4,
        value_fn=lambda s: s.original_gravity,
    ),
    GrainfatherSessionSensorDescription(
        key="final_gravity",
        translation_key="session_final_gravity",
        suggested_display_precision=4,
        value_fn=lambda s: s.final_gravity,
    ),
    GrainfatherSessionSensorDescription(
        key="batch_variant_name",
        translation_key="session_batch_variant_name",
        value_fn=lambda s: s.batch_variant_name,
    ),
    GrainfatherSessionSensorDescription(
        key="recipe_image_url",
        translation_key="session_recipe_image_url",
        value_fn=lambda s: s.recipe_image_url,
    ),
)


def _serialize_history_points(
    points: tuple[GrainfatherHistoryPoint, ...],
    max_points: int,
) -> list[dict[str, Any]]:
    # Keep attributes reasonably small for Home Assistant state storage.
    recent_points = points[-max_points:]
    return [
        {
            "timestamp": point.timestamp,
            "temperature": point.temperature,
            "target_temperature": point.target_temperature,
            "specific_gravity": point.specific_gravity,
        }
        for point in recent_points
    ]


def _truncate_text(value: str | None, max_chars: int) -> str | None:
    if value is None:
        return None
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}..."


def _last_history_value(
    points: tuple[GrainfatherHistoryPoint, ...],
    attr_name: str,
) -> float | None:
    for point in reversed(points):
        value = getattr(point, attr_name, None)
        if value is not None:
            return value
    return None


def _gravity_fallback(
    device: GrainfatherFermentationDevice,
    snapshot: GrainfatherSnapshot,
) -> float | None:
    """Fallback chain for gravity: linked session final_gravity → linked devices."""
    if device.last_specific_gravity is not None:
        return device.last_specific_gravity

    history = snapshot.fermentation_history_by_device_id.get(
        device.device_id or -1,
        tuple(),
    )
    history_gravity = _last_history_value(history, "specific_gravity")
    if history_gravity is not None:
        return history_gravity

    if device.linked_brew_session_id is not None:
        linked_session = next(
            (
                s
                for s in snapshot.brew_sessions
                if str(s.batch_id) == str(device.linked_brew_session_id)
            ),
            None,
        )
        if linked_session is not None and linked_session.final_gravity is not None:
            return linked_session.final_gravity

        other_device_gravity = next(
            (
                d.last_specific_gravity
                for d in snapshot.fermentation_devices
                if d.device_id != device.device_id
                and str(d.linked_brew_session_id) == str(device.linked_brew_session_id)
                and d.last_specific_gravity is not None
            ),
            None,
        )
        if other_device_gravity is not None:
            return other_device_gravity

    return None


def _get_collaborating_devices(
    device: GrainfatherFermentationDevice,
    snapshot: GrainfatherSnapshot,
) -> list[dict[str, Any]]:
    """Find other fermentation devices for the same session that provide data."""
    collaborators = []
    if device.linked_brew_session_id is None:
        return collaborators

    for other in snapshot.fermentation_devices:
        if (
            other.device_id == device.device_id
            or str(other.linked_brew_session_id) != str(device.linked_brew_session_id)
        ):
            continue

        has_data = (
            other.last_temperature is not None or other.last_specific_gravity is not None
        )
        if not has_data:
            history = snapshot.fermentation_history_by_device_id.get(
                other.device_id or -1,
                tuple(),
            )
            has_data = len(history) > 0

        if has_data:
            collaborators.append(
                {
                    "device_id": other.device_id,
                    "name": other.name or f"Fermentation Device {other.device_id}",
                }
            )

    return collaborators


def _controller_runtime_metadata(
    device: GrainfatherFermentationDevice,
    coordinator: GrainfatherDataUpdateCoordinator,
) -> dict[str, Any]:
    """Describe controller transport and live MQTT telemetry observations."""
    snapshot = coordinator.data
    if device.esp_chip_id:
        accessory = next(
            (
                item
                for item in snapshot.accessory_devices
                if item.chip_id
                and item.chip_id.casefold() == device.esp_chip_id.casefold()
            ),
            None,
        )
        live = coordinator.esp_runtime.get(device.esp_chip_id)
        event = live.event if live else None
        meta = live.meta if live else None
        return {
            "controller_transport": "esp_mqtt",
            "esp_chip_id": device.esp_chip_id,
            "particle_device_id": device.particle_device_id,
            "accessory_device_discovered": accessory is not None,
            "accessory_device_id": accessory.accessory_id if accessory else None,
            "accessory_device_type_id": accessory.device_type_id if accessory else None,
            "accessory_device_name": accessory.name if accessory else None,
            "live_mqtt_transport_connected": (
                live.broker_connected if live else False
            ),
            "live_mqtt_subscribed": live.mqtt_subscribed if live else False,
            "live_mqtt_subscription_codes": (
                list(live.subscription_codes)
                if live and live.subscription_codes is not None
                else None
            ),
            "live_mqtt_connected": (
                bool(live and live.broker_connected and live.mqtt_subscribed)
            ),
            "live_mqtt_broker": live.broker if live else None,
            "controller_online": live.device_online if live else None,
            "live_mqtt_last_message_at": (
                live.last_message_at.isoformat()
                if live and live.last_message_at
                else None
            ),
            "live_mqtt_last_topic": live.last_topic if live else None,
            "live_mqtt_last_error": (
                live.last_connection_error if live else None
            ),
            "live_mqtt_telemetry_keepalive_last_sent_at": (
                live.telemetry_keepalive_last_sent_at.isoformat()
                if live and live.telemetry_keepalive_last_sent_at
                else None
            ),
            "live_mqtt_telemetry_keepalive_seconds": (
                live.telemetry_keepalive_seconds if live else None
            ),
            "live_mqtt_telemetry_keepalive_count": (
                live.telemetry_keepalive_count if live else 0
            ),
            "live_temperature": event.temperature if event else None,
            "live_target_temperature": event.target_temperature if event else None,
            "live_mqtt_event_subscription_time": (
                event.subscription_time if event else None
            ),
            "heating": event.heating if event else None,
            "cooling": event.cooling if event else None,
            "controller_rssi": event.rssi if event else (meta.rssi if meta else None),
            "control_mode": event.control_mode if event else None,
            "control_active": event.control_active if event else None,
            "controller_units": event.units if event else None,
            "controller_hysteresis": event.hysteresis if event else None,
            "controller_temperature_offset": event.temperature_offset if event else None,
            "session_id": event.session_id if event else None,
            "managed_mode": event.managed_mode if event else None,
            "session_stage": event.session_stage if event else None,
            "stage_end_time": event.stage_end_time if event else None,
            "firmware_version": meta.firmware_version if meta else None,
            "controller_error_code": meta.error_code if meta else None,
            "controller_ota_available": meta.ota_available if meta else None,
            "controller_ota_status": meta.ota_status if meta else None,
        }

    if device.particle_device_id:
        return {
            "controller_transport": "particle",
            "esp_chip_id": None,
            "particle_device_id": device.particle_device_id,
            "accessory_device_discovered": False,
            "live_mqtt_connected": False,
        }

    return {
        "controller_transport": "unknown",
        "esp_chip_id": None,
        "particle_device_id": None,
        "accessory_device_discovered": False,
        "live_mqtt_connected": False,
    }


def _session_batch_number_attributes(
    session: GrainfatherBrewSession,
    snapshot: GrainfatherSnapshot,
) -> dict[str, Any]:
    history: tuple[GrainfatherHistoryPoint, ...] = tuple()
    batch_id_int = None
    if session.batch_id is not None:
        try:
            batch_id_int = int(session.batch_id)
        except (TypeError, ValueError):
            batch_id_int = None

    if batch_id_int is not None:
        history = snapshot.brew_session_history_by_batch_id.get(batch_id_int, tuple())

    return {
        "grainfather_entity_type": "brew_session",
        "batch_number": session.batch_number if session.batch_number is not None else 0,
        "batch_variant_name": session.batch_variant_name,
        "status": BREW_SESSION_STATUS_NAME_BY_CODE.get(session.status or -1, "unknown"),
        "brew_session_id": session.batch_id,
        "recipe_id": session.recipe_id,
        "session_name": session.session_name,
        "recipe_name": session.recipe_name,
        "condition_date": session.condition_date,
        "fermentation_start_date": session.fermentation_start_date,
        "created_at": session.created_at,
        "recipe_image_url": session.recipe_image_url,
        "notes": _truncate_text(session.notes, _MAX_EXPOSED_NOTES_CHARS),
        "equipment_name": session.equipment_name,
        "fermentation_device_ids": list(session.fermentation_device_ids),
        "fermentation_steps": [
            {
                "index": i,
                "name": step.name,
                "temperature": step.temperature,
                "duration_minutes": step.duration,
                "is_ramp_step": step.is_ramp_step,
            }
            for i, step in enumerate(session.fermentation_steps)
        ],
        "history_points": _serialize_history_points(history, _MAX_EXPOSED_BATCH_HISTORY_POINTS),
        "history_points_count": len(history),
    }


def _session_device_info(session: GrainfatherBrewSession) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, brew_session_device_identifier(session))},
        name=brew_session_display_name(session),
        manufacturer="fidley",
        model="Brew Session",
        entry_type=DeviceEntryType.SERVICE,
    )


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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GrainfatherDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_unique_ids: set[str] = set()

    entities = _build_sensor_entities(coordinator, entry, known_unique_ids)
    async_add_entities(entities)

    def _async_handle_coordinator_update() -> None:
        new_entities = _build_sensor_entities(coordinator, entry, known_unique_ids)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_handle_coordinator_update))


def _build_sensor_entities(
    coordinator: GrainfatherDataUpdateCoordinator,
    entry: ConfigEntry,
    known_unique_ids: set[str],
) -> list[SensorEntity]:
    entities: list[SensorEntity] = []

    for session in coordinator.data.brew_sessions:
        session_fragment = brew_session_unique_fragment(session)
        for description in SESSION_SENSORS:
            unique_id = f"{entry.entry_id}_session_{session_fragment}_{description.key}"
            if unique_id in known_unique_ids:
                continue
            known_unique_ids.add(unique_id)
            entities.append(
                GrainfatherSessionSensor(
                    coordinator,
                    entry,
                    session.batch_id,
                    session_fragment,
                    description,
                )
            )

    for device in coordinator.data.fermentation_devices:
        if device.device_id is None:
            continue

        temp_unique_id = f"{entry.entry_id}_fermdevice_{device.device_id}_temperature"
        if temp_unique_id not in known_unique_ids:
            known_unique_ids.add(temp_unique_id)
            entities.append(
                GrainfatherFermDeviceTemperatureSensor(coordinator, entry, device.device_id)
            )

        target_unique_id = (
            f"{entry.entry_id}_fermdevice_{device.device_id}_target_temperature"
        )
        history = coordinator.data.fermentation_history_by_device_id.get(
            device.device_id,
            tuple(),
        )
        has_target = (
            device.is_controller_linked is True
            or _last_history_value(history, "target_temperature") is not None
        )
        if has_target and target_unique_id not in known_unique_ids:
            known_unique_ids.add(target_unique_id)
            entities.append(
                GrainfatherFermDeviceTargetTemperatureSensor(
                    coordinator,
                    entry,
                    device.device_id,
                )
            )

        gravity_unique_id = f"{entry.entry_id}_fermdevice_{device.device_id}_gravity"
        if gravity_unique_id not in known_unique_ids:
            known_unique_ids.add(gravity_unique_id)
            entities.append(
                GrainfatherFermDeviceGravitySensor(coordinator, entry, device.device_id)
            )

    return entities


class GrainfatherSessionSensor(
    CoordinatorEntity[GrainfatherDataUpdateCoordinator],
    SensorEntity,
):
    entity_description: GrainfatherSessionSensorDescription

    def __init__(
        self,
        coordinator: GrainfatherDataUpdateCoordinator,
        entry: ConfigEntry,
        batch_id: int | str | None,
        session_unique_fragment: str,
        description: GrainfatherSessionSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._batch_id = batch_id
        self._attr_has_entity_name = True
        self._attr_unique_id = (
            f"{entry.entry_id}_session_{session_unique_fragment}_{description.key}"
        )

    @property
    def _session(self) -> GrainfatherBrewSession | None:
        for session in self.coordinator.data.brew_sessions:
            if str(session.batch_id) == str(self._batch_id):
                return session
        return None

    @property
    def available(self) -> bool:
        return self._session is not None

    @property
    def device_info(self) -> DeviceInfo | None:
        session = self._session
        if session is None:
            return None
        return _session_device_info(session)

    @property
    def native_value(self) -> Any:
        session = self._session
        if session is None:
            return None
        return self.entity_description.value_fn(session)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        session = self._session
        if session is None or self.entity_description.attributes_fn is None:
            return None
        attrs = self.entity_description.attributes_fn(session, self.coordinator.data)
        if attrs is None:
            return None
        attrs["default_density_unit"] = self.coordinator.entry.options.get(
            CONF_DEFAULT_DENSITY_UNIT,
            DEFAULT_DENSITY_UNIT,
        )
        return attrs

    async def async_added_to_hass(self) -> None:
        """Write updated state whenever coordinator data changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._force_state_write_if_batch_number)
        )

    def _force_state_write_if_batch_number(self) -> None:
        """Force state write for the batch-number anchor sensor."""
        if self.entity_description.key == "batch_number":
            self.async_write_ha_state()

    @property
    def entity_picture(self) -> str | None:
        if self.entity_description.key != "recipe_image_url":
            return None
        session = self._session
        if session is None:
            return None
        return session.recipe_image_url



class GrainfatherFermDeviceTemperatureSensor(
    CoordinatorEntity[GrainfatherDataUpdateCoordinator],
    SensorEntity,
):
    _attr_translation_key = "fermdevice_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: GrainfatherDataUpdateCoordinator,
        entry: ConfigEntry,
        device_id: int | None,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{entry.entry_id}_fermdevice_{device_id}_temperature"

    @property
    def _device(self) -> GrainfatherFermentationDevice | None:
        for device in self.coordinator.data.fermentation_devices:
            if device.device_id == self._device_id:
                return device
        return None

    @property
    def available(self) -> bool:
        return self._device is not None

    @property
    def native_value(self) -> Any:
        device = self._device
        if device is None:
            return None
        if device.last_temperature is not None:
            return device.last_temperature
        history = self.coordinator.data.fermentation_history_by_device_id.get(
            device.device_id or -1,
            tuple(),
        )
        return _last_history_value(history, "temperature")

    @property
    def device_info(self) -> DeviceInfo | None:
        device = self._device
        if device is None:
            return None
        return _ferm_device_info(device, self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self._device
        if device is None:
            return None
        history = self.coordinator.data.fermentation_history_by_device_id.get(
            device.device_id or -1,
            tuple(),
        )
        collaborators = _get_collaborating_devices(device, self.coordinator.data)
        return {
            "grainfather_entity_type": "fermentation_device",
            "grainfather_measurement": "temperature",
            "device_id": device.device_id,
            "last_heard": device.last_heard,
            "last_specific_gravity": device.last_specific_gravity,
            "linked_brew_session_id": device.linked_brew_session_id,
            "linked_brew_session_name": device.linked_brew_session_name,
            "is_controller_linked": device.is_controller_linked,
            **_controller_runtime_metadata(device, self.coordinator),
            "collaborating_devices": collaborators,
            "default_density_unit": self.coordinator.entry.options.get(
                CONF_DEFAULT_DENSITY_UNIT,
                DEFAULT_DENSITY_UNIT,
            ),
            "history_points": _serialize_history_points(history, _MAX_EXPOSED_DEVICE_HISTORY_POINTS),
            "history_points_count": len(history),
        }


class GrainfatherFermDeviceTargetTemperatureSensor(
    CoordinatorEntity[GrainfatherDataUpdateCoordinator],
    SensorEntity,
):
    """Read-only fermentation controller target from Grainfather history."""

    _attr_translation_key = "fermdevice_target_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: GrainfatherDataUpdateCoordinator,
        entry: ConfigEntry,
        device_id: int | None,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_has_entity_name = True
        self._attr_unique_id = (
            f"{entry.entry_id}_fermdevice_{device_id}_target_temperature"
        )

    @property
    def _device(self) -> GrainfatherFermentationDevice | None:
        for device in self.coordinator.data.fermentation_devices:
            if device.device_id == self._device_id:
                return device
        return None

    @property
    def available(self) -> bool:
        return self._device is not None

    @property
    def native_value(self) -> Any:
        device = self._device
        if device is None:
            return None
        history = self.coordinator.data.fermentation_history_by_device_id.get(
            device.device_id or -1,
            tuple(),
        )
        return _last_history_value(history, "target_temperature")

    @property
    def device_info(self) -> DeviceInfo | None:
        device = self._device
        if device is None:
            return None
        return _ferm_device_info(device, self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self._device
        if device is None:
            return None
        history = self.coordinator.data.fermentation_history_by_device_id.get(
            device.device_id or -1,
            tuple(),
        )
        return {
            "grainfather_entity_type": "fermentation_device",
            "grainfather_measurement": "target_temperature",
            "device_id": device.device_id,
            "linked_brew_session_id": device.linked_brew_session_id,
            "linked_brew_session_name": device.linked_brew_session_name,
            "is_controller_linked": device.is_controller_linked,
            **_controller_runtime_metadata(device, self.coordinator),
            "source": "fermentation_device_history",
            "history_points_count": len(history),
        }


class GrainfatherFermDeviceGravitySensor(
    CoordinatorEntity[GrainfatherDataUpdateCoordinator],
    SensorEntity,
):
    _attr_translation_key = "fermdevice_gravity"
    _attr_suggested_display_precision = 4

    def __init__(
        self,
        coordinator: GrainfatherDataUpdateCoordinator,
        entry: ConfigEntry,
        device_id: int | None,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{entry.entry_id}_fermdevice_{device_id}_gravity"

    @property
    def _device(self) -> GrainfatherFermentationDevice | None:
        for device in self.coordinator.data.fermentation_devices:
            if device.device_id == self._device_id:
                return device
        return None

    @property
    def available(self) -> bool:
        return self._device is not None

    @property
    def native_value(self) -> Any:
        device = self._device
        if device is None:
            return None
        return _gravity_fallback(device, self.coordinator.data)

    @property
    def device_info(self) -> DeviceInfo | None:
        device = self._device
        if device is None:
            return None
        return _ferm_device_info(device, self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self._device
        if device is None:
            return None
        history = self.coordinator.data.fermentation_history_by_device_id.get(
            device.device_id or -1,
            tuple(),
        )
        collaborators = _get_collaborating_devices(device, self.coordinator.data)
        return {
            "grainfather_entity_type": "fermentation_device",
            "grainfather_measurement": "gravity",
            "device_id": device.device_id,
            "last_heard": device.last_heard,
            "linked_brew_session_id": device.linked_brew_session_id,
            "linked_brew_session_name": device.linked_brew_session_name,
            **_controller_runtime_metadata(device, self.coordinator),
            "collaborating_devices": collaborators,
            "default_density_unit": self.coordinator.entry.options.get(
                CONF_DEFAULT_DENSITY_UNIT,
                DEFAULT_DENSITY_UNIT,
            ),
            "history_points": _serialize_history_points(history, _MAX_EXPOSED_DEVICE_HISTORY_POINTS),
            "history_points_count": len(history),
        }

