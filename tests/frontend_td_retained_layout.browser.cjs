// Render the shipped shell over fresh and retained TD layouts, including the command deck and appended queue.
// Scripted bridge: regression coverage for retained layout clipping, not native acceptance.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/td-delivered-map-controls.json'), 'utf8'));
// Current public package declarations: older map fixtures omit several shipped controls.
fixture.surfaces.find(surface => surface.id === 'operations.map').actions =
  JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/td-current-map-actions.json'), 'utf8'));

async function open(browser, viewport, { freshLayout = false } = {}) {
  const page = await browser.newPage({ viewport });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('https://sylvia-strip.test/**', async (route) => {
    const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
    const file = path.join(frontend, name);
    if (!file.startsWith(frontend) || !fs.existsSync(file)) return route.abort();
    let body = fs.readFileSync(file);
    if (name === 'app.js') body = body.toString()
      .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.stripQA = { state, wireControls, initSidebarResize, renderRulesetPanelProjection,
        renderSidebarState, showSessionView, ensureSessionActivity, sequenceMarkup }; // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css'
      : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-strip.test/app.html');
  await page.evaluate(({ source, freshLayout }) => {
    const projection = JSON.parse(JSON.stringify(source));
    if (freshLayout) delete projection.layout;
    const api = window.stripQA;
    document.getElementById('connecting-overlay').classList.add('hidden');
    document.getElementById('session-frame').style.display = 'flex';
    const queue = { campaign_id: projection.campaign_id, session_id: 'strip-gm', sequence_id: 'q', version: 1, status: 'ready',
      expected_revision: projection.revision, committed_action_ids: [], steps: [{ step_id: 'pending-crouch',
        actor: 'vesper', actor_name: 'Vesper', label: 'Crouch', status: 'pending',
        source: { kind: 'panel', surface_id: 'operations.character', action_id: 'stance-crouched' },
        selection: { actor: 'vesper' } }] };
    Object.assign(api.state, { view: 'session', activeCampaignId: projection.campaign_id, activeSessionId: 'strip-gm', activeSessionRole: 'dm',
      activeSnapshot: { revision: projection.revision }, actionSequence: queue,
      campaigns: [{ campaign_id: projection.campaign_id, display_name: 'Strip', lifecycle: 'active', sessions: [{ session_id: 'strip-gm', role: 'dm', status: 'active' }] }],
      bridge: { compose_action_sequence: async () => queue, action_sequence: async () => queue, snapshot: async () => ({ revision: projection.revision }),
        campaign_panels: async () => projection,
        preview_panel_action: async () => { throw new Error('Layout checks must not preview a commit'); },
        execute_panel_action: async () => { throw new Error('Layout checks must not commit an action'); },
        player_action_availability: async (c) => ({ revision: c.expected_revision, results: c.requests.map(() => ({ available: true })) }) } });
    api.ensureSessionActivity({ sessionId: 'strip-gm', sessionRole: 'dm', campaignId: projection.campaign_id });
    api.wireControls(); api.initSidebarResize(); api.showSessionView();
    api.renderRulesetPanelProjection(projection); api.renderSidebarState();
    window.stripProjection = projection;
  }, { source: fixture, freshLayout });
  return { page, errors };
}

for (const width of [860, 320]) {
  test(`expanded queue tools fit the card at panel width ${width}`, async () => {
    const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
      channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
    try {
      const { page, errors } = await open(browser, { width: 1030, height: 779 });
      await page.evaluate(width => {
        const panel = document.querySelector('.ruleset-queue-panel');
        panel.style.width = `${width}px`;
        const queue = window.stripQA.state.actionSequence;
        queue.steps[0].label = 'Cycle weapon';
        queue.steps[0].error = 'This character does not have enough action points to cycle the weapon.';
        panel.querySelector('.ruleset-panel-content').innerHTML = window.stripQA.sequenceMarkup();
      }, width);
      const tools = page.locator('.sequence-step-tools');
      await tools.locator('summary').click();
      const geometry = await tools.evaluate(node => {
        const card = node.closest('.sequence-step').getBoundingClientRect();
        const content = node.querySelector(':scope > div').getBoundingClientRect();
        const buttons = [...node.querySelectorAll('button')].map(button => {
          const rect = button.getBoundingClientRect();
          return { left: rect.left, right: rect.right, width: rect.width, height: rect.height,
            themed: button.classList.contains('btn-secondary') };
        });
        return { card: { left: card.left, right: card.right, width: card.width },
          content: { left: content.left, right: content.right, width: content.width }, buttons };
      });
      assert.ok(geometry.content.width > geometry.card.width * .8, JSON.stringify(geometry));
      for (const button of geometry.buttons) {
        assert.ok(button.left >= geometry.card.left && button.right <= geometry.card.right,
          `expanded control spills outside its card: ${JSON.stringify(geometry)}`);
        assert.ok(button.width >= 60 && button.height >= 24 && button.themed,
          `expanded control is cramped or unthemed: ${JSON.stringify(button)}`);
      }
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  });
}

for (const viewport of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }]) {
  test(`fresh TD layout exposes the map and Execute with all declared controls at ${viewport.width}x${viewport.height}`, async () => {
    const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
      channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
    try {
      const { page, errors } = await open(browser, viewport, { freshLayout: true });
      const map = page.locator('[data-panel-id="operations.map"]');
      const declared = fixture.surfaces.find(surface => surface.id === 'operations.map').actions;
      // The three wall declarations live in Edit walls; the other declarations form the hotbar.
      const wallActions = ['wall-draw', 'wall-extend', 'wall-connect'];
      const expectedActions = declared.filter(action => !wallActions.includes(action.id)).map(action => action.id).sort();
      const visibleActions = await map.locator('[data-tactical-action]').evaluateAll(nodes => nodes.map(node => node.dataset.tacticalAction).sort());
      assert.deepEqual(visibleActions, expectedActions);
      await map.locator('.tactical-toolbar [data-tactical-wall-edit]').click();
      const wallControls = await map.locator('[data-wall-commit]').evaluateAll(nodes => nodes.map(node => `wall-${node.dataset.wallCommit}`).sort());
      assert.deepEqual(wallControls, wallActions.sort());
      await map.locator('.tactical-toolbar [data-tactical-wall-edit]').click();
      const mapVisibility = await map.locator('.tactical-scroll').evaluate(node => new Promise(resolve => {
        const observer = new IntersectionObserver(([entry]) => {
          observer.disconnect(); resolve(entry.intersectionRect.height);
        }); observer.observe(node);
      }));
      assert.ok(mapVisibility >= 100, `the full toolbar clips the fresh map: ${mapVisibility}`);
      await page.locator('#queue-badge').click();
      const visibility = await page.locator('[data-panel-id="sylvia.action-queue"] [data-sequence-execute]')
        .evaluate(node => new Promise(resolve => {
          const observer = new IntersectionObserver(([entry]) => {
            observer.disconnect(); resolve(entry.intersectionRatio);
          }); observer.observe(node);
        }));
      assert.ok(visibility > 0.99, `Execute is clipped in the fresh layout: ${visibility}`);
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  });
}

for (const viewport of [{ width: 1402, height: 932 }, { width: 1366, height: 768 }]) {
  test(`retained TD layout keeps its battlefield visible at ${viewport.width}x${viewport.height}`, async () => {
    const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
      channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
    try {
      const { page, errors } = await open(browser, viewport);
      for (const pass of ['initial', 'redraw']) {
        if (pass === 'redraw') await page.evaluate(() => window.stripQA.renderRulesetPanelProjection(window.stripProjection));
        const measures = await page.locator('[data-panel-id="operations.map"] .tactical-scroll').evaluate(async (node) => {
          const content = node.closest('.ruleset-panel-content');
          const visible = await new Promise(resolve => {
            const observer = new IntersectionObserver(([entry]) => {
              observer.disconnect(); resolve(entry.intersectionRect.height);
            });
            observer.observe(node);
          });
          return { mapHeight: node.getBoundingClientRect().height,
            contentHeight: content.getBoundingClientRect().height, visible };
        });
        assert.ok(measures.mapHeight >= 100 && measures.contentHeight >= 100 && measures.visible >= 100,
          `${pass}: battlefield clipped by retained split: ${JSON.stringify(measures)}`);
      }
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  });
}


for (const viewport of [{ width: 1402, height: 900 }, { width: 1366, height: 736 }]) {
  test(`queue badge reveals Execute with all declared TD controls at ${viewport.width}x${viewport.height}`, async () => {
    const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
      channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
    try {
      const { page, errors } = await open(browser, viewport);
      await page.locator('#queue-badge').click();
      const execute = page.locator('[data-panel-id="sylvia.action-queue"] [data-sequence-execute]');
      const geometry = await execute.evaluate(async node => {
        const visibility = await new Promise(resolve => {
          const observer = new IntersectionObserver(([entry]) => {
            observer.disconnect(); resolve(entry.intersectionRatio);
          }); observer.observe(node);
        });
        const rect = node.getBoundingClientRect();
        return { visibility, top: rect.top, bottom: rect.bottom, viewport: innerHeight };
      });
      assert.ok(geometry.visibility > 0.99, `Execute remains clipped after Show queue: ${JSON.stringify(geometry)}`);
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  });
}
