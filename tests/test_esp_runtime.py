import json
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.grainfather.esp_runtime import (
    ESP_EVENT_FRESHNESS_SECONDS,
    ESP_INITIAL_SUBSCRIPTION_SECONDS,
    ESP_REFRESH_SUBSCRIPTION_SECONDS,
    ESP_SET_TARGET_TEMPERATURE_COMMAND,
    ESP_SUBSCRIPTION_TIME_COMMAND,
    ESP_TARGET_TEMPERATURE_MAX_C,
    ESP_TARGET_TEMPERATURE_MIN_C,
    PRIMARY_MQTT_BROKER,
    SECONDARY_MQTT_BROKER,
    GrainfatherEspRuntimeStore,
    command_topic,
    device_topic,
    event_age_seconds,
    event_is_fresh,
    mqtt_credentials,
    mqtt_password,
    mqtt_username,
    parse_device_topic,
    parse_event_payload,
    parse_meta_payload,
    parse_status_payload,
    subscription_topics,
    target_temperature_payload,
    telemetry_keepalive_payload,
)


def test_brokers_match_current_grainfather_app_defaults() -> None:
    assert PRIMARY_MQTT_BROKER == "mqtt.grainfather.com"
    assert SECONDARY_MQTT_BROKER == "mqtt2.grainfather.com"


def test_device_topic_and_subscriptions() -> None:
    chip_id = "36002c000347383531363136"

    assert device_topic(chip_id) == f"devices/{chip_id}/"
    assert subscription_topics(chip_id) == (
        f"devices/{chip_id}/#",
        f"devices/{chip_id}/meta",
        f"devices/{chip_id}/status",
        f"devices/{chip_id}/config",
    )


def test_device_topic_rejects_empty_chip_id() -> None:
    with pytest.raises(ValueError):
        device_topic("   ")


def test_telemetry_keepalive_payload_is_bounded_to_command_23() -> None:
    assert ESP_SUBSCRIPTION_TIME_COMMAND == 23
    assert ESP_INITIAL_SUBSCRIPTION_SECONDS == 15
    assert ESP_REFRESH_SUBSCRIPTION_SECONDS == 120
    assert telemetry_keepalive_payload(15) == '{"command":23,"value":"15"}'
    assert telemetry_keepalive_payload(120) == '{"command":23,"value":"120"}'
    assert command_topic("ABC123") == "devices/ABC123/command"

    with pytest.raises(ValueError):
        telemetry_keepalive_payload(30)


def test_parse_status_payload() -> None:
    assert parse_status_payload("true") is True
    assert parse_status_payload("false") is False
    assert parse_status_payload(b"1") is True
    assert parse_status_payload(b"0") is False


def test_parse_event_payload_normalizes_gf30_fields() -> None:
    payload = {
        "data": {
            "temp": "18.7",
            "target": "18.0",
            "heatStatus": True,
            "coolStatus": False,
            "rssi": "-61",
            "subTime": "17",
        },
        "settings": {
            "controlMode": 1,
            "controlStatus": 1,
            "units": 1,
            "hysteresis": "0.5",
            "offSetTemp": "-0.2",
            "lowerAlertEnabled": 1,
        },
        "session": {
            "sessionId": 1234,
            "managedMode": 1,
            "sessionStage": 2,
            "stageEndTime": 3600,
        },
    }

    event = parse_event_payload(json.dumps(payload))

    assert event.temperature == 18.7
    assert event.target_temperature == 18.0
    assert event.heating is True
    assert event.cooling is False
    assert event.rssi == -61.0
    assert event.subscription_time == 17
    assert event.control_mode == 1
    assert event.control_active is True
    assert event.units == 1
    assert event.hysteresis == 0.5
    assert event.temperature_offset == -0.2
    assert event.lower_temp_alert_enabled is True
    assert event.session_id == 1234
    assert event.managed_mode is True
    assert event.session_stage == 2
    assert event.stage_end_time == 3600


