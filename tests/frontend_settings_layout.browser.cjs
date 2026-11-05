// Settings layout: every section is reachable by pointer and keyboard, readable at desktop and
// narrow widths, and honest about browser limits. Synthetic shell only; no owner or account is used.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = process.env.SYLVIA_SETTINGS_FRONTEND || path.resolve(__dirname, '../src-tauri/frontend');
const TABS = ['models', 'accounts', 'appearance', 'dictation', 'packages', 'campaign-data', 'about', 'admin'];

async function openShell(browser, viewport, { browserInvoke = false } = {}) {
  const page = await browser.newPage({ viewport });
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://sylvia-settings-layout.test/**', async route => {
    const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
    const file = path.join(frontend, name);
    if (!fs.existsSync(file)) return route.abort();
    let body = fs.readFileSync(file);
    if (name === 'app.js') body = body.toString().replace(
      "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', 'window.layoutTest = { state, wireControls, openSettings, closeSettings, beginModelConnection, startBuilderChat, refreshProviderSettings };\n  // ---- Public API ----');
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
      name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-settings-layout.test/app.html');
  await page.evaluate((browserInvoke) => {
    if (browserInvoke) window.__sylviaBrowserInvoke = async () => { throw new Error('no owner in this test'); };
    window.layoutTest.state.bridge = {};
    window.layoutTest.wireControls();
    document.querySelectorAll('.connecting-overlay').forEach(node => node.classList.add('hidden'));
  }, browserInvoke);
  return { page, errors };
}

test('arrow keys move between Settings sections only while focus is in the section list', async () => {
  const browser = await chromium.launch();
  try {
    const { page, errors } = await openShell(browser, { width: 1600, height: 1000 });
    await page.evaluate(() => window.layoutTest.openSettings('models'));
    await page.locator('#settings-tab-models').focus();
    const order = [];
    for (let step = 0; step < TABS.length; step += 1) {
      order.push(await page.evaluate(() => document.activeElement.dataset.settingsTab));
      await page.keyboard.press('ArrowDown');
    }
    assert.deepEqual(order, TABS, 'ArrowDown walks every section in visual order');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.settingsTab), 'models', 'it wraps');
    await page.keyboard.press('End');
    assert.equal(await page.locator('#settings-panel-admin').isVisible(), true);
    assert.equal(await page.locator('#settings-tab-admin').getAttribute('aria-selected'), 'true');
    await page.keyboard.press('Home');
    assert.equal(await page.locator('#settings-panel-models').isVisible(), true);
    await page.locator('#settings-tab-about').click();
    await page.locator('#local-report-reproduction').focus();
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'local-report-reproduction');
    assert.equal(await page.locator('#settings-panel-about').isVisible(), true, 'form controls keep their own arrows');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#settings-overlay').isVisible(), false);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

test('Tab reaches disclosure summaries and skips controls inside closed disclosures', async () => {
  const browser = await chromium.launch();
  try {
    const { page, errors } = await openShell(browser, { width: 1600, height: 1000 });
    await page.evaluate(() => {
      window.layoutTest.openSettings('models');
      window.layoutTest.state.providerSettings = { configured: [], presets: [{ provider_id: 'custom', name: 'Custom', mode: 'byok' }] };
      window.layoutTest.beginModelConnection('api');
    });
    const focused = () => page.evaluate(() => document.activeElement.id || document.activeElement.textContent.trim());
    await page.locator('#provider-credential').focus();
    await page.keyboard.press('Tab');
    assert.equal(await focused(), 'Advanced connection settings');
    await page.keyboard.press('Tab');
    assert.equal(await focused(), 'provider-continue-btn', 'the closed disclosure does not trap focus');
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Enter');
    await page.keyboard.press('Tab');
    assert.equal(await focused(), 'provider-transport', 'an opened disclosure exposes its controls');
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

test('closing Settings ends an unsaved model setup so a later Builder refusal is visible', async () => {
  const browser = await chromium.launch();
  try {
    const { page, errors } = await openShell(browser, { width: 1600, height: 1000 });
    const result = await page.evaluate(async () => {
      const api = window.layoutTest;
      let builderRequests = 0;
      api.state.bridge = { create_builder_chat: async () => { builderRequests += 1; } };
      api.openSettings('models');
      api.state.providerSettings = { configured: [], presets: [{ provider_id: 'custom', name: 'Custom', mode: 'byok' }] };
      api.beginModelConnection('api');
      document.getElementById('provider-credential').value = 'typed but never saved';
      api.closeSettings();
      await api.startBuilderChat();
      const refusal = document.getElementById('models-action-status');
      return { refusal: refusal.textContent, visible: refusal.getClientRects().length > 0,
        editor: !document.getElementById('provider-editor').hidden, key: document.getElementById('provider-credential').value, builderRequests };
    });
    assert.deepEqual(result, { refusal: 'Configure a provider before starting Builder', visible: true, editor: false, key: '', builderRequests: 0 });
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

// A save the owner completes after Settings closed is still reflected when Settings reopens; nothing is repeated.
// `outcome` is true (saved), false (an Error) or a string rejection, which is how the desktop host reports failures.
function installSaveBridge(page) {
  return page.evaluate(() => {
    const api = window.layoutTest;
    const calls = window.saveCalls = { save: 0, default: 0, switch: 0, list: 0 };
    let configured = [];
    window.finishSave = null;
    api.state.bridge = {
      provider_settings: async () => { calls.list += 1; return { configured, presets: [{ provider_id: 'custom', name: 'Custom', mode: 'byok' }], default_model: null }; },
      model_choices: async () => configured.flatMap(p => p.models.map(m => ({ provider_id: p.provider_id, model_id: m.canonical_id, label: m.name }))),
      save_provider_settings: (command) => { calls.save += 1; return new Promise((resolve, reject) => {
        window.finishSave = (ok) => {
          if (ok === true || ok?.committed) configured = [{ provider_id: 'custom', name: 'Synthetic service', models: [{ id: command.model_id, canonical_id: command.model_id, name: 'Synthetic saved model' }] }];
          if (ok !== true) return reject(typeof ok === 'object'
            ? Object.assign(new Error(ok.message), { code: ok.code })
            : ok === false ? new Error('synthetic save failure') : ok);
          resolve({ provider_id: 'custom', canonical_model_id: command.model_id, name: 'Synthetic saved model' }); };
      }); },
      set_default_model: async () => { calls.default += 1; },
      switch_session_model: async () => { calls.switch += 1; },
    };
    api.state.providerSettings = { configured: [], presets: [{ provider_id: 'custom', name: 'Custom', mode: 'byok' }] };
  });
}

async function startSave(page, modelId = 'synthetic/model') {
  await page.evaluate(() => { window.layoutTest.openSettings('models'); window.layoutTest.beginModelConnection('api'); });
  await page.locator('#provider-credential').fill('typed but private');
  await page.locator('#provider-manual-entry-btn').click();
  await page.locator('#provider-model-id').fill(modelId);
  await page.locator('#save-provider-btn').click();
  assert.equal(await page.locator('#provider-settings-status').textContent(), 'Saving model.');
}

function modelsView(page) {
  return page.evaluate(() => ({
    list: document.getElementById('configured-provider-list').textContent.replace(/\s+/g, ' ').trim(),
    notice: document.getElementById('models-action-status').textContent,
    editorStatus: document.getElementById('provider-settings-status').textContent,
    visibleText: document.getElementById('settings-panel-models').innerText,
    defaultOffer: !document.getElementById('set-added-model-default-btn').hidden,
    defaultChoices: [...document.getElementById('model-choice').options].map(o => o.textContent),
    editor: !document.getElementById('provider-editor').hidden,
    key: document.getElementById('provider-credential').value,
    calls: window.saveCalls,
  }));
}

async function submitThenClose(page, outcome, modelId) {
  await startSave(page, modelId);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#settings-overlay').isVisible(), false);
  await page.evaluate((ok) => window.finishSave(ok), outcome);
  await page.waitForTimeout(300);
  await page.evaluate(() => window.layoutTest.openSettings('models'));
  return modelsView(page);
}

async function saveThenClose(page, outcome) {
  await installSaveBridge(page);
  return submitThenClose(page, outcome);
}

test('a model saved while Settings was closed is listed and announced on reopen, saved once, never made default', async () => {
  const browser = await chromium.launch();
  try {
    const { page, errors } = await openShell(browser, { width: 1600, height: 1000 });
    const result = await saveThenClose(page, true);
    assert.match(result.list, /Synthetic saved model/);
    assert.match(result.notice, /synthetic\/model saved\. Your chats keep their current models\./);
    assert.ok(result.defaultChoices.some(label => /synthetic\/model/.test(label)), result.defaultChoices.join(' | '));
    assert.equal(result.editor, false);
    assert.equal(result.key, '');
    assert.deepEqual({ save: result.calls.save, default: result.calls.default, switch: result.calls.switch }, { save: 1, default: 0, switch: 0 });
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

test('a save that fails after Settings closed reports the failure on reopen without retrying', async () => {
  const browser = await chromium.launch();
  try {
    const { page, errors } = await openShell(browser, { width: 1600, height: 1000 });
    const result = await saveThenClose(page, false);
    assert.match(result.visibleText, /Could not save the model\..*Your saved models are unchanged\./);
    assert.doesNotMatch(result.list, /Synthetic saved model/);
    assert.deepEqual({ save: result.calls.save, default: result.calls.default, switch: result.calls.switch }, { save: 1, default: 0, switch: 0 });
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

test('a save the settings file refuses stays in setup and names the local file, not the service', async () => {
  const browser = await chromium.launch();
  try {
    const { page, errors } = await openShell(browser, { width: 1600, height: 1000 });
    await installSaveBridge(page);
    await startSave(page);
    await page.evaluate(() => window.finishSave(
      'command_rejected: Could not protect and save provider settings; the previous file is unchanged'));
    await page.waitForTimeout(300);
    const result = await modelsView(page);
    assert.equal(result.editor, true);
    assert.match(result.editorStatus, /settings file/);
    assert.match(result.editorStatus, /Your saved models are unchanged\./);
    assert.doesNotMatch(result.editorStatus, /service address|connection/);
    assert.doesNotMatch(result.notice, /saved\. Your chats/);
    assert.equal(result.defaultOffer, false);
    assert.deepEqual({ save: result.calls.save, default: result.calls.default, switch: result.calls.switch }, { save: 1, default: 0, switch: 0 });
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

// The editor is taller than the Settings pane, so a save's status must be scrolled into view to be seen.
for (const viewport of [{ width: 1400, height: 900 }, { width: 960, height: 640 }, { width: 390, height: 844 }]) {
  test(`a failed save shows its error inside the visible Settings pane at ${viewport.width}x${viewport.height}`, async () => {
    const browser = await chromium.launch();
    try {
      const { page, errors } = await openShell(browser, viewport);
      await installSaveBridge(page);
      await startSave(page);
      await page.evaluate(() => window.finishSave(
        'command_rejected: Could not protect and save provider settings; the previous file is unchanged'));
      await page.waitForTimeout(300);
      const seen = await page.evaluate(() => {
        const status = document.getElementById('provider-settings-status');
        let pane = status.parentElement;
        while (pane && !(/(auto|scroll)/.test(getComputedStyle(pane).overflowY) && pane.scrollHeight > pane.clientHeight)) pane = pane.parentElement;
        const box = status.getBoundingClientRect();
        const view = (pane || document.documentElement).getBoundingClientRect();
        const bottom = pane ? view.bottom : window.innerHeight;
        return { text: status.textContent, top: Math.round(box.top), bottom: Math.round(box.bottom),
          paneTop: Math.round(pane ? view.top : 0), paneBottom: Math.round(bottom) };
      });
      assert.match(seen.text, /settings file/);
      assert.ok(seen.top >= seen.paneTop && seen.bottom <= seen.paneBottom, JSON.stringify(seen));
      assert.deepEqual(errors, []);
    } finally {
      await browser.close();
    }
  });
}

test('reopening Settings after a failed save starts Models at its top with the failure in view', async () => {
  const browser = await chromium.launch();
  try {
    const { page, errors } = await openShell(browser, { width: 960, height: 640 });
    const result = await saveThenClose(page,
      'command_rejected: Could not protect and save provider settings; the previous file is unchanged');
    const view = await page.evaluate(() => {
      const content = document.querySelector('.settings-content');
      const notice = document.getElementById('models-action-status').getBoundingClientRect();
      const pane = content.getBoundingClientRect();
      return { scrollTop: content.scrollTop, noticeInView: notice.top >= pane.top && notice.bottom <= pane.bottom };
    });
    assert.deepEqual(view, { scrollTop: 0, noticeInView: true });
    assert.match(result.notice, /settings file/);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

for (const [name, failure, recovery] of [
  ['current native EOF', { message: 'Sylvia GM authority reply pipe closed' }, /Restart Sylvia/],
  ['committed save with native EOF', { message: 'Sylvia GM authority reply pipe closed', committed: true }, /Restart Sylvia/],
  ['unreadable native response', { message: 'Sylvia GM authority response could not be read' }, /Restart Sylvia/],
  ['old native exit', { message: 'Sylvia GM authority process exited' }, /Restart Sylvia/],
  ['browser outcome_unknown', { message: 'Unconfirmed response', code: 'outcome_unknown' }, /Reload \/ reconnect/],
  ['committed browser outcome_unknown', { message: 'Unconfirmed response', code: 'outcome_unknown', committed: true }, /Reload \/ reconnect/],
]) test(`a save cut off by ${name} stays unconfirmed without replay`, async () => {
  const browser = await chromium.launch();
  try {
    const { page, errors } = await openShell(browser, failure.committed
      ? { width: 390, height: 844 } : { width: 1600, height: 1000 });
    const result = await saveThenClose(page, failure);
    assert.match(result.visibleText, /before confirming the save/);
    assert.match(result.visibleText, recovery);
    assert.match(result.visibleText, /check Your models to see whether it was saved/);
    assert.match(result.visibleText, /Check before saving again/);
    assert.doesNotMatch(result.visibleText, /unchanged|saved\. Your chats/);
    assert.equal(result.defaultOffer, false);
    if (process.env.SYLVIA_WEBUI_QA_OUTPUT) await page.screenshot({ path: path.join(
      process.env.SYLVIA_WEBUI_QA_OUTPUT, `save-unknown-${name.replaceAll(' ', '-')}.png`) });
    // Restart/reconnect reloads owner settings through this read-only production path.
    await page.evaluate(() => window.layoutTest.refreshProviderSettings());
    const inspected = await modelsView(page);
    assert.equal(inspected.list.includes('Synthetic saved model'), Boolean(failure.committed),
      'read-only recovery discovers the actual saved state without resending the save');
    assert.deepEqual({ save: inspected.calls.save, default: inspected.calls.default, switch: inspected.calls.switch },
      { save: 1, default: 0, switch: 0 });
    assert.deepEqual({ save: result.calls.save, default: result.calls.default, switch: result.calls.switch }, { save: 1, default: 0, switch: 0 });
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

test('a later save that fails after Settings closed withdraws the earlier model\'s default offer', async () => {
  const browser = await chromium.launch();
  try {
    const { page, errors } = await openShell(browser, { width: 1600, height: 1000 });
    const first = await saveThenClose(page, true);
    assert.equal(first.defaultOffer, true);
    const result = await submitThenClose(page,
      'command_rejected: Could not protect and save provider settings; the previous file is unchanged', 'synthetic/second');
    assert.equal(result.defaultOffer, false);
    assert.match(result.notice, /settings file.*Your saved models are unchanged\./);
    assert.match(result.list, /Synthetic saved model/);
    assert.deepEqual({ save: result.calls.save, default: result.calls.default, switch: result.calls.switch }, { save: 2, default: 0, switch: 0 });
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

test('theme choices expose their pressed state and keep the saved choice', async () => {
  const browser = await chromium.launch();
  try {
    const { page } = await openShell(browser, { width: 1600, height: 1000 });
    await page.evaluate(() => window.layoutTest.openSettings('appearance'));
    await page.locator('.theme-btn[data-theme="sylvia-dark"]').click();
    assert.deepEqual(await page.locator('.theme-btn').evaluateAll(buttons =>
      buttons.map(button => [button.dataset.theme, button.getAttribute('aria-pressed')])),
    [['slate', 'false'], ['sylvia', 'false'], ['sylvia-dark', 'true']]);
    assert.equal(await page.evaluate(() => localStorage.getItem('sylvia-gm-theme')), 'sylvia-dark');
    assert.equal(await page.evaluate(() => document.body.dataset.theme), 'sylvia-dark');
  } finally {
    await browser.close();
  }
});

test('browser Dictation explains the desktop requirement instead of suggesting a reinstall', async () => {
  const browser = await chromium.launch();
  try {
    const { page } = await openShell(browser, { width: 1600, height: 1000 }, { browserInvoke: true });
    await page.evaluate(() => {
      window.getUserMediaCalls = 0;
      navigator.mediaDevices && (navigator.mediaDevices.getUserMedia = async () => { window.getUserMediaCalls += 1; });
      window.layoutTest.openSettings('dictation');
    });
    await page.waitForFunction(() => /desktop app/.test(document.getElementById('dictation-model-status').textContent));
    const status = await page.locator('#dictation-model-status').innerText();
    assert.doesNotMatch(status, /reinstall/i);
    assert.equal(await page.evaluate(() => window.getUserMediaCalls), 0);
  } finally {
    await browser.close();
  }
});

for (const viewport of [{ width: 1600, height: 1000 }, { width: 390, height: 844 }]) {
  test(`every Settings section opens at its top without sideways overflow at ${viewport.width}x${viewport.height}`, async () => {
    const browser = await chromium.launch();
    try {
      const { page, errors } = await openShell(browser, viewport);
      await page.evaluate(() => window.layoutTest.openSettings('models'));
      for (const tab of TABS) {
        await page.locator(`#settings-tab-${tab}`).click();
        const facts = await page.evaluate((tab) => {
          const content = document.querySelector('.settings-content');
          const nav = document.querySelector('.settings-nav').getBoundingClientRect();
          const button = document.getElementById(`settings-tab-${tab}`).getBoundingClientRect();
          const heading = document.querySelector(`#settings-panel-${tab} h2`).getBoundingClientRect();
          const dialog = document.querySelector('.settings-dialog').getBoundingClientRect();
          return {
            scrollTop: content.scrollTop,
            overflow: content.scrollWidth - content.clientWidth,
            tabVisible: button.left >= nav.left - 1 && button.right <= nav.right + 1 &&
              button.top >= nav.top - 1 && button.bottom <= nav.bottom + 1,
            headingVisible: heading.top >= dialog.top && heading.bottom <= dialog.bottom,
            dialogFits: dialog.right <= innerWidth + 1 && dialog.bottom <= innerHeight + 1,
          };
        }, tab);
        assert.deepEqual(facts, { scrollTop: 0, overflow: 0, tabVisible: true, headingVisible: true, dialogFits: true }, tab);
      }
      assert.deepEqual(errors, []);
    } finally {
      await browser.close();
    }
  });
}
