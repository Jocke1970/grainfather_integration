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

## Write boundary

No physical-controller write is approved by this branch at this stage.

Future target writes must use explicit Home Assistant/BrewAssistant authorization,
post-write readback and visible failure handling. BrewAssistant must never directly
control the GF30 heater or cooling circulation pump merely because historical
repositories contain those function names.
