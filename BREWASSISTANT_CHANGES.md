# BrewAssistant Grainfather branch

Branch: `brewassistant-grainfather`  
Upstream: `fidley/grainfather_integration:main`  
Initial upstream base: `f57106cb0f126b387257d47ad6a9d12e7f02b5c7`

This branch carries BrewAssistant-oriented Grainfather integration work while the fork's
`main` remains an upstream-tracking branch.

## Design goals

- Preserve the upstream Home Assistant integration as the architectural base.
- Add verified Grainfather fermentation-controller telemetry without duplicating it inside BrewAssistant.
- Keep controller writes disabled until they are independently verified on live hardware.
- Expose normalized Home Assistant entities/services that BrewAssistant can consume.
- Keep secrets such as Grainfather credentials and Particle tokens out of Home Assistant state and logs.

## Phase 1 — verified Grainfather REST/history telemetry

Implemented:

- Parse `target_temperature` from fermentation-device history.
- Keep history points that contain a target even when temperature and gravity are absent.
- Expose a read-only fermentation-device Target Temperature sensor.
- Include target temperature in recent history attributes.
- Create the target sensor only for controller-linked devices or devices with observed target history.
- Add parser regression tests.
- Add BrewAssistant branch CI with compile, Ruff and pytest.

The real GF30 used for BrewAssistant development has already shown
`target_temperature` in Grainfather history, so this phase is based on field-observed
2026 data rather than historical controller assumptions.

## Live Particle probe result — 2026-10-01

Field test against the linked GF30 account returned:

```text
Particle sessions returned by Grainfather: 0
RESULT: no Particle session was returned.
```

Conclusion:

- the historical Grainfather → Particle token path is **not available for this current linked GF30 account**;
- `wardsimon/gfFermentation` and `mossman/grainfather_exporter` remain useful protocol/history references only;
- current development should not depend on Particle runtime access;
- realtime/status research moves to the current ESP/Grainfather backend;
- REST/history remains the verified read-only fallback.

## Phase 2 — optional controller realtime discovery

Historical projects `wardsimon/gfFermentation` and `mossman/grainfather_exporter`
show that Grainfather previously issued Particle sessions through
`/api/particle/tokens` and exposed controller variables such as:

- `temp`
- `targetTemp`
- `heatStatus`
- `coolStatus`
- online/status metadata

The current GF30 Grainfather equipment record is ESP-linked and has no legacy
`particle_device_id`, so this transport is not assumed to exist.

Use `tools/probe_particle_readonly.py` to test the currently authenticated account.
The probe performs GET/read operations only after Grainfather login; it does not call
`setTarget`, `controlFermenting`, `highActivity`, or any other Particle function.

If Particle realtime is confirmed, the next implementation phase may normalize
actual temperature, target, heating, cooling, online and controller status with
explicit freshness/fallback rules. If it is absent, research moves to the current
ESP/Grainfather backend while REST/history remains the supported read path.

## Phase 3 — current ESP/MQTT runtime discovery

Reverse engineering of the current Grainfather Android app (5.7.0) on 2026-10-01
confirmed that modern linked controllers use a Grainfather ESP/MQTT runtime rather
than the legacy Particle-session path.

Verified application behavior:

- accessory/controller discovery: `GET /api/accessory-devices?api_token=...`;
- primary MQTT broker default: `mqtt.grainfather.com`;
- fallback broker: `mqtt2.grainfather.com`;
- device topic prefix: `devices/<chip_id>/`;
- the app subscribes to `#`, `meta`, `status` and `config` below that prefix;
- GF30/WFC event payloads expose `data.temp`, `data.target`,
  `data.heatStatus`, `data.coolStatus`, controller settings and session state;
- retained `meta` payloads expose firmware/error/network metadata.

Implemented read-only groundwork:

- fermentation-device records now retain `esp_chip_id` and `particle_device_id`;
- snapshots optionally discover accessory devices without making that endpoint a
  hard dependency for the stable REST/history path;
- fermentation sensor attributes identify the discovered controller transport;
- `esp_runtime.py` contains pure topic helpers and parsers for incoming
  `events`, `meta` and `status` payloads;
- `tools/probe_esp_accessory_readonly.py` field-tests REST-side controller/accessory
  discovery without opening MQTT;
- there is deliberately **no MQTT publish function** in the integration.

MQTT authentication is only partially verified from the app bundle. The app uses
the Grainfather user ID as the MQTT username and derives the password by applying
webpack module 2517 to `<user_id>BEVIE`. The exact hash implementation has not yet
been independently identified, so a live MQTT client is intentionally not enabled.

REST/history remains the supported fallback and does not depend on MQTT.

## Write boundary

No physical-controller write is approved by this branch at this stage.

Future target writes must use explicit Home Assistant/BrewAssistant authorization,
post-write readback and visible failure handling. BrewAssistant must never directly
control the GF30 heater or cooling circulation pump merely because historical
repositories contain those function names.
