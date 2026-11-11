// The model, effort and speed a new chat starts with: Settings defaults, the choice shown
// before an automatic first model call, and the paths that must not send when it is cancelled.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const chatStartModule = require('../src-tauri/frontend/chat-start.js');
const readyCampaigns = require('../src-tauri/frontend/ready-campaigns.js');

const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');

// Values built inside the renderer sandbox belong to another realm; compare their content.
const plain = value => JSON.parse(JSON.stringify(value));

// Take one real function out of the renderer so it runs against explicit test doubles.
function slice(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start + 1);
  assert.ok(start >= 0 && end > start, `missing renderer section: ${startMarker}`);
  return source.slice(start, end);
}

const fastModel = {
  provider_id: 'chatgpt', provider_name: 'ChatGPT', model_id: 'chatgpt/gpt-6.1-sol', model_name: 'GPT-6.1 Sol',
  supported_reasoning_efforts: ['low', 'medium', 'high', 'xhigh'],
  reasoning_mode_defaults: { dm: 'high', builder: 'xhigh' },
  speed_options: [{ id: 'priority', name: 'Fast', description: '2x speed, increased usage' }],
};
const plainModel = {
  provider_id: 'local', provider_name: 'Local', model_id: 'local/small', model_name: 'Small',
  supported_reasoning_efforts: null, reasoning_mode_defaults: { dm: null, builder: null },
  speed_options: null,
};

class Node {
  constructor(document) { this.ownerDocument = document; this.children = []; this.value = ''; this.events = {}; this.textContent = ''; }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  addEventListener(name, action) { this.events[name] = action; }
  showModal() { this.open = true; }
  close() { if (!this.open) return; this.open = false; this.events.close?.(); }
  focus() { this.focused = true; }
  options() { return this.children.map(child => [child.value, child.textContent]); }
}

function chooser(models = [fastModel, plainModel], defaults = {}) {
  const nodes = new Map();
  const document = { getElementById: id => { if (!nodes.has(id)) nodes.set(id, new Node(document)); return nodes.get(id); },
    createElement: () => new Node(document) };
  const controller = chatStartModule.create({ document, models: () => models, defaults: () => defaults });
  return { controller, node: document.getElementById };
}

test('effort and speed options come only from the model capability data', () => {
  assert.deepEqual(chatStartModule.effortOptions(fastModel, 'dm'), [
    ['mode_default', 'Mode default (High)'], ['provider_default', 'Provider default'],
    ['low', 'Low'], ['medium', 'Medium'], ['high', 'High'], ['xhigh', 'X-High'],
  ]);
  assert.equal(chatStartModule.effortOptions(fastModel, null)[0][1], 'Mode default (Game Master High · Builder X-High)');
  assert.deepEqual(chatStartModule.effortOptions(plainModel, 'builder'), [
    ['mode_default', 'Mode default (Provider default)'], ['provider_default', 'Provider default'],
  ]);
  assert.deepEqual(chatStartModule.speedOptions(fastModel), [['standard', 'Standard'], ['priority', 'Fast']]);
  assert.deepEqual(chatStartModule.speedOptions(plainModel), [['standard', 'Standard']]);
});

test('a new chat inherits Settings defaults only where its model supports them', () => {
  const defaults = { reasoning_effort: 'xhigh', speed: 'priority' };
  assert.deepEqual(chatStartModule.inherited(fastModel, defaults), { reasoning_effort: 'xhigh', speed: 'priority' });
  assert.deepEqual(chatStartModule.inherited(plainModel, defaults), { reasoning_effort: 'mode_default', speed: 'standard' });
  assert.deepEqual(chatStartModule.inherited(fastModel, {}), { reasoning_effort: 'mode_default', speed: 'standard' });
  assert.deepEqual(chatStartModule.inherited(plainModel, { reasoning_effort: 'provider_default' }),
    { reasoning_effort: 'provider_default', speed: 'standard' });
  assert.equal(chatStartModule.summary(fastModel, { reasoning_effort: 'xhigh', speed: 'priority' }, 'dm'),
    'GPT-6.1 Sol · X-High · Fast speed');
});

