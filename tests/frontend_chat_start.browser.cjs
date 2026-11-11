// Exercise the shipped Settings defaults, start choice and chat footer in a real browser.
// Owner projections are synthetic; no account, provider or model is contacted.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

async function screenshot(page, name) {
  if (!process.env.SYLVIA_CHAT_START_SCREENSHOTS) return;
  fs.mkdirSync(process.env.SYLVIA_CHAT_START_SCREENSHOTS, { recursive: true });
  await page.screenshot({ path: path.join(process.env.SYLVIA_CHAT_START_SCREENSHOTS, name + '.png') });
}

async function openShell(browser, viewport = { width: 1400, height: 950 }) {
  const page = await browser.newPage({ viewport });
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://sylvia-chat-start.test/**', async route => {
    const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
    const file = path.join(frontend, name);
    if (!file.startsWith(frontend + path.sep) || !fs.existsSync(file)) return route.abort();
    let body = fs.readFileSync(file);
    if (name === 'app.js') body = body.toString().replace(
      "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.startTest = { state, wireControls,
        refreshProviderSettings, refreshModelChoices, renderSessionModelState, ensureSessionActivity,
        openSettings, closeSettings, chooseChatStart, updateModelSwitchButtons };
        // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
      name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-chat-start.test/');
  await page.evaluate(async () => {
    const api = window.startTest;
    const row = (id, name) => ({ id, name, canonical_id: `service/${id}`, configured_context_window: 64000,
      effective_context_window: 64000, effective_context_source: 'user_configured', max_output_tokens: 8192,
      transport: 'responses' });
    const fixture = window.fixture = {
      calls: [],
      settings: {
        default_model: { provider_id: 'service', model_id: 'service/sol' },
        chat_defaults: { reasoning_effort: 'low', speed: null },
        presets: [{ provider_id: 'custom', name: 'Custom service', mode: 'custom', base_url: '', endpoint_editable: true }],
        configured: [{ provider_id: 'service', preset_id: 'custom', name: 'My model service', mode: 'custom',
          base_url: 'https://synthetic.example.test/v1', credential_configured: true,
          models: [row('sol', 'Sol with a deliberately long display name for layout'), row('plain', 'Plain')] }],
      },
      choices: [
        { provider_id: 'service', provider_name: 'My model service', model_id: 'service/sol',
          model_name: 'Sol with a deliberately long display name for layout',
          supported_reasoning_efforts: ['low', 'medium', 'high', 'xhigh'],
          reasoning_mode_defaults: { dm: 'high', builder: 'xhigh' },
          speed_options: [{ id: 'priority', name: 'Fast', description: '2x speed, increased usage' }] },
        { provider_id: 'service', provider_name: 'My model service', model_id: 'service/plain', model_name: 'Plain',
          supported_reasoning_efforts: null, reasoning_mode_defaults: { dm: null, builder: null }, speed_options: null },
      ],
      speed: { supported: [{ id: 'priority', name: 'Fast', description: '2x speed, increased usage' }],
        current: 'priority', effective_request: 'priority', valid: true },
    };
    api.state.bridge = {
      provider_settings: async () => structuredClone(fixture.settings),
      model_choices: async () => structuredClone(fixture.choices),
      set_chat_defaults: async (command) => {
        fixture.calls.push({ kind: 'defaults', ...command });
        if (fixture.rejectDefaults) throw new Error('command_rejected: This model does not offer the selected speed.');
        fixture.settings.default_model = { provider_id: command.provider_id, model_id: command.model_id };
        fixture.settings.chat_defaults = {
          reasoning_effort: command.reasoning_effort === 'mode_default' ? null : command.reasoning_effort,
          speed: command.speed === 'standard' ? null : command.speed };
      },
      set_default_model: async () => { fixture.calls.push({ kind: 'legacy-default' }); },
      switch_session_speed: async (command) => {
        fixture.calls.push({ kind: 'speed', ...command });
        const chosen = command.speed === 'standard' ? null : command.speed;
        fixture.speed = { ...fixture.speed, current: chosen, effective_request: chosen };
        return { status: 'switched', speed_state: structuredClone(fixture.speed) };
      },
      switch_session_reasoning: async (command) => { fixture.calls.push({ kind: 'effort', ...command }); return {}; },
    };
    document.getElementById('connecting-overlay').style.display = 'none';
    document.getElementById('session-frame').style.display = 'flex';
    api.state.view = 'session'; api.state.activeSessionId = 'synthetic-gm'; api.state.activeSessionRole = 'dm';
    api.state.activeCampaignId = 'synthetic';
    api.ensureSessionActivity({ sessionId: 'synthetic-gm', sessionRole: 'dm', campaignId: 'synthetic' });
    api.wireControls();
    await api.refreshProviderSettings();
    await api.refreshModelChoices();
    window.showChat = () => api.renderSessionModelState({
      model_state: { current: { provider_id: 'service', model_id: 'service/sol' } },
      reasoning_state: { current: null, once: null, scope: 'mode_default', effective_request: 'high', valid: true,
        supported: ['low', 'medium', 'high', 'xhigh'], provider_id: 'service', model_id: 'service/sol' },
      speed_state: structuredClone(fixture.speed),
    });
    window.showChat();
  });
  return { page, errors };
}

