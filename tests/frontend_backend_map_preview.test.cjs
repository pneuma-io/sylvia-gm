const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

function element() {
  return { innerHTML: '', dataset: {}, hidden: false, style: {},
    classList: { add() {}, remove() {}, toggle() {} }, addEventListener() {},
    removeEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
    setAttribute() {}, appendChild() {} };
}

function harness() {
  const nodes = new Map();
  const node = (id) => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); };
  const context = { window: {}, crypto: webcrypto, console, URL, performance, CSS: { escape: value => value },
    document: { body: element(), getElementById: node, addEventListener() {}, createElement: element },
    localStorage: { getItem: () => null, setItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
  let source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const insertion = source.lastIndexOf('  return {');
  source = source.slice(0, insertion) + `
    window.previewTest = { state, renderTacticalGridPanel, panelActionCheckKey,
      updateTacticalSelection, tacticalTargetSelection, tacticalPreviewMarkup, refreshPanelActionChecks, panelMarkup };
  ` + source.slice(insertion);
  source = ['panel-workspace.js', 'shot-playback.js', 'panel-host.js'].map((name) =>
    fs.readFileSync(path.join(__dirname, '../src-tauri/frontend', name), 'utf8')).join('\n') + '\n' + source;
  context.setTimeout ||= setTimeout;
  context.clearTimeout ||= clearTimeout;
  context.window.innerWidth = 1400;
  vm.runInNewContext(source, context);
  return context.window.previewTest;
}

function fixture() {
  const action = { id: 'move', label: 'Move', checks_required: true,
    map_preview_enabled: true, bindings: { actor: 'actor', destination: 'destination' } };
  const surface = { id: 'map', renderer: 'tactical_grid', actions: [action], data: {
    map_revision: 7, map: { id: 'floor', bounds: { min: [0, 0, 0], max: [4, 4, 3] },
      grid: { cell_size: 1, unit: 'm' } }, selection: { actor: 'hero' },
    terrain: [], zones: [], objectives: [],
    geometry: [{ id: 'cover', active: true, movement: 'blocking', visibility: 'opaque',
      occupies_cells: true, volume: { kind: 'box', min: [2, 1, 0], max: [3, 2, 1] },
      display: { label: 'Cover', kind: 'cover' } }],
    entities: [{ id: 'hero', position: [0.5, 0.5, 0], display: { label: 'Hero' } }],
  } };
  return { surface, action };
}

function setup() {
  const api = harness();
  const { surface, action } = fixture();
  Object.assign(api.state, { activeCampaignId: 'campaign', activeSessionId: 'session',
    panelProjection: { campaign_id: 'campaign', revision: 11, player_control: null,
      surfaces: [surface] } });
  api.updateTacticalSelection(surface, { destination: [3, 2] });
  return { api, surface, action };
}

test('cached action preview renders the owner route, ray, card, and geometry highlights', () => {
  const { api, surface, action } = setup();
  const key = api.panelActionCheckKey(surface, action);
  api.state.panelActionChecks.set(key, { available: true,
    preview: { version: 1, title: 'Movement', summary: [{ label: 'AP', value: 2, emphasis: false }],
      details: [], actions: [] },
    map_preview: { map_id: 'floor', map_revision: 7, notice: 'Based on the visible map.',
      path: { found: true, truncated: false, cost: 2, cells: [[0, 0], [1, 1]],
        points: [[0.5, 0.5, 0], [1.5, 1.5, 0], [3.5, 2.5, 0]] },
      ray: { origin: [0.5, 0.5, 1], destination: [3.5, 2.5, 1],
        contacts: [{ kind: 'geometry', id: 'cover', point: [2.5, 1.5, 1] }] },
      exposure: { fraction: 0.5, cover_ids: ['cover'] }, crossed_zones: [] } });
  const html = api.renderTacticalGridPanel(surface.data, surface.id);
  const completePanel = api.panelMarkup(surface);
  assert.match(completePanel, /data-tactical-action="move"/);
  assert.match(completePanel, /Commit Move/);
  assert.match(html, /tactical-preview-route/);
  assert.match(html, /tactical-preview-ray/);
  assert.match(html, /tactical-preview-contact/);
  assert.match(html, /preview-highlight/);
  assert.match(html, /Movement/);
  assert.match(html, /Route cost 2 · Objects along firing line: 1 · Exposed silhouette: 50%/);
  assert.match(html, /Based on the visible map\./);
});

test('unavailable and stale records do not draw overlays or execute during selection', () => {
  const { api, surface, action } = setup();
  let executions = 0;
  api.state.bridge = { execute_panel_action: async () => { executions += 1; } };
  let key = api.panelActionCheckKey(surface, action);
  api.state.panelActionChecks.set(key, { available: false, reason: 'No legal route.',
    preview: { version: 1, title: 'Movement estimate',
      summary: [{ label: 'AP', value: 3, emphasis: false }], details: [], actions: [] },
    map_preview: { map_id: 'floor', map_revision: 7, notice: 'No route is currently available.',
      path: { found: false, truncated: false, cost: null, cells: [], points: [] } } });
  let html = api.renderTacticalGridPanel(surface.data, surface.id);
  assert.match(html, /No legal route\./);
  assert.match(html, /Movement estimate/);
  assert.match(html, /No route found/);
  assert.match(html, /No route is currently available\./);
  assert.doesNotMatch(html, /tactical-preview-route/);
  api.state.panelActionChecks.set(key, { available: true, map_preview: {
    map_id: 'floor', map_revision: 6, notice: 'Old.',
    path: { found: true, truncated: false, cost: 1, points: [[0, 0, 0], [1, 1, 0]] } } });
  html = api.renderTacticalGridPanel(surface.data, surface.id);
  assert.match(html, /map preview is out of date/);
  assert.doesNotMatch(html, /tactical-preview-route/);
  api.updateTacticalSelection(surface, { destination: [1, 1] });
  assert.equal(executions, 0);
});

test('firing preview explains hidden, partial and clear silhouettes using public cover labels', () => {
  const { api, surface, action } = setup();
  surface.data.geometry[0].display.label = 'Crate <front>';
  surface.data.entities.push({ id: 'target', display: { label: 'Target' } });
  const key = api.panelActionCheckKey(surface, action);
  for (const [fraction, explanation] of [
    [0, 'Target hidden from this angle.'],
    [1 / 3, 'Target partly visible from this angle.'],
    [1, 'Target fully visible from this angle.'],
  ]) {
    api.state.panelActionChecks.set(key, { available: true, map_preview: {
      map_id: 'floor', map_revision: 7,
      ray: { contacts: [
        { kind: 'geometry', id: 'cover' }, { kind: 'entity', id: 'target' },
      ] },
      exposure: { fraction, cover_ids: fraction < 1 ? ['cover'] : [] },
    } });
    const html = api.tacticalPreviewMarkup(surface, {});
    assert.ok(html.includes(explanation));
    assert.match(html, /Firing line meets: Crate &lt;front&gt; → Target\./);
    if (fraction < 1) assert.match(html, /Obscuring cover: Crate &lt;front&gt;\./);
    else assert.doesNotMatch(html, /Obscuring cover:/);
    assert.doesNotMatch(html, /<front>|hit chance|cannot penetrate/i);
  }
});

test('firing explanations never surface unknown contact IDs or private geometry labels', () => {
  const { api, surface, action } = setup();
  delete surface.data.geometry[0].display;
  surface.data.geometry[0].metadata = { label: 'Private construction detail' };
  const key = api.panelActionCheckKey(surface, action);
  api.state.panelActionChecks.set(key, { available: true, map_preview: {
    map_id: 'floor', map_revision: 7,
    ray: { contacts: [
      { kind: 'geometry', id: 'unseen-wall' }, { kind: 'geometry', id: 'cover' },
    ] },
    exposure: { fraction: 0, cover_ids: ['unseen-wall', 'cover'] },
  } });
  const html = api.tacticalPreviewMarkup(surface, {});
  assert.match(html, /Target hidden from this angle\./);
  assert.match(html, /Obscuring cover: Barrier\./);
  assert.match(html, /Firing line meets: Barrier\./);
  assert.doesNotMatch(html, /unseen-wall|Private construction detail/);
});


test('stance and geometry refresh retain a selected target and refresh its firing aim', () => {
  const { api, surface } = setup();
  surface.data.entities[0].height = 1.8;
  surface.data.entities.push({ id: 'target', position: [3.5, 2.5, 0], height: 1.8,
    display: { label: 'Target', kind: 'unit' } });
  api.updateTacticalSelection(surface, api.tacticalTargetSelection(surface, 'target'));
  surface.data.map_revision += 1;
  surface.data.entities[0].height = 1.15;
  api.renderTacticalGridPanel(surface.data, surface.id);
  assert.equal(api.state.tacticalSelections.map.target, 'target');
  assert.deepEqual(Array.from(api.state.tacticalSelections.map.aim_point || []), [3.5, 2.5, 0.9]);
  assert.equal(api.state.tacticalSelections.map.map_revision, surface.data.map_revision);
  surface.data.map_revision += 1;
  surface.data.entities[1].height = 0.45;
  surface.data.entities[1].position = [2.5, 3.5, 0];
  api.renderTacticalGridPanel(surface.data, surface.id);
  assert.deepEqual(Array.from(api.state.tacticalSelections.map.aim_point || []), [2.5, 3.5, 0.225]);
  surface.data.map_revision += 1;
  surface.data.entities.pop();
  api.renderTacticalGridPanel(surface.data, surface.id);
  assert.equal(api.state.tacticalSelections.map.target, null);
  assert.equal(api.state.tacticalSelections.map.aim_point, undefined);
});


test('explicit cell aim is discarded on a new geometry revision instead of retargeted', () => {
  const { api, surface } = setup();
  surface.data.entities.push({ id: 'target', position: [3.5, 2.5, 0], height: 1.8,
    display: { label: 'Target', kind: 'unit' } });
  api.updateTacticalSelection(surface, api.tacticalTargetSelection(surface, 'target'));
  api.updateTacticalSelection(surface, { destination: [2, 1], aim_point: [2.5, 1.5, 0] });
  surface.data.map_revision += 1;
  api.renderTacticalGridPanel(surface.data, surface.id);
  assert.equal(api.state.tacticalSelections.map.aim_point, undefined);
  assert.equal(api.state.tacticalSelections.map.destination, undefined);
  surface.data.map_revision += 1;
  api.renderTacticalGridPanel(surface.data, surface.id);
  assert.equal(api.state.tacticalSelections.map.aim_point, undefined);
});


function delayedAvailability() {
  const { api, surface, action } = setup();
  const projection = api.state.panelProjection;
  const button = { dataset: { panelActionId: action.id }, disabled: false, title: '',
    closest: () => ({ dataset: { panelId: surface.id } }), setAttribute() {} };
  const container = { querySelectorAll: () => [button], querySelector: () => null };
  let complete;
  api.state.bridge = { player_action_availability: () => new Promise(resolve => { complete = resolve; }) };
  const key = api.panelActionCheckKey(surface, action);
  const running = api.refreshPanelActionChecks(projection, container);
  assert.equal(api.state.panelActionChecks.get(key).pending, true);
  return { api, surface, action, projection, key, running, complete, button };
}

test('an equivalent refreshed projection completes its in-flight map preview', async () => {
  const { api, projection, key, running, complete } = delayedAvailability();
  api.state.panelProjection = structuredClone(projection);
  complete({ campaign_id: 'campaign', revision: 11,
    results: [{ available: false, reason: 'The Action is already spent.' }] });
  await running;
  const result = api.state.panelActionChecks.get(key);
  assert.equal(result.pending, undefined);
  assert.equal(result.available, false);
  assert.equal(result.reason, 'The Action is already spent.');
});

test('a delayed failed check on an equivalent projection leaves a retry message, not Checking', async () => {
  const { api, projection, key, running, complete } = delayedAvailability();
  api.state.panelProjection = structuredClone(projection);
  complete({ campaign_id: 'campaign', revision: 12, results: [] });
  await running;
  const result = api.state.panelActionChecks.get(key);
  assert.equal(result.pending, undefined);
  assert.equal(result.available, false);
  assert.match(result.reason, /Could not check/);
});

test('a newer revision discards the old result and releases its abandoned pending entry', async () => {
  const { api, projection, key, running, complete } = delayedAvailability();
  api.state.panelProjection = { ...projection, revision: 12 };
  complete({ campaign_id: 'campaign', revision: 11, results: [{ available: true }] });
  await running;
  assert.equal(api.state.panelActionChecks.has(key), false);
});

test('abandoned preview cleanup preserves a newer request owning the same key', async () => {
  const { api, projection, key, running, complete } = delayedAvailability();
  const newer = { available: false, pending: true, reason: 'New request owns this key.' };
  api.state.panelActionChecks.set(key, newer);
  api.state.panelProjection = { ...projection, revision: 12 };
  complete({ campaign_id: 'campaign', revision: 11, results: [{ available: true }] });
  await running;
  assert.equal(api.state.panelActionChecks.get(key), newer);
});
