#!/usr/bin/env python3
"""Read-only Grainfather/Particle capability probe.

This utility authenticates to Grainfather, asks Grainfather for any Particle sessions
associated with the account, and inspects Particle device metadata/known variables.

It deliberately performs no Particle function calls and never prints access or refresh
tokens.
"""

from __future__ import annotations

import getpass
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GRAINFATHER_BASE = "https://community.grainfather.com/api"
PARTICLE_BASE = "https://api.particle.io/v1/devices"

EXPECTED_VARIABLES = ("temp", "targetTemp", "heatStatus", "coolStatus")


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

    request = Request(
        url,
        data=body,
        headers=request_headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {url} failed with HTTP {err.code}: {detail[:300]}"
        ) from err
    except URLError as err:
        raise RuntimeError(f"{method} {url} failed: {err.reason}") from err


def _grainfather_login(email: str, password: str) -> str:
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
    return token


def _get_particle_sessions(grainfather_token: str) -> list[dict[str, Any]]:
    payload = _request_json(
        "GET",
        f"{GRAINFATHER_BASE}/particle/tokens",
        headers={"Authorization": f"Bearer {grainfather_token}"},
    )
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise RuntimeError(
            f"Unexpected /particle/tokens response type: {type(payload).__name__}"
        )
    return [item for item in payload if isinstance(item, dict)]


def _get_particle_devices(access_token: str) -> list[dict[str, Any]]:
    payload = _request_json(
        "GET",
        PARTICLE_BASE,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if not isinstance(payload, list):
        raise RuntimeError(
            f"Unexpected Particle devices response type: {type(payload).__name__}"
        )
    return [item for item in payload if isinstance(item, dict)]


def _read_particle_variable(
    access_token: str,
    device_id: str,
    variable: str,
) -> Any:
    payload = _request_json(
        "GET",
        f"{PARTICLE_BASE}/{device_id}/{variable}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if isinstance(payload, dict):
        return payload.get("result")
    return None


def _safe_device_summary(device: dict[str, Any]) -> dict[str, Any]:
    variables = device.get("variables")
    functions = device.get("functions")

    return {
        "id": device.get("id"),
        "name": device.get("name"),
        "product_id": device.get("product_id"),
        "online": device.get("online"),
        "status": device.get("status"),
        "last_heard": device.get("last_heard"),
        "variables": sorted(variables) if isinstance(variables, dict) else variables,
        "functions": sorted(functions) if isinstance(functions, list) else functions,
    }


def main() -> int:
    print("BrewAssistant Grainfather — read-only Particle capability probe")
    print("No controller function calls are made and tokens are never printed.\n")

    email = input("Grainfather email: ").strip()
    password = getpass.getpass("Grainfather password: ")

    if not email or not password:
        print("Email and password are required.", file=sys.stderr)
        return 2

    try:
        grainfather_token = _grainfather_login(email, password)
        sessions = _get_particle_sessions(grainfather_token)
    except RuntimeError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(f"Particle sessions returned by Grainfather: {len(sessions)}")

    if not sessions:
        print(
            "RESULT: no Particle session was returned. "
            "Do not assume the current GF30 uses the legacy Particle path."
        )
        return 0

    total_devices = 0
    matched_variables = False

    for session_index, session in enumerate(sessions, start=1):
        access_token = session.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            print(f"Session {session_index}: no usable access_token field")
            continue

        expires_at = session.get("expires_at")
        print(
            f"\nSession {session_index}: token present"
            + (f", expires_at={expires_at}" if expires_at else "")
        )

        try:
            devices = _get_particle_devices(access_token)
        except RuntimeError as err:
            print(f"  Particle device enumeration failed: {err}")
            continue

        print(f"  Devices visible through this session: {len(devices)}")
        total_devices += len(devices)

        for device_index, device in enumerate(devices, start=1):
            summary = _safe_device_summary(device)
            print(f"\n  Device {device_index}:")
            print(json.dumps(summary, indent=2, sort_keys=True))

            device_id = device.get("id")
            variables = device.get("variables")
            if not isinstance(device_id, str) or not isinstance(variables, dict):
                continue

            readable = [name for name in EXPECTED_VARIABLES if name in variables]
            if not readable:
                continue

            matched_variables = True
            print("    Read-only values:")
            for variable in readable:
                try:
                    value = _read_particle_variable(access_token, device_id, variable)
                    print(f"      {variable}: {value!r}")
                except RuntimeError as err:
                    print(f"      {variable}: ERROR ({err})")

    print("\nSummary")
    print(f"  Particle sessions: {len(sessions)}")
    print(f"  Particle devices: {total_devices}")
    print(f"  Expected GF controller variables found: {matched_variables}")

    if total_devices == 0:
        print(
            "RESULT: Particle credentials exist, but no device was visible. "
            "Further identity/backend research is required."
        )
    elif matched_variables:
        print(
            "RESULT: legacy-style Grainfather controller telemetry is visible read-only. "
            "This is sufficient evidence to design a Home Assistant realtime adapter."
        )
    else:
        print(
            "RESULT: Particle device(s) are visible, but the expected legacy controller "
            "variables were not advertised."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
