// Exercise the shipped UI with synthetic owner projections; no real account or inference is used.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

async function openShell(browser) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 950 } });
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://sylvia-settings.test/**', async route => {
    const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
    if (!['app.html', 'app.css', 'app.js', 'panel-workspace.js', 'shot-playback.js',
      'panel-host.js', 'assets/sylvia-portrait.png'].includes(name)) return route.abort();
    let body = fs.readFileSync(path.join(frontend, name));
    if (name === 'app.js') body = body.toString().replace(
      "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.settingsTest = { state, wireControls,
        refreshProviderSettings, refreshModelChoices, renderSessionModelState, ensureSessionActivity,
        openSettings, closeSettings, showModelsOverview, setInlineStatus, selectedStatus,
        renderSidebarState, refreshSessionInputAdmission, constructionStepIsValid,
        providerEditorCommand, renderContribution, refreshRulesetBrowser, renderCampaignPartyOptions };
        // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
      name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-settings.test/');
  await page.evaluate(async () => {
    const api = window.settingsTest;
    const model = (id, name, context = 64000) => ({ id, name, canonical_id: `test-api/${id}`,
      configured_context_window: context, effective_context_window: context,
      effective_context_source: 'user_configured', max_output_tokens: 8192,
      transport: 'chat_completions' });
    window.fixture = {
      calls: [],
      settings: { default_model: { provider_id: 'test-api', model_id: 'test-api/alpha' },
        presets: [
          { provider_id: 'openai', name: 'OpenAI API', mode: 'byok', base_url: 'https://api.example.test/v1', credential_required: true, default_transport: 'responses' },
          { provider_id: 'ollama', name: 'Ollama', mode: 'local', base_url: 'http://127.0.0.1:11434/v1', endpoint_editable: true },
          { provider_id: 'custom', name: 'Custom service', mode: 'custom', base_url: '', endpoint_editable: true },
          { provider_id: 'chatgpt_subscription', name: 'ChatGPT subscription', mode: 'subscription' }],
        configured: [{ provider_id: 'test-api', preset_id: 'custom', name: 'My connection', mode: 'custom',
          base_url: 'https://synthetic.example.test/v1', credential_configured: true,
          models: [model('alpha', 'Alpha'), model('beta', 'Beta', 128000)] }] },
    };
    const fixture = window.fixture;
    api.state.bridge = {
      provider_settings: async () => structuredClone(fixture.settings),
      open_provider_setup: async preset_id => {
        fixture.calls.push({ kind: 'setup', preset_id });
        return { opened: true };
      },
      model_choices: async () => fixture.settings.configured.flatMap(provider => provider.models.map(item => ({
        provider_id: provider.provider_id, provider_name: provider.name,
        model_id: item.canonical_id, model_name: item.name,
      }))),
      set_default_model: async (provider_id, model_id) => {
        fixture.calls.push({ kind: 'default', provider_id, model_id });
        fixture.settings.default_model = { provider_id, model_id };
      },
      save_provider_settings: async command => {
        fixture.calls.push({ kind: 'save', ...command });
        const provider = fixture.settings.configured.find(item => item.provider_id === command.provider_id);
        if (provider) {
          const prior = provider.models.find(item => item.id === command.model_id);
          if (prior) Object.assign(prior, { name: command.model_name, max_output_tokens: command.max_output_tokens });
          else provider.models.push(model(command.model_id, command.model_name));
        }
        return { provider_id: command.provider_id, canonical_model_id: `${command.provider_id}/${command.model_id}` };
      },
      discover_provider_models: async command => {
        fixture.calls.push({ kind: 'discover', ...command });
        if (fixture.delayDiscovery) await new Promise(resolve => { fixture.finishDiscovery = resolve; });
        if (fixture.discoveryError) throw new Error(fixture.discoveryError);
        return { total: 2, models: fixture.discoveryModels || [model('gamma', 'Gamma'), { id: 'unknown', name: 'Unknown limits' }] };
      },
      detect_local_providers: async () => [{ provider_id: 'ollama', name: 'Ollama', reachable: true }],
      validate_provider_model: async command => {
        fixture.calls.push({ kind: 'test', ...command });
        return { agent_ready: false, reachability: 'failed', authentication: 'unverified' };
      },
      chatgpt_connection: async (action, selection) => {
        fixture.calls.push({ kind: 'account', action, selection });
        const result = { signed_in: true, models: fixture.subscriptionModels || [{ id: 'subscription-model', name: 'Account model' }] };
        if (action === 'add_model') {
          const facts = result.models.find(item => item.id === selection.id);
          const saved = { ...model(selection.id, 'Account model'),
            configured_context_window: selection.context_window,
            effective_context_window: selection.context_window ?? facts.runtime_context_window,
            runtime_context_window: facts.runtime_context_window,
            configured_output_tokens: selection.max_output_tokens,
            declared_max_output_tokens: facts.output_reference_tokens,
            max_output_tokens: selection.max_output_tokens ?? facts.output_reference_tokens,
            canonical_id: `chatgpt-subscription/${selection.id}` };
          const provider = fixture.settings.configured.find(item => item.provider_id === 'chatgpt_subscription');
          if (provider) provider.models = [saved];
          else fixture.settings.configured.push({ provider_id: 'chatgpt_subscription', preset_id: 'chatgpt_subscription',
            name: 'ChatGPT subscription', models: [saved] });
          result.saved_model = `chatgpt-subscription/${selection.id}`;
        }
        return result;
      },
    };
    document.getElementById('connecting-overlay').style.display = 'none';
    document.getElementById('session-frame').style.display = 'flex';
    api.state.view = 'session'; api.state.activeSessionId = 'synthetic-gm'; api.state.activeSessionRole = 'dm';
    api.state.activeCampaignId = 'synthetic';
    api.ensureSessionActivity({ sessionId: 'synthetic-gm', sessionRole: 'dm', campaignId: 'synthetic' });
    api.wireControls();
    await api.refreshProviderSettings();
    await api.refreshModelChoices();
    api.renderSessionModelState({ model_state: { current: { provider_id: 'test-api', model_id: 'test-api/beta' } } });
    api.openSettings('models');
  });
  return { page, errors };
}