test('speed wording states what is requested without promising a faster reply', () => {
  assert.match(chatStartModule.speedNote(plainModel, 'standard'), /no speed choice/);
  assert.match(chatStartModule.speedNote(fastModel, 'standard'), /sends no speed setting/);
  const fast = chatStartModule.speedNote(fastModel, 'priority');
  assert.match(fast, /^Fast: 2x speed, increased usage/);
  assert.match(fast, /the provider decides the speed it actually uses/);
  assert.match(chatStartModule.speedNote(fastModel, 'flex'), /not available for this model/);
});

test('the start choice shows inherited values and returns exactly what was picked', async () => {
  const h = chooser([fastModel, plainModel], { reasoning_effort: 'low', speed: 'priority' });
  const pending = h.controller.choose({ role: 'dm', title: 'Start Game Master', action: 'Begin adventure', model: fastModel });
  assert.equal(h.node('chat-start-dialog').open, true);
  assert.equal(h.node('chat-start-title').textContent, 'Start Game Master');
  assert.equal(h.node('chat-start-confirm').textContent, 'Begin adventure');
  assert.equal(h.node('chat-start-model').value, '0');
  assert.equal(h.node('chat-start-effort').value, 'low');
  assert.equal(h.node('chat-start-speed').value, 'priority');
  assert.equal(h.node('chat-start-effort').options()[0][1], 'Mode default (High)');
  h.node('chat-start-effort').value = 'xhigh';
  h.node('chat-start-speed').value = 'standard';
  h.node('chat-start-confirm').events.click();
  assert.deepEqual(await pending, { provider_id: 'chatgpt', model_id: 'chatgpt/gpt-6.1-sol',
    reasoning_effort: 'xhigh', speed: 'standard' });
  assert.equal(h.node('chat-start-dialog').open, false);
});

test('changing the model keeps supported picks and drops unsupported ones', async () => {
  const h = chooser([fastModel, plainModel], { reasoning_effort: 'low', speed: 'priority' });
  const pending = h.controller.choose({ role: 'builder', title: 'Start Builder chat', action: 'Start Builder', model: fastModel });
  h.node('chat-start-model').value = '1';
  h.node('chat-start-model').events.change();
  assert.equal(h.node('chat-start-effort').value, 'mode_default');
  assert.equal(h.node('chat-start-speed').value, 'standard');
  assert.equal(h.node('chat-start-speed').disabled, true);
  assert.match(h.node('chat-start-speed-note').textContent, /no speed choice/);
  h.node('chat-start-effort').value = 'provider_default';
  h.node('chat-start-model').value = '0';
  h.node('chat-start-model').events.change();
  assert.equal(h.node('chat-start-effort').value, 'provider_default', 'a still-valid pick survives a model change');
  assert.equal(h.node('chat-start-speed').disabled, false);
  h.node('chat-start-confirm').events.click();
  assert.deepEqual(await pending, { provider_id: 'chatgpt', model_id: 'chatgpt/gpt-6.1-sol',
    reasoning_effort: 'provider_default', speed: 'standard' });
});

test('Cancel, Escape and a second open all resolve without a start', async () => {
  const h = chooser();
  const first = h.controller.choose({ role: 'dm', title: 'T', action: 'A', model: fastModel });
  assert.equal(await h.controller.choose({ role: 'dm', title: 'T', action: 'A', model: fastModel }), null,
    'a second request while open must not replace or duplicate the first');
  h.node('chat-start-cancel').events.click();
  assert.equal(await first, null);
  const second = h.controller.choose({ role: 'dm', title: 'T', action: 'A', model: fastModel });
  h.node('chat-start-dialog').close(); // native Escape closes the dialog
  assert.equal(await second, null);
  const reopened = h.controller.choose({ role: 'dm', title: 'T', action: 'A', model: plainModel });
  assert.equal(h.node('chat-start-model').value, '1');
  h.node('chat-start-confirm').events.click();
  assert.equal((await reopened).model_id, 'local/small');
  assert.equal(await chooser([]).controller.choose({ role: 'dm', title: 'T', action: 'A' }), null);
});

// ── Renderer paths whose first model call starts by itself ───────────────────────────────

