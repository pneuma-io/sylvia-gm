// The suite shows the package-declared turn-order values and spell categories (#29/#30) exactly as projected, and
// falls back to place numbers and resource-only spell lines when an entry is absent. The fixture is a real SRD-Lite
// owner projection (tests/test_lite_panel_combat.py case: Fire Bolt caster against a guard, begin_combat_turn
// committed). Shipped shell with a synthetic bridge; not native acceptance.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/srd-lite-combat-projection.json'), 'utf8'));

async function open(browser, projection) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('https://sylvia-order.test/**', async (route) => {
    const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
    const file = path.join(frontend, name);
    if (!file.startsWith(frontend) || !fs.existsSync(file)) return route.abort();
    let body = fs.readFileSync(file);
    if (name === 'app.js') body = body.toString()
      .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.orderQA = { state, wireControls, initSidebarResize, renderRulesetPanelProjection,
        renderSidebarState, showSessionView, ensureSessionActivity, updateTacticalSelection }; // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css'
      : name.endsWith('.png') ? 'image/png' : name.endsWith('.svg') ? 'image/svg+xml' : 'text/html' });
  });
  await page.goto('https://sylvia-order.test/app.html');
  await page.evaluate((projection) => {
    const api = window.orderQA;
    document.getElementById('connecting-overlay').classList.add('hidden');
    document.getElementById('session-frame').style.display = 'flex';
    const queue = { campaign_id: projection.campaign_id, session_id: 'order-gm', sequence_id: 'q', version: 1, status: 'ready',
      expected_revision: projection.revision, committed_action_ids: [], steps: [] };
    Object.assign(api.state, { view: 'session', activeCampaignId: projection.campaign_id, activeSessionId: 'order-gm', activeSessionRole: 'dm',
      activeSnapshot: { revision: projection.revision }, actionSequence: queue,
      campaigns: [{ campaign_id: projection.campaign_id, display_name: 'Order', lifecycle: 'active', sessions: [{ session_id: 'order-gm', role: 'dm', status: 'active' }] }],
      bridge: { compose_action_sequence: async () => queue, action_sequence: async () => queue, snapshot: async () => ({ revision: projection.revision }),
        campaign_panels: async () => projection,
        player_action_availability: async (c) => ({ revision: c.expected_revision, results: c.requests.map(() => ({ available: true })) }) } });
    api.ensureSessionActivity({ sessionId: 'order-gm', sessionRole: 'dm', campaignId: projection.campaign_id });
    const map = projection.surfaces.find((item) => ['tactical_grid', 'coordinate_map'].includes(item.renderer));
    api.state.tacticalSelections[map.id] = { actor: 'hero' };
    api.state.characterInspection = { campaignId: projection.campaign_id, rulesetId: projection.ruleset_id || null, id: 'hero', unavailableId: null, expanded: [] };
    api.wireControls(); api.initSidebarResize(); api.showSessionView();
    api.renderRulesetPanelProjection(projection);
    api.updateTacticalSelection(api.state.panelProjection.surfaces.find((item) => item.id === map.id), { actor: 'hero' });
    api.renderRulesetPanelProjection(api.state.panelProjection);
    api.renderSidebarState();
  }, projection);
  return { page, errors };
}

const launch = () => chromium.launch({ headless: true, args: ['--disable-gpu'],
  channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
const variant = (edit) => { const copy = JSON.parse(JSON.stringify(fixture)); edit(copy); return copy; };
const surface = (projection, id) => projection.surfaces.find((item) => item.id === id);
const ladder = (page) => page.evaluate(() => {
  const root = document.querySelector('.ladder');
  return root && {
    now: root.querySelector('.ladder-now')?.dataset.panelEntity,
    vitals: [...root.querySelectorAll('.ladder-now .ladder-vitals span')].map((node) => node.textContent),
    rungs: [...root.querySelectorAll('.ladder-rung')].map((node) => {
      const badge = node.querySelector('.ladder-init');
      return { id: node.dataset.panelEntity, text: badge.textContent, label: badge.getAttribute('aria-label'), value: badge.dataset.orderValue ?? null };
    }),
  };
});
const fireBolt = (page) => page.evaluate(() => {
  const card = document.querySelector('[data-grimoire-cast="cast-fire_bolt"]');
  return card && { category: card.dataset.spellCategory ?? null, line: card.querySelector('.grim-spell-main small')?.textContent ?? null };
});

test('real owner projection: the ladder shows each recorded order value and the place stays announced', { timeout: 60000 }, async () => {
  const values = surface(fixture, 'dnd-encounter').data.order_values;
  assert.deepEqual(values, { hero: 39, guard: 13 }, 'fixture carries the committed totals');
  const browser = await launch();
  try {
    const { page, errors } = await open(browser, fixture);
    const view = await ladder(page);
    assert.equal(view.now, 'hero');
    assert.ok(view.vitals.includes('Order 1 · value 39'), JSON.stringify(view.vitals));
    assert.deepEqual(view.rungs, [{ id: 'guard', text: '13', label: 'Turn order 2, order value 13', value: '13' }]);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('an actor without a recorded value keeps its place number; no value is invented', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    for (const [name, edit] of [['absent map', (data) => { delete data.order_values; }], ['guard missing', (data) => { delete data.order_values.guard; }],
      ['non-numeric', (data) => { data.order_values.guard = '13'; }]]) {
      const { page, errors } = await open(browser, variant((copy) => edit(surface(copy, 'dnd-encounter').data)));
      const view = await ladder(page);
      assert.deepEqual(view.rungs, [{ id: 'guard', text: '2', label: 'Turn order 2', value: null }], name);
      assert.ok(view.vitals.includes(name === 'absent map' ? 'Order 1' : 'Order 1 · value 39'), `${name}: ${JSON.stringify(view.vitals)}`);
      assert.deepEqual(errors, [], name);
      await page.close();
    }
  } finally { await browser.close(); }
});

test('spell cards show the projected category beside the resource, and only the resource without one', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    let { page, errors } = await open(browser, fixture);
    assert.deepEqual(await fireBolt(page), { category: 'evocation', line: 'Evocation · Cantrip' });
    assert.deepEqual(errors, []);
    await page.close();
    // The caster's own surface lacks the map: another projected surface's map is used.
    ({ page, errors } = await open(browser, variant((copy) => { delete surface(copy, 'dnd-character').data.spell_categories; })));
    assert.deepEqual(await fireBolt(page), { category: 'evocation', line: 'Evocation · Cantrip' });
    await page.close();
    ({ page, errors } = await open(browser, variant((copy) => { for (const item of copy.surfaces) if (item.data) delete item.data.spell_categories; })));
    assert.deepEqual(await fireBolt(page), { category: null, line: 'Cantrip' });
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});