test('model menu fits the composer with both sidebars at narrow desktop widths', async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const { page, errors } = await openShell(browser);
    await page.evaluate(() => window.settingsTest.closeSettings());
    for (const width of [1024, 960, 1366]) {
      await page.setViewportSize({ width, height: 768 });
      await page.evaluate(() => {
        document.getElementById('home-view').hidden = true;
        document.getElementById('left-sidebar').hidden = false;
        document.getElementById('right-sidebar').hidden = false;
      });
      await page.locator('#chat-model-picker-btn').click();
      const composer = await page.locator('.input-composer').boundingBox();
      const menu = await page.locator('#chat-model-popover').boundingBox();
      assert.ok(menu && composer);
      assert.ok(menu.x >= composer.x - 1, `Model menu clipped at ${width}px`);
      assert.ok(menu.x + menu.width <= composer.x + composer.width + 1);
      await page.locator('#chat-model-search').fill('Beta');
      const choice = page.locator('#chat-model-results [role="option"]').filter({ hasText: 'Beta' });
      assert.equal(await choice.count(), 1);
      const option = await choice.boundingBox();
      assert.ok(option.x >= menu.x && option.x + option.width <= menu.x + menu.width);
      await page.locator('#chat-model-picker-btn').click();
      await page.waitForFunction(() => document.getElementById('chat-model-popover').hidden);
    }
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

test('subscription setup saves automatic facts and retains manual caps until explicitly restored', { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ headless: true,
    channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const { page, errors } = await openShell(browser);
    await page.evaluate(() => { window.fixture.subscriptionModels = [{
      id: 'gpt-6-sol', name: 'GPT-6 Sol', runtime_context_window: 258400,
      runtime_context_source: 'model_catalog', output_reference_tokens: 128000,
      output_reference_source: 'https://developers.openai.com/api/docs/models/gpt-6-sol',
    }]; });
    await page.locator('#add-model-btn').click();
    await page.locator('#open-account-signin-btn').click();
    await page.locator('#chatgpt-model-choice').selectOption('gpt-6-sol');
    assert.match(await page.locator('#chatgpt-model-summary').innerText(), /automatic/i);
    assert.match(await page.locator('#chatgpt-model-summary').innerText(), /258,400/);
    assert.equal(await page.locator('#chatgpt-model-context').inputValue(), '');
    assert.equal(await page.locator('#chatgpt-model-output').inputValue(), '');
    await page.locator('[data-chatgpt-action="add_model"]').click();
    await page.waitForFunction(() => !document.getElementById('models-overview').hidden);
    const saved = () => page.evaluate(() => window.fixture.calls.filter(c => c.action === 'add_model').at(-1).selection);
    assert.deepEqual(await saved(), { id: 'gpt-6-sol', context_window: null, max_output_tokens: null });
    await page.locator('[data-edit-model="gpt-6-sol"]').click();
    await page.locator('#chatgpt-model-advanced summary').click();
    await page.locator('#chatgpt-model-context').fill('200000');
    await page.locator('#chatgpt-model-output').fill('32000');
    await page.locator('[data-chatgpt-action="status"]').click();
    assert.equal(await page.locator('#chatgpt-model-context').inputValue(), '200000');
    await page.locator('[data-chatgpt-action="add_model"]').click();
    await page.waitForFunction(() => !document.getElementById('models-overview').hidden);
    assert.deepEqual(await saved(), { id: 'gpt-6-sol', context_window: 200000, max_output_tokens: 32000 });
    await page.locator('[data-edit-model="gpt-6-sol"]').click();
    await page.locator('#chatgpt-model-advanced summary').click();
    assert.equal(await page.locator('#chatgpt-model-output').inputValue(), '32000');
    await page.locator('#chatgpt-automatic-limits-btn').click();
    assert.equal(await page.locator('#chatgpt-model-context').inputValue(), '');
    assert.equal(await page.locator('#chatgpt-model-output').inputValue(), '');
    await page.locator('[data-chatgpt-action="add_model"]').click();
    await page.waitForFunction(() => !document.getElementById('models-overview').hidden);
    assert.deepEqual(await saved(), { id: 'gpt-6-sol', context_window: null, max_output_tokens: null });
    assert.deepEqual(await page.evaluate(() => window.fixture.calls.filter(c => ['default', 'test'].includes(c.kind))), []);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('automatic model limits preserve overrides and reset for a different identity', { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ headless: true,
    channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const { page, errors } = await openShell(browser);
    await page.evaluate(async () => {
      const sol = { id: 'gpt-6-sol', name: 'GPT-6 Sol', preset_id: 'openai',
        declared_context_window: 1050000, declared_context_source: 'model_catalog',
        runtime_context_window: 1050000, runtime_context_source: 'model_catalog',
        max_output_tokens: 128000, declared_max_output_tokens: 128000,
        transport: 'responses', supports_tools: true, supports_streaming: true,
        documented_source: 'https://developers.openai.com/api/docs/models/gpt-6-sol', documented_at: '2026-09-23' };
      window.fixture.settings.model_defaults = [sol];
      window.fixture.discoveryModels = [sol, { id: 'unlisted-model', name: 'Unknown model' }];
      await window.settingsTest.refreshProviderSettings();
    });
    await page.locator('#add-model-btn').click();
    await page.locator('[data-model-route="api"]').click();
    await page.locator('#provider-credential').fill('synthetic-key');
    await page.locator('#provider-continue-btn').click();
    await page.locator('#provider-discovered-models').selectOption('0');
    assert.match(await page.locator('#provider-output-summary').textContent(), /128,000/);
    assert.equal(await page.locator('#provider-output-limit').inputValue(), '');
    assert.equal(await page.locator('#provider-context-window').inputValue(), '');
    await page.locator('#provider-model-advanced summary').click();
    await page.locator('#provider-context-window').fill('500000');
    await page.locator('#provider-output-limit').fill('64000');
    await page.locator('#provider-builder-output-limit').fill('16000');
    await page.locator('#discover-provider-models-btn').click();
    await page.waitForFunction(() => window.fixture.calls.filter(c => c.kind === 'discover').length === 2);
    assert.equal(await page.locator('#provider-context-window').inputValue(), '500000');
    assert.equal(await page.locator('#provider-output-limit').inputValue(), '64000');
    assert.equal(await page.locator('#provider-builder-output-limit').inputValue(), '16000');
    await page.locator('#provider-automatic-limits-btn').click();
    assert.equal(await page.locator('#provider-context-window').inputValue(), '');
    assert.equal(await page.locator('#provider-output-limit').inputValue(), '');
    await page.locator('#provider-discovered-models').selectOption('1');
    assert.match(await page.locator('#provider-output-summary').textContent(), /not reported/);
    await page.locator('#provider-manual-model summary').click();
    await page.locator('#provider-model-id').fill('gpt-6-sol');
    assert.equal(await page.locator('#provider-transport').inputValue(), 'responses');
    assert.match(await page.locator('#provider-output-summary').textContent(), /128,000/);
    await page.locator('#save-provider-btn').click();
    await page.waitForFunction(() => window.fixture.calls.some(c => c.kind === 'save'));
    const calls = await page.evaluate(() => window.fixture.calls);
    const saved = calls.find(c => c.kind === 'save');
    assert.equal(saved.model_id, 'gpt-6-sol');
    assert.equal(saved.max_output_tokens, null);
    assert.equal(saved.declared_max_output_tokens, 128000);
    assert.equal(saved.configured_context_window, null);
    assert.equal(saved.runtime_context_source, 'model_catalog');
    assert.equal(saved.supports_tools, true);
    assert.equal(calls.filter(c => c.kind === 'test' || c.kind === 'default').length, 0);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('service setup guidance and discovery failures preserve form values and saved choices', async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const { page, errors } = await openShell(browser);
    await page.evaluate(() => {
      window.fixture.settings.presets.push({ provider_id: 'groq', name: 'Groq', mode: 'byok',
        base_url: 'https://api.groq.com/openai/v1', credential_required: true,
        default_transport: 'chat_completions', setup_hint: 'Create a GroqCloud API key.',
        setup_url: 'https://console.groq.com/keys' });
      window.fixture.settings.presets.find(item => item.provider_id === 'ollama').setup_hint = 'Start Ollama with a downloaded model.';
      return window.settingsTest.refreshProviderSettings();
    });
    await page.locator('#add-model-btn').click();
    await page.locator('[data-model-route="api"]').click();
    await page.locator('#provider-preset').selectOption('groq');
    assert.equal(await page.locator('#provider-endpoint').inputValue(), 'https://api.groq.com/openai/v1');
    assert.match(await page.locator('#provider-setup-hint').innerText(), /GroqCloud API key/);
    await page.locator('#provider-setup-btn').click();
    await page.waitForFunction(() => document.getElementById('provider-settings-status').textContent.includes('opened'));
    assert.deepEqual(await page.evaluate(() => window.fixture.calls.filter(call => call.kind === 'setup')),
      [{ kind: 'setup', preset_id: 'groq' }]);
    await page.locator('#provider-credential').fill('synthetic-key');
    await page.evaluate(() => { window.fixture.discoveryError = 'Groq rejected the API credential raw-secret-must-not-appear'; });
    await page.locator('#provider-continue-btn').click();
    await page.waitForFunction(() => document.getElementById('provider-settings-status').textContent.includes('replace'));
    assert.match(await page.locator('#provider-settings-status').innerText(), /saved models are unchanged/);
    assert.doesNotMatch(await page.locator('#provider-settings-status').innerText(), /raw-secret/);
    assert.equal(await page.locator('#provider-credential').inputValue(), 'synthetic-key');
    assert.equal(await page.locator('#provider-connection-step').isVisible(), true);
    await page.evaluate(() => { window.fixture.discoveryError = null; });
    await page.locator('#provider-continue-btn').click();
    await page.waitForFunction(() => !document.getElementById('provider-model-step').hidden);
    await page.locator('#provider-discovered-models').selectOption('0');
    await page.locator('#provider-model-advanced').evaluate(el => { el.open = true; });
    await page.locator('#provider-context-window').fill('48000');
    await page.locator('#provider-output-limit').fill('4096');
    await page.evaluate(() => { window.fixture.discoveryError = 'model discovery failed with HTTP 429'; });
    await page.locator('#discover-provider-models-btn').click();
    await page.waitForFunction(() => document.getElementById('provider-settings-status').textContent.includes('rate-limiting'));
    assert.equal(await page.locator('#provider-model-id').inputValue(), 'gamma');
    assert.equal(await page.locator('#provider-context-window').inputValue(), '48000');
    assert.equal(await page.locator('#provider-output-limit').inputValue(), '4096');
    assert.deepEqual(await page.evaluate(() => window.fixture.settings.default_model),
      { provider_id: 'test-api', model_id: 'test-api/alpha' });
    assert.equal(await page.evaluate(() => window.fixture.settings.configured[0].models.length), 2);
    await page.locator('#provider-editor [data-models-back]').click();
    await page.locator('#add-model-btn').click();
    await page.locator('[data-model-route="local"]').click();
    assert.match(await page.locator('#provider-setup-hint').innerText(), /Start Ollama/);
    await page.evaluate(() => { window.fixture.discoveryError = 'Could not connect to Ollama'; });
    await page.locator('#provider-continue-btn').click();
    await page.waitForFunction(() => document.getElementById('provider-settings-status').textContent.includes('Start it'));
    assert.equal(await page.evaluate(() => window.fixture.calls.some(call => ['test', 'save', 'default'].includes(call.kind))), false);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('Models separates defaults, exact saved models, setup routes and deliberate tests', { timeout: 45000 }, async () => {
  const browser = await chromium.launch({ headless: true,
    channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const { page, errors } = await openShell(browser);
    const calls = kind => page.evaluate(kind => window.fixture.calls.filter(call => call.kind === kind), kind);
    assert.equal(await page.locator('#provider-editor').isVisible(), false);
    assert.equal(await page.locator('#model-choice').inputValue(), '0');
    assert.equal(await page.locator('#chat-model-choice').inputValue(), '1');
    assert.equal(await page.locator('#set-default-model-btn').isEnabled(), false);
    if (process.env.SYLVIA_MODELS_SCREENSHOT) await page.screenshot({ path: process.env.SYLVIA_MODELS_SCREENSHOT });
    await page.locator('#model-choice').selectOption('1');
    await page.locator('#set-default-model-btn').click();
    await page.waitForFunction(() => window.fixture.calls.some(call => call.kind === 'default'));
    assert.equal((await calls('default'))[0].model_id, 'test-api/beta');
    await page.locator('[data-edit-model="beta"]').click();
    assert.equal(await page.locator('#provider-model-id').inputValue(), 'beta');
    assert.equal(await page.locator('#provider-model-id').getAttribute('readonly'), '');
    assert.equal(await page.locator('#provider-context-window').inputValue(), '128000');
    await page.locator('#provider-model-name').fill('Beta renamed');
    await page.locator('#save-provider-btn').click();
    await page.waitForFunction(() => !document.getElementById('models-overview').hidden);
    const saved = (await calls('save'))[0];
    assert.equal(saved.provider_id, 'test-api');
    assert.equal(saved.model_id, 'beta');
    assert.equal(saved.credential, '');
    assert.equal((await calls('default')).length, 1);
    assert.equal((await calls('test')).length, 0);
    assert.equal(await page.locator('#chat-model-choice').inputValue(), '1');

    await page.locator('#add-model-btn').click();
    await page.locator('#use-existing-provider-btn').click();
    await page.locator('#provider-discovered-models').selectOption('0');
    await page.locator('#provider-manual-model summary').click();
    await page.locator('#provider-model-id').fill('different-manual-model');
    assert.equal(await page.locator('#provider-output-limit').inputValue(), '', 'a different manual identity cannot inherit discovered limits');
    assert.equal(await page.locator('#provider-context-window').inputValue(), '');
    await page.locator('#provider-discovered-models').selectOption('1');
    await page.locator('#save-provider-btn').click();
    await page.waitForFunction(() => !document.getElementById('models-overview').hidden);
    const automaticSave = (await calls('save')).at(-1);
    assert.equal(automaticSave.max_output_tokens, null);
    assert.equal(automaticSave.configured_context_window, null);
    assert.equal(automaticSave.declared_max_output_tokens, null);
    assert.equal((await calls('default')).length, 1, 'adding a model does not make it the default');
    assert.equal((await calls('test')).length, 0, 'ordinary setup does not run inference');

    await page.locator('#add-model-btn').click();
    await page.locator('[data-model-route="api"]').click();
    assert.equal(await page.locator('#provider-preset').inputValue(), 'openai');
    assert.equal(await page.locator('#detect-local-providers-btn').isVisible(), false);
    await page.locator('#provider-editor [data-models-back]').click();
    await page.locator('#add-model-btn').click();
    await page.locator('[data-model-route="local"]').click();
    assert.match(await page.locator('#provider-route-help').innerText(), /does not download or start/);
    await page.locator('#detect-local-providers-btn').click();
    assert.equal(await page.locator('#provider-preset').inputValue(), 'ollama');
    await page.evaluate(() => { window.fixture.delayDiscovery = true; });
    await page.locator('#provider-continue-btn').click();
    await page.locator('#provider-editor [data-models-back]').click();
    await page.evaluate(() => window.fixture.finishDiscovery());
    await page.waitForTimeout(40);
    assert.equal(await page.locator('#models-overview').isVisible(), true, 'late discovery cannot reopen abandoned setup');

    await page.locator('#add-model-btn').click();
    await page.locator('#open-account-signin-btn').click();
    await page.locator('#chatgpt-model-choice').selectOption('subscription-model');
    await page.locator('[data-chatgpt-action="add_model"]').click();
    await page.waitForFunction(() => !document.getElementById('models-overview').hidden);
    const automaticAccount = (await calls('account')).find(c => c.action === 'add_model');
    assert.deepEqual(automaticAccount.selection,
      { id: 'subscription-model', context_window: null, max_output_tokens: null });
    assert.equal(await page.locator('#set-added-model-default-btn').isVisible(), true);
    assert.equal((await calls('default')).length, 1);
    await page.locator('#set-added-model-default-btn').click();
    await page.waitForFunction(() => window.fixture.calls.filter(call => call.kind === 'default').length === 2);
    assert.equal((await calls('default'))[1].model_id, 'chatgpt-subscription/subscription-model');
    assert.equal(await page.locator('#chat-model-choice').inputValue(), '1');
    await page.setViewportSize({ width: 960, height: 700 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 960);
    await page.locator('#add-model-btn').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#model-existing-provider').evaluate(el => el === document.activeElement), true);
    await page.locator('[data-model-route="local"]').click();
    await page.locator('#provider-manual-entry-btn').click();
    assert.equal(await page.locator('#provider-model-id').evaluate(el => el === document.activeElement), true);
    const bounds = await page.locator('#provider-model-id').boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 960);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('first use and contextual session feedback remain visible without the status strip', { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ headless: true,
    channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const { page, errors } = await openShell(browser);
    await page.evaluate(async () => {
      window.fixture.settings = { ...window.fixture.settings, configured: [], default_model: null };
      await window.settingsTest.refreshProviderSettings();
      await window.settingsTest.refreshModelChoices();
    });
    assert.match(await page.locator('#configured-provider-list').innerText(), /No models saved/);
    assert.equal(await page.locator('#set-default-model-btn').isEnabled(), false);
    await page.locator('#add-model-btn').click();
    assert.equal(await page.locator('#model-existing-connection').isVisible(), false);
    await page.locator('#close-settings-btn').click();
    assert.equal(await page.locator('.session-status, .status-dot').count(), 0);
    await page.evaluate(() => {
      window.settingsTest.setInlineStatus('Writing a reply…');
      window.settingsTest.state.sessionActivities.get('synthetic-gm').phase = 'working';
      window.settingsTest.refreshSessionInputAdmission();
    });
    assert.equal(await page.locator('#stop-btn').isVisible(), true);
    assert.match(await page.locator('.input-composer').innerText(), /Writing a reply/);
    await page.evaluate(() => window.settingsTest.setInlineStatus(''));
    assert.equal(await page.locator('#session-status-text').isVisible(), false);
    await page.locator('#toggle-left-sidebar-btn').click();
    await page.locator('#toggle-right-sidebar-btn').click();
    await page.evaluate(() => window.settingsTest.setInlineStatus('Panel could not open', 'panels'));
    assert.equal(await page.locator('#session-status-text').innerText(), 'Panel could not open');
    assert.equal(await page.locator('#session-status-text').isVisible(), true);
    await page.evaluate(() => {
      window.settingsTest.setInlineStatus('Loading campaign Interview…', 'construction');
      const form = document.getElementById('construction-panel');
      document.getElementById('chronology').append(form);
      form.hidden = false;
      window.settingsTest.setInlineStatus('Campaign name required', 'construction');
    });
    assert.equal(await page.locator('#construction-status').isVisible(), true);
    assert.equal(await page.locator('#session-status-text').isVisible(), false, 'the ready Interview clears its loading fallback');
    assert.equal(await page.locator('#construction-status').getAttribute('role'), 'status');
    await page.setViewportSize({ width: 960, height: 700 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 960);
    await page.evaluate(() => {
      window.settingsTest.openSettings('campaign-data');
      window.settingsTest.setInlineStatus('Rename rejected', 'campaign-data');
    });
    assert.equal(await page.locator('#campaign-data-status').isVisible(), true);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('character limits count emoji like the owner and preserve oversized input for correction', async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const { page } = await openShell(browser);
    const result = await page.evaluate(() => {
      const api = window.settingsTest;
      document.getElementById('construction-package').value = '';
      api.state.constructionStep = 1;
      const panel = document.getElementById('construction-panel');
      document.getElementById('chronology').append(panel);
      panel.hidden = false;
      const name = document.getElementById('construction-name');
      name.value = '🦊'.repeat(120);
      const accepted = api.constructionStepIsValid('name');
      name.value += 'x';
      const rejected = api.constructionStepIsValid('name');
      const status = document.getElementById('construction-status').textContent;
      const connection = document.getElementById('provider-display-name');
      connection.value = '🦊'.repeat(120);
      const command = api.providerEditorCommand({ requireOutputLimit: false });
      connection.value += 'x';
      let error;
      try { api.providerEditorCommand({ requireOutputLimit: false }); } catch (e) { error = e.message; }
      return { accepted, rejected, status, saved: command.display_name, error,
        retained: connection.value, maximum: connection.getAttribute('maxlength') };
    });
    assert.equal(result.accepted, true);
    assert.equal(result.rejected, false);
    assert.match(result.status, /120 characters; you entered 121/);
    assert.equal(result.saved, '🦊'.repeat(120));
    assert.match(result.error, /120 characters; you entered 121/);
    assert.equal(result.retained, '🦊'.repeat(120) + 'x');
    assert.equal(result.maximum, null);
    await page.close();
  } finally { await browser.close(); }
});

test('unavailable saved models never appear as a configured default for the current chat', async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const { page, errors } = await openShell(browser);
    const missing = { provider_id: 'removed-service', model_id: 'removed-service/saved-model' };
    await page.evaluate(async missing => {
      const api = window.settingsTest;
      api.renderSessionModelState({ model_state: { current: missing } });
      await api.refreshModelChoices();
      api.closeSettings();
    }, missing);
    assert.equal(await page.locator('#chat-model-choice').inputValue(), '');
    assert.match(await page.locator('#chat-model-picker-btn').innerText(), /Unavailable.*saved-model/);
    assert.match(await page.locator('#chat-model-picker-btn').getAttribute('aria-label'), /unavailable/i);
    assert.doesNotMatch(await page.locator('#chat-model-picker-btn').innerText(), /Alpha/);
    assert.deepEqual(await page.evaluate(() => window.fixture.calls.filter(c =>
      ['default', 'test', 'switch'].includes(c.kind))), []);

    await page.evaluate(() => window.settingsTest.renderSessionModelState({
      model_state: { current: { provider_id: 'test-api', model_id: 'test-api/beta' } },
    }));
    assert.equal(await page.locator('#chat-model-choice').inputValue(), '1');
    assert.match(await page.locator('#chat-model-picker-btn').innerText(), /Beta/);

    await page.evaluate(missing => window.settingsTest.renderSessionModelState({
      model_state: { current: { provider_id: 'test-api', model_id: 'test-api/beta' }, once: missing },
    }), missing);
    assert.match(await page.locator('#chat-model-picker-btn').innerText(), /Unavailable.*saved-model.*next reply/);
    assert.equal(await page.locator('#chat-model-choice').inputValue(), '1');
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});


test('invalid manual limits identify their field and preserve automatic blanks', async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const { page, errors } = await openShell(browser);
    await page.locator('[data-edit-model="alpha"]').click();
    await page.locator('#provider-model-advanced summary').click();
    const fields = [
      ['provider-context-window', /context token limit/i],
      ['provider-output-limit', /output token limit/i],
      ['provider-builder-output-limit', /Builder output token limit/i],
    ];
    for (const [id] of fields) await page.locator('#' + id).fill('');
    for (const [id, label] of fields) {
      for (const value of ['0', '-1', '1.5']) {
        await page.locator('#' + id).fill(value);
        await page.locator('#save-provider-btn').click();
        assert.match(await page.locator('#provider-settings-status').textContent(), label);
        assert.equal(await page.locator('#' + id).getAttribute('aria-invalid'), 'true');
        assert.match(await page.locator('#' + id).evaluate(node => node.validationMessage), label);
        assert.equal(await page.locator('#' + id).evaluate(node => node.validity.customError), true);
        assert.equal(await page.locator('#' + id).evaluate(node => document.activeElement === node), true);
        assert.equal(await page.evaluate(() => window.fixture.calls.filter(c => c.kind === 'save').length), 0);
      }
      await page.locator('#' + id).fill('');
    }
    await page.locator('#save-provider-btn').click();
    await page.waitForFunction(() => window.fixture.calls.some(c => c.kind === 'save'));
    const saved = await page.evaluate(() => window.fixture.calls.find(c => c.kind === 'save'));
    assert.equal(saved.configured_context_window, null);
    assert.equal(saved.max_output_tokens, null);
    assert.equal(saved.builder_output_tokens, null);
    for (const [id] of fields) assert.equal(await page.locator('#' + id).evaluate(node => node.validity.customError), false);
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});


test('current and legacy Lite package choices show SRD Lite without changing their identities', async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const { page, errors } = await openShell(browser);
    await page.evaluate(async () => {
      const candidates = ['0.3.5', '0.3.10'].map((version, index) => ({
        candidate_id: index ? 'current-lite' : 'saved-lite',
        package_lock: { entries: [{ package_id: 'sylvia.srd-lite.bg3', version, role: 'primary' }] },
      }));
      window.fixture.originalCandidates = structuredClone(candidates);
      window.fixture.candidates = candidates;
      const bridge = window.settingsTest.state.bridge;
      bridge.package_candidates = async () => candidates;
      bridge.inspect_package_candidate = async id => ({ packages: [{
        id: 'sylvia.srd-lite.bg3', version: id === 'current-lite' ? '0.3.10' : '0.3.5',
        label: 'SRD Lite - BG3 spells', role: 'primary', publisher: 'QA', content_sha256: 'a'.repeat(64),
        compatibility: { host_contract: 1, runtime_format: 1 }, files: [], public_actions: [],
      }] });
      window.settingsTest.openSettings('packages');
    });
    await page.waitForFunction(() => document.getElementById('ruleset-candidate-select').options.length === 2);
    await page.locator('#ruleset-candidate-select').selectOption('current-lite');
    await page.waitForFunction(() => document.getElementById('ruleset-identity').textContent.includes('SRD Lite@0.3.10'));
    assert.equal(await page.locator('#ruleset-candidate-select').inputValue(), 'current-lite');
    assert.deepEqual(await page.locator('#ruleset-candidate-select option').allTextContents(), ['SRD Lite 0.3.5', 'SRD Lite 0.3.10']);
    assert.match(await page.locator('#ruleset-compare-select').textContent(), /SRD Lite 0\.3\.5/);
    assert.doesNotMatch(await page.locator('#ruleset-identity').innerText(), /bg3/i);
    assert.doesNotMatch(await page.locator('#ruleset-package-list').innerText(), /bg3/i);
    assert.equal(await page.evaluate(() => JSON.stringify(window.fixture.candidates) === JSON.stringify(window.fixture.originalCandidates)), true);
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});


test('saved character choices retain exact bindings while displaying the friendly Lite name', async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const { page, errors } = await openShell(browser);
    const result = await page.evaluate(() => {
      const template = { player_archetypes: [{ id: 'hero-value', name: 'Hero', summary: 'Ready', reusable: true,
        ruleset: { id: 'sylvia.srd-lite.bg3', version: '0.3.5' },
        campaign_setting: { id: 'pneuma.severed-lands', version: '1.0.0-alpha.2' } }],
        companions: [{ id: 'companion-value', name: 'Companion', summary: 'Ready', reusable: true,
          ruleset: { id: 'sylvia.srd-lite.bg3', version: '0.3.10' } },
          { id: 'foreign-value', name: 'Other', summary: 'Ready', reusable: true,
            ruleset: { id: 'other.ruleset', version: '2.0' } }],
        starting_party: { player_character: { mode: 'compatible' }, custom_companions: { minimum: 0, maximum: 5 }, required_companions: [] } };
      const original = structuredClone(template);
      window.settingsTest.renderCampaignPartyOptions(template, { archetype_id: 'hero-value', companion_ids: ['companion-value'] });
      return { selected: document.getElementById('rift-archetype').value,
        option: document.querySelector('#rift-archetype option[value="hero-value"]').textContent,
        companionText: document.getElementById('rift-companions').textContent,
        checked: document.querySelector('#rift-companions input[value="companion-value"]').checked,
        template, original };
    });
    assert.match(result.option, /Saved Ruleset: SRD Lite 0\.3\.5/);
    assert.doesNotMatch(result.option, /bg3/i);
    assert.match(result.companionText, /Saved Ruleset: SRD Lite 0\.3\.10/);
    assert.doesNotMatch(result.companionText, /bg3/i);
    assert.match(result.companionText, /Saved Ruleset: other\.ruleset 2\.0/);
    assert.equal(result.selected, 'hero-value');
    assert.equal(result.checked, true);
    assert.deepEqual(result.template, result.original);
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});
