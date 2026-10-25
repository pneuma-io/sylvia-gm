// Exercise the real layout host across main and two panel renderers with a local event transport.
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
    crypto: webcrypto, Date: { now: () => clock.now },
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

test('independent viewer profiles retain their own inspection and layout while synchronizing their own child', async () => {
  const projection = state().panelProjection;
  const originalProjection = JSON.stringify(projection);
  const viewers = [];
  for (const inspected of ['alpha', 'bravo']) {
    const bus = transport(), storage = new Map(), viewerState = state();
    viewerState.panelProjection = projection;
    const main = renderer(bus, 'main', viewerState, storage);
    await main.host.init(); main.render(); await flush();
    main.host.changed({ type: 'move', panelId: 'party', destination: { windowId: 'new' } });
    await flush();
    const windowId = bus.windows.keys().next().value;
    const child = renderer(bus, windowId, state(), storage, bus.windows.get(windowId));
    await child.host.init();
    main.state.characterInspection = { campaignId: 'td', rulesetId: 'td-rules', id: inspected,
      unavailableId: null, expanded: [inspected] };
    await main.host.publish();
    viewers.push({ main, child, storage, inspected });
  }
  viewers[0].main.host.changed({ type: 'hide', panelId: 'inventory' });
  await viewers[0].main.host.publish();
  for (const [index, viewer] of viewers.entries()) {
    assert.equal(viewer.main.state.characterInspection.id, viewer.inspected);
    assert.equal(viewer.child.state.characterInspection.id, viewer.inspected);
    assert.equal(viewer.child.host.snapshot().hidden.includes('inventory'), index === 0);
    assert.equal(JSON.stringify(viewer.main.state.panelProjection), originalProjection);
    assert.equal(viewer.main.dispatches.length, 0);
    assert.equal(viewer.child.dispatches.length, 0);
    const reopened = renderer(transport(), 'main', state(), viewer.storage);
    reopened.render();
    assert.equal(reopened.state.characterInspection.id, viewer.inspected);
    assert.equal(reopened.host.snapshot().hidden.includes('inventory'), index === 0);
    reopened.host.leave(); viewer.child.host.leave(); viewer.main.host.leave();
  }
  assert.equal(JSON.stringify(projection), originalProjection, 'Presentation never mutates the shared projection');
});

