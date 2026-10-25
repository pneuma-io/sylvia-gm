const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { create } = require('../src-tauri/frontend/ready-campaigns.js');

class Node {
  constructor() { this.children = []; this.value = ''; this.events = {}; }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  addEventListener(name, action) { this.events[name] = action; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}
const entries = [{ id: 'last-cart-through', content_sha256: 'a'.repeat(64),
  title: 'Last Cart Through', summary: '<script>public hook</script>', party: [
    { name: 'Bram Alder', role: 'player_character', level: 2, classes: [{ id: 'fighter', level: 2 }],
      summary: 'Prepared Fighter', profile: '<img src=x onerror=bad()>' },
    { name: 'Oren Reed', role: 'companion', level: 2, classes: [{ id: 'cleric', level: 2 }], summary: 'Prepared Cleric' },
  ] }];

function harness(overrides = {}) {
  const nodes = new Map();
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  const calls = [];
  const values = new Map();
  const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key) };
  const options = {
    document: { getElementById: node, createElement: () => new Node() }, storage,
    uuid: () => 'stable-id', catalog: async () => entries,
    model: () => ({ provider_id: 'test', model_id: 'test/model' }),
    start: async command => { calls.push(command); return { campaign_id: 'copy', local_ready: true }; },
    customize: async command => { calls.push(command); return { characters: [] }; },
    onStarted: async result => calls.push({ opened: result.campaign_id }),
    onCustomize: async (row, result) => calls.push({ customize: row.id, result }),
    guidance: () => 'Model capability needs confirmation.', error: (label, error) => `${label}: ${error}`,
    ...overrides,
  };
  return { controller: create(options), node, calls, options, values };
}

test('preview displays scenario and complete party as text; browsing creates nothing', async () => {
  const h = harness();
  await h.controller.open();
  assert.equal(h.calls.length, 0);
  assert.equal(h.node('ready-campaign-summary').textContent, entries[0].summary);
  const party = h.node('ready-campaign-party').children;
  assert.equal(party.length, 2);
  assert.match(party[0].textContent, /Bram Alder.*fighter 2.*your character/);
  assert.match(party[1].textContent, /Oren Reed.*cleric 2.*companion/);
  assert.equal(party[0].children[0].children[1].textContent, entries[0].party[0].profile);
});

test('Start keeps provider setup separate and opens only after the local ready receipt', async () => {
  const h = harness({ model: () => null });
  await h.controller.open();
  await h.controller.start();
  assert.equal(h.calls.length, 0);
  assert.match(h.node('ready-campaign-status').textContent, /configured model/);
  h.options.model = () => ({ provider_id: 'test', model_id: 'test/model' });
  await h.controller.start();
  assert.equal(h.calls[0].expected_content_sha256, entries[0].content_sha256);
  assert.equal(h.calls[0].model_guidance_override, false);
  assert.deepEqual(h.calls[1], { opened: 'copy' });
  assert.equal(h.node('ready-campaign-dialog').open, false);
  assert.equal(h.values.size, 0);
});

test('uncertain Start retries and restart use the same operation; warning requires explicit continue', async () => {
  let attempt = 0;
  const h = harness({ start: async command => {
    h.calls.push(command);
    if (++attempt === 1) throw new Error('reply lost');
    if (attempt === 2) return { model_guidance: { status: 'unrated' } };
    return { campaign_id: 'copy', local_ready: true };
  } });
  await h.controller.open();
  await h.controller.start();
  const restarted = create(h.options);
  await restarted.open();
  await restarted.start();
  assert.equal(h.calls[0].operation_id, h.calls[1].operation_id);
  assert.equal(h.calls[1].model_guidance_override, false);
  assert.equal(h.node('ready-campaign-start').textContent, 'Continue with this model');
  await restarted.start();
  assert.equal(h.calls[2].operation_id, h.calls[0].operation_id);
  assert.equal(h.calls[2].model_guidance_override, true);
  assert.equal(h.values.size, 0);
});

