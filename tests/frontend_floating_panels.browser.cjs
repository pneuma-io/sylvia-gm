// Browser WebUI floating panel windows: the owner's detach, cover, dock-restore and minimize journey.
// Shipped app shell, WebUI bridge and delivered TD map projection with a synthetic owner; real pointer input.
// Native detached windows are a separate host and are not exercised here.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/td-delivered-map-controls.json'), 'utf8'));
const MAP = 'operations.map', PARTY = 'operations.party';
const evidence = process.env.SYLVIA_FLOATING_QA_OUTPUT;

async function open(page) {
  await page.goto('https://sylvia-floating.test/app.html');
  await page.evaluate((projection) => {
    const api = window.floatingTest;
    document.getElementById('connecting-overlay').classList.add('hidden');
    document.getElementById('session-frame').style.display = 'flex';
    Object.assign(api.state, { view: 'session', activeCampaignId: projection.campaign_id, activeSessionId: 'floating-gm',
      activeSessionRole: 'dm', activeSnapshot: { revision: projection.revision },
      campaigns: [{ campaign_id: projection.campaign_id, display_name: 'Floating check', lifecycle: 'active',
        sessions: [{ session_id: 'floating-gm', role: 'dm', status: 'active' }] }],
      bridge: { snapshot: async () => ({ campaign_id: projection.campaign_id, revision: projection.revision }),
        campaign_panels: async () => projection,
        player_action_availability: async (command) => ({ campaign_id: command.campaign_id,
          revision: command.expected_revision, results: command.requests.map(() => ({ available: false, reason: 'Synthetic' })) }),
        execute_panel_action: async () => { throw new Error('Window management must not execute game actions'); } } });
    api.ensureSessionActivity({ sessionId: 'floating-gm', sessionRole: 'dm', campaignId: projection.campaign_id });
    document.getElementById('chronology').innerHTML = '<div class="story-card talk"><p>Synthetic narration.</p></div>';
    api.wireControls(); api.initSidebarResize(); api.showSessionView();
    api.renderRulesetPanelProjection(projection);
    api.renderSidebarState();
  }, fixture);
}

async function scene(browser) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://sylvia-floating.test') return route.abort();
    if (url.pathname.startsWith('/api/')) return route.fulfill({ json: { ok: true, result: [] } });
    const name = url.pathname.slice(1) || 'app.html', file = path.join(frontend, name);
    if (!file.startsWith(frontend + path.sep) || !fs.existsSync(file)) return route.abort();
    let body = fs.readFileSync(file);
    // Mirror the WebUI server's app.html injection (webui.py asset()).
    if (name === 'app.html') body = body.toString().replace('</head>', '<link rel="stylesheet" href="webui-mobile.css"></head>')
      .replace('<script src="panel-workspace.js">', '<script src="webui-bridge.js"></script><script src="webui-mobile.js"></script><script src="panel-workspace.js">');
    if (name === 'app.js') body = body.toString()
      .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.floatingTest = { state, wireControls, initSidebarResize,
        renderRulesetPanelProjection, renderSidebarState, showSessionView, gamePanels, ensureSessionActivity };
        // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css')
      ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await open(page);
  return { page, errors };
}

const win = (page, panelId) => page.locator('[data-floating-window]').filter({ has: page.locator(`[data-panel-id="${panelId}"]`) });
const dockItem = (page, title) => page.locator('[data-floating-dock-view]').filter({ hasText: title });
const shot = (page, name) => evidence ? page.screenshot({ path: path.join(evidence, `${name}.png`), timeout: 10000 }) : null;

// The element actually painted at a window's centre tells whether that window is visible on top.
async function topmostPanelAt(page, locator) {
  const rect = await locator.boundingBox();
  return page.evaluate(([x, y]) => {
    const element = document.elementFromPoint(x, y);
    return element?.closest('[data-floating-window]') ? element.closest('[data-panel-id]')?.dataset.panelId || 'window'
      : element?.closest('.floating-panel-dock') ? 'dock' : 'main';
  }, [rect.x + rect.width / 2, rect.y + rect.height / 2]);
}

async function drag(page, locator, x, y) {
  const bar = await locator.locator('.floating-panel-titlebar').boundingBox();
  await page.mouse.move(bar.x + 40, bar.y + bar.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, y, { steps: 12 });
  await page.mouse.up();
}

