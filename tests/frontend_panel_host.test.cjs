// Exercise the selected inline-only host. Old window layouts are migration fixtures, never live windows.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const flush = () => new Promise(setImmediate);

function transport(options = {}) {
  const listeners = new Map(), calls = [], windows = new Map();
  let registeredContext = null;
  let publishStalled = false;
  let activeDrag = null;
  const emit = (id, name, payload) => listeners.get(`${id}:${name}`)?.({ payload });
  const endpoint = (id) => ({
    event: { listen: async (name, fn) => { listeners.set(`${id}:${name}`, fn); } },
    core: { invoke: async (command, args) => {
      calls.push({ id, command, args });
      if (command === 'sync_panel_windows') {
        await options.beforeSync?.(args);
        registeredContext = args.context;
        windows.clear();
        const failed = args.windows.filter((window) => options.failWindow?.(window));
        args.windows.filter((window) => !failed.includes(window))
          .forEach((window) => windows.set(window.id, args.context));
        return { failed_window_ids: failed.map((window) => window.id) };
      } else if (command === 'begin_panel_drag') {
        await options.beforeDrag?.(args);
        activeDrag = { id, token: args.dragId };
      } else if (command === 'finish_panel_drag') {
        if (activeDrag?.id !== id || activeDrag.token !== args.dragId) return null;
        activeDrag = null;
        return args.cancelled ? null : options.dropPlacement || null;
      } else if (command === 'publish_panel_workspace') {
        if (options.enforceRegisteredContext &&
            JSON.stringify(args.context) !== JSON.stringify(registeredContext)) {
          throw new Error('Panel context is no longer active');
        }
        if (publishStalled) return new Promise(() => {});
        windows.forEach((context, window_id) => emit(window_id, 'panel-workspace', {
          context: args.context, window_id, payload: args.payload,
        }));
      } else if (command === 'panel_window_message') {
        emit('main', 'panel-window-message', { ...args, window_id: id });
      } else if (command === 'panel_window_reply') {
        emit(args.windowId, 'panel-window-reply', { ...args, request_id: args.requestId });
      }
      return {};
    } },
  });
  return { endpoint, emit, calls, windows,
    stallPublishes: () => { publishStalled = true; },
    resumePublishes: () => { publishStalled = false; } };
}

function renderer(bus, id, state, storage, context, options = {}) {
  const clock = options.clock || { now: Date.now() };
  const errors = [], selections = [], dispatches = [], previews = [];
  let renderCount = 0;
  let bodyTheme = null;
  const sandbox = {
    window: { __TAURI__: bus.endpoint(id), innerWidth: 1200, innerHeight: 800,
      requestAnimationFrame: options.requestAnimationFrame ||
      ((callback) => { callback(); return 1; }), ...(context ? {
      __sylviaPanelContext: { context, window_id: id },
    } : {}) },
    document: { body: { setAttribute(name, value) { if (name === 'data-theme') bodyTheme = value; },
      appendChild: (element) => previews.push(element) },
      addEventListener() {}, querySelectorAll: () => [],
      createElement: () => ({ style: {}, setAttribute() {}, querySelectorAll: () => [],
        remove() { this.removed = true; } }) },
    localStorage: { getItem: (key) => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value) },
    crypto: webcrypto, CSS: {escape: value => String(value)}, Date: { now: () => clock.now },
    setTimeout: options.setTimeout || setTimeout, clearTimeout: options.clearTimeout || clearTimeout,
  };
  for (const name of ['panel-workspace.js', 'shot-playback.js', 'panel-host.js']) {
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend', name), 'utf8'), sandbox);
  }
  if (options.frontendTheme) {
    const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8')
      .replace('  // ---- Public API ----', `window.themeTest = { state, setTheme,
        bindHost(value) { panelHost = value; } }; // ---- Public API ----`);
    vm.runInNewContext(source, sandbox);
    Object.assign(sandbox.window.themeTest.state, state);
    state = sandbox.window.themeTest.state;
  }
  let host, html;
  const render = (projection = state.panelProjection) => {
    renderCount += 1;
    state.panelProjection = projection;
    html = host.markup(projection);
    host.presentEffects(projection, () => {});
    clock.now += options.renderCost || 0;
  };
  host = sandbox.window.SylviaPanelHost.create({
    state, render, renderPanel: (surface) => `<article>${surface.title}</article>`,
    dispatch: async (method, args, scope) => {
      dispatches.push({ method, args, campaign: scope.campaign_id });
      return { revision: state.panelProjection.revision };
    },
    selection: (surfaceId, patch) => {
      selections.push({ surfaceId, patch });
      state.tacticalSelections.map = { ...state.tacticalSelections.map, ...patch };
    },
    busy: () => Boolean(state.panelWindowBusy), status: (error) => errors.push(String(error)),
  });
  sandbox.window.themeTest?.bindHost(host);
  return { host, render, html: () => html, renderCount: () => renderCount,
    state, errors, selections, dispatches, previews, bodyTheme: () => bodyTheme,
    setTheme: (theme) => sandbox.window.themeTest.setTheme(theme) };
}

