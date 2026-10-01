import json

import pytest

from custom_components.grainfather.esp_runtime import (
    PRIMARY_MQTT_BROKER,
    SECONDARY_MQTT_BROKER,
    GrainfatherEspRuntimeStore,
    device_topic,
    mqtt_credentials,
    mqtt_password,
    mqtt_username,
    parse_device_topic,
    parse_event_payload,
    parse_meta_payload,
    parse_status_payload,
    subscription_topics,
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
    assert state.broker == PRIMARY_MQTT_BROKER
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
