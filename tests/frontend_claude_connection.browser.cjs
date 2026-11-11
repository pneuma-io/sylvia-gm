// Exercise the Claude Code setup and account flow with a synthetic owner; no download, runtime, account or
// inference is used.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const shots = process.env.SYLVIA_CLAUDE_UI_SHOTS;

async function openShell(browser, viewport = { width: 1400, height: 950 }) {
  const page = await browser.newPage({ viewport });
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://sylvia-claude.test/**', async route => {
    const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
    if (!['app.html', 'app.css', 'app.js', 'tutorials.js', 'tutorials.css', 'panel-workspace.js', 'shot-playback.js',
      'panel-host.js', 'assets/sylvia-portrait.png'].includes(name)) return route.abort();
    let body = fs.readFileSync(path.join(frontend, name));
    if (name === 'app.js') body = body.toString().replace(
      "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.settingsTest = { state, wireControls,
        refreshProviderSettings, refreshModelChoices, openSettings, closeSettings };
        // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
      name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-claude.test/');
  await page.evaluate(async () => {
    const api = window.settingsTest;
    const status = (extra = {}) => ({ installed: true, supported: true, version: '2.1.322', signed_in: false,
      login_pending: false, auth_method: null, billing: 'unknown', message: '',
      install: { state: 'idle', version: null, received: 0, total: null, message: null },
      executable: 'C:\\Users\\player\\AppData\\Roaming\\Sylvia\\claude-runtime\\install\\2.1.322\\claude.exe', ...extra });
    window.fixture = {
      calls: [],
      status,
      next: [status()],
      settings: { default_model: null,
        presets: [
          { provider_id: 'anthropic', name: 'Anthropic', mode: 'byok', base_url: 'https://api.anthropic.com/v1', credential_required: true },
          { provider_id: 'custom', name: 'Custom service', mode: 'custom', base_url: '', endpoint_editable: true },
          { provider_id: 'chatgpt_subscription', name: 'ChatGPT subscription', mode: 'subscription' },
          { provider_id: 'claude_runtime', name: 'Claude (local runtime)', mode: 'subscription' }],
        configured: [] },
    };
    const fixture = window.fixture;
    api.state.bridge = {
      provider_settings: async () => structuredClone(fixture.settings),
      model_choices: async () => fixture.settings.configured.flatMap(provider => provider.models.map(item => ({
        provider_id: provider.provider_id, provider_name: provider.name,
        model_id: item.canonical_id, model_name: item.name,
      }))),
      set_default_model: async () => {},
      open_provider_setup: async preset_id => {
        fixture.calls.push({ kind: 'setup', preset_id });
        return { opened: true };
      },
      chatgpt_connection: async action => {
        fixture.calls.push({ kind: 'chatgpt', action });
        return { signed_in: false };
      },
      claude_connection: async (action, options) => {
        fixture.calls.push({ kind: 'claude', action, options });
        const result = structuredClone(fixture.next.length > 1 ? fixture.next.shift() : fixture.next[0]);
        if (action === 'add_model') {
          const saved = { id: options.model.id, name: options.model.id, canonical_id: `claude-runtime/${options.model.id}`,
            configured_context_window: options.model.context_window, effective_context_window: options.model.context_window,
            transport: 'claude_code' };
          const provider = fixture.settings.configured.find(item => item.preset_id === 'claude_runtime');
          if (provider) provider.models = provider.models.filter(item => item.id !== saved.id).concat(saved);
          else fixture.settings.configured.push({ provider_id: 'claude_runtime', preset_id: 'claude_runtime',
            name: 'Claude (local runtime)', mode: 'subscription', models: [saved] });
          result.saved_model = saved.canonical_id;
        }
        return result;
      },
    };
    document.getElementById('connecting-overlay').style.display = 'none';
    document.getElementById('session-frame').style.display = 'flex';
    api.wireControls();
    await api.refreshProviderSettings();
    await api.refreshModelChoices();
    api.openSettings('models');
  });
  const claudeCalls = () => page.evaluate(() => window.fixture.calls.filter(call => call.kind === 'claude'));
  const setNext = (...results) => page.evaluate(results => {
    window.fixture.next = results.map(extra => window.fixture.status(extra));
  }, results);
  return { page, errors, claudeCalls, setNext };
}

async function snap(page, name) {
  if (shots) await page.waitForTimeout(400);
  if (shots) await page.locator('.settings-content').screenshot({ path: path.join(shots, `${name}.png`) });
}

function launch() {
  return chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
}

