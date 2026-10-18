// Exercise network actor selection against the real frontend and panel host modules.
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
    appendChild(child) { child.parentElement = this; this.children.push(child); },
    remove() { this.parentElement.children = this.parentElement.children.filter((item) => item !== this); },
    showModal() { this.open = true; },
    close(value) { this.returnValue = value; this.open = false; listeners.get('close')(); },
  };
}

function networkSurface(bindActor = true) {
  return {
    id: 'network', renderer: 'network_graph', title: 'Network',
    minimum_size: 96, default_size: 180,
    data: {
      graph_revision: 4,
      groups: [{ id: 'security', label: 'Security' }],
      nodes: [{ id: 'controller', label: 'Controller', kind: 'controller',
        group_id: 'security', functions: [] }],
      links: [], selection: { node: 'controller' },
    },
    actions: [{ id: 'operate', label: 'Operate', available: true,
      bindings: bindActor ? { operator: 'actor', node_id: 'node' } : { node_id: 'node' } }],
  };
}

function harness(surface = networkSurface()) {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  const body = element(), panel = element(), actionButton = element(), actorSelect = element();
  panel.dataset.panelId = surface.id;
  actionButton.dataset.panelActionId = 'operate';
  actionButton.closest = () => panel;
  panel.querySelector = (selector) => selector === '[data-network-actor]' ? actorSelect : null;
  panel.querySelectorAll = () => [];
  node('ruleset-panels').querySelectorAll = (selector) =>
    selector === '[data-panel-action-id]' ? [actionButton]
      : selector === '[data-panel-id]' ? [panel] : [];
  node('ruleset-panels').querySelector = (selector) =>
    selector === `[data-panel-id="${surface.id}"]` ? panel : null;

  const context = {
    window: { requestAnimationFrame: (callback) => { callback(); return 1; }, innerWidth: 1400 },
    crypto: webcrypto, console, URL, performance: { now: () => 1000 },
    CSS: { escape: (value) => value },
    document: { body, getElementById: node, addEventListener() {}, createElement: element,
      querySelectorAll: selector => selector === '[data-panel-action-id]' ? [actionButton] : [] },
    localStorage: { getItem: () => null, setItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    setTimeout, clearTimeout,
  };
  let source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const insertion = source.lastIndexOf('  return {');
  source = source.slice(0, insertion) + `
    window.networkActorTest = { state, ensureSessionActivity, panelMarkup, networkSelection,
      renderRulesetPanelProjection, applyPanelSelection, panelActionCheckKey, updateModelSwitchButtons };
    refreshSelectedCampaign = async () => {};
    renderRulesetPanels = async () => {};
  ` + source.slice(insertion);
  source = ['panel-workspace.js', 'shot-playback.js', 'panel-host.js'].map((name) =>
    fs.readFileSync(path.join(__dirname, '../src-tauri/frontend', name), 'utf8')).join('\n') + '\n' + source;
  vm.runInNewContext(source, context);

  const api = context.window.networkActorTest;
  Object.assign(api.state, {
    activeCampaignId: 'campaign', activeSessionId: 'gm', activeSessionRole: 'dm',
    activeSnapshot: { revision: 0 }, view: 'session', campaigns: [],
  });
  api.ensureSessionActivity({ sessionId: 'gm', sessionRole: 'dm', campaignId: 'campaign' });
  const projection = {
    campaign_id: 'campaign', revision: 0,
    player_control: { available: true, focused_actor_id: 'alpha', actors: [
      { id: 'alpha', name: 'Asha' }, { id: 'bravo', name: 'Bram' },
    ] },
    surfaces: [surface],
  };
  const previews = [], executions = [];
  api.state.panelProjection = projection;
  api.state.bridge = {
    snapshot: async () => ({ revision: 8 }),
    preview_panel_action: async (command) => {
      previews.push(command);
      return { confirmation_required: true, confirmation: 'exact', label: 'Operate', preview: null };
    },
    execute_panel_action: async (command) => { executions.push(command); return { status: 'committed' }; },
  };
  api.renderRulesetPanelProjection(projection);
  return {
    api, body, surface, projection, actorSelect, actionButton, previews, executions,
    markup: () => api.panelMarkup(surface),
    click: () => actionButton.listeners.get('click')(),
  };
}

test('bound network actions require a selected projected actor and dispatch that exact ID', async () => {
  const h = harness();
  let markup = h.markup();
  const options = markup.split('aria-label="Controlled network actor"')[1].split('</select>')[0];
  assert.match(options, />Asha \(current\)<\/option>/);
  assert.match(options, />Bram<\/option>/);
  assert.match(markup, /data-panel-action-id="operate" disabled/);
  assert.equal(h.api.networkSelection(h.surface).actor, undefined);

  h.actorSelect.listeners.get('change')({ target: { value: 'bravo' } });
  assert.equal(h.api.networkSelection(h.surface).actor, 'bravo');
  assert.doesNotMatch(h.markup(), /data-panel-action-id="operate" disabled/);

  const pending = h.click();
  await new Promise(setImmediate);
  assert.deepEqual(JSON.parse(JSON.stringify(h.previews[0].selection)), {
    graph_revision: 4, actor: 'bravo', node: 'controller',
  });
  h.body.children[0].close('confirm');
  await pending;
  assert.equal(h.executions[0].selection.actor, 'bravo');
  assert.equal(h.executions[0].expected_revision, 8);

  h.surface.actions[0].bindings = { node_id: 'node' };
  markup = h.markup();
  assert.doesNotMatch(markup, /aria-label="Controlled network actor"/);
});

test('model control refresh preserves panel actor and current readiness gates', () => {
  const h = harness();
  h.api.updateModelSwitchButtons();
  assert.equal(h.actionButton.disabled, true, 'No actor remains unavailable after the session idles');
  h.api.applyPanelSelection(h.surface.id, { actor: 'alpha' });
  h.api.updateModelSwitchButtons();
  assert.equal(h.actionButton.disabled, false);
  h.surface.actions[0].checks_required = true;
  const key = h.api.panelActionCheckKey(h.surface, h.surface.actions[0]);
  h.api.state.panelActionChecks.set(key, { available: false, reason: 'Insufficient AP' });
  h.api.updateModelSwitchButtons();
  assert.equal(h.actionButton.disabled, true, 'A failed mechanic check remains unavailable');
  h.api.state.panelActionChecks.set(key, { available: true });
  h.api.updateModelSwitchButtons();
  assert.equal(h.actionButton.disabled, false);
  h.api.state.reasoningPending = true;
  h.api.updateModelSwitchButtons();
  assert.equal(h.actionButton.disabled, true);
  assert.equal(h.executions.length, 0);
});

test('network access preview follows the selected actor and current revision without dispatch', () => {
  const h = harness();
  const action = h.surface.actions[0];
  h.api.applyPanelSelection(h.surface.id, { actor: 'alpha' });
  const key = h.api.panelActionCheckKey(h.surface, action);
  h.api.state.panelActionChecks.set(key, {
    available: true, preview: { version: 1, title: 'Network access', details: [], actions: [], summary: [
      { label: 'Current route', value: 'Dock <console> → Gate', emphasis: false },
    ] },
  });
  assert.match(h.markup(), /Dock &lt;console&gt; → Gate/);
  h.api.applyPanelSelection(h.surface.id, { actor: 'bravo' });
  assert.doesNotMatch(h.markup(), /Dock &lt;console&gt;/);
  h.api.applyPanelSelection(h.surface.id, { actor: 'alpha' });
  h.api.state.panelActionChecks.get(key).available = false;
  assert.doesNotMatch(h.markup(), /Dock &lt;console&gt;/);
  h.api.state.panelActionChecks.get(key).available = true;
  h.projection.revision += 1;
  assert.doesNotMatch(h.markup(), /Dock &lt;console&gt;/);
  assert.equal(h.executions.length, 0);
  assert.equal(h.previews.length, 0);
});

test('a selected function uses its own preview or reason, never a different network action', () => {
  const surface = networkSurface();
  surface.data.nodes[0].functions = [{ id: 'open', label: 'Open gate', enabled: true }];
  surface.actions[0].bindings.remote_function = 'remote_function';
  surface.actions.unshift({ id: 'discover', label: 'Discover', available: true,
    bindings: { actor: 'actor', node_id: 'node' } });
  const h = harness(surface);
  h.api.applyPanelSelection(surface.id, { actor: 'alpha', node: 'controller', remote_function: 'open' });
  const card = value => ({ version: 1, title: 'Network access', details: [], actions: [],
    summary: [{ label: 'Access', value, emphasis: false }] });
  const discoveryKey = h.api.panelActionCheckKey(surface, surface.actions[0]);
  const operationKey = h.api.panelActionCheckKey(surface, surface.actions[1]);
  h.api.state.panelActionChecks.set(discoveryKey, { available: true, preview: card('Unrelated discovery') });
  h.api.state.panelActionChecks.set(operationKey, { available: true, preview: card('Function permitted') });
  assert.match(h.markup(), /Function permitted/);
  assert.doesNotMatch(h.markup(), /Unrelated discovery/);
  h.api.state.panelActionChecks.set(operationKey, {
    available: false, reason: 'Need <operator> access', preview: card('STALE permission'),
  });
  assert.match(h.markup(), /Need &lt;operator&gt; access/);
  assert.doesNotMatch(h.markup(), /Unrelated discovery|STALE permission/);
  h.projection.revision += 1;
  assert.doesNotMatch(h.markup(), /Need &lt;operator&gt; access|Function permitted/);
  assert.equal(h.executions.length, 0);
});

test('network actor survives graph and panel-window refreshes only while control remains valid', () => {
  const h = harness();
  h.actorSelect.listeners.get('change')({ target: { value: 'bravo' } });
  h.surface.data = { ...h.surface.data, graph_revision: 5, selection: { node: 'controller' } };
  assert.equal(h.api.networkSelection(h.surface).actor, 'bravo');

  // applyPanelSelection is the main-window normalizer called by an extra window's selection RPC.
  h.api.applyPanelSelection(h.surface.id, { actor: 'alpha' });
  assert.equal(h.api.networkSelection(h.surface).actor, 'alpha');
  h.api.applyPanelSelection(h.surface.id, { actor: 'missing' });
  assert.equal(h.api.networkSelection(h.surface).actor, 'alpha');

  h.api.state.panelProjection.player_control = {
    available: true, focused_actor_id: 'bravo', actors: [{ id: 'bravo', name: 'Bram' }],
  };
  const markup = h.markup();
  assert.equal(h.api.networkSelection(h.surface).actor, null);
  assert.match(markup, /data-panel-action-id="operate" disabled/);
  assert.doesNotMatch(markup, /option value="bravo" selected/);

  h.api.applyPanelSelection(h.surface.id, { actor: 'bravo' });
  h.api.state.panelProjection.player_control = null;
  assert.equal(h.api.networkSelection(h.surface).actor, null);
  h.api.applyPanelSelection(h.surface.id, { actor: 'bravo' });
  assert.equal(h.api.networkSelection(h.surface).actor, null);
  assert.match(h.markup(), /data-panel-action-id="operate" disabled/);
});

test('network discovery preserves a still-visible device without reusing its old preview', () => {
  const surface = networkSurface();
  surface.data.selection = {};
  surface.data.nodes[0].functions = [{ id: 'open', label: 'Open gate', enabled: true }];
  const h = harness(surface);
  h.api.applyPanelSelection(surface.id, { actor: 'alpha', node: 'controller', remote_function: 'open' });
  const oldKey = h.api.panelActionCheckKey(surface, surface.actions[0]);
  h.api.state.panelActionChecks.set(oldKey, { available: true,
    preview: { version: 1, title: 'Network access', details: [], actions: [],
      summary: [{ label: 'Route', value: 'Old route', emphasis: false }] } });
  surface.data = { ...surface.data, graph_revision: 5,
    nodes: [...surface.data.nodes, { id: 'discovered', label: 'New device', functions: [] }] };
  h.projection.revision += 1;
  const selection = h.api.networkSelection(surface);
  assert.equal(selection.node, 'controller');
  assert.equal(selection.remote_function, 'open');
  assert.equal(selection.graph_revision, 5);
  assert.doesNotMatch(h.markup(), /Old route/);
  assert.equal(h.executions.length, 0);

  surface.data = { ...surface.data, graph_revision: 6, nodes: [surface.data.nodes[1]] };
  assert.equal(h.api.networkSelection(surface).node, undefined);
  assert.equal(h.api.networkSelection(surface).remote_function, undefined);
  assert.equal(h.api.networkSelection(surface).actor, 'alpha');
});

test('network refresh retains a valid route but drops a changed link or a different graph', () => {
  const surface = networkSurface();
  surface.data.graph_id = 'office';
  surface.data.selection = {};
  surface.data.nodes.push({ id: 'entry', label: 'Entry', functions: [] });
  surface.data.links = [{ id: 'lan', source: 'entry', target: 'controller', direction: 'bidirectional' }];
  const h = harness(surface);
  h.api.applyPanelSelection(surface.id, { actor: 'alpha', node: 'controller', link: 'lan',
    route_step: ['entry', 'controller', 'lan'] });
  surface.data.graph_revision += 1;
  assert.deepEqual(Array.from(h.api.networkSelection(surface).route_step), ['entry', 'controller', 'lan']);
  surface.data.links = [{ id: 'lan', source: 'controller', target: 'entry', direction: 'directed' }];
  surface.data.graph_revision += 1;
  assert.equal(h.api.networkSelection(surface).route_step, undefined);
  assert.equal(h.api.networkSelection(surface).node, 'controller');

  surface.data.graph_id = 'warehouse';
  const selection = h.api.networkSelection(surface);
  assert.equal(selection.node, undefined);
  assert.equal(selection.link, undefined);
  assert.equal(selection.actor, 'alpha');
});