function state() {
  return { activeCampaignId: 'td', activeSessionId: 'gm', activeSessionRole: 'dm',
    selectedTheme: 'slate', tacticalViews: {}, tacticalSelections: {}, networkSelections: {},
    panelProjection: { campaign_id: 'td', ruleset_id: 'td-rules', revision: 2, surfaces: [
      { id: 'map', title: 'Map', renderer: 'tactical_grid', data: { map: { id: 'floor' } } },
      { id: 'party', title: 'Party', renderer: 'party' },
      { id: 'inventory', title: 'Inventory', renderer: 'inventory' },
    ] } };
}


test('keyboard tabs restore focus after synchronous main layout changes and cancel late focus after leaving', async () => {
  const bus=transport(),s=state();
  s.panelProjection.surfaces=[{id:'map',title:'Map',renderer:'tactical_grid',group:'scene'},
    {id:'network',title:'Network',renderer:'network_graph',group:'scene'}];
  const original=JSON.stringify(s.panelProjection),main=renderer(bus,'main',s,new Map());
  await main.host.init();main.render();await flush();
  const group=main.host.snapshot().sidebar.id,handlers=new Map();let focused=null;
  const tabs=['map','network'].map(id=>({dataset:{workspacePanel:id,workspaceTab:group},
    addEventListener:(name,handler)=>handlers.set(id+':'+name,handler),focus:()=>{focused=id;}}));
  const parent={querySelectorAll:()=>tabs};tabs.forEach(tab=>{tab.parentElement=parent;});
  const container={classList:{add(){},remove(){}},querySelectorAll:selector=>selector==='[data-workspace-tab]'?tabs:[],
    querySelector:selector=>tabs.find(tab=>selector.includes(`data-workspace-panel="${tab.dataset.workspacePanel}"`))};
  main.host.wire(container);
  let prevented=false;handlers.get('map:keydown')({key:'ArrowRight',preventDefault:()=>{prevented=true;}});
  await flush();assert.equal(prevented,true);assert.equal(focused,'network');
  assert.equal(main.host.snapshot().sidebar.active,'network');
  assert.equal(JSON.stringify(s.panelProjection),original);assert.deepEqual(main.dispatches,[]);assert.deepEqual(main.errors,[]);
  focused=null;handlers.get('network:keydown')({key:'Home',preventDefault(){}});main.host.leave();await flush();
  assert.equal(focused,null);assert.deepEqual(main.errors,[]);
});

function dragHarness(panelId, destination) {
  const listeners = new Map(), values = new Map();
  const classes = new Set();
  const element = (dataset) => ({
    dataset,
    closest: () => ({ getBoundingClientRect: () => ({ left: 900, top: 100, width: 300, height: 420 }) }),
    addEventListener: (name, handler) => listeners.set(`${dataset.workspacePanel || dataset.panelWorkspaceDestination}:${name}`, handler),
  });
  const tab = element({ workspacePanel: panelId, workspaceTab: 'tabs' });
  const target = element({ panelWorkspaceDestination: destination });
  const container = { classList: { add: (name) => classes.add(name), remove: (name) => classes.delete(name) },
    querySelectorAll: (selector) => {
    if (selector === '[data-workspace-tab]') return [tab];
    if (selector === '[data-panel-workspace-destination]') return [target];
    return [];
  } };
  const dataTransfer = {
    types: ['application/x-sylvia-panel'], effectAllowed: '', dropEffect: '',
    setData: (type, value) => values.set(type, value),
    getData: (type) => values.get(type) || '',
    setDragImage: (element, x, y) => { dataTransfer.image = { element, x, y }; },
  };
  return {
    container,
    classes,
    dataTransfer,
    drag: () => listeners.get(`${panelId}:dragstart`)({ dataTransfer }),
    end: () => listeners.get(`${panelId}:dragend`)({ dataTransfer }),
    drop: () => listeners.get(`${destination}:drop`)({ dataTransfer, preventDefault() {} }),
  };
}