function chooseStub(answer, seen = []) {
  return async (request) => { seen.push(request); return typeof answer === 'function' ? answer(request) : answer; };
}
const chosen = { provider_id: 'chatgpt', model_id: 'chatgpt/gpt-6.1-sol', reasoning_effort: 'xhigh', speed: 'priority' };

test('chooseChatStart never blocks a start that has nothing to choose', async () => {
  const code = slice('  async function chooseChatStart(request) {', '  function chatModelModeLabel() {');
  const make = (chatStart, models) => {
    const context = { state: { modelChoices: models }, chatStart, defaultModelChoice: () => models[0] || null };
    vm.runInNewContext(code, context);
    return context.chooseChatStart;
  };
  assert.deepEqual(plain(await make(null, [])({ role: 'builder' })), {}, 'no configured model: proceed unchanged');
  assert.deepEqual(plain(await make(null, [fastModel])({ role: 'builder' })),
    { provider_id: 'chatgpt', model_id: 'chatgpt/gpt-6.1-sol' }, 'no chooser: Settings defaults apply');
  const asked = [];
  const viaChooser = make({ choose: async (request) => { asked.push(request); return null; } }, [fastModel]);
  assert.equal(await viaChooser({ role: 'dm', model: plainModel }), null);
  assert.equal(asked[0].model, plainModel);
});

for (const cancelled of [true, false]) {
  test(`import repair ${cancelled ? 'sends nothing when the start is cancelled' : 'starts with the chosen settings'}`, async () => {
    const calls = [], status = [], asked = [];
    const button = { disabled: false, hidden: false };
    const context = {
      crypto: webcrypto,
      state: { pendingImportRepair: { repair_id: 'r'.repeat(64) }, bridge: {
        start_import_repair: async (command) => { calls.push(command); return { session: { session_id: 'repair-chat' } }; } } },
      $: () => button, defaultModelChoice: () => fastModel, setPackageInstallStatus: text => status.push(text),
      openSettings() {}, closeSettings() {}, refreshBuilderChats: async () => {}, openBuilderChat: async () => {},
      submitInput: async (text, options) => calls.push({ submit: options }),
      chooseChatStart: chooseStub(cancelled ? null : chosen, asked),
      friendlyCommandError: (label, error) => `${label}: ${error.message}`,
    };
    vm.runInNewContext(slice('  async function startImportRepair() {', '  function rulesetPackageName('), context);
    await context.startImportRepair();
    assert.equal(asked.length, 1);
    assert.equal(asked[0].role, 'builder');
    if (cancelled) {
      assert.deepEqual(calls, []);
      assert.match(status.at(-1), /Nothing was sent to the model/);
      assert.ok(context.state.pendingImportRepair, 'the failed import stays available to repair later');
    } else {
      assert.equal(calls.length, 2);
      assert.equal(calls[0].provider_id, 'chatgpt');
      assert.equal(calls[0].model_id, 'chatgpt/gpt-6.1-sol');
      assert.equal(calls[0].reasoning_effort, 'xhigh');
      assert.equal(calls[0].speed, 'priority');
      assert.deepEqual(plain(calls[1]), { submit: { builderStart: true, builderSessionId: 'repair-chat' } });
    }
    assert.equal(button.disabled, false);
  });
}

function subflowContext(answer) {
  const calls = [], status = [], asked = [];
  const context = {
    crypto: webcrypto, calls, status, asked,
    state: { constructionDraftId: 'draft', activeSessionId: 'interview', constructionRevision: 3,
      builderLaunchPending: new Set(), activeCreationSubflow: null, bridge: {
        start_guided_creation_subflow: async (command) => { calls.push(command);
          return { status: command.purpose === 'campaign_customization' ? 'linked' : 'independent',
            child_session_id: 'child', campaign_id: 'draft', construction_revision: 4 }; } } },
    updateCharacterProfileControls() {}, setInlineStatus: text => status.push(text),
    selectedModelChoice: () => fastModel, defaultModelChoice: () => fastModel,
    chooseChatStart: chooseStub(answer, asked),
    refreshBuilderChats: async () => {}, openBuilderChat: async () => {}, refreshCampaignIndex: async () => {},
    openCampaign: async () => {}, submitInput: async (text, options) => calls.push({ submit: options }),
    friendlyCommandError: (label, error) => `${label}: ${error.message}`,
  };
  vm.runInNewContext(slice('  async function startCreationSubflow(', '  async function returnCreationSubflow('), context);
  return context;
}