test('inspection transport follows panel lifecycle and rejects an old presentation after campaign navigation',async()=>{
  const bus=transport(),storage=new Map(),main=renderer(bus,'main',state(),storage);
  await main.host.init();main.render();await flush();
  main.host.changed({type:'move',panelId:'party',destination:{windowId:'new'}});await flush();
  const windowId=bus.windows.keys().next().value,context=bus.windows.get(windowId);
  const child=renderer(bus,windowId,state(),storage,context);await child.host.init();
  main.state.characterInspection={campaignId:'td',rulesetId:'td-rules',id:'bravo',unavailableId:null,expanded:['alpha','bravo']};
  await main.host.publish();
  assert.deepEqual(JSON.parse(JSON.stringify(child.state.characterInspection)),main.state.characterInspection);
  const old=bus.calls.filter(c=>c.command==='publish_panel_workspace').at(-1).args;
  main.host.changed({type:'hide',panelId:'inventory'});
  main.host.changed({type:'show',panelId:'inventory'});await flush();
  assert.equal(child.state.characterInspection.id,'bravo');
  main.host.leave();main.state.activeCampaignId='different';main.state.activeSessionId='different-gm';
  main.render({...main.state.panelProjection,campaign_id:'different'});await flush();
  bus.emit(windowId,'panel-workspace',{context:old.context,window_id:windowId,payload:{...old.payload,presentation_sequence:0}});
  assert.equal(main.state.characterInspection,null);
  assert.equal(main.dispatches.length,0);assert.equal(child.dispatches.length,0);
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
  return { action_id: `child-shot-${revision}`, revision, shots, unavailable: false };
}

test('the app theme setting updates an already-open child through the owner transport', async () => {
  const bus = transport(), storage = new Map();
  const main = renderer(bus, 'main', state(), storage, null, { frontendTheme: true });
  await main.host.init(); main.render(); await flush();
  main.host.changed({ type: 'move', panelId: 'party', destination: { windowId: 'new' } });
  await flush();
  const [windowId, context] = Array.from(bus.windows.entries())[0];
  const child = renderer(bus, windowId, state(), storage, context);
  await child.host.init(); await flush();
  for (const theme of ['sylvia', 'sylvia-dark', 'slate']) {
    main.setTheme(theme);
    await flush();
    assert.equal(main.bodyTheme(), theme);
    assert.equal(child.bodyTheme(), theme, 'An open child must follow the app setting immediately');
    assert.equal(child.state.selectedTheme, theme);
  }
  assert.deepEqual(main.dispatches, []);
  assert.deepEqual(child.dispatches, []);
  assert.deepEqual([...main.errors, ...child.errors].filter(Boolean), []);
});

test('saved extra windows restore during loading without old state, then receive current panels', async () => {
  const bus = transport({ enforceRegisteredContext: true }), storage = new Map();
  const main = renderer(bus, 'main', state(), storage);
  await main.host.init(); main.render(); await flush();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  main.host.changed({ type: 'move', panelId: 'party', destination: { windowId: 'new' } });
  await flush();
  const saved = storage.get('sylvia-gm-panel-workspace-v1:campaign:td');
  const ready = main.state.panelProjection;
  main.host.leave(); await flush();
  main.render(main.host.loading('td', 'gm'));
  assert.equal(main.host.effectRevision(), null, 'opening must not request all old shot effects');
  await flush();
  assert.equal(bus.windows.size, 2);
  assert.equal(storage.get('sylvia-gm-panel-workspace-v1:campaign:td'), saved);
  const initial = bus.calls.filter((call) => call.command === 'publish_panel_workspace').at(-1).args;
  assert.equal(initial.payload.projection.loading, 'loading');
  assert.equal(initial.payload.projection.surfaces.some((surface) => surface.data), false);
  assert.equal(initial.payload.effects.active.length, 0);
  const windowId = bus.windows.keys().next().value;
  const child = renderer(bus, windowId, state(), storage, bus.windows.get(windowId));
  await child.host.init(); await main.host.publish();
  assert.equal(child.state.activeSnapshot, null);
  main.render({ ...ready, revision: 5, shot_effects: [burstRecord(4)] });
  await flush();
  assert.equal(child.state.activeSnapshot.revision, 5);
  assert.equal(child.state.panelProjection.loading, undefined);
  assert.equal(main.host.effects.snapshot().active.length, 0, 'old shots do not replay on opening');
  assert.equal(bus.windows.get(windowId).generation, initial.context.generation);
  assert.deepEqual(main.errors, []);
  assert.deepEqual(child.errors.filter(Boolean), []);
  main.host.leave(); await flush();
  assert.equal(bus.windows.size, 0);
});

test('a fresh app restores two saved frames without replaying old effects or accepting pre-restart messages', async () => {
  const storage = new Map(), oldBus = transport({ enforceRegisteredContext: true });
  const previous = renderer(oldBus, 'main', state(), storage);
  await previous.host.init(); previous.render(); await flush();
  const placement = { x: -1200, y: 100, width: 650, height: 700 };
  previous.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new', placement } });
  previous.host.changed({ type: 'move', panelId: 'party', destination: { windowId: 'new' } });
  await flush();
  previous.state.characterInspection = {
    campaignId: 'td', rulesetId: 'td-rules', id: 'bravo', unavailableId: null, expanded: ['alpha', 'bravo'],
  };
  previous.host.saveView(previous.state.panelProjection.surfaces[0], {
    zoom: 1.5, scrollLeft: 80, scrollTop: 40,
  });
  await previous.host.publish();
  const saved = JSON.parse(JSON.stringify(previous.host.snapshot()));
  const oldContext = oldBus.windows.get(saved.windows[0].id);
  const oldPacket = oldBus.calls.filter(call => call.command === 'publish_panel_workspace').at(-1).args;
  previous.host.leave(); await flush();
  assert.equal(oldBus.windows.size, 0);

  // A new renderer and native transport model process replacement, using only durable storage.
  const bus = transport({ enforceRegisteredContext: true });
  const current = renderer(bus, 'main', state(), storage);
  await current.host.init();
  current.render(current.host.loading('td', 'gm')); await flush();
  const loading = bus.calls.filter(call => call.command === 'publish_panel_workspace').at(-1).args;
  assert.equal(bus.windows.size, 2);
  assert.notEqual(loading.context.generation, oldContext.generation);
  assert.equal(loading.payload.projection.surfaces.some(surface => surface.data || surface.actions.length), false);
  assert.equal(loading.payload.effects.active.length, 0);
  const windowId = saved.windows[0].id;
  const child = renderer(bus, windowId, state(), storage, bus.windows.get(windowId));
  await child.host.init(); await flush();
  assert.equal(child.state.activeSnapshot, null);
  current.render({ ...state().panelProjection, revision: 9, shot_effects: [burstRecord(8)] });
  await flush();
  assert.deepEqual(JSON.parse(JSON.stringify(current.host.snapshot())), saved);
  assert.equal(child.state.activeSnapshot.revision, 9);
  assert.equal(child.state.characterInspection.id, 'bravo');
  assert.equal(child.state.tacticalViews.map.zoom, 1.5);
  assert.equal(current.host.effects.snapshot().active.length, 0, 'historical effects must not replay');

  const before = JSON.stringify(current.host.snapshot()), renders = child.renderCount();
  bus.emit('main', 'panel-window-closed', { context: oldContext, window_id: windowId });
  bus.emit('main', 'panel-window-message', { context: oldContext, window_id: windowId, payload: {
    type: 'rpc', request_id: 'before-restart', method: 'layout',
    args: { type: 'close-window', windowId },
  } });
  bus.emit(windowId, 'panel-workspace', { ...oldPacket, window_id: windowId });
  await flush();
  assert.equal(JSON.stringify(current.host.snapshot()), before);
  assert.equal(child.renderCount(), renders);
  assert.equal(current.dispatches.length, 0);
  assert.equal(current.selections.length, 0);
  assert.equal((await child.host.request('snapshot', {})).revision, 9);
  assert.equal(current.dispatches.length, 1, 'one new request receives one current reply');
  assert.deepEqual(current.errors, []);
  assert.deepEqual(child.errors.filter(Boolean), []);
  current.host.leave(); await flush();
  assert.equal(bus.windows.size, 0);
});

test('applying a saved Ruleset layout restores native window placement only on explicit reset', async () => {
  const bus = transport(), storage = new Map();
  const main = renderer(bus, 'main', state(), storage);
  await main.host.init();
  main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  await flush();
  const windowId = main.host.snapshot().windows[0].id;
  const context = bus.windows.get(windowId);
  const saved = { x: 1920, y: 120, width: 1200, height: 900 };
  bus.emit('main', 'panel-window-placement', { context, window_id: windowId, placement: saved });
  applyPreference(main.host, 'save');
  await flush();
  assert.equal(bus.calls.filter((call) => call.command === 'sync_panel_windows')
    .some((call) => call.args.restoreLayout), false);

  const moved = { x: 100, y: 200, width: 800, height: 600 };
  bus.emit('main', 'panel-window-placement', { context, window_id: windowId, placement: moved });
  main.render();
  await flush();
  assert.deepEqual({ ...main.host.snapshot().windows[0].placement }, moved);

  applyPreference(main.host, 'mine');
  await flush();
  const restore = bus.calls.filter((call) => call.command === 'sync_panel_windows').at(-1);
  assert.equal(restore.args.restoreLayout, true);
  assert.deepEqual({ ...restore.args.windows[0].placement }, saved);
  main.render();
  await flush();
  assert.equal(bus.calls.filter((call) => call.command === 'sync_panel_windows').at(-1)
    .args.restoreLayout, false);
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

test('drop destinations detach a panel and return it to an empty sidebar', async () => {
  const bus = transport({ dropPlacement: { x: 1700, y: 200, width: 500, height: 500 } }), storage = new Map();
  const main = renderer(bus, 'main', state(), storage);
  await main.host.init();
  main.render();

  const detach = dragHarness('map', 'new');
  main.host.wire(detach.container);
  detach.drag();
  assert.equal(detach.classes.has('panel-workspace-dragging'), true);
  detach.drop();
  detach.end();
  assert.equal(detach.classes.has('panel-workspace-dragging'), false);
  await flush();
  const panelWindow = main.host.snapshot().windows[0];
  assert.ok(panelWindow);
  assert.deepEqual(Array.from(panelWindow.tree.panels), ['map']);
  main.host.changed({ type: 'move', panelId: 'party', destination: { windowId: panelWindow.id } });
  main.host.changed({ type: 'move', panelId: 'inventory', destination: { windowId: panelWindow.id } });
  assert.equal(main.host.snapshot().sidebar, null);
  await flush();

  const child = renderer(bus, panelWindow.id, state(), storage, bus.windows.get(panelWindow.id));
  await child.host.init();
  await flush();
  const redock = dragHarness('map', 'sidebar');
  child.host.wire(redock.container);
  redock.drag();
  redock.drop();
  redock.end();
  await flush();
  assert.equal(main.host.snapshot().windows.length, 1);
  const panels = (tree) => tree.kind === 'tabs' ? tree.panels : [...panels(tree.first), ...panels(tree.second)];
  assert.deepEqual(Array.from(panels(main.host.snapshot().sidebar)), ['map']);
  assert.deepEqual(Array.from(panels(main.host.snapshot().windows[0].tree)).sort(), ['inventory', 'party']);
  assert.equal(bus.calls.filter((call) => call.command === 'finish_panel_drag')
    .every((call) => call.args.cancelled), true);
  assert.deepEqual(main.errors, []);
});

test('outside release detaches once with a panel preview, saved placement and no game action', async () => {
  const placement = { x: -1300, y: 250, width: 600, height: 630 };
  const bus = transport({ dropPlacement: placement }), storage = new Map();
  const main = renderer(bus, 'main', state(), storage);
  await main.host.init(); main.render(); await flush();
  const drag = dragHarness('party', 'new');
  main.host.wire(drag.container);
  drag.drag(); await flush();
  assert.match(drag.dataTransfer.image.element.innerHTML, /<article>Party<\/article>/);
  assert.equal(drag.dataTransfer.image.x, 32);
  drag.end(); drag.end(); await flush();
  const layout = main.host.snapshot();
  assert.equal(layout.windows.length, 1);
  assert.deepEqual(Array.from(layout.windows[0].tree.panels), ['party']);
  assert.deepEqual({ ...layout.windows[0].placement }, placement);
  assert.equal(bus.calls.filter((call) => call.command === 'finish_panel_drag').length, 1);
  assert.equal(drag.dataTransfer.image.element.removed, true);
  assert.deepEqual(main.dispatches, []);
  assert.deepEqual(main.errors, []);
  main.host.leave(); await flush();
  main.render(); await flush();
  assert.deepEqual({ ...main.host.snapshot().windows[0].placement }, placement);
});

test('an extra-window tab detaches through the main owner and failed creation redocks it', async () => {
  let failWindow = false, keptWindow;
  const bus = transport({ dropPlacement: { x: 1700, y: 200, width: 500, height: 500 },
    failWindow: (window) => failWindow && window.id !== keptWindow }), storage = new Map();
  const main = renderer(bus, 'main', state(), storage);
  await main.host.init(); main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  await flush();
  const oldId = main.host.snapshot().windows[0].id;
  const child = renderer(bus, oldId, state(), storage, bus.windows.get(oldId));
  await child.host.init(); await flush();
  const drag = dragHarness('map', 'new');
  child.host.wire(drag.container);
  drag.drag(); await flush(); drag.end(); await flush();
  const newWindow = main.host.snapshot().windows[0];
  assert.notEqual(newWindow.id, oldId);
  assert.deepEqual({ ...newWindow.placement }, { x: 1700, y: 200, width: 500, height: 500 });
  assert.equal(bus.windows.has(oldId), false);
  assert.equal(bus.windows.size, 1);
  assert.deepEqual(child.dispatches, []);

  keptWindow = newWindow.id;
  failWindow = true;
  main.host.changed({ type: 'move', panelId: 'party', destination: { windowId: 'new' } });
  await flush();
  assert.equal(main.host.snapshot().windows.length, 1);
  assert.equal(bus.windows.size, 1);
  assert.equal(main.host.snapshot().windows[0].id, keptWindow);
  assert.match(main.html(), /data-workspace-panel="party"/);
  assert.match(main.errors.at(-1), /returned to the sidebar/);
  assert.equal(JSON.parse(storage.get('sylvia-gm-panel-workspace-v1:campaign:td')).windows.length, 1);
});

test('cancelled or inside release preserves layout and an unavailable observer explains recovery', async () => {
  for (const unavailable of [false, true]) {
    const bus = transport({ beforeDrag: () => { if (unavailable) throw new Error('unavailable'); } });
    const main = renderer(bus, 'main', state(), new Map());
    await main.host.init(); main.render(); await flush();
    const before = JSON.stringify(main.host.snapshot());
    const drag = dragHarness('map', 'new');
    main.host.wire(drag.container);
    drag.drag(); await flush(); drag.end(); await flush();
    assert.equal(JSON.stringify(main.host.snapshot()), before);
    assert.equal(bus.windows.size, 0);
    assert.equal(drag.classes.size, 0);
    assert.equal(main.previews.at(-1).removed, true);
    if (unavailable) assert.match(main.errors.at(-1), /Try again or use Arrange panels/);
    else assert.deepEqual(main.errors, []);
  }
});

test('leaving during observer startup cancels the old gesture after startup completes', async () => {
  let ready;
  const bus = transport({ beforeDrag: () => new Promise((resolve) => { ready = resolve; }),
    dropPlacement: { x: 1700, y: 200, width: 500, height: 500 } });
  const main = renderer(bus, 'main', state(), new Map());
  await main.host.init(); main.render(); await flush();
  const drag = dragHarness('map', 'new');
  main.host.wire(drag.container);
  drag.drag(); await flush();
  main.host.leave();
  main.state.activeCampaignId = 'another';
  main.state.activeSessionId = 'another-gm';
  main.render({ ...main.state.panelProjection, campaign_id: 'another' });
  ready(); await flush();
  assert.equal(bus.windows.size, 0);
  assert.equal(main.host.snapshot().windows.length, 0);
  assert.equal(bus.calls.find((call) => call.command === 'finish_panel_drag').args.cancelled, true);
  assert.deepEqual(main.errors, []);
});

test('active shot timing survives window moves and settings share the main playback owner', async () => {
  const bus = transport(), storage = new Map();
  const main = renderer(bus, 'main', state(), storage);
  await main.host.init();
  main.render();
  assert.equal(main.host.effectRevision(), 2);
  const cue = { map_id: 'floor', origin: [1, 1, 1], destination: [4, 1, 1],
    delay_ms: 1000, miss: true, contacts: [], style: {
      color: '#FFFFFF', width: 2, travel_ms: 100, impact_ms: 100, fire_sound: null, cues: {},
    } };
  main.render({ ...main.state.panelProjection, revision: 3,
    shot_effects: [{ action_id: 'shot', revision: 3, shots: [cue], unavailable: false }] });
  const started = main.host.effects.snapshot().active[0].start_ms;
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  await flush();
  const window = main.host.snapshot().windows[0];
  const child = renderer(bus, window.id, state(), storage, bus.windows.get(window.id));
  await child.host.init();
  await flush();
  assert.equal(child.host.effects.snapshot().active[0].start_ms, started);
  main.render();
  await flush();
  assert.equal(main.host.effects.snapshot().active.length, 1);
  assert.equal(child.host.effects.snapshot().active[0].start_ms, started);
  await child.host.configureEffects({ visual: 'static', muted: true });
  await flush();
  assert.equal(main.host.effects.preferences().visual, 'static');
  assert.equal(child.host.effects.preferences().muted, true);
  assert.equal(main.dispatches.length, 0);
  await child.host.skipEffects();
  await flush();
  assert.equal(main.host.effects.snapshot().active.length, 0);
  assert.equal(child.host.effects.snapshot().active.length, 0);
  main.host.leave();
  main.render();
  await flush();
  assert.equal(main.host.effects.snapshot().active.length, 0);
  assert.equal(main.host.effectRevision(), 3);
  assert.deepEqual(main.errors, []);
  main.host.leave();
});

test('two windows share current selection and requests; navigation preserves their saved arrangement', async () => {
  const bus = transport(), storage = new Map();
  const main = renderer(bus, 'main', state(), storage);
  await main.host.init();
  main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  main.host.changed({ type: 'move', panelId: 'party', destination: { windowId: 'new' } });
  const [mapWindow, partyWindow] = main.host.snapshot().windows;
  main.host.changed({ type: 'move', panelId: 'inventory', destination: { windowId: partyWindow.id } });
  await flush();
  assert.equal(bus.windows.size, 2);
  const context = bus.windows.get(mapWindow.id);
  const map = renderer(bus, mapWindow.id, state(), storage, context);
  const party = renderer(bus, partyWindow.id, state(), storage, context);
  await map.host.init();
  await party.host.init();
  await flush();
  assert.match(map.html(), /<article>Map/);
  assert.doesNotMatch(map.html(), /<article>Party/);
  assert.match(party.html(), /<article>Party/);
  assert.match(party.html(), /<article>Inventory/);
  assert.doesNotMatch(main.html(), /<article>/);
  party.host.choose('party', { actor: 'bravo' });
  await flush();
  assert.equal(map.state.tacticalSelections.map.actor, 'bravo');
  assert.equal(main.dispatches.length, 0);
  const result = await map.host.request('snapshot', {});
  assert.equal(result.revision, 2);
  assert.deepEqual(main.dispatches.map((item) => item.campaign), ['td']);
  map.host.saveView(map.state.panelProjection.surfaces[0], { zoom: 1.5, scrollLeft: 80, scrollTop: 40 });
  await flush();
  main.state.panelWindowBusy = true;
  await main.host.publish();
  assert.equal(map.state.panelWindowBusy, true);
  main.host.leave();
  await flush();
  assert.equal(bus.windows.size, 0);
  const count = main.selections.length;
  bus.emit('main', 'panel-window-message', { context, window_id: partyWindow.id,
    payload: { type: 'rpc', request_id: 'stale', method: 'selection',
      args: { surfaceId: 'party', patch: { actor: 'stale' } } } });
  await flush();
  assert.equal(main.selections.length, count);
  main.render();
  await flush();
  assert.equal(bus.windows.size, 2);
  assert.notEqual(bus.windows.get(mapWindow.id).generation, context.generation);
  assert.equal(main.state.tacticalViews.map.zoom, 1.5);
  assert.equal(main.state.tacticalSelections.map, undefined);
  bus.emit('main', 'panel-window-closed', { context: bus.windows.get(partyWindow.id), window_id: partyWindow.id });
  await flush();
  assert.equal(bus.windows.size, 1);
  assert.match(main.html(), /<article>Party/);
  assert.match(main.html(), /<article>Inventory/);
  assert.deepEqual(main.errors, []);
  assert.equal(map.errors.every((message) => !message || message === 'Sylvia is working…'), true);
  assert.equal(party.errors.every((message) => !message || message === 'Sylvia is working…'), true);
});

test('fresh detached-window shots survive the child map replacement render', async () => {
  const bus = transport(), storage = new Map(), clock = { now: 1000 };
  const main = renderer(bus, 'main', state(), storage, null, { clock });
  await main.host.init();
  main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  await flush();
  const panelWindow = main.host.snapshot().windows[0];
  const child = renderer(bus, panelWindow.id, state(), storage, bus.windows.get(panelWindow.id), {
    clock, renderCost: 600,
  });
  await child.host.init();
  await flush();
  const rendersBefore = child.renderCount();
  main.render({ ...main.state.panelProjection, revision: 3,
    shot_effects: [burstRecord()] });
  await flush();

  assert.equal(child.host.effects.snapshot().active.length, 5,
    'receiving the timeline before a slow replacement render must not expire every child cue');
  assert.equal(child.renderCount(), rendersBefore + 1,
    'the prepared projection renders once and the started clock arrives effects-only');
});

test('child waits for two paint frames and rejects stale readiness', async () => {
  const bus = transport(), storage = new Map(), frames = [];
  const main = renderer(bus, 'main', state(), storage);
  await main.host.init();
  main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  await flush();
  const panelWindow = main.host.snapshot().windows[0];
  const context = bus.windows.get(panelWindow.id);
  const child = renderer(bus, panelWindow.id, state(), storage, context, {
    requestAnimationFrame: (callback) => { frames.push(callback); return frames.length; },
  });
  await child.host.init();
  await flush();

  main.render({ ...main.state.panelProjection, revision: 3, shot_effects: [burstRecord()] });
  await flush();
  assert.equal(main.host.effects.snapshot().active.length, 0);
  const prepare = bus.calls.filter((call) => call.args?.payload?.effects_prepare).at(-1)
    .args.payload.effects_prepare;
  bus.emit('main', 'panel-window-message', { context: { ...context, generation: 'stale' },
    window_id: panelWindow.id, payload: { type: 'effects-ready', ...prepare } });
  bus.emit('main', 'panel-window-message', { context, window_id: panelWindow.id,
    payload: { type: 'effects-ready', ...prepare, playback_sequence: prepare.playback_sequence - 1 } });
  assert.equal(main.host.effects.snapshot().active.length, 0);

  frames.splice(0).forEach((callback) => callback());
  assert.equal(main.host.effects.snapshot().active.length, 0);
  frames.splice(0).forEach((callback) => callback());
  await flush();
  assert.equal(main.host.effects.snapshot().active.length, 5);
  assert.equal(child.host.effects.snapshot().active.length, 5);
});

test('equivalent workspace snapshots skip rendering while real presentation changes render', async () => {
  const bus = transport(), storage = new Map();
  const main = renderer(bus, 'main', state(), storage);
  await main.host.init();
  main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  await flush();
  const panelWindow = main.host.snapshot().windows[0];
  const child = renderer(bus, panelWindow.id, state(), storage, bus.windows.get(panelWindow.id));
  await child.host.init();
  await flush();

  main.render({ ...main.state.panelProjection, revision: 3, shot_effects: [burstRecord()] });
  await flush();
  const staleWorkspace = bus.calls.filter((call) => call.args?.payload?.effects_prepare).at(-1).args;
  let renders = child.renderCount();
  bus.emit(panelWindow.id, 'panel-workspace', {
    context: staleWorkspace.context, window_id: panelWindow.id, payload: staleWorkspace.payload,
  });
  assert.equal(child.renderCount(), renders);
  assert.equal(child.host.effects.snapshot().active.length, 5);
  await main.host.publish();
  assert.equal(child.renderCount(), renders);
  assert.equal(child.host.effects.snapshot().active.length, 5);

  main.state.panelWindowBusy = true;
  await main.host.publish();
  assert.equal(child.renderCount(), ++renders);
  main.state.tacticalSelections = { map: { actor: 'bravo' } };
  await main.host.publish();
  assert.equal(child.renderCount(), ++renders);
  main.host.configureEffects({ muted: true });
  await flush();
  assert.equal(child.renderCount(), ++renders);
  assert.equal(child.host.effects.preferences().muted, true);
  main.host.changed({ type: 'move', panelId: 'party', destination: { windowId: panelWindow.id } });
  await flush();
  assert.equal(child.renderCount(), ++renders);
});

test('closing an unready child recomputes ownership and starts the pending timeline', async () => {
  const bus = transport(), storage = new Map(), frames = [];
  const main = renderer(bus, 'main', state(), storage);
  await main.host.init();
  main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  await flush();
  const panelWindow = main.host.snapshot().windows[0];
  const context = bus.windows.get(panelWindow.id);
  const child = renderer(bus, panelWindow.id, state(), storage, context, {
    requestAnimationFrame: (callback) => { frames.push(callback); return frames.length; },
  });
  await child.host.init();
  await flush();
  main.render({ ...main.state.panelProjection, revision: 3, shot_effects: [burstRecord()] });
  await flush();
  assert.equal(main.host.effects.snapshot().active.length, 0);

  bus.emit('main', 'panel-window-closed', { context, window_id: panelWindow.id });
  await flush();
  assert.equal(main.host.effects.snapshot().active.length, 5);
  assert.equal(main.host.snapshot().windows.length, 0);
});

test('historical records and empty fresh projections advance without waiting for a child', async () => {
  const bus = transport(), storage = new Map(), frames = [], timers = [];
  let holdFrames = false;
  const schedule = (callback, milliseconds) => {
    const timer = { callback, milliseconds, active: true };
    timers.push(timer);
    return timer;
  };
  const main = renderer(bus, 'main', state(), storage, null,
    { setTimeout: schedule, clearTimeout: (timer) => { timer.active = false; } });
  await main.host.init();
  main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  await flush();
  const panelWindow = main.host.snapshot().windows[0];
  const child = renderer(bus, panelWindow.id, state(), storage, bus.windows.get(panelWindow.id), {
    requestAnimationFrame: (callback) => {
      if (holdFrames) frames.push(callback);
      else callback();
      return frames.length;
    },
  });
  await child.host.init();
  await flush();
  main.render({ ...main.state.panelProjection, revision: 3, shot_effects: [burstRecord()] });
  await flush();
  assert.equal(main.host.effectRevision(), 3);

  holdFrames = true;
  main.render(main.state.panelProjection);
  await flush();
  assert.equal(timers.some((timer) => timer.active && timer.milliseconds === 2000), false);
  main.render({ ...main.state.panelProjection, revision: 4, shot_effects: [] });
  await flush();
  assert.equal(main.host.effectRevision(), 4);
  assert.equal(timers.some((timer) => timer.active && timer.milliseconds === 2000), false);
});

test('an inactive tactical tab does not delay playback waiting for an absent map DOM', async () => {
  const bus = transport(), storage = new Map(), frames = [], timers = [];
  const schedule = (callback, milliseconds) => {
    const timer = { callback, milliseconds, active: true };
    timers.push(timer);
    return timer;
  };
  const main = renderer(bus, 'main', state(), storage, null,
    { setTimeout: schedule, clearTimeout: (timer) => { timer.active = false; } });
  await main.host.init();
  main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  const panelWindow = main.host.snapshot().windows[0];
  main.host.changed({ type: 'move', panelId: 'party', destination: {
    windowId: panelWindow.id, targetPanelId: 'map', position: 'tab',
  } });
  await flush();
  const group = main.host.snapshot().windows[0].tree;
  main.host.changed({ type: 'tab', groupId: group.id, panelId: 'party' });
  await flush();
  const child = renderer(bus, panelWindow.id, state(), storage, bus.windows.get(panelWindow.id), {
    requestAnimationFrame: (callback) => { frames.push(callback); return frames.length; },
  });
  await child.host.init();
  await flush();

  main.render({ ...main.state.panelProjection, revision: 3, shot_effects: [burstRecord()] });
  await flush();
  assert.equal(main.host.effects.snapshot().active.length, 5);
  assert.equal(timers.some((timer) => timer.active && timer.milliseconds === 2000), false);
});

test('an unready child cannot hold playback forever or accept the timed-out clock late', async () => {
  const bus = transport(), storage = new Map(), frames = [], timers = [], clock = { now: 1000 };
  const schedule = (callback, milliseconds) => {
    const timer = { callback, milliseconds, active: true };
    timers.push(timer);
    return timer;
  };
  const cancel = (timer) => { timer.active = false; };
  const main = renderer(bus, 'main', state(), storage, null,
    { clock, setTimeout: schedule, clearTimeout: cancel });
  await main.host.init();
  main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  await flush();
  const panelWindow = main.host.snapshot().windows[0];
  const child = renderer(bus, panelWindow.id, state(), storage, bus.windows.get(panelWindow.id), {
    clock,
    requestAnimationFrame: (callback) => { frames.push(callback); return frames.length; },
  });
  await child.host.init();
  await flush();
  main.render({ ...main.state.panelProjection, revision: 3, shot_effects: [burstRecord()] });
  await flush();

  const deadline = timers.find((timer) => timer.active && timer.milliseconds === 2000);
  assert.ok(deadline);
  deadline.callback();
  assert.equal(main.host.effects.snapshot().active.length, 5);
  assert.equal(child.host.effects.snapshot().active.length, 0);
  assert.match(main.errors.at(-1), /not ready for shot effects/);
  frames.splice(0).forEach((callback) => callback());
  frames.splice(0).forEach((callback) => callback());
  await flush();
  assert.equal(child.host.effects.snapshot().active.length, 0);
  const ownerStart = main.host.effects.snapshot().active[0].start_ms;
  clock.now += 50;
  await main.host.publish();
  assert.equal(child.host.effects.snapshot().active.length, 5);
  assert.equal(child.host.effects.snapshot().active[0].start_ms, ownerStart,
    'a late ready child may join only the existing owner clock without rebasing it');
});

test('the readiness deadline is armed before native publication settles', async () => {
  const bus = transport(), storage = new Map(), timers = [];
  const schedule = (callback, milliseconds) => {
    const timer = { callback, milliseconds, active: true };
    timers.push(timer);
    return timer;
  };
  const main = renderer(bus, 'main', state(), storage, null,
    { setTimeout: schedule, clearTimeout: (timer) => { timer.active = false; } });
  await main.host.init();
  main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  await flush();
  const panelWindow = main.host.snapshot().windows[0];
  const child = renderer(bus, panelWindow.id, state(), storage, bus.windows.get(panelWindow.id));
  await child.host.init();
  await flush();
  bus.stallPublishes();
  main.render({ ...main.state.panelProjection, revision: 3, shot_effects: [burstRecord()] });
  await flush();

  const deadline = timers.find((timer) => timer.active && timer.milliseconds === 2000);
  assert.ok(deadline);
  deadline.callback();
  assert.equal(main.host.effects.snapshot().active.length, 5);
});

test('a child that missed prepare joins a later current clock after rendering without rebasing', async () => {
  const bus = transport(), storage = new Map(), timers = [], clock = { now: 1000 };
  const schedule = (callback, milliseconds) => {
    const timer = { callback, milliseconds, active: true };
    timers.push(timer);
    return timer;
  };
  const main = renderer(bus, 'main', state(), storage, null, {
    clock, setTimeout: schedule, clearTimeout: (timer) => { timer.active = false; },
  });
  await main.host.init();
  main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  await flush();
  const panelWindow = main.host.snapshot().windows[0];
  const child = renderer(bus, panelWindow.id, state(), storage, bus.windows.get(panelWindow.id), {
    clock, renderCost: 100,
  });
  await child.host.init();
  await flush();
  bus.stallPublishes();
  main.render({ ...main.state.panelProjection, revision: 3, shot_effects: [burstRecord()] });
  await flush();
  timers.find((timer) => timer.active && timer.milliseconds === 2000).callback();
  const ownerStart = main.host.effects.snapshot().active[0].start_ms;
  assert.equal(child.host.effects.snapshot().active.length, 0);

  bus.resumePublishes();
  clock.now += 50;
  await main.host.publish();
  assert.equal(child.host.effects.snapshot().active.length, 5);
  assert.equal(child.host.effects.snapshot().active[0].start_ms, ownerStart);
});

test('duplicate same-sequence preparation does not cancel the child paint acknowledgement', async () => {
  const bus = transport(), storage = new Map(), frames = [], timers = [];
  const main = renderer(bus, 'main', state(), storage, null, {
    setTimeout: (callback, milliseconds) => {
      const timer = { callback, milliseconds, active: true };
      timers.push(timer);
      return timer;
    },
    clearTimeout: (timer) => { timer.active = false; },
  });
  await main.host.init();
  main.render();
  main.host.changed({ type: 'move', panelId: 'map', destination: { windowId: 'new' } });
  await flush();
  const panelWindow = main.host.snapshot().windows[0];
  const context = bus.windows.get(panelWindow.id);
  const child = renderer(bus, panelWindow.id, state(), storage, context, {
    requestAnimationFrame: (callback) => { frames.push(callback); return frames.length; },
  });
  await child.host.init();
  await flush();

  main.render({ ...main.state.panelProjection, revision: 3, shot_effects: [burstRecord()] });
  await flush();
  const preparedCall = bus.calls.filter((call) => call.args?.payload?.effects_prepare).at(-1);
  assert.ok(preparedCall);
  assert.equal(main.host.effects.snapshot().active.length, 0);

  const firstPaint = frames.shift();
  firstPaint();
  bus.emit(panelWindow.id, 'panel-workspace', {
    context: preparedCall.args.context,
    window_id: panelWindow.id,
    payload: preparedCall.args.payload,
  });
  const secondPaint = frames.shift();
  secondPaint();
  await flush();

  assert.equal(main.host.effects.snapshot().active.length, 5,
    'an equivalent prepare packet must not invalidate paint readiness already in progress');
  assert.equal(timers.some((timer) => timer.active && timer.milliseconds === 2000), false);
});


test('queue-only presentation keys update an open child at the same mechanical revision',async()=>{
 const bus=transport(),storage=new Map(),main=renderer(bus,'main',state(),storage);await main.host.init();main.render();main.host.changed({type:'move',panelId:'map',destination:{windowId:'new'}});await flush();
 const popped=main.host.snapshot().windows[0],child=renderer(bus,popped.id,state(),storage,bus.windows.get(popped.id));await child.host.init();await flush();const revision=main.state.panelProjection.revision;let count=child.renderCount();
 for(const key of ['queue:one:1:ready','queue:one:2:ready','queue:one:2:busy','queue:one:2:paused']){
  main.state.panelProjection={...main.state.panelProjection,presentation_key:key};await main.host.publish();assert.equal(child.renderCount(),++count);assert.equal(child.state.panelProjection.revision,revision);
 }
 main.host.leave();await flush();
});
