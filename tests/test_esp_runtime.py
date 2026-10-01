import json

import pytest

from custom_components.grainfather.esp_runtime import (
    PRIMARY_MQTT_BROKER,
    SECONDARY_MQTT_BROKER,
    device_topic,
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
