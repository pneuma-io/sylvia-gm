// Shipped shell and panel family over a delivered TD projection with a synthetic bridge.
// Layout and interaction proof only; not native window, provider or installed acceptance.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/td-delivered-map-controls.json'), 'utf8'));

async function open(browser, viewport, { previewAvailable = false } = {}) {
  const page = await browser.newPage({ viewport });
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('https://sylvia-layout.test/**', async (route) => {
    const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
    const file = path.join(frontend, name);
    if (!file.startsWith(frontend) || !fs.existsSync(file)) return route.abort();
    let body = fs.readFileSync(file);
    if (name === 'app.js') body = body.toString()
      .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.layoutTest = { state, wireControls, initSidebarResize,
        renderRulesetPanelProjection, renderSidebarState, showSessionView, gamePanels, renderBuilderChatRail,
        updateTacticalSelection, tacticalTargetSelection, setSidebarCollapsed, ensureSessionActivity };
        // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css')
      ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-layout.test/app.html');
  await page.evaluate(({ projection, previewAvailable }) => {
    const api = window.layoutTest;
    const calls = window.layoutCalls = { compose: [], execute: 0 };
    document.getElementById('connecting-overlay').classList.add('hidden');
    document.getElementById('session-frame').style.display = 'flex';
    const queue = { campaign_id: projection.campaign_id, session_id: 'layout-gm', sequence_id: 'layout-queue',
      version: 1, status: 'ready', expected_revision: projection.revision, committed_action_ids: [],
      steps: [{ step_id: 'step-1', label: 'Aim', status: 'pending', actor: 'vesper', actor_name: 'Vesper',
        selection: { target: 'hostile_alpha' }, preview: { summary: [{ label: 'AP cost', value: 1 }] } }] };
    Object.assign(api.state, { view: 'session', activeCampaignId: projection.campaign_id, activeSessionId: 'layout-gm',
      activeSessionRole: 'dm', activeSnapshot: { revision: projection.revision }, actionSequence: queue,
      campaigns: [{ campaign_id: projection.campaign_id, display_name: 'Layout check', lifecycle: 'active',
        ruleset_label: 'Trigger Discipline 0.1.24', campaign_setting_label: 'Siren Protocol 0.2.0',
        sessions: [{ session_id: 'layout-gm', role: 'dm', status: 'active' }] }],
      bridge: { compose_action_sequence: async command => { calls.compose.push(command); return queue; },
        action_sequence: async () => queue,
        snapshot: async () => ({ campaign_id: projection.campaign_id, revision: projection.revision }),
        campaign_panels: async () => projection,
        execute_action_sequence: async () => { calls.execute += 1; throw new Error('Unexpected execution'); },
        execute_panel_action: async () => { calls.execute += 1; throw new Error('Unexpected execution'); },
        player_action_availability: async (command) => ({ campaign_id: command.campaign_id,
          revision: command.expected_revision, results: command.requests.map(request => ({
            available: previewAvailable && request.source.action_id === 'aim',
            reason: previewAvailable && request.source.action_id === 'aim' ? '' : 'Synthetic preview refusal',
          })) }) } });
    document.getElementById('session-title').textContent = 'Layout check';
    api.ensureSessionActivity({ sessionId: 'layout-gm', sessionRole: 'dm', campaignId: projection.campaign_id });
    document.getElementById('chronology').innerHTML = '<div class="story-card talk"><p>Synthetic narration.</p></div>';
    api.wireControls(); api.initSidebarResize(); api.showSessionView();
    api.renderRulesetPanelProjection(projection);
    api.renderSidebarState();
  }, { projection: fixture, previewAvailable });
  return { page, errors };
}

const box = (page, selector) => page.locator(selector).boundingBox();

test('shell navigation, Story width presets, Board fold, divider keys and panel strip', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    const { page, errors } = await open(browser, { width: 1600, height: 1000 });
    assert.ok(await page.locator('.app-rail-logo').evaluate((image) => image.complete && image.naturalWidth > 0),
      'the rail shows the real Sylvia logo asset');
    assert.equal(await page.locator('#rail-play-btn').getAttribute('aria-current'), 'page');
    assert.equal(await page.locator('#session-subtitle').innerText(), 'Trigger Discipline 0.1.24 · Siren Protocol 0.2.0');
    assert.equal(await page.locator('#queue-badge').innerText(), '≡ 1 queued');
    assert.equal(await page.locator('[data-view-mode="balanced"]').getAttribute('aria-pressed'), 'true',
      'a fresh map workspace opens Balanced');

    const columns = await box(page, '#play-columns');
    await page.locator('[data-view-mode="story"]').click();
    const story = await box(page, '#story-column');
    assert.ok(Math.abs(story.width / (columns.width - 14) - 0.58) < 0.02, `Story share ${story.width / columns.width}`);
    assert.equal(await page.locator('[data-view-mode="story"]').getAttribute('aria-pressed'), 'true');
    const saved = await page.evaluate(() => window.layoutTest.gamePanels().snapshot().sidebar_width);
    assert.equal(saved, Math.round((await box(page, '#right-sidebar')).width), 'the preset is saved as the panel width');
    const clipped = await page.locator('[data-panel-chip]').evaluateAll((chips) => chips.filter((chip) => {
      const area = chip.closest('.panel-strip').getBoundingClientRect();
      const rect = chip.getBoundingClientRect();
      return rect.left < area.left - 0.5 || rect.right > area.right + 0.5;
    }).map((chip) => chip.textContent));
    assert.deepEqual(clipped, [], 'every panel chip stays visible at the Story split');

    await page.locator('[data-view-mode="board"]').click();
    assert.equal(await page.locator('#story-column').isVisible(), false);
    assert.equal(await page.locator('#story-rail-btn').isVisible(), true);
    assert.equal(await page.locator('#story-rail-unread').isVisible(), false);
    assert.deepEqual(JSON.parse(await page.evaluate(() => localStorage.getItem('sylvia-gm-sidebar-visibility'))).story, true);
    await page.evaluate(() => document.getElementById('chronology')
      .insertAdjacentHTML('beforeend', '<div class="story-card talk"><p>New narration.</p></div>'));
    await page.locator('#story-rail-unread').waitFor({ state: 'visible' });
    await page.locator('#story-rail-btn').click();
    assert.equal(await page.locator('#story-column').isVisible(), true);
    assert.equal(await page.locator('#story-rail-unread').isVisible(), false);
    assert.equal(Math.round((await box(page, '#right-sidebar')).width), saved, 'expanding Story restores the saved width');

    const handle = page.locator('#sidebar-resize-handle');
    await handle.focus();
    const before = (await box(page, '#right-sidebar')).width;
    await page.keyboard.press('ArrowRight');
    assert.equal((await box(page, '#right-sidebar')).width, before + 16);
    await page.keyboard.press('End');
    assert.ok((await box(page, '#story-column')).width >= 320, 'Story keeps its minimum width');
    assert.match(await handle.getAttribute('aria-valuetext'), /^Game panels \d+ pixels · Story \d+%$/);
    await page.keyboard.press('Home');
    assert.equal((await box(page, '#right-sidebar')).width, 220);
    // At the narrowest split, wrapped chips use the strip's full width rather than a column beside the label.
    const rows = await page.locator('[data-panel-chip]').evaluateAll((chips) => {
      const strip = chips[0].closest('.panel-strip').getBoundingClientRect();
      const first = chips[0].getBoundingClientRect().top;
      return { wrappedLefts: chips.map((chip) => chip.getBoundingClientRect())
        .filter((rect) => rect.top > first + 1).map((rect) => Math.round(rect.left - strip.left)) };
    });
    assert.ok(rows.wrappedLefts.length > 0, 'the fixture wraps at 220 pixels');
    assert.equal(Math.min(...rows.wrappedLefts), 0, 'wrapped chips start at the strip edge');

    const known = fixture.surfaces.length + 1;
    assert.ok(await page.locator('[data-panel-chip]').count() <= known);
    await page.evaluate(() => window.layoutTest.gamePanels().changed({ type: 'hide', panelId: 'operations.loadout' }));
    const chip = page.locator('[data-panel-chip="operations.loadout"]');
    assert.equal(await chip.getAttribute('data-panel-placement'), 'hidden');
    await chip.click();
    assert.notEqual(await page.locator('[data-panel-chip="operations.loadout"]').getAttribute('data-panel-placement'), 'hidden');

    await page.locator('[data-view-mode="balanced"]').click();
    const map = fixture.surfaces.find((surface) => surface.renderer === 'tactical_grid');
    const groups = await page.locator('[data-action-group]').evaluateAll((nodes) => nodes.map((node) => node.dataset.actionGroup));
    const order = ['self', 'move', 'target', 'area', 'devices'];
    assert.deepEqual(groups, order.filter((id) => groups.includes(id)), 'groups keep a stable order');
    const buttons = await page.locator('.map-action-palette [data-tactical-action]').evaluateAll((nodes) =>
      nodes.map((node) => node.dataset.tacticalAction));
    assert.equal(new Set(buttons).size, buttons.length, 'each declared action appears once');
    assert.ok(buttons.every((id) => map.actions.some((action) => action.id === id)), 'no invented actions');
    const hostile = map.data.entities.find((entity) => entity.display?.side === 'hostiles');
    if (hostile) assert.equal(await page.locator(`[data-tactical-entity="${hostile.id}"]`).getAttribute('data-tone'), 'hostile');

    await page.locator('#queue-badge').click();
    await page.waitForFunction(() => document.activeElement?.closest?.('.action-sequence'));
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

test('narrow windows switch between game panels and Story without horizontal overflow', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    const { page, errors } = await open(browser, { width: 390, height: 844 });
    assert.equal(await page.locator('[data-view-mode="balanced"]').isVisible(), false);
    assert.equal(await page.locator('#right-sidebar').isVisible(), true, 'a game workspace opens on its panels');
    assert.equal(await page.locator('#story-column').isVisible(), false);
    await page.locator('[data-view-mode="story"]').click();
    assert.equal(await page.evaluate(() => document.getElementById('session-frame').dataset.browserPane), 'chat');
    assert.equal(await page.locator('#story-column').isVisible(), true);
    assert.equal(await page.locator('#right-sidebar').isVisible(), false);
    assert.equal(await page.locator('[data-view-mode="story"]').getAttribute('aria-pressed'), 'true');
    await page.locator('[data-view-mode="board"]').click();
    assert.equal(await page.locator('#right-sidebar').isVisible(), true);
    const rail = await box(page, '#app-rail');
    assert.ok(rail.y + rail.height <= 844 && rail.y > 700, 'navigation sits at the bottom');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

test('queue badge truth and reveal, initial framing, map overview and preview-only language', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    const { page, errors } = await open(browser, { width: 1600, height: 1000 });
    const map = fixture.surfaces.find((surface) => surface.renderer === 'tactical_grid');
    const scroll = page.locator('[data-panel-id="operations.map"] .tactical-scroll');
    const inside = (id) => page.evaluate((id) => {
      const view = document.querySelector('[data-panel-id="operations.map"] .tactical-scroll').getBoundingClientRect();
      const token = document.querySelector('[data-tactical-entity="' + id + '"]').getBoundingClientRect();
      const x = (token.left + token.right) / 2, y = (token.top + token.bottom) / 2;
      return x > view.left && x < view.right && y > view.top && y < view.bottom;
    }, id);
    for (const id of ['vesper', 'hex', 'hostile_alpha']) assert.equal(await inside(id), true, id + ' framed initially');

    const setSteps = (statuses, status = 'ready') => page.evaluate(({ statuses, status }) => {
      const api = window.layoutTest;
      api.state.actionSequence = { ...api.state.actionSequence, status, version: api.state.actionSequence.version + 1,
        steps: statuses.map((value, index) => ({ step_id: 's' + index, label: 'Step ' + index, status: value,
          actor: 'vesper', actor_name: 'Vesper', selection: {}, preview: { summary: [] } })) };
      api.renderRulesetPanelProjection(api.state.panelProjection);
    }, { statuses, status });
    await setSteps(['committed', 'executing', 'awaiting_choice', 'pending', 'pending']);
    assert.equal(await page.locator('#queue-badge').getAttribute('aria-label'),
      '2 queued, 1 sending · unconfirmed, 1 awaiting choice · show queue');
    assert.match(await page.locator('.sequence-note').innerText(), /Sending · waiting for Sylvia’s confirmation/);
    await setSteps(['committed', 'pending'], 'failed');
    assert.equal(await page.locator('#queue-badge').getAttribute('aria-label'), '1 queued · needs review · show queue');
    assert.equal(await page.locator('.sequence-note').count(), 0, 'a stopped queue is not described as ready to run');
    await setSteps(['committed']);
    assert.equal(await page.locator('#queue-badge').isVisible(), false, 'committed steps are not counted');
    await setSteps(['pending']);
    assert.match(await page.locator('.sequence-note').innerText(), /Not yet run · each pending intent is rechecked on Execute/);

    await page.evaluate(() => window.layoutTest.gamePanels().changed({ type: 'hide', panelId: 'sylvia.action-queue' }));
    await page.evaluate(() => window.layoutTest.setSidebarCollapsed('right', true));
    assert.equal(await page.locator('.action-sequence').count(), 0);
    await page.locator('#queue-badge').click();
    await page.waitForFunction(() => document.activeElement?.closest?.('.action-sequence'));
    assert.equal(await page.locator('#right-sidebar').isVisible(), true);

    const toggle = page.locator('[data-panel-id="operations.map"] [data-map-offview]');
    assert.equal(await toggle.isVisible(), true);
    assert.match(await toggle.innerText(), /^(\d+ off view|Overview)$/);
    assert.equal(await page.locator('[data-map-overview]').isVisible(), false, 'the overview does not cover the map by default');
    await toggle.click();
    const overview = page.locator('[data-map-overview]');
    assert.equal(await overview.isVisible(), true);
    const area = await scroll.boundingBox(), panel = await overview.boundingBox();
    assert.ok(panel.y > area.y + area.height / 2 && panel.y + panel.height <= area.y + area.height,
      'the overview sits at the bottom of the visible map');
    const viewWidth = () => page.locator('[data-map-overview-view]').evaluate((node) => parseFloat(node.style.width));
    const before = await viewWidth();
    await page.setViewportSize({ width: 1200, height: 1000 });
    await page.waitForFunction((value) => parseFloat(document.querySelector('[data-map-overview-view]').style.width) !== value, before);
    const narrowed = await viewWidth();
    await page.locator('#sidebar-resize-handle').focus();
    await page.keyboard.press('End');
    await page.waitForFunction((value) => parseFloat(document.querySelector('[data-map-overview-view]').style.width) > value, narrowed);
    const moved = await overview.boundingBox(), resized = await scroll.boundingBox();
    assert.ok(moved.y + moved.height <= resized.y + resized.height, 'the overview follows the resized map');

    await page.evaluate((mapId) => {
      const api = window.layoutTest;
      const surface = api.state.panelProjection.surfaces.find((item) => item.id === mapId);
      api.updateTacticalSelection(surface, { actor: 'vesper', ...api.tacticalTargetSelection(surface, 'hostile_alpha') });
      api.state.tacticalViews[mapId].previewActionId = 'aim';
      api.renderRulesetPanelProjection(api.state.panelProjection);
    }, map.id);
    await page.locator('.preview-only-note').waitFor();
    assert.equal(await page.locator('.preview-only-note').innerText(),
      'Preview only · queuing adds an unvalidated intent; Sylvia’s rules recheck it on Execute.');
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

// Restore the queue as a wide lower row, shorten the map's split and keep every map control reachable.
async function shortMapWithPreview(page, ratio) {
  await page.evaluate((ratio) => {
    const api = window.layoutTest, panels = api.gamePanels();
    panels.changed({ type: 'hide', panelId: 'sylvia.action-queue' });
    panels.reveal('sylvia.action-queue');
    const root = panels.snapshot().sidebar;
    if (root.kind === 'split') panels.changed({ type: 'ratio', nodeId: root.id, ratio });
    const surface = api.state.panelProjection.surfaces.find((item) => item.id === 'operations.map');
    api.updateTacticalSelection(surface, { actor: 'vesper', ...api.tacticalTargetSelection(surface, 'hostile_alpha') });
    api.state.tacticalViews[surface.id].previewActionId = 'aim';
    api.renderRulesetPanelProjection(api.state.panelProjection);
  }, ratio);
  await page.locator('.preview-only-note').waitFor();
}

// True when the element's centre is inside the map content's visible box and not covered by another layer.
const reachable = (page, selector, bounds = '.ruleset-panel-content') => page.evaluate(({ selector, bounds }) => {
  const panel = document.querySelector('[data-panel-id="operations.map"]');
  const content = panel.querySelector(bounds);
  const node = panel.querySelector(selector);
  const view = content.getBoundingClientRect(), rect = node.getBoundingClientRect();
  const x = rect.left + Math.min(rect.width / 2, 20), y = rect.top + Math.min(rect.height / 2, 8);
  const hit = document.elementFromPoint(x, y);
  return rect.height > 0 && y > view.top && y < view.bottom && Boolean(hit && (node === hit || node.contains(hit)));
}, { selector, bounds });

for (const [label, viewport, ratio] of [['ordinary 1600x1000', { width: 1600, height: 1000 }, 0.5],
  ['short split 1600x1000', { width: 1600, height: 1000 }, 0.32], ['200% zoom 800x500', { width: 800, height: 500 }, 0.5]]) {
  test('restored wide queue keeps map tools and the selected preview reachable: ' + label, { timeout: 60000 }, async () => {
    const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
      channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
    try {
      // This layout cell needs a selectable preview. Refusal is covered separately below.
      const { page, errors } = await open(browser, viewport, { previewAvailable: true });
      await shortMapWithPreview(page, ratio);
      const map = page.locator('[data-panel-id="operations.map"]');
      const content = map.locator('.ruleset-panel-content');
      const scroll = await map.locator('.tactical-scroll').boundingBox(), box = await content.boundingBox();
      assert.ok(scroll.height <= Math.max(box.height, 121), 'the map viewport shrinks with its panel to its declared minimum');
      const deck = await map.locator('.map-command-deck').boundingBox();
      assert.ok(box.y + box.height <= deck.y + 1, 'the command deck never sits over map content');

      // Keyboard: Tab order reaches Map tools and the preview's queue control, each brought into view.
      for (const [selector, bounds] of [['.map-detail-controls > summary', '.ruleset-panel-content'],
        ['.map-commit-strip .ruleset-panel-action', '.map-command-deck']]) {
        await map.locator(selector).focus();
        assert.equal(await reachable(page, selector, bounds), true, selector + ' is visible when focused');
      }
      // Wheel through the actual nested regions: panel stack, map content and compact preview.
      // Reset the starting position only; no scrollIntoView substitutes for the user's wheel.
      const regions = [page.locator('#right-sidebar .sidebar-body'), content,
        map.locator('[data-tactical-preview]'), map.locator('.preview-only-note')];
      for (const region of regions.slice(0, -1)) await region.evaluate(node => { node.scrollTop = 0; });
      for (let index = 0; index < regions.length - 1; index += 1) {
        const owner = regions[index], child = regions[index + 1];
        const steps = await owner.evaluate(node => Math.ceil(node.scrollHeight / 120) + 1);
        for (let step = 0; step < steps; step += 1) {
          const area = await owner.boundingBox(), target = await child.boundingBox();
          const top = Math.max(0, area.y), bottom = Math.min(viewport.height, area.y + area.height);
          if (target.y >= top && target.y + Math.min(target.height, bottom - top) <= bottom) break;
          await page.mouse.move(Math.min(area.x + area.width - 3, viewport.width - 3), (top + bottom) / 2);
          await page.mouse.wheel(0, target.y < top ? -120 : 120);
          await page.evaluate(() => new Promise(requestAnimationFrame));
        }
      }
      assert.equal(await reachable(page, '.preview-only-note'), true, 'wheel scrolling reaches the preview text');
      const queue = await page.locator('.ruleset-queue-panel').boundingBox();
      assert.ok(queue.height < viewport.height * 0.75, 'the lower queue does not take the whole window');
      assert.deepEqual(errors, []);
      await page.close();
    } finally { await browser.close(); }
  });
}

test('an unavailable preview keeps its refusal while Queue remains unvalidated and never executes', async () => {
  const browser = await chromium.launch({ headless: true,
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    const { page, errors } = await open(browser, { width: 1600, height: 1000 });
    await shortMapWithPreview(page, .5);
    const card = page.locator('[data-panel-id="operations.map"] [data-tactical-preview]');
    await card.getByText('Synthetic preview refusal', { exact: true }).waitFor();
    assert.match(await card.innerText(), /queuing adds an unvalidated intent/);
    const before = await page.evaluate(() => JSON.stringify(window.layoutTest.state.panelProjection.surfaces
      .filter(surface => surface.id !== 'sylvia.action-queue')));
    await page.locator('[data-panel-id="operations.map"] .map-commit-strip .ruleset-panel-action').click();
    await page.waitForFunction(() => window.layoutCalls.compose.length === 1);
    const after = await page.evaluate(() => ({ revision: window.layoutTest.state.panelProjection.revision,
      projection: JSON.stringify(window.layoutTest.state.panelProjection.surfaces.filter(surface => surface.id !== 'sylvia.action-queue')),
      mutations: window.layoutCalls.execute }));
    assert.equal(after.projection, before); assert.equal(after.revision, fixture.revision);
    assert.equal(after.mutations, 0);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('map resize observers are replaced, not accumulated, across renders and leaving', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    const { page, errors } = await open(browser, { width: 1600, height: 1000 });
    // Track observers still observing, and every callback delivered for a target no longer in the page.
    const live = await page.evaluate(() => {
      const active = new Set(), Base = ResizeObserver;
      window.detachedCallbacks = 0;
      window.ResizeObserver = class extends Base {
        constructor(callback) {
          super((entries, observer) => {
            window.detachedCallbacks += entries.filter((entry) => !entry.target.isConnected).length;
            callback(entries, observer);
          });
        }
      };
      const { observe, disconnect } = Base.prototype;
      Base.prototype.observe = function (...args) { active.add(this); return observe.apply(this, args); };
      Base.prototype.disconnect = function () { active.delete(this); return disconnect.call(this); };
      window.liveObservers = active;
      const api = window.layoutTest;
      for (let index = 0; index < 25; index += 1)
        api.renderRulesetPanelProjection({ ...api.state.panelProjection, revision: api.state.panelProjection.revision });
      return active.size;
    });
    const maps = fixture.surfaces.filter((surface) => ['tactical_grid', 'coordinate_map'].includes(surface.renderer)).length;
    assert.ok(live >= 1 && live <= maps, live + ' live map observers after 25 renders');
    // The surviving observer is the current map's: a real resize reaches it and updates its overview.
    await page.locator('[data-map-offview]').click();
    const box = () => page.locator('[data-map-overview-view]').evaluate((node) => node.style.width);
    const before = await box();
    await page.setViewportSize({ width: 1180, height: 900 });
    await page.waitForFunction((value) => document.querySelector('[data-map-overview-view]').style.width !== value, before);
    await page.waitForTimeout(150);
    assert.equal(await page.evaluate(() => window.detachedCallbacks), 0, 'no callback runs for a replaced map');
    await page.evaluate(() => window.layoutTest.renderRulesetPanelProjection(null));
    assert.equal(await page.evaluate(() => window.liveObservers.size), 0, 'clearing the panels disconnects map observers');
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.waitForTimeout(150);
    assert.equal(await page.evaluate(() => window.detachedCallbacks), 0, 'no callback runs after the panels are cleared');
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

test('one thin rail expands in place to Builder sessions, with no second sidebar or context block', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const { page, errors } = await open(browser, { width: 1600, height: 1000 });
    await page.evaluate(() => {
      const { state, renderBuilderChatRail } = window.layoutTest;
      state.builderChats = [{ display_name: 'Harbour heist plan', session: {
        session_id: 'builder-one', role: 'builder', status: 'active', provider_id: 'test', model_id: 'test' } }];
      Object.assign(state.bridge, {
        builder_chat_index: async () => state.builderChats,
        session_context: async () => ({ messages: [] }),
        session_history_page: async () => ({ messages: [], latest_sequence: 0 }),
      });
      renderBuilderChatRail();
    });
    assert.equal(await page.locator('#session-context, #ctx-campaign, #ctx-writer').count(), 0);
    const sidebar = await box(page, '#app-sidebar');
    assert.equal(sidebar.width, 72, 'the rail starts thin');
    assert.equal((await box(page, '#app-rail')).width, sidebar.width, 'the rail is the whole sidebar');
    assert.equal(await page.locator('#left-sidebar').isVisible(), false);
    assert.equal((await box(page, '#play-area')).x, sidebar.x + sidebar.width, 'no second column beside the rail');

    await page.locator('#toggle-left-sidebar-btn').click();
    const wide = await box(page, '#app-sidebar'), rail = await box(page, '#app-rail');
    const drawer = await box(page, '#left-sidebar');
    assert.ok(wide.width > 200, 'expanded in place');
    assert.equal(drawer.x, wide.x, 'Builder sessions share the rail column');
    assert.equal(drawer.width, wide.width);
    assert.ok(drawer.y >= rail.y + rail.height - 1, 'Builder sessions sit beneath the navigation');
    assert.equal((await box(page, '#play-area')).x, wide.x + wide.width);
    assert.equal(await page.locator('#home-btn .rail-label').isVisible(), true);
    assert.equal(await page.locator('[data-builder-session-id="builder-one"]').isVisible(), true,
      'Builder sessions are reachable while a campaign is open');

    // The synthetic shell has no provider, so the composer is disabled; its text is still a saved draft.
    await page.evaluate(() => { document.getElementById('player-input').value = 'Unsent campaign plan'; });
    await page.locator('[data-builder-session-id="builder-one"]').click();
    await page.waitForFunction(() => window.layoutTest.state.activeSessionId === 'builder-one');
    assert.equal(await page.evaluate(() => window.layoutTest.state.activeSessionRole), 'builder');
    assert.equal(await page.evaluate(() => window.layoutTest.state.sessionDrafts.get('layout-gm')?.text),
      'Unsent campaign plan', 'the campaign draft is kept when switching to Builder');
    assert.equal(await page.locator('#toggle-left-sidebar-btn').getAttribute('aria-expanded'), 'true');
    assert.equal(await page.locator('#app-sidebar .portrait-col').count(), 1, 'still one sidebar');

    await page.locator('#toggle-left-sidebar-btn').click();
    assert.equal((await box(page, '#app-sidebar')).width, 72);
    assert.equal(await page.locator('#left-sidebar').isVisible(), false);
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

test('navigating from Play through the rail keeps the open OOC note in the saved draft', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const { page, errors } = await open(browser, { width: 1600, height: 1000 });
    await page.evaluate(() => {
      const { state, renderBuilderChatRail } = window.layoutTest;
      state.builderChats = [{ display_name: 'Harbour heist plan', session: {
        session_id: 'builder-one', role: 'builder', status: 'active', provider_id: 'test', model_id: 'test' } }];
      Object.assign(state.bridge, {
        builder_chat_index: async () => state.builderChats,
        session_context: async () => ({ messages: [] }),
        session_history_page: async () => ({ messages: [], latest_sequence: 0 }),
      });
      renderBuilderChatRail();
      // The synthetic shell has no provider, so the composer is disabled; its contents are still the draft.
      document.getElementById('player-input').value = 'Unsent campaign plan';
      document.getElementById('ooc-input').value = 'Private table note';
      document.getElementById('ooc-note').hidden = false; // admission is provider-gated in this synthetic shell
    });
    await page.locator('#ooc-note summary').click();
    await page.waitForFunction(() => window.layoutTest.state.sessionDrafts.get('layout-gm')?.oocOpen === true);
    await page.locator('#toggle-left-sidebar-btn').click();
    // Keyboard activation of a rail row is a click on that row, outside the OOC note.
    await page.locator('[data-builder-session-id="builder-one"]').focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => window.layoutTest.state.activeSessionId === 'builder-one');
    await page.waitForTimeout(200);
    assert.deepEqual(await page.evaluate(() => window.layoutTest.state.sessionDrafts.get('layout-gm')),
      { text: 'Unsent campaign plan', oocText: 'Private table note', oocOpen: true });
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

test('a click elsewhere in Play still closes the OOC note', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const { page, errors } = await open(browser, { width: 1600, height: 1000 });
    await page.evaluate(() => { document.getElementById('ooc-note').hidden = false; });
    await page.locator('#ooc-note summary').click();
    assert.equal(await page.locator('#ooc-note').evaluate((note) => note.open), true);
    await page.locator('#chronology').click();
    assert.equal(await page.locator('#ooc-note').evaluate((note) => note.open), false);
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

test('at the native minimum window the open OOC note stays inside its composer and Story', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const { page, errors } = await open(browser, { width: 960, height: 640 });
    await page.evaluate(() => { document.getElementById('ooc-note').hidden = false; });
    await page.locator('#ooc-note summary').click();
    const popover = await box(page, '.ooc-note-popover'), composer = await box(page, '.input-composer');
    const story = await box(page, '.chronology-col'), field = await box(page, '#ooc-input');
    assert.ok(composer.width < 360, `composer is narrower than the popover's preferred width (${composer.width})`);
    assert.ok(popover.x >= composer.x && popover.x + popover.width <= composer.x + composer.width + 0.5,
      `popover ${popover.x}+${popover.width} inside composer ${composer.x}+${composer.width}`);
    assert.ok(popover.x + popover.width <= story.x + story.width + 0.5, 'popover border inside Story');
    assert.ok(field.width >= 200, `the OOC field stays readable (${field.width}px)`);
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

test('choosing or refreshing a Builder session keeps keyboard focus on its row', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const { page, errors } = await open(browser, { width: 1600, height: 1000 });
    await page.evaluate(() => {
      const { state, renderBuilderChatRail } = window.layoutTest;
      state.builderChats = ['one', 'two'].map((id) => ({ display_name: `Builder ${id}`, session: {
        session_id: `builder-${id}`, role: 'builder', status: 'active', provider_id: 'test', model_id: 'test' } }));
      Object.assign(state.bridge, {
        builder_chat_index: async () => state.builderChats,
        session_context: async () => ({ messages: [] }),
        session_history_page: async () => ({ messages: [], latest_sequence: 0 }),
      });
      renderBuilderChatRail();
    });
    await page.locator('#toggle-left-sidebar-btn').click();
    const focused = () => page.evaluate(() => ({ id: document.activeElement?.dataset?.builderSessionId || null,
      tag: document.activeElement?.tagName, row: document.activeElement?.closest('.builder-chat-row')
        ?.querySelector('[data-builder-session-id]')?.dataset.builderSessionId || null }));
    // Enter on a row opens it; the list re-renders with the new active row.
    await page.locator('[data-builder-session-id="builder-two"]').focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => window.layoutTest.state.activeSessionId === 'builder-two');
    await page.waitForTimeout(200);
    assert.deepEqual(await focused(), { id: 'builder-two', tag: 'BUTTON', row: 'builder-two' });
    // A later list refresh (for example a renamed session) keeps focus on the same row's actions toggle.
    await page.keyboard.press('Tab');
    assert.deepEqual(await focused(), { id: null, tag: 'SUMMARY', row: 'builder-two' });
    await page.evaluate(() => {
      const { state, renderBuilderChatRail } = window.layoutTest;
      state.builderChats[1].display_name = 'Builder two renamed';
      renderBuilderChatRail();
    });
    assert.deepEqual(await focused(), { id: null, tag: 'SUMMARY', row: 'builder-two' });
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

test('the tactical grid is one Tab stop; arrows move focus without selecting and Enter still selects', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const { page, errors } = await open(browser, { width: 1600, height: 1000 });
    await page.evaluate(() => {
      const api = window.layoutTest;
      const surface = api.state.panelProjection.surfaces.find((item) => item.renderer === 'tactical_grid');
      api.updateTacticalSelection(surface, { actor: 'vesper' });
      api.renderRulesetPanelProjection(api.state.panelProjection);
    });
    const grid = () => page.evaluate(() => {
      const cells = [...document.querySelectorAll('.ruleset-map-panel [data-tactical-cell]')];
      const surface = window.layoutTest.state.panelProjection.surfaces.find((item) => item.renderer === 'tactical_grid');
      return { cells: cells.length, tabbable: cells.filter((cell) => cell.tabIndex >= 0).map((cell) => cell.dataset.tacticalCell),
        pressed: cells.filter((cell) => cell.getAttribute('aria-pressed') === 'true').map((cell) => cell.dataset.tacticalCell),
        focused: document.activeElement?.dataset?.tacticalCell || null,
        destination: window.layoutTest.state.tacticalSelections?.[surface.id]?.destination || null,
        queueVersion: window.layoutTest.state.actionSequence?.version };
    });
    const before = await grid();
    assert.equal(before.cells, 3600);
    assert.deepEqual(before.tabbable, ['2,2'], 'one Tab stop, at the acting unit');
    // Tab into the map from its scroll area: the next cell reached is the single entry cell.
    await page.locator('.ruleset-map-panel .tactical-scroll').first().focus();
    let presses = 0;
    while (!(await grid()).focused && presses < 15) { await page.keyboard.press('Tab'); presses += 1; }
    assert.equal((await grid()).focused, '2,2');
    assert.equal(await page.evaluate(() => getComputedStyle(document.activeElement).outlineOffset), '-2px',
      'the focus ring is drawn inside the cell so map edges cannot clip it');
    await page.keyboard.press('ArrowRight');
    for (let i = 0; i < 4; i += 1) await page.keyboard.press('ArrowUp');
    const moved = await grid();
    assert.equal(moved.focused, '3,0', 'arrows move focus and stop at the map edge');
    assert.deepEqual(moved.tabbable, ['3,0']);
    assert.deepEqual(moved.pressed, [], 'arrow keys never select a cell');
    assert.equal(moved.destination, null);
    assert.equal(moved.queueVersion, before.queueVersion);
    await page.keyboard.press('Enter');
    const chosen = await grid();
    assert.deepEqual(chosen.pressed, ['3,0'], 'Enter selects the focused cell');
    assert.deepEqual(chosen.destination, [3, 0]);
    assert.equal(chosen.focused, '3,0', 'focus survives the selection re-render');
    assert.deepEqual(chosen.tabbable, ['3,0']);
    await page.keyboard.press('Tab');
    assert.equal((await grid()).focused, null, 'Tab leaves the grid in one press');
    await page.keyboard.press('Shift+Tab');
    assert.equal((await grid()).focused, '3,0', 'Shift+Tab returns to the remembered cell');
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

test('the grid remembers its keyboard cell across a panel reset, per campaign and session', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const { page, errors } = await open(browser, { width: 1600, height: 1000 });
    const tabbable = () => page.evaluate(() => [...document.querySelectorAll('.ruleset-map-panel [data-tactical-cell]')]
      .filter((cell) => cell.tabIndex >= 0).map((cell) => cell.dataset.tacticalCell));
    await page.locator('.ruleset-map-panel [data-tactical-cell="0,0"]').first().focus();
    await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowDown');
    assert.deepEqual(await tabbable(), ['2,1']);
    // Opening Builder resets the panels (views and selections) before the campaign returns.
    const reset = (session) => page.evaluate((sessionId) => {
      const api = window.layoutTest;
      const projection = api.state.panelProjection;
      api.gamePanels().leave();
      api.state.activeSessionId = sessionId;
      api.renderRulesetPanelProjection(projection);
    }, session);
    await reset('layout-gm');
    assert.deepEqual(await tabbable(), ['2,1'], 'the same campaign and session return to the remembered cell');
    await reset('other-session');
    assert.notDeepEqual(await tabbable(), ['2,1'], 'another session does not inherit the cell');
    assert.equal((await tabbable()).length, 1);
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

test('arrow travel keeps the focused grid cell wholly inside the visible map', { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    // About 200% zoom of a 1600x1000 window: the map shows only a few cells at a time.
    const { page, errors } = await open(browser, { width: 800, height: 500 });
    await page.locator('.ruleset-map-panel [data-tactical-cell="0,0"]').first().focus();
    const contained = () => page.evaluate(() => {
      const cell = document.activeElement;
      const scroll = cell.closest('.tactical-scroll');
      const c = cell.getBoundingClientRect(), s = scroll.getBoundingClientRect();
      const left = s.left + scroll.clientLeft, top = s.top + scroll.clientTop;
      return { cell: cell.dataset.tacticalCell, scrollLeft: Math.round(scroll.scrollLeft),
        inside: c.left >= left - 0.5 && c.right <= left + scroll.clientWidth + 0.5 &&
          c.top >= top - 0.5 && c.bottom <= top + scroll.clientHeight + 0.5 };
    });
    for (let i = 0; i < 25; i += 1) await page.keyboard.press('ArrowRight');
    for (let i = 0; i < 12; i += 1) await page.keyboard.press('ArrowDown');
    const after = await contained();
    assert.equal(after.cell, '25,12');
    assert.ok(after.scrollLeft > 0, 'the map scrolled to follow focus');
    assert.equal(after.inside, true, `focused cell is clipped: ${JSON.stringify(after)}`);
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});