const launch = () => chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
const calls = page => page.evaluate(() => window.fixture.calls);
const inViewport = async (page, selector) => {
  const box = await page.locator(selector).boundingBox();
  const size = page.viewportSize();
  return box.x >= 0 && box.y >= 0 && box.x + box.width <= size.width + 1 && box.y + box.height <= size.height + 1;
};

test('Settings saves and reopens the model, effort and speed new chats start from', { timeout: 30000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await openShell(browser);
    await page.evaluate(() => window.startTest.openSettings('models'));
    assert.equal(await page.locator('#default-effort-choice').inputValue(), 'low');
    assert.equal(await page.locator('#default-speed-choice').inputValue(), 'standard');
    assert.equal(await page.locator('#set-default-model-btn').isDisabled(), true, 'nothing has changed yet');
    assert.deepEqual(await page.locator('#default-speed-choice option').allInnerTexts(), ['Standard', 'Fast']);
    assert.match(await page.locator('#model-status').innerText(), /New chats use Sol.* · Low · Standard speed\./);
    await page.locator('#default-effort-choice').selectOption('high');
    await page.locator('#default-speed-choice').selectOption('priority');
    assert.match(await page.locator('#default-speed-note').innerText(), /Fast: 2x speed, increased usage/);
    assert.equal(await page.locator('#set-default-model-btn').isDisabled(), false);
    await screenshot(page, 'settings-defaults');
    await page.locator('#set-default-model-btn').click();
    await page.waitForFunction(() => /will use/.test(document.getElementById('model-status').textContent));
    assert.deepEqual(await calls(page), [{ kind: 'defaults', provider_id: 'service', model_id: 'service/sol',
      reasoning_effort: 'high', speed: 'priority' }]);
    assert.match(await page.locator('#model-status').innerText(), /High · Fast speed.*already have keep their own settings/);
    // Reopening shows what was saved, and Save has nothing left to do.
    await page.evaluate(async () => { const api = window.startTest; api.closeSettings();
      await api.refreshProviderSettings(); await api.refreshModelChoices(); api.openSettings('models'); });
    assert.equal(await page.locator('#default-effort-choice').inputValue(), 'high');
    assert.equal(await page.locator('#default-speed-choice').inputValue(), 'priority');
    assert.equal(await page.locator('#set-default-model-btn').isDisabled(), true);
    // A model without those options offers neither, and says so on the control.
    await page.locator('#model-choice').selectOption({ label: 'My model service · Plain' });
    assert.equal(await page.locator('#default-effort-choice').inputValue(), 'mode_default');
    assert.equal(await page.locator('#default-speed-choice').inputValue(), 'standard');
    assert.equal(await page.locator('#default-speed-choice').isDisabled(), true);
    assert.match(await page.locator('#default-speed-choice').getAttribute('title'), /no speed choice/);
    assert.deepEqual(await page.locator('#default-effort-choice option').allInnerTexts(),
      ['Mode default (Provider default)', 'Provider default']);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('a refused default leaves the saved settings shown and explains why', { timeout: 30000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await openShell(browser);
    await page.evaluate(() => { window.fixture.rejectDefaults = true; window.startTest.openSettings('models'); });
    await page.locator('#default-speed-choice').selectOption('priority');
    await page.locator('#set-default-model-btn').click();
    await page.waitForFunction(() => /could not be changed/.test(document.getElementById('model-status').textContent));
    assert.match(await page.locator('#model-status').innerText(), /does not offer the selected speed/);
    assert.deepEqual(await page.evaluate(() => window.fixture.settings.chat_defaults), { reasoning_effort: 'low', speed: null });
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('the start choice is keyboard operable, cancellable and sends nothing by itself', { timeout: 30000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await openShell(browser);
    const ask = () => page.evaluate(() => { window.startResult = 'pending';
      void window.startTest.chooseChatStart({ role: 'dm', title: 'Start Game Master', action: 'Begin adventure',
        model: window.startTest.state.modelChoices[0] }).then(value => { window.startResult = value; }); });
    await ask();
    assert.equal(await page.locator('#chat-start-dialog').isVisible(), true);
    assert.equal(await page.locator('#chat-start-title').innerText(), 'Start Game Master');
    assert.equal(await page.locator('#chat-start-effort').inputValue(), 'low', 'the Settings default is shown');
    assert.equal(await page.locator('#chat-start-speed').inputValue(), 'standard');
    assert.equal(await page.locator('#chat-start-effort option').first().innerText(), 'Mode default (High)');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'chat-start-confirm');
    assert.equal(await inViewport(page, '#chat-start-dialog'), true);
    await screenshot(page, 'start-game-master');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => window.startResult === null);
    assert.equal(await page.locator('#chat-start-dialog').isVisible(), false);
    // Reopen, choose with the keyboard, and confirm with Enter.
    await ask();
    await page.locator('#chat-start-effort').focus();
    await page.locator('#chat-start-effort').selectOption('xhigh');
    await page.locator('#chat-start-speed').selectOption('priority');
    assert.match(await page.locator('#chat-start-speed-note').innerText(), /the provider decides the speed it actually uses/);
    await page.locator('#chat-start-confirm').focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => window.startResult && window.startResult !== 'pending');
    assert.deepEqual(await page.evaluate(() => window.startResult), { provider_id: 'service', model_id: 'service/sol',
      reasoning_effort: 'xhigh', speed: 'priority' });
    assert.deepEqual(await calls(page), [], 'opening, cancelling and confirming the choice call nothing');
    // The same choice stays inside a narrow window with its long model name.
    await page.setViewportSize({ width: 420, height: 800 });
    await ask();
    assert.equal(await inViewport(page, '#chat-start-dialog'), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await screenshot(page, 'start-game-master-narrow');
    await page.locator('#chat-start-cancel').click();
    await page.waitForFunction(() => window.startResult === null);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('the chat footer shows this chat’s effort and speed and changes only this chat', { timeout: 30000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await openShell(browser);
    assert.equal(await page.locator('#chat-reasoning-btn').innerText(), 'High · Fast');
    assert.match(await page.locator('#chat-reasoning-btn').getAttribute('aria-label'), /Speed: Fast\./);
    await page.locator('#chat-reasoning-btn').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#chat-reasoning-popover').isVisible(), true);
    assert.equal(await page.locator('label[for="chat-speed-current"]').innerText(), 'Speed · this chat');
    assert.equal(await page.locator('#chat-speed-current').inputValue(), 'priority');
    assert.equal(await inViewport(page, '#chat-reasoning-popover'), true);
    await screenshot(page, 'chat-footer-effort-speed');
    await page.locator('#chat-speed-current').selectOption('standard');
    await page.waitForFunction(() => document.getElementById('chat-reasoning-btn').textContent === 'High');
    assert.deepEqual(await calls(page), [{ kind: 'speed', session_id: 'synthetic-gm', speed: 'standard' }]);
    assert.match(await page.locator('#chat-speed-hint').innerText(), /sends no speed setting/);
    assert.deepEqual(await page.evaluate(() => window.fixture.settings.chat_defaults), { reasoning_effort: 'low', speed: null },
      'changing a chat never rewrites the Settings defaults');
    // While a change is being saved, the controls are busy rather than accepting another.
    await page.evaluate(() => { window.startTest.state.reasoningPending = 'synthetic-gm'; window.startTest.updateModelSwitchButtons(); });
    assert.equal(await page.locator('#chat-reasoning-btn').isDisabled(), true);
    assert.equal(await page.locator('#chat-speed-current').isDisabled(), true);
    await page.evaluate(() => { window.startTest.state.reasoningPending = null; window.startTest.updateModelSwitchButtons(); });
    // A model without speed tiers shows the control disabled instead of inventing a choice.
    await page.evaluate(() => { window.fixture.speed = { supported: null, current: null, effective_request: null, valid: true }; window.showChat(); });
    assert.equal(await page.locator('#chat-reasoning-btn').innerText(), 'High');
    await page.locator('#chat-reasoning-btn').click();
    assert.equal(await page.locator('#chat-speed-current').isDisabled(), true);
    assert.match(await page.locator('#chat-speed-hint').innerText(), /no speed choice/);
    await page.setViewportSize({ width: 420, height: 800 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