function applyPreference(host, action) {
  let click;
  host.wire({ querySelectorAll: (selector) => selector === '[data-panel-preference]' ? [{
    dataset: { panelPreference: action }, addEventListener: (_name, handler) => { click = handler; },
  }] : [] });
  click();
}

function burstRecord(revision = 3) {
  const shots = Array.from({ length: 5 }, (_, index) => ({
    map_id: 'floor', origin: [1, 1, 1], destination: [4, 1, 1], delay_ms: index * 80,
    miss: true, contacts: [], style: {
      color: '#FFFFFF', width: 2, travel_ms: 90, impact_ms: 100, fire_sound: null, cues: {},
    },
  }));
  return { action_id: `inline-shot-${revision}`, revision, shots, unavailable: false };
}



const { createStore } = require('../src-tauri/frontend/panel-workspace.js');
const copy = value => JSON.parse(JSON.stringify(value));
const panels = tree => !tree ? [] : tree.kind === 'tabs' ? Array.from(tree.panels)
  : [...panels(tree.first), ...panels(tree.second)];
const groupOf = (tree, id) => !tree ? null : tree.kind === 'tabs'
  ? tree.panels.includes(id) ? tree : null : groupOf(tree.first, id) || groupOf(tree.second, id);
const stored = storage => JSON.parse(storage.get('sylvia-gm-panel-workspace-v1:campaign:td'));

function assertInline(main, bus) {
  const layout = main.host.snapshot();
  assert.equal(layout.windows.length, 0);
  assert.equal(bus.windows.size, 0);
  assert.deepEqual([...panels(layout.sidebar), ...layout.hidden].sort(), Array.from(layout.known).sort(),
    'every known panel occurs exactly once, visible or explicitly hidden');
  assert.ok(bus.calls.filter(call => call.command === 'sync_panel_windows')
    .every(call => call.args.windows.length === 0), 'no native window was opened, including during loading');
  assert.deepEqual(main.dispatches, [], 'presentation must not issue a game mutation');
}

// The store retains the legacy data format; only the selected host policy returns windows inline.
function seedLegacy(storage) {
  const fixture = state();
  const store = createStore({ getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) });
  store.select({ campaignId: 'td', rulesetId: 'td-rules', surfaces: fixture.panelProjection.surfaces });
  store.change({ type: 'move', panelId: 'map', destination: { windowId: 'new',
    placement: { x: -1200, y: 100, width: 650, height: 700 } } });
  store.change({ type: 'move', panelId: 'party', destination: { windowId: 'new' } });
  store.change({ type: 'hide', panelId: 'inventory' });
  store.change({ type: 'inspection', value: { campaignId: 'td', rulesetId: 'td-rules', id: 'bravo',
    unavailableId: null, expanded: ['alpha', 'bravo'] } });
  store.saveView('map:floor', { zoom: 1.5, scrollLeft: 80, scrollTop: 40, coverInfo: true });
  return store.snapshot();
}

test('saved legacy windows return inline before loading publishes and survive a cold reopen without data loss', async () => {
  const bus = transport(), storage = new Map();
  const legacy = seedLegacy(storage);
  const main = renderer(bus, 'main', state(), storage);
  await main.host.init();
  const loading = main.host.loading('td', 'gm');
  main.render(loading); await flush();
  assertInline(main, bus);
  assert.deepEqual(Array.from(main.host.snapshot().hidden), ['inventory']);
  main.render({ ...state().panelProjection, revision: 9 }); await flush();
  await main.host.publish();
  const migrated = copy(main.host.snapshot());
  assertInline(main, bus);
  assert.deepEqual(migrated.views, legacy.views);
  assert.deepEqual(migrated.inspection, legacy.inspection);
  assert.equal(main.state.characterInspection.id, 'bravo');
  assert.equal(main.state.tacticalViews.map.zoom, 1.5);
  assert.equal(main.host.effectRevision(), 9, 'history establishes a baseline without replay');
  assert.equal(main.host.effects.snapshot().active.length, 0);
  assert.deepEqual(stored(storage), migrated);
  main.host.leave(); await flush();
  const reopenedBus = transport(), reopened = renderer(reopenedBus, 'main', state(), storage);
  await reopened.host.init(); reopened.render(); await flush();
  assertInline(reopened, reopenedBus);
  assert.deepEqual(copy(reopened.host.snapshot()), migrated);
  reopened.host.leave();
});

