from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

PRIMARY_MQTT_BROKER = "mqtt.grainfather.com"
SECONDARY_MQTT_BROKER = "mqtt2.grainfather.com"

# The current Grainfather app subscribes to these suffixes for every ESP accessory.
# "#" includes events/profiles; the explicit retained topics mirror the app runtime.
MQTT_SUBSCRIPTION_SUFFIXES = ("#", "meta", "status", "config")
ESP_SUBSCRIPTION_TIME_COMMAND = 23
ESP_INITIAL_SUBSCRIPTION_SECONDS = 15
ESP_REFRESH_SUBSCRIPTION_SECONDS = 120


def mqtt_username(user_id: str | int) -> str:
    """Return the Grainfather MQTT username used by the current mobile app."""
    value = str(user_id).strip()
    if not value:
        raise ValueError("user_id must not be empty")
    return value


def mqtt_password(user_id: str | int) -> str:
    """Derive the Grainfather MQTT password exactly as the current app does.

    The app imports webpack module 2517, which maps to CryptoJS MD5, and calls
    MD5("<user_id>BEVIE"). Stringifying the CryptoJS WordArray yields lowercase
    hexadecimal, matching hashlib.hexdigest().
    """
    username = mqtt_username(user_id)
    return hashlib.md5(f"{username}BEVIE".encode()).hexdigest()


def mqtt_credentials(user_id: str | int) -> tuple[str, str]:
    """Return the current app-compatible MQTT username/password pair."""
    username = mqtt_username(user_id)
    return username, mqtt_password(username)


@dataclass(frozen=True, slots=True)
class GrainfatherEspEvent:
    """Read-only normalized GF30/WFC event payload."""

    temperature: float | None
    target_temperature: float | None
    heating: bool | None
    cooling: bool | None
    rssi: float | None
    subscription_time: int | None
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


def telemetry_keepalive_payload(seconds: int) -> str:
    """Build the only controller-bound payload allowed by live telemetry."""
    if seconds not in {
        ESP_INITIAL_SUBSCRIPTION_SECONDS,
        ESP_REFRESH_SUBSCRIPTION_SECONDS,
    }:
        raise ValueError("Unsupported Grainfather telemetry subscription time")
    return json.dumps(
        {
            "command": ESP_SUBSCRIPTION_TIME_COMMAND,
            "value": str(seconds),
        },
        separators=(",", ":"),
    )


def command_topic(chip_id: str) -> str:
    """Return the Grainfather controller command topic for one ESP accessory."""
    return f"{device_topic(chip_id)}command"


def parse_status_payload(payload: str | bytes) -> bool:
    """Parse Grainfather's JSON MQTT status payload."""
    value = json.loads(_decode_payload(payload))
    return bool(value)


def parse_event_payload(payload: str | bytes) -> GrainfatherEspEvent:
    """Parse modern GF30/WFC events without issuing any controller command."""
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
        subscription_time=_to_int(reading.get("subTime")),
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


@dataclass(slots=True)
class GrainfatherEspLiveState:
    """Mutable read-only cache for one ESP accessory's observed MQTT state."""

    chip_id: str
    broker_connected: bool = False
    mqtt_subscribed: bool = False
    subscription_codes: tuple[int, ...] | None = None
    broker: str | None = None
    device_online: bool | None = None
    event: GrainfatherEspEvent | None = None
    meta: GrainfatherEspMeta | None = None
    config: dict[str, Any] | None = None
    profiles: Any = None
    last_topic: str | None = None
    last_message_at: datetime | None = None
    last_connection_error: str | None = None
    telemetry_keepalive_last_sent_at: datetime | None = None
    telemetry_keepalive_seconds: int | None = None
    telemetry_keepalive_count: int = 0


@dataclass(slots=True)
class GrainfatherEspRuntimeStore:
    """In-memory cache for subscribe-only ESP MQTT observations."""

    states: dict[str, GrainfatherEspLiveState] = field(default_factory=dict)

    def get(self, chip_id: str | None) -> GrainfatherEspLiveState | None:
        if not chip_id:
            return None
        return self.states.get(chip_id.strip().casefold())

    def ensure(self, chip_id: str) -> GrainfatherEspLiveState:
        normalized = chip_id.strip().casefold()
        if not normalized:
            raise ValueError("chip_id must not be empty")
        state = self.states.get(normalized)
        if state is None:
            state = GrainfatherEspLiveState(chip_id=normalized)
            self.states[normalized] = state
        return state

    def set_broker_connected(
        self,
        chip_ids: tuple[str, ...] | list[str],
        connected: bool,
        *,
        broker: str | None = None,
    ) -> None:
        for chip_id in chip_ids:
            state = self.ensure(chip_id)
            state.broker_connected = connected
            if broker is not None:
                state.broker = broker

    def set_subscription_result(
        self,
        chip_ids: tuple[str, ...] | list[str],
        subscribed: bool,
        codes: tuple[int, ...] | None = None,
    ) -> None:
        for chip_id in chip_ids:
            state = self.ensure(chip_id)
            state.mqtt_subscribed = subscribed
            if codes is not None:
                state.subscription_codes = codes

    def set_connection_error(
        self,
        chip_ids: tuple[str, ...] | list[str],
        error: str | None,
    ) -> None:
        for chip_id in chip_ids:
            self.ensure(chip_id).last_connection_error = error

    def mark_telemetry_keepalive(
        self,
        chip_id: str,
        seconds: int,
        *,
        sent_at: datetime | None = None,
    ) -> None:
        state = self.ensure(chip_id)
        state.telemetry_keepalive_last_sent_at = sent_at or datetime.now(UTC)
        state.telemetry_keepalive_seconds = seconds
        state.telemetry_keepalive_count += 1

    def ingest(
        self,
        topic: str,
        payload: str | bytes,
        *,
        received_at: datetime | None = None,
    ) -> GrainfatherEspLiveState:
        chip_id, topic_type = parse_device_topic(topic)
        state = self.ensure(chip_id)
        state.last_topic = topic
        state.last_message_at = received_at or datetime.now(UTC)

        if topic_type == "status":
            state.device_online = parse_status_payload(payload)
        elif topic_type == "meta":
            state.meta = parse_meta_payload(payload)
        elif topic_type == "config":
            state.config = _json_object(payload)
        elif topic_type == "events":
            state.event = parse_event_payload(payload)
        elif topic_type == "profiles":
            state.profiles = json.loads(_decode_payload(payload))

        return state


def parse_device_topic(topic: str) -> tuple[str, str]:
    """Split a modern Grainfather device topic into chip id and topic type."""
    parts = topic.strip().split("/")
    if len(parts) < 3 or parts[0] != "devices" or not parts[1] or not parts[2]:
        raise ValueError(f"Unsupported Grainfather MQTT topic: {topic!r}")
    return parts[1].casefold(), parts[2]