test('lost reply restores the chosen adventure, custom name and original model before retry', async () => {
  let identity = 0;
  const debt = { ...entries[0], id: 'a-debt-in-iron', title: 'A Debt in Iron' };
  const h = harness({ catalog: async () => [debt, ...entries], uuid: () => String(++identity),
    start: async command => { h.calls.push(command); throw new Error('reply lost'); } });
  await h.controller.open();
  h.node('ready-campaign-choice').value = 'last-cart-through';
  h.node('ready-campaign-choice').events.change();
  h.node('ready-campaign-name').value = 'My Cart';
  await h.controller.start();
  h.options.model = () => ({ provider_id: 'changed', model_id: 'changed/model' });
  const restarted = create(h.options);
  await restarted.open();
  assert.equal(h.node('ready-campaign-choice').value, 'last-cart-through');
  assert.equal(h.node('ready-campaign-name').value, 'My Cart');
  assert.equal(h.node('ready-campaign-choice').disabled, true);
  assert.equal(h.node('ready-campaign-name').disabled, true);
  assert.equal(h.node('ready-campaign-customize').disabled, true);
  assert.match(h.node('ready-campaign-status').textContent, /resume/i);
  await restarted.start();
  assert.deepEqual(h.calls[1], h.calls[0]);
  assert.equal(identity, 1);
  h.options.start = async command => { h.calls.push(command); return { campaign_id: 'existing', local_ready: true }; };
  await restarted.start();
  assert.equal(h.calls[2].operation_id, h.calls[0].operation_id);
  assert.equal(h.values.size, 0);
});

for (const change of ['stale hash', 'missing template', 'empty catalog']) test(`confirmed no-creation rejection unlocks refreshed choices: ${change}`, async () => {
  let identity = 0;
  const refreshed = change === 'stale hash' ? { ...entries[0], content_sha256: 'b'.repeat(64) }
    : { ...entries[0], id: 'a-debt-in-iron', title: 'A Debt in Iron', content_sha256: 'c'.repeat(64) };
  let catalog = entries;
  const h = harness({ catalog: async () => catalog, uuid: () => String(++identity),
    start: async command => { h.calls.push(command); throw new Error('reply lost'); } });
  await h.controller.open();
  h.node('ready-campaign-name').value = 'My Cart';
  await h.controller.start();
  catalog = change === 'empty catalog' ? [] : [refreshed];
  const restarted = create(h.options);
  await restarted.open();
  h.options.start = async command => {
    h.calls.push(command);
    throw 'ready_campaign_not_created: This template is unavailable; no campaign was created';
  };
  await restarted.start();
  assert.equal(h.calls[1].operation_id, h.calls[0].operation_id);
  assert.equal(h.calls[1].expected_content_sha256, 'a'.repeat(64));
  assert.equal(h.values.size, 0);
  for (const id of ['ready-campaign-choice', 'ready-campaign-name']) {
    assert.equal(h.node(id).disabled, false);
  }
  assert.match(h.node('ready-campaign-status').textContent, /no campaign was created/i);
  if (change === 'empty catalog') {
    assert.equal(h.node('ready-campaign-customize').disabled, true);
    catalog = [refreshed];
    await restarted.open();
  }
  assert.equal(h.node('ready-campaign-customize').disabled, false);
  assert.equal(h.node('ready-campaign-choice').value, refreshed.id);
  if (change === 'stale hash') assert.equal(h.node('ready-campaign-name').value, 'My Cart');
  h.options.start = async command => { h.calls.push(command); return { campaign_id: 'new', local_ready: true }; };
  await restarted.start();
  assert.notEqual(h.calls[2].operation_id, h.calls[0].operation_id);
  assert.equal(h.calls[2].expected_content_sha256, refreshed.content_sha256);
  assert.equal(h.calls[2].template_id, refreshed.id);
});

test('generic validation or transport errors cannot discard an uncertain command', async () => {
  const h = harness({ start: async () => { throw new Error('transport: ready_campaign_not_created: untrusted text'); } });
  await h.controller.open();
  await h.controller.start();
  assert.equal(h.values.size, 1);
  assert.equal(h.node('ready-campaign-choice').disabled, true);
  for (const code of ['ready_campaign_invalid', 'ready_campaign_unavailable']) {
    h.options.start = async () => { throw { code }; };
    await h.controller.start();
    assert.equal(h.values.size, 1);
    assert.equal(h.node('ready-campaign-customize').disabled, true);
  }
});

test('a late successful Start opens its saved copy after a transient retry error', async () => {
  let finish;
  const h = harness({ start: () => new Promise(resolve => { finish = resolve; }) });
  await h.controller.open();
  const earlier = h.controller.start();
  const resumed = create(h.options);
  await resumed.open();
  h.options.start = async () => { throw { code: 'ready_campaign_unavailable' }; };
  await resumed.start();
  assert.equal(h.values.size, 1);
  assert.equal(h.node('ready-campaign-choice').disabled, true);
  finish({ campaign_id: 'saved', local_ready: true });
  await earlier;
  assert.equal(h.values.size, 0);
  assert.deepEqual(h.calls, [{ opened: 'saved' }]);
});

