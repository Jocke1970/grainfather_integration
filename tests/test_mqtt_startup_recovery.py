from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "custom_components/grainfather/__init__.py"
MQTT = ROOT / "custom_components/grainfather/esp_mqtt.py"


def test_mqtt_reconcile_runs_after_later_coordinator_refresh() -> None:
    source = INIT.read_text(encoding="utf-8")

    assert "async def _async_reconcile_esp_mqtt_subscriber" in source
    assert "_async_handle_coordinator_update" in source
    assert "_async_reconcile_esp_mqtt_subscriber(coordinator)" in source
    assert "device.esp_chip_id.strip().casefold()" in source


def test_mqtt_reconcile_is_idempotent_for_healthy_subscriber() -> None:
    source = INIT.read_text(encoding="utf-8")

    assert "subscriber.chip_ids == chip_ids" in source
    assert "subscriber.running" in source
    assert "await subscriber.async_stop()" in source


def test_cold_start_requests_telemetry_immediately_after_suback() -> None:
    source = MQTT.read_text(encoding="utf-8")

    marker = "# Do not depend on a retained/initial status publication"
    assert marker in source
    assert "for chip_id in self._chip_ids:" in source
    assert "ESP_INITIAL_SUBSCRIPTION_SECONDS" in source
    assert "await self._send_telemetry_keepalive(" in source


def test_subscriber_exposes_normalized_chip_ids_for_reconciliation() -> None:
    source = MQTT.read_text(encoding="utf-8")

    assert "def chip_ids(self) -> tuple[str, ...]:" in source
    assert "return self._chip_ids" in source