def test_parse_event_payload_accepts_legacy_setting_aliases() -> None:
    payload = {
        "settings": {
            "offset": "0.3",
            "lowTemp": 0,
        }
    }

    event = parse_event_payload(json.dumps(payload))

    assert event.temperature_offset == 0.3
    assert event.lower_temp_alert_enabled is False


def test_parse_meta_payload() -> None:
    payload = {
        "version": 5.1,
        "errorCode": 0,
        "ssid": "Brewery",
        "ip": "192.0.2.10",
        "mac": "AA:BB:CC:DD:EE:FF",
        "otaStatus": 0,
        "ota": True,
        "rssi": -55,
    }

    meta = parse_meta_payload(json.dumps(payload))

    assert meta.firmware_version == 5.1
    assert meta.error_code == 0
    assert meta.ssid == "Brewery"
    assert meta.ip_address == "192.0.2.10"
    assert meta.mac_address == "AA:BB:CC:DD:EE:FF"
    assert meta.ota_status == 0
    assert meta.ota_available is True
    assert meta.rssi == -55.0


def test_event_parser_rejects_non_object_json() -> None:
    with pytest.raises(ValueError):
        parse_event_payload("[]")


def test_mqtt_credentials_match_current_app_md5_derivation() -> None:
    assert mqtt_username("12345") == "12345"
    assert mqtt_password("12345") == "1285290e6e93e92ea85ce46f74cba631"
    assert mqtt_credentials(12345) == (
        "12345",
        "1285290e6e93e92ea85ce46f74cba631",
    )


def test_mqtt_username_rejects_empty_user_id() -> None:
    with pytest.raises(ValueError):
        mqtt_username("   ")


def test_parse_device_topic() -> None:
    assert parse_device_topic("devices/ABC123/events") == ("abc123", "events")

    with pytest.raises(ValueError):
        parse_device_topic("wrong/ABC123/events")


def test_runtime_store_ingests_read_only_topics() -> None:
    store = GrainfatherEspRuntimeStore()
    chip_id = "ABC123"
    store.set_broker_connected([chip_id], True, broker=PRIMARY_MQTT_BROKER)

    state = store.ingest(
        "devices/ABC123/events",
        json.dumps(
            {
                "data": {
                    "temp": 19.2,
                    "target": 18,
                    "heatStatus": True,
                    "coolStatus": False,
                    "rssi": -48,
                },
                "settings": {"controlMode": 3, "controlStatus": True},
            }
        ),
    )
    store.ingest(
        "devices/ABC123/meta",
        json.dumps({"version": "1.2.3", "errorCode": 0}),
    )
    store.ingest("devices/ABC123/status", "true")
    store.ingest("devices/ABC123/config", json.dumps({"example": 1}))
    store.ingest("devices/ABC123/profiles", json.dumps([{"id": 1}]))

    assert state is store.get("abc123")
    assert state.broker_connected is True
    store.set_subscription_result([chip_id], True, (0, 0, 0, 0))
    assert state.mqtt_subscribed is True
    assert state.subscription_codes == (0, 0, 0, 0)
    assert state.broker == PRIMARY_MQTT_BROKER
    store.set_connection_error([chip_id], "test connection error")
    assert state.last_connection_error == "test connection error"
    store.set_connection_error([chip_id], None)
    assert state.last_connection_error is None
    assert state.device_online is True
    assert state.event is not None
    assert state.event.temperature == 19.2
    assert state.event.target_temperature == 18.0
    assert state.event.heating is True
    assert state.event.cooling is False
    assert state.event.control_mode == 3
    assert state.meta is not None
    assert state.meta.firmware_version == "1.2.3"
    assert state.config == {"example": 1}
    assert state.profiles == [{"id": 1}]
    assert state.last_message_at is not None
    store.mark_telemetry_keepalive(chip_id, 120)
    assert state.telemetry_keepalive_seconds == 120
    assert state.telemetry_keepalive_count == 1
    assert state.telemetry_keepalive_last_sent_at is not None