test('a character Builder chat asks first and forwards the chosen settings', async () => {
  const h = subflowContext(chosen);
  await h.startCreationSubflow('companion', 'Create New Companion');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.asked.length, 1);
  assert.equal(h.calls[0].purpose, 'companion');
  assert.equal(h.calls[0].provider_id, 'chatgpt');
  assert.equal(h.calls[0].reasoning_effort, 'xhigh');
  assert.equal(h.calls[0].speed, 'priority');
  assert.deepEqual(plain(h.calls[1]), { submit: { builderStart: true, builderSessionId: 'child' } });
  assert.equal(h.state.builderLaunchPending.size, 0);
});

test('cancelling a character Builder chat creates nothing and can be started again', async () => {
  const h = subflowContext(null);
  await h.startCreationSubflow('player_character', 'Create New Player Character');
  assert.deepEqual(h.calls, []);
  assert.match(h.status.at(-1), /Nothing was sent to the model/);
  assert.equal(h.state.builderLaunchPending.size, 0, 'a cancelled start must not lock later starts');
  h.chooseChatStart = chooseStub(chosen, h.asked);
  await h.startCreationSubflow('player_character', 'Create New Player Character');
  assert.equal(h.calls[0].purpose, 'player_character');
});

test('a linked Builder chat waits for a message, so it starts without asking', async () => {
  const h = subflowContext(chosen);
  await h.startCreationSubflow('campaign_customization', 'Keep Building');
  assert.equal(h.asked.length, 0);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].provider_id, undefined);
  assert.equal(h.calls[0].reasoning_effort, undefined);
});

function constructionContext(answer) {
  const nodes = {};
  for (const id of ['construction-name', 'construction-package', 'construction-boundaries', 'construction-preferences',
    'construction-premise', 'construction-setting', 'rift-archetype', 'construction-template']) nodes[id] = { value: '' };
  nodes['construction-name'].value = 'Start settings';
  nodes['construction-no-requests'] = { checked: true };
  nodes['rift-companions'] = { querySelectorAll: () => [] };
  for (const id of ['begin-construction-btn', 'cancel-construction-btn', 'construction-next-btn', 'construction-back-btn']) {
    nodes[id] = { disabled: false };
  }
  const calls = [], status = [], asked = [];
  const context = {
    calls, status, asked, nodes,
    state: { constructionAnswers: {}, constructionDraftId: 'draft', activeSessionId: 'interview', bridge: {
      start_guided_campaign: async (command) => { calls.push(command); return { campaign_id: 'campaign', builder_session_id: 'interview' }; } } },
    $: id => nodes[id], selectedModelChoice: () => fastModel, setInlineStatus: text => status.push(text),
    campaignPartySelectionProblem: () => null, usesBundledCampaignSetup: () => false,
    selectedCampaignPlan: () => ({ id: 'plan' }), hideConstructionPanel() {}, refreshCampaignIndex: async () => {},
    openCampaign: async () => {}, updateCampaignPartyActionAvailability() {},
    finalizeSelectedConstruction: async (...args) => calls.push({ finalize: args }),
    chooseChatStart: chooseStub(answer, asked), friendlyCommandError: (_label, error) => error.message,
  };
  vm.runInNewContext(slice('  async function beginConstruction() {', '  async function finalizeSelectedConstruction('), context);
  return context;
}

test('Ready to Begin asks for the Game Master start before anything is committed', async () => {
  const cancelled = constructionContext(null);
  await cancelled.beginConstruction();
  assert.equal(cancelled.asked[0].role, 'dm');
  assert.equal(cancelled.asked[0].model, fastModel);
  assert.deepEqual(cancelled.calls, [], 'cancelling must not confirm the campaign or hand off');
  assert.match(cancelled.status.at(-1), /Nothing was sent to the model/);
  assert.equal(cancelled.state.campaignConfirmationPending, undefined);

  const started = constructionContext(chosen);
  await started.beginConstruction();
  assert.equal(started.calls.length, 2);
  assert.equal(started.calls[0].display_name, 'Start settings');
  assert.deepEqual(started.calls[1], { finalize: [false, chosen] });
  assert.equal(started.nodes['begin-construction-btn'].disabled, false);
});

