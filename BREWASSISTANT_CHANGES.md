# BrewAssistant Grainfather branch

Branch: `brewassistant-grainfather`  
Upstream: `fidley/grainfather_integration:main`  
Initial upstream base: `f57106cb0f126b387257d47ad6a9d12e7f02b5c7`


## Current verified baseline — 2026-10-02

Current BrewAssistant release: **2026.10.0b1**.

Versioning transition:

- `v0.1.5-ba.8` is the final release in the original experimental series;
- `2026.10.0b1` starts the calendar-based beta series;
- subsequent October betas use `2026.10.0b2`, `2026.10.0b3`, and so on;
- the intended stable October baseline is `2026.10.0`.

Field-verified on the current ESP-linked GF30:

- TLS MQTT on `mqtt.grainfather.com:8883`;
- live temperature/target/heating/cooling/controller state without opening the app;
- proactive command 23 telemetry keepalive;
- fresh MQTT precedence with REST/history fallback;
- field-captured target command `{"command":0,"value":"21.00"}`;
- supervised target service with explicit confirmation and fresh MQTT readback;
- live GF30 sensor/binary-sensor entity coverage;
- Home Assistant async-safe cached TLS context.

Current outbound MQTT boundary is exactly:

- command 0 — supervised target temperature;
- command 23 — telemetry keepalive (15/120);
- all other controller writes unavailable.

Important diagnostics caveat:

- firmware/RSSI/OTA/session fields are optional controller observations and may be
  null/unknown;
- a null/unknown OTA field must **not** be interpreted as an available firmware update;
- `MQTT Event Subscription Value` is diagnostic raw data and is not treated as a
  countdown because its exact semantics remain unverified.


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

Implemented live telemetry runtime:

- fermentation-device records retain `esp_chip_id` and `particle_device_id`;
- snapshots optionally discover accessory devices without making that endpoint a
  hard dependency for the stable REST/history path;
- fermentation sensor attributes identify the discovered controller transport;
- `esp_runtime.py` contains topic helpers, payload parsers and an in-memory
  live-state cache;
- `tools/probe_esp_accessory_readonly.py` field-tests REST-side controller/accessory
  discovery without opening MQTT;
- webpack source mapping identifies module 2517 as
  `./shared/helpers/crypto-js/md5.js`, verifying that the current app uses the
  Grainfather user ID as MQTT username and lowercase hexadecimal
  `MD5("<user_id>BEVIE")` as MQTT password;
- `esp_mqtt.py` implements a minimal MQTT 3.1.1 telemetry client;
- live Home Assistant testing verified `mqtt.grainfather.com:8883` over TLS,
  successful MQTT authentication and SUBACK result codes `[0, 0, 0, 0]`;
- the controller publishes retained `status=true`, but current live `events`
  only become active after the client mirrors the Grainfather app's telemetry
  keepalive handshake;
- the only controller-bound MQTT payload implemented by this phase is
  `ESP_SUBSCRIPTION_TIME` command 23, with the exact app-observed values 15
  seconds for initial activation and 120 seconds for refresh;
- incoming live observations can expose broker/device online state, temperature,
  target, heating, cooling, RSSI, control mode/status and controller metadata on
  the existing fermentation entities.

The app behavior was field-verified in Home Assistant: opening the Grainfather app
caused the GF30 to publish live `events`, which Home Assistant decoded as live
temperature 22.8 C, target 22 C, heating/cooling false and control mode 0. The app
source shows that `status=true` triggers `keepActive()`, while event payloads
with `data.subTime < 25` trigger `keepActive(120)`.

The telemetry client mirrors only that subscription-time behavior. There is no
generic command publisher exposed to Home Assistant or BrewAssistant.

REST/history remains the supported fallback and does not depend on MQTT.

## Write boundary

The only approved outbound MQTT write in this phase is the telemetry subscription
handshake:

- topic: `devices/<chip_id>/command`;
- command: `23` (`ESP_SUBSCRIPTION_TIME`);
- allowed values: `"15"` and `"120"`;
- purpose: request/refresh controller telemetry only.

No target-temperature, control-mode, heater, cooling or profile write is approved
by this branch at this stage. Those future writes must use explicit
Home Assistant/BrewAssistant authorization, post-write readback and visible
failure handling. BrewAssistant must never directly control the GF30 heater or
cooling circulation pump merely because historical repositories contain those
function names.


## Phase 4 — GF30 MQTT hardening (v0.1.5-ba.5)

