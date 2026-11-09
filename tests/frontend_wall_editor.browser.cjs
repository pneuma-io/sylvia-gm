const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

// Real layout and pointer geometry: grid-edge walls are drafted on actual cell borders and
// submitted as the declared panel selection, never as typed identities.
const wall = (id, min, max) => ({ id, map_id: 'floor', edge_wall: 'wall-a', active: true,
  movement: 'blocking', occupies_cells: false, visibility: 'opaque', destroyed: {},
  volume: { kind: 'grid_edge', min: [...min, 0], max: [...max, 3] },
  display: { label: 'Full wall', kind: 'wall', height_label: 'Full' } });
const bindings = { map_id: 'map_id', expected_revision: 'map_revision' };
const piece = { height: 3, display: { label: 'Full wall', kind: 'wall', height_label: 'Full' } };
const map = { id: 'map', title: 'Tactical map', renderer: 'tactical_grid', minimum_size: 320, default_size: 620,
  data: { map_revision: 1, map: { id: 'floor', bounds: { min: [0, 0, 0], max: [16, 12, 3] },
    grid: { cell_size: 1, unit: 'm', origin: [0, 0] } }, selection: {}, terrain: [], zones: [], objectives: [],
  geometry: [wall('wall-a', [6, 2], [6, 3]), wall('wall-b', [6, 3], [6, 4])], entities: [] },
  actions: [
    { id: 'wall-draw', label: 'New wall', available: true, bindings: { ...bindings, segments: 'edges' }, edge_segment: piece },
    { id: 'wall-extend', label: 'Extend wall', available: true,
      bindings: { ...bindings, geometry_ids: 'edge_anchor', segments: 'edges' }, edge_segment: piece },
    { id: 'wall-connect', label: 'Connect walls', available: true, bindings: { ...bindings, geometry_ids: 'edge_walls' } },
  ] };

