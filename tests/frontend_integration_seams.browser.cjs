// Integration seams between browser floating panel windows and character chat on Home.
// Shipped app shell, WebUI bridge and delivered TD map projection, with a scripted owner bridge for
// panels and character chat; real pointer input. Native detached windows are a separate host.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/td-delivered-map-controls.json'), 'utf8'));
const MAP = 'operations.map';
const evidence = process.env.SYLVIA_INTEGRATION_SEAMS_OUTPUT;

const launch = () => chromium.launch({ headless: true, args: ['--disable-gpu'],
  channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
const win = (page, panelId) => page.locator('[data-floating-window]').filter({ has: page.locator(`[data-panel-id="${panelId}"]`) });
const shot = (page, name) => evidence ? page.screenshot({ path: path.join(evidence, `${name}.png`), timeout: 10000 }) : null;

async function scene(browser, viewport = { width: 1600, height: 1000 }) {
  const page = await browser.newPage({ viewport });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://sylvia-seams.test') return route.abort();
    if (url.pathname.startsWith('/api/')) return route.fulfill({ json: { ok: true, result: [] } });
    const name = url.pathname.slice(1) || 'app.html', file = path.join(frontend, name);
    if (!file.startsWith(frontend + path.sep) || !fs.existsSync(file)) return route.abort();
    let body = fs.readFileSync(file);
    // Mirror the WebUI server's app.html injection (webui.py asset()).
    if (name === 'app.html') body = body.toString().replace('</head>', '<link rel="stylesheet" href="webui-mobile.css"></head>')
      .replace('<script src="panel-workspace.js">', '<script src="webui-bridge.js"></script><script src="webui-mobile.js"></script><script src="panel-workspace.js">');
    if (name === 'app.js') body = body.toString()
      .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.seamsTest = { state, wireControls, initSidebarResize, showHome,
        renderRulesetPanelProjection, renderSidebarState, showSessionView, gamePanels, ensureSessionActivity };
        // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css')
      ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-seams.test/app.html');
  await page.evaluate((projection) => {
    const api = window.seamsTest;
    localStorage.setItem('sylvia-gm-home-tutorial-v1', JSON.stringify({ status: 'completed', step: 0 }));
    document.getElementById('connecting-overlay').classList.add('hidden');
    document.getElementById('session-frame').style.display = 'flex';
    const vela = { artifact_id: 'character-profile-' + '1'.repeat(24) + '-' + 'a'.repeat(16), content_sha256: 'a'.repeat(64),
      name: 'Vela', summary: 'A travelling archivist.', has_greeting: true };
    const chats = new Map();
    window.failNext = false;
    window.panelCalls = 0;
    Object.assign(api.state, { view: 'session', activeCampaignId: projection.campaign_id, activeSessionId: 'seams-gm',
      activeSessionRole: 'dm', activeSnapshot: { revision: projection.revision }, campaignIndexStatus: 'ready',
      campaigns: [{ campaign_id: projection.campaign_id, display_name: 'Seams check', lifecycle: 'active',
        sessions: [{ session_id: 'seams-gm', role: 'dm', status: 'active' }] }],
      modelChoices: [{ provider_id: 'synthetic', model_id: 'model-a', provider_name: 'Synthetic', model_name: 'Model A' }],
      providerSettings: { default_model: { provider_id: 'synthetic', model_id: 'model-a' } },
      bridge: {
        snapshot: async () => ({ campaign_id: projection.campaign_id, revision: projection.revision }),
        campaign_panels: async () => { window.panelCalls += 1; return projection; },
        player_action_availability: async (command) => ({ campaign_id: command.campaign_id,
          revision: command.expected_revision, results: command.requests.map(() => ({ available: false, reason: 'Synthetic' })) }),
        execute_panel_action: async () => { throw new Error('Window management must not execute game actions'); },
        character_chat: async (command) => {
          if (command.action === 'catalog') return { cards: [vela], chats: [...chats.values()].map((chat) => ({ chat_id: chat.chat_id,
            character_name: 'Vela', artifact_id: vela.artifact_id, message_count: chat.messages.length,
            updated_at: '2026-11-14T00:00:00+00:00', preview: chat.messages.at(-1)?.text || '' })), unreadable_count: 0 };
          if (command.action === 'open') return JSON.parse(JSON.stringify(chats.get(command.chat_id)));
          const chat = { chat_id: 'character-chat-' + String(chats.size + 1).padStart(24, '0'),
            character: { artifact_id: vela.artifact_id, content_sha256: vela.content_sha256, name: 'Vela', summary: vela.summary },
            card_available: true, user_name: command.user_name || 'You', provider_id: command.provider_id, model_id: command.model_id,
            messages: [{ sequence: 1, role: 'character', text: '*looks up* Hello.' }] };
          chats.set(chat.chat_id, chat);
          return JSON.parse(JSON.stringify(chat));
        },
        send_character_chat_message: async (command) => {
          if (window.failNext) { window.failNext = false; throw Object.assign(new Error('The selected provider timed out while responding'), { code: 'provider_error' }); }
          const chat = chats.get(command.chat_id), n = chat.messages.length;
          chat.messages.push({ sequence: n + 1, role: 'user', text: command.text },
            { sequence: n + 2, role: 'character', text: `You said "${command.text}".` });
          return JSON.parse(JSON.stringify(chat));
        },
      } });
    api.ensureSessionActivity({ sessionId: 'seams-gm', sessionRole: 'dm', campaignId: projection.campaign_id });
    document.getElementById('chronology').innerHTML = '<div class="story-card talk"><p>Synthetic narration.</p></div>';
    api.wireControls(); api.initSidebarResize(); api.showSessionView();
    api.renderRulesetPanelProjection(projection);
    api.renderSidebarState();
  }, fixture);
  return { page, errors };
}

// Every floating window and the dock, as painted: state, frame and visibility.
const windowsPainted = (page) => page.evaluate(() => [...document.querySelectorAll('[data-floating-window], .floating-panel-dock')]
  .map((node) => {
    const rect = node.getBoundingClientRect(), style = getComputedStyle(node);
    return { id: node.dataset.floatingWindow || 'dock', state: node.dataset.windowState || null, position: style.position,
      visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' };
  }));
// The saved panel arrangement lives in this browser's storage, per campaign.
const savedWindows = (page) => page.evaluate(() => JSON.stringify(Object.keys(localStorage).sort().map((key) => [key, localStorage.getItem(key)])));

async function backToSession(page, fixtureValue) {
  await page.evaluate((projection) => {
    const api = window.seamsTest;
    // As opening a campaign does: the panel host first re-enters the campaign, then gets its panels.
    Object.assign(api.state, { view: 'session', activeCampaignId: projection.campaign_id, activeSessionId: 'seams-gm',
      activeSessionRole: 'dm', activeSnapshot: { revision: projection.revision } });
    api.showSessionView();
    api.renderRulesetPanelProjection(api.gamePanels().loading(projection.campaign_id, 'seams-gm'));
    api.renderRulesetPanelProjection(projection);
    api.renderSidebarState();
  }, fixtureValue);
}

test('Home hides floating windows; character chat opens above them and leaves their arrangement alone', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await scene(browser);
    await page.locator(`[data-panel-float="${MAP}"]`).click();
    const map = win(page, MAP);
    await map.waitFor();
    const size = await map.boundingBox();
    const bar = await map.locator('.floating-panel-titlebar').boundingBox();
    await page.mouse.move(bar.x + 40, bar.y + bar.height / 2);
    await page.mouse.down();
    await page.mouse.move(800 - size.width / 2 + 40, 500 - size.height / 2 + 17, { steps: 12 });
    await page.mouse.up();
    assert.equal(await map.getAttribute('data-window-state'), 'front');
    const frame = await map.boundingBox();
    const identity = await map.getAttribute('data-floating-window');
    const saved = await savedWindows(page);

    // Home: no floating window or dock is painted over it, and nothing about them is saved.
    await page.evaluate(() => window.seamsTest.showHome());
    await page.locator('#home-character-chat-btn').waitFor({ state: 'visible' });
    assert.deepEqual((await windowsPainted(page)).filter((item) => item.visible), [], 'Home paints no floating window or dock');
    await shot(page, 'seams-1-home-while-map-floats');

    // Character chat opens above everything; using it does not touch the campaign's windows.
    await page.locator('#home-character-chat-btn').click();
    await page.locator('#character-chat-start').click();
    await page.locator('#character-chat-log .cc-message').first().waitFor();
    const top = await page.locator('#character-chat-input').boundingBox();
    assert.equal(await page.evaluate(([x, y]) => Boolean(document.elementFromPoint(x, y)?.closest('dialog')),
      [top.x + top.width / 2, top.y + top.height / 2]), true, 'the chat dialog is topmost');
    await page.locator('#character-chat-input').fill('Hello Vela');
    await page.locator('#character-chat-input').press('Enter');
    await page.waitForFunction(() => document.getElementById('character-chat-log').innerText.includes('You said "Hello Vela".'));
    await page.evaluate(() => { window.failNext = true; });
    await page.locator('#character-chat-input').fill('Tell me about the archive.');
    await page.locator('#character-chat-send').click();
    await page.waitForFunction(() => /timed out/.test(document.getElementById('character-chat-status').innerText));
    assert.equal(await page.locator('#character-chat-input').inputValue(), 'Tell me about the archive.');
    await shot(page, 'seams-2-character-chat-above-floating-state');
    await page.locator('#character-chat-close').click();
    assert.equal(await savedWindows(page), saved, 'character chat leaves the saved window arrangement unchanged');

    // Back in the campaign: the same map window, in the same place, in front.
    await backToSession(page, fixture);
    await map.waitFor();
    assert.equal(await map.getAttribute('data-floating-window'), identity);
    const back = await map.boundingBox();
    assert.ok(Math.abs(back.x - frame.x) < 1.5 && Math.abs(back.y - frame.y) < 1.5, `map kept its place: ${JSON.stringify({ frame, back })}`);
    assert.equal(await map.getAttribute('data-window-state'), 'front');
    assert.equal(await page.locator('.floating-panel-dock').isVisible(), true);
    assert.equal(await page.locator('#character-chat-dialog[open]').count(), 0);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

test('narrow screens: inline panels in the campaign, and character chat fits on Home', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await scene(browser);
    await page.locator(`[data-panel-float="${MAP}"]`).click();
    await win(page, MAP).waitFor();
    await page.setViewportSize({ width: 700, height: 900 });
    await page.waitForFunction(() => !document.querySelector('.floating-panel-dock'));
    assert.deepEqual((await windowsPainted(page)).filter((item) => item.position === 'fixed' && item.visible), [],
      'no fixed floating window or dock at 700px');
    await page.evaluate(() => window.seamsTest.showHome());
    await page.locator('#home-character-chat-btn').click();
    const box = await page.locator('#character-chat-dialog').boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 700.5, `chat fits: ${JSON.stringify(box)}`);
    await shot(page, 'seams-3-narrow-character-chat');
    await page.locator('#character-chat-close').click();
    await page.setViewportSize({ width: 1600, height: 1000 });
    await backToSession(page, fixture);
    await win(page, MAP).waitFor({ state: 'attached' });
    assert.equal(await page.locator('.floating-panel-dock').isVisible(), true, 'widening restores the floating map and dock');
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
