const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createStore } = require('../src-tauri/frontend/panel-workspace.js');

function memoryStorage(seed = {}) {
  const values = new Map(Object.entries(seed));
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, value); },
    values,
  };
}

const surfaces = [
  { id: 'map', title: 'Map', order: 10 },
  { id: 'party', title: 'Party', order: 20, group: 'people' },
  { id: 'inventory', title: 'Inventory', order: 30, group: 'people' },
  { id: 'encounter', title: 'Encounter', order: 40 },
];

test('inspection and expansion persist through arrangement and reopen without entering another campaign default', () => {
  const storage=memoryStorage(),store=createStore(storage);
  store.select({campaignId:'a',rulesetId:'r',surfaces});
  const inspection={campaignId:'a',rulesetId:'r',id:'companion',unavailableId:null,expanded:['hero','companion']};
  store.change({type:'inspection',value:inspection});
  store.change({type:'hide',panelId:'party'});
  store.change({type:'show',panelId:'party'});
  const moved=store.change({type:'move',panelId:'party',destination:{windowId:'new'}});
  store.change({type:'close-window',windowId:moved.windows[0].id});
  assert.deepEqual(store.snapshot().inspection,inspection);
  store.preference('save');
  const reopened=createStore(storage);
  assert.deepEqual(reopened.select({campaignId:'a',rulesetId:'r',surfaces}).inspection,inspection);
  assert.equal(reopened.select({campaignId:'b',rulesetId:'r',surfaces}).inspection,null);
});

function locations(layout) {
  const result = [];
  function collect(tree) {
    if (!tree) return;
    if (tree.kind === 'tabs') result.push(...tree.panels);
    else { collect(tree.first); collect(tree.second); }
  }
  collect(layout.sidebar);
  layout.windows.forEach((window) => collect(window.tree));
  result.push(...layout.hidden);
  return result;
}

