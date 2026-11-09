// Render the shipped shell over the declared TD layout, including its command deck and appended queue.
// Scripted bridge: regression coverage for native map collapse, not native acceptance.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/td-delivered-map-controls.json'), 'utf8'));

async function open(browser, viewport) {
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
        renderSidebarState, showSessionView, ensureSessionActivity }; // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css'
      : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-strip.test/app.html');
  await page.evaluate((source) => {
    const projection = JSON.parse(JSON.stringify(source));
    const api = window.stripQA;
    document.getElementById('connecting-overlay').classList.add('hidden');
    document.getElementById('session-frame').style.display = 'flex';
    const queue = { campaign_id: projection.campaign_id, session_id: 'strip-gm', sequence_id: 'q', version: 1, status: 'ready',
      expected_revision: projection.revision, committed_action_ids: [], steps: [] };
    Object.assign(api.state, { view: 'session', activeCampaignId: projection.campaign_id, activeSessionId: 'strip-gm', activeSessionRole: 'dm',
      activeSnapshot: { revision: projection.revision }, actionSequence: queue,
      campaigns: [{ campaign_id: projection.campaign_id, display_name: 'Strip', lifecycle: 'active', sessions: [{ session_id: 'strip-gm', role: 'dm', status: 'active' }] }],
      bridge: { compose_action_sequence: async () => queue, action_sequence: async () => queue, snapshot: async () => ({ revision: projection.revision }),
        campaign_panels: async () => projection,
        player_action_availability: async (c) => ({ revision: c.expected_revision, results: c.requests.map(() => ({ available: true })) }) } });
    api.ensureSessionActivity({ sessionId: 'strip-gm', sessionRole: 'dm', campaignId: projection.campaign_id });
    api.wireControls(); api.initSidebarResize(); api.showSessionView();
    api.renderRulesetPanelProjection(projection); api.renderSidebarState();
    window.stripProjection = projection;
  }, fixture);
  return { page, errors };
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
