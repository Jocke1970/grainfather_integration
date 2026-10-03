# Home Assistant Grainfather Integration

> [!IMPORTANT]
> This branch is the BrewAssistant-oriented Grainfather variant.
> Development branch: `brewassistant-grainfather`.
> The fork's `main` remains upstream-tracking.
> See [BREWASSISTANT_CHANGES.md](BREWASSISTANT_CHANGES.md) for verified protocol research,
> safety boundaries and release-by-release development notes.

Custom Home Assistant integration for Grainfather cloud data and modern GF30 ESP/MQTT
telemetry, including brew sessions, fermentation devices, live controller state and a
bounded supervised target-temperature write path.

## Current BrewAssistant baseline

Current development release: **2026.10.0b1**  
Release label: **2026.10 Beta 1**

Verified on a real Grainfather GF30 / ESP-linked controller:

- Grainfather REST remains the stable cloud/history fallback.
- Live controller telemetry uses Grainfather MQTT over TLS/8883.
- Primary broker: `mqtt.grainfather.com:8883/tls`.
- Secondary TLS broker: `mqtt2.grainfather.com:8883/tls`.
- Plaintext MQTT fallback is intentionally disabled.
- Home Assistant can activate and maintain live GF30 telemetry without the official app.
- Fresh MQTT data takes precedence over REST/history.
- MQTT event freshness is tracked independently from outbound command echoes.
- Home Assistant's cached SSL helper is used to avoid blocking certificate loading in the event loop.
- The modern GF30 target-temperature command was field-captured from the official app:
  `{"command":0,"value":"21.00"}`.
- A supervised Home Assistant service can set controller target temperature and requires
  a fresh MQTT readback before success is reported.

## Safety boundary

Outbound controller MQTT is deliberately restricted.

Approved commands:

- **Command 0** — supervised GF30 target temperature only.
- **Command 23** — telemetry subscription maintenance only, with values `15` and `120`.

There is **no generic MQTT publish API**.

The integration does **not** expose direct writes for:

- heater
- cooling pump / cooling state
- control mode
- hysteresis
- temperature calibration / offset
- OTA
- profiles
- controller session state

BrewAssistant should treat the GF30 controller as the owner of heater/cooling logic and
only request a target temperature through the supervised service.

## Features

### Brew sessions

- Config flow with Grainfather email/password.
- Brew-session entities for batch, style, gravity, ABV, recipe image and variants.
- Fermentation-step editing through the Grainfather cloud API.
- Session status controls and helpers.
- History data exposed on brew-session attributes.

### Fermentation devices

Core entities include:

- Temperature
- Target Temperature
- Gravity

For ESP-linked GF30 controllers, live entities also include:

Binary sensors:

- Controller Online
- Heating
- Cooling
- Control Active
- Managed Mode
- Lower Temperature Alert Enabled
- OTA Update Available

Sensors:

- Controller RSSI
- Controller Hysteresis
- Controller Temperature Offset
- Control Mode Code
- Units Code
- Firmware Version
- Controller Error Code
- OTA Status Code
- Controller Session ID
- Controller Session Stage
- Controller Stage End Time
- MQTT Event Subscription Value

Not every firmware/controller state publishes every field. Some entities may therefore
remain `unknown` until the controller actually provides a value.

> [!NOTE]
> `OTA Update Available`, firmware, RSSI, OTA status and session fields may be absent/null
> on a given GF30. An unknown/null OTA value is **not evidence that an update is available**.
> The official Grainfather app remains the authoritative user-facing source for whether an
> OTA update is actually being offered.

## Live MQTT architecture

Modern GF30 controllers use a Grainfather ESP/MQTT runtime.

Verified topic prefix:

`devices/<chip_id>/`

The integration subscribes beneath that prefix and consumes:

- `events`
- `status`
- `meta`
- `config`
- `profiles`
- wildcard traffic needed for protocol observation

Live event fields currently decoded include:

- current temperature
- target temperature
- heating
- cooling
- RSSI
- subscription value
- control mode
- control active
- units
- hysteresis
- temperature offset
- lower-temperature-alert state
- session ID
- managed mode
- session stage
- stage end time

Meta payloads can expose:

- firmware version
- controller error code
- SSID
- IP address
- MAC address
- OTA status
- OTA available flag
- RSSI

## Freshness and fallback

The effective temperature/target source is:

1. fresh MQTT event data;
2. REST/history fallback when MQTT is missing or stale.

MQTT event freshness is intentionally separate from generic MQTT traffic, so the
integration's own command publishes do not make stale telemetry appear current.

## Supervised target-temperature service

Service:

`grainfather.set_controller_target_temperature`

Required fields:

- `device_id`
- `temperature`
- `confirm: true`

Example:

```yaml
action: grainfather.set_controller_target_temperature
data:
  device_id: 92245
  temperature: 18.0
  confirm: true
```

Behavior:

1. validates the device and bounded target range;
2. requires a live/online subscribed GF30 MQTT controller;
3. publishes only field-verified command 0;
4. waits for a **new** MQTT event;
5. succeeds only when the reported target matches the requested target;
6. reports mismatch/timeout instead of assuming success.

Current technical write bound: **0–40 °C**.

Write diagnostics are exposed on the fermentation temperature sensor attributes,
including request time/value and readback result/value.

