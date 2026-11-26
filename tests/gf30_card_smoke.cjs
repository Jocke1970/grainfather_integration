// Dependency-free Home Assistant card smoke tests for GitHub Actions (Node 20+).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath = path.resolve(
  __dirname, '../custom_components/grainfather/www/grainfather-gf30-control-card.js',
);
const source = fs.readFileSync(sourcePath, 'utf8')
  .replace(/^import .*?;\r?\n/, '');

const html = (strings, ...values) => strings.reduce(
  (result, chunk, index) => result + chunk + (index < values.length ? String(values[index]) : ''),
  '',
);
const context = {
  LitElement: class {},
  css: html,
  html,
  nothing: '',
  customElements: { define() {} },
  window: {},
};
vm.runInNewContext(source + '\nthis.Card = GrainfatherGf30ControlCard;', context, {
  filename: sourcePath,
});
const Card = context.Card;

function fixture(options = {}) {
  const attrs = {
    grainfather_entity_type: 'fermentation_device',
    grainfather_measurement: 'temperature',
    device_id: 92245,
    controller_online: true,
    live_mqtt_transport_connected: true,
    live_mqtt_subscribed: true,
    live_mqtt_event_fresh: true,
  };
  const states = {
    'sensor.gf_temp': { state: '21.4', attributes: attrs },
    'sensor.gf_target': { state: '17.0', attributes: { device_id: 92245 } },
    'binary_sensor.gf_online': { state: 'on', attributes: {} },
  };
  if (options.tempAttributes) Object.assign(attrs, options.tempAttributes);
  if (options.targetAttributes) {
    Object.assign(states['sensor.gf_target'].attributes, options.targetAttributes);
  }
  if (options.controllerState) states['binary_sensor.gf_online'].state = options.controllerState;
  if (options.targetState) states['sensor.gf_target'].state = options.targetState;
  if (options.missingOnline) delete states['binary_sensor.gf_online'];
  if (options.missingTarget) delete states['sensor.gf_target'];

  const card = new Card();
  card.setConfig({
    name: 'GF30',
    temperature_entity: 'sensor.gf_temp',
    target_entity: 'sensor.gf_target',
    online_entity: 'binary_sensor.gf_online',
    heating_entity: 'binary_sensor.gf_heating',
    cooling_entity: 'binary_sensor.gf_cooling',
    control_active_entity: 'binary_sensor.gf_active',
  });
  const calls = [];
  card.hass = {
    states,
    callService: async (...args) => { calls.push(args); },
  };
  return { card, calls };
}

async function main() {
  let test = fixture();
  assert.equal(test.card._controllerStatus().key, 'online');
  assert.equal(test.card._mqttStatus().key, 'online');
  assert.equal(test.card._canWrite(), true);
  assert.match(test.card.render(), /Connection diagnostics \/ entity mapping/);
  assert.match(test.card.render(), /MQTT LIVE/);

  test = fixture({ controllerState: 'off' });
  assert.equal(test.card._controllerStatus().key, 'offline');
  assert.equal(test.card._canWrite(), false);
  assert.match(test.card.render(), /Controller unavailable/);
  assert.match(test.card.render(), /Connection diagnostics/);
  await test.card._apply();
  assert.equal(test.calls.length, 0);

  test = fixture({ tempAttributes: { live_mqtt_transport_connected: false } });
  assert.equal(test.card._mqttStatus().key, 'offline');
  assert.equal(test.card._canWrite(), false);
  await test.card._apply();
  assert.equal(test.calls.length, 0);

  test = fixture({ tempAttributes: { live_mqtt_subscribed: false } });
  assert.equal(test.card._mqttStatus().label, 'MQTT NOT SUBSCRIBED');
  assert.equal(test.card._canWrite(), false);

  test = fixture({ tempAttributes: { live_mqtt_event_fresh: false } });
  assert.equal(test.card._mqttStatus().label, 'MQTT STALE');
  assert.equal(test.card._canWrite(), false);
  assert.match(test.card.render(), /REST\/history fallback/);

  test = fixture({ missingOnline: true });
  assert.equal(test.card._controllerStatus().key, 'unknown');
  assert.equal(test.card._canWrite(), false);
  assert.match(test.card.render(), /binary_sensor.gf_online \(not found\)/);

  test = fixture({ missingTarget: true });
  assert.equal(test.card._canWrite(), false);
  assert.match(test.card.render(), /sensor.gf_target \(not found\)/);

  test = fixture({ targetState: 'unknown' });
  assert.equal(test.card._currentTarget(), null);
  assert.equal(test.card._canWrite(), false);

  test = fixture({ targetAttributes: { device_id: 12345 } });
  assert.equal(test.card._canWrite(), false);
  assert.match(test.card.render(), /device ID mismatch/);

  test = fixture();
  test.card._config.device_id = 12345;
  assert.equal(test.card._canWrite(), false);
  assert.match(test.card.render(), /Configured device ID 12345 differs/);

  test = fixture();
  test.card.hass.states['sensor.gf_temp'].state = 'unknown';
  assert.equal(test.card._canWrite(), false);

  test = fixture();
  test.card._setDraft(21.5);
  test.card._arm();
  assert.equal(test.card._armed, true);
  await test.card._apply();
  assert.equal(test.calls.length, 1);
  assert.equal(test.calls[0][0], 'grainfather');
  assert.equal(test.calls[0][1], 'set_controller_target_temperature');
  assert.equal(test.calls[0][2].confirm, true);
  assert.equal(test.calls[0][2].device_id, 92245);
  assert.equal(test.calls[0][2].temperature, 21.5);

  console.log('GF30 card: 12 smoke checks passed');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