function handoffContext({ answer, guidance = { warning_required: false } }) {
  const calls = [], status = [], asked = [];
  const controls = {};
  const context = {
    calls, status, asked,
    state: { activeCampaignId: 'campaign', activeSessionId: 'interview', activeSessionRole: 'builder',
      modelChoices: [fastModel, plainModel], pendingModelGuidance: null, pendingChatStart: null, bridge: {
        model_guidance: async (...args) => { calls.push({ guidance: args }); return guidance; },
        finalize_campaign_construction: async (...args) => { calls.push({ finalize: args });
          return { handoff: { dm_session_id: 'gm', resulting_campaign_revision: 2 }, dm_attempt_id: 'attempt',
            dm_turn_id: 'turn', plan: { plan_revision: 1 } }; },
        start_dm_session: async (...args) => { calls.push({ opening: args }); return {}; } } },
    $: id => (controls[id] ||= { disabled: false }),
    selectedModelChoice: () => plainModel, setInlineStatus: text => status.push(text),
    chooseChatStart: chooseStub(answer, asked),
    clearModelGuidanceWarning() { context.state.pendingModelGuidance = null; },
    showModelGuidanceWarning(shown, selected) {
      context.state.pendingModelGuidance = { campaign_id: 'campaign', provider_id: selected.provider_id,
        model_id: selected.model_id, guidance: shown };
    },
    hideConstructionPanel() {}, saveSelectedSessionDraft() {}, restoreSelectedSessionDraft() {},
    ensureSessionActivity: value => value, beginSessionActivity: value => value, selectedStatus() {},
    refreshActivityControls() {}, renderActivityIndexes() {}, startProjectionPolling() {},
    startStreamHeartbeat() {}, stopStreamHeartbeat() {}, stopProjectionPolling() {}, finishSessionActivity() {},
    refreshCampaignIndex: async () => {}, selectedActivityMatches: () => true, refreshSelectedCampaign: async () => {},
    friendlyCommandError: (label, error) => `${label}: ${error.message}`,
  };
  vm.runInNewContext(slice('  async function finalizeSelectedConstruction(', '  function selectedIndexEntry()'), context);
  return context;
}

test('the Game Master handoff sends the chosen model, effort and speed, then opens once', async () => {
  const h = handoffContext({ answer: chosen });
  await h.finalizeSelectedConstruction();
  assert.equal(h.asked.length, 1);
  assert.equal(h.asked[0].model, plainModel, 'the chat footer model is offered first, as before');
  assert.deepEqual(h.calls.map(call => Object.keys(call)[0]), ['guidance', 'finalize', 'opening']);
  assert.deepEqual(h.calls[0].guidance, ['campaign', 'chatgpt/gpt-6.1-sol', false]);
  assert.deepEqual(h.calls[1].finalize, ['campaign', 'chatgpt', 'chatgpt/gpt-6.1-sol', false, chosen]);
  assert.deepEqual(h.calls[2].opening, ['campaign', 'gm', 'turn']);
  assert.equal(h.state.campaignHandoffPending, false);
});

test('cancelling the Game Master start sends nothing and leaves the handoff available', async () => {
  const h = handoffContext({ answer: null });
  await h.finalizeSelectedConstruction();
  assert.deepEqual(h.calls, []);
  assert.match(h.status.at(-1), /Nothing was sent to the model/);
  assert.equal(h.state.campaignHandoffPending, false);
  h.chooseChatStart = chooseStub(chosen, h.asked);
  await h.finalizeSelectedConstruction();
  assert.deepEqual(h.calls.map(call => Object.keys(call)[0]), ['guidance', 'finalize', 'opening']);
});

test('a start already chosen at Ready to Begin is not asked for twice', async () => {
  const h = handoffContext({ answer: () => { throw new Error('must not ask again'); } });
  await h.finalizeSelectedConstruction(false, chosen);
  assert.equal(h.asked.length, 0);
  assert.deepEqual(h.calls[1].finalize.at(-1), chosen);
});

