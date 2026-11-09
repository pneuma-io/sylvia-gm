// Rig motion in the shipped shell over committed revisions of the delivered TD character data: a run plays the
// committed manipulation once in its own panel, survives a same-revision redraw, and stops (caption back to the
// committed rest state, no later phase writes) on Off, Static, Skip, reduced motion, a campaign switch, an actor
// switch, a changed weapon, and reopen. Synthetic projection sequence; not native or provider acceptance.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const base = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/td-delivered-map-controls.json'), 'utf8'));
delete base.layout;
const manual = base.surfaces.find((surface) => surface.id === 'operations.character').data.entities.hostile_alpha.weapon;
function revision(rev, patch, campaign = base.campaign_id) {
  const projection = JSON.parse(JSON.stringify(base));
  Object.assign(projection, { revision: rev, campaign_id: campaign });
  for (const surface of projection.surfaces) if (surface.data?.entities?.vesper) surface.data.entities.vesper.weapon = { ...JSON.parse(JSON.stringify(manual)), ...patch };
  return projection;
}
const spent = { magazine: { id: 'm0', rounds: 1 }, spare_magazines: [{ id: 'm1', rounds: 5 }], chamber: 'SPENT', needs_cycle: true };
const reloaded = { magazine: { id: 'm1', rounds: 5 }, spare_magazines: [], retained_magazines: [{ id: 'm0', rounds: 1 }], chamber: 'SPENT', needs_cycle: true };
const cycled = { ...reloaded, magazine: { id: 'm1', rounds: 4 }, chamber: 'LIVE', needs_cycle: false };
const PHASES = /Magazine release|Retain in dump pouch|Draw fresh magazine|Insert & seat|Lift bolt|Draw back|Push forward|Lock down|cycle next/;

