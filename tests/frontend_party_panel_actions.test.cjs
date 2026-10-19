// Exercise Party actor controls against the real frontend module.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { webcrypto } = require('node:crypto');

function element() {
  const listeners = new Map();
  return {
    listeners, dataset: {}, children: [], innerHTML: '', disabled: false, hidden: false,
    classList: { add() {}, remove() {}, toggle() {} }, style: { setProperty(name, value) { this[name] = value; } },
    addEventListener(name, handler) { listeners.set(name, handler); },
    removeEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
    setAttribute(name, value) { this[name] = value; },
    getBoundingClientRect() { return { height: 40 }; },
    appendChild(child) { child.parentElement = this; this.children.push(child); },
    remove() { this.parentElement.children = this.parentElement.children.filter((x) => x !== this); },
    showModal() { this.open = true; },
    close(value) { this.returnValue = value; this.open = false; listeners.get('close')(); },
  };
}

function partySurface() {
  return {
    id: 'party', title: 'Party', renderer: 'party', minimum_size: 96, default_size: 180,
    data: { participants: [
      { id: 'alpha', kind: 'character', data: { name: 'Asha' } },
      { id: 'bravo', kind: 'character', data: { name: 'Bram' } },
      { id: 'npc', kind: 'companion', data: { name: 'Nell' } },
    ], entities: {} },
    actions: [
      { id: 'act', label: 'Act', available: true, bindings: { creature_id: 'actor' } },
      { id: 'rest', label: 'Rest', available: true, bindings: {} },
    ],
  };
}

function harness(playerControl, surface = partySurface()) {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  const body = element();
  const button = element();
  button.dataset.panelActionId = 'act';
  button.dataset.panelActorId = 'bravo';
  button.closest = () => ({ dataset: { panelId: surface.id } });
  const panel = element(), scroll = element(), actorSelect = element(), targetSelect = element();
  const previewHost = element(), previewOverlay = element();
  panel.dataset.panelId = surface.id;
  Object.assign(scroll, { scrollWidth: 256, clientWidth: 256, scrollHeight: 192, clientHeight: 192 });
  const entityButtons = (surface.data.entities instanceof Array ? surface.data.entities : []).map((item) => {
    const control = element();
    control.dataset.tacticalEntity = item.id;
    return control;
  });
  panel.querySelector = (selector) => ({
    '.tactical-scroll': scroll, '[data-tactical-actor]': actorSelect,
    '[data-tactical-target]': targetSelect,
    '[data-tactical-preview]': previewHost,
    '[data-tactical-map-preview-overlay]': previewOverlay,
  })[selector] || null;
  panel.querySelectorAll = (selector) => selector === '[data-tactical-entity]' ? entityButtons : [];
  node('ruleset-panels').querySelectorAll = (selector) =>
    selector === '[data-panel-action-id]' ? [button] : selector === '[data-panel-id]' ? [panel] : [];
  node('ruleset-panels').querySelector = (selector) =>
    selector === `[data-panel-id="${surface.id}"]` ? panel : null;
  const context = {
    window: { requestAnimationFrame: (callback) => { callback(); return 1; } },
    crypto: webcrypto, console, URL, performance: { now: () => 1000 },
    CSS: { escape: (value) => {
      assert.match(value, /^[a-z]+$/); // Fixture IDs need no CSS escaping.
      return value;
    } },
    document: { body, getElementById: node, addEventListener() {}, createElement: element },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  };
  let source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const insertion = source.lastIndexOf('  return {');
  source = source.slice(0, insertion) + `
    window.partyTest = { state, ensureSessionActivity, panelMarkup, tacticalSelection,
      renderRulesetPanelProjection, dispatchPanelWindowRequest, applyPanelSelection,
      refreshPanels: renderRulesetPanels };
    refreshSelectedCampaign = async () => {};
    renderRulesetPanels = async () => {};
  ` + source.slice(insertion);
  source = ['panel-workspace.js', 'shot-playback.js', 'panel-host.js', 'piece-table.js'].map((name) =>
    fs.readFileSync(path.join(__dirname, '../src-tauri/frontend', name), 'utf8')).join('\n') + '\n' + source;
  context.setTimeout ||= setTimeout;
  context.clearTimeout ||= clearTimeout;
  context.window.innerWidth = 1400;
  vm.runInNewContext(source, context);
  const api = context.window.partyTest;
  Object.assign(api.state, {
    activeCampaignId: 'campaign', activeSessionId: 'gm', activeSessionRole: 'dm',
    activeSnapshot: { revision: 0 }, view: 'session', campaigns: [],
  });
  const activity = api.ensureSessionActivity({
    sessionId: 'gm', sessionRole: 'dm', campaignId: 'campaign',
  });
  const projection = { campaign_id: 'campaign', player_control: playerControl, surfaces: [surface] };
  api.state.panelProjection = projection;
  const previews = [];
  const executions = [];
  api.state.bridge = {
    snapshot: async () => ({ revision: 8 }),
    preview_panel_action: async (command) => {
      previews.push(command);
      return { confirmation_required: true, confirmation: 'exact', label: 'Act', preview: null };
    },
    execute_panel_action: async (command) => { executions.push(command); return { status: 'committed' }; },
  };
  api.renderRulesetPanelProjection(projection);
  return {
    api, activity, body, button, surface, projection, previews, executions,
    actorSelect, targetSelect, entityButtons, previewHost, previewOverlay, panels: node('ruleset-panels'),
    host: node('ruleset-panel-host'),
    markup: () => api.panelMarkup(surface),
    click: () => button.listeners.get('click')(),
  };
}

for (const order of [
  { order_first: 'alpha', order_second: 'cal' },
  { order: [{ entity_id: 'alpha' }, { entity_id: 'cal' }], order_first: 'old' },
]) {
  test(`Encounter shows only this encounter and resolves NPC names: ${JSON.stringify(order)}`, () => {
    const surface = { id: 'encounter', title: 'Encounter', renderer: 'encounter',
      minimum_size: 96, default_size: 180, actions: [], data: {
        combat: { encounter_active: true, round: 2, current_actor: 'cal', ...order },
        participants: [{ id: 'alpha', data: { name: 'Asha' } }],
        entities: {
          alpha: { hp: 20, max_hp: 20, creature_kind: 'character' },
          cal: { name: 'Cal <Moss>', hp: 10, max_hp: 10, creature_kind: 'monster' },
          old: { name: 'Earlier opponent', hp: 5, max_hp: 10, creature_kind: 'monster' },
        },
      } };
    const h = harness({ available: true, actors: [] }, surface);
    const html = h.markup();
    assert.match(html, /Current: Cal &lt;Moss&gt;/);
    assert.match(html, />Cal &lt;Moss&gt;<\/strong>/);
    assert.match(html, />Asha<\/strong>/);
    assert.doesNotMatch(html, /Earlier opponent|data-panel-entity="old"/);
    assert.equal((html.match(/data-panel-entity=/g) || []).length, 2);
  });
}

