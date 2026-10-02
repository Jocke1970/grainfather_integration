from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

from homeassistant.util.ssl import client_context

from .esp_runtime import (
    ESP_INITIAL_SUBSCRIPTION_SECONDS,
    ESP_REFRESH_SUBSCRIPTION_SECONDS,
    ESP_TARGET_TEMPERATURE_MAX_C,
    ESP_TARGET_TEMPERATURE_MIN_C,
    PRIMARY_MQTT_BROKER,
    SECONDARY_MQTT_BROKER,
    GrainfatherEspRuntimeStore,
    command_topic,
    mqtt_credentials,
    parse_device_topic,
    subscription_topics,
    target_temperature_payload,
    telemetry_keepalive_payload,
)

_LOGGER = logging.getLogger(__name__)

_MQTT_KEEPALIVE_SECONDS = 60
_MQTT_IDLE_BEFORE_PING_SECONDS = 45
_MQTT_PING_TIMEOUT_SECONDS = 15
_MQTT_RECONNECT_DELAY_SECONDS = 30
_MQTT_INITIAL_TELEMETRY_REFRESH_DELAY_SECONDS = 10
_MQTT_TELEMETRY_REFRESH_INTERVAL_SECONDS = 90
_TARGET_WRITE_READBACK_TIMEOUT_SECONDS = 10
_TARGET_WRITE_POLL_INTERVAL_SECONDS = 0.1


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
    GrainfatherMqttEndpoint(SECONDARY_MQTT_BROKER, 8883, True),
)


