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

async function open(browser, viewport, member = null) {
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
  await page.evaluate(([source, member]) => {
    const projection = JSON.parse(JSON.stringify(source));
    delete projection.layout; // a fresh workspace: the suite default arrangement
    // Optionally rename the first party member and give it a party role, in every surface that lists it.
    if (member) for (const surface of projection.surfaces) for (const participant of surface.data?.participants || []) {
      if (participant.id === 'hex') Object.assign(participant.data, member);
    }
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
  }, [fixture, member]);
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

// A party card's name, its vitals and its party-role label must all stay readable. With a long name, or a wider
// font than the one the layout was drawn with (Linux desktops usually give DejaVu Sans), the role used to run
// over the name and the HP and AP pips. The role now has its own line; nothing may overlap it or leave the card,
// the name keeps at least its first letters, and anything cut ends in an ellipsis. The two common sizes show the
// compact strip; the tall window shows the roomier list, where the role has a line of its own.
for (const viewport of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1440, height: 1700 }]) {
  for (const name of ['Tavin Bell', 'Tavin Bellweather of the Lantern Road']) {
    test(`party card "${name}" keeps name, vitals and role apart at ${viewport.width}x${viewport.height}`, { timeout: 60000 }, async () => {
      const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
        channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
      try {
        const { page, errors } = await open(browser, viewport, { name, party_role: 'player_character' });
        const card = page.locator('[data-panel-id="operations.party"] .squad-card').first();
        const geometry = await card.evaluate((node) => {
          const box = (element) => { if (!element) return null; const r = element.getBoundingClientRect();
            return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width }; };
          const name = node.querySelector('.sq-name strong');
          const role = node.querySelector('.panel-unit-role');
          const cut = (element) => element.scrollWidth > element.clientWidth + 0.5;
          // Where the text itself is drawn: a label's box can span a whole line it shares with another.
          const text = (element) => { const range = document.createRange(); range.selectNodeContents(element);
            const r = range.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width }; };
          const strip = node.closest('.squad-list');
          return { card: box(node), strip: box(strip), stripScrolls: strip.scrollHeight > strip.clientHeight + 1,
            details: text(node.querySelector('.roster-inspection > summary')),
            name: box(name), role: text(role), bars: box(node.querySelector('.sq-bars')),
            weapon: text(node.querySelector('.sq-weap')), roleText: role.textContent,
            nameCut: cut(name), nameEllipsis: getComputedStyle(name).textOverflow,
            roleCut: cut(role), roleEllipsis: getComputedStyle(role).textOverflow };
        });
        assert.equal(geometry.roleText, 'Player Character');
        const overlaps = (a, b) => a && b && Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5 &&
          Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5;
        // The strip shows the whole card without scrolling, as it does without a role.
        assert.equal(geometry.stripScrolls, false, `the card fits the strip: ${JSON.stringify(geometry)}`);
        for (const part of ['name', 'bars', 'weapon', 'details']) {
          assert.ok(!overlaps(geometry.role, geometry[part]), `the role overlaps the ${part}: ${JSON.stringify(geometry)}`);
        }
        for (const part of ['name', 'role']) {
          assert.ok(geometry[part].left >= geometry.card.left - 0.5 && geometry[part].right <= geometry.card.right + 0.5 &&
            geometry[part].bottom <= geometry.card.bottom + 0.5, `the ${part} stays inside the card: ${JSON.stringify(geometry)}`);
        }
        assert.ok(geometry.name.width >= 30, `the name keeps its first letters: ${JSON.stringify(geometry)}`);
        if (name === 'Tavin Bell') assert.equal(geometry.nameCut, false, 'a short name is shown whole');
        if (geometry.nameCut) assert.equal(geometry.nameEllipsis, 'ellipsis', 'a cut name ends in an ellipsis');
        if (geometry.roleCut) assert.equal(geometry.roleEllipsis, 'ellipsis', 'a cut role ends in an ellipsis');
        assert.deepEqual(errors, []);
        await page.close();
      } finally { await browser.close(); }
    });
  }
}