test('persists a two-window lifecycle and manually redocks a closed window', () => {
  const storage = memoryStorage();
  const store = createStore(storage, { randomUUID: (() => { let n = 0; return () => `id${++n}`; })() });
  store.select({ campaignId: 'a', rulesetId: 'r', surfaces });
  let layout = store.change({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  const mapWindow = layout.windows[0].id;
  layout = store.change({ type: 'move', panelId: 'party', destination: { windowId: 'new' } });
  const peopleWindow = layout.windows[1].id;
  layout = store.change({ type: 'move', panelId: 'inventory', destination: {
    windowId: peopleWindow, targetPanelId: 'party', position: 'below'
  } });
  assert.equal(layout.windows.length, 2);
  assert.deepEqual(locations(layout).sort(), surfaces.map((item) => item.id).sort());
  assert.equal(layout.windows.find((item) => item.id === peopleWindow).tree.axis, 'column');

  const reopened = createStore(storage).select({ campaignId: 'a', rulesetId: 'r', surfaces });
  assert.equal(reopened.windows.length, 2);
  assert.equal(reopened.windows.some((item) => item.id === mapWindow), true);
  const closed = createStore(storage).select({ campaignId: 'a', rulesetId: 'r', surfaces });
  const closer = createStore(storage);
  closer.select({ campaignId: 'a', rulesetId: 'r', surfaces });
  layout = closer.change({ type: 'close-window', windowId: peopleWindow });
  assert.equal(layout.windows.length, 1);
  assert.equal(locations(layout).filter((id) => id === 'party').length, 1);
  assert.equal(locations(layout).filter((id) => id === 'inventory').length, 1);
  assert.equal(closed.windows.length, 2);
});

test('Ruleset save and resets preserve campaign isolation and map views', () => {
  const storage = memoryStorage();
  const store = createStore(storage, { randomUUID: () => 'fixed' });
  store.select({ campaignId: 'a', rulesetId: 'r', surfaces });
  store.change({ type: 'hide', panelId: 'inventory' });
  store.saveView('map-one', {
    zoom: 1.5, scrollLeft: 20, scrollTop: 30, coverInfo: true,
    selectedGeometry: 'room-2', destination: 'room-9', action: 'move',
  });
  store.preference('save');

  let other = store.select({ campaignId: 'b', rulesetId: 'r', surfaces });
  assert.deepEqual(other.hidden, ['inventory']);
  store.change({ type: 'show', panelId: 'inventory' });
  other = store.preference('mine');
  assert.deepEqual(other.hidden, ['inventory']);

  let first = store.select({ campaignId: 'a', rulesetId: 'r', surfaces });
  assert.deepEqual(first.hidden, ['inventory']);
  assert.deepEqual(store.view('map-one'), {
    zoom: 1.5, scrollLeft: 20, scrollTop: 30, coverInfo: true, selectedGeometry: 'room-2'
  });
  first = store.preference('original');
  assert.deepEqual(first.hidden, []);
  assert.equal(store.view('map-one').zoom, 1.5);
  store.preference('mine');
  assert.deepEqual(store.snapshot().hidden, ['inventory']);
});

test('reconciles new and temporarily absent panels without duplication', () => {
  const storage = memoryStorage();
  const store = createStore(storage, { randomUUID: () => 'fixed' });
  store.select({ campaignId: 'a', rulesetId: 'r', surfaces });
  let layout = store.change({ type: 'move', panelId: 'encounter', destination: { windowId: 'new' } });
  const windowId = layout.windows[0].id;

  layout = store.select({ campaignId: 'a', rulesetId: 'r', surfaces: surfaces.filter((item) => item.id !== 'encounter') });
  assert.equal(layout.windows[0].id, windowId);
  assert.equal(locations(layout).includes('encounter'), true);

  layout = store.select({ campaignId: 'a', rulesetId: 'r', surfaces: [...surfaces, { id: 'journal', order: 50 }] });
  assert.equal(locations(layout).filter((id) => id === 'encounter').length, 1);
  assert.equal(locations(layout).filter((id) => id === 'journal').length, 1);
  assert.equal(layout.windows[0].id, windowId);
});

test('malformed persistence falls back and legacy presentation fields migrate once', () => {
  const storage = memoryStorage({
    'sylvia-gm-panel-workspace-v1:campaign:a': '{broken',
    'sylvia-gm-panel-layout-v1': JSON.stringify({ sidebar_width: 333, panel_sizes: { map: 260 }, tabs: { people: 'party' } }),
  });
  const layout = createStore(storage).select({ campaignId: 'a', rulesetId: 'r', surfaces });
  assert.equal(layout.sidebar_width, 333);
  assert.equal(layout.panel_sizes.map, 260);
  assert.deepEqual(locations(layout).sort(), surfaces.map((item) => item.id).sort());
});

test('Ruleset preference wins over legacy data and legacy tabs migrate on original fallback', () => {
  const originalStorage = memoryStorage({
    'sylvia-gm-panel-layout-v1': JSON.stringify({
      sidebar_width: 333, panel_sizes: { map: 260 }, tabs: { people: 'inventory' }
    }),
  });
  let layout = createStore(originalStorage).select({ campaignId: 'a', rulesetId: 'r', surfaces });
  function activeFor(tree, panelId) {
    if (!tree) return null;
    if (tree.kind === 'tabs' && tree.panels.includes(panelId)) return tree.active;
    return tree.kind === 'split' ? activeFor(tree.first, panelId) || activeFor(tree.second, panelId) : null;
  }
  assert.equal(activeFor(layout.sidebar, 'party'), 'inventory');

  const preferredStorage = memoryStorage({
    'sylvia-gm-panel-layout-v1': JSON.stringify({ sidebar_width: 399, panel_sizes: { map: 300 } }),
  });
  const preferred = createStore(preferredStorage);
  preferred.select({ campaignId: 'seed', rulesetId: 'r', surfaces });
  preferred.change({ type: 'sidebar-width', width: 240 });
  preferred.preference('save');
  layout = preferred.select({ campaignId: 'new', rulesetId: 'r', surfaces });
  assert.equal(layout.sidebar_width, 240);
  assert.equal(layout.panel_sizes.map, 300);
});

test('keeps null width, distinct default IDs, and normalizes malformed identities', () => {
  const colliding = [
    { id: 'one', order: 1, group: 'a.b' },
    { id: 'two', order: 2, group: 'a-b' },
    { id: 'panel:x', order: 3 },
    { id: 'x', order: 4, group: 'panel:x' },
  ];
  let layout = createStore(memoryStorage()).select({ campaignId: 'a', rulesetId: 'r', surfaces: colliding });
  assert.equal(layout.sidebar_width, null);
  const nodeIds = [];
  (function collect(tree) {
    if (!tree) return;
    nodeIds.push(tree.id);
    if (tree.kind === 'split') { collect(tree.first); collect(tree.second); }
  })(layout.sidebar);
  assert.equal(new Set(nodeIds).size, nodeIds.length);

  const malformed = {
    version: 1,
    sidebar: { kind: 'tabs', id: 'same', panels: ['one', 'one', 4], active: 'one' },
    windows: [
      { id: 'window', title: 'A', tree: { kind: 'tabs', id: 'same', panels: ['two'], active: 'two' } },
      { id: 'window', title: 'B', tree: { kind: 'tabs', panels: ['panel:x'], active: 'panel:x' } },
    ],
    hidden: ['x', 'x', null], known: ['one', 'two', 'panel:x', 'x', null, 5],
    panel_sizes: {}, sidebar_width: null, views: {},
  };
  layout = createStore(memoryStorage()).select({ campaignId: 'b', rulesetId: 'r', surfaces: colliding, original: malformed });
  assert.deepEqual(locations(layout).sort(), colliding.map((item) => item.id).sort());
  assert.equal(new Set(layout.windows.map((item) => item.id)).size, layout.windows.length);
  const allNodeIds = [];
  (function collectAll(tree) {
    if (!tree) return;
    allNodeIds.push(tree.id);
    if (tree.kind === 'split') { collectAll(tree.first); collectAll(tree.second); }
  })(layout.sidebar);
  layout.windows.forEach((window) => (function collectAll(tree) {
    if (!tree) return;
    allNodeIds.push(tree.id);
    if (tree.kind === 'split') { collectAll(tree.first); collectAll(tree.second); }
  })(window.tree));
  assert.equal(new Set(allNodeIds).size, allNodeIds.length);
});

test('moving a panel onto itself or its sole window is a no-op', () => {
  const store = createStore(memoryStorage(), { randomUUID: () => 'fixed' });
  store.select({ campaignId: 'a', rulesetId: 'r', surfaces });
  let layout = store.change({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  const before = JSON.stringify(layout);
  const windowId = layout.windows[0].id;
  layout = store.change({ type: 'move', panelId: 'map', destination: {
    windowId, targetPanelId: 'map', position: 'tab'
  } });
  assert.equal(JSON.stringify(layout), before);
  layout = store.change({ type: 'move', panelId: 'map', destination: { windowId } });
  assert.equal(JSON.stringify(layout), before);
});

test('map views reject commands and malformed camera values from input and storage', () => {
  const storage = memoryStorage();
  const store = createStore(storage);
  store.select({ campaignId: 'a', rulesetId: 'r', surfaces });
  assert.deepEqual(store.saveView('map', {
    zoom: 'huge', scrollLeft: -8, scrollTop: Infinity, coverInfo: 'yes',
    selectedGeometry: { id: 'secret' }, action: 'move', destination: 'north',
  }), { scrollLeft: 0 });
  const saved = JSON.parse(storage.values.get('sylvia-gm-panel-workspace-v1:campaign:a'));
  saved.views.map = { zoom: 99, scrollLeft: '12', coverInfo: true, selectedGeometry: 'x', command: { act: true } };
  storage.values.set('sylvia-gm-panel-workspace-v1:campaign:a', JSON.stringify(saved));
  store.select({ campaignId: 'a', rulesetId: 'r', surfaces });
  assert.deepEqual(store.view('map'), { zoom: 2, coverInfo: true, selectedGeometry: 'x' });
});

test('browser wrapper installs on window when globalThis is separate', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/panel-workspace.js'), 'utf8');
  const context = { window: {}, globalThis: { wrong: true } };
  vm.runInNewContext(source, context);
  assert.equal(typeof context.window.SylviaPanelWorkspace.createStore, 'function');
  assert.equal(context.globalThis.SylviaPanelWorkspace, undefined);
});

test('null selection does not write storage', () => {
  const storage = memoryStorage();
  const store = createStore(storage);
  assert.equal(store.select(null), null);
  assert.equal(storage.values.size, 0);
});

test('Ruleset default placements create their declared window groups', () => {
  const store = createStore(memoryStorage());
  const authored = surfaces.map((surface) => ({ ...surface,
    default_placement: surface.group === 'people' ? 'window' : 'docked' }));
  const layout = store.select({ campaignId: 'a', rulesetId: 'r', surfaces: authored });
  assert.equal(layout.windows.length, 1);
  assert.deepEqual(layout.windows[0].tree.panels, ['party', 'inventory']);
  assert.equal(layout.windows[0].title, 'Party');
  assert.deepEqual(new Set(locations(layout)), new Set(surfaces.map((item) => item.id)));
});


test('fresh game layout gives the app queue its own compact leaf, not a Party tab',()=>{
 const store=createStore(memoryStorage());const panels=[{id:'map',renderer:'coordinate_map'},{id:'party',renderer:'party'},{id:'character',renderer:'unit_list'},{id:'sylvia.action-queue',renderer:'action_queue',default_size:220}];
 const layout=store.select({campaignId:'queue',rulesetId:'rules',surfaces:panels});
 const leaves=[];const visit=t=>{if(t.kind==='tabs')leaves.push(t.panels);else{visit(t.first);visit(t.second);}};visit(layout.sidebar);
 assert.ok(leaves.some(ids=>ids.length===1&&ids[0]==='sylvia.action-queue'));assert.ok(leaves.some(ids=>ids.length===1&&ids[0]==='party'));
});
test('adding queue to a saved workspace preserves its existing tree, windows and sizes',()=>{
 const storage=memoryStorage(),store=createStore(storage);store.select({campaignId:'saved',rulesetId:'r',surfaces});store.change({type:'size',panelId:'party',height:321});store.change({type:'move',panelId:'inventory',destination:{windowId:'new'}});const before=store.snapshot();
 const after=createStore(storage).select({campaignId:'saved',rulesetId:'r',surfaces:[...surfaces,{id:'sylvia.action-queue',renderer:'action_queue'}]});
 assert.deepEqual(after.sidebar.first,before.sidebar);assert.deepEqual(after.windows,before.windows);assert.deepEqual(after.panel_sizes,before.panel_sizes);assert.equal(locations(after).filter(id=>id==='sylvia.action-queue').length,1);
});
