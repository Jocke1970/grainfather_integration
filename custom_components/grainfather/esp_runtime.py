from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

PRIMARY_MQTT_BROKER = "mqtt.grainfather.com"
SECONDARY_MQTT_BROKER = "mqtt2.grainfather.com"

# The current Grainfather app subscribes to these suffixes for every ESP accessory.
# "#" includes events/profiles; the explicit retained topics mirror the app runtime.
MQTT_SUBSCRIPTION_SUFFIXES = ("#", "meta", "status", "config")


@dataclass(frozen=True, slots=True)
class GrainfatherEspEvent:
    """Read-only normalized GF30/WFC event payload."""

    temperature: float | None
    target_temperature: float | None
    heating: bool | None
    cooling: bool | None
    rssi: float | None
    control_mode: int | None
    control_active: bool | None
    units: int | None
    hysteresis: float | None
    temperature_offset: float | None
    lower_temp_alert_enabled: bool | None
    session_id: int | None
    managed_mode: bool | None
    session_stage: int | None
    stage_end_time: int | None
    raw_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GrainfatherEspMeta:
    """Read-only controller metadata published on the retained meta topic."""

    firmware_version: float | str | None
    error_code: int | None
    ssid: str | None
    ip_address: str | None
    mac_address: str | None
    ota_status: int | None
    ota_available: bool | None
    rssi: float | None
    raw_payload: dict[str, Any]


def device_topic(chip_id: str) -> str:
    """Return the Grainfather MQTT topic prefix for one ESP accessory."""
    normalized = chip_id.strip()
    if not normalized:
        raise ValueError("chip_id must not be empty")
    return f"devices/{normalized}/"


def subscription_topics(chip_id: str) -> tuple[str, ...]:
    """Return the read-only topic set mirrored from the Grainfather app."""
    prefix = device_topic(chip_id)
    return tuple(f"{prefix}{suffix}" for suffix in MQTT_SUBSCRIPTION_SUFFIXES)


def parse_status_payload(payload: str | bytes) -> bool:
    """Parse Grainfather's JSON MQTT status payload."""
    value = json.loads(_decode_payload(payload))
    return bool(value)


def parse_event_payload(payload: str | bytes) -> GrainfatherEspEvent:
    """Parse the modern GF30/WFC events payload without issuing any controller command."""
    data = _json_object(payload)
    reading = data.get("data") if isinstance(data.get("data"), dict) else {}
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    session = data.get("session") if isinstance(data.get("session"), dict) else {}

    return GrainfatherEspEvent(
        temperature=_to_float(reading.get("temp")),
        target_temperature=_to_float(reading.get("target")),
        heating=_to_bool_or_none(reading.get("heatStatus")),
        cooling=_to_bool_or_none(reading.get("coolStatus")),
        rssi=_to_float(reading.get("rssi")),
        control_mode=_to_int(settings.get("controlMode")),
        control_active=_to_bool_or_none(settings.get("controlStatus")),
        units=_to_int(settings.get("units")),
        hysteresis=_to_float(settings.get("hysteresis")),
        temperature_offset=_to_float(
            settings.get("offSetTemp")
            if settings.get("offSetTemp") is not None
            else settings.get("offset")
        ),
        lower_temp_alert_enabled=_to_bool_or_none(
            settings.get("lowerAlertEnabled")
            if settings.get("lowerAlertEnabled") is not None
            else settings.get("lowTemp")
        ),
        session_id=_to_int(session.get("sessionId")),
        managed_mode=_to_bool_or_none(session.get("managedMode")),
        session_stage=_to_int(session.get("sessionStage")),
        stage_end_time=_to_int(session.get("stageEndTime")),
        raw_payload=data,
    )


def parse_meta_payload(payload: str | bytes) -> GrainfatherEspMeta:
    """Parse the modern controller meta payload."""
    data = _json_object(payload)
    return GrainfatherEspMeta(
        firmware_version=data.get("version"),
        error_code=_to_int(data.get("errorCode")),
        ssid=_to_str(data.get("ssid")),
        ip_address=_to_str(data.get("ip")),
        mac_address=_to_str(data.get("mac")),
        ota_status=_to_int(data.get("otaStatus")),
        ota_available=_to_bool_or_none(data.get("ota")),
        rssi=_to_float(data.get("rssi")),
        raw_payload=data,
    )


def _json_object(payload: str | bytes) -> dict[str, Any]:
    value = json.loads(_decode_payload(payload))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object payload")
    return value


def _decode_payload(payload: str | bytes) -> str:
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    return payload


def _to_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "on"}:
            return True
        if lowered in {"false", "0", "off"}:
            return False
    return bool(value)
