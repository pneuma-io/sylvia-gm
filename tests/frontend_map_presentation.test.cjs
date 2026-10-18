const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

function element() {
  return {
    innerHTML: '', dataset: {}, hidden: false, style: { setProperty(name, value) { this[name] = value; } },
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, removeEventListener() {}, querySelectorAll() { return []; },
    querySelector() { return null; }, setAttribute() {}, appendChild() {},
  };
}

function harness(options = {}) {
  let time = 1000;
  const frames = [], overlays = [];
  const nodes = new Map();
  let activeElement = null, effectDom = null, effectGeneration = 0;
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  if (options.effectControls) {
    const panels = element();
    let html = '';
    const control = (name) => {
      const item = element();
      const listeners = new Map();
      item.hasAttribute = (attribute) => attribute === `data-shot-${name}`;
      item.addEventListener = (type, handler) => { listeners.set(type, handler); };
      item.focus = (options) => { activeElement = item; item.focusOptions = options; };
      item.change = () => listeners.get('change')?.();
      return item;
    };
    const rebuild = () => {
      if (effectDom?.details.contains(activeElement)) activeElement = null;
      if (!html.includes('class="shot-controls"')) {
        effectDom = null;
        return;
      }
      const panelId = html.match(/data-panel-id="([^"]+)"/)?.[1] || 'map';
      const controls = {
        visual: control('visual'), volume: control('volume'),
        muted: control('muted'), skip: control('skip'),
      };
      const details = { ...element(), open: false,
        contains: (candidate) => Object.values(controls).includes(candidate) };
      const scroll = { ...element(), scrollLeft: 0, scrollTop: 0,
        scrollWidth: 128, scrollHeight: 128, clientWidth: 128, clientHeight: 128,
        hasPointerCapture: () => false, releasePointerCapture() {}, setPointerCapture() {} };
      const panel = { ...element(), dataset: { panelId } };
      details.closest = () => panel;
      panel.querySelector = (selector) => ({
        '.shot-controls': details,
        '.tactical-scroll': scroll,
        '[data-shot-visual]': controls.visual,
        '[data-shot-volume]': controls.volume,
        '[data-shot-muted]': controls.muted,
        '[data-shot-skip]': controls.skip,
      })[selector] || null;
      panels.querySelectorAll = (selector) => ({
        '.shot-controls': [details],
        '[data-panel-id]': [panel],
        '[data-shot-visual], [data-shot-volume], [data-shot-muted]':
          [controls.visual, controls.volume, controls.muted],
        '[data-shot-skip]': [controls.skip],
      })[selector] || [];
      panels.querySelector = (selector) =>
        selector.startsWith('[data-panel-id=') ? panel : null;
      effectDom = { generation: ++effectGeneration, details, controls, panel };
    };
    Object.defineProperty(panels, 'innerHTML', {
      get: () => html,
      set: (value) => { html = value; rebuild(); },
    });
    nodes.set('ruleset-panels', panels);
  }
  if (Object.hasOwn(options, 'panelRenderMs')) {
    const panels = element();
    let html = '';
    Object.defineProperty(panels, 'innerHTML', {
      get: () => html,
      set: (value) => { html = value; time += options.panelRenderMs; },
    });
    nodes.set('ruleset-panels', panels);
  }
  if (Object.hasOwn(options, 'panelLayoutMs')) {
    const scroll = { ...element(), scrollLeft: 0, scrollTop: 0,
      clientWidth: 128, clientHeight: 128, scrollHeight: 128 };
    Object.defineProperty(scroll, 'scrollWidth', {
      get() { time += options.panelLayoutMs; return 128; },
    });
    const panel = { ...element(), dataset: { panelId: 'map' },
      querySelector: (selector) => selector === '.tactical-scroll' ? scroll : null };
    node('ruleset-panels').querySelectorAll = (selector) =>
      selector === '[data-panel-id]' ? [panel] : [];
  }
  const context = {
    window: options.childContext ? { __sylviaPanelContext: options.childContext } : {},
    crypto: webcrypto, console, URL, performance,
    Date: class extends Date { static now() { return time; } },
    document: { body: element(), getElementById: node, addEventListener() {}, createElement: element,
      get activeElement() { return activeElement; },
      querySelectorAll(selector) { return selector === '[data-shot-overlay]' ? overlays : []; } },
    localStorage: { getItem: () => null, setItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  };
  let source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const insertion = source.lastIndexOf('  return {');
  source = source.slice(0, insertion) + `
    window.mapPresentationTest = { state, renderTacticalGridPanel, renderRulesetPanels,
      renderRulesetPanelProjection, gamePanels };
  ` + source.slice(insertion);
  source = ['panel-workspace.js', 'shot-playback.js', 'panel-host.js'].map((name) =>
    fs.readFileSync(path.join(__dirname, '../src-tauri/frontend', name), 'utf8')).join('\n') + '\n' + source;
  context.setTimeout ||= setTimeout;
  context.clearTimeout ||= clearTimeout;
  context.window.innerWidth = 1400;
  context.window.requestAnimationFrame = (callback) => { frames.push(callback); return frames.length; };
  vm.runInNewContext(source, context);
  context.window.mapPresentationTest.state.bridge ||= {};
  return { ...context.window.mapPresentationTest, node, overlays,
    effectDom: () => effectDom, activeElement: () => activeElement,
    advance(milliseconds) { time += milliseconds; frames.splice(0).forEach((callback) => callback()); },
    paint() { this.advance(16); this.advance(16); } };
}

