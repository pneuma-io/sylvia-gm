// Exercise the actual panel click and confirmation flow with synthetic owner replies.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { webcrypto } = require('node:crypto');

function element() {
  const listeners = new Map();
  return {
    listeners, dataset: {}, style: { setProperty(name, value) { this[name] = value; } },
    children: [], innerHTML: '', hidden: false, disabled: false,
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener: (name, handler) => listeners.set(name, handler),
    querySelectorAll: () => [], querySelector: () => null, setAttribute() {},
    appendChild(child) { child.parentElement = this; this.children.push(child); },
    remove() { this.parentElement.children = this.parentElement.children.filter((x) => x !== this); },
    showModal() { this.open = true; },
    close(value) { this.returnValue = value; this.open = false; listeners.get('close')(); },
  };
}

function harness(required = true, proposal = {}, values = new Map()) {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  const body = element();
  const button = element();
  button.dataset.panelActionId = 'move';
  button.closest = () => ({ dataset: { panelId: 'map' } });
  node('ruleset-panels').querySelectorAll = (selector) =>
    selector === '[data-panel-action-id]' ? [button] : [];
  const context = {
    window: {}, crypto: webcrypto, console,
    document: { body, getElementById: node, addEventListener() {}, createElement: element },
    localStorage: { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) },
    sessionStorage: { getItem: () => null, setItem() {} },
  };
  let source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const insertion = source.lastIndexOf('  return {');
  source = source.slice(0, insertion) + `
    window.panelTest = { state, ensureSessionActivity, renderRulesetPanelProjection, pendingPanelCommand, recoverPanelAction };
    refreshSelectedCampaign = async () => {};
    renderRulesetPanels = async () => {};
  ` + source.slice(insertion);
  source = ['panel-workspace.js', 'shot-playback.js', 'panel-host.js'].map((name) =>
    fs.readFileSync(path.join(__dirname, '../src-tauri/frontend', name), 'utf8')).join('\n') + '\n' + source;
  context.setTimeout ||= setTimeout;
  context.clearTimeout ||= clearTimeout;
  context.window.innerWidth = 1400;
  vm.runInNewContext(source, context);
  const api = context.window.panelTest;
  Object.assign(api.state, {
    activeCampaignId: 'campaign', activeSessionId: 'gm', activeSnapshot: { revision: 0 }, view: 'session',
  });
  api.ensureSessionActivity({ sessionId: 'gm', sessionRole: 'dm', campaignId: 'campaign' });
  const proposals = [], executions = [];
  api.state.bridge = {
    snapshot: async () => ({ revision: 7 }),
    preview_panel_action: async (command) => {
      proposals.push(command);
      return {
        confirmation_required: required, confirmation: 'exact-request',
        label: 'Move <operator>', selection: { destination: [4, 4], map_revision: 7 },
        preview: null, ...proposal,
      };
    },
    execute_panel_action: async (command) => { executions.push(command); return { status: 'committed' }; },
  };
  api.renderRulesetPanelProjection({ surfaces: [{
    id: 'map', title: 'Map', renderer: 'generic', data: {},
    actions: [{ id: 'move', label: 'Move', params: {} }],
  }] });
  return { api, body, button, proposals, executions, click: () => button.listeners.get('click')() };
}

test('cancel closes the preview without executing or leaving the button disabled', async () => {
  const h = harness();
  const pending = h.click();
  await new Promise(setImmediate);
  const dialog = h.body.children[0];
  assert(dialog.open);
  assert(dialog.innerHTML.includes('Move &lt;operator&gt;'));
  assert(!dialog.innerHTML.includes('map revision'));
  dialog.close('cancel');
  await pending;
  assert.equal(h.executions.length, 0);
  assert.equal(h.body.children.length, 0);
  assert.equal(h.button.disabled, false);
});

for (const selection of [{ actor: 'participant-internal', destination: [4, 4] }, {}]) {
  test(`confirmation identifies the actor with ${Object.keys(selection).length ? 'panel' : 'card'} selection`, async () => {
    const h = harness(true, {
      selection, actor: { id: 'participant-internal', name: 'Vesper <Scout>' },
    });
    const pending = h.click();
    await new Promise(setImmediate);
    const dialog = h.body.children[0];
    assert(dialog.innerHTML.includes('Vesper &lt;Scout&gt;'));
    assert(!dialog.innerHTML.includes('participant-internal'));
    if (selection.destination) assert(dialog.innerHTML.includes('[4,4]'));
    dialog.close('cancel');
    await pending;
    assert.equal(h.executions.length, 0);
  });
}

test('confirm retains the prepared identity and revision even after a display refresh', async () => {
  const h = harness();
  const pending = h.click();
  await new Promise(setImmediate);
  h.api.state.activeSnapshot = { revision: 8 };
  h.body.children[0].close('confirm');
  await pending;
  assert.equal(h.executions.length, 1);
  assert.equal(h.executions[0].expected_revision, 7);
  assert.equal(h.executions[0].operation_id, h.proposals[0].operation_id);
  assert.equal(h.executions[0].confirmation, 'exact-request');
  assert.equal(h.executions[0].session_id, 'gm');
});

test('a session change during confirmation cannot execute in either campaign', async () => {
  const h = harness();
  const pending = h.click();
  await new Promise(setImmediate);
  h.api.state.activeSessionId = 'another-gm';
  h.body.children[0].close('confirm');
  await pending;
  assert.equal(h.executions.length, 0);
});

test('Ruleset immediate policy executes without a confirmation dialog', async () => {
  const h = harness(false);
  await h.click();
  assert.equal(h.executions.length, 1);
  assert.equal(h.body.children.length, 0);
});

test('an uncertain committed send survives reload and receipt recovery never executes again', async () => {
  const storage = new Map(), h = harness(false, {}, storage);
  let command;
  h.api.state.bridge.execute_panel_action = async (value) => {
    command = JSON.parse(JSON.stringify(value)); h.executions.push(value);
    throw new Error('Synthetic reply lost after commit');
  };
  await h.click();
  assert.equal(h.executions.length, 1);
  assert.equal(h.api.pendingPanelCommand().operation_id, command.operation_id);
  const reopened = harness(false, {}, storage);
  assert.equal(reopened.api.pendingPanelCommand().operation_id, command.operation_id);
  reopened.api.state.bridge.panel_action_receipt = async (value) => {
    assert.deepEqual(JSON.parse(JSON.stringify(value)), command);
    return {status:'committed',action_id:command.operation_id};
  };
  await reopened.api.recoverPanelAction();
  assert.equal(reopened.executions.length, 0);
  assert.equal(reopened.api.pendingPanelCommand(), null);
  assert.equal(storage.size, 0);
});

test('an absent receipt offers only the original operation for explicit retry', async () => {
  const storage = new Map(), h = harness(false, {}, storage);
  h.api.state.bridge.execute_panel_action = async value => { h.executions.push(value); throw new Error('Synthetic send uncertain'); };
  await h.click(); const original = JSON.parse(JSON.stringify(h.api.pendingPanelCommand()));
  h.api.state.bridge.panel_action_receipt = async () => null;
  await h.api.recoverPanelAction();
  assert.equal(h.api.state.panelRecoveryAbsent, original.operation_id);
  h.api.state.bridge.execute_panel_action = async value => { assert.deepEqual(JSON.parse(JSON.stringify(value)),original); return {status:'committed'}; };
  await h.api.recoverPanelAction(true);
  assert.equal(h.api.pendingPanelCommand(), null);
});
