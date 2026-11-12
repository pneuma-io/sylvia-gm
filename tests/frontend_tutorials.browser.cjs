// Shipped-shell component journeys with synthetic data; no real provider or desktop access.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const { lessons, mainLessons, expressions } = require('../src-tauri/frontend/tutorials.js');

test('every lesson expression resolves to preserved, explicitly packaged artwork', () => {
  const inventory = JSON.parse(fs.readFileSync(path.join(frontend, '../webui-data-files.json'), 'utf8'));
  const provenance = JSON.parse(fs.readFileSync(path.join(frontend, '../../branding/sprites/provenance.json'), 'utf8'));
  const crops = new Set(provenance.sprites.map(sprite => sprite.file.replace('src-tauri/frontend/', '')));
  for (const [id, lesson] of Object.entries(lessons)) {
    assert.equal(expressions[id].length, lesson.steps.length, id);
    for (const pose of expressions[id]) {
      for (const expression of pose === 'point' ? ['point-left-full', 'point-right-full', 'presenting'] : [pose]) {
        const file = 'assets/tutorial/sylvia-' + expression + '.png';
        assert.ok(crops.has(file), file);
        assert.ok(inventory.files.includes(file), file);
      }
    }
  }
});

async function fixture(browser, { legacy, saved, existing = false, configured = false, brokenStorage = false } = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(({ legacy, saved, brokenStorage }) => {
    if (legacy) localStorage.setItem('sylvia-gm-home-tutorial-v1', JSON.stringify(legacy));
    if (saved) localStorage.setItem('sylvia-gm-tutorials-v1', JSON.stringify(saved));
    if (brokenStorage) {
      Storage.prototype.getItem = () => { throw new Error('Unavailable storage'); };
      Storage.prototype.setItem = () => { throw new Error('Unavailable storage'); };
    }
  }, { legacy, saved, brokenStorage });
  await page.route('https://sylvia-tutorial.test/**', async route => {
    const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
    const file = path.resolve(frontend, name);
    if (!file.startsWith(frontend + path.sep) || !fs.existsSync(file)) return route.abort();
    let body = fs.readFileSync(file);
    if (name === 'app.js') body = body.toString().replace(
      "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', 'window.tutorialTest = { state, wireControls, showHome, showSessionView, ensureSessionActivity, openSettings, ' +
        'showSettingsTab, refreshSessionInputAdmission, controller: () => tutorials }; \n  // ---- Public API ----');
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
      name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-tutorial.test/');
  await page.evaluate(({ existing, configured }) => {
    const { state, wireControls, showHome } = window.tutorialTest;
    document.getElementById('connecting-overlay').style.display = 'none';
    document.getElementById('session-frame').style.display = 'flex';
    state.campaignIndexStatus = 'ready';
    state.campaigns = existing ? [{ campaign_id: 'synthetic', display_name: 'Synthetic adventure',
      lifecycle: 'active', sessions: [] }] : [];
    state.providerSettings = { presets: [
      { provider_id: 'openai', name: 'OpenAI', mode: 'remote', credential_required: true, base_url: 'https://api.example.test' },
      { provider_id: 'ollama', name: 'Ollama', mode: 'local', credential_required: false, endpoint_editable: true, base_url: 'http://localhost:11434' },
    ], configured: [], default_model: configured ? { provider_id: 'fixture', model_id: 'fixture' } : null };
    state.modelChoices = configured ? [{ provider_id: 'fixture', model_id: 'fixture' }] : [];
    window.commandCalls = [];
    state.bridge = {
      chatgpt_connection: async action => { window.commandCalls.push(action); return { signed_in: false }; },
      ready_campaign_catalog: async () => [{
        id: 'last-cart-through', title: 'Last Cart Through', summary: 'A driver needs help.',
        content_sha256: 'synthetic', party: [{ name: 'Bram', role: 'player_character',
          classes: [{ id: 'fighter', level: 2 }], summary: 'Prepared character' }],
      }],
      start_ready_campaign: async () => { window.commandCalls.push('start'); throw new Error('Synthetic interruption'); },
      submit_session_input: async () => { window.commandCalls.push('submit'); },
    };
    wireControls();
    showHome();
  }, { existing, configured });
  return { page, context, errors };
}
async function next(page) { await page.locator('#tutorial-next-btn').click(); }
async function pause(page) { await page.locator('#tutorial-pause-btn').click(); }
async function start(page, id) { await page.evaluate(id => window.tutorialTest.controller().start(id), id); }
async function session(page, { role = 'dm', ruleset = '', interview = false } = {}) {
  await page.evaluate(({ role, ruleset, interview }) => {
    const { state, showSessionView, ensureSessionActivity, refreshSessionInputAdmission } = window.tutorialTest;
    state.activeSessionId = 'synthetic-session';
    state.activeSessionRole = role;
    state.activeCampaignId = role === 'dm' || interview ? 'synthetic' : null;
    state.constructionDraftId = interview ? 'synthetic' : null;
    state.campaigns = [{ campaign_id: 'synthetic', ruleset_id: ruleset, lifecycle: 'active',
      sessions: [{ session_id: 'synthetic-session', role, status: 'active', provider_id: 'fixture', model_id: 'fixture' }] }];
    if (role === 'builder' && !interview) state.builderChats = [{ display_name: 'Synthetic Builder', session: {
      session_id: 'synthetic-session', role, status: 'active', provider_id: 'fixture', model_id: 'fixture',
    } }];
    state.panelProjection = { campaign_id: 'synthetic', ruleset_id: ruleset, surfaces: [] };
    ensureSessionActivity({ sessionId: state.activeSessionId, sessionRole: role, campaignId: state.activeCampaignId });
    showSessionView();
    document.getElementById('ruleset-panel-host').hidden = false;
    document.getElementById('view-switch').hidden = false;
    document.getElementById('ruleset-panels').innerHTML =
      '<button type="button" data-tactical-action="move">Move</button><button type="button" data-panel-menu="fixture">Arrange</button>';
    refreshSessionInputAdmission();
  }, { role, ruleset, interview });
}
async function screenshot(page, name) {
  if (!process.env.SYLVIA_TUTORIAL_SCREENSHOTS) return;
  fs.mkdirSync(process.env.SYLVIA_TUTORIAL_SCREENSHOTS, { recursive: true });
  await page.screenshot({ path: path.join(process.env.SYLVIA_TUTORIAL_SCREENSHOTS, name + '.png') });
}

test('optional tutorial suite in the shipped shell', { timeout: 120000 }, async t => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    await t.test('fresh welcome is optional, persistent, and introduces all independent lessons', async () => {
      const { page, context, errors } = await fixture(browser);
      await page.locator('#tutorial-welcome').waitFor({ state: 'visible' });
      await screenshot(page, 'welcome-desktop');
      await page.locator('#tutorial-welcome-explore').click();
      await page.locator('#home-tutorials-btn').click();
      assert.equal(await page.locator('.tutorial-lesson').count(), 12);
      for (const id of mainLessons) assert.ok(await page.getByText(lessons[id].title, { exact: true }).count());
      await screenshot(page, 'tutorial-library-desktop');
      await page.locator('#tutorial-hub-close').click();
      await page.evaluate(() => window.tutorialTest.showHome());
      assert.equal(await page.locator('#tutorial-welcome').isVisible(), false);
      assert.deepEqual(await page.evaluate(() => window.commandCalls), []);
      assert.deepEqual(errors, []);
      await context.close();
    });
    await t.test('direct subscription help needs no welcome or battle and never starts authentication', async () => {
      const { page, context, errors } = await fixture(browser, { existing: true });
      await page.evaluate(() => window.tutorialTest.openSettings('accounts'));
      await page.locator('[data-tutorial-offer="subscription"]').click();
      assert.match(await page.locator('#tutorial-title').innerText(), /ChatGPT account/);
      await next(page);
      assert.match(await page.locator('#tutorial-title').innerText(), /Sign in/);
      assert.equal(await page.locator('#tutorial-next-btn').isDisabled(), true);
      assert.deepEqual(await page.evaluate(() => window.commandCalls), ['status', 'status']);
      await page.setViewportSize({ width: 420, height: 800 });
      await screenshot(page, 'subscription-guide-narrow');
      const card = await page.locator('.tutorial-card').boundingBox();
      assert.ok(card.x >= 0 && card.x + card.width <= 421);
      await pause(page);
      const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('sylvia-gm-tutorials-v1')));
      assert.equal(saved.lessons.subscription.index, 1);
      await start(page, 'subscription');
      assert.match(await page.locator('#tutorial-title').innerText(), /Sign in/);
      assert.deepEqual(errors, []);
      await context.close();
    });
    await t.test('guided start recognizes an existing model and Start is always the player’s action', async () => {
      const { page, context, errors } = await fixture(browser, { configured: true });
      await page.locator('#tutorial-welcome-play').click();
      assert.equal(await page.locator('#ready-campaign-dialog').isVisible(), true);
      assert.equal(await page.locator('#home-tutorial').evaluate(el => el.parentElement.id), 'ready-campaign-dialog');
      await screenshot(page, 'ready-campaign-guide');
      await next(page); await next(page); await next(page);
      assert.equal(await page.locator('#tutorial-next-btn').isDisabled(), true);
      assert.deepEqual(await page.evaluate(() => window.commandCalls), []);
      await page.locator('#ready-campaign-start').click();
      // Start first offers the Game Master's model, effort and speed; nothing is sent yet.
      assert.equal(await page.locator('#chat-start-dialog').isVisible(), true);
      assert.deepEqual(await page.evaluate(() => window.commandCalls), []);
      await page.locator('#chat-start-confirm').click();
      assert.deepEqual(await page.evaluate(() => window.commandCalls), ['start']);
      assert.match(await page.locator('#ready-campaign-status').innerText(), /retry resumes the same copy/i);
      assert.match(await page.locator('#tutorial-title').innerText(), /Start when/);
      await pause(page);
      await context.close();
      assert.deepEqual(errors, []);
    });
    await t.test('all main lessons can be read independently without any game mutations', async () => {
      const { page, context, errors } = await fixture(browser, { existing: true });
      for (const id of mainLessons) {
        await start(page, id);
        for (let index = 0; index < lessons[id].steps.length; index += 1) {
          if (await page.locator('#tutorial-skip-step-btn').isVisible()) await page.locator('#tutorial-skip-step-btn').click();
          else await next(page);
        }
        await page.locator('#tutorial-finished').waitFor({ state: 'visible' });
        assert.equal(await page.locator('#tutorial-continue-btn').isVisible(), false);
        await page.locator('#tutorial-finished-close').click();
      }
      assert.deepEqual(await page.evaluate(() => window.commandCalls), []);
      assert.deepEqual(errors, []);
      await context.close();
    });
    await t.test('legacy completion and skipped preferences survive; malformed progress is bounded', async () => {
      for (const status of ['completed', 'skipped']) {
        const { page, context } = await fixture(browser, { legacy: { status, step: 3 } });
        assert.equal(await page.locator('#tutorial-welcome').isVisible(), false);
        const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('sylvia-gm-tutorials-v1')));
        assert.equal(saved.lessons.home.status, status);
        assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('sylvia-gm-home-tutorial-v1')).status), status);
        await context.close();
      }
      const { page, context, errors } = await fixture(browser, { saved: {
        welcomeSeen: true, lessons: { home: { status: 'in_progress', index: 999999 }, unknown: { status: 'completed' } },
      } });
      await start(page, 'home');
      assert.match(await page.locator('#tutorial-title').innerText(), /Settings/);
      assert.deepEqual(errors, []);
      await context.close();
    });
    await t.test('missing storage does not block onboarding or navigation', async () => {
      const { page, context, errors } = await fixture(browser, { brokenStorage: true });
      await page.locator('#tutorial-welcome-explore').click();
      await start(page, 'home');
      await next(page);
      await pause(page);
      await page.locator('#home-tutorials-btn').click();
      assert.equal(await page.locator('.tutorial-lesson').count(), 12);
      assert.deepEqual(errors, []);
      await context.close();
    });
    await t.test('subscription, API and local guides are independently reachable', async () => {
      const { page, context, errors } = await fixture(browser, { existing: true });
      for (const id of ['subscription', 'api', 'local']) {
        await start(page, id);
        assert.equal(await page.locator('#home-tutorial').isVisible(), true);
        await pause(page);
      }
      assert.ok((await page.evaluate(() => window.commandCalls)).every(action => action === 'status'));
      assert.deepEqual(errors, []);
      await context.close();
    });
    await t.test('first-scene lesson observes the selected session and real OOC interaction', async () => {
      const { page, context, errors } = await fixture(browser, { existing: true });
      await session(page);
      await start(page, 'play');
      assert.match(await page.locator('#tutorial-title').innerText(), /Read the scene/);
      await next(page);
      await page.evaluate(() => window.tutorialTest.controller().notify('player-message', 'background-session'));
      assert.match(await page.locator('#tutorial-title').innerText(), /Make your first choice/);
      assert.equal(await page.locator('#tutorial-next-btn').isDisabled(), true);
      await page.evaluate(() => window.tutorialTest.controller().notify('player-message', 'synthetic-session'));
      await page.locator('#ooc-note > summary').click();
      assert.match(await page.locator('#tutorial-title').innerText(), /Check your character/);
      await next(page);
      assert.equal(await page.locator('#tutorial-finished').isVisible(), true);
      assert.deepEqual(await page.evaluate(() => window.commandCalls), []);
      assert.deepEqual(errors, []);
      await context.close();
    });
    await t.test('combat guidance respects the Ruleset and waits for a committed action', async () => {
      const { page, context, errors } = await fixture(browser, { existing: true });
      await session(page);
      await start(page, 'combat');
      assert.match(await page.locator('#tutorial-title').innerText(), /Learn during/);
      assert.equal(await page.locator('#tutorial-next-btn').isDisabled(), true);
      assert.equal(await page.locator('[data-tutorial-offer="combat"]').isVisible(), false);
      await pause(page);
      await session(page, { ruleset: 'sylvia.srd-lite.bg3' });
      await start(page, 'combat');
      await next(page);
      await page.locator('[data-tactical-action="move"]').click();
      assert.match(await page.locator('#tutorial-title').innerText(), /Spend actions/);
      await page.evaluate(() => window.tutorialTest.controller().notify('action-committed', 'background-session'));
      assert.match(await page.locator('#tutorial-title').innerText(), /Spend actions/);
      await page.evaluate(() => window.tutorialTest.controller().notify('action-committed', 'synthetic-session'));
      assert.match(await page.locator('#tutorial-title').innerText(), /Read the result/);
      await next(page); await next(page);
      assert.equal(await page.locator('#tutorial-finished').isVisible(), true);
      assert.deepEqual(await page.evaluate(() => window.commandCalls), []);
      assert.deepEqual(errors, []);
      await context.close();
    });
    await t.test('connection handoff preserves the guided journey without creating a campaign', async () => {
      const { page, context, errors } = await fixture(browser);
      await page.locator('#tutorial-welcome-play').click();
      await next(page);
      await page.locator('#tutorial-routes').getByText('ChatGPT subscription', { exact: true }).click();
      await next(page);
      await page.locator('#tutorial-skip-step-btn').click();
      await page.locator('#tutorial-skip-step-btn').click();
      await page.evaluate(() => {
        const { state } = window.tutorialTest;
        state.modelChoices = [{ provider_id: 'fixture', model_id: 'fixture' }];
        state.providerSettings.default_model = { provider_id: 'fixture', model_id: 'fixture' };
        window.tutorialTest.controller().refresh();
      });
      await page.locator('#tutorial-finished').waitFor({ state: 'visible' });
      await page.locator('#tutorial-continue-btn').click();
      assert.equal(await page.locator('#ready-campaign-dialog').isVisible(), true);
      const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('sylvia-gm-tutorials-v1')));
      assert.equal(saved.journey, 'ready');
      assert.equal(saved.lessons.connect.status, 'completed');
      assert.ok((await page.evaluate(() => window.commandCalls)).every(action => action === 'status'));
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#home-tutorial').isVisible(), false);
      assert.equal(await page.locator('#ready-campaign-dialog').isVisible(), true);
      assert.deepEqual(errors, []);
      await context.close();
    });
    await t.test('an Interview lesson keeps its draft and skips stages absent from No Ruleset', async () => {
      const { page, context, errors } = await fixture(browser, { existing: true });
      await session(page, { role: 'builder', interview: true });
      await page.evaluate(() => { document.getElementById('construction-panel').hidden = false; });
      await start(page, 'customize');
      assert.match(await page.locator('#tutorial-title').innerText(), /Choose a Ruleset/);
      assert.equal(await page.evaluate(() => window.tutorialTest.state.activeCampaignId), 'synthetic');
      await page.evaluate(() => {
        window.tutorialTest.state.constructionStep = 1;
        window.tutorialTest.controller().refresh();
      });
      await page.waitForFunction(() => document.getElementById('tutorial-title').textContent === 'Name your campaign');
      assert.equal(await page.locator('#tutorial-next-btn').isDisabled(), true);
      await pause(page);
      assert.deepEqual(await page.evaluate(() => window.commandCalls), []);
      assert.deepEqual(errors, []);
      await context.close();
    });
    await t.test('a guided connection resumes its exact branch after restarting', async () => {
      const original = await fixture(browser);
      await original.page.locator('#tutorial-welcome-play').click();
      await next(original.page);
      await original.page.locator('#tutorial-routes').getByText('ChatGPT subscription', { exact: true }).click();
      await next(original.page);
      await pause(original.page);
      const saved = await original.page.evaluate(() => JSON.parse(localStorage.getItem('sylvia-gm-tutorials-v1')));
      await original.context.close();
      const { page, context, errors } = await fixture(browser, { saved });
      await page.locator('#tutorial-resume-btn').click();
      assert.match(await page.locator('#tutorial-title').innerText(), /Sign in through your browser/);
      assert.match(await page.locator('#tutorial-step').innerText(), /ChatGPT subscription/i);
      assert.ok((await page.evaluate(() => window.commandCalls)).every(action => action === 'status'));
      assert.deepEqual(errors, []);
      await context.close();
    });
    await t.test('Builder and layout tutorials use the current screen without sending or executing', async () => {
      const { page, context, errors } = await fixture(browser, { existing: true });
      await session(page, { role: 'builder' });
      await start(page, 'builder');
      assert.match(await page.locator('#tutorial-title').innerText(), /Describe the change/);
      await page.evaluate(() => window.tutorialTest.controller().notify('builder-message', 'synthetic-session'));
      await next(page); await next(page);
      await page.locator('#tutorial-finished-close').click();
      await session(page);
      await start(page, 'layout');
      await page.locator('[data-view-mode="story"]').click();
      assert.match(await page.locator('#tutorial-title').innerText(), /Resize/);
      await next(page);
      await page.locator('[data-panel-menu="fixture"]').click();
      assert.match(await page.locator('#tutorial-title').innerText(), /Keep or restore/);
      await next(page);
      assert.deepEqual(await page.evaluate(() => window.commandCalls), []);
      assert.deepEqual(errors, []);
      await context.close();
    });
    await t.test('Sylvia changes expression with her lesson and fits desktop and narrow screens', async () => {
      const { page, context, errors } = await fixture(browser, { existing: true });
      for (const [width, height, theme] of [[1440, 900, 'slate'], [420, 800, 'sylvia'], [320, 568, 'sylvia-dark']]) {
        await page.setViewportSize({ width, height });
        await page.evaluate(theme => { document.body.dataset.theme = theme; }, theme);
        await start(page, 'connect');
        await page.waitForFunction(() => {
          const image = document.getElementById('tutorial-sylvia');
          return image.complete && image.naturalWidth > 0 && image.src.endsWith('sylvia-thinking.png');
        });
        const guide = await page.locator('#tutorial-guide').boundingBox();
        const card = await page.locator('.tutorial-card').boundingBox();
        assert.ok(guide.x >= 0 && guide.x + guide.width <= width + 1);
        assert.ok(card.y >= 0 && card.y + card.height <= height + 1);
        assert.match(await page.locator('#tutorial-copy').innerText(), /we play|my thinking/);
        await screenshot(page, 'sylvia-thinking-' + width);
        await pause(page);
        await page.evaluate(() => window.tutorialTest.controller().start('home', { restart: true }));
        for (let i = 0; i < lessons.home.steps.length; i += 1) await next(page);
        await page.waitForFunction(() => {
          const image = document.querySelector('#tutorial-finished img');
          return image.complete && image.naturalWidth > 0;
        });
        await screenshot(page, 'sylvia-celebrating-' + width);
        await page.locator('#tutorial-finished-close').click();
      }
      assert.deepEqual(await page.evaluate(() => window.commandCalls), []);
      assert.deepEqual(errors, []);
      await context.close();
    });
  } finally { await browser.close(); }
});