test('Customize requests independent copies and hands them to ordinary setup', async () => {
  const h = harness();
  await h.controller.open();
  await h.controller.customize();
  assert.equal(h.calls[0].template_id, 'last-cart-through');
  assert.equal(h.calls[1].customize, 'last-cart-through');
  assert.equal(h.node('ready-campaign-dialog').open, false);
});

for (const reload of [false, true]) test(`lost Customize reply resumes the same saved party after reopening: ${reload}`, async () => {
  let identity = 0;
  const saved = new Map();
  const h = harness({ uuid: () => String(++identity), customize: async command => {
    h.calls.push(command);
    if (!saved.has(command.operation_id)) saved.set(command.operation_id, { characters: [{ name: 'Bram' }] });
    if (h.calls.length === 1) throw new Error('reply lost after saving the party');
    return saved.get(command.operation_id);
  } });
  await h.controller.open();
  await h.controller.customize();
  h.node('ready-campaign-close').events.click();
  const resumed = reload ? create(h.options) : h.controller;
  await resumed.open();
  await resumed.customize();
  assert.deepEqual(h.calls[1], h.calls[0], 'an uncertain save must retain the original operation and content binding');
  assert.equal(saved.size, 1, 'reopening must not save another independent party');
  assert.equal(identity, 1);
  assert.equal(h.values.size, 0);
});

test('uncertain customization cannot be replaced by Start or a different adventure', async () => {
  const h = harness({ customize: async command => { h.calls.push(command); throw new Error('reply lost'); } });
  await h.controller.open();
  await h.controller.customize();
  for (const id of ['ready-campaign-choice', 'ready-campaign-name', 'ready-campaign-start']) {
    assert.equal(h.node(id).disabled, true);
  }
  assert.equal(h.node('ready-campaign-customize').disabled, false);
  await h.controller.start();
  assert.equal(h.calls.length, 1);
  assert.match(h.node('ready-campaign-status').textContent, /retry uses the same party copies/);
});

test('customization retry retains its original template binding when the catalog changes', async () => {
  const h = harness({ customize: async command => { h.calls.push(command); throw new Error('reply lost'); } });
  await h.controller.open();
  await h.controller.customize();
  h.options.catalog = async () => [];
  h.options.customize = async command => { h.calls.push(command); return { characters: [] }; };
  const resumed = create(h.options);
  await resumed.open();
  assert.equal(h.node('ready-campaign-start').disabled, true);
  assert.equal(h.node('ready-campaign-customize').disabled, false);
  assert.match(h.node('ready-campaign-status').textContent, /previous customization of Last Cart Through/);
  await resumed.customize();
  assert.deepEqual(h.calls[1], h.calls[0]);
  assert.equal(h.calls[2].customize, 'last-cart-through');
  assert.equal(h.values.size, 0);
});

test('unavailable browser storage still preserves an in-memory customization retry across chooser reopening', async () => {
  let identity = 0;
  const h = harness({ uuid: () => String(++identity), storage: {
    getItem() { throw new Error('storage unavailable'); },
    setItem() { throw new Error('storage unavailable'); },
    removeItem() { throw new Error('storage unavailable'); },
  }, customize: async command => { h.calls.push(command); throw new Error('reply lost'); } });
  await h.controller.open();
  await h.controller.customize();
  h.node('ready-campaign-close').events.click();
  await h.controller.open();
  await h.controller.customize();
  assert.deepEqual(h.calls[1], h.calls[0]);
  assert.equal(identity, 1);
});

test('the real customization handoff cannot report success when interview creation failed', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const start = source.indexOf('      onCustomize: async (row, result');
  const end = source.indexOf('\n    });', start);
  const statuses = [];
  const callback = vm.runInNewContext(`({${source.slice(start, end)}}).onCustomize`, {
    startCampaignBuilder: async () => undefined,
    setInlineStatus: text => statuses.push(text),
  });
  const h = harness({ onCustomize: callback });
  await h.controller.open();
  await h.controller.customize();
  assert.equal(h.values.size, 1, 'failed setup must retain its party operation for retry');
  assert.equal(h.node('ready-campaign-dialog').open, true, 'the failed handoff must leave visible retry controls');
  assert.deepEqual(statuses, [], 'do not overwrite the actual draft failure with success instructions');
});

