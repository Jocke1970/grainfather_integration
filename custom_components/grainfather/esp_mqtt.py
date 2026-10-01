from __future__ import annotations

import asyncio
import logging
import secrets
import ssl
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

from .esp_runtime import (
    PRIMARY_MQTT_BROKER,
    SECONDARY_MQTT_BROKER,
    GrainfatherEspRuntimeStore,
    mqtt_credentials,
    subscription_topics,
)

_LOGGER = logging.getLogger(__name__)

_MQTT_KEEPALIVE_SECONDS = 60
_MQTT_IDLE_BEFORE_PING_SECONDS = 45
_MQTT_PING_TIMEOUT_SECONDS = 15
_MQTT_RECONNECT_DELAY_SECONDS = 30


@dataclass(frozen=True, slots=True)
class GrainfatherMqttEndpoint:
    """One read-only MQTT transport candidate."""

    hostname: str
    port: int
    use_tls: bool

    @property
    def label(self) -> str:
        transport = "tls" if self.use_tls else "tcp"
        return f"{self.hostname}:{self.port}/{transport}"


DEFAULT_MQTT_ENDPOINTS: tuple[GrainfatherMqttEndpoint, ...] = (
    GrainfatherMqttEndpoint(PRIMARY_MQTT_BROKER, 8883, True),
    GrainfatherMqttEndpoint(PRIMARY_MQTT_BROKER, 1883, False),
    GrainfatherMqttEndpoint(SECONDARY_MQTT_BROKER, 8883, True),
    GrainfatherMqttEndpoint(SECONDARY_MQTT_BROKER, 1883, False),
)


class GrainfatherEspMqttSubscriber:
    """Minimal subscribe-only MQTT 3.1.1 client for Grainfather ESP devices.

    This class intentionally has no MQTT PUBLISH implementation. It only sends
    protocol control packets required to connect, subscribe, keep the connection
    alive and acknowledge inbound QoS 1 messages.
    """

    def __init__(
        self,
        *,
        user_id: str | int,
        chip_ids: tuple[str, ...],
        runtime_store: GrainfatherEspRuntimeStore,
        on_update: Callable[[], None],
        endpoints: tuple[GrainfatherMqttEndpoint, ...] = DEFAULT_MQTT_ENDPOINTS,
    ) -> None:
        self._username, self._password = mqtt_credentials(user_id)
        self._chip_ids = tuple(
            dict.fromkeys(
                chip_id.strip().casefold()
                for chip_id in chip_ids
                if chip_id and chip_id.strip()
            )
        )
        self._runtime_store = runtime_store
        self._on_update = on_update
        self._endpoints = endpoints
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._client_id = f"ha-gf-{secrets.token_hex(4)}"

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running or not self._chip_ids:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(),
            name="grainfather-esp-mqtt-subscriber",
        )

    async def async_stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._set_broker_connected(False)

    async def _run(self) -> None:
        while not self._stop.is_set():
            for endpoint in self._endpoints:
                if self._stop.is_set():
                    return
                try:
                    await self._connect_and_listen(endpoint)
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # noqa: BLE001 - transport fallback boundary
                    error = (
                        f"{endpoint.label}: {type(err).__name__}: {err}"
                    )
                    self._runtime_store.set_connection_error(
                        list(self._chip_ids),
                        error,
                    )
                    self._on_update()
                    _LOGGER.debug(
                        "Grainfather ESP MQTT subscribe-only connection failed "
                        "via %s: %s",
                        endpoint.label,
                        err,
                    )
                finally:
                    self._runtime_store.set_subscription_result(
                        list(self._chip_ids),
                        False,
                    )
                    self._set_broker_connected(False)

            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=_MQTT_RECONNECT_DELAY_SECONDS,
                )
            except TimeoutError:
                pass

    async def _connect_and_listen(self, endpoint: GrainfatherMqttEndpoint) -> None:
        ssl_context = ssl.create_default_context() if endpoint.use_tls else None

        async with asyncio.timeout(12):
            reader, writer = await asyncio.open_connection(
                endpoint.hostname,
                endpoint.port,
                ssl=ssl_context,
                server_hostname=endpoint.hostname if endpoint.use_tls else None,
            )

        try:
            writer.write(
                _build_connect_packet(
                    client_id=self._client_id,
                    username=self._username,
                    password=self._password,
                    keepalive=_MQTT_KEEPALIVE_SECONDS,
                )
            )
            await writer.drain()

            packet_type, _flags, body = await asyncio.wait_for(
                _read_packet(reader),
                timeout=12,
            )
            if packet_type != 2 or len(body) < 2:
                raise ConnectionError("Broker did not return MQTT CONNACK")
            if body[1] != 0:
                raise ConnectionError(f"MQTT CONNACK rejected with code {body[1]}")

            self._set_broker_connected(True, broker=endpoint.label)

            topics = tuple(
                topic
                for chip_id in self._chip_ids
                for topic in subscription_topics(chip_id)
            )
            writer.write(_build_subscribe_packet(1, topics))
            await writer.drain()

            packet_type, _flags, body = await asyncio.wait_for(
                _read_packet(reader),
                timeout=12,
            )
            if packet_type != 9:
                raise ConnectionError(
                    f"Broker did not return MQTT SUBACK; packet type {packet_type}"
                )

            packet_id, subscription_codes = _parse_suback_packet(body)
            if packet_id != 1:
                raise ConnectionError(
                    f"MQTT SUBACK packet id mismatch: {packet_id}"
                )
            if len(subscription_codes) != len(topics):
                raise ConnectionError(
                    "MQTT SUBACK result count did not match subscription count"
                )

            subscription_ok = not any(
                code == 0x80 for code in subscription_codes
            )
            self._runtime_store.set_subscription_result(
                list(self._chip_ids),
                subscription_ok,
                subscription_codes,
            )
            self._on_update()
            if not subscription_ok:
                raise ConnectionError(
                    f"MQTT subscription rejected: {subscription_codes}"
                )

            self._runtime_store.set_connection_error(
                list(self._chip_ids),
                None,
            )
            self._on_update()
            _LOGGER.info(
                "Grainfather ESP MQTT subscribed via %s to %d topic(s) "
                "for %d device(s)",
                endpoint.label,
                len(topics),
                len(self._chip_ids),
            )

            while not self._stop.is_set():
                try:
                    packet = await asyncio.wait_for(
                        _read_packet(reader),
                        timeout=_MQTT_IDLE_BEFORE_PING_SECONDS,
                    )
                except TimeoutError:
                    writer.write(b"\xc0\x00")  # MQTT PINGREQ, not PUBLISH.
                    await writer.drain()
                    packet = await asyncio.wait_for(
                        _read_packet(reader),
                        timeout=_MQTT_PING_TIMEOUT_SECONDS,
                    )

                await self._handle_packet(writer, *packet)
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _handle_packet(
        self,
        writer: asyncio.StreamWriter,
        packet_type: int,
        flags: int,
        body: bytes,
    ) -> None:
        if packet_type != 3:
            return

        topic, payload, qos, packet_id = _parse_publish_packet(flags, body)
        try:
            self._runtime_store.ingest(topic, payload)
        except (ValueError, TypeError) as err:
            _LOGGER.debug(
                "Ignoring unsupported Grainfather ESP MQTT payload on %s: %s",
                topic,
                err,
            )
        else:
            self._on_update()

        if qos == 1 and packet_id is not None:
            writer.write(b"\x40\x02" + packet_id.to_bytes(2, "big"))  # PUBACK.
            await writer.drain()

    def _set_broker_connected(
        self,
        connected: bool,
        *,
        broker: str | None = None,
    ) -> None:
        self._runtime_store.set_broker_connected(
            list(self._chip_ids),
            connected,
            broker=broker,
        )
        self._on_update()