test('opening and switching the Claude view never contacts Claude Code', { timeout: 30000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors, claudeCalls } = await openShell(browser);
    await page.locator('#add-model-btn').click();
    await page.locator('#open-claude-signin-btn').click();
    assert.equal(await page.locator('#account-view-claude').isVisible(), true);
    assert.equal(await page.locator('#account-view-chatgpt').isVisible(), false);
    assert.match(await page.locator('#claude-connection-status').innerText(), /only when you ask/);
    assert.equal(await page.locator('[data-claude-action]:visible').count(), 1, 'only the explicit check is offered');
    assert.equal(await page.locator('#claude-model-card').isVisible(), false);
    await snap(page, '1-not-checked');
    await page.locator('[data-account-view="chatgpt"]').click();
    assert.equal(await page.locator('#account-view-chatgpt').isVisible(), true);
    await page.locator('[data-account-view="claude"]').click();
    await page.evaluate(() => { window.settingsTest.closeSettings(); window.settingsTest.openSettings('accounts'); });
    await page.waitForTimeout(50);
    assert.deepEqual(await claudeCalls(), []);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('Sylvia installs its own copy with visible progress and offers an update when needed', { timeout: 30000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors, claudeCalls, setNext } = await openShell(browser);
    await page.evaluate(() => window.settingsTest.openSettings('accounts'));
    await page.locator('[data-account-view="claude"]').click();
    const missing = { installed: false, supported: false, version: null, executable: null,
      message: 'Sylvia uses its own copy of Claude Code. Install it to continue; any Claude Code you already have is left alone.' };
    await setNext(missing);
    await page.locator('[data-claude-action="status"]').click();
    await page.waitForFunction(() => /doesn’t have its copy/.test(document.getElementById('claude-connection-status').textContent));
    assert.equal(await page.locator('#claude-signin-step').innerText(), '1. INSTALL CLAUDE CODE');
    assert.equal(await page.locator('[data-claude-action="login"]').first().isVisible(), false);
    assert.equal(await page.locator('#claude-runtime-facts').isVisible(), false);
    assert.equal(await page.locator('[data-claude-action="install"]').innerText(), 'Install Claude Code for Sylvia');
    assert.match(await page.locator('#claude-install-help').innerText(), /keeps it in Sylvia’s own folder/);
    assert.match(await page.locator('#claude-connection-detail').innerText(), /already have is left alone/);
    assert.equal(await page.locator('#account-view-claude input[type="text"]:visible').count(), 0,
      'no field can point Sylvia at another Claude Code');
    await snap(page, '2-missing');
    await page.locator('#claude-terms-btn').click();
    await page.waitForFunction(() => window.fixture.calls.some(call => call.kind === 'setup'));
    assert.deepEqual(await page.evaluate(() => window.fixture.calls.filter(call => call.kind === 'setup')),
      [{ kind: 'setup', preset_id: 'claude_runtime' }]);

    const downloading = received => ({ ...missing,
      install: { state: 'downloading', version: '2.1.322', received, total: 346496160, message: null } });
    await setNext(downloading(0), downloading(104857600));
    await page.locator('[data-claude-action="install"]').click();
    await page.waitForFunction(() => /100 of 330 MB/.test(document.getElementById('claude-connection-status').textContent),
      null, { timeout: 8000 });
    assert.equal(await page.locator('#claude-signin-step').innerText(), '1. INSTALLING CLAUDE CODE');
    assert.equal(await page.locator('#claude-install-progress').isVisible(), true);
    assert.equal(await page.locator('#claude-install-progress').evaluate(bar => bar.value / bar.max > 0.3), true);
    assert.equal(await page.locator('[data-claude-action="cancel"]').innerText(), 'Cancel download');
    assert.equal(await page.locator('[data-claude-action="install"]').isVisible(), false);
    await snap(page, '2b-downloading');
    await setNext({ install: { state: 'ready', version: '2.1.322', received: 346496160, total: 346496160,
      message: 'Claude Code 2.1.322 is installed for Sylvia.' } });
    await page.locator('[data-claude-action="login"]').first().waitFor({ timeout: 8000 });
    assert.equal(await page.locator('#claude-install-progress').isVisible(), false);
    assert.match(await page.locator('#claude-connection-detail').innerText(), /2\.1\.322 is installed for Sylvia/);
    assert.equal(await page.locator('#claude-fact-version').innerText(), '2.1.322');
    assert.equal(await page.locator('[data-claude-action="install"]').innerText(), 'Check for a newer version');
    await page.locator('[data-claude-action="status"]').click();
    await page.waitForFunction(() => !/installed for Sylvia/.test(document.getElementById('claude-connection-detail').textContent));
    const settled = (await claudeCalls()).length;
    await page.waitForTimeout(1700);
    assert.equal((await claudeCalls()).length, settled, 'a finished download does not keep polling');

    await setNext({ supported: false, version: '2.0.1',
      message: 'Claude Code 2.1.322 or later is required. Update Sylvia’s copy in Settings > Accounts.' });
    await page.locator('[data-claude-action="status"]').click();
    await page.waitForFunction(() => /needs updating/.test(document.getElementById('claude-connection-status').textContent));
    assert.match(await page.locator('#claude-connection-status').innerText(), /2\.0\.1/);
    assert.match(await page.locator('#claude-connection-detail').innerText(), /2\.1\.322 or later/);
    assert.equal(await page.locator('[data-claude-action="login"]').first().isVisible(), false);
    assert.equal(await page.locator('[data-claude-action="install"]').innerText(), 'Update Claude Code');
    const calls = (await claudeCalls()).map(call => [call.action, call.options]);
    assert.deepEqual(calls.slice(0, 2), [['status', null], ['install', null]]);
    assert.equal(calls.slice(2).every(([action, options]) => action === 'status' && options === null), true);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('sign-in polls read-only status, can be cancelled, and states billing exactly', { timeout: 30000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors, claudeCalls, setNext } = await openShell(browser);
    await page.evaluate(() => window.settingsTest.openSettings('accounts'));
    await page.locator('[data-account-view="claude"]').click();
    await page.locator('[data-claude-action="status"]').click();
    const login = page.locator('[data-claude-action="login"]:not([data-claude-account])');
    await login.waitFor();
    assert.match(await page.locator('#claude-login-help').innerText(), /Anthropic Console account/);

    await setNext({ login_pending: true });
    await login.click();
    await page.locator('[data-claude-action="cancel"]').waitFor();
    assert.equal(await page.locator('[data-claude-action="cancel"]').innerText(), 'Cancel sign-in');
    assert.match(await page.locator('#claude-cancel-help').innerText(), /Any other\s+Claude Code on this computer is unaffected/);
    await snap(page, '3-pending');
    await setNext({});
    await page.locator('[data-claude-action="cancel"]').click();
    await page.waitForFunction(() => /cancelled/.test(document.getElementById('claude-connection-status').textContent));
    assert.equal(await page.locator('[data-claude-action="cancel"]').isVisible(), false);

    await setNext({ login_pending: true }, { login_pending: true },
      { signed_in: true, auth_method: 'claude.ai', billing: 'subscription', email: 'player@example.test' });
    await login.click();
    await page.locator('#claude-model-card').waitFor({ timeout: 8000 });
    const actions = (await claudeCalls()).map(call => call.action);
    assert.deepEqual(actions, ['status', 'login', 'cancel', 'login', 'status', 'status']);
    assert.equal((await claudeCalls()).every(call => call.options === null), true);
    assert.match(await page.locator('#claude-billing-note').innerText(), /Claude subscription/);
    assert.equal(await page.locator('#claude-fact-auth').innerText(), 'Claude account');
    assert.equal(await page.locator('#claude-signin-step').innerText(), '1. CLAUDE CONNECTED');
    assert.match(await page.locator('#claude-signout-help').innerText(), /affects only\s+Sylvia’s copy/);
    assert.equal(await login.isVisible(), true);
    assert.match(await login.innerText(), /Sign in again/);
    assert.doesNotMatch(await page.locator('#settings-panel-accounts').innerText(), /player@example\.test/);
    await snap(page, '4-signed-in-subscription');

    await setNext({ signed_in: true, login_pending: true, billing: 'subscription' });
    await login.click();
    await page.locator('[data-claude-action="cancel"]').waitFor();
    assert.equal(await page.locator('#claude-model-card').isVisible(), false);
    await setNext({ signed_in: true, auth_method: 'claude.ai', billing: 'subscription' });
    await page.locator('[data-claude-action="cancel"]').click();
    await page.locator('#claude-model-card').waitFor();

    // Signing out and the Console account choice act on Sylvia's own copy, through the same owner command.
    await setNext({});
    await page.locator('[data-claude-action="logout"]').click();
    await page.waitForFunction(() => document.getElementById('claude-model-card').hidden);
    assert.equal(await page.locator('[data-claude-action="logout"]').isVisible(), false);
    assert.equal(await page.locator('#claude-fact-auth').innerText(), 'Not signed in');
    await setNext({ signed_in: true, auth_method: 'console', billing: 'api' });
    await page.locator('[data-claude-action="login"][data-claude-account="api"]').click();
    await page.waitForFunction(() => document.getElementById('claude-billing-note').classList.contains('settings-note-warn'));
    assert.deepEqual((await claudeCalls()).slice(-2).map(call => [call.action, call.options]),
      [['logout', null], ['login', { account_type: 'api' }]]);
    assert.match(await page.locator('#claude-billing-note').innerText(), /billed to that Console account/);
    assert.equal(await page.locator('#claude-fact-billing').innerText(), 'Anthropic Console API');
    await page.evaluate(() => window.settingsTest.closeSettings());
    const settled = (await claudeCalls()).length;
    await page.waitForTimeout(1700);
    assert.equal((await claudeCalls()).length, settled, 'a finished sign-in does not keep polling');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('models are saved by typed alias, edited in place and kept out of API discovery', { timeout: 30000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors, claudeCalls, setNext } = await openShell(browser);
    await setNext({ signed_in: true, auth_method: 'claude.ai', billing: 'subscription' });
    await page.evaluate(() => window.settingsTest.openSettings('accounts'));
    await page.locator('[data-account-view="claude"]').click();
    await page.locator('[data-claude-action="status"]').click();
    await page.locator('#claude-model-card').waitFor();
    assert.equal((await claudeCalls())[0].options, null);

    await page.locator('[data-claude-action="add_model"]').click();
    assert.match(await page.locator('#claude-model-status').innerText(), /alias such as sonnet/);
    await page.locator('#claude-model-id').fill('claude sonnet');
    await page.locator('[data-claude-action="add_model"]').click();
    assert.match(await page.locator('#claude-model-status').innerText(), /no spaces/);
    await page.locator('[data-claude-alias="sonnet"]').click();
    assert.equal(await page.locator('#claude-model-id').inputValue(), 'sonnet');
    assert.equal(await page.locator('[data-claude-alias="sonnet"]').getAttribute('aria-pressed'), 'true');
    await snap(page, '5-add-model');
    await page.locator('[data-claude-action="add_model"]').click();
    await page.waitForFunction(() => !document.getElementById('models-overview').hidden);
    const added = (await claudeCalls()).filter(call => call.action === 'add_model');
    assert.deepEqual(added.map(call => call.options), [{
      model: { id: 'sonnet', context_window: null, max_output_tokens: null } }]);

    await page.locator('[data-edit-model="sonnet"]').click();
    assert.equal(await page.locator('#account-view-claude').isVisible(), true);
    assert.equal(await page.locator('#claude-model-id').inputValue(), 'sonnet');
    assert.equal(await page.locator('#claude-model-heading').innerText(), '2. MODEL SETTINGS');
    assert.equal(await page.locator('[data-claude-action="add_model"]').innerText(), 'Save model changes');
    await page.locator('#claude-model-advanced summary').click();
    await page.locator('#claude-model-context').fill('200000');
    await page.locator('[data-claude-action="add_model"]').click();
    await page.waitForFunction(() => !document.getElementById('models-overview').hidden);
    assert.deepEqual((await claudeCalls()).filter(call => call.action === 'add_model').at(-1).options.model,
      { id: 'sonnet', context_window: 200000, max_output_tokens: null });

    await page.locator('#add-model-btn').click();
    await page.locator('[data-model-route="api"]').click();
    const presets = await page.locator('#provider-preset option').evaluateAll(options => options.map(option => option.value));
    assert.equal(presets.includes('claude_runtime'), false);
    assert.equal(presets.includes('chatgpt_subscription'), false);
    await page.evaluate(() => window.settingsTest.openSettings('accounts'));
    await page.locator('[data-account-view="claude"]').click();
    await page.locator('#claude-api-route-btn').click();
    assert.equal(await page.locator('#provider-editor').isVisible(), true);
    assert.equal(await page.locator('#provider-preset').inputValue(), 'anthropic');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('the Claude view fits a phone-width Settings pane', { timeout: 30000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors, setNext } = await openShell(browser, { width: 390, height: 844 });
    await setNext({ signed_in: true, auth_method: 'claude.ai', billing: 'subscription' });
    await page.evaluate(() => window.settingsTest.openSettings('accounts'));
    await page.locator('[data-account-view="claude"]').click();
    await page.locator('[data-claude-action="status"]').click();
    await page.locator('#claude-model-card').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390);
    const overflow = await page.locator('#account-view-claude').evaluate(view =>
      [...view.querySelectorAll('.settings-card, .account-facts, .provider-actions')].some(node => node.scrollWidth > node.clientWidth + 1));
    assert.equal(overflow, false);
    if (shots) await page.waitForTimeout(400);
    if (shots) await page.screenshot({ path: path.join(shots, '6-phone.png'), fullPage: true });
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
