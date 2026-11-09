// The suite's fresh TD workspace keeps the roster strip usable: every squad card face and its Details control is
// fully visible (scrolling only inside the strip) at common and compact desktop sizes, on first load and after a
// redraw. Shipped shell over the delivered TD projection with a synthetic bridge; not native acceptance.
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
    delete projection.layout; // a fresh workspace: the suite default arrangement
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

// Fraction of the element inside its strip and the viewport, after bringing it into view inside the strip.
const visibleShare = (locator) => locator.evaluate(async (element) => {
  element.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  await new Promise(requestAnimationFrame);
  return new Promise((resolve) => { const observer = new IntersectionObserver(([entry]) => { observer.disconnect(); resolve(entry.intersectionRatio); });
    observer.observe(element); });
});

for (const viewport of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }]) {
  test(`suite roster strip shows every card and Details at ${viewport.width}x${viewport.height}`, { timeout: 60000 }, async () => {
    const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
      channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
    try {
      const { page, errors } = await open(browser, viewport);
      for (const pass of ['initial', 'redraw']) {
        if (pass === 'redraw') await page.evaluate(() => window.stripQA.renderRulesetPanelProjection(window.stripProjection));
        const strip = page.locator('[data-panel-id="operations.party"] .squad-list');
        const box = await strip.boundingBox();
        assert.ok(box && box.height >= 60, `${pass}: the strip has room for its cards (${JSON.stringify(box)})`);
        const faces = page.locator('[data-panel-id="operations.party"] [data-party-select-actor]');
        assert.ok(await faces.count() >= 2, `${pass}: squad cards render`);
        for (let index = 0; index < await faces.count(); index += 1) {
          assert.equal(await visibleShare(faces.nth(index)), 1, `${pass}: card face ${index} is fully visible`);
        }
        const details = page.locator('[data-panel-id="operations.party"] [data-character-expansion] > summary');
        for (let index = 0; index < await details.count(); index += 1) {
          assert.equal(await visibleShare(details.nth(index)), 1, `${pass}: Details ${index} is fully visible`);
        }
        await details.first().click();
        assert.equal(await page.locator('[data-panel-id="operations.party"] [data-character-expansion]').first().evaluate((node) => node.open), true);
        await details.first().click();
      }
      assert.deepEqual(errors, []);
      await page.close();
    } finally { await browser.close(); }
  });
}