// Each panel is rendered once and owned by exactly one place in the saved arrangement.
async function assertSingleIdentity(page) {
  const counts = await page.evaluate(() => {
    const layout = window.floatingTest.gamePanels().snapshot();
    const ids = (tree) => !tree ? [] : tree.kind === 'tabs' ? tree.panels : [...ids(tree.first), ...ids(tree.second)];
    const owned = [...ids(layout.sidebar), ...layout.windows.flatMap((item) => ids(item.tree)), ...layout.hidden];
    return { owned, rendered: [...document.querySelectorAll('section.ruleset-panel[data-panel-id]')].map((node) => node.dataset.panelId) };
  });
  assert.equal(new Set(counts.owned).size, counts.owned.length, `duplicate ownership ${counts.owned}`);
  assert.equal(new Set(counts.rendered).size, counts.rendered.length, `duplicate rendering ${counts.rendered}`);
}

const launch = () => chromium.launch({ headless: true, args: ['--disable-gpu'],
  channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });

test('owner journey: detach and drag the map, cover it with the game view, restore and minimize from the dock', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await scene(browser);
    assert.equal(await page.locator('.floating-panel-dock').count(), 0, 'no dock before anything floats');

    // 1. Detach the map and drag its window into the middle of the screen.
    await page.locator(`[data-panel-float="${MAP}"]`).click();
    const map = win(page, MAP);
    await map.waitFor();
    assert.equal(await page.locator(`#ruleset-panels > .panel-workspace-tree [data-panel-id="${MAP}"]`).count(), 0,
      'the map left the panel column');
    await page.locator('[data-tactical-zoom="in"]').click();
    const zoom = await page.evaluate((id) => window.floatingTest.state.tacticalViews[id].zoom, MAP);
    assert.ok(zoom > 1, 'map zoom changed inside the floating window');
    const size = await map.boundingBox();
    await drag(page, map, 800 - size.width / 2 + 40, 520 - size.height / 2 + 17);
    const middle = await map.boundingBox();
    assert.ok(Math.abs(middle.x + middle.width / 2 - 800) < 4 && Math.abs(middle.y + middle.height / 2 - 520) < 4,
      `map centred, got ${JSON.stringify(middle)}`);
    assert.equal(await topmostPanelAt(page, map), MAP);
    await shot(page, '1-map-floating-middle');

    // 2. Click the game view: it comes forward and fully covers the map; the dock stays reachable.
    await page.locator('#chronology').click();
    assert.equal(await map.getAttribute('data-window-state'), 'covered');
    assert.equal(await map.isVisible(), false);
    assert.equal(await topmostPanelAt(page, map), 'main');
    const dock = await page.locator('.floating-panel-dock').boundingBox();
    const frame = await page.locator('#session-frame').evaluate((node) => {
      const rect = node.getBoundingClientRect(), style = getComputedStyle(node);
      return { bottom: rect.bottom - parseFloat(style.paddingBottom), height: rect.height };
    });
    assert.ok(Math.abs(frame.bottom - dock.y) < 1.5, `the game view fills down to the dock (${frame.bottom} vs ${dock.y})`);
    assert.ok(dock.y + dock.height <= 1000.5 && dock.height >= 30);
    assert.equal(await topmostPanelAt(page, dockItem(page, 'Tactical map')), 'dock', 'the dock item is not covered');
    assert.equal(await dockItem(page, 'Tactical map').getAttribute('data-window-state'), 'covered');
    assert.equal(await dockItem(page, 'Game view').getAttribute('aria-pressed'), 'true');
    await shot(page, '2-game-view-covers-map');

    // 3. Choose the map in the dock: the same window returns to the front where it was.
    const identity = await map.getAttribute('data-floating-window');
    await dockItem(page, 'Tactical map').click();
    assert.equal(await map.getAttribute('data-window-state'), 'front');
    assert.equal(await map.getAttribute('data-floating-window'), identity);
    assert.deepEqual(await map.boundingBox(), middle);
    assert.equal(await topmostPanelAt(page, map), MAP);
    await shot(page, '3-map-restored-from-dock');

    // 4. Minimize from the title bar and restore from the dock; then the taskbar toggle minimizes the front window.
    await map.locator('[data-floating-minimize]').click();
    assert.equal(await win(page, MAP).count(), 0);
    assert.equal(await dockItem(page, 'Tactical map').getAttribute('data-window-state'), 'minimized');
    await shot(page, '4-map-minimized');
    await dockItem(page, 'Tactical map').click();
    assert.equal(await map.getAttribute('data-floating-window'), identity);
    assert.deepEqual(await map.boundingBox(), middle);
    assert.equal(await page.evaluate((id) => window.floatingTest.state.tacticalViews[id].zoom, MAP), zoom,
      'map view state survives minimize and restore');
    await dockItem(page, 'Tactical map').click();
    assert.equal(await dockItem(page, 'Tactical map').getAttribute('data-window-state'), 'minimized', 'clicking the front window in the dock minimizes it');
    await dockItem(page, 'Tactical map').click();
    assert.equal(await map.getAttribute('data-window-state'), 'front');
    await assertSingleIdentity(page);

    // 5. Overlapping windows keep coherent focus and stacking. The map covers Squad's float control,
    // so bring the game view forward from the dock first.
    await dockItem(page, 'Game view').click();
    assert.equal(await map.getAttribute('data-window-state'), 'covered');
    await page.locator(`[data-panel-float="${PARTY}"]`).click();
    const party = win(page, PARTY);
    await party.waitFor();
    assert.equal(await map.getAttribute('data-window-state'), 'covered');
    assert.equal(await party.getAttribute('data-window-state'), 'front');
    await drag(page, party, middle.x + 120, middle.y + 80);
    const overlap = { x: middle.x + middle.width - 30, y: middle.y + middle.height - 30 };
    const partyBox = await party.boundingBox();
    assert.ok(overlap.x > partyBox.x && overlap.y > partyBox.y, 'the windows overlap');
    const panelAt = ({ x, y }) => document.elementFromPoint(x, y).closest('[data-floating-window]')
      ?.querySelector('section.ruleset-panel[data-panel-id]')?.dataset.panelId;
    assert.equal(await page.evaluate(panelAt, overlap), PARTY);
    await dockItem(page, 'Tactical map').click();
    assert.equal(await map.getAttribute('data-window-state'), 'front');
    assert.equal(await party.getAttribute('data-window-state'), 'open');
    assert.equal(await page.evaluate(panelAt, overlap), MAP);
    await shot(page, '5-overlap-map-front');
    // Press the part of Party still showing beside the map, as a user would.
    const exposed = { x: middle.x + middle.width + 30, y: partyBox.y + partyBox.height / 2 };
    assert.ok(exposed.x < partyBox.x + partyBox.width - 5);
    assert.equal(await page.evaluate(panelAt, exposed), PARTY);
    await page.mouse.click(exposed.x, exposed.y);
    assert.equal(await party.getAttribute('data-window-state'), 'front', 'pressing a window raises it');
    assert.equal(await map.getAttribute('data-window-state'), 'open');
    assert.equal(await page.evaluate(panelAt, overlap), PARTY);
    await page.locator('#chronology').click();
    assert.equal(await map.getAttribute('data-window-state'), 'covered');
    assert.equal(await party.getAttribute('data-window-state'), 'covered');
    await dockItem(page, 'Tactical map').click();
    assert.equal(await map.getAttribute('data-window-state'), 'front');
    assert.equal(await party.getAttribute('data-window-state'), 'covered', 'only the chosen window comes forward');
    await shot(page, '6-dock-restores-only-map');
    await assertSingleIdentity(page);

    // Hiding the panel column keeps floating windows and the dock.
    await page.locator('#toggle-right-sidebar-btn').click();
    assert.equal(await page.locator('#session-frame.right-sidebar-collapsed').count(), 1);
    assert.equal(await page.locator('.floating-panel-dock').isVisible(), true);
    await dockItem(page, 'Tactical map').click();
    assert.equal(await map.isVisible(), true);
    assert.equal(await topmostPanelAt(page, map), MAP);
    await shot(page, '7-panel-column-hidden-map-still-floating');
    await page.locator('#toggle-right-sidebar-btn').click();

    // Saved geometry, minimized state and identity survive a reload.
    await dockItem(page, 'Squad').click();
    await party.locator('[data-floating-minimize]').click();
    await page.reload();
    await open(page);
    assert.equal(await map.getAttribute('data-floating-window'), identity);
    assert.deepEqual(await map.boundingBox(), middle);
    assert.equal(await dockItem(page, 'Squad').getAttribute('data-window-state'), 'minimized');
    await assertSingleIdentity(page);

    // Returning both panels to the column removes the dock and gives the game view its full height.
    await map.locator('[data-floating-return]').click();
    await dockItem(page, 'Squad').click();
    await party.locator('[data-floating-return]').click();
    assert.equal(await page.locator('[data-floating-window], .floating-panel-dock').count(), 0);
    assert.equal(await page.locator(`#ruleset-panels > .panel-workspace-tree [data-panel-id="${MAP}"]`).count(), 1);
    assert.equal(await page.locator('#session-frame').evaluate((node) => getComputedStyle(node).paddingBottom), '0px');
    await assertSingleIdentity(page);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('narrow WebUI screens show saved floating windows inline and offer no float control', { timeout: 30000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await scene(browser);
    await page.locator(`[data-panel-float="${MAP}"]`).click();
    const map = win(page, MAP);
    await map.waitFor();
    const identity = await map.getAttribute('data-floating-window');
    const saved = await savedFrames(page);
    // Resizing alone (no game update) switches layouts.
    await page.setViewportSize({ width: 600, height: 900 });
    await page.locator('[data-floating-inline]').waitFor({ state: 'attached' });
    assert.equal(await page.locator('[data-floating-window], .floating-panel-dock, [data-panel-float]').count(), 0);
    assert.equal(await page.locator(`[data-floating-inline] [data-panel-id="${MAP}"]`).count(), 1);
    await assertSingleIdentity(page);
    // Narrow screens show one pane at a time; the inline map is in the Game panels pane.
    await page.getByRole('button', { name: 'Game panels', exact: true }).click();
    assert.equal(await page.locator(`[data-floating-inline] [data-panel-id="${MAP}"]`).isVisible(), true);
    if (evidence) await shot(page, '10-narrow-600-inline-map');
    await page.setViewportSize({ width: 1600, height: 1000 });
    await map.waitFor();
    assert.equal(await map.getAttribute('data-floating-window'), identity);
    assert.deepEqual(await savedFrames(page), saved, 'the narrow round trip saves nothing');
    assert.equal(await page.locator('[data-floating-inline]').count(), 0);
    await assertSingleIdentity(page);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

const savedFrames = (page) => page.evaluate(() =>
  window.floatingTest.gamePanels().snapshot().windows.map((item) => ({ id: item.id, frame: item.frame })));
const activeControl = (page) => page.evaluate(() => {
  const active = document.activeElement;
  return { dock: active?.dataset?.floatingDockView || null, tab: active?.dataset?.workspacePanel || null,
    inWindow: active?.closest?.('[data-floating-window]')?.dataset.floatingWindow || null };
});

// Exactly one view is in front, minimized windows are absent, and the dock agrees with the windows.
async function assertCoherent(page) {
  const view = await page.evaluate(() => ({
    windows: [...document.querySelectorAll('[data-floating-window]')].map((node) =>
      ({ id: node.dataset.floatingWindow, state: node.dataset.windowState, z: Number(node.style.zIndex),
        painted: [node, ...node.querySelectorAll('*')].filter((child) => getComputedStyle(child).visibility === 'visible').length })),
    dock: [...document.querySelectorAll('[data-floating-dock-view]')].map((node) =>
      ({ id: node.dataset.floatingDockView, state: node.dataset.windowState, pressed: node.getAttribute('aria-pressed') })),
    saved: window.floatingTest.gamePanels().snapshot().windows.map((item) => ({ id: item.id, minimized: item.frame.minimized })),
  }));
  const fronts = view.dock.filter((item) => item.state === 'front');
  assert.equal(fronts.length, 1, `one front view ${JSON.stringify(view.dock)}`);
  assert.deepEqual(view.dock.filter((item) => item.pressed === 'true').map((item) => item.id), [fronts[0].id]);
  for (const saved of view.saved) {
    const rendered = view.windows.find((item) => item.id === saved.id);
    const docked = view.dock.find((item) => item.id === saved.id);
    assert.equal(Boolean(rendered), !saved.minimized, `${saved.id} rendered only when not minimized`);
    assert.equal(docked.state === 'minimized', saved.minimized);
    if (rendered) assert.equal(rendered.state, docked.state);
    if (rendered?.state === 'covered') assert.equal(rendered.painted, 0, `nothing in covered ${saved.id} is painted over the game view`);
  }
  if (fronts[0].id !== 'main') {
    const top = view.windows.find((item) => item.id === fronts[0].id);
    assert.ok(view.windows.every((item) => item.id === top.id || item.z < top.z), 'the front window is topmost');
  }
  await assertSingleIdentity(page);
}

test('keyboard window controls keep focus and Tab brings a covered-by-window panel forward', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await scene(browser);
    await page.locator(`[data-panel-float="${MAP}"]`).focus();
    await page.keyboard.press('Enter');
    const map = win(page, MAP);
    await map.waitFor();
    assert.equal((await activeControl(page)).tab, MAP, 'floating moves focus to the panel in its new window');
    const mapId = await map.getAttribute('data-floating-window');

    await map.locator('[data-floating-minimize]').focus();
    await page.keyboard.press('Enter');
    assert.equal((await activeControl(page)).dock, mapId, 'minimizing moves focus to the window\'s dock item');
    await page.keyboard.press('Enter');
    assert.equal(await map.getAttribute('data-window-state'), 'front');
    assert.equal((await activeControl(page)).dock, mapId, 'restoring keeps focus on the dock item');
    await page.keyboard.press('Enter');
    assert.equal(await dockItem(page, 'Tactical map').getAttribute('data-window-state'), 'minimized', 'Enter on the front window minimizes it');
    assert.equal((await activeControl(page)).dock, mapId);
    await page.keyboard.press('Enter');
    await assertCoherent(page);

    // A second window over the map; keyboard focus entering the map raises it.
    await dockItem(page, 'Game view').click();
    await page.locator(`[data-panel-float="${PARTY}"]`).click();
    const party = win(page, PARTY);
    await party.waitFor();
    await dockItem(page, 'Tactical map').click();
    await dockItem(page, 'Squad').click();
    assert.equal(await map.getAttribute('data-window-state'), 'open');
    await map.locator(`[data-workspace-panel="${MAP}"]`).focus();
    assert.equal(await map.getAttribute('data-window-state'), 'front', 'focus raises the window holding it');
    assert.equal(await party.getAttribute('data-window-state'), 'open');
    await assertCoherent(page);

    await map.locator('[data-floating-return]').focus();
    await page.keyboard.press('Enter');
    assert.equal(await win(page, MAP).count(), 0);
    const returned = await activeControl(page);
    assert.equal(returned.tab, MAP, 'returning focuses the panel\'s tab in the column');
    assert.equal(returned.inWindow, null);
    await assertCoherent(page);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('viewport changes keep title bars reachable without saving clamped geometry', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await scene(browser);
    await page.locator(`[data-panel-float="${MAP}"]`).click();
    const map = win(page, MAP);
    await map.waitFor();
    await drag(page, map, 1450, 900);
    const saved = await savedFrames(page);
    for (const viewport of [{ width: 1100, height: 700 }, { width: 800, height: 500 }]) {
      await page.setViewportSize(viewport);
      await page.waitForFunction(({ width, height }) => {
        const bar = document.querySelector('[data-floating-window] .floating-panel-titlebar')?.getBoundingClientRect();
        return bar && bar.left < width - 40 && bar.right > 40 && bar.top >= 0 && bar.bottom <= height;
      }, viewport);
      const bar = await map.locator('.floating-panel-titlebar').boundingBox();
      const dock = await page.locator('.floating-panel-dock').boundingBox();
      assert.ok(bar.y + bar.height <= dock.y + 0.5, `title bar above the dock at ${viewport.width}x${viewport.height}`);
      const windowBox = await map.boundingBox();
      assert.ok(windowBox.width <= viewport.width && windowBox.height <= dock.y, 'the window fits the visible area');
      assert.deepEqual(await savedFrames(page), saved, 'clamping to the viewport saves nothing');
      assert.equal(await dockItem(page, 'Tactical map').isVisible(), true);
      if (evidence) await shot(page, `8-viewport-${viewport.width}x${viewport.height}`);
    }
    // Moving in the small viewport saves the new position but keeps the saved size.
    const bar = await map.locator('.floating-panel-titlebar').boundingBox();
    await drag(page, map, bar.x + 20, bar.y - 40 + bar.height / 2);
    const moved = await savedFrames(page);
    assert.notEqual(moved[0].frame.top, saved[0].frame.top);
    assert.equal(moved[0].frame.width, saved[0].frame.width);
    assert.equal(moved[0].frame.height, saved[0].frame.height);
    await page.setViewportSize({ width: 1600, height: 1000 });
    await page.waitForFunction((height) => Math.round(document.querySelector('[data-floating-window]').getBoundingClientRect().height) === height,
      saved[0].frame.height);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('repeated minimize and restore of overlapping windows stays coherent through hiding the column and reload', { timeout: 90000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await scene(browser);
    await page.locator(`[data-panel-float="${MAP}"]`).click();
    const map = win(page, MAP);
    await map.waitFor();
    await dockItem(page, 'Game view').click();
    await page.locator(`[data-panel-float="${PARTY}"]`).click();
    const party = win(page, PARTY);
    await party.waitFor();
    await drag(page, party, 900, 300);
    const geometry = await savedFrames(page);
    const strip = (frames) => frames.map(({ id, frame }) => ({ id, left: frame.left, top: frame.top, width: frame.width, height: frame.height }));
    // Each round starts and ends with Squad in front and the map covered by the game view.
    const steps = [
      ['dock map: covered map comes forward', () => dockItem(page, 'Tactical map').click()],
      ['title-bar minimize map', () => map.locator('[data-floating-minimize]').click()],
      ['dock Squad: front Squad minimizes', () => dockItem(page, 'Squad').click()],
      ['dock map: minimized map restores', () => dockItem(page, 'Tactical map').click()],
      ['dock map: front map minimizes', () => dockItem(page, 'Tactical map').click()],
      ['dock Squad: restores', () => dockItem(page, 'Squad').click()],
      ['dock map: restores over Squad', () => dockItem(page, 'Tactical map').click()],
      ['game view click covers both', () => page.locator('#session-title').click()],
      ['dock Squad: comes forward alone', () => dockItem(page, 'Squad').click()],
    ];
    assert.equal(await map.getAttribute('data-window-state'), 'covered');
    assert.equal(await party.getAttribute('data-window-state'), 'front');
    for (let round = 0; round < 2; round += 1) {
      for (const [label, step] of steps) {
        await step();
        await assertCoherent(page).catch((error) => { throw new Error(`round ${round + 1}, ${label}: ${error.message}`); });
        assert.deepEqual(strip(await savedFrames(page)), strip(geometry), `${label}: minimize and restore never move or resize`);
      }
      assert.equal(await map.getAttribute('data-window-state'), 'covered');
      assert.equal(await party.getAttribute('data-window-state'), 'front');
      // The second round runs with the panel column hidden.
      if (round === 0) {
        await page.locator('#toggle-right-sidebar-btn').click();
        await dockItem(page, 'Squad').click();
        assert.equal(await page.locator('#session-frame.right-sidebar-collapsed').count(), 1);
      }
    }
    if (evidence) await shot(page, '9-after-repeated-cycles-column-hidden');
    await page.locator('#toggle-right-sidebar-btn').click();
    await dockItem(page, 'Squad').click();
    await party.locator('[data-floating-minimize]').click();
    const beforeReload = await savedFrames(page);
    await page.reload();
    await open(page);
    assert.deepEqual(await savedFrames(page), beforeReload);
    assert.equal(await dockItem(page, 'Squad').getAttribute('data-window-state'), 'minimized');
    assert.equal(await map.getAttribute('data-window-state'), 'front');
    await assertCoherent(page);
    await dockItem(page, 'Squad').click();
    await assertCoherent(page);
    assert.equal(await party.getAttribute('data-window-state'), 'front');
    assert.deepEqual(strip(await savedFrames(page)), strip(geometry));
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