test('independent inline viewer profiles keep inspection, arrangement and map views on reopen', async () => {
  const projection = state().panelProjection, original = JSON.stringify(projection);
  for (const [index, inspected] of ['alpha', 'bravo'].entries()) {
    const bus = transport(), storage = new Map(), current = state();
    current.panelProjection = projection;
    const main = renderer(bus, 'main', current, storage);
    await main.host.init(); main.render(); await flush();
    main.state.characterInspection = { campaignId: 'td', rulesetId: 'td-rules', id: inspected,
      unavailableId: null, expanded: [inspected] };
    if (index === 0) main.host.changed({ type: 'hide', panelId: 'inventory' });
    main.host.saveView(projection.surfaces[0], { zoom: 1.5 + index, scrollLeft: 80, scrollTop: 40 });
    await main.host.publish();
    const saved = copy(main.host.snapshot()); main.host.leave(); await flush();
    const reopenedBus = transport(), reopened = renderer(reopenedBus, 'main', state(), storage);
    await reopened.host.init(); reopened.render(); await flush();
    assertInline(reopened, reopenedBus);
    assert.deepEqual(copy(reopened.host.snapshot()), saved);
    assert.equal(reopened.state.characterInspection.id, inspected);
    assert.equal(reopened.host.snapshot().hidden.includes('inventory'), index === 0);
    reopened.host.leave();
  }
  assert.equal(JSON.stringify(projection), original, 'viewers do not mutate their shared projection');
});

test('reveal restores hidden inline panels and selects background tabs in place, with durable focus', async () => {
  const bus = transport(), storage = new Map(), main = renderer(bus, 'main', state(), storage);
  await main.host.init(); main.render();
  main.host.changed({ type: 'hide', panelId: 'inventory' });
  assert.equal(main.host.snapshot().hidden.includes('inventory'), true);
  main.host.reveal('inventory'); await flush();
  assert.equal(main.host.snapshot().hidden.includes('inventory'), false);
  assert.equal(groupOf(main.host.snapshot().sidebar, 'inventory').active, 'inventory');
  main.host.changed({ type: 'move', panelId: 'inventory', destination: {
    windowId: 'sidebar', targetPanelId: 'party', position: 'tab' } });
  const group = groupOf(main.host.snapshot().sidebar, 'party');
  main.host.changed({ type: 'tab', groupId: group.id, panelId: 'party' });
  main.host.reveal('inventory'); await flush();
  assert.equal(groupOf(main.host.snapshot().sidebar, 'inventory').active, 'inventory');
  assert.equal(groupOf(stored(storage).sidebar, 'inventory').active, 'inventory');
  const focuses = bus.calls.filter(call => call.command === 'focus_panel_window');
  assert.equal(focuses.length, 2);
  assert.ok(focuses.every(call => call.args.windowId === 'sidebar'));
  assertInline(main, bus); assert.deepEqual(main.errors, []); main.host.leave();
});

test('inline tab grouping, edge splits, dimensions and hidden state persist through loading and reopen', async () => {
  const bus = transport(), storage = new Map(), main = renderer(bus, 'main', state(), storage);
  await main.host.init(); main.render();
  main.host.changed({ type: 'move', panelId: 'inventory', destination: {
    windowId: 'sidebar', targetPanelId: 'party', position: 'tab' } });
  main.host.changed({ type: 'move', panelId: 'map', destination: {
    windowId: 'sidebar', targetPanelId: 'party', position: 'right' } });
  // The edge split that now holds the map (the suite default nests it under the roster strip).
  const holding = (tree) => tree?.kind !== 'split' ? null
    : [tree.first, tree.second].some(child => child.kind === 'tabs' && child.panels.includes('map')) ? tree
      : holding(tree.first) || holding(tree.second);
  const split = holding(main.host.snapshot().sidebar);
  assert.equal(split.kind, 'split'); assert.equal(split.axis, 'row');
  main.host.changed({ type: 'ratio', nodeId: split.id, ratio: .65 });
  main.host.changed({ type: 'size', panelId: 'map', height: 620 });
  main.host.changed({ type: 'hide', panelId: 'inventory' });
  const saved = copy(main.host.snapshot());
  const savedSplit = (function find(tree) { return tree?.kind !== 'split' ? null : tree.id === split.id ? tree : find(tree.first) || find(tree.second); })(saved.sidebar);
  assert.equal(savedSplit.ratio, .65); assert.equal(saved.panel_sizes.map, 620);
  assertInline(main, bus); main.host.leave(); await flush();
  const reopenedBus = transport(), reopened = renderer(reopenedBus, 'main', state(), storage);
  await reopened.host.init(); reopened.render(reopened.host.loading('td', 'gm'));
  reopened.render(state().panelProjection); await flush();
  assert.deepEqual(copy(reopened.host.snapshot()), saved);
  assertInline(reopened, reopenedBus); reopened.host.leave();
});