test('squad scope renders controls for two controlled members and disables the noncontrolled member', () => {
  const h = harness({ available: true, focused_actor_id: 'bravo', actors: [
    { id: 'alpha', name: 'Asha' }, { id: 'bravo', name: 'Bram' },
  ] });
  const markup = h.markup();
  assert.match(markup, /Asha[\s\S]*Player controlled/);
  assert.match(markup, /data-party-select-actor="bravo" aria-pressed="true"/);
  assert.equal((markup.match(/data-panel-actor-id="alpha"/g) || []).length, 1);
  assert.equal((markup.match(/data-panel-actor-id="bravo"/g) || []).length, 1);
  assert.match(markup, /data-panel-actor-id="npc" disabled>Act<\/button>/);
  assert.equal((markup.match(/>Rest<\/button>/g) || []).length, 1);
  assert.equal((markup.match(/data-party-select-actor=/g) || []).length, 3);
  assert.match(markup, /data-party-select-actor="npc" aria-pressed="false"/);
});

test('party posture comes from the public map projection and is escaped', () => {
  const h = harness({ available: true, actors: [{ id: 'alpha', name: 'Asha' }] });
  const map = mapSurface();
  map.data.map.grid.unit = 'm';
  map.data.entities[0].height = 0.45;
  map.data.entities[0].display.stance = 'prone <test>';
  map.data.entities[0].private_note = 'hidden body detail';
  h.projection.surfaces.push(map);
  h.api.renderRulesetPanelProjection(h.projection);
  assert.match(h.markup(), /prone &lt;test&gt;, 0\.45 m high/);
  assert.doesNotMatch(h.markup(), /hidden body detail|private_note/);
});

test('Party puts the canonical Player Character first without mutating roster or following focus', () => {
  const surface = partySurface();
  surface.data.participants = [
    { id: 'bravo', kind: 'character', data: { name: 'Bram', party_role: 'companion' } },
    { id: 'alpha', kind: 'character', data: { name: 'Asha', party_role: 'player_character' } },
    { id: 'npc', kind: 'character', data: { name: 'Nell', party_role: 'companion' } },
  ];
  const roster = JSON.stringify(surface.data.participants);
  const h = harness({ available: true, focused_actor_id: 'bravo', actors: [
    { id: 'bravo' }, { id: 'alpha' },
  ] }, surface);
  const html = h.markup();
  assert(html.indexOf('>Asha</strong>') < html.indexOf('>Bram</strong>'));
  assert(html.indexOf('>Bram</strong>') < html.indexOf('>Nell</strong>'));
  assert.equal(JSON.stringify(surface.data.participants), roster);
  assert.match(html, /data-party-select-actor="bravo" aria-pressed="true"/);
  delete surface.data.participants[1].data.party_role;
  assert(h.markup().indexOf('>Bram</strong>') < h.markup().indexOf('>Asha</strong>'));
  assert.equal(h.executions.length, 0);
});

test('Party and legacy Inventory humanize identifiers while preserving authored names and values', () => {
  const surface = partySurface();
  surface.data.participants[0] = { id: 'alpha', kind: 'player_character', data: { name: 'Asha_of_Oak' } };
  surface.data.entities.alpha = { resources: { hit_die: 1, second_wind: 2, Arrows: 20 },
    inventory: ['chain_mail', 'dungeoneers_pack', 'gaming_set_dice', '53_gp', 'Asha_of_Oak’s Charm'] };
  const before = JSON.stringify(surface.data);
  const h = harness({ available: true, actors: [{ id: 'alpha' }] }, surface);
  const party = h.markup();
  assert.match(party, /Player Character/);
  assert.match(party, /Hit Die: 1 · Second Wind: 2 · Arrows: 20/);
  assert.match(party, /Asha_of_Oak/);
  surface.renderer = 'inventory';
  const inventory = h.markup();
  assert.match(inventory, /Chain Mail/);
  assert.match(inventory, /Dungeoneers Pack/);
  assert.match(inventory, /Gaming Set Dice/);
  assert.match(inventory, /53 Gp/);
  assert.match(inventory, /Asha_of_Oak’s Charm/);
  assert.equal(JSON.stringify(surface.data), before);
});

test('generic Inventory distinguishes identifier fallbacks from authored item names', () => {
  const h = harness({ available: false, actors: [] }, {
    id: 'inventory', renderer: 'generic_inventory', data: { characters: [{ id: 'alpha', name: 'Asha',
      items: [
        { item_id: 'chain_mail', name: 'chain_mail', quantity: 1, holder: 'Asha', owned: true },
        { name: 'moon_shard', quantity: 2, holder: 'Asha', owned: true },
        { item_id: 'another_item', name: 'a_special_name', quantity: 1, holder: 'Asha', owned: true },
      ],
    }] },
  });
  assert.match(h.markup(), /Chain Mail/);
  assert.match(h.markup(), /moon_shard/);
  assert.match(h.markup(), /a_special_name/);
});