test('Continue anyway after a capability warning reuses the start chosen before it', async () => {
  const h = handoffContext({ answer: chosen, guidance: { warning_required: true, status: 'unrated' } });
  await h.finalizeSelectedConstruction();
  assert.deepEqual(h.calls.map(call => Object.keys(call)[0]), ['guidance'], 'a warning stops before the handoff');
  assert.deepEqual(plain(h.state.pendingChatStart), { campaign_id: 'campaign', start: chosen });
  h.chooseChatStart = chooseStub(() => { throw new Error('must not ask again'); }, h.asked);
  await h.finalizeSelectedConstruction(true);
  assert.equal(h.asked.length, 1);
  assert.deepEqual(h.calls.at(-2).finalize, ['campaign', 'chatgpt', 'chatgpt/gpt-6.1-sol', true, chosen]);
  assert.equal(Object.keys(h.calls.at(-1))[0], 'opening');
  assert.equal(h.state.pendingChatStart, null);
});

// ── Ready campaigns ──────────────────────────────────────────────────────────────────────

function readyHarness(model) {
  const nodes = new Map();
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Node(null)); return nodes.get(id); };
  const calls = [], values = new Map();
  const controller = readyCampaigns.create({
    document: { getElementById: node, createElement: () => new Node(null) },
    storage: { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) },
    uuid: () => 'stable-id', model,
    catalog: async () => [{ id: 'cart', content_sha256: 'a'.repeat(64), title: 'Last Cart Through', summary: '', party: [] }],
    start: async (command) => { calls.push(command); return { campaign_id: 'copy', local_ready: true }; },
    customize: async () => ({ characters: [] }), onStarted: async () => calls.push('opened'), onCustomize: async () => {},
    guidance: () => 'warning', error: (label, error) => `${label}: ${error}`,
  });
  return { controller, node, calls, values };
}

test('a ready campaign start carries the chosen Game Master effort and speed', async () => {
  const h = readyHarness(async () => chosen);
  await h.controller.open();
  await h.controller.start();
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[0].provider_id, 'chatgpt');
  assert.equal(h.calls[0].model_id, 'chatgpt/gpt-6.1-sol');
  assert.equal(h.calls[0].reasoning_effort, 'xhigh');
  assert.equal(h.calls[0].speed, 'priority');
  assert.equal(h.calls[1], 'opened');
});

test('an uncertain ready start retries with the same chosen settings', async () => {
  const h = readyHarness(async () => chosen);
  let attempts = 0;
  const controller = readyCampaigns.create({
    document: { getElementById: h.node, createElement: () => new Node(null) },
    storage: { getItem: key => h.values.get(key), setItem: (key, value) => h.values.set(key, value), removeItem: key => h.values.delete(key) },
    uuid: () => 'stable-id', model: async () => { attempts += 1; return chosen; },
    catalog: async () => [{ id: 'cart', content_sha256: 'a'.repeat(64), title: 'Last Cart Through', summary: '', party: [] }],
    start: async (command) => { h.calls.push(command); if (h.calls.length === 1) throw new Error('lost reply'); return { campaign_id: 'copy', local_ready: true }; },
    customize: async () => ({}), onStarted: async () => {}, onCustomize: async () => {},
    guidance: () => 'warning', error: (label, error) => `${label}: ${error}`,
  });
  await controller.open();
  await controller.start();
  await controller.start();
  assert.equal(attempts, 1, 'the retry resumes the saved start instead of asking again');
  assert.equal(h.calls.length, 2);
  assert.deepEqual(h.calls[1], h.calls[0]);
  assert.equal(h.calls[1].speed, 'priority');
});

test('cancelling a ready campaign start creates nothing', async () => {
  const h = readyHarness(async () => ({ cancelled: true }));
  await h.controller.open();
  await h.controller.start();
  assert.deepEqual(h.calls, []);
  assert.match(h.node('ready-campaign-status').textContent, /Nothing was created or sent/);
  assert.equal(h.values.size, 0, 'no pending start is remembered');
});