test('saved and original layout preferences stay inline and preserve viewer state', async () => {
  const bus = transport(), storage = new Map(), main = renderer(bus, 'main', state(), storage);
  await main.host.init(); main.render();
  main.host.changed({ type: 'hide', panelId: 'inventory' });
  applyPreference(main.host, 'save');
  main.host.changed({ type: 'show', panelId: 'inventory' });
  main.host.saveView(main.state.panelProjection.surfaces[0], { zoom: 1.5, scrollLeft: 80, scrollTop: 40 });
  const views = copy(main.host.snapshot().views);
  applyPreference(main.host, 'mine');
  assert.equal(main.host.snapshot().hidden.includes('inventory'), true);
  applyPreference(main.host, 'original'); await flush();
  assert.equal(main.host.snapshot().hidden.includes('inventory'), false);
  assert.deepEqual(copy(main.host.snapshot().views), views);
  assertInline(main, bus); main.host.leave();
});

test('old window messages cannot mutate the inline workspace or dispatch an action after navigation', async () => {
  const bus = transport(), main = renderer(bus, 'main', state(), new Map());
  await main.host.init(); main.render(); await flush();
  const previous = bus.calls.filter(call => call.command === 'publish_panel_workspace').at(-1).args;
  main.host.leave(); main.state.activeSessionId = 'other-gm';
  main.state.activeCampaignId = 'other';
  main.render({ ...state().panelProjection, campaign_id: 'other' }); await flush();
  const before = JSON.stringify(main.host.snapshot());
  const current = bus.calls.filter(call => call.command === 'publish_panel_workspace').at(-1).args.context;
  for (const context of [previous.context, current]) bus.emit('main', 'panel-window-message', {
    context, window_id: 'retired-window', payload: { type: 'rpc', request_id: 'retired-action',
      method: 'execute_panel_action', args: { panelId: 'map' } } });
  bus.emit('main', 'panel-window-closed', { context: previous.context, window_id: 'retired-window' });
  await flush();
  assert.equal(JSON.stringify(main.host.snapshot()), before);
  assertInline(main, bus); main.host.leave();
});

test('dragging within the workspace reorders inline; an outside or cancelled release changes nothing', async () => {
  const bus = transport(), main = renderer(bus, 'main', state(), new Map());
  await main.host.init(); main.render();
  const unmoved = panels(main.host.snapshot().sidebar).filter(id => id !== 'map');
  const drag = dragHarness('map', 'sidebar'); main.host.wire(drag.container);
  drag.drag(); drag.drop(); drag.end(); await flush();
  assert.deepEqual(panels(main.host.snapshot().sidebar), [...unmoved, 'map']);
  const before = JSON.stringify(main.host.snapshot());
  for (const cancelled of [false, true]) {
    const outside = dragHarness('party', 'new'); main.host.wire(outside.container);
    outside.drag(); if (cancelled) outside.dataTransfer.dropEffect = 'none';
    outside.end(); await flush();
    assert.equal(JSON.stringify(main.host.snapshot()), before);
    assert.equal(outside.classes.size, 0); assert.equal(main.previews.at(-1).removed, true);
  }
  assert.equal(bus.calls.some(call => ['begin_panel_drag', 'finish_panel_drag'].includes(call.command)), false);
  assertInline(main, bus); main.host.leave();
});