def _encode_utf8(value: str) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) > 65535:
        raise ValueError("MQTT UTF-8 field is too long")
    return len(encoded).to_bytes(2, "big") + encoded


def _encode_remaining_length(value: int) -> bytes:
    if value < 0 or value > 268435455:
        raise ValueError("Invalid MQTT remaining length")
    encoded = bytearray()
    while True:
        digit = value % 128
        value //= 128
        if value:
            digit |= 0x80
        encoded.append(digit)
        if not value:
            return bytes(encoded)


def _build_connect_packet(
    *,
    client_id: str,
    username: str,
    password: str,
    keepalive: int,
) -> bytes:
    protocol = _encode_utf8("MQTT") + bytes((4, 0xC2)) + keepalive.to_bytes(2, "big")
    payload = (
        _encode_utf8(client_id)
        + _encode_utf8(username)
        + _encode_utf8(password)
    )
    remaining = protocol + payload
    return b"\x10" + _encode_remaining_length(len(remaining)) + remaining


def _build_subscribe_packet(packet_id: int, topics: tuple[str, ...]) -> bytes:
    if not topics:
        raise ValueError("At least one MQTT topic is required")
    payload = b"".join(_encode_utf8(topic) + b"\x00" for topic in topics)
    body = packet_id.to_bytes(2, "big") + payload
    return b"\x82" + _encode_remaining_length(len(body)) + body


async def _read_packet(
    reader: asyncio.StreamReader,
) -> tuple[int, int, bytes]:
    first = (await reader.readexactly(1))[0]
    remaining_length = 0
    multiplier = 1

    for _ in range(4):
        digit = (await reader.readexactly(1))[0]
        remaining_length += (digit & 0x7F) * multiplier
        if not digit & 0x80:
            break
        multiplier *= 128
    else:
        raise ValueError("Malformed MQTT remaining length")

    body = await reader.readexactly(remaining_length)
    return first >> 4, first & 0x0F, body


def _parse_suback_packet(body: bytes) -> tuple[int, tuple[int, ...]]:
    """Parse MQTT SUBACK packet id and granted QoS/error codes."""
    if len(body) < 3:
        raise ValueError("Malformed MQTT SUBACK packet")
    packet_id = int.from_bytes(body[:2], "big")
    codes = tuple(body[2:])
    if any(code not in {0, 1, 2, 0x80} for code in codes):
        raise ValueError(f"Invalid MQTT SUBACK return code(s): {codes}")
    return packet_id, codes


def _parse_publish_packet(
    flags: int,
    body: bytes,
) -> tuple[str, bytes, int, int | None]:
    if len(body) < 2:
        raise ValueError("Malformed MQTT PUBLISH packet")

    topic_length = int.from_bytes(body[:2], "big")
    cursor = 2
    topic_end = cursor + topic_length
    if topic_end > len(body):
        raise ValueError("Malformed MQTT PUBLISH topic")

    topic = body[cursor:topic_end].decode("utf-8")
    cursor = topic_end
    qos = (flags >> 1) & 0x03
    if qos == 3:
        raise ValueError("Invalid MQTT PUBLISH QoS")

    packet_id: int | None = None
    if qos > 0:
        if cursor + 2 > len(body):
            raise ValueError("Missing MQTT PUBLISH packet id")
        packet_id = int.from_bytes(body[cursor : cursor + 2], "big")
        cursor += 2

    return topic, body[cursor:], qos, packet_id