test('lost interview-creation reply retries the real customization handoff with the same draft operation', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const saved = new Map(), calls = [];
  let identity = 0;
  const context = {
    state: { bridge: { create_guided_campaign_draft: async command => {
      calls.push(command);
      if (!saved.has(command.operation_id)) saved.set(command.operation_id, {
        campaign_id: `draft-${saved.size}`, builder_session_id: 'builder', construction_revision: 0,
      });
      if (calls.length === 1) throw new Error('reply lost after saving draft');
      return saved.get(command.operation_id);
    } } },
    defaultModelChoice: () => null, crypto: { randomUUID: () => String(++identity) },
    hideConstructionPanel() {}, setInlineStatus() {}, refreshCampaignIndex: async () => {},
    openCampaign: async () => {}, $: () => ({}), friendlyCommandError: (label, error) => `${label}: ${error}`,
  };
  const start = source.indexOf('  async function startCampaignBuilder(');
  const end = source.indexOf('  async function beginConstruction()', start);
  vm.runInNewContext(source.slice(start, end), context);
  const callbackStart = source.indexOf('      onCustomize: async (row, result');
  const callbackEnd = source.indexOf('\n    });', callbackStart);
  const callback = vm.runInNewContext(`({${source.slice(callbackStart, callbackEnd)}}).onCustomize`, context);
  const h = harness({ onCustomize: callback,
    customizationModel: () => ({ provider_id: 'original', model_id: 'original/model' }) });
  await h.controller.open();
  await h.controller.customize();
  h.options.customizationModel = () => ({ provider_id: 'changed', model_id: 'changed/model' });
  const resumed = create(h.options);
  await resumed.open();
  await resumed.customize();
  assert.equal(calls[1].operation_id, calls[0].operation_id);
  assert.deepEqual(calls[1], calls[0], 'retry must preserve the original draft model binding');
  assert.equal(saved.size, 1, 'a lost reply must not create another campaign interview');
  assert.equal(h.values.size, 0);
});

test('shipped page, bridge and explicit frozen inventory contain the ready path', () => {
  const root = path.join(__dirname, '..');
  const html = fs.readFileSync(path.join(root, 'src-tauri/frontend/app.html'), 'utf8');
  const source = fs.readFileSync(path.join(root, 'src-tauri/frontend/app.js'), 'utf8');
  const rust = fs.readFileSync(path.join(root, 'src-tauri/src/lib.rs'), 'utf8');
  const data = JSON.parse(fs.readFileSync(path.join(root, 'src-tauri/sidecar-data-files.json'), 'utf8'));
  for (const id of ['home-ready-campaign-btn', 'ready-campaign-dialog', 'ready-campaign-choice',
    'ready-campaign-name', 'ready-campaign-party', 'ready-campaign-start', 'ready-campaign-customize',
    'ready-campaign-close', 'ready-campaign-status', 'ready-campaign-summary']) assert.ok(html.includes(`id="${id}"`));
  for (const command of ['ready_campaign_catalog', 'start_ready_campaign', 'customize_ready_campaign']) {
    assert.ok(source.includes(command));
    assert.ok(rust.includes(`async fn ${command}(`));
  }
  assert.ok(data.files.includes('src/sylvia_gm/campaign/ready-campaigns.json'));
});

for (const navigated of [false, true]) test(`an interrupted opening preserves its campaign and selected chat: ${navigated}`, async () => {
  const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const start = source.indexOf('  async function openReadyCampaign(result) {');
  const end = source.indexOf('  async function startCampaignBuilder(', start);
  const finishes = [], statuses = [];
  const context = {
    state: { view: 'home', activeCampaignId: null, activeSessionId: null,
      bridge: { start_dm_session: async () => ({ interruption: { code: 'scripted' } }) } },
    refreshCampaignIndex: async () => {
      if (navigated) Object.assign(context.state, { view: 'session', activeCampaignId: 'other', activeSessionId: 'other-chat' });
    },
    openCampaign: async (campaign, session) => {
      Object.assign(context.state, { view: 'session', activeCampaignId: campaign, activeSessionId: session });
    },
    setInlineStatus: text => statuses.push(text), selectedStatus() {},
    ensureSessionActivity: value => value, beginSessionActivity: value => value,
    refreshActivityControls() {}, renderActivityIndexes() {}, startProjectionPolling() {},
    startStreamHeartbeat() {}, stopStreamHeartbeat() {}, stopProjectionPolling() {},
    finishSessionActivity: (_activity, phase) => finishes.push(phase),
    selectedActivityMatches: () => context.state.activeSessionId === 'dm',
    refreshSelectedCampaign: async () => {},
    friendlyCommandError: (label, error) => `${label}: ${error}`,
  };
  vm.runInNewContext(source.slice(start, end), context);
  await context.openReadyCampaign({ campaign_id: 'saved', dm_session_id: 'dm',
    dm_turn_id: 'turn', dm_attempt_id: 'opening' });
  assert.deepEqual(finishes, ['interrupted']);
  if (navigated) {
    assert.equal(context.state.activeSessionId, 'other-chat');
    assert.deepEqual(statuses, []);
  } else assert.match(statuses.at(-1), /campaign is saved/);
});