test('inline shot playback starts after replacement paint without waiting for a detached renderer', async () => {
  const frames = [], clock = { now: 1000 }, bus = transport();
  const main = renderer(bus, 'main', state(), new Map(), null, {
    clock, renderCost: 1800, requestAnimationFrame: callback => frames.push(callback) });
  await main.host.init(); main.render();
  while (frames.length) frames.shift()(); await flush();
  const record = burstRecord();
  record.shots.forEach(shot => { shot.delay_ms += 10000; });
  main.render({ ...main.state.panelProjection, revision: 3, shot_effects: [record] });
  assert.equal(main.host.effects.snapshot().active.length, 0, 'replacement has not painted');
  while (frames.length) frames.shift()(); await flush();
  assert.equal(main.host.effects.snapshot().active.length, 5);
  assert.equal(main.host.effects.snapshot().active[0].start_ms, clock.now + 10000);
  const start = main.host.effects.snapshot().active[0].start_ms;
  main.host.changed({ type: 'move', panelId: 'party', destination: {
    windowId: 'sidebar', targetPanelId: 'map', position: 'tab' } });
  while (frames.length) frames.shift()(); await flush();
  assert.equal(main.host.effects.snapshot().active[0].start_ms, start, 'arrangement cannot replay the effect');
  await main.host.configureEffects({ visual: 'static', muted: true });
  assert.equal(main.host.effects.preferences().muted, true);
  await main.host.skipEffects(); assert.equal(main.host.effects.snapshot().active.length, 0);
  assertInline(main, bus); main.host.leave();
});

test('historical and empty projections advance inline effects without replaying a backlog', async () => {
  const bus = transport(), main = renderer(bus, 'main', state(), new Map());
  await main.host.init();
  main.render({ ...main.state.panelProjection, revision: 4, shot_effects: [burstRecord(3)] });
  assert.equal(main.host.effectRevision(), 4); assert.equal(main.host.effects.snapshot().active.length, 0);
  main.render({ ...main.state.panelProjection, revision: 5, shot_effects: [] });
  assert.equal(main.host.effectRevision(), 5); assert.equal(main.host.effects.snapshot().active.length, 0);
  main.render({ ...main.state.panelProjection, revision: 6, shot_effects: [burstRecord(6)] });
  assert.equal(main.host.effects.snapshot().active.length, 5);
  const start = main.host.effects.snapshot().active[0].start_ms;
  main.render({ ...main.state.panelProjection, presentation_key: 'queue:changed' });
  assert.equal(main.host.effects.snapshot().active[0].start_ms, start);
  assertInline(main, bus); main.host.leave();
});

test('returning to a campaign waits for native context registration before publishing', async () => {
  let blockReturn = false, releaseReturn;
  const returnGate = new Promise((resolve) => { releaseReturn = resolve; });
  const bus = transport({
    enforceRegisteredContext: true,
    beforeSync: async ({ context }) => {
      if (blockReturn && context) await returnGate;
    },
  });
  const main = renderer(bus, 'main', state(), new Map());
  await main.host.init();
  main.render();
  await flush();
  await main.host.publish();

  const projection = main.state.panelProjection;
  main.state.activeSessionRole = 'builder';
  main.render(null);
  await flush();
  blockReturn = true;
  main.state.activeSessionId = 'gm-returned';
  main.state.activeSessionRole = 'dm';
  main.render({ ...projection, revision: 3 });

  const outcome = main.host.publish().then(() => null, (error) => error);
  await flush();
  releaseReturn();
  assert.equal(await outcome, null,
    'publish must wait until sync_panel_windows registers the returned campaign context');
  assert.deepEqual(main.errors, []);
});

test('phase one keeps default window panels inline and refuses new floating windows without game actions', async () => {
  const bus = transport(), current = state();
  current.panelProjection.surfaces[2].default_placement = 'window';
  const main = renderer(bus, 'main', current, new Map());
  await main.host.init(); main.render(); await flush();
  assert.equal(main.host.snapshot().windows.length, 0, 'a window default is returned to the main workspace');
  assert.equal(bus.windows.size, 0);
  const before = JSON.stringify(main.host.snapshot());
  await main.host.changed({ type: 'move', panelId: 'party', destination: { windowId: 'new' } });
  await flush();
  assert.equal(JSON.stringify(main.host.snapshot()), before);
  assert.equal(bus.windows.size, 0);
  assert.match(main.errors.at(-1), /keeps panels in the main workspace/);
  assert.deepEqual(main.dispatches, []);
  assert.equal(main.html().includes('data-panel-workspace-destination="new"'), false);
});
