# GF30 ESP/MQTT mixed-case identifier compatibility (development branch)

## External beta observation

A tester with two GF30 units (ESP Wi-Fi, firmware 8.4) reported:

- Grainfather REST/history temperature and target values work correctly.
- Both controllers are discovered through the Grainfather API, with accessories found.
- ESP controller identifiers use mixed case, with a `prd-WFC-B12-...` prefix.
- MQTT broker CONNACK succeeded but all eight SUBACK grants (four per controller)
  were `0x80` (subscription refused); no live events were received.
- Device/controller status in HA was unknown; no target-write test was performed.

## Confirmed source-code defect

The integration lowercased `esp_chip_id` using `.casefold()` before building
the MQTT SUBSCRIBE topic filters. MQTT topic filters are case-sensitive (MQTT
3.1.1, OASIS section 4.7). This is harmless for purely hexadecimal lowercase
IDs but changes IDs like `prd-WFC-B12-...`.

## Development fix

- Preserve exact API ESP chip ID casing for MQTT SUBSCRIBE, telemetry keepalive
  PUBLISH and supervised command-0 PUBLISH topics.
- Continue to use case-insensitive internal runtime state keys, with an explicit
  mapping back to the exact API identifier for outgoing topics.
- Reject ambiguous ID sets that differ only by case.
- Attribute SUBACK codes to each controller (four per device) rather than
  presenting the full combined result on both devices.
- Add regression tests for two simultaneous controllers, mixed-case topics,
  and status-triggered keepalive.

## Validation required before release

1. Python compile, pytest, Ruff, Hassfest and HACS Validate for the exact release SHA.
2. Install only via the next HACS prerelease, never straight from dev.
3. With the controllers already operating normally, check MQTT SUBACK,
   `live_mqtt_transport_connected`, `live_mqtt_subscribed` and
   `live_mqtt_event_fresh` for each GF30.
4. Require confirmed incoming live events before testing any target writes.

The observed `0x80` results establish a subscription rejection. They do
**not** prove casing was the broker's rejection reason. If failures persist
with exact-case topics, investigate per-account broker authorization, actual
device topic namespace and app protocol behavior without sending control
commands or logging credentials.

## Safety

No new controller commands. No changes to target readback requirements,
timeout, permissible temperatures or authorization boundaries. This fix is
not yet released or field-verified.