Field testing of `ba.4` verified that Home Assistant can activate GF30 live
telemetry without opening the Grainfather app. It also exposed three hardening
requirements that are addressed in `ba.5`:

- MQTT controller connections are TLS-only on port 8883; plaintext 1883
  fallbacks have been removed.
- wildcard echoes from `devices/<chip_id>/command` no longer advance inbound
  telemetry timestamps or masquerade as controller data.
- controller `events` have their own timestamp/freshness state. Fresh MQTT
  events take precedence over REST/history, while stale MQTT automatically
  falls back to REST/history.
- telemetry keepalive is now refreshed proactively before the 120-second
  subscription expires, instead of relying only on a future `subTime < 25`
  event to trigger the next refresh.
- controller-offline status cancels the proactive refresh schedule.

The outbound write boundary is unchanged: only command 23 with values 15/120
is permitted. There is still no target-temperature, mode, heater, cooling or
profile write path.


## Phase 5 — passive target-command discovery (v0.1.5-ba.6)

No target-temperature write is implemented in this release.

The MQTT wildcard subscription can observe commands published by another client,
such as the official Grainfather app. To identify the modern GF30 target protocol
without guessing, the integration now passively records non-keepalive command
observations:

- command 23 telemetry keepalives are explicitly ignored by the observer;
- command observations never advance telemetry/event freshness;
- only bounded diagnostic fields are exposed: timestamp, command id, value,
  compact payload and observation count;
- malformed command payloads are ignored safely;
- the observer never publishes or executes the observed command.

This release is intended for a controlled field capture: with Home Assistant
connected, change only the GF30 target temperature once in the official app and
inspect the resulting observer attributes. The captured command will then be
used to design a separately bounded supervised target-write path with readback.


## Phase 6 — supervised GF30 target write and live entities (v0.1.5-ba.7)

Field capture with the official Grainfather app verified the modern GF30 target
command on 2026-10-02:

```json
{"command":0,"value":"21.00"}
```

The same controller immediately reported `data.target = 21` in a fresh MQTT
event. Based on that field evidence, `ba.7` adds one deliberately bounded
controller write:

- Home Assistant service: `grainfather.set_controller_target_temperature`;
- requires fermentation `device_id`, Celsius `temperature`, and
  `confirm: true`;
- publishes only command 0 with a two-decimal target string;
- target writes are technically bounded to 0-40 °C;
- the service requires a newer MQTT event whose target matches the request;
- mismatch/timeout is reported as failure instead of assuming success;
- write request/result/readback diagnostics are exposed on the fermentation
  sensor attributes.

The outbound MQTT boundary is now exactly:

- command 0: supervised target temperature only;
- command 23: telemetry subscription maintenance only;
- all other controller commands remain unavailable.

This release also promotes already decoded live GF30 values into Home Assistant
entities. In addition to current temperature, target temperature and gravity,
linked ESP controllers expose binary entities for controller online, heating,
cooling, control active, managed mode, lower-temperature alert and OTA
availability, plus diagnostic sensors for RSSI, hysteresis, temperature offset,
control-mode code, units code, firmware/error/OTA status and controller
session/stage fields.

No heater, cooling, control-mode, profile, hysteresis, calibration, OTA or
session write path is implemented.


## Phase 7 — async-safe MQTT TLS setup (v0.1.5-ba.8)

Home Assistant 2026 / Python 3.14 reports `ssl.create_default_context()` as a
blocking event-loop operation because loading default certificate paths performs
disk I/O. The Grainfather MQTT client now uses Home Assistant's cached generic
SSL helper from `homeassistant.util.ssl.client_context`.

This removes the blocking `load_default_certs` /
`set_default_verify_paths` calls from the integration event loop while keeping
the same TLS-only MQTT transport and certificate verification behavior.


## Phase 8 — calendar-version beta baseline (2026.10.0b1)

This release starts the new BrewAssistant calendar-versioning scheme.

Included baseline maintenance:

- version changes from the experimental `0.1.5-ba.x` sequence to
  `2026.10.0b1`;
- manifest documentation URL now points to
  `Jocke1970/grainfather_integration`;
- manifest issue tracker now points to
  `Jocke1970/grainfather_integration/issues`;
- README and BrewAssistant development notes are synchronized with the current
  GF30 MQTT/readback architecture and safety boundary;
- all functionality from `v0.1.5-ba.8` remains the functional baseline.

No new controller command is introduced by this versioning/metadata release.
The outbound MQTT boundary remains command 0 for supervised target temperature
and command 23 for telemetry keepalive only.
