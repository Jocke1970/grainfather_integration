#!/usr/bin/env python3
"""Read-only Grainfather ESP accessory discovery probe.

The probe authenticates to Grainfather, reads fermentation equipment and accessory
metadata, and correlates controller chip IDs. It does not open MQTT, subscribe to
topics, or publish controller commands. Tokens and passwords are never printed.
"""

from __future__ import annotations

import getpass
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

GRAINFATHER_BASE = "https://community.grainfather.com/api"


def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> Any:
    body = None
    request_headers = {"Accept": "application/json", **(headers or {})}

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = Request(url, data=body, headers=request_headers, method=method)

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {url.split('?')[0]} failed with HTTP {err.code}: {detail[:300]}"
        ) from err
    except URLError as err:
        raise RuntimeError(f"{method} {url.split('?')[0]} failed: {err.reason}") from err


def _grainfather_login(email: str, password: str) -> tuple[str, str | None]:
    payload = _request_json(
        "POST",
        f"{GRAINFATHER_BASE}/auth/login",
        payload={"email": email, "password": password},
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Grainfather login response")

    token = payload.get("api_token") or payload.get("accessToken") or payload.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Grainfather login response did not contain an API token")

    user_id = payload.get("id") or payload.get("userId")
    return token, str(user_id) if user_id is not None else None


def _get_fermentation_devices(token: str) -> list[dict[str, Any]]:
    payload = _request_json(
        "GET",
        f"{GRAINFATHER_BASE}/equipment/fermentation-devices",
        headers={"Authorization": f"Bearer {token}"},
    )
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected fermentation-device response")
    return [item for item in payload if isinstance(item, dict)]


def _get_accessory_devices(token: str) -> list[dict[str, Any]]:
    query = urlencode({"api_token": token})
    payload = _request_json(
        "GET",
        f"{GRAINFATHER_BASE}/accessory-devices?{query}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("devices") or []
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected accessory-device response")
    return [item for item in payload if isinstance(item, dict)]


def _safe_accessory_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "chip_id": item.get("chip_id") or item.get("chipId"),
        "device_type_id": item.get("device_type_id") or item.get("deviceTypeId"),
        "is_particle_chip": item.get("is_particle_chip")
        if "is_particle_chip" in item
        else item.get("isParticleChip"),
        "updated_at": item.get("updated_at") or item.get("updatedAt"),
    }


def main() -> int:
    print("BrewAssistant Grainfather — read-only ESP accessory probe")
    print("No MQTT connection or controller command is made; tokens are never printed.\n")

    email = input("Grainfather email: ").strip()
    password = getpass.getpass("Grainfather password: ")
    if not email or not password:
        print("Email and password are required.", file=sys.stderr)
        return 2

    try:
        token, user_id = _grainfather_login(email, password)
        fermentation_devices = _get_fermentation_devices(token)
        accessory_devices = _get_accessory_devices(token)
    except RuntimeError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(f"Grainfather user ID available: {user_id is not None}")
    print(f"Fermentation devices: {len(fermentation_devices)}")
    print(f"Accessory devices: {len(accessory_devices)}")

    accessories_by_chip = {
        str(item.get("chip_id") or item.get("chipId")).casefold(): item
        for item in accessory_devices
        if item.get("chip_id") or item.get("chipId")
    }

    for device in fermentation_devices:
        esp_chip_id = device.get("esp_chip_id") or device.get("espChipId")
        particle_device_id = device.get("particle_device_id") or device.get("particleDeviceId")
        if not esp_chip_id and not particle_device_id:
            continue

        print("\nFermentation controller:")
        print(
            json.dumps(
                {
                    "id": device.get("id"),
                    "name": device.get("name"),
                    "is_controller_linked": device.get("is_controller_linked"),
                    "esp_chip_id": esp_chip_id,
                    "particle_device_id_present": bool(particle_device_id),
                },
                indent=2,
                sort_keys=True,
            )
        )

        accessory = (
            accessories_by_chip.get(str(esp_chip_id).casefold()) if esp_chip_id else None
        )
        print("Matching accessory:")
        print(
            json.dumps(
                _safe_accessory_summary(accessory) if accessory else None,
                indent=2,
                sort_keys=True,
            )
        )

    print("\nAccessory inventory:")
    for item in accessory_devices:
        print(json.dumps(_safe_accessory_summary(item), sort_keys=True))

    print(
        "\nRESULT: REST-side ESP discovery completed. "
        "No live MQTT capability was exercised."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