function surface(id = 'map') {
  return {
    id, renderer: 'tactical_grid', data: {
      map_revision: 4,
      map: { id: 'floor', bounds: { min: [0, 0, 0], max: [4, 4, 3] }, grid: { cell_size: 1, unit: 'm' } },
      selection: {}, terrain: [], zones: [], objectives: [], entities: [],
      geometry: [
        { id: 'pillar', volume: { kind: 'box', min: [0.25, 1.25, 0], max: [0.75, 2.75, 1.25] },
          active: true, movement: 'blocking', visibility: 'opaque', occupies_cells: true,
          display: { label: 'Stone pillar', kind: 'cover', height_label: 'chest-high' },
          private_note: 'hidden encounter detail' },
        { id: 'fence', volume: { kind: 'box', min: [2, 0, 0], max: [2.08, 4, 2] },
          active: true, movement: 'blocking', visibility: 'clear', occupies_cells: false,
          display: { label: 'Wire fence', kind: 'wall' } },
      ],
    }, actions: [],
  };
}

test('prepared actions offer the authored zone label and retain only current map zones', () => {
  const api = harness();
  const map = surface();
  map.actions = [{ id: 'watch', label: 'Watch', bindings: { actor: 'actor', zone_id: 'zone' } }];
  map.data.zones = [{ id: 'harbor-approach', shape: { kind: 'box', min: [0, 0, 0], max: [1, 1, 2] },
    display: { label: 'Harbor <approach>' } }];
  api.state.panelProjection = { surfaces: [map] };
  api.state.tacticalSelections.map = { zone: 'harbor-approach', map_revision: 4 };
  assert.match(api.renderTacticalGridPanel(map.data, map.id), /Harbor &lt;approach&gt;/);
  assert.equal(api.state.tacticalSelections.map.zone, 'harbor-approach');
  map.data.map_revision = 5;
  map.data.zones = [];
  api.renderTacticalGridPanel(map.data, map.id);
  assert.equal(api.state.tacticalSelections.map.zone, null);
});

