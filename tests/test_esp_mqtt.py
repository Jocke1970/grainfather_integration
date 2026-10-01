from __future__ import annotations

from custom_components.grainfather.esp_mqtt import (
    DEFAULT_MQTT_ENDPOINTS,
    _build_connect_packet,
    _build_subscribe_packet,
    _encode_remaining_length,
    _parse_publish_packet,
    _parse_suback_packet,
)
from custom_components.grainfather.esp_runtime import (
    PRIMARY_MQTT_BROKER,
    SECONDARY_MQTT_BROKER,
)


def test_default_endpoints_prefer_tls_then_plaintext_fallback() -> None:
    assert [
        (endpoint.hostname, endpoint.port, endpoint.use_tls)
        for endpoint in DEFAULT_MQTT_ENDPOINTS
    ] == [
        (PRIMARY_MQTT_BROKER, 8883, True),
        (PRIMARY_MQTT_BROKER, 1883, False),
        (SECONDARY_MQTT_BROKER, 8883, True),
        (SECONDARY_MQTT_BROKER, 1883, False),
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