class GrainfatherEspMqttSubscriber:
    """MQTT 3.1.1 telemetry client for Grainfather ESP devices.

    Outbound MQTT is structurally limited to two field-verified commands:
    command 23 for telemetry subscription maintenance and command 0 for an
    explicitly requested target temperature. There is no generic command
    publisher and no heater/cooling/mode/profile write path.
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
        self._telemetry_refresh_due_at: dict[str, float] = {}
        self._active_writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()

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
                        "Grainfather ESP MQTT telemetry connection failed "
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
        self._telemetry_refresh_due_at.clear()
        ssl_context = client_context() if endpoint.use_tls else None

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
            self._active_writer = writer
            self._on_update()
            _LOGGER.info(
                "Grainfather ESP MQTT subscribed via %s to %d topic(s) "
                "for %d device(s)",
                endpoint.label,
                len(topics),
                len(self._chip_ids),
            )

            while not self._stop.is_set():
                if await self._send_due_telemetry_keepalives(writer):
                    continue

                try:
                    packet = await asyncio.wait_for(
                        _read_packet(reader),
                        timeout=self._next_read_timeout(),
                    )
                except TimeoutError:
                    if await self._send_due_telemetry_keepalives(writer):
                        continue
                    writer.write(b"\xc0\x00")  # MQTT PINGREQ, not PUBLISH.
                    await writer.drain()
                    packet = await asyncio.wait_for(
                        _read_packet(reader),
                        timeout=_MQTT_PING_TIMEOUT_SECONDS,
                    )

                await self._handle_packet(writer, *packet)
        finally:
            self._telemetry_refresh_due_at.clear()
            if self._active_writer is writer:
                self._active_writer = None
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
        state = None
        try:
            state = self._runtime_store.ingest(topic, payload)
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

        if state is None:
            return

        chip_id, topic_type = parse_device_topic(topic)
        if topic_type == "status":
            if state.device_online is True:
                await self._send_telemetry_keepalive(
                    writer,
                    chip_id,
                    ESP_INITIAL_SUBSCRIPTION_SECONDS,
                )
            else:
                self._telemetry_refresh_due_at.pop(chip_id, None)
        elif (
            topic_type == "events"
            and state.event is not None
            and state.event.subscription_time is not None
            and state.event.subscription_time < 25
        ):
            await self._send_telemetry_keepalive(
                writer,
                chip_id,
                ESP_REFRESH_SUBSCRIPTION_SECONDS,
            )

    def _next_read_timeout(self) -> float:
        """Wake before the next telemetry refresh or normal MQTT ping."""
        if not self._telemetry_refresh_due_at:
            return _MQTT_IDLE_BEFORE_PING_SECONDS

        loop = asyncio.get_running_loop()
        next_due = min(self._telemetry_refresh_due_at.values())
        return max(
            0.1,
            min(_MQTT_IDLE_BEFORE_PING_SECONDS, next_due - loop.time()),
        )

    async def _send_due_telemetry_keepalives(
        self,
        writer: asyncio.StreamWriter,
    ) -> bool:
        """Refresh active controller telemetry before subscription expiry."""
        if not self._telemetry_refresh_due_at:
            return False

        now = asyncio.get_running_loop().time()
        due_chip_ids = [
            chip_id
            for chip_id, due_at in self._telemetry_refresh_due_at.items()
            if due_at <= now
        ]
        for chip_id in due_chip_ids:
            await self._send_telemetry_keepalive(
                writer,
                chip_id,
                ESP_REFRESH_SUBSCRIPTION_SECONDS,
            )
        return bool(due_chip_ids)

    async def async_set_target_temperature(
        self,
        chip_id: str,
        temperature_c: float,
    ) -> float:
        """Set GF30 target and require a fresh MQTT readback before success."""
        normalized_chip_id = chip_id.strip().casefold()
        if normalized_chip_id not in self._chip_ids:
            raise ValueError("Unknown Grainfather ESP chip id")

        target = float(temperature_c)
        if not ESP_TARGET_TEMPERATURE_MIN_C <= target <= ESP_TARGET_TEMPERATURE_MAX_C:
            raise ValueError(
                "Target temperature is outside the bounded Grainfather range"
            )

        writer = self._active_writer
        state = self._runtime_store.get(normalized_chip_id)
        if (
            writer is None
            or state is None
            or not state.broker_connected
            or not state.mqtt_subscribed
            or state.device_online is not True
        ):
            raise ConnectionError("Grainfather controller MQTT is not ready")

        previous_event_at = state.last_event_at
        packet = _build_target_temperature_packet(normalized_chip_id, target)
        self._runtime_store.mark_target_write_requested(normalized_chip_id, target)
        self._on_update()

        async with self._write_lock:
            writer.write(packet)
            await writer.drain()

        deadline = (
            asyncio.get_running_loop().time()
            + _TARGET_WRITE_READBACK_TIMEOUT_SECONDS
        )
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(_TARGET_WRITE_POLL_INTERVAL_SECONDS)
            state = self._runtime_store.get(normalized_chip_id)
            if state is None or state.event is None or state.last_event_at is None:
                continue
            if (
                previous_event_at is not None
                and state.last_event_at <= previous_event_at
            ):
                continue
            readback = state.event.target_temperature
            if readback is None:
                continue
            if abs(readback - target) <= 0.01:
                self._runtime_store.mark_target_write_result(
                    normalized_chip_id,
                    "verified",
                    readback_value=readback,
                    readback_at=state.last_event_at,
                )
                self._on_update()
                return readback

            self._runtime_store.mark_target_write_result(
                normalized_chip_id,
                "mismatch",
                readback_value=readback,
                readback_at=state.last_event_at,
            )
            self._on_update()

        self._runtime_store.mark_target_write_result(
            normalized_chip_id,
            "timeout",
        )
        self._on_update()
        raise TimeoutError(
            f"GF30 target {target:.2f} °C was not verified by fresh MQTT readback"
        )

    async def _send_telemetry_keepalive(
        self,
        writer: asyncio.StreamWriter,
        chip_id: str,
        seconds: int,
    ) -> None:
        packet = _build_telemetry_keepalive_packet(chip_id, seconds)
        async with self._write_lock:
            writer.write(packet)
            await writer.drain()
        self._runtime_store.mark_telemetry_keepalive(chip_id, seconds)
        refresh_delay = (
            _MQTT_INITIAL_TELEMETRY_REFRESH_DELAY_SECONDS
            if seconds == ESP_INITIAL_SUBSCRIPTION_SECONDS
            else _MQTT_TELEMETRY_REFRESH_INTERVAL_SECONDS
        )
        self._telemetry_refresh_due_at[chip_id] = (
            asyncio.get_running_loop().time() + refresh_delay
        )
        self._on_update()
        _LOGGER.debug(
            "Refreshed Grainfather ESP telemetry subscription for %s to %ss",
            chip_id,
            seconds,
        )

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


def _build_target_temperature_packet(chip_id: str, temperature_c: float) -> bytes:
    """Build the only approved supervised controller target PUBLISH packet."""
    topic = command_topic(chip_id)
    payload = target_temperature_payload(temperature_c).encode()
    body = _encode_utf8(topic) + payload
    return b"\x30" + _encode_remaining_length(len(body)) + body


def _build_telemetry_keepalive_packet(chip_id: str, seconds: int) -> bytes:
    """Build the only allowed controller-bound MQTT PUBLISH packet."""
    topic = command_topic(chip_id)
    payload = telemetry_keepalive_payload(seconds).encode()
    body = _encode_utf8(topic) + payload
    return b"\x30" + _encode_remaining_length(len(body)) + body


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