test('Effects disclosure and focused controls survive same-session replacement renders', () => {
  const api = harness({ effectControls: true });
  const projection = { campaign_id: 'td', ruleset_id: 'td', revision: 2,
    surfaces: [surface()], shot_effects: [] };
  Object.assign(api.state, {
    activeCampaignId: 'td', activeSessionId: 'gm', activeSessionRole: 'dm',
  });
  api.renderRulesetPanelProjection(projection);

  let current = api.effectDom();
  current.details.open = true;
  current.controls.volume.value = '65';
  current.controls.volume.focus();
  current.controls.volume.change();

  current = api.effectDom();
  assert.equal(current.details.open, true);
  assert.equal(api.activeElement(), current.controls.volume);
  assert.equal(current.controls.volume.focusOptions.preventScroll, true);
  assert.equal(api.gamePanels().effects.preferences().volume, 0.65);

  current.controls.muted.checked = true;
  current.controls.muted.focus();
  current.controls.muted.change();
  current = api.effectDom();
  assert.equal(current.details.open, true);
  assert.equal(api.activeElement(), current.controls.muted);
  assert.equal(api.gamePanels().effects.preferences().muted, true);

  current.controls.visual.value = 'off';
  current.controls.visual.focus();
  current.controls.visual.change();
  current = api.effectDom();
  assert.equal(current.details.open, true);
  assert.equal(api.activeElement(), current.controls.visual);
  assert.equal(api.gamePanels().effects.preferences().visual, 'off');

  api.renderRulesetPanelProjection({ ...projection, revision: 3 });
  current = api.effectDom();
  assert.equal(current.details.open, true);
  assert.equal(api.activeElement(), current.controls.visual);
  assert.ok(current.controls.skip);
});

test('Effects disclosure and focus do not cross session or panel identity', () => {
  const api = harness({ effectControls: true });
  const projection = { campaign_id: 'td', ruleset_id: 'td', revision: 2,
    surfaces: [surface()], shot_effects: [] };
  Object.assign(api.state, {
    activeCampaignId: 'td', activeSessionId: 'gm', activeSessionRole: 'dm',
  });
  api.renderRulesetPanelProjection(projection);
  api.effectDom().details.open = true;
  api.effectDom().controls.volume.focus();

  api.state.activeSessionId = 'other';
  api.renderRulesetPanelProjection(projection);
  assert.equal(api.effectDom().details.open, false);
  assert.equal(api.activeElement(), null);

  api.effectDom().details.open = true;
  api.effectDom().controls.muted.focus();
  api.renderRulesetPanelProjection({ ...projection, surfaces: [surface('other-map')] });
  assert.equal(api.effectDom().details.open, false);
  assert.equal(api.activeElement(), null);
});

test('selected actors show public posture and height beside their names', () => {
  const api = harness();
  const map = surface();
  map.data.entities = [{ id: 'alpha', position: [0.5, 0.5, 0], height: 1.15,
    display: { label: 'Asha', stance: 'crouched' }, private_note: 'hidden body detail' }];
  api.state.panelProjection = { player_control: null, surfaces: [map] };
  api.state.tacticalSelections.map = { map_revision: map.data.map_revision, actor: 'alpha' };
  const html = api.renderTacticalGridPanel(map.data, map.id);
  assert.match(html, /Actor: Asha \(crouched, 1\.15 m high\)/);
  assert.match(html, /title="Asha, crouched, 1\.15 m high"/);
  assert.doesNotMatch(html, /hidden body detail|private_note/);
});