test('Squad and Character omit definition IDs but retain authored roles and character state', () => {
  for (const renderer of ['party', 'unit_list']) {
    const surface = { ...partySurface(), renderer };
    surface.data.participants = [{ id: 'alpha', kind: 'trigger-discipline.character',
      data: { name: 'Asha', class_id: 'RIFLEMAN', level: 5 } }];
    surface.data.entities.alpha = { hp: 9, res: 4, ap: 2 };
    const before = JSON.stringify(surface.data);
    const h = harness({ available: true, actors: [{ id: 'alpha' }] }, surface);
    assert.match(h.markup(), /<strong>Asha<\/strong>/);
    assert.match(h.markup(), /HP 9/);
    assert.match(h.markup(), /RES 4 · AP 2/);
    assert.doesNotMatch(h.markup(), /trigger-discipline\.character|panel-unit-role/);
    if (renderer === 'unit_list') assert.match(h.markup(), /RIFLEMAN · Level 5/);
    assert.equal(JSON.stringify(surface.data), before);
    surface.data.participants[0].data.party_role = '<Dr. Scout>';
    assert.match(h.markup(), /panel-unit-role">&lt;Dr\. Scout&gt;<\/span>/);
    assert.equal(h.executions.length, 0);
  }
});

test('status panels name missing values without exposing null or raw identifiers', () => {
  const surface = { id: 'status', renderer: 'key_value', data: {
    active_side: 'allies', decision_owner: 'AWAITING_PLAYER', mission_status: null, count: 0,
  }, actions: [] };
  const h = harness({ available: false, actors: [] }, surface);
  assert.match(h.markup(), /Active Side/);
  assert.match(h.markup(), /Awaiting Player/);
  assert.match(h.markup(), /Not available yet/);
  assert.doesNotMatch(h.markup(), /active_side|decision_owner|mission_status|>null</);
  surface.data = { active_side: null };
  assert.match(h.markup(), /No current status yet/);
});

test('Character inspection shares Loadout while Map retains its acting character without a command', () => {
  const character = { ...partySurface(), id: 'character', renderer: 'unit_list' };
  character.data.entities.alpha = { hp: 9, res: 4, ap: 2, max_ap: 6, skills: { local_hacking: 2 } };
  character.data.entities.bravo = { hp: 7, res: 3, ap: 1, weapon: {
    name: '<Custom rifle>', location: 'READY', magazine: { rounds: 8, capacity: 20 }, chamber: { loaded: true },
  } };
  const h = harness({ available: true, actors: [{ id: 'alpha' }, { id: 'bravo' }] }, character);
  const map = mapSurface();
  h.projection.surfaces.push(map, { id: 'loadout', renderer: 'inventory', data: character.data });
  h.api.renderRulesetPanelProjection(h.projection);
  h.api.applyPanelSelection('character', { actor: 'bravo' });
  assert.equal(h.api.tacticalSelection(map).actor, 'alpha');
  assert.match(h.markup(), /HP 7/);
  assert.match(h.markup(), /RES 3 · AP 1/);
  assert.doesNotMatch(h.markup(), /HP 9/);
  const loadout = h.api.panelMarkup(h.projection.surfaces.at(-1));
  assert.match(loadout, /&lt;Custom rifle&gt;/);
  assert.match(loadout, /Magazine: 8 \/ 20/);
  assert.match(loadout, /Chamber: Loaded/);
  assert.deepEqual(h.executions, []);
  h.api.applyPanelSelection('map', { actor: 'alpha' });
  assert.match(h.markup(), /Local Hacking/);
  assert.match(h.markup(), /HP 9/);
  h.api.applyPanelSelection('character', { actor: 'npc' });
  assert.equal(h.api.state.characterInspection.id, 'npc');
  assert.equal(h.api.tacticalSelection(map).actor, 'alpha');
});

test('generic inventory selects committed character holdings and escapes all labels', () => {
  const surface = {
    id: 'inventory', title: 'Inventory', renderer: 'generic_inventory', actions: [],
    minimum_size: 120, default_size: 220,
    data: { characters: [
      { id: 'alpha', name: 'Asha', items: [
        { name: '<Arrow>', quantity: 2, kind: 'item', holder: 'Asha',
          owner: '<Waystation>', carried: true, owned: false },
        { name: 'Gold', quantity: 49, kind: 'currency', holder: 'Asha',
          owner: 'Asha', carried: true, owned: true },
      ] },
      { id: 'bravo', name: 'Bram', items: [] },
    ] },
  };
  const h = harness({ available: false, actors: [] }, surface);
  assert.match(h.markup(), /&lt;Arrow&gt;/);
  assert.match(h.markup(), /Owner: &lt;Waystation&gt;/);
  assert.match(h.markup(), /Currency/);
  assert.doesNotMatch(h.markup(), /\[object Object\]/);
  h.api.applyPanelSelection('inventory', { character: 'bravo' });
  assert.match(h.markup(), /No recorded items or currency/);
  assert.doesNotMatch(h.markup(), /&lt;Arrow&gt;/);
  h.api.applyPanelSelection('inventory', { character: 'missing' });
  assert.match(h.markup(), /No recorded items or currency/);
});

function mapSurface() {
  return {
    id: 'map', renderer: 'tactical_grid', title: 'Map', minimum_size: 96, default_size: 180,
    data: {
      map_revision: 7,
      map: { bounds: { min: [0, 0, 0], max: [8, 6, 3] }, grid: { cell_size: 1 } },
      entities: ['alpha', 'bravo', 'npc'].map((id, i) => ({
        id, display: { label: ['Asha', 'Bram', 'Nell'][i] }, position: [i + 0.5, 0.5, 0],
        height: [1.8, 1.6, 2][i],
      })),
      selection: { actor: 'alpha' }, geometry: [], zones: [], objectives: [], terrain: [],
    },
    actions: [{ id: 'act', label: 'Move', available: true, bindings: { creature_id: 'actor' } }],
  };
}

test('map actor selection offers controlled members, while targets include the whole visible map', () => {
  const h = harness({ available: true, focused_actor_id: 'alpha', actors: [
    { id: 'alpha', name: 'Asha' }, { id: 'bravo', name: 'Bram' },
  ] }, mapSurface());
  const markup = h.markup();
  const actorOptions = markup.split('aria-label="Choose acting character"')[1].split('</select>')[0];
  assert.match(actorOptions, />Asha<\/option>/);
  assert.match(actorOptions, />Bram<\/option>/);
  assert.doesNotMatch(actorOptions, /Nell/);
  assert.match(markup.split('aria-label="Choose target"')[1], />Nell<\/option>/);
  h.actorSelect.listeners.get('change')({ target: { value: 'bravo' } });
  assert.equal(h.api.tacticalSelection(h.surface).actor, 'bravo');
  h.actorSelect.listeners.get('change')({ target: { value: 'npc' } });
  assert.equal(h.api.tacticalSelection(h.surface).actor, 'bravo');
  h.targetSelect.listeners.get('change')({ target: { value: 'alpha' } });
  assert.equal(h.api.tacticalSelection(h.surface).target, 'alpha');
  assert.deepEqual(JSON.parse(JSON.stringify(h.api.tacticalSelection(h.surface).aim_point)),
    [0.5, 0.5, 0.9]);
  h.entityButtons[2].listeners.get('click')();
  assert.equal(h.api.tacticalSelection(h.surface).target, 'npc');
  assert.deepEqual(JSON.parse(JSON.stringify(h.api.tacticalSelection(h.surface).aim_point)),
    [2.5, 0.5, 1]);
  assert.equal(h.api.tacticalSelection(h.surface).actor, 'bravo');
  assert.equal(h.previews.length, 0);
  assert.equal(h.executions.length, 0);

  delete h.surface.data.entities[0].height;
  h.targetSelect.listeners.get('change')({ target: { value: 'alpha' } });
  assert.equal(h.api.tacticalSelection(h.surface).aim_point, null);
});

test('panel controls refresh when owner availability or session activity changes at the same revision', async () => {
  const h = harness({ available: true, actors: [{ id: 'bravo', name: 'Bram' }] });
  let available = false;
  h.api.state.bridge.campaign_panels = async () => ({ ...h.projection, revision: 28,
    surfaces: [{ ...h.surface, actions: h.surface.actions.map((action) => ({ ...action, available })) }],
  });
  await h.api.refreshPanels('campaign');
  assert.match(h.panels.innerHTML, /data-panel-action-id="rest" disabled/);
  available = true;
  await h.api.refreshPanels('campaign');
  assert.doesNotMatch(h.panels.innerHTML, /data-panel-action-id="rest" disabled/);
  h.activity.phase = 'working';
  await h.api.refreshPanels('campaign');
  assert.match(h.panels.innerHTML, /data-panel-action-id="rest" disabled/);
  h.activity.phase = 'completed';
  await h.api.refreshPanels('campaign');
  assert.doesNotMatch(h.panels.innerHTML, /data-panel-action-id="rest" disabled/);
});

test('a late busy panel reply cannot replace the final available projection at the same revision', async () => {
  const h = harness({ available: true, actors: [{ id: 'bravo', name: 'Bram' }] });
  const pending = [];
  h.api.state.bridge.campaign_panels = () => new Promise((resolve, reject) => pending.push({ resolve, reject }));
  const stale = h.api.refreshPanels('campaign', true);
  const final = h.api.refreshPanels('campaign', true);
  const ready = { ...h.projection, revision: 28 };
  pending[1].resolve(ready);
  await final;
  pending[0].resolve({ ...ready, surfaces: [{ ...h.surface,
    actions: h.surface.actions.map((action) => ({ ...action, available: false })),
  }] });
  await stale;
  assert.equal(h.api.state.panelProjectionSource, ready);
  assert.deepEqual(h.api.state.panelProjection.player_control, ready.player_control);
  assert.doesNotMatch(h.panels.innerHTML, /data-panel-action-id="rest" disabled/);

  const obsoleteFailure = h.api.refreshPanels('campaign', true);
  const newer = h.api.refreshPanels('campaign', true);
  pending[3].resolve(ready);
  await newer;
  pending[2].reject(new Error('Old request disconnected'));
  await obsoleteFailure;
  assert.equal(h.api.state.panelProjectionSource, ready);
  assert.deepEqual(h.api.state.panelProjection.player_control, ready.player_control);
});

test('selecting a visible firing target supplies its public body center for action readiness', async () => {
  const surface = mapSurface();
  surface.actions[0] = {
    id: 'fire', label: 'Fire', available: true, checks_required: true,
    map_preview_enabled: true,
    bindings: { attacker: 'actor', target: 'target', aim_point: 'aim_point' },
  };
  const h = harness({ available: true, focused_actor_id: 'alpha', actors: [
    { id: 'alpha', name: 'Asha' },
  ] }, surface);
  h.button.dataset.panelActionId = 'fire';
  h.projection.revision = 0;
  const checks = [];
  h.api.state.bridge.player_action_availability = (command) => new Promise((resolve) => {
    checks.push({ command, resolve });
  });
  h.api.renderRulesetPanelProjection(h.projection);

  h.targetSelect.listeners.get('change')({ target: { value: 'bravo' } });
  assert.deepEqual(JSON.parse(JSON.stringify(checks.at(-1).command.requests[0].selection)), {
    map_revision: 7, actor: 'alpha', target: 'bravo', aim_point: [1.5, 0.5, 0.8],
  });
  checks.at(-1).resolve({ campaign_id: 'campaign', revision: 0,
    results: [{ available: true, reason: null }] });
  await new Promise(setImmediate);
  assert.equal(h.button.disabled, false);
});

test('successful map action retains valid identities across the refreshed map revision', async () => {
  const surface = mapSurface();
  surface.actions[0].bindings = { creature_id: 'actor', destination: 'destination' };
  const h = harness({ available: true, focused_actor_id: 'alpha', actors: [
    { id: 'alpha', name: 'Asha' }, { id: 'bravo', name: 'Bram' },
  ] }, surface);
  h.api.state.tacticalSelections.map = {
    map_revision: 7, actor: 'alpha', target: 'npc', destination: [3, 2],
    aim_point: [3.5, 2.5, 0], path: [[0.5, 0.5, 0], [3.5, 2.5, 0]],
  };

  const pending = h.click();
  await new Promise(setImmediate);
  h.body.children[0].close('confirm');
  await pending;
  assert.deepEqual(JSON.parse(JSON.stringify(h.executions[0].selection)), {
    map_revision: 7, actor: 'alpha', destination: [3, 2],
  });

  surface.data.map_revision = 8;
  surface.data.selection = { destination: [3, 2], area: { kind: 'box' } };
  surface.data.entities[0].position = [3.5, 2.5, 0];
  const refreshed = h.api.tacticalSelection(surface);
  assert.equal(refreshed.actor, 'alpha');
  assert.equal(refreshed.target, 'npc');
  assert.equal(refreshed.map_revision, 8);
  assert.equal('destination' in refreshed, false);
  assert.equal('aim_point' in refreshed, false);
  assert.equal('area' in refreshed, false);
  assert.equal('path' in refreshed, false);

  surface.data.map_revision = 9;
  surface.data.entities = surface.data.entities.filter((entity) => entity.id === 'bravo');
  const unavailable = h.api.tacticalSelection(surface);
  assert.equal(unavailable.actor, null);
  assert.equal(unavailable.target, null);
});

test('a consumed map destination shows declared input guidance instead of an engine rejection', async () => {
  const surface = mapSurface();
  surface.actions[0] = {
    id: 'move', label: 'Move', available: true, checks_required: true,
    map_preview_enabled: true, bindings: { actor: 'actor', destination: 'destination' },
  };
  const h = harness({ available: true, focused_actor_id: 'alpha', actors: [
    { id: 'alpha', name: 'Asha' },
  ] }, surface);
  h.button.dataset.panelActionId = 'move';
  h.projection.revision = 1;
  const checks = [];
  h.api.state.bridge.player_action_availability = (command) => new Promise((resolve) => {
    checks.push({ command, resolve });
  });
  h.api.state.tacticalSelections.map = {
    map_revision: 7, actor: 'alpha', destination: [3, 2],
  };
  surface.data.map_revision = 8;
  h.api.renderRulesetPanelProjection(h.projection);
  assert.equal(h.api.tacticalSelection(surface).actor, 'alpha');
  assert.equal('destination' in h.api.tacticalSelection(surface), false);
  checks.at(-1).resolve({ campaign_id: 'campaign', revision: 1,
    results: [{ available: false, reason: 'Panel action selection is invalid' }] });
  await new Promise(setImmediate);
  assert.match(h.previewHost.innerHTML, /Select a map cell to preview Move/);
  assert.doesNotMatch(h.previewHost.innerHTML, /Panel action selection is invalid/);
});

test('control changes clear an obsolete map actor even when the map revision has not changed', () => {
  const h = harness({ available: true, focused_actor_id: 'alpha', actors: [
    { id: 'alpha', name: 'Asha' },
  ] }, mapSurface());
  assert.equal(h.api.tacticalSelection(h.surface).actor, 'alpha');
  h.api.state.panelProjection.player_control = { available: false, focused_actor_id: null, actors: [] };
  const markup = h.markup();
  assert.equal(h.api.tacticalSelection(h.surface).actor, null);
  assert.match(markup, /aria-label="Choose acting character" disabled/);
  assert.match(markup, /data-panel-action-id="act" disabled/);
});

test('single-character scope enables only its named actor and busy state disables every action', () => {
  const h = harness({ available: true, focused_actor_id: 'alpha', actors: [
    { id: 'alpha', name: 'Asha' },
  ] });
  assert.match(h.markup(), /data-party-select-actor="alpha" aria-pressed="true"/);
  assert.match(h.markup(), /data-panel-actor-id="bravo" disabled>Act<\/button>/);
  h.activity.phase = 'working';
  const busy = h.markup();
  assert.equal((busy.match(/data-panel-actor-id="[^"]+" disabled/g) || []).length, 3);
  assert.match(busy, />Rest<\/button>/);
  assert.match(busy, /data-panel-action-id="rest" disabled/);
});

test('actor click previews and executes with the captured member only', async () => {
  const h = harness({ available: true, focused_actor_id: 'bravo', actors: [
    { id: 'alpha', name: 'Asha' }, { id: 'bravo', name: 'Bram' },
  ] });
  const pending = h.click();
  await new Promise(setImmediate);
  assert.deepEqual(JSON.parse(JSON.stringify(h.previews[0].selection)), { actor: 'bravo' });
  assert.equal(h.previews[0].expected_revision, 8);
  h.body.children[0].close('confirm');
  await pending;
  assert.deepEqual(JSON.parse(JSON.stringify(h.executions[0].selection)), { actor: 'bravo' });
  assert.equal(h.executions[0].confirmation, 'exact');
});

test('cancellation and session navigation cannot execute the Party action', async () => {
  const cancelled = harness({ available: true, focused_actor_id: 'bravo', actors: [
    { id: 'bravo', name: 'Bram' },
  ] });
  let pending = cancelled.click();
  await new Promise(setImmediate);
  cancelled.body.children[0].close('cancel');
  await pending;
  assert.equal(cancelled.executions.length, 0);

  const navigated = harness({ available: true, focused_actor_id: 'bravo', actors: [
    { id: 'bravo', name: 'Bram' },
  ] });
  pending = navigated.click();
  await new Promise(setImmediate);
  navigated.api.state.activeSessionId = 'other-session';
  navigated.body.children[0].close('confirm');
  await pending;
  assert.equal(navigated.executions.length, 0);
});

test('readiness waits for the selected actor and ignores a late result for the previous actor', async () => {
  const h = harness({ available: true, focused_actor_id: 'alpha', actors: [
    { id: 'alpha', name: 'Asha' }, { id: 'bravo', name: 'Bram' },
  ] }, mapSurface());
  delete h.button.dataset.panelActorId;
  h.surface.actions[0].checks_required = true;
  h.surface.actions[0].map_preview_enabled = true;
  h.projection.revision = 0;
  const checks = [];
  h.api.state.bridge.player_action_availability = (command) => new Promise((resolve) => {
    checks.push({ command, resolve });
  });
  h.api.renderRulesetPanelProjection(h.projection);
  assert.equal(h.button.disabled, true);
  await h.click();
  assert.equal(h.previews.length, 0);
  h.actorSelect.listeners.get('change')({ target: { value: 'bravo' } });
  assert.equal(checks.length, 2);
  assert.equal(checks[0].command.requests[0].selection.actor, 'alpha');
  assert.equal(checks[1].command.requests[0].selection.actor, 'bravo');
  checks[0].resolve({ campaign_id: 'campaign', revision: 0,
    results: [{ available: true, reason: null, map_preview: {
      map_id: 'floor', map_revision: 7, notice: 'Old actor preview.',
      path: { found: true, truncated: false, cost: 1, points: [[0, 0, 0], [1, 1, 0]] },
    } }] });
  await new Promise(setImmediate);
  assert.equal(h.button.disabled, true);
  assert.equal(h.previewOverlay.innerHTML, '');
  assert.doesNotMatch(h.previewHost.innerHTML, /Old actor preview/);
  checks[1].resolve({ campaign_id: 'campaign', revision: 0,
    results: [{ available: false, reason: 'No action points remaining', map_preview: {
      map_id: 'floor', map_revision: 7, notice: 'Based on the visible map.',
      path: { found: true, truncated: false, cost: 2, points: [[1, 0, 0], [2, 2, 0]] },
    } }] });
  await new Promise(setImmediate);
  assert.equal(h.button.disabled, true);
  assert.equal(h.button.title, 'No action points remaining');
  assert.equal(h.button['aria-description'], 'No action points remaining');
  assert.match(h.previewHost.innerHTML, /Route cost 2/);
  assert.match(h.previewHost.innerHTML, /No action points remaining/);
  assert.match(h.previewOverlay.innerHTML, /points="32,0 64,64"/);
  h.actorSelect.listeners.get('change')({ target: { value: 'alpha' } });
  assert.equal(h.button.disabled, false);
  assert.equal(checks.length, 2); // Reuse only the exact selection at the unchanged revision.
});

test('late readiness cannot enable controls in a different session or while Sylvia is working', async () => {
  for (const changeSession of [false, true]) {
    const h = harness(null);
    h.surface.actions[0].checks_required = true;
    h.projection.revision = 0;
    let finish;
    h.api.state.bridge.player_action_availability = () => new Promise((resolve) => { finish = resolve; });
    h.api.renderRulesetPanelProjection(h.projection);
    assert.equal(h.button.disabled, true);
    if (changeSession) h.api.state.activeSessionId = 'another-session';
    else h.activity.phase = 'working';
    finish({ campaign_id: 'campaign', revision: 0, results: [{ available: true, reason: null }] });
    await new Promise(setImmediate);
    assert.equal(h.button.disabled, true);
    await h.click();
    assert.equal(h.previews.length, 0);
  }
});

test('panel windows queue in their owning session and cannot bypass execution or panel ownership', async () => {
  const h = harness(null);
  let saved = null; const compositions = [];
  Object.assign(h.api.state.bridge, {
    action_sequence: async () => saved,
    compose_action_sequence: async command => {
      compositions.push(command);
      saved = {...command,version:1,status:'ready',steps:command.steps.map(step=>({...step,status:'pending'}))};
      return saved;
    },
  });
  const scope = { campaign_id:'campaign',session_id:'gm',current:()=>true,owns:id=>id==='party' };
  const command = {campaign_id:'campaign',session_id:'gm',surface_id:'party',panel_action_id:'act',
    operation_id:'window-action',selection:{actor:'bravo'}};
  await h.api.dispatchPanelWindowRequest('queue_panel_action',command,scope);
  assert.equal(compositions.length,1);
  assert.equal(compositions[0].steps[0].step_id,'window-action');
  assert.deepEqual(JSON.parse(JSON.stringify(compositions[0].steps[0].selection)),{actor:'bravo'});
  assert.equal(h.previews.length,0);assert.equal(h.executions.length,0);
  await assert.rejects(h.api.dispatchPanelWindowRequest('execute_panel_action',command,scope),/unavailable/);
  for (const [args,context] of [
    [{...command,campaign_id:'other'},scope], [{...command,surface_id:'other-panel'},scope],
    [command,{...scope,current:()=>false}],
  ]) await assert.rejects(h.api.dispatchPanelWindowRequest('queue_panel_action',args,context));
  h.activity.phase='working';
  await assert.rejects(h.api.dispatchPanelWindowRequest('queue_panel_action',command,scope),/Sylvia is working/);
  assert.equal(compositions.length,1);assert.equal(h.executions.length,0);
});

test('the actual Party action listener queues by default and still rejects an uncontrolled actor', async () => {
  const h=harness({available:true,actors:[{id:'bravo'}]});let saved=null;const composed=[];
  Object.assign(h.api.state.bridge,{
    action_sequence:async()=>saved,
    compose_action_sequence:async command=>{composed.push(command);saved={...command,version:1,status:'ready',steps:command.steps};return saved;},
  });
  h.api.renderRulesetPanelProjection(h.projection);await h.click();
  assert.equal(composed.length,1);assert.equal(h.previews.length,0);assert.equal(h.executions.length,0);
  assert.equal(composed[0].steps[0].source.action_id,'act');
  assert.deepEqual(JSON.parse(JSON.stringify(composed[0].steps[0].selection)),{actor:'bravo'});
  h.projection.player_control.actors=[{id:'alpha'}];h.api.renderRulesetPanelProjection(h.projection);
  await h.click();assert.equal(composed.length,1,'Displayed actors do not grant control in default queue mode');
});

test('Party inspection preserves the acting character, target, aim and camera without an action', () => {
  const h = harness({ available: true, actors: [{ id: 'alpha' }, { id: 'bravo' }] });
  h.projection.surfaces.push(mapSurface());
  h.api.renderRulesetPanelProjection(h.projection);
  h.api.state.tacticalViews.map = { zoom: 1.5, scrollLeft: 72, scrollTop: 32 };
  const selection = h.api.tacticalSelection(h.projection.surfaces[1]);
  selection.target = 'npc'; selection.aim_point = [2.5, .5, 1];
  const before = JSON.stringify(selection);
  h.api.applyPanelSelection('party', { character: 'bravo' });
  assert.equal(JSON.stringify(h.api.tacticalSelection(h.projection.surfaces[1])), before);
  assert.equal(h.api.state.tacticalViews.map.scrollLeft, 72);
  assert.equal(h.api.state.tacticalViews.map.zoom, 1.5);
  assert.match(h.markup(), /data-party-select-actor="bravo" aria-pressed="true"/);
  assert.equal(h.executions.length, 0);
});

test('an inactive Encounter disappears while its saved panel position is retained', () => {
  const h = harness(null);
  const encounter = { id: 'encounter', title: 'Encounter', renderer: 'encounter',
    data: { combat: { encounter_active: true, round: 1 }, entities: {}, participants: [] }, actions: [] };
  h.projection.surfaces.push(encounter);
  h.api.renderRulesetPanelProjection(h.projection);
  assert.match(h.panels.innerHTML, /data-panel-id="encounter"/);
  encounter.data.combat.encounter_active = false;
  h.api.renderRulesetPanelProjection(h.projection);
  assert.doesNotMatch(h.panels.innerHTML, /data-panel-id="encounter"/);
  encounter.data.combat.encounter_active = true;
  h.api.renderRulesetPanelProjection(h.projection);
  assert.match(h.panels.innerHTML, /data-panel-id="encounter"/);
});

test('an unavailable tactical map keeps valid sibling panels and the game-panel host visible', () => {
  const h = harness(null);
  h.projection.surfaces.push(
    { id: 'operation', title: 'Operation status', renderer: 'objectives',
      data: [{ id: 'secure-gate', title: 'Secure the gate', description: 'In progress' }],
      actions: [] },
    { id: 'map', title: 'Area map', renderer: 'tactical_grid',
      data: { unavailable_reason: 'The saved map data is invalid. This campaign needs a corrected map.' },
      actions: [] },
  );

  h.api.renderRulesetPanelProjection(h.projection);

  assert.equal(h.host.hidden, false);
  assert.match(h.panels.innerHTML, /data-panel-id="party"/);
  assert.match(h.panels.innerHTML, /data-panel-id="operation"/);
  assert.match(h.panels.innerHTML, /data-panel-id="map"/);
  assert.match(h.panels.innerHTML, /The saved map data is invalid/);
});

for (const actor of ['vesper', 'autodrone_alpha']) test(`Delivered Map selection ${actor} replaces a previously selected Hex character`, () => {
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/td-delivered-character-selection.json')));
  const h = harness(fixture.player_control, fixture.character);
  h.projection.surfaces.push(fixture.map);
  h.api.renderRulesetPanelProjection(h.projection);
  h.api.applyPanelSelection(fixture.character.id, { actor: 'hex' });
  h.api.applyPanelSelection(fixture.map.id, { actor });
  assert.equal(h.api.state.characterInspection.id, actor);
  assert.match(h.markup(), new RegExp('data-character-entity="' + actor + '"'));
  assert.doesNotMatch(h.markup(), /<strong>Hex<\/strong>|value="hex" selected/);
  assert.deepEqual(h.executions, []);
});

test('one inspected noncontrolled member reaches Character and Inventory without granting control', () => {
  const h = harness({ available:true, focused_actor_id:'alpha', actors:[{id:'alpha'}] });
  const character = {...partySurface(), id:'character', renderer:'unit_list'};
  character.data.entities.npc = {hp:17, inventory:['silver_charm'], skills:{lore:4}};
  h.projection.surfaces.push(character, {...character,id:'inventory',renderer:'inventory'},mapSurface());
  h.api.renderRulesetPanelProjection(h.projection);
  const map = h.api.tacticalSelection(h.projection.surfaces.at(-1));
  map.target='bravo'; const before=JSON.stringify(map);
  h.api.applyPanelSelection('party',{character:'npc'});
  const details=h.api.panelMarkup(character),inventory=h.api.panelMarkup(h.projection.surfaces[2]);
  assert.match(details,/Nell/);assert.match(details,/HP 17/);assert.match(details,/lore <b>4/);
  assert.doesNotMatch(details,/data-character-actor/);
  assert.match(details,/Choose in Party/);assert.match(inventory,/Silver Charm/);
  assert.match(details,/data-panel-actor-id="npc" disabled/);
  assert.equal(JSON.stringify(map),before);assert.equal(h.executions.length,0);
});

test('Party details include Character-only class, loadout, resources and every declared stat group', () => {
  const h=harness({available:true,actors:[{id:'alpha'}]});
  const character={...partySurface(),id:'character',renderer:'unit_list'};
  character.data.participants[0].data={name:'Asha',class_id:'ranger',level:6};
  character.data.entities.alpha={hp:19,max_hp:23,ap:4,conditions:['Poisoned'],
    weapon_sets:{active_set:'ranged',ranged:{main_hand:'long_bow',off_hand:'silver_dagger'}},
    resources:{focus:7},aptitudes:{resolve:3},skills:{tracking:5}};
  h.projection.surfaces.push(character);h.api.renderRulesetPanelProjection(h.projection);
  // Party's compact values remain its own projection; detail groups use the Character owner.
  const party=h.markup(),single=h.api.panelMarkup(character);
  for(const expected of [/ranger · Level 6/,/Long Bow \/ Silver Dagger/,/focus/i,/resolve <b>3/,/tracking <b>5/]){
    assert.match(party,expected);assert.match(single,expected);
  }
  assert.equal((party.match(/data-panel-action-id="act" data-panel-actor-id="alpha"/g)||[]).length,1);
  assert.equal(h.executions.length,0);
});

test('Character-only actions remain on their owning panel instead of creating a cross-window dispatcher',()=>{
  const h=harness({available:true,actors:[{id:'alpha'}]});
  const character={...partySurface(),id:'character',title:'Character',renderer:'unit_list',
    actions:[{id:'crouch',label:'Crouch',available:true,bindings:{actor:'actor'}}]};
  h.projection.surfaces.push(character);h.api.renderRulesetPanelProjection(h.projection);
  assert.match(h.markup(),/Crouch/);
  assert.match(h.markup(),/data-character-action-panel="character"/);
  assert.doesNotMatch(h.markup(),/data-panel-action-id="crouch"/);
  assert.match(h.api.panelMarkup(character),/data-panel-action-id="crouch"/);
  assert.equal(h.executions.length,0);
});

test('removed inspection clears across views and does not return when the same id reappears', () => {
  const h=harness({available:true,actors:[{id:'alpha'},{id:'bravo'}]});
  const character={...partySurface(),id:'character',renderer:'unit_list'};
  h.projection.surfaces.push(character);h.api.renderRulesetPanelProjection(h.projection);
  h.api.applyPanelSelection('party',{character:'bravo'});
  h.api.applyPanelSelection('party',{expansion:{id:'bravo',open:true}});
  for(const surface of h.projection.surfaces)surface.data.participants=surface.data.participants.filter(p=>p.id!=='bravo');
  h.projection.player_control.actors=[{id:'alpha'}];h.api.renderRulesetPanelProjection(h.projection);
  assert.equal(h.api.state.characterInspection.id,null);
  assert.deepEqual(Array.from(h.api.state.characterInspection.expanded),[]);
  assert.match(h.api.panelMarkup(character),/previously inspected character is unavailable/);
  for(const surface of h.projection.surfaces)surface.data.participants.push({id:'bravo',data:{name:'New Bram'}});
  h.api.renderRulesetPanelProjection(h.projection);
  assert.equal(h.api.state.characterInspection.id,null);
  assert.doesNotMatch(h.api.panelMarkup(character),/New Bram/);
  h.api.applyPanelSelection('party',{character:'alpha'});
  assert.match(h.api.panelMarkup(character),/Asha/);
});

test('Ruleset replacement chooses current permitted focus rather than an old per-panel selection', () => {
  const h=harness({available:true,focused_actor_id:'alpha',actors:[{id:'alpha'},{id:'bravo'}]});
  h.api.applyPanelSelection('party',{character:'bravo'});
  h.projection.ruleset_id='replacement';h.api.renderRulesetPanelProjection(h.projection);
  assert.equal(h.api.state.characterInspection.id,'alpha');
  assert.equal(h.api.state.characterInspection.rulesetId,'replacement');
  assert.deepEqual(Array.from(h.api.state.characterInspection.expanded),[]);
});

test('delayed preview retains its captured character while inspection changes, with no duplicate send', async () => {
  const h=harness({available:true,actors:[{id:'alpha'},{id:'bravo'}]});
  let finish;h.api.state.bridge.preview_panel_action=command=>{
    h.previews.push(command);return new Promise(resolve=>finish=resolve);
  };
  const pending=h.click();await new Promise(setImmediate);
  h.api.applyPanelSelection('party',{character:'alpha'});
  h.api.renderRulesetPanelProjection(h.projection);h.button.disabled=false;
  await h.click();assert.equal(h.previews.length,1);
  finish({confirmation_required:true,confirmation:'bound-bravo',actor:{name:'Bram'},label:'Act'});
  await new Promise(setImmediate);assert.match(h.body.children[0].innerHTML,/Bram/);
  h.body.children[0].close('confirm');await pending;
  assert.equal(h.executions.length,1);assert.equal(h.executions[0].selection.actor,'bravo');
  assert.equal(h.api.state.characterInspection.id,'alpha');
});

for(const obsolete of ['removed','revision','navigation'])test(`obsolete ${obsolete} preview cannot reach confirmation or execution`,async()=>{
  const h=harness({available:true,actors:[{id:'bravo'}]});let finish;
  h.api.state.bridge.preview_panel_action=()=>new Promise(resolve=>finish=resolve);
  const pending=h.click();await new Promise(setImmediate);
  if(obsolete==='removed')h.projection.player_control.actors=[];
  if(obsolete==='revision'){h.projection.revision=9;h.api.renderRulesetPanelProjection(h.projection);}
  if(obsolete==='navigation')h.api.state.activeCampaignId='other';
  finish({confirmation_required:true,confirmation:'obsolete',label:'Act'});await pending;
  assert.equal(h.body.children.length,0);assert.equal(h.executions.length,0);
});

test('revision change during confirmation cancels the original command',async()=>{
  const h=harness({available:true,actors:[{id:'bravo'}]});
  const pending=h.click();await new Promise(setImmediate);
  h.projection.revision=9;h.api.renderRulesetPanelProjection(h.projection);h.body.children[0].close('confirm');await pending;
  assert.equal(h.executions.length,0);
});

test('a delayed queued action keeps its member after another character is inspected',async()=>{
  const h=harness({available:true,actors:[{id:'alpha'},{id:'bravo'}]});let finish;const composed=[];
  Object.assign(h.api.state.bridge,{action_sequence:async()=>null,
    snapshot:()=>new Promise(resolve=>finish=resolve),
    compose_action_sequence:async command=>{composed.push(command);return {...command,version:1,status:'ready',steps:command.steps};}});
  const pending=h.click();await new Promise(setImmediate);
  h.api.applyPanelSelection('party',{character:'alpha'});h.api.renderRulesetPanelProjection(h.projection);
  h.button.disabled=false;await h.click();finish({revision:8});await pending;
  assert.equal(composed.length,1);assert.equal(composed[0].steps.length,1);
  assert.equal(composed[0].steps[0].selection.actor,'bravo');assert.equal(h.executions.length,0);
});

test('a panel moved into a child cannot duplicate its pending main-window intention',async()=>{
  const h=harness({available:true,actors:[{id:'bravo'}]});
  h.api.state.panelIntentPending={campaignId:'campaign',sessionId:'gm'};
  const scope={campaign_id:'campaign',session_id:'gm',current:()=>true,owns:()=>true};
  const command={campaign_id:'campaign',session_id:'gm',surface_id:'party',panel_action_id:'act',selection:{actor:'bravo'}};
  await assert.rejects(h.api.dispatchPanelWindowRequest('queue_panel_action',command,scope),/pending panel action/);
  await assert.rejects(h.api.dispatchPanelWindowRequest('preview_panel_action',command,scope),/pending panel action/);
  assert.equal(h.previews.length,0);assert.equal(h.executions.length,0);
});

test('a delayed map confirmation retains both captured actor and target while inspection changes',async()=>{
  const surface=mapSurface();surface.actions[0].bindings={attacker:'actor',target:'target'};
  const h=harness({available:true,actors:[{id:'alpha'},{id:'bravo'}]},surface);
  const party=partySurface();h.projection.surfaces.push(party);h.api.renderRulesetPanelProjection(h.projection);
  h.api.tacticalSelection(surface).target='npc';let finish;
  h.api.state.bridge.preview_panel_action=command=>{h.previews.push(command);return new Promise(resolve=>finish=resolve);};
  const pending=h.click();await new Promise(setImmediate);
  h.api.applyPanelSelection('party',{character:'bravo'});
  h.api.applyPanelSelection('map',{target:'bravo'});
  finish({confirmation_required:true,confirmation:'alpha-npc',actor:{name:'Asha'},selection:{target:'npc'},label:'Act'});
  await new Promise(setImmediate);assert.match(h.body.children[0].innerHTML,/npc/);
  h.body.children[0].close('confirm');await pending;
  assert.equal(h.executions.length,1);assert.equal(h.executions[0].selection.actor,'alpha');
  assert.equal(h.executions[0].selection.target,'npc');assert.equal(h.api.tacticalSelection(surface).target,'bravo');
});

// A declared, ruleset-neutral table exercises the integration without adding
// Character/Party surfaces to the delivered Riichi ruleset.
function pieceSurface() {
  return { id: 'relay', title: 'Relay', renderer: 'piece_table', minimum_size: 96, default_size: 180,
    actions: [{ id: 'act', label: 'Run program', available: true, bindings: { selected: 'piece' } }],
    data: { format: 1, areas: [{ position: 'bottom', groups: [{ label: 'Programs', pieces: [
      { id: 'ghost', label: 'Ghost route', action_id: 'act' },
      { id: 'beacon', label: 'Beacon route', action_id: 'act' },
    ] }] }] } };
}

function pieceHarness() {
  const h = harness({ available: true, actors: [{ id: 'alpha' }, { id: 'bravo' }] }, pieceSurface());
  delete h.button.dataset.panelActorId;
  h.button.dataset.panelPieceId = 'ghost';
  h.projection.surfaces.push(partySurface());
  h.api.renderRulesetPanelProjection(h.projection);
  return h;
}

test('delayed piece preview keeps its captured piece across inspection and blocks a second intention', async () => {
  const h = pieceHarness();
  let finish;
  h.api.state.bridge.preview_panel_action = command => {
    h.previews.push(command); return new Promise(resolve => { finish = resolve; });
  };
  const pending = h.click(); await new Promise(setImmediate);
  h.api.applyPanelSelection('party', { character: 'bravo' });
  h.button.dataset.panelPieceId = 'beacon';
  h.api.renderRulesetPanelProjection(h.projection);
  h.button.disabled = false;
  await h.click();
  assert.equal(h.previews.length, 1);
  const scope = { campaign_id: 'campaign', session_id: 'gm', current: () => true, owns: () => true };
  await assert.rejects(h.api.dispatchPanelWindowRequest('preview_panel_action', {
    campaign_id: 'campaign', session_id: 'gm', surface_id: 'relay', panel_action_id: 'act',
    selection: { piece: 'beacon' },
  }, scope), /pending panel action/);
  finish({ confirmation_required: true, confirmation: 'ghost-only', label: 'Run program' });
  await new Promise(setImmediate);
  h.body.children[0].close('confirm'); await pending;
  assert.equal(h.executions.length, 1);
  assert.equal(JSON.stringify(h.executions[0].selection), '{"piece":"ghost"}');
  assert.equal(h.api.state.characterInspection.id, 'bravo');
});

for (const change of ['removed', 'concealed', 'rebound', 'revision', 'campaign']) {
  test(`delayed piece preview rejects a ${change} binding without execution`, async () => {
    const h = pieceHarness(); let finish;
    h.api.state.bridge.preview_panel_action = () => new Promise(resolve => { finish = resolve; });
    const pending = h.click(); await new Promise(setImmediate);
    const group = h.surface.data.areas[0].groups[0];
    if (change === 'removed') group.pieces.shift();
    if (change === 'concealed') group.concealed = true;
    if (change === 'rebound') group.pieces[0].action_id = 'different';
    if (change === 'revision') h.projection.revision = 9;
    if (change === 'campaign') h.api.state.activeCampaignId = 'other';
    if (change !== 'campaign') h.api.renderRulesetPanelProjection(h.projection);
    const before = JSON.stringify(h.projection);
    finish({ confirmation_required: true, confirmation: 'obsolete', label: 'Run program' });
    await pending;
    assert.equal(h.body.children.length, 0);
    assert.equal(h.executions.length, 0);
    assert.equal(JSON.stringify(h.projection), before);
  });
}

test('owner work beginning during piece confirmation prevents commit and inspection remains display-only', async () => {
  const h = pieceHarness();
  const pending = h.click(); await new Promise(setImmediate);
  h.activity.phase = 'working'; h.activity.attemptId = 'isolated-owner-decision';
  h.activity.actorId = 'npc';
  const before = JSON.stringify(h.projection);
  h.api.applyPanelSelection('party', { character: 'bravo' });
  h.api.renderRulesetPanelProjection(h.projection);
  h.body.children[0].close('confirm'); await pending;
  assert.equal(h.api.state.characterInspection.id, 'bravo');
  assert.equal(h.activity.attemptId, 'isolated-owner-decision');
  assert.equal(h.activity.actorId, 'npc');
  assert.equal(h.activity.phase, 'working');
  assert.equal(h.executions.length, 0);
  assert.equal(JSON.stringify(h.projection), before);
});