def test_command_echo_does_not_advance_inbound_freshness() -> None:
    store = GrainfatherEspRuntimeStore()
    observed_at = datetime(2026, 10, 2, 16, 0, tzinfo=UTC)

    result = store.ingest(
        "devices/ABC123/command",
        '{"command":23,"value":"120"}',
        received_at=observed_at,
    )

    assert result is None
    state = store.get("ABC123")
    assert state is None


def test_event_freshness_tracks_events_not_other_topics() -> None:
    store = GrainfatherEspRuntimeStore()
    event_at = datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
    later = event_at + timedelta(seconds=ESP_EVENT_FRESHNESS_SECONDS - 1)
    stale = event_at + timedelta(seconds=ESP_EVENT_FRESHNESS_SECONDS + 1)

    state = store.ingest(
        "devices/ABC123/events",
        '{"data":{"temp":23,"target":22,"subTime":120}}',
        received_at=event_at,
    )
    assert state is not None
    assert state.last_event_at == event_at
    assert event_age_seconds(state, now=later) == ESP_EVENT_FRESHNESS_SECONDS - 1
    assert event_is_fresh(state, now=later) is True
    assert event_is_fresh(state, now=stale) is False

    status_at = stale + timedelta(seconds=30)
    store.ingest("devices/ABC123/status", "true", received_at=status_at)
    assert state.last_message_at == status_at
    assert state.last_event_at == event_at



def test_command_23_echo_is_ignored_by_passive_observer() -> None:
    store = GrainfatherEspRuntimeStore()
    observed_at = datetime(2026, 10, 2, 19, 0, tzinfo=UTC)

    store.observe_command(
        "ABC123",
        '{"command":23,"value":"120"}',
        observed_at=observed_at,
    )

    assert store.get("ABC123") is None


def test_non_keepalive_command_is_observed_without_advancing_telemetry() -> None:
    store = GrainfatherEspRuntimeStore()
    observed_at = datetime(2026, 10, 2, 19, 1, tzinfo=UTC)

    result = store.ingest(
        "devices/ABC123/command",
        '{"command":42,"value":"18.5"}',
        received_at=observed_at,
    )

    assert result is None
    state = store.get("ABC123")
    assert state is not None
    assert state.last_message_at is None
    assert state.last_event_at is None
    assert state.observed_external_command_at == observed_at
    assert state.observed_external_command_id == 42
    assert state.observed_external_command_value == "18.5"
    assert state.observed_external_command_payload == (
        '{"command":42,"value":"18.5"}'
    )
    assert state.observed_external_command_count == 1


def test_malformed_command_observation_is_ignored() -> None:
    store = GrainfatherEspRuntimeStore()

    store.observe_command("ABC123", b"not-json")

    assert store.get("ABC123") is None



def test_target_temperature_payload_is_bounded_to_command_zero() -> None:
    assert ESP_SET_TARGET_TEMPERATURE_COMMAND == 0
    assert ESP_TARGET_TEMPERATURE_MIN_C == 0.0
    assert ESP_TARGET_TEMPERATURE_MAX_C == 40.0
    assert target_temperature_payload(21) == '{"command":0,"value":"21.00"}'
    assert target_temperature_payload(1.5) == '{"command":0,"value":"1.50"}'

    with pytest.raises(ValueError):
        target_temperature_payload(-0.1)
    with pytest.raises(ValueError):
        target_temperature_payload(40.1)


def test_target_write_diagnostics_are_recorded() -> None:
    store = GrainfatherEspRuntimeStore()
    requested_at = datetime(2026, 10, 2, 20, 0, tzinfo=UTC)
    readback_at = requested_at + timedelta(seconds=1)

    store.mark_target_write_requested("ABC123", 18.5, requested_at=requested_at)
    state = store.get("ABC123")
    assert state is not None
    assert state.target_write_last_requested_at == requested_at
    assert state.target_write_last_requested_value == 18.5
    assert state.target_write_last_result == "pending"

    store.mark_target_write_result(
        "ABC123",
        "verified",
        readback_value=18.5,
        readback_at=readback_at,
    )
    assert state.target_write_last_result == "verified"
    assert state.target_write_last_readback_value == 18.5
    assert state.target_write_last_readback_at == readback_at
