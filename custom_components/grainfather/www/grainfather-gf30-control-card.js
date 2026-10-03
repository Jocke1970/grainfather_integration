import { LitElement, css, html, nothing } from 'https://unpkg.com/lit@3.3.0/index.js?module';

const DEFAULT_MIN = 0;
const DEFAULT_MAX = 40;
const DEFAULT_STEP = 0.5;

function _number(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function _fmtTemp(value) {
  const parsed = _number(value);
  return parsed == null ? '—' : `${parsed.toFixed(1)} °C`;
}

class GrainfatherGf30ControlCard extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
    _draft: { state: true },
    _armed: { state: true },
    _busy: { state: true },
    _error: { state: true },
  };

  static styles = css`
    :host { display: block; }

    ha-card {
      padding: 16px;
      border-radius: 16px;
      overflow: hidden;
    }

    .header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 14px;
    }

    .title {
      font-size: 1.2rem;
      font-weight: 700;
      line-height: 1.2;
    }

    .subtitle {
      margin-top: 3px;
      color: var(--secondary-text-color);
      font-size: .9rem;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 9px;
      border-radius: 999px;
      font-size: .78rem;
      font-weight: 700;
      white-space: nowrap;
      background: color-mix(in srgb, var(--secondary-text-color) 12%, transparent);
    }

    .badge.online {
      color: var(--success-color, #43a047);
      background: color-mix(in srgb, var(--success-color, #43a047) 14%, transparent);
    }

    .badge.offline {
      color: var(--error-color, #db4437);
      background: color-mix(in srgb, var(--error-color, #db4437) 14%, transparent);
    }

    .temperatures {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-bottom: 14px;
    }

    .metric {
      border: 1px solid var(--divider-color);
      border-radius: 14px;
      padding: 12px;
      text-align: center;
      background: color-mix(in srgb, var(--card-background-color) 94%, var(--primary-color));
    }

    .metric-label {
      font-size: .75rem;
      color: var(--secondary-text-color);
      text-transform: uppercase;
      letter-spacing: .04em;
    }

    .metric-value {
      margin-top: 4px;
      font-size: 1.35rem;
      font-weight: 800;
    }

    .metric-value.current { color: var(--success-color, #43a047); }
    .metric-value.target { color: var(--primary-color); }

    .status-row {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      margin-bottom: 16px;
    }

    .status {
      border-radius: 10px;
      padding: 7px 8px;
      text-align: center;
      font-size: .78rem;
      font-weight: 700;
      background: color-mix(in srgb, var(--secondary-text-color) 9%, transparent);
      color: var(--secondary-text-color);
    }

    .status.heat {
      color: var(--warning-color, #ff9800);
      background: color-mix(in srgb, var(--warning-color, #ff9800) 14%, transparent);
    }

    .status.cool {
      color: #2196f3;
      background: color-mix(in srgb, #2196f3 14%, transparent);
    }

    .status.active {
      color: var(--success-color, #43a047);
      background: color-mix(in srgb, var(--success-color, #43a047) 14%, transparent);
    }

    .control {
      border-top: 1px solid var(--divider-color);
      padding-top: 14px;
    }

    .control-label {
      font-size: .82rem;
      font-weight: 700;
      margin-bottom: 8px;
    }

    .target-editor {
      display: grid;
      grid-template-columns: 44px 1fr 44px;
      gap: 8px;
      align-items: stretch;
    }

    button {
      font: inherit;
      cursor: pointer;
    }

    .step-btn {
      border: 1px solid var(--divider-color);
      border-radius: 10px;
      background: var(--card-background-color);
      color: var(--primary-text-color);
      font-size: 1.3rem;
      font-weight: 700;
    }

    .step-btn:disabled,
    .action-btn:disabled {
      opacity: .45;
      cursor: not-allowed;
    }

    input {
      min-width: 0;
      width: 100%;
      box-sizing: border-box;
      border: 1px solid var(--divider-color);
      border-radius: 10px;
      padding: 10px 12px;
      background: var(--card-background-color);
      color: var(--primary-text-color);
      font: inherit;
      font-size: 1.05rem;
      font-weight: 700;
      text-align: center;
      outline: none;
    }

    input:focus {
      border-color: var(--primary-color);
      box-shadow: 0 0 0 1px var(--primary-color);
    }

    .delta {
      margin: 7px 0 12px;
      color: var(--secondary-text-color);
      font-size: .8rem;
      text-align: center;
    }

    .action-btn {
      width: 100%;
      border: 0;
      border-radius: 12px;
      padding: 11px 12px;
      font-weight: 800;
      background: var(--primary-color);
      color: var(--text-primary-color, white);
    }

    .action-btn.confirm {
      background: var(--warning-color, #ff9800);
      color: #111;
    }

    .confirm-box {
      margin-top: 10px;
      padding: 12px;
      border-radius: 12px;
      background: color-mix(in srgb, var(--warning-color, #ff9800) 12%, transparent);
      border: 1px solid color-mix(in srgb, var(--warning-color, #ff9800) 45%, transparent);
    }

    .confirm-title {
      font-weight: 800;
      margin-bottom: 4px;
    }

    .confirm-copy {
      font-size: .84rem;
      color: var(--secondary-text-color);
      margin-bottom: 10px;
    }

    .confirm-actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }

    .cancel-btn {
      border: 1px solid var(--divider-color);
      border-radius: 12px;
      padding: 10px;
      background: transparent;
      color: var(--primary-text-color);
      font-weight: 700;
    }

    .result {
      margin-top: 12px;
      border-radius: 11px;
      padding: 10px 11px;
      font-size: .84rem;
      font-weight: 700;
    }

    .result.pending {
      color: var(--warning-color, #ff9800);
      background: color-mix(in srgb, var(--warning-color, #ff9800) 12%, transparent);
    }

    .result.verified {
      color: var(--success-color, #43a047);
      background: color-mix(in srgb, var(--success-color, #43a047) 12%, transparent);
    }

    .result.mismatch,
    .result.timeout,
    .result.error {
      color: var(--error-color, #db4437);
      background: color-mix(in srgb, var(--error-color, #db4437) 12%, transparent);
    }

    .fineprint {
      margin-top: 10px;
      color: var(--secondary-text-color);
      font-size: .72rem;
      line-height: 1.35;
    }
  `;

  constructor() {
    super();
    this.hass = undefined;
    this._config = {};
    this._draft = null;
    this._armed = false;
    this._busy = false;
    this._error = null;
  }

  setConfig(config) {
    if (!config?.temperature_entity) {
      throw new Error('temperature_entity is required');
    }
    if (!config?.target_entity) {
      throw new Error('target_entity is required');
    }

    this._config = {
      name: 'Grainfather GF30',
      min: DEFAULT_MIN,
      max: DEFAULT_MAX,
      step: DEFAULT_STEP,
      ...config,
    };
    this._draft = null;
    this._armed = false;
    this._busy = false;
    this._error = null;
  }

  getCardSize() {
    return 5;
  }

  _state(entityId) {
    return entityId ? this.hass?.states?.[entityId] : undefined;
  }

  _temperatureState() {
    return this._state(this._config.temperature_entity);
  }

  _targetState() {
    return this._state(this._config.target_entity);
  }

  _currentTarget() {
    return _number(this._targetState()?.state);
  }

  _draftValue() {
    if (this._draft != null) return this._draft;
    return this._currentTarget();
  }

  _deviceId() {
    const configured = _number(this._config.device_id);
    if (configured != null) return configured;
    return _number(this._temperatureState()?.attributes?.device_id);
  }

  _isOn(entityId) {
    return this._state(entityId)?.state === 'on';
  }

  _online() {
    const configured = this._config.online_entity;
    if (configured) return this._state(configured)?.state === 'on';
    return this._temperatureState()?.attributes?.controller_online === true;
  }

  _setDraft(value) {
    const min = Number(this._config.min ?? DEFAULT_MIN);
    const max = Number(this._config.max ?? DEFAULT_MAX);
    const step = Number(this._config.step ?? DEFAULT_STEP);
    const parsed = _number(value);
    if (parsed == null) return;

    const rounded = Math.round(parsed / step) * step;
    this._draft = Math.max(min, Math.min(max, Number(rounded.toFixed(2))));
    this._armed = false;
    this._error = null;
  }

  _nudge(delta) {
    const current = this._draftValue();
    if (current == null) return;
    this._setDraft(current + delta);
  }

  _arm() {
    if (!this._online() || this._busy) return;
    const draft = this._draftValue();
    const current = this._currentTarget();
    if (draft == null || current == null || Math.abs(draft - current) < 0.001) return;
    this._armed = true;
    this._error = null;
  }

  _cancel() {
    this._armed = false;
    this._draft = this._currentTarget();
    this._error = null;
  }

  async _apply() {
    const target = this._draftValue();
    const deviceId = this._deviceId();
    if (!this.hass || target == null || deviceId == null || !this._online()) return;

    this._busy = true;
    this._armed = false;
    this._error = null;

    try {
      await this.hass.callService(
        'grainfather',
        'set_controller_target_temperature',
        {
          device_id: deviceId,
          temperature: target,
          confirm: true,
        },
      );
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._busy = false;
    }
  }

  _result() {
    const attrs = this._temperatureState()?.attributes || {};
    if (this._error) {
      return { state: 'error', text: this._error };
    }

    const result = attrs.target_write_last_result;
    if (!result) return null;

    const requested = attrs.target_write_last_requested_value;
    const readback = attrs.target_write_last_readback_value;

    if (result === 'pending') {
      return { state: 'pending', text: `Väntar på MQTT-readback för ${_fmtTemp(requested)}…` };
    }
    if (result === 'verified') {
      return { state: 'verified', text: `Verifierad av GF30: ${_fmtTemp(readback)}` };
    }
    if (result === 'mismatch') {
      return {
        state: 'mismatch',
        text: `Readback avviker: begärt ${_fmtTemp(requested)}, fick ${_fmtTemp(readback)}`,
      };
    }
    if (result === 'timeout') {
      return { state: 'timeout', text: `Timeout: GF30 verifierade inte ${_fmtTemp(requested)}` };
    }
    return { state: String(result), text: String(result) };
  }

  render() {
    if (!this.hass) return nothing;

    const tempState = this._temperatureState();
    const targetState = this._targetState();
    if (!tempState || !targetState) {
      return html`
        <ha-card>
          <div class="result error">
            Kan inte hitta temperature_entity eller target_entity.
          </div>
        </ha-card>
      `;
    }

    const currentTemp = _number(tempState.state);
    const currentTarget = this._currentTarget();
    const draft = this._draftValue();
    const deviceId = this._deviceId();
    const online = this._online();

    const heating = this._isOn(this._config.heating_entity);
    const cooling = this._isOn(this._config.cooling_entity);
    const controlActive = this._isOn(this._config.control_active_entity);

    const changed = (
      draft != null
      && currentTarget != null
      && Math.abs(draft - currentTarget) >= 0.001
    );

    const delta = (
      draft != null && currentTarget != null
        ? draft - currentTarget
        : null
    );

    const result = this._result();
    const step = Number(this._config.step ?? DEFAULT_STEP);

    return html`
      <ha-card>
        <div class="header">
          <div>
            <div class="title">${this._config.name}</div>
            <div class="subtitle">Supervised target control</div>
          </div>
          <div class="badge ${online ? 'online' : 'offline'}">
            <ha-icon icon="${online ? 'mdi:lan-connect' : 'mdi:lan-disconnect'}"></ha-icon>
            ${online ? 'ONLINE' : 'OFFLINE'}
          </div>
        </div>

        ${!online ? nothing : html`
        <div class="temperatures">
          <div class="metric">
            <div class="metric-label">Aktuell</div>
            <div class="metric-value current">${_fmtTemp(currentTemp)}</div>
          </div>
          <div class="metric">
            <div class="metric-label">Target</div>
            <div class="metric-value target">${_fmtTemp(currentTarget)}</div>
          </div>
        </div>

        <div class="status-row">
          <div class="status ${heating ? 'heat' : ''}">
            ${heating ? '🔥 Värmer' : 'Värme av'}
          </div>
          <div class="status ${cooling ? 'cool' : ''}">
            ${cooling ? '❄️ Kyler' : 'Kyla av'}
          </div>
          <div class="status ${controlActive ? 'active' : ''}">
            ${controlActive ? '✓ Kontroll' : 'Kontroll av'}
          </div>
        </div>

        <div class="control">
          <div class="control-label">Ny target</div>

          <div class="target-editor">
            <button
              class="step-btn"
              ?disabled=${this._busy || draft == null}
              @click=${() => this._nudge(-step)}
              aria-label="Sänk target"
            >−</button>

            <input
              type="number"
              .value=${draft == null ? '' : String(draft)}
              min=${this._config.min}
              max=${this._config.max}
              step=${step}
              ?disabled=${this._busy}
              @input=${(event) => this._setDraft(event.target.value)}
            />

            <button
              class="step-btn"
              ?disabled=${this._busy || draft == null}
              @click=${() => this._nudge(step)}
              aria-label="Höj target"
            >+</button>
          </div>

          <div class="delta">
            ${delta == null
              ? nothing
              : Math.abs(delta) < 0.001
                ? 'Oförändrad'
                : `${delta > 0 ? '+' : ''}${delta.toFixed(1)} °C från nuvarande target`}
          </div>

          ${!this._armed
            ? html`
                <button
                  class="action-btn"
                  ?disabled=${this._busy || !online || !changed || deviceId == null}
                  @click=${this._arm}
                >
                  ${this._busy ? 'Väntar på GF30…' : 'ARMERA TARGETÄNDRING'}
                </button>
              `
            : html`
                <div class="confirm-box">
                  <div class="confirm-title">Bekräfta target ${_fmtTemp(draft)}</div>
                  <div class="confirm-copy">
                    GF30 kommer att få ett enda verifierat command 0.
                    Tjänsten lyckas först efter färsk MQTT-readback.
                  </div>
                  <div class="confirm-actions">
                    <button class="cancel-btn" @click=${this._cancel}>Avbryt</button>
                    <button class="action-btn confirm" @click=${this._apply}>
                      APPLY ${_fmtTemp(draft)}
                    </button>
                  </div>
                </div>
              `}

          ${result
            ? html`<div class="result ${result.state}">${result.text}</div>`
            : nothing}

          <div class="fineprint">
            Device ID: ${deviceId ?? '—'} · Tillåtet intervall:
            ${Number(this._config.min).toFixed(1)}–${Number(this._config.max).toFixed(1)} °C.
            Ingen direkt heater/cooling/mode-styrning exponeras av kortet.
          </div>
        </div>
        `}
      </ha-card>
    `;
  }

  static getStubConfig() {
    return {
      temperature_entity: 'sensor.grainfather_gf30_temperature',
      target_entity: 'sensor.grainfather_gf30_target_temperature',
      online_entity: 'binary_sensor.grainfather_gf30_controller_online',
      heating_entity: 'binary_sensor.grainfather_gf30_heating',
      cooling_entity: 'binary_sensor.grainfather_gf30_cooling',
      control_active_entity: 'binary_sensor.grainfather_gf30_control_active',
    };
  }
}

customElements.define('grainfather-gf30-control-card', GrainfatherGf30ControlCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'grainfather-gf30-control-card',
  name: 'Grainfather GF30 Supervised Control',
  description: 'Supervised GF30 target control with explicit apply and MQTT readback.',
  preview: true,
});