async function openMap(width, height) {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'], channel: process.platform === 'win32' ? 'msedge' : undefined });
  const page = await browser.newPage({ viewport: { width, height } });
  await page.route('**/*', async route => {
    const name = new URL(route.request().url()).pathname.slice(1);
    if (!['app.html', 'app.js', 'app.css', 'panel-workspace.js', 'panel-host.js', 'shot-playback.js',
      'campaign-plan-browser.js', 'assets/sylvia-portrait.png'].includes(name)) return route.abort();
    let body = fs.readFileSync(path.join(frontend, name));
    if (name === 'app.js') body = body.toString()
      .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', 'window.wallTest = { state, renderRulesetPanelProjection, ensureSessionActivity }; // ---- Public API ----');
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
      name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-walls.test/app.html');
  await page.evaluate((surface) => {
    document.getElementById('connecting-overlay')?.classList.add('hidden');
    document.getElementById('session-frame').style.display = 'flex';
    const projection = { campaign_id: 'walls', ruleset_id: 'walls', revision: 1, shot_effects: [], surfaces: [surface] };
    window.previews = [];
    Object.assign(window.wallTest.state, { activeCampaignId: 'walls', activeSessionId: 'walls-session',
      activeSessionRole: 'dm', activeSnapshot: { revision: 1 }, view: 'session' });
    window.wallTest.ensureSessionActivity();
    window.wallTest.state.bridge = {
      snapshot: async () => ({ revision: 1 }),
      campaign_panels: async () => projection,
      preview_panel_action: async (command) => { window.previews.push(command);
        throw new Error('spatial_wall_selection_required: The owner refused this test edit.'); },
      execute_panel_action: async () => { throw new Error('not reached'); },
    };
    window.wallTest.renderRulesetPanelProjection(projection);
  }, map);
  return { browser, page, panel: page.locator('.ruleset-panel[data-panel-id="map"]') };
}

// Page coordinates of a point given in grid units, scrolled into view.
function gridPoint(panel, x, y) {
  return panel.evaluate((node, [x, y]) => {
    const world = node.querySelector('.tactical-world');
    const rect = world.getBoundingClientRect();
    return [rect.left + x * rect.width / 16, rect.top + y * rect.height / 12];
  }, [x, y]);
}

async function drag(page, panel, points) {
  // The current layout scrolls a large map inside its panel. Bring the intended
  // border run into view before computing pointer coordinates for the gesture.
  await panel.evaluate((node, points) => {
    const world = node.querySelector('.tactical-world').getBoundingClientRect();
    const scroll = node.querySelector('.tactical-scroll');
    const xs = points.map(point => point[0]), ys = points.map(point => point[1]);
    scroll.scrollTo((Math.min(...xs) + Math.max(...xs)) / 2 * world.width / 16 - scroll.clientWidth / 2,
      (Math.min(...ys) + Math.max(...ys)) / 2 * world.height / 12 - scroll.clientHeight / 2);
  }, points);
  const [start, ...rest] = await Promise.all(points.map(([x, y]) => gridPoint(panel, x, y)));
  await page.mouse.move(...start);
  await page.mouse.down();
  for (const point of rest) await page.mouse.move(...point, { steps: 10 });
  await page.mouse.up();
}

test('wall editor drafts real borders and submits only declared selections', { timeout: 60000 }, async () => {
  const { browser, page, panel } = await openMap(1400, 932);
  try {
    assert.equal(await panel.locator('.map-command-deck button', { hasText: /wall/i }).count(), 0,
      'Wall edits are not bare command-deck buttons');
    await panel.locator('.tactical-toolbar [data-tactical-wall-edit]').click();
    // A drag along one border crosses corners without drafting the perpendicular borders there.
    await drag(page, panel, [[1, 8.02], [2.5, 8], [3.98, 7.98]]);
    assert.equal(await panel.locator('[data-wall-draft-overlay] .wall-draft-edge').count(), 3);
    await panel.locator('[data-wall-clear]').click();
    // Starting on an existing wall's end draws from it instead of selecting the wall.
    await panel.locator('[data-tactical-geometry="wall-a"]').click();
    await drag(page, panel, [[6, 2.02], [6.02, 1.1]]);
    assert.equal(await panel.locator('[data-wall-draft-overlay] .wall-draft-edge').count(), 1);
    assert.match(await panel.locator('[data-wall-editor-status]').innerText(), /choose Extend wall to connect it, or New wall to keep it separate/);
    const extend = panel.locator('[data-wall-commit="extend"]');
    assert.ok(await extend.isEnabled());
    assert.equal(await extend.evaluate(node => node.classList.contains('btn-primary')), false,
      'Joining is never the visually preferred default');
    await extend.click();
    await page.waitForFunction(() => /Not saved/.test(document.querySelector('[data-wall-editor-status]')?.textContent || ''));
    const [command] = await page.evaluate(() => window.previews);
    assert.equal(command.panel_action_id, 'wall-extend');
    assert.deepEqual(command.selection, { map_revision: 1, edges: [[[6, 1], [6, 2]]], edge_anchor: ['wall-a'] });
    assert.equal(await panel.locator('[data-wall-editor-status]').innerText(), 'Not saved. The owner refused this test edit.');
    assert.equal(await panel.locator('[data-wall-draft-overlay] .wall-draft-edge').count(), 1, 'A refusal keeps the draft');
    // A coded owner refusal at commit, with no saved result, leaves nothing to recover.
    await page.evaluate(() => Object.assign(window.wallTest.state.bridge, {
      preview_panel_action: async () => ({ confirmation_required: false, confirmation: 'exact' }),
      execute_panel_action: async () => { throw Object.assign(new Error('The wall edit was refused.'),
        { code: 'ruleset_precondition_rejected' }); },
      panel_action_receipt: async () => null,
    }));
    await panel.locator('[data-wall-commit="extend"]').click();
    await page.waitForFunction(() => /refused/.test(document.querySelector('[data-wall-editor-status]')?.textContent || ''));
    assert.equal(await page.locator('.panel-action-recovery').count(), 0);
    assert.equal(await page.evaluate(() => Object.keys(localStorage).filter((key) => key.startsWith('sylvia-panel-recovery')).length), 0);
    assert.ok(await panel.locator('[data-wall-commit="extend"]').isEnabled(), 'The editor stays usable after a refusal');
    // Keyboard: Shift+Arrow toggles a border of the focused cell and keeps focus there.
    await panel.locator('[data-tactical-cell="2,2"]').focus();
    await page.keyboard.press('Shift+ArrowDown');
    assert.equal(await page.evaluate(() => document.activeElement?.dataset.tacticalCell), '2,2');
    assert.match(await panel.locator('[data-wall-editor-status]').innerText(), /Added the north border of cell 3, 4/);
    await page.keyboard.press('Escape');
    assert.equal(await panel.locator('[data-wall-draft-overlay] .wall-draft-edge').count(), 0);
  } finally { await browser.close(); }
});

test('wall editor stays inside a narrow map panel', { timeout: 60000 }, async () => {
  const { browser, page, panel } = await openMap(760, 900);
  try {
    await panel.locator('.tactical-toolbar [data-tactical-wall-edit]').click();
    const box = await panel.locator('.wall-editor').evaluate((node) => {
      const content = node.closest('.ruleset-panel-content').getBoundingClientRect();
      const rect = node.getBoundingClientRect();
      return { inside: rect.left >= content.left - 1 && rect.right <= content.right + 1, overflow: node.scrollWidth > node.clientWidth + 1 };
    });
    assert.deepEqual(box, { inside: true, overflow: false });
    for (const name of ['New wall', 'Clear', 'Done']) assert.ok(await panel.getByRole('button', { name, exact: true }).isVisible());
  } finally { await browser.close(); }
});

test('wall editor distinguishes an unknown save from a proven owner refusal', { timeout: 60000 }, async () => {
  const { browser, page, panel } = await openMap(1400, 932);
  try {
    await panel.locator('.tactical-toolbar [data-tactical-wall-edit]').click();
    await panel.locator('[data-tactical-cell="2,2"]').focus();
    await page.keyboard.press('Shift+ArrowRight');
    await page.evaluate(() => {
      window.executions = [];
      Object.assign(window.wallTest.state.bridge, {
        preview_panel_action: async () => ({ confirmation_required: false, confirmation: 'exact' }),
        execute_panel_action: async (command) => { window.executions.push(command);
          throw Object.assign(new Error('Connection interrupted; saved work may exist.'), { code: 'outcome_unknown' }); },
        panel_action_receipt: async () => { throw new Error('The receipt is unavailable.'); },
      });
    });
    await panel.locator('[data-wall-commit="draw"]').click();
    await page.waitForFunction(() => window.executions.length === 1 && !window.wallTest.state.panelCommitPending &&
      !window.wallTest.state.panelIntentPending);
    const message = await panel.locator('[data-wall-editor-status]').innerText();
    assert.match(message, /unconfirmed/i);
    assert.match(message, /saved work may exist/);
    assert.doesNotMatch(message, /not saved/i);
    assert.equal(await page.locator('.panel-action-recovery').count(), 1);
    assert.equal(await panel.locator('[data-wall-draft-overlay] .wall-draft-edge').count(), 1);
    assert.ok(await panel.locator('[data-wall-commit="draw"]').isDisabled());
    assert.equal(await page.evaluate(() => document.activeElement?.hasAttribute('data-wall-editor-status')), true);
    // An absent lookup alone does not prove a timed-out send was rejected; retry retains its ID.
    await page.evaluate(() => { window.wallTest.state.bridge.panel_action_receipt = async () => null; });
    await page.locator('[data-panel-recover]').click();
    await page.locator('[data-panel-retry-original]').waitFor();
    assert.doesNotMatch(await panel.locator('[data-wall-editor-status]').innerText(), /not saved/i);
    await page.evaluate(() => { window.wallTest.state.bridge.execute_panel_action = async (command) => {
      window.executions.push(command); return { status: 'committed' }; }; });
    await page.locator('[data-panel-retry-original]').click();
    await page.waitForFunction(() => !document.querySelector('.panel-action-recovery'));
    const commands = await page.evaluate(() => window.executions);
    assert.equal(commands.length, 2);
    assert.deepEqual(commands[1], commands[0], 'Explicit retry must send the identical original operation');
    // A later, coded rejection confirmed absent can truthfully say Not saved.
    await page.evaluate(() => { window.wallTest.state.bridge.execute_panel_action = async () => {
      throw Object.assign(new Error('The owner refused this edit.'), { code: 'ruleset_precondition_rejected' }); }; });
    await panel.locator('[data-wall-commit="draw"]').click();
    await page.waitForFunction(() => /Not saved/.test(document.querySelector('[data-wall-editor-status]')?.textContent || ''));
    assert.equal(await panel.locator('[data-wall-editor-status]').innerText(), 'Not saved. The owner refused this edit.');
    assert.equal(await page.locator('.panel-action-recovery').count(), 0);
    assert.ok(await panel.locator('[data-wall-commit="draw"]').isEnabled());
  } finally { await browser.close(); }
});
