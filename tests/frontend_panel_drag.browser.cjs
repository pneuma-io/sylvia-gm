// Run explicitly with Playwright available: node --test tests/frontend_panel_drag.browser.cjs.
// Browser input proves event delivery; the native observer/window boundary remains simulated here.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

// Use the shipped host, reducer and CSS; only native IPC and synthetic panel contents are replaced.
async function createScene(browser, width = 350) {
    const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
    const errors = [], events = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {
      if (message.text().startsWith('panel-input:')) events.push(message.text().slice(12));
    });
    await page.route('https://sylvia-drag.test/**', async route => {
      const name = new URL(route.request().url()).pathname.slice(1);
      if (['app.css', 'panel-workspace.js', 'shot-playback.js', 'panel-host.js'].includes(name)) {
        return route.fulfill({ contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript',
          body: fs.readFileSync(path.join(frontend, name), 'utf8') });
      }
      return route.fulfill({ contentType: 'text/html', body:
        '<meta charset="utf-8"><link rel="stylesheet" href="/app.css">' +
        `<div class="right-sidebar" style="margin-left:700px;width:${width}px">` +
        '<div id="ruleset-panels"></div></div><script src="/panel-workspace.js"></script>' +
        '<script src="/shot-playback.js"></script><script src="/panel-host.js"></script>' });
    });
    await page.goto('https://sylvia-drag.test/');
    await page.evaluate(async () => {
      window.dragTest = { calls: [], statuses: [], released: false };
      for (const type of ['dragstart', 'dragend']) document.addEventListener(type,
        () => console.log(`panel-input:${type}`), true);
      document.addEventListener('drag', () => { window.dragTest.moved = true; }, true);
      window.__TAURI__ = { event: { listen: async () => () => {} }, core: {
        invoke: async (command, args) => {
          window.dragTest.calls.push(command);
          if (command === 'sync_panel_windows') {
            window.dragTest.context = args.context;
            return { failed_window_ids: [] };
          }
          if (command === 'finish_panel_drag') return args.cancelled || !window.dragTest.released
            ? null : { x: 1300, y: 200, width: 500, height: 500 };
        },
      } };
      const state = { activeCampaignId: 'test', activeSessionId: 'gm', activeSessionRole: 'dm',
        selectedTheme: 'slate', tacticalViews: {}, tacticalSelections: {}, networkSelections: {},
        panelProjection: { campaign_id: 'test', ruleset_id: 'test-rules', revision: 1,
          surfaces: [{ id: 'party', title: 'Party', renderer: 'party' },
            { id: 'inventory', title: 'Inventory', renderer: 'inventory' }] } };
      const container = document.getElementById('ruleset-panels');
      let host;
      const render = () => { container.innerHTML = host.markup(state.panelProjection); host.wire(container); };
      host = window.SylviaPanelHost.create({ state, render,
        renderPanel: () => '<section class="ruleset-panel"><div style="height:200px">Synthetic party</div></section>',
        dispatch: async () => { throw new Error('Dragging must not dispatch a game action'); },
        selection: () => {}, busy: () => false,
        status: message => window.dragTest.statuses.push(String(message)),
      });
      window.dragTest.host = host;
      await host.init(); render();
    });
    return { page, errors, events };
}

const launch = () => chromium.launch({ headless: true,
  channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });

