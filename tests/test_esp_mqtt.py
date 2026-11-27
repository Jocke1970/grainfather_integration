from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from custom_components.grainfather.esp_mqtt import (
    DEFAULT_MQTT_ENDPOINTS,
    GrainfatherEspMqttSubscriber,
    _build_connect_packet,
    _build_subscribe_packet,
    _build_target_temperature_packet,
    _build_telemetry_keepalive_packet,
    _encode_remaining_length,
    _parse_publish_packet,
    _parse_suback_packet,
)
from custom_components.grainfather.esp_runtime import (
    PRIMARY_MQTT_BROKER,
    SECONDARY_MQTT_BROKER,
    GrainfatherEspRuntimeStore,
    subscription_topics,
)


def test_default_endpoints_are_tls_only() -> None:
    assert [
        (endpoint.hostname, endpoint.port, endpoint.use_tls)
        for endpoint in DEFAULT_MQTT_ENDPOINTS
    ] == [
        (PRIMARY_MQTT_BROKER, 8883, True),
        (SECONDARY_MQTT_BROKER, 8883, True),
    ]


def test_encode_remaining_length() -> None:
    assert _encode_remaining_length(0) == bytes([0])
    assert _encode_remaining_length(127) == bytes([127])
    assert _encode_remaining_length(128) == bytes([128, 1])
    assert _encode_remaining_length(16384) == bytes([128, 128, 1])


def test_build_connect_packet_contains_protocol_and_credentials() -> None:
    packet = _build_connect_packet(
        client_id="ha-gf-test",
        username="test-user",
        password="test-password",
        keepalive=60,
    )

    assert packet[0] == 0x10
    assert b"MQTT" in packet
    assert b"ha-gf-test" in packet
    assert b"test-user" in packet
    assert b"test-password" in packet


def test_build_subscribe_packet_uses_qos_zero() -> None:
    packet = _build_subscribe_packet(
        1,
        ("devices/abc123/#", "devices/def456/#"),
    )

    assert packet[0] == 0x82
    assert b"devices/abc123/#" in packet
    assert b"devices/def456/#" in packet


def test_parse_qos_zero_publish_packet() -> None:
    topic = b"devices/abc123/status"
    body = len(topic).to_bytes(2, "big") + topic + b"true"

    parsed_topic, payload, qos, packet_id = _parse_publish_packet(0, body)

    assert parsed_topic == "devices/abc123/status"
    assert payload == b"true"
    assert qos == 0
    assert packet_id is None


def test_parse_qos_one_publish_packet() -> None:
    topic = b"devices/abc123/events"
    body = (
        len(topic).to_bytes(2, "big")
        + topic
        + bytes([0x12, 0x34])
        + b'{"data":{"temp":20.1}}'
    )

    parsed_topic, payload, qos, packet_id = _parse_publish_packet(2, body)

    assert parsed_topic == "devices/abc123/events"
    assert payload == b'{"data":{"temp":20.1}}'
    assert qos == 1
    assert packet_id == 0x1234


def test_parse_suback_packet() -> None:
    packet_id, codes = _parse_suback_packet(
        bytes([0x00, 0x01, 0x00, 0x00, 0x00, 0x00])
    )

    assert packet_id == 1
    assert codes == (0, 0, 0, 0)


def test_parse_suback_packet_accepts_rejection_code() -> None:
    packet_id, codes = _parse_suback_packet(
        bytes([0x00, 0x01, 0x00, 0x80])
    )

    assert packet_id == 1
    assert codes == (0, 0x80)


def test_build_telemetry_keepalive_packet_is_qos_zero_command_23_only() -> None:
    packet = _build_telemetry_keepalive_packet("abc123", 120)

    assert packet[0] == 0x30
    assert b"devices/abc123/command" in packet
    assert b'{"command":23,"value":"120"}' in packet



def test_build_target_temperature_packet_is_qos_zero_command_zero_only() -> None:
    packet = _build_target_temperature_packet("abc123", 21.0)

    assert packet[0] == 0x30
    assert b"devices/abc123/command" in packet
    assert b'{"command":0,"value":"21.00"}' in packet


def test_mixed_case_gf30_id_is_preserved_in_subscriptions() -> None:
    mixed_case_id = "prd-WFC-B12-Example001"
    legacy_id = "36002c000347383531363136"
    subscriber = GrainfatherEspMqttSubscriber(
        user_id="123",
        chip_ids=(mixed_case_id, legacy_id),
        runtime_store=GrainfatherEspRuntimeStore(),
        on_update=lambda: None,
    )

    assert subscriber.chip_ids == (mixed_case_id, legacy_id)
    topics = [
        topic for chip_id in subscriber.chip_ids
        for topic in subscription_topics(chip_id)
    ]
    assert len(topics) == 8
    assert topics[0] == "devices/prd-WFC-B12-Example001/#"
    assert topics[4] == f"devices/{legacy_id}/#"
    packet = _build_subscribe_packet(1, tuple(topics))
    assert b"devices/prd-WFC-B12-Example001/#" in packet
    assert b"devices/prd-wfc-b12-example001/#" not in packet


def test_mixed_case_gf30_id_is_preserved_in_outbound_commands() -> None:
    chip_id = "prd-WFC-B12-Example001"
    keepalive = _build_telemetry_keepalive_packet(chip_id, 120)
    target = _build_target_temperature_packet(chip_id, 21.0)
    assert b"devices/prd-WFC-B12-Example001/command" in keepalive
    assert b"devices/prd-WFC-B12-Example001/command" in target


def test_live_status_keepalive_uses_original_topic_casing() -> None:
    chip_id = "prd-WFC-B12-Example001"
    subscriber = GrainfatherEspMqttSubscriber(
        user_id="123",
        chip_ids=(chip_id, "abc123"),
        runtime_store=GrainfatherEspRuntimeStore(),
        on_update=lambda: None,
    )
    send_keepalive = AsyncMock()
    subscriber._send_telemetry_keepalive = send_keepalive

    topic = f"devices/{chip_id}/status".encode()
    body = len(topic).to_bytes(2, "big") + topic + b"true"
    asyncio.run(subscriber._handle_packet(None, 3, 0, body))

    send_keepalive.assert_awaited_once_with(None, chip_id, 15)
    assert subscriber._runtime_store.get(chip_id).device_online is True