## Service actions

The integration currently registers:

- `grainfather.set_brew_session_status`
- `grainfather.set_fermentation_steps`
- `grainfather.set_fermentation_step_duration`
- `grainfather.clear_fermentation_step_finish_temperature`
- `grainfather.adjust_current_step_temperature`
- `grainfather.adjust_current_step_duration`
- `grainfather.advance_to_next_fermentation_step`
- `grainfather.set_controller_target_temperature`

The brew-session/recipe services operate through Grainfather cloud APIs.
The GF30 controller-target service uses the separately bounded ESP/MQTT path described above.

## Installation

### HACS

1. Open HACS.
2. Add this repository as a custom repository of type `Integration` if required.
3. Install `Grainfather`.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration**.
6. Search for `Grainfather` and enter Grainfather credentials.

### Manual

Copy `custom_components/grainfather` into Home Assistant's `custom_components`
directory, restart Home Assistant and add the Grainfather integration.

## Dashboard / Lovelace

The repository includes custom Grainfather JavaScript cards in
`custom_components/grainfather/www`.

For the current GF30 controller dashboard, BrewAssistant development also uses common
HACS frontend cards such as:

- Mushroom
- Stack In Card
- Expander Card
- Auto Entities

The preferred GF30 dashboard pattern is:

- prominent current + target temperature;
- color-coded Online / Heating / Cooling / Control status;
- temperature history graph;
- controller settings grouped separately from diagnostics;
- dynamic entity lists filtered by the Grainfather GF30 device;
- unknown diagnostics hidden from the normal view while remaining available in a raw/debug expander.


### GF30 Supervised Control Card

The integration now includes `grainfather-gf30-control-card.js`, a dedicated target
control panel that keeps controller writes separate from ordinary number entities.

Example:

```yaml
type: custom:grainfather-gf30-control-card
name: Grainfather GF30
temperature_entity: sensor.grainfather_gf30_temperature
target_entity: sensor.grainfather_gf30_target_temperature
online_entity: binary_sensor.grainfather_gf30_controller_online
heating_entity: binary_sensor.grainfather_gf30_heating
cooling_entity: binary_sensor.grainfather_gf30_cooling
control_active_entity: binary_sensor.grainfather_gf30_control_active
min: 0
max: 40
step: 0.1
```

The card uses a two-stage workflow:

1. choose a new target and arm the change;
2. explicitly apply the target.

Apply calls `grainfather.set_controller_target_temperature` with `confirm: true`.
The card displays backend write/readback state from the temperature entity attributes:
`pending`, `verified`, `mismatch` or `timeout`.

The card never exposes heater, cooling or controller-mode writes.

## REST / cloud data

The Grainfather cloud side still provides:

- brew sessions
- fermentation devices
- fermentation history linked to devices and sessions
- recipe images
- accessory/controller discovery used to map ESP chip IDs

REST/history remains available even when MQTT telemetry is unavailable.

## Historical Particle transport

Older Grainfather projects used Particle sessions and functions such as
`setTarget`. Live testing of the current linked GF30 account returned **no Particle session**.

Particle-era projects remain protocol/history references only and are not a runtime
dependency for this BrewAssistant branch.

## Development and verification

Important development rules:

- Feature work stays on `brewassistant-grainfather`.
- No controller write is added from historical assumptions alone.
- New commands must be independently observed/verified before implementation.
- Controller writes require narrow schemas, visible failure handling and readback where practical.
- Full CI, Ruff and Hassfest must pass before a BrewAssistant release is considered ready for field testing.

Current automated baseline for `2026.10.0b1`:

- 56 tests passing
- Ruff passing
- Hassfest passing

HACS repository validation may still report repository-level metadata issues that are
separate from integration runtime correctness.

## Current limitations

- Grainfather's modern controller protocol is not officially documented for this integration;
  behavior is based on reverse engineering plus live field verification.
- Some MQTT/meta fields are firmware- and state-dependent and may remain unknown.
- Raw control-mode/unit numeric mappings are not yet presented as human-readable enums.
- `MQTT Event Subscription Value` is kept as a diagnostic raw value; it is not treated as
  a countdown because its exact semantics have not been fully established.
- The supervised target-write path is intentionally the only GF30 control write.
- Full Home Assistant end-to-end runtime test coverage is still more limited than parser/client tests.

## Roadmap

1. Field-test supervised target writes from Home Assistant across multiple target changes.
2. Map verified control-mode/unit codes to human-readable values.
3. Continue cataloguing real GF30 event/meta fields across active heating/cooling/session states.
4. Improve the GF30 dashboard and supervised-apply UI.
5. Keep REST/history as the stable fallback while MQTT behavior is hardened further.

## Versioning

The BrewAssistant branch now uses calendar-based prerelease versions:

- `2026.10.0b1`, `2026.10.0b2`, ... for October 2026 betas;
- `2026.10.0` for the corresponding stable baseline;
- patch releases increment the final numeric component when required.

`v0.1.5-ba.8` is the final release in the earlier experimental `ba.x` series.

## BrewAssistant development log

See [BREWASSISTANT_CHANGES.md](BREWASSISTANT_CHANGES.md) for the detailed release and
reverse-engineering history.