// ── Settings card and chat footer ────────────────────────────────────────────────────────

function settingsContext(savedDefaults, selected = fastModel) {
  const ids = ['default-effort-choice', 'default-speed-choice', 'default-speed-note'];
  const nodes = Object.fromEntries(ids.map(id => [id, { value: '', innerHTML: '', textContent: '', disabled: false }]));
  const context = {
    window: { SylviaChatStart: chatStartModule }, nodes,
    state: { providerSettings: { chat_defaults: savedDefaults } },
    $: id => nodes[id], selectedModelChoice: () => selected, escapeHtml: value => String(value),
  };
  vm.runInNewContext(slice('  // What new chats start with', '  // Models saved before speed tiers'), context);
  return context;
}

test('Settings shows saved defaults and labels one the default model cannot use', () => {
  const supported = settingsContext({ reasoning_effort: 'low', speed: 'priority' });
  supported.renderChatDefaultChoices(true);
  assert.equal(supported.nodes['default-effort-choice'].value, 'low');
  assert.equal(supported.nodes['default-speed-choice'].value, 'priority');
  assert.match(supported.nodes['default-effort-choice'].innerHTML, /Mode default \(Game Master High · Builder X-High\)/);
  assert.match(supported.nodes['default-speed-note'].textContent, /^Fast: 2x speed/);
  assert.equal(supported.chatDefaultsSummary(fastModel), 'GPT-6.1 Sol · Low · Fast speed');

  const unsupported = settingsContext({ reasoning_effort: 'xhigh', speed: 'priority' }, plainModel);
  unsupported.renderChatDefaultChoices(true);
  assert.match(unsupported.nodes['default-effort-choice'].innerHTML, /X-High \(not on this model: new chats use Mode default\)/);
  assert.match(unsupported.nodes['default-speed-choice'].innerHTML, /priority \(not on this model: new chats use Standard\)/);
  assert.equal(unsupported.chatDefaultsSummary(plainModel), 'Small · Mode default (Provider default) · Standard speed');
  // Picking another model by hand drops what it cannot use instead of offering it.
  unsupported.renderChatDefaultChoices();
  assert.equal(unsupported.nodes['default-effort-choice'].value, 'mode_default');
  assert.equal(unsupported.nodes['default-speed-choice'].value, 'standard');
  assert.equal(unsupported.nodes['default-speed-choice'].disabled, true);
  assert.equal(unsupported.nodes['default-speed-note'].textContent, '', 'standard speed needs no card note');
  assert.match(unsupported.nodes['default-speed-choice'].title, /no speed choice/);
});

test('the chat footer names the requested speed and explains an unavailable one', () => {
  const nodes = { 'chat-speed-current': { value: '', innerHTML: '', disabled: false }, 'chat-speed-hint': { textContent: '' } };
  const context = { window: { SylviaChatStart: chatStartModule }, $: id => nodes[id], escapeHtml: value => String(value) };
  vm.runInNewContext(slice('  function renderSpeedPicker(saved, disabled) {', '  async function switchSessionSpeed('), context);
  const fast = { supported: fastModel.speed_options, current: 'priority', effective_request: 'priority' };
  assert.equal(context.renderSpeedPicker({ speed: fast }, false), 'Fast');
  assert.equal(nodes['chat-speed-current'].value, 'priority');
  assert.equal(nodes['chat-speed-current'].disabled, false);
  assert.match(nodes['chat-speed-hint'].textContent, /the provider decides/);
  assert.equal(context.renderSpeedPicker({ speed: { supported: null, current: null, effective_request: null } }, false), '');
  assert.equal(nodes['chat-speed-current'].disabled, true, 'a model without tiers offers no choice');
  assert.match(nodes['chat-speed-hint'].textContent, /no speed choice/);
  const unsent = { supported: null, current: 'priority', effective_request: null };
  assert.equal(context.renderSpeedPicker({ speed: unsent }, false), '');
  assert.match(nodes['chat-speed-hint'].textContent, /uses standard speed/);
  assert.equal(context.renderSpeedPicker(null, true), '');
  assert.equal(nodes['chat-speed-hint'].textContent, '');
});