async function open(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('https://sylvia-rigmotion.test/**', async (route) => {
    const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
    const file = path.join(frontend, name);
    if (!file.startsWith(frontend) || !fs.existsSync(file)) return route.abort();
    let body = fs.readFileSync(file);
    if (name === 'app.js') body = body.toString()
      .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.rigQA = { state, wireControls, initSidebarResize, renderRulesetPanelProjection,
        renderSidebarState, showSessionView, ensureSessionActivity }; // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css'
      : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-rigmotion.test/app.html');
  await page.evaluate(() => {
    document.getElementById('connecting-overlay').classList.add('hidden');
    document.getElementById('session-frame').style.display = 'flex';
    // Every rig caption ever written, with the moment it was written, for "no later phase writes" checks.
    window.captionLog = [];
    new MutationObserver(() => document.querySelectorAll('[data-rig-phase]').forEach((node) =>
      window.captionLog.push([performance.now(), node.closest('[data-panel-id]')?.dataset.panelId, window.rigQA.state.activeCampaignId, node.textContent])))
      .observe(document.body, { subtree: true, childList: true, characterData: true });
  });
  return { page, errors };
}

async function show(page, projection, { session = 'rig-gm', actor = 'vesper' } = {}) {
  await page.evaluate(({ projection, session, actor }) => {
    const api = window.rigQA;
    const queue = { campaign_id: projection.campaign_id, session_id: session, sequence_id: 'q', version: 1, status: 'ready',
      expected_revision: projection.revision, committed_action_ids: [], steps: [] };
    const first = !api.state.activeCampaignId;
    Object.assign(api.state, { view: 'session', activeCampaignId: projection.campaign_id, activeSessionId: session, activeSessionRole: 'dm',
      activeSnapshot: { revision: projection.revision }, actionSequence: queue,
      campaigns: [{ campaign_id: projection.campaign_id, display_name: 'Rig', lifecycle: 'active', sessions: [{ session_id: session, role: 'dm', status: 'active' }] }],
      bridge: { compose_action_sequence: async () => queue, action_sequence: async () => queue, snapshot: async () => ({ revision: projection.revision }),
        campaign_panels: async () => projection,
        player_action_availability: async (c) => ({ revision: c.expected_revision, results: c.requests.map(() => ({ available: true })) }) } });
    if (api.state.characterInspection?.campaignId !== projection.campaign_id)
      api.state.characterInspection = { campaignId: projection.campaign_id, rulesetId: projection.ruleset_id, id: actor, unavailableId: null, expanded: [] };
    api.ensureSessionActivity({ sessionId: session, sessionRole: 'dm', campaignId: projection.campaign_id });
    if (first) { api.wireControls(); api.initSidebarResize(); api.showSessionView(); }
    api.renderRulesetPanelProjection(projection);
    if (first) api.renderSidebarState();
  }, { projection, session, actor });
}
const caption = (page) => page.locator('[data-panel-id="operations.character"] [data-rig-phase="vesper"]').innerText();
const mark = (page) => page.evaluate(() => performance.now());
const phaseWritesSince = (page, since) => page.evaluate(({ since, source }) => window.captionLog
  .filter(([at, , , text]) => at > since && new RegExp(source).test(text)), { since, source: PHASES.source });

async function startReload(page) {
  await show(page, revision(2, spent));
  await show(page, revision(3, reloaded));
  await page.waitForFunction(() => /Magazine release/.test(document.querySelector('[data-panel-id="operations.character"] [data-rig-phase="vesper"]')?.textContent || ''));
}

async function withPage(run) {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    const { page, errors } = await open(browser);
    await run(page);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

test('a committed magazine change plays once and survives a same-revision redraw', { timeout: 60000 }, () => withPage(async (page) => {
  await startReload(page);
  await page.evaluate(() => window.rigQA.renderRulesetPanelProjection(window.rigQA.state.panelProjection));
  await page.waitForFunction(() => /Draw fresh magazine|Insert & seat/.test(document.querySelector('[data-rig-phase="vesper"]')?.textContent || ''));
  await page.waitForFunction(() => !/Magazine|Draw|Insert|cycle next/.test(document.querySelector('[data-rig-phase="vesper"]')?.textContent || ''), null, { timeout: 4000 });
  assert.match(await caption(page), /Spent case chambered/, 'ends on the committed rest state');
  const after = await mark(page);
  await page.evaluate(() => window.rigQA.renderRulesetPanelProjection(window.rigQA.state.panelProjection));
  await page.waitForTimeout(600);
  assert.deepEqual(await phaseWritesSince(page, after), [], 'a redraw at the same revision never replays');
}));

for (const [label, interrupt] of [
  ['Off', (page) => page.evaluate(() => { const select = document.querySelector('[data-shot-visual]'); select.value = 'off'; select.dispatchEvent(new Event('change')); })],
  ['Static', (page) => page.evaluate(() => { const select = document.querySelector('[data-shot-visual]'); select.value = 'static'; select.dispatchEvent(new Event('change')); })],
  ['Skip', (page) => page.evaluate(() => document.querySelector('[data-shot-skip]').click())],
  ['reduced motion', (page) => page.emulateMedia({ reducedMotion: 'reduce' })],
]) {
  test(`${label} stops a running rig at once`, { timeout: 60000 }, () => withPage(async (page) => {
    await startReload(page);
    await interrupt(page);
    const since = await mark(page);
    await page.waitForTimeout(2200);
    assert.deepEqual(await phaseWritesSince(page, since), [], 'no phase is written after the interruption');
    assert.match(await caption(page), /Spent case chambered/);
    assert.equal(await page.locator('[data-rig-part="fx"] > *').count(), 0, 'no ejected case is left mid-air');
  }));
}

test('a campaign switch cancels the old run and never writes into the new campaign', { timeout: 60000 }, () => withPage(async (page) => {
  await startReload(page);
  await show(page, revision(1, { ...manual }, 'other-campaign'));
  const since = await mark(page);
  await page.waitForTimeout(1100);
  // The new campaign may show its focused actor first; inspect Vesper there too while the old timers would fire.
  await page.locator('[data-operator-pick="vesper"]').click();
  await page.waitForTimeout(1100);
  assert.deepEqual(await phaseWritesSince(page, since), [], 'the old run writes into no caption of the new campaign');
  for (const text of await page.locator('[data-rig-phase]').allInnerTexts()) assert.doesNotMatch(text, PHASES);
}));

test('reopening a campaign does not replay commits made while away', { timeout: 60000 }, () => withPage(async (page) => {
  await startReload(page);
  await show(page, revision(1, { ...manual }, 'other-campaign'));
  await show(page, revision(4, cycled));
  const since = await mark(page);
  await page.waitForTimeout(1600);
  assert.deepEqual(await phaseWritesSince(page, since), []);
  assert.match(await caption(page), /Ready/);
}));

test('switching the shown actor stops the run; switching back does not replay it', { timeout: 60000 }, () => withPage(async (page) => {
  await startReload(page);
  await page.locator('[data-operator-pick="hex"]').click();
  await page.locator('[data-operator-pick="vesper"]').click();
  const since = await mark(page);
  await page.waitForTimeout(2200);
  assert.deepEqual(await phaseWritesSince(page, since), []);
  assert.match(await caption(page), /Spent case chambered/);
}));

test('a different weapon at the next revision ends the run without animating a swap', { timeout: 60000 }, () => withPage(async (page) => {
  await startReload(page);
  const service = base.surfaces.find((surface) => surface.id === 'operations.character').data.entities.vesper.weapon;
  const next = revision(4, {});
  for (const surface of next.surfaces) if (surface.data?.entities?.vesper) surface.data.entities.vesper.weapon = JSON.parse(JSON.stringify(service));
  await show(page, next);
  const since = await mark(page);
  await page.waitForTimeout(2200);
  assert.deepEqual(await phaseWritesSince(page, since), []);
  assert.match(await caption(page), /Ready/);
}));