test('showing panel drop targets does not cancel a real browser drag before release', { timeout: 20000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors, events } = await createScene(browser);
    const tab = page.locator('[data-workspace-panel="party"]');
    const before = await tab.boundingBox();
    assert.equal(await page.locator('.panel-workspace-drop-destinations').isVisible(), false);
    await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2);
    await page.mouse.down();
    // A cancelled native drag can leave the input command pending; inspect the event verdict promptly.
    const move = page.mouse.move(before.x - 100, before.y + 150, { steps: 15 });
    await Promise.race([move, new Promise(resolve => setTimeout(resolve, 500))]);
    assert.deepEqual(events, ['dragstart'], 'drag must remain active while the mouse is held');
    const during = await tab.boundingBox();
    assert.deepEqual(during, before, 'revealing destinations must not shift the grabbed tab');
    await page.waitForFunction(() => window.dragTest.moved, undefined, { timeout: 2000 });
    assert.equal(await page.locator('.panel-workspace-drop-destinations').isVisible(), true);
    const started = await page.evaluate(() => window.dragTest.calls);
    assert.equal(started.filter(command => command === 'begin_panel_drag').length, 1);
    await page.evaluate(() => { window.dragTest.released = true; });
    // Release with no HTML destination; the fake observer supplies outside-window placement.
    await page.mouse.up();
    await page.waitForFunction(() => window.dragTest.host.snapshot().windows.length === 1);
    assert.deepEqual(events, ['dragstart', 'dragend']);
    assert.deepEqual(await page.evaluate(() => window.dragTest.statuses), []);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('panel placement preview matches vertical stacking, side splits and tab grouping before release', { timeout: 30000 }, async () => {
  const browser = await launch();
  try {
    for (const [position, x, y, axis, incomingFirst] of [
      ['above', 0.5, 0.06, 'column', true],
      ['above', 0.12, 0.04, 'column', true],
      ['below', 0.5, 0.94, 'column', false],
      ['left', 0.06, 0.5, 'row', true],
      ['right', 0.94, 0.5, 'row', false],
      ['tab', 0.5, 0.5, null, false],
    ]) {
      const { page, errors } = await createScene(browser, 220);
      const start = await page.locator('[data-workspace-panel="party"]').boundingBox();
      const target = await page.locator('[data-panel-drop="inventory"]').boundingBox();
      const before = await page.evaluate(() => window.dragTest.host.snapshot());
      await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
      await page.mouse.down();
      await page.mouse.move(target.x + target.width * x, target.y + target.height * y, { steps: 15 });
      const preview = page.locator('.panel-drop-preview');
      assert.equal(await preview.isVisible(), true, `${position} has a highlighted destination before release`);
      assert.equal(await preview.getAttribute('data-position'), position);
      const highlighted = await preview.boundingBox();
      assert.ok(highlighted.width > 0 && highlighted.height > 0);
      assert.ok(highlighted.x >= target.x && highlighted.y >= target.y);
      assert.ok(highlighted.x + highlighted.width <= target.x + target.width + 1);
      assert.ok(highlighted.y + highlighted.height <= target.y + target.height + 1);
      assert.deepEqual(await page.evaluate(() => window.dragTest.host.snapshot()), before,
        'hover must not mutate the saved arrangement');
      assert.deepEqual(await page.locator('[data-workspace-panel="party"]').boundingBox(), start,
        'preview must not displace the source');
      if (position === 'above' && x === 0.5 && process.env.SYLVIA_PANEL_PREVIEW_CAPTURE)
        await page.screenshot({ path: process.env.SYLVIA_PANEL_PREVIEW_CAPTURE,
          clip: { x: 690, y: 0, width: 250, height: 640 } });
      await page.mouse.up();
      const after = await page.evaluate(() => window.dragTest.host.snapshot());
      assert.equal(await preview.count(), 0, 'release clears the transient preview');
      assert.equal(after.windows.length, 0);
      if (axis) {
        assert.equal(after.sidebar.axis, axis);
        assert.deepEqual(after.sidebar.first.panels, [incomingFirst ? 'party' : 'inventory']);
        assert.deepEqual(after.sidebar.second.panels, [incomingFirst ? 'inventory' : 'party']);
      } else assert.deepEqual(after.sidebar.panels, ['inventory', 'party']);
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
});

test('hover changes and Escape clear the preview without moving panels', { timeout: 20000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await createScene(browser);
    const start = await page.locator('[data-workspace-panel="party"]').boundingBox();
    const target = await page.locator('[data-panel-drop="inventory"]').boundingBox();
    const before = await page.evaluate(() => window.dragTest.host.snapshot());
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
    await page.mouse.down();
    await page.mouse.move(target.x + target.width / 2, target.y + 5, { steps: 12 });
    assert.equal(await page.locator('.panel-drop-preview').getAttribute('data-position'), 'above');
    await page.mouse.move(target.x + target.width / 2, target.y + target.height - 5, { steps: 6 });
    assert.equal(await page.locator('.panel-drop-preview').getAttribute('data-position'), 'below');
    await page.mouse.move(target.x - 100, target.y + target.height / 2, { steps: 6 });
    assert.equal(await page.locator('.panel-drop-preview').count(), 0, 'leaving the target clears feedback');
    await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 6 });
    assert.equal(await page.locator('.panel-drop-preview').getAttribute('data-position'), 'tab');
    await page.keyboard.press('Escape');
    await page.mouse.up();
    assert.equal(await page.locator('.panel-drop-preview').count(), 0);
    assert.deepEqual(await page.evaluate(() => window.dragTest.host.snapshot()), before);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('return from a separate window previews and commits one vertical stack, rejecting stale campaign drops', { timeout: 20000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await createScene(browser, 220);
    await page.evaluate(() => window.dragTest.host.changed({ type: 'move', panelId: 'party', destination: { windowId: 'new' } }));
    assert.equal((await page.evaluate(() => window.dragTest.host.snapshot())).windows.length, 1);
    // Cross-native-window delivery is unavailable here. Exercise a transferred payload through
    // the actual receiver DOM; the same-context and single-owner checks must still apply.
    const send = async (type, stale = false) => page.evaluate(({ type, stale }) => {
      const target = document.querySelector('[data-panel-drop="inventory"]');
      const rect = target.getBoundingClientRect();
      const dataTransfer = new DataTransfer();
      dataTransfer.setData('application/x-sylvia-panel', JSON.stringify({
        context: { ...window.dragTest.context, ...(stale ? { generation: 'old' } : {}) }, panelId: 'party',
      }));
      target.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer,
        clientX: rect.left + rect.width * 0.12, clientY: rect.top + rect.height * 0.04 }));
    }, { type, stale });
    await send('dragover');
    assert.equal(await page.locator('.panel-drop-preview').getAttribute('data-position'), 'above');
    const before = await page.evaluate(() => window.dragTest.host.snapshot());
    await send('drop', true);
    assert.deepEqual(await page.evaluate(() => window.dragTest.host.snapshot()), before);
    assert.equal(await page.locator('.panel-drop-preview').count(), 0);
    await send('dragover');
    await send('drop');
    const after = await page.evaluate(() => window.dragTest.host.snapshot());
    assert.equal(after.windows.length, 0);
    assert.equal(after.sidebar.axis, 'column');
    assert.deepEqual(after.sidebar.first.panels, ['party']);
    assert.deepEqual(after.sidebar.second.panels, ['inventory']);
    assert.equal(await page.locator('.panel-drop-preview').count(), 0);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