test('geometry remains visible and distinguishes occupied cells from edge barriers', () => {
  const api = harness();
  const map = surface();
  api.state.panelProjection = { player_control: null, surfaces: [map] };
  const html = api.renderTacticalGridPanel(map.data, map.id);
  assert.match(html, /aria-label="Stone pillar[^\"]*occupies grid cells"/);
  assert.match(html, /class="tactical-geometry tactical-cover occupies-cells/);
  assert.match(html, /class="tactical-occupied-cells" aria-hidden="true" style="left:0px;top:32px;width:32px;height:64px"/);
  assert.match(html, /data-tactical-geometry="pillar" style="left:8px;top:40px;width:16px;height:48px"/);
  assert.match(html, /aria-label="Wire fence[^\"]*physical edge barrier"/);
  assert.match(html, /class="tactical-geometry tactical-wall edge-barrier/);
});

test('selection exposes exact safe height while the optional overlay only adds labels', () => {
  const api = harness();
  const map = surface();
  api.state.panelProjection = { player_control: null, surfaces: [map] };
  api.state.tacticalViews.map = {
    zoom: 1, scrollLeft: 0, scrollTop: 0, coverInfo: true, selectedGeometry: 'pillar',
  };
  const html = api.renderTacticalGridPanel(map.data, map.id);
  assert.match(html, /tactical-world show-cover-info/);
  assert.match(html, /Height: 1\.25 m \(chest-high\)/);
  assert.match(html, /Movement: blocking · Visibility: opaque/);
  assert.match(html, /tactical-height-indicator" aria-hidden="true">1\.25 m</);
  assert.match(html, /data-tactical-geometry="fence"/);
  assert.doesNotMatch(html, /hidden encounter detail|private_note|line of sight|hit chance/i);
});

test('retracted or destroyed geometry leaves no physical shape or occupied-cell shading', () => {
  const api = harness();
  const map = surface();
  map.data.geometry.forEach((item) => { item.active = false; });
  api.state.panelProjection = { player_control: null, surfaces: [map] };
  const html = api.renderTacticalGridPanel(map.data, map.id);
  assert.doesNotMatch(html, /data-tactical-geometry=|class="tactical-occupied-cells"/);
});

test('panel refresh requests only new shots and redraws them into replacement map DOM', async () => {
  const api = harness(), requests = [];
  const projection = { campaign_id: 'td', ruleset_id: 'td', revision: 2, surfaces: [surface()],
    shot_effects: [] };
  const cue = { map_id: 'floor', origin: [1, 1, 1], destination: [3, 1, 1],
    delay_ms: 100, miss: true, contacts: [], style: {
      color: '#FFFFFF', width: 2, travel_ms: 100, impact_ms: 100, fire_sound: null, cues: {},
    } };
  Object.assign(api.state, { activeCampaignId: 'td', activeSessionId: 'gm', activeSessionRole: 'dm',
    bridge: { campaign_panels: async (...args) => { requests.push(args); return { ...projection }; } } });
  try {
    await api.renderRulesetPanels('td');
    api.paint();
    assert.equal(api.gamePanels().effectRevision(), 2);
    projection.revision = 3;
    projection.shot_effects = [{ action_id: 'shot', revision: 3, shots: [cue], unavailable: false }];
    const overlay = () => ({ innerHTML: '', closest: () => ({ dataset: { panelId: 'map' } }) });
    api.overlays.push(overlay());
    await api.renderRulesetPanels('td');
    assert.deepEqual(requests.map((args) => args[1]), [null, 2]);
    assert.match(api.node('ruleset-panels').innerHTML, /data-shot-overlay/);
    assert.equal(api.gamePanels().effects.snapshot().active.length, 0);
    api.paint();
    assert.equal(api.gamePanels().effects.snapshot().active.length, 1);
    api.advance(150);
    assert.match(api.overlays[0].innerHTML, /<line/);
    const started = api.gamePanels().effects.snapshot().active[0].start_ms;
    api.overlays.splice(0, 1, overlay());
    await api.renderRulesetPanels('td', true);
    api.advance(5);
    assert.match(api.overlays[0].innerHTML, /<line/);
    assert.equal(api.gamePanels().effects.snapshot().active[0].start_ms, started);
    assert.equal(api.gamePanels().effects.snapshot().active.length, 1);
    assert.equal(requests[2][1], 3);
  } finally { api.gamePanels().leave(); }
});

test('new shot timelines begin after the replacement map DOM is ready', () => {
  const timing = { panelRenderMs: 0 };
  const api = harness(timing);
  const shot = (delay_ms) => ({
    map_id: 'floor', origin: [1, 1, 1], destination: [3, 1, 1], delay_ms,
    miss: true, contacts: [], style: {
      color: '#FFFFFF', width: 2, travel_ms: 90, impact_ms: 140,
      fire_sound: null, cues: {},
    },
  });
  const projection = {
    campaign_id: 'td', ruleset_id: 'td', revision: 3, surfaces: [surface()],
    shot_effects: [{
      action_id: 'burst', revision: 3, shots: [shot(0), shot(80), shot(160)],
      unavailable: false,
    }],
  };
  Object.assign(api.state, {
    activeCampaignId: 'td', activeSessionId: 'gm', activeSessionRole: 'dm',
  });
  api.renderRulesetPanelProjection({ ...projection, revision: 2, shot_effects: [] });
  timing.panelRenderMs = 250;

  api.renderRulesetPanelProjection(projection);

  assert.equal(api.gamePanels().effects.snapshot().active.length, 0);
  api.paint();
  assert.deepEqual(
    Array.from(api.gamePanels().effects.snapshot().active, (item) => item.start_ms),
    [1282, 1362, 1442],
  );
});

test('late map refreshes cannot replace a newer revision or a different session', async () => {
  const api = harness(), replies = [];
  const projection = (revision) => ({ campaign_id: 'td', ruleset_id: 'td', revision,
    surfaces: [surface()], shot_effects: [] });
  Object.assign(api.state, { activeCampaignId: 'td', activeSessionId: 'gm', activeSessionRole: 'dm',
    bridge: { campaign_panels: () => new Promise((resolve) => replies.push(resolve)) } });
  api.renderRulesetPanelProjection(projection(2));
  const older = api.renderRulesetPanels('td'), newer = api.renderRulesetPanels('td');
  replies[1](projection(4));
  await newer;
  replies[0](projection(3));
  await older;
  assert.equal(api.state.panelProjection.revision, 4);
  const previousSession = api.renderRulesetPanels('td');
  api.state.activeSessionId = 'builder';
  api.state.activeSessionRole = 'builder';
  api.renderRulesetPanelProjection(null);
  replies[2](projection(5));
  await previousSession;
  assert.equal(api.state.panelProjection, null);
  assert.equal(api.gamePanels().effectRevision(), null);
});

test('restoring map scroll and layout cannot consume a new burst before its first frame', () => {
  const timing = { panelLayoutMs: 0 };
  const api = harness(timing);
  const shot = (delay_ms) => ({
    map_id: 'floor', origin: [1, 1, 1], destination: [3, 1, 1], delay_ms,
    miss: true, contacts: [], style: {
      color: '#FFFFFF', width: 2, travel_ms: 90, impact_ms: 140,
      fire_sound: null, cues: {},
    },
  });
  const projection = { campaign_id: 'td', ruleset_id: 'td', revision: 2,
    surfaces: [surface()], shot_effects: [] };
  Object.assign(api.state, {
    activeCampaignId: 'td', activeSessionId: 'gm', activeSessionRole: 'dm',
  });
  api.overlays.push({ innerHTML: '', closest: () => ({ dataset: { panelId: 'map' } }) });
  api.renderRulesetPanelProjection(projection);
  timing.panelLayoutMs = 250;
  try {
    api.renderRulesetPanelProjection({ ...projection, revision: 3,
      shot_effects: [{ action_id: 'burst', revision: 3,
        shots: [shot(0), shot(80), shot(160)] }] });
    assert.equal(api.gamePanels().effects.snapshot().active.length, 0);
    api.advance(16);
    assert.equal(api.gamePanels().effects.snapshot().active.length, 0);
    api.advance(16);
    assert.deepEqual(
      Array.from(api.gamePanels().effects.snapshot().active, (item) => item.start_ms),
      [1282, 1362, 1442],
    );
    api.advance(16);
    assert.match(api.overlays[0].innerHTML, /<line/);
  } finally { api.gamePanels().leave(); }
});

test('an empty concurrent rerender retains fresh effects until the replacement map paints', () => {
  const api = harness();
  const projection = { campaign_id: 'td', ruleset_id: 'td', revision: 2,
    surfaces: [surface()], shot_effects: [] };
  const cue = { map_id: 'floor', origin: [1, 1, 1], destination: [3, 1, 1], delay_ms: 0,
    miss: true, contacts: [], style: {
      color: '#FFFFFF', width: 2, travel_ms: 90, impact_ms: 140, fire_sound: null, cues: {},
    } };
  Object.assign(api.state, {
    activeCampaignId: 'td', activeSessionId: 'gm', activeSessionRole: 'dm',
  });
  api.renderRulesetPanelProjection(projection);
  api.paint();

  api.renderRulesetPanelProjection({ ...projection, revision: 3,
    shot_effects: [{ action_id: 'shot', revision: 3, shots: [cue] }] });
  api.renderRulesetPanelProjection({ ...projection, revision: 3, shot_effects: [] });
  assert.equal(api.gamePanels().effectRevision(), 2);
  api.paint();

  assert.equal(api.gamePanels().effectRevision(), 3);
  assert.equal(api.gamePanels().effects.snapshot().active.length, 1);
});

test('a child render immediately draws the received owner timeline without changing its anchor', () => {
  const ownerContext = { campaign_id: 'td', session_id: 'gm', generation: 'owner-generation' };
  const api = harness({ childContext: { context: ownerContext, window_id: 'map-window' } });
  const cue = { map_id: 'floor', origin: [1, 1, 1], destination: [3, 1, 1], delay_ms: 0,
    miss: true, contacts: [], style: {
      color: '#FFFFFF', width: 2, travel_ms: 90, impact_ms: 140, fire_sound: null, cues: {},
    } };
  const ownerStart = 900;
  api.gamePanels().effects.receive({
    scope: ownerContext.generation,
    revision: 3,
    active: [{ id: 'shot:0', start_ms: ownerStart, cue }],
    preferences: { visual: 'motion', volume: 0.5, muted: false },
  });
  Object.assign(api.state, {
    activeCampaignId: 'td', activeSessionId: 'gm', activeSessionRole: 'dm',
  });
  api.overlays.push({ innerHTML: '', closest: () => ({ dataset: { panelId: 'map' } }) });

  api.renderRulesetPanelProjection({ campaign_id: 'td', ruleset_id: 'td', revision: 3,
    surfaces: [surface()], shot_effects: [] });

  assert.match(api.overlays[0].innerHTML, /<line/);
  assert.equal(api.gamePanels().effects.snapshot().active[0].start_ms, ownerStart);
});

test('leaving or skipping before paint cancels pending presentation without later replay', () => {
  const projection = { campaign_id: 'td', ruleset_id: 'td', revision: 2,
    surfaces: [surface()], shot_effects: [] };
  const cue = { map_id: 'floor', origin: [1, 1, 1], destination: [3, 1, 1], delay_ms: 0,
    miss: true, contacts: [], style: {
      color: '#FFFFFF', width: 2, travel_ms: 90, impact_ms: 140, fire_sound: null, cues: {},
    } };
  const fresh = { ...projection, revision: 3,
    shot_effects: [{ action_id: 'shot', revision: 3, shots: [cue] }] };

  const skipped = harness();
  Object.assign(skipped.state, {
    activeCampaignId: 'td', activeSessionId: 'gm', activeSessionRole: 'dm',
  });
  skipped.renderRulesetPanelProjection(projection);
  skipped.paint();
  skipped.renderRulesetPanelProjection(fresh);
  skipped.gamePanels().skipEffects();
  skipped.paint();
  assert.equal(skipped.gamePanels().effectRevision(), 3);
  assert.equal(skipped.gamePanels().effects.snapshot().active.length, 0);

  const left = harness();
  Object.assign(left.state, {
    activeCampaignId: 'td', activeSessionId: 'gm', activeSessionRole: 'dm',
  });
  left.renderRulesetPanelProjection(projection);
  left.paint();
  left.renderRulesetPanelProjection(fresh);
  left.gamePanels().leave();
  left.state.activeSessionId = 'other';
  left.paint();
  assert.equal(left.gamePanels().effectRevision(), null);
  assert.equal(left.gamePanels().effects.snapshot().active.length, 0);
});


test('geometry inspector shows physical footprint and only the public material label', () => {
  const api = harness();
  const map = surface();
  map.data.geometry[0].display.material_label = 'Steel <screen> · resistance 3';
  api.state.panelProjection = { surfaces: [map] };
  api.state.tacticalViews.map = { zoom: 1, selectedGeometry: 'pillar' };
  let html = api.renderTacticalGridPanel(map.data, map.id);
  assert.match(html, /Footprint: 0.5 × 1.5 m/);
  assert.match(html, /Material: Steel &lt;screen&gt; · resistance 3/);
  assert.doesNotMatch(html, /hidden encounter detail/);
  delete map.data.geometry[0].display.material_label;
  html = api.renderTacticalGridPanel(map.data, map.id);
  assert.match(html, /Material: Not specified/);
});
