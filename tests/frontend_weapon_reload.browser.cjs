// Shipped shell: the single Reload control appends only the planned declared steps to the owner's action queue,
// never twice, and leaves gameplay state to the owner. Synthetic bridge; not native or provider acceptance.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/td-delivered-character-selection.json')));

test('Reload queues the planned declared steps once and keeps the granular actions', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('https://sylvia-reload.test/**', async (route) => {
      const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
      const file = path.join(frontend, name);
      if (!file.startsWith(frontend) || !fs.existsSync(file)) return route.abort();
      let body = fs.readFileSync(file);
      if (name === 'app.js') body = body.toString()
        .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
        .replace('  // ---- Public API ----', 'window.reloadQA = { state, renderRulesetPanelProjection, ensureSessionActivity }; // ---- Public API ----');
      await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css'
        : name.endsWith('.png') ? 'image/png' : 'text/html' });
    });
    await page.goto('https://sylvia-reload.test/app.html');
    await page.evaluate((fixture) => {
      const api = window.reloadQA;
      document.getElementById('connecting-overlay').classList.add('hidden');
      document.getElementById('session-frame').style.display = 'flex';
      const character = JSON.parse(JSON.stringify(fixture.character));
      // Vesper holds the projected spent manual rifle with a fuller spare: magazine change, then cycle.
      character.data.entities.vesper.weapon = { ...character.data.entities.hostile_alpha.weapon, magazine: { rounds: 1 } };
      const projection = { campaign_id: 'reload-qa', ruleset_id: 'td', revision: 7, shot_effects: [],
        surfaces: [fixture.map, character], player_control: fixture.player_control };
      window.composed = [];
      let queue = null;
      Object.assign(api.state, { activeCampaignId: 'reload-qa', activeSessionId: 'reload-gm', activeSessionRole: 'dm',
        activeSnapshot: { revision: 7 }, view: 'session', campaignIndexStatus: 'ready', panelProjection: projection });
      api.state.tacticalSelections[fixture.map.id] = { actor: 'vesper' };
      api.ensureSessionActivity();
      api.state.bridge = {
        snapshot: async () => ({ campaign_id: 'reload-qa', revision: 7 }),
        campaign_panels: async () => projection,
        action_sequence: async () => queue,
        compose_action_sequence: async (command) => {
          window.composed.push(command.steps.map((step) => step.source.action_id));
          queue = { sequence_id: command.sequence_id, version: (queue?.version || 0) + 1, status: 'ready',
            steps: command.steps.map((step) => ({ ...step, status: 'pending' })) };
          return queue;
        },
        player_action_availability: async (command) => ({ campaign_id: 'reload-qa', revision: 7,
          results: command.requests.map(() => ({ available: true })) }),
      };
      api.renderRulesetPanelProjection(projection);
    }, fixture);
    const reload = page.locator('[data-weapon-reload="vesper"]');
    await reload.waitFor();
    assert.match(await page.locator('.weapon-reload-plan').innerText(), /Queues Reload.*→ Cycle weapon/);
    assert.equal(await page.locator('svg.weapon-rig[data-rig="bolt"] [data-rig-chamber="spent"]').count(), 1);
    await page.waitForFunction(() => !document.querySelector('[data-weapon-reload="vesper"]')?.disabled);
    await reload.click();
    await page.waitForFunction(() => window.composed.length >= 2);
    const composed = await page.evaluate(() => window.composed);
    assert.deepEqual(composed.at(-1), ['reload-retain', 'cycle'], 'both planned steps are queued in order');
    // A second press at the same revision does not queue the steps again.
    await page.waitForFunction(() => !document.querySelector('[data-weapon-reload="vesper"]')?.disabled);
    await page.locator('[data-weapon-reload="vesper"]').click();
    await page.waitForTimeout(400);
    assert.equal((await page.evaluate(() => window.composed)).length, composed.length);
    assert.equal(await page.locator('[data-panel-action-id="cycle"][data-panel-actor-id="vesper"]').count() > 0, true);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
