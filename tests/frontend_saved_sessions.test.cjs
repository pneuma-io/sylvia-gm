// Execute the real renderer with synthetic bridge responses and a minimal DOM.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const path = require('node:path');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function element() {
  const attributes = new Map();
  const classes = new Set();
  const listeners = new Map();
  return {
    value: '', textContent: '', innerHTML: '', dataset: {}, style: { setProperty(name, value) { this[name] = value; } }, options: [], children: [],
    disabled: false, hidden: false, open: false, scrollTop: 0, scrollHeight: 0, clientHeight: 100,
    classList: {
      add: (...names) => names.forEach((name) => classes.add(name)),
      remove: (...names) => names.forEach((name) => classes.delete(name)),
      contains: (name) => classes.has(name), toggle() {},
    },
    setAttribute: (key, value) => attributes.set(key, value),
    getAttribute: (key) => attributes.get(key),
    removeAttribute: (key) => attributes.delete(key),
    querySelectorAll: () => [], querySelector: () => null,
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(handler);
    },
    removeEventListener(type, handler) { listeners.get(type)?.delete(handler); },
    listenerCount(type) { return listeners.get(type)?.size || 0; },
    dispatchEvent(event) {
      event.target ||= this;
      for (const handler of listeners.get(event.type) || []) handler(event);
      return !event.defaultPrevented;
    },
    setCustomValidity(message) { this.validationMessage = message; },
    reportValidity() { this.validityReports = (this.validityReports || 0) + 1; return !this.validationMessage; },
    focus() { this.focused = true; }, contains: () => false,
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; },
    appendChild(child) { this.children.push(child); child.parentElement = this; },
    replaceChildren(...children) { this.children = children; },
    replaceWith(next) {
      const parent = this.parentElement;
      parent.children[parent.children.indexOf(this)] = next;
      next.parentElement = parent;
    },
    remove() { this.parentElement.children = this.parentElement.children.filter((child) => child !== this); },
    get lastElementChild() { return this.children.at(-1); },
  };
}

function harness(savedStorage = new Map(), { shippedDom = false } = {}) {
  const clipboard = [];
  const nodes = new Map();
  const shippedIds = new Set(Array.from(fs.readFileSync(
    path.join(__dirname, '../src-tauri/frontend/app.html'), 'utf8'
  ).matchAll(/\bid="([^"]+)"/g), (match) => match[1]));
  const node = (id) => {
    if (shippedDom && !shippedIds.has(id)) return null;
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  // Shell wiring needs the composer's actual parent relationships and measurable geometry.
  const composer = element();
  composer.querySelector = (selector) => selector === '.composer-footer' ? { offsetHeight: 32 } : null;
  const chronology = { clientHeight: 800, querySelector: () => ({ offsetHeight: 48 }) };
  node('session-status-text').offsetHeight = 0;
  node('player-input').closest = (selector) => selector === '.input-composer' ? composer :
    selector === '.chronology-col' ? chronology : null;
  node('player-input').getBoundingClientRect = () => ({
    width: 600, height: parseFloat(node('player-input').style.height) || 58,
  });
  const timers = new Map();
  let timerId = 0;
  const storage = {
    getItem: (key) => savedStorage.get(key) ?? null,
    setItem: (key, value) => savedStorage.set(key, value),
    removeItem: (key) => savedStorage.delete(key),
  };
  const document = {
    getElementById: node, querySelectorAll: () => [],
    querySelector: selector => selector === '[data-chatgpt-action="add_model"]' ? element() : null,
    addEventListener() {}, body: element(), activeElement: null,
    createElement(tag) {
      const result = element();
      if (tag === 'template') {
        result.content = { children: [] };
        Object.defineProperty(result, 'innerHTML', { set(html) {
          const child = element();
          child.innerHTML = child.outerHTML = html;
          result.content.children = html ? [child] : [];
        } });
      }
      return result;
    },
  };
  const context = {
    window: { addEventListener() {}, confirm: () => true, requestAnimationFrame() {} },
    navigator: { clipboard: { writeText: async (text) => { clipboard.push(text); } } },
    document, sessionStorage: storage, localStorage: storage,
    crypto: webcrypto, console, AbortController,
    setInterval: (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms }); return id; },
    clearInterval: (id) => timers.delete(id),
    setTimeout: (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms }); return id; },
    clearTimeout: (id) => timers.delete(id),
    requestAnimationFrame() {}, cancelAnimationFrame() {},
  };
  let source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  // Expose existing private entry points for the test, without changing their implementation.
  const insertion = source.lastIndexOf('  return {');
  source = source.slice(0, insertion) + `  window.sessionTest = {
    state, openBuilderChat, openCampaign, showHome, refreshSelectedCampaign, submitInput, stop, retry, reconnect,
    initDictation, getDictation: () => dictation, createHostBridge,
    signalActiveStreamDisconnect, restoreDisconnectedStream, renderBuilderSession,
    renderDMSession, ensureSessionActivity, finalizeSelectedConstruction, renameBuilderChat,
    deleteBuilderChat, setBuilderArchived, setCampaignArchived, renderHome, renderBuilderChatRail,
    deleteReusableLibraryEntry, respondToPlayerAction,
    showSettingsTab, closeSettings, updateChatGPTConnection, selectChatGPTModel,
    refreshAdminAudit, exportAdminAudit, selectDiscoveredProviderModel, exportSelectedCampaign,
    toggleAdminAudit, copyVisibleAdminAudit,
    setSidebarCollapsed, initSidebarResize, wireControls, savePanelLayout,
    saveSelectedProvider,
    renderSessionModelState, renderReasoningPicker, switchSessionReasoning, switchSelectedSessionModel,
    wireBuilderQuestionForms: typeof wireBuilderQuestionForms === 'function'
      ? wireBuilderQuestionForms : null,
  };\n` + source.slice(insertion);
  source = ['dictation.js', 'panel-workspace.js', 'shot-playback.js', 'panel-host.js'].map((name) =>
    fs.readFileSync(path.join(__dirname, '../src-tauri/frontend', name), 'utf8')).join('\n') + '\n' + source;
  context.setTimeout ||= setTimeout;
  context.clearTimeout ||= clearTimeout;
  context.window.innerWidth = 1400;
  vm.runInNewContext(source, context, { filename: 'app.js' });
  const api = context.window.sessionTest;
  api.state.builderChats = ['a', 'b'].map((id) => ({
    display_name: `Builder ${id}`, session: {
      session_id: id, role: 'builder', status: 'active', provider_id: 'test', model_id: 'test',
    },
  }));
  const calls = { submissions: [], steering: [], stop: [], heartbeat: [], disconnect: [], reconnect: [] };
  const pending = new Map();
  api.state.bridge = {
    session_context: async (id) => ({ messages: [{ kind: 'talk', message_id: id, content: { text: `Answer ${id}` } }] }),
    builder_chat_index: async () => api.state.builderChats,
    campaign_index: async () => api.state.campaigns,
    submit_session_input(command) {
      calls.submissions.push(command);
      const wait = deferred(); pending.set(command.attempt_id, wait); return wait.promise;
    },
    steer_session: async (...args) => calls.steering.push(args),
    interrupt_provider_stream: async (...args) => calls.stop.push(args),
    provider_stream_heartbeat: async (...args) => {
      calls.heartbeat.push(args); return { attachment_generation: 1 };
    },
    disconnect_provider_stream: async (...args) => calls.disconnect.push(args),
    reconnect_provider_stream: async (...args) => {
      calls.reconnect.push(args); return { attachment_generation: 2 };
    },
    provider_stream_status: async () => ({ status: 'active' }),
  };
  return { ...api, calls, pending, node, timers, window: context.window, storage: savedStorage, clipboard,
    makeElement: element,
    flush: async () => { for (let n = 0; n < 15; n++) await Promise.resolve(); } };
}

test('startup controls bind against the shipped page without requiring removed elements', () => {
  const h = harness(new Map(), { shippedDom: true });
  assert.doesNotThrow(() => h.wireControls());
  assert.equal(h.node('player-input').style.height, '58px');
  assert.equal(h.node('composer-resize-handle').getAttribute('aria-valuenow'), '58');
});

function reasoningContext(overrides = {}) {
  return {
    messages: [],
    model_state: { current: { provider_id: 'test', model_id: 'test' }, once: null },
    reasoning_state: { current: null, once: null, provider_id: 'test', model_id: 'test',
      supported: ['low', 'high', 'xhigh', 'max'], scope: 'mode_default',
      valid: true, effective_request: 'xhigh', ...overrides },
  };
}

async function reasoningHarness(context = reasoningContext()) {
  const h = harness();
  h.state.modelChoices = [{ provider_id: 'test', model_id: 'test',
    supported_reasoning_efforts: ['low', 'high', 'xhigh', 'max'],
    reasoning_mode_defaults: { dm: 'high', builder: 'xhigh' } }];
  h.state.bridge.session_context = async () => context;
  await h.openBuilderChat('a');
  return h;
}

test('archive and restore keep the same Builder and draft, with explicit resume', async () => {
  const h = harness(new Map(), { shippedDom: true });
  await h.openBuilderChat('a');
  h.node('player-input').value = 'Unsent synthetic draft';
  h.state.bridge.archive_builder_chat = async (id) => {
    const chat = h.state.builderChats.find(item => item.session.session_id === id);
    return { ...chat, session: { ...chat.session, status: 'stopped' } };
  };
  h.state.bridge.restore_builder_chat = async (id) => {
    const chat = h.state.builderChats.find(item => item.session.session_id === id);
    return { ...chat, session: { ...chat.session, status: 'active' } };
  };
  await h.setBuilderArchived('a', true);
  assert.equal(h.state.view, 'home');
  assert.doesNotMatch(h.node('builder-chat-rail').innerHTML, /data-builder-session-id="a"/);
  assert.match(h.node('archived-items-list').innerHTML, /data-restore-builder="a"/);
  await h.openBuilderChat('a');
  assert.equal(h.state.activeSessionId, null, 'opening an archived item must not resume it');
  assert.match(h.node('archive-action-status').textContent, /Restore/);
  await h.setBuilderArchived('a', false);
  assert.equal(h.state.activeSessionId, null);
  await h.openBuilderChat('a');
  assert.equal(h.node('player-input').value, 'Unsent synthetic draft');
  assert.equal(h.calls.submissions.length, 0);
});

test('archive failure stays visible and late completion preserves current navigation', async () => {
  const h = harness();
  h.state.bridge.archive_builder_chat = async () => { throw Error('private transport detail'); };
  await h.setBuilderArchived('a', true);
  assert.match(h.node('builder-archive-status').textContent, /Archive failed/);
  assert.doesNotMatch(h.node('builder-archive-status').textContent, /private transport/);
  assert.match(h.node('builder-chat-rail').innerHTML, /data-builder-session-id="a"/);
  const saving = deferred();
  h.state.bridge.archive_builder_chat = () => saving.promise;
  await h.openBuilderChat('a');
  const archived = h.setBuilderArchived('a', true);
  assert.equal(h.node('send-btn').disabled, true);
  await h.openBuilderChat('b');
  saving.resolve({ display_name: 'Builder a', session: { ...h.state.builderChats[0].session, status: 'stopped' } });
  await archived;
  assert.equal(h.state.activeSessionId, 'b');
  assert.equal(h.node('send-btn').disabled, false);
});

test('Home archives a campaign through its existing owner lifecycle and filters its Interview', async () => {
  const h = harness();
  h.state.campaigns = [{ campaign_id: 'campaign', display_name: 'Synthetic draft', revision: 4,
    lifecycle: 'active', availability: 'draft', sessions: [
      { session_id: 'interview', role: 'builder', status: 'active' }] }];
  const commands = [];
  h.state.bridge.archive_campaign = async (...args) => {
    commands.push(args); h.state.campaigns[0].lifecycle = 'archived';
    h.state.campaigns[0].sessions[0].status = 'stopped';
  };
  h.state.bridge.restore_archived_campaign = async (...args) => {
    commands.push(args); h.state.campaigns[0].lifecycle = 'active';
    h.state.campaigns[0].sessions[0].status = 'active';
  };
  h.renderHome(); h.renderBuilderChatRail();
  assert.match(h.node('campaign-shelves').innerHTML, /data-archive-campaign="campaign"/);
  await h.setCampaignArchived('campaign', true);
  assert.deepEqual(commands[0].slice(1), ['campaign', 4, 'interview']);
  assert.match(h.node('home-campaign-count').textContent, /0 ongoing/);
  assert.doesNotMatch(h.node('builder-chat-rail').innerHTML, /data-builder-session-id="interview"/);
  assert.match(h.node('archived-items-list').innerHTML, /data-restore-campaign="campaign"/);
  assert.match(h.node('builder-chat-rail').innerHTML, /data-builder-session-id="a"/);
  await h.setCampaignArchived('campaign', false);
  assert.match(h.node('builder-chat-rail').innerHTML, /data-builder-session-id="interview"/);
  assert.match(h.node('home-campaign-count').textContent, /1 ongoing/);
});

test('reasoning picker shows owner defaults and keeps saved and next-reply controls distinct', async () => {
  const context = reasoningContext();
  const h = await reasoningHarness(context);
  assert.match(h.node('chat-reasoning-btn').title, /X-High \(mode default\)/);
  assert.match(h.node('chat-reasoning-current').innerHTML, /value="max"/);
  assert.doesNotMatch(h.node('chat-reasoning-current').innerHTML, /value="medium"/);
  const commands = [];
  h.state.bridge.switch_session_reasoning = async (command) => {
    commands.push(command);
    const state = context.reasoning_state;
    state[command.mode] = command.effort;
    state.scope = state.once ? 'once' : state.current ? 'session' : 'mode_default';
    state.effective_request = state.once === 'mode_default' ? 'xhigh' : state.once || state.current || 'xhigh';
    return { reasoning_state: { ...state } };
  };
  h.wireControls();
  h.node('chat-reasoning-current').value = 'low';
  h.node('chat-reasoning-current').dispatchEvent({ type: 'change' });
  await h.flush();
  assert.equal(commands[0].mode, 'current');
  assert.equal(commands[0].effort, 'low');
  assert.match(h.node('chat-reasoning-btn').title, /Low \(this chat\)/);
  h.node('chat-reasoning-once').value = 'mode_default';
  h.node('chat-reasoning-once').dispatchEvent({ type: 'change' });
  await h.flush();
  assert.equal(commands[1].effort, 'mode_default');
  assert.equal(h.node('chat-reasoning-current').value, 'low');
  assert.match(h.node('chat-reasoning-btn').textContent, /X-High · next reply/);
  h.node('chat-reasoning-once').value = '';
  h.node('chat-reasoning-once').dispatchEvent({ type: 'change' });
  await h.flush();
  assert.equal(commands[2].effort, null);
  assert.match(h.node('chat-reasoning-btn').title, /Low \(this chat\)/);
  await h.showHome();
  assert.equal(h.node('chat-reasoning-btn').disabled, true);
  await h.openBuilderChat('a');
  assert.match(h.node('chat-reasoning-btn').title, /Low \(this chat\)/);
  assert.equal(h.calls.submissions.length, 0);
});

test('reasoning controls use the next model capabilities and report unknown support honestly', async () => {
  const context = reasoningContext({ supported: null, effective_request: null,
    model_id: 'unknown', scope: 'once', once: 'provider_default' });
  context.model_state.once = { provider_id: 'test', model_id: 'unknown' };
  const h = await reasoningHarness(context);
  assert.match(h.node('chat-reasoning-btn').textContent, /Provider default · next reply/);
  assert.match(h.node('chat-reasoning-hint').textContent, /unknown/);
  assert.match(h.node('chat-reasoning-current').innerHTML, /value="xhigh"/);
  assert.doesNotMatch(h.node('chat-reasoning-once').innerHTML, /value="xhigh"/);
  context.reasoning_state = { ...context.reasoning_state, current: 'ultra', once: 'ultra', valid: false };
  await h.renderBuilderSession('a');
  assert.match(h.node('chat-reasoning-btn').textContent, /Choose a supported level/);
  assert.match(h.node('chat-reasoning-current').innerHTML, /unavailable/);
  assert.equal(h.node('chat-reasoning-current').disabled, false, 'invalid values must remain resettable');
});

test('GM reasoning displays its own High default and refreshes a consumed next-reply selection', async () => {
  const h = await reasoningHarness();
  configureGM(h);
  const context = reasoningContext({ effective_request: 'high' });
  h.state.bridge.session_context = async () => context;
  await h.openCampaign('campaign', 'gm');
  assert.match(h.node('chat-reasoning-btn').title, /High \(mode default\)/);
  assert.match(h.node('chat-reasoning-current').innerHTML, /Mode default \(High\)/);
  context.reasoning_state = { ...context.reasoning_state, once: 'low', scope: 'once', effective_request: 'low' };
  await h.renderDMSession('campaign', 'gm');
  assert.match(h.node('chat-reasoning-btn').textContent, /Low · next reply/);
  context.reasoning_state = { ...context.reasoning_state, once: null, scope: 'mode_default', effective_request: 'high' };
  await h.renderDMSession('campaign', 'gm');
  assert.equal(h.node('chat-reasoning-once').value, '');
  assert.match(h.node('chat-reasoning-btn').title, /High \(mode default\)/);
});

test('reasoning changes are disabled during pending replies and stale saves do not affect another chat', async () => {
  const h = await reasoningHarness();
  const saving = deferred();
  let calls = 0;
  h.state.bridge.switch_session_reasoning = async () => { calls++; return saving.promise; };
  const save = h.switchSessionReasoning('current', 'low');
  assert.equal(h.node('chat-reasoning-btn').disabled, true);
  assert.equal(h.node('chat-model-picker-btn').disabled, true);
  await h.openBuilderChat('b');
  saving.resolve({ reasoning_state: reasoningContext({ effective_request: 'low' }).reasoning_state });
  await save;
  assert.match(h.node('chat-reasoning-btn').textContent, /X-High/);
  h.node('player-input').value = 'Synthetic input';
  const sending = h.submitInput('Synthetic input');
  await h.flush();
  assert.equal(h.node('chat-reasoning-btn').disabled, true);
  await h.switchSessionReasoning('current', 'high');
  assert.equal(calls, 1);
  for (const pending of h.pending.values()) pending.resolve({});
  await sending;
});

test('reasoning save failures retain the actual choice and display a safe reset instruction', async () => {
  const h = await reasoningHarness();
  h.state.bridge.switch_session_reasoning = async () => { throw Error('PRIVATE_CREDENTIAL'); };
  await h.switchSessionReasoning('current', 'low');
  assert.match(h.node('chat-reasoning-btn').textContent, /X-High/);
  assert.match(h.node('chat-reasoning-hint').textContent, /Mode default/);
  assert.doesNotMatch(h.node('chat-reasoning-hint').textContent, /PRIVATE_CREDENTIAL/);
});

test('an unsupported model switch keeps the owner model and explains how to reset effort', async () => {
  const h = await reasoningHarness();
  h.state.modelChoices.push({ provider_id: 'test', model_id: 'other', model_name: 'Other' });
  h.node('model-choice').value = h.node('chat-model-choice').value = '1';
  h.state.bridge.switch_session_model = async () => { throw Error('PRIVATE_ERROR'); };
  await h.switchSelectedSessionModel('current');
  assert.equal(h.node('chat-model-choice').value, '0');
  assert.equal(h.node('model-choice').value, '1', 'chat switching must not edit the Settings default selector');
  assert.match(h.node('chat-model-hint').textContent, /Mode default/);
  assert.doesNotMatch(h.node('chat-model-hint').textContent, /PRIVATE_ERROR/);
});

test('connection save identifies invalid nonempty limits before calling the bridge', async () => {
  const h = harness();
  h.node('provider-model-id').value = 'synthetic-model';
  let calls = 0;
  h.state.bridge.save_provider_settings = async () => { calls++; };
  for (const [id, label] of [
    ['provider-context-window', /context token limit/i],
    ['provider-output-limit', /output token limit/i],
    ['provider-builder-output-limit', /Builder output token limit/i],
  ]) {
    for (const value of ['0', '-1', '1.5', 'invalid']) {
      h.node(id).value = value;
      await h.saveSelectedProvider();
      assert.match(h.node('provider-settings-status').textContent, label);
      assert.equal(h.node(id).getAttribute('aria-invalid'), 'true');
      assert.equal(h.node(id).focused, true);
      assert.match(h.node(id).validationMessage, label);
      assert.ok(h.node(id).validityReports > 0);
      assert.equal(h.node('provider-model-advanced').open, true);
    }
    h.node(id).value = '';
  }
  assert.equal(calls, 0);
});

test('connection save preserves blank automatic limits for the owner to resolve', async () => {
  const h = harness();
  h.node('provider-model-id').value = 'synthetic-model';
  let saved;
  h.state.bridge.save_provider_settings = async command => {
    saved = command;
    throw Error('synthetic boundary after command capture');
  };
  await h.saveSelectedProvider();
  assert.equal(saved.configured_context_window, null);
  assert.equal(saved.max_output_tokens, null);
  assert.equal(saved.builder_output_tokens, null);
  for (const id of ['provider-context-window', 'provider-output-limit', 'provider-builder-output-limit']) {
    assert.notEqual(h.node(id).getAttribute('aria-invalid'), 'true');
    assert.equal(h.node(id).validationMessage, '');
  }
});

test('connection save keeps arbitrary bridge failures private', async () => {
  const h = harness();
  h.node('provider-model-id').value = 'synthetic-model';
  h.node('provider-output-limit').value = '32000';
  h.state.providerCapacity = { effective: 64000 };
  h.state.bridge.save_provider_settings = async () => { throw Error('PRIVATE_CREDENTIAL'); };
  await h.saveSelectedProvider();
  assert.doesNotMatch(h.node('provider-settings-status').textContent, /PRIVATE_CREDENTIAL/);
  assert.match(h.node('provider-settings-status').textContent, /Could not save/);
  assert.equal(h.node('provider-output-limit').getAttribute('aria-invalid'), undefined);
});

test('Thinking export requires a fresh explicit opt-in for each export', async () => {
  const h = harness();
  h.state.activeCampaignId = 'selected';
  const calls = [];
  h.state.bridge.export_campaign_file = async (...args) => {
    calls.push(args);
    return { status: 'cancelled' };
  };
  await h.exportSelectedCampaign();
  h.node('export-include-thinking').checked = true;
  await h.exportSelectedCampaign();
  assert.equal(h.node('export-include-thinking').checked, false);
  await h.exportSelectedCampaign();
  assert.deepEqual(calls.map((args) => args[3]), [false, true, false]);
  assert(calls.every((args) => args[2] === 'selected'));
});

test('Home, campaign and Builder navigation show the appropriate left sidebar without stopping work', async () => {
  const h = harness();
  configureGM(h);
  h.showHome();
  assert.equal(h.node('session-context').hidden, true);
  assert.equal(h.node('builder-launcher').hidden, false);
  await h.openCampaign('campaign', 'gm');
  assert.equal(h.node('session-context').hidden, false);
  assert.equal(h.node('builder-launcher').hidden, true);
  assert.equal(h.node('lifecycle-actions').hidden, true, 'active GM needs no right-side session controls');
  await h.openBuilderChat('a');
  assert.equal(h.node('builder-launcher').hidden, false);
  assert.equal(h.node('session-context').hidden, true);
  h.showHome();
  assert.equal(h.node('session-context').hidden, true);
  assert.equal(h.calls.stop.length, 0);
  assert.equal(h.calls.submissions.length, 0);
});

test('sidebar toggles are independent and persist through navigation and app reload', async () => {
  const h = harness();
  configureGM(h);
  h.wireControls();
  h.node('toggle-left-sidebar-btn').dispatchEvent({ type: 'click' });
  assert.equal(h.node('toggle-left-sidebar-btn').getAttribute('aria-expanded'), 'false');
  assert.equal(h.node('toggle-right-sidebar-btn').getAttribute('aria-expanded'), 'true');
  await h.openCampaign('campaign', 'gm');
  h.savePanelLayout({ sidebar_width: 350 });
  await h.flush();
  h.node('toggle-right-sidebar-btn').dispatchEvent({ type: 'click' });
  h.node('home-btn').dispatchEvent({ type: 'click' });
  assert.equal(h.state.view, 'home');
  await h.openCampaign('campaign', 'gm');
  assert.equal(h.node('session-frame').classList.contains('left-sidebar-collapsed'), true);
  assert.equal(h.node('session-frame').classList.contains('right-sidebar-collapsed'), true);
  assert.equal(h.node('right-sidebar').style.width, '350px');
  const reopened = harness(h.storage);
  reopened.initSidebarResize();
  assert.equal(reopened.node('toggle-left-sidebar-btn').getAttribute('aria-label'), 'Expand left sidebar');
  assert.equal(reopened.node('toggle-right-sidebar-btn').getAttribute('aria-expanded'), 'false');
  h.node('toggle-right-sidebar-btn').dispatchEvent({ type: 'click' });
  assert.equal(h.node('right-sidebar').style.width, '350px');
  assert.equal(h.node('session-frame').classList.contains('left-sidebar-collapsed'), true);
  assert.equal(h.node('session-frame').classList.contains('right-sidebar-collapsed'), false);
});

test('Audit enables and copies visible records without confirmation and handles save cancellation', async () => {
  const h = harness();
  h.window.confirm = () => { throw Error('Audit must not ask for confirmation'); };
  let enabled = false;
  h.state.bridge.set_admin_full_audit = async (value) => { enabled = value; };
  h.state.bridge.admin_full_audit_status = async () => ({ enabled, trace_count: enabled ? 2 : 0 });
  h.state.bridge.admin_full_audit_record = async (index) => ({ trace: { kind: index ? 'skip' : 'selected', text: 'synthetic' } });
  await h.toggleAdminAudit();
  assert.equal(h.state.adminAuditEnabled, true);
  h.node('admin-audit-search').value = 'selected';
  await h.copyVisibleAdminAudit();
  assert.deepEqual(JSON.parse(h.clipboard[0]), [{ kind: 'selected', text: 'synthetic' }]);
  let exports = 0;
  h.state.bridge.export_admin_audit_file = async () => { exports++; return { status: 'cancelled' }; };
  await h.exportAdminAudit();
  assert.equal(exports, 1);
  assert.equal(h.node('admin-audit-status').textContent, 'Model log export cancelled.');
  assert.equal(h.state.adminAuditTraces.length, 2);
});

test('custom model selection retains its connection API format', () => {
  for (const transport of ['anthropic_messages', 'responses']) {
    const h = harness();
    h.node('provider-preset').value = 'custom';
    h.node('provider-transport').value = transport;
    h.node('provider-discovered-models').value = '0';
    h.state.providerDiscovery = { models: [{ id: 'local', transport: 'chat_completions' }] };
    h.selectDiscoveredProviderModel();
    assert.equal(h.node('provider-transport').value, transport);
    assert.equal(h.node('provider-model-id').value, 'local');
  }
  const h = harness();
  h.node('provider-preset').value = 'opencode-go';
  h.node('provider-transport').value = 'chat_completions';
  h.node('provider-discovered-models').value = '0';
  h.state.providerDiscovery = { models: [{ id: 'named', transport: 'anthropic_messages' }] };
  h.selectDiscoveredProviderModel();
  assert.equal(h.node('provider-transport').value, 'anthropic_messages');
});

test('audit refresh bounds loaded records and coalesces overlapping refreshes', async () => {
  const h = harness();
  const gate = deferred();
  const loaded = [];
  let statuses = 0;
  h.state.bridge.admin_full_audit_status = async () => {
    statuses++;
    await gate.promise;
    return { enabled: true, trace_count: 8973 };
  };
  h.state.bridge.admin_full_audit_record = async (index) => {
    loaded.push(index);
    return { trace: { kind: 'test', index } };
  };
  const first = h.refreshAdminAudit();
  const second = h.refreshAdminAudit();
  gate.resolve();
  await Promise.all([first, second]);
  assert.equal(statuses, 1);
  assert.equal(loaded.length, 50);
  assert.equal(loaded[0], 8923);
  assert.equal(loaded.at(-1), 8972);
  assert.equal(h.state.adminAuditRawTraceCount, 8973);
  assert.equal(h.state.adminAuditTraces.length, 50);
  assert.match(h.node('admin-audit-status').textContent, /latest 50/);
});

test('audit export shows actionable save errors and permits another Save As', async () => {
  const h = harness();
  h.state.adminAuditRawTraceCount = 1;
  let calls = 0;
  h.state.bridge.export_admin_audit_file = async () => {
    calls++;
    throw 'command_rejected: Audit export could not be saved: not enough space. Free space or choose another drive.';
  };
  await h.exportAdminAudit();
  assert.match(h.node('admin-audit-status').textContent, /not enough space/);
  assert.match(h.node('admin-audit-status').textContent, /choose another drive/);
  assert.equal(h.state.adminAuditRawTraceCount, 1);
  h.state.bridge.export_admin_audit_file = async () => {
    calls++;
    return { status: 'exported', file_name: 'audit.json' };
  };
  await h.exportAdminAudit();
  assert.match(h.node('admin-audit-status').textContent, /Model logs exported as audit.json/);
  assert.equal(calls, 2);
  assert.equal(h.state.adminAuditRawTraceCount, 1);
  assert.equal(h.calls.submissions.length, 0);
});

test('account sign-in polls pending consent and stops after connection or leaving the page', async () => {
  const h = harness();
  let signedIn = false;
  const calls = [];
  h.state.bridge.chatgpt_connection = async (action) => {
    calls.push(action);
    return action === 'models' ? { signed_in: true, models: [
      { id: 'sol', name: 'Sol' }, { id: 'other', name: 'Another model' },
    ] } : { signed_in: signedIn, login_pending: !signedIn };
  };
  h.showSettingsTab('accounts');
  await h.flush();
  assert.match(h.node('chatgpt-connection-status').textContent, /update automatically/);
  const timer = [...h.timers.values()].find((item) => item.ms === 1500);
  assert.ok(timer);
  signedIn = true;
  timer.fn();
  await h.flush();
  assert.deepEqual(calls, ['status', 'status', 'models']);
  assert.equal(h.node('chatgpt-model-card').hidden, false);
  assert.match(h.node('chatgpt-model-choice').innerHTML, /Another model/);
  assert.equal(h.timers.size, 0);
  signedIn = false;
  await h.updateChatGPTConnection('login');
  assert.equal(h.timers.size, 1);
  h.showSettingsTab('models');
  assert.equal(h.timers.size, 0);
});

test('subscription capacities follow the selected saved model and remain blank for an unknown model', () => {
  const h = harness();
  h.state.providerSettings = { configured: [{ preset_id: 'chatgpt_subscription', models: [
    { id: 'sol', configured_context_window: 262144, max_output_tokens: 128000 },
  ] }] };
  h.node('chatgpt-model-choice').value = 'sol';
  h.selectChatGPTModel();
  assert.equal(h.node('chatgpt-model-context').value, 262144);
  h.node('chatgpt-model-choice').value = 'unknown-model';
  h.selectChatGPTModel();
  assert.equal(h.node('chatgpt-model-context').value, '');
  assert.equal(h.node('chatgpt-model-output').value, '');
});

for (const navigateAway of [false, true]) {
  test(`publication refreshes the saved Builder title (navigate away: ${navigateAway})`, async () => {
    const h = harness();
    await h.openBuilderChat('a');
    const publishing = h.submitInput('Validate and publish my setting.');
    const command = h.calls.submissions.at(-1);
    if (navigateAway) await h.openBuilderChat('b');
    h.state.bridge.builder_chat_index = async () => h.state.builderChats.map((chat) =>
      chat.session.session_id === 'a' ? { ...chat, display_name: 'Lantern Harbor · Campaign Setting' } : chat);
    h.pending.get(command.attempt_id).resolve({ status: 'completed' });
    await publishing;
    assert.equal(h.node('session-title').textContent,
      navigateAway ? 'Builder b' : 'Lantern Harbor · Campaign Setting');
    assert.match(h.node('builder-chat-rail').innerHTML, /Lantern Harbor/);
    await h.openBuilderChat('a');
    assert.equal(h.node('session-title').textContent, 'Lantern Harbor · Campaign Setting');
  });
}

for (const message of [
  'The selected provider could not complete the request',
  'The selected model is at capacity. Saved work is kept. Try again later or choose another model for this chat.',
]) test(`a provider rejection stays visible after repaint: ${message}`, async () => {
  const h = harness();
  await h.openBuilderChat('a');
  const failed = h.submitInput('', { builderStart: true, builderSessionId: 'a' });
  const command = h.calls.submissions.at(-1);
  h.state.bridge.session_context = async () => ({ messages: [], activity: {
    attempt_id: command.attempt_id, status: 'interrupted',
  } });
  h.pending.get(command.attempt_id).reject(new Error(message));
  await failed;
  assert.ok(h.node('session-status-text').textContent.includes(message));
  await h.openBuilderChat('b');
  await h.openBuilderChat('a');
  assert.ok(h.node('session-status-text').textContent.includes(message));
});

test('saved Builder questions render their choices and one answer submission', async () => {
  const h = harness();
  h.state.bridge.session_context = async () => ({ messages: [
    {
      message_id: 'malformed-question', kind: 'evidence', content: {
        tool_name: 'ask_question', tool_call_id: 'malformed-call', result: null,
      },
    },
    {
      message_id: 'complete-widget', kind: 'evidence', content: {
        tool_name: 'ask_question', tool_call_id: 'complete-call', result: {
          success: true, status: 'completed', widget: { type: 'complete', data: {} },
        },
      },
    },
    {
      message_id: 'source-question', kind: 'evidence', content: {
        tool_name: 'ask_question', tool_call_id: 'source-call', result: {
          success: true, status: 'awaiting_answer', widget: { type: 'select', data: {
            id: 'companion_source',
            question: 'How should <source> & origin be chosen?',
            options: [
              { value: 'Fully custom', label: 'Fully custom — design from scratch' },
              { value: 'Existing', label: 'Use an existing Companion' },
            ],
          } },
        },
      },
    },
    {
      message_id: 'role-question', kind: 'evidence', content: {
        tool_name: 'ask_question', tool_call_id: 'role-call', result: {
          success: true, status: 'awaiting_answer', widget: { type: 'select', data: {
            id: 'companion_role', question: 'What role should they fill?',
            options: ['Guardian', 'Guide & scout'],
          } },
        },
      },
    },
  ] });

  await h.openBuilderChat('a');
  const html = h.node('chronology').dataset.chronologyHtml;
  assert.match(html, /How should &lt;source&gt; &amp; origin be chosen\?/);
  assert.match(html, /Fully custom — design from scratch/);
  assert.match(html, /Guide &amp; scout/);
  assert.equal((html.match(/data-builder-question-control/g) || []).length, 4);
  assert.equal((html.match(/Submit answers/g) || []).length, 1);
  assert.doesNotMatch(html, /Operation completed/);
  assert.doesNotMatch(html, /The result was saved/);
});

test('Builder question answers use the original saved session and remain readable after reopen', async () => {
  const h = harness();
  const questionMessages = [
    {
      message_id: 'details-question', kind: 'evidence', content: {
        tool_name: 'ask_question', tool_call_id: 'details-call', result: {
          success: true, status: 'awaiting_answer', widget: { type: 'text', data: {
            id: 'companion_details', question: 'What details should be included?',
          } },
        },
      },
    },
    {
      message_id: 'confirm-question', kind: 'evidence', content: {
        tool_name: 'ask_question', tool_call_id: 'confirm-call', result: {
          success: true, status: 'awaiting_answer', widget: { type: 'confirm', data: {
            id: 'include_history', question: 'Include their prior history?',
            options: ['Keep the boundary', 'Change it'],
          } },
        },
      },
    },
  ];
  let savedReply = null;
  h.state.bridge.session_context = async (sessionId) => ({
    messages: sessionId === 'a'
      ? [...questionMessages, ...(savedReply ? [{
          message_id: 'saved-reply', kind: 'user', content: { text: savedReply },
        }] : [])]
      : [{ message_id: 'b-talk', kind: 'talk', content: { text: 'Builder b' } }],
  });
  await h.openBuilderChat('a');
  assert.ok(h.wireBuilderQuestionForms);
  assert.match(h.node('chronology').dataset.chronologyHtml, /Keep the boundary/);
  assert.match(h.node('chronology').dataset.chronologyHtml, /Change it/);

  const textControl = h.makeElement();
  textControl.dataset = {
    builderQuestionKey: 'details-question', builderQuestionType: 'text',
    builderQuestionText: 'What details should be included?', builderQuestionOrder: '0',
  };
  textControl.value = 'A quiet <watcher>';
  textControl.setAttribute('form', 'builder-question-form-a');
  const confirmControl = h.makeElement();
  confirmControl.dataset = {
    builderQuestionKey: 'confirm-question', builderQuestionType: 'confirm',
    builderQuestionText: 'Include their prior history?', builderQuestionOrder: '1',
  };
  confirmControl.value = 'Keep the boundary';
  confirmControl.setAttribute('form', 'builder-question-form-a');
  const submitButton = h.makeElement();
  const form = h.makeElement();
  form.id = 'builder-question-form-a';
  form.dataset = { builderQuestionSession: 'a' };
  form.querySelector = () => submitButton;
  const container = h.makeElement();
  container.querySelectorAll = (selector) => selector === '[data-builder-question-form]'
    ? [form] : [textControl, confirmControl];
  h.wireBuilderQuestionForms(container);

  textControl.dispatchEvent({ type: 'input' });
  confirmControl.dispatchEvent({ type: 'change' });
  await h.openBuilderChat('b');
  form.dispatchEvent({ type: 'submit', preventDefault() {} });
  assert.equal(h.calls.submissions.length, 0);
  await h.openBuilderChat('a');
  let html = h.node('chronology').dataset.chronologyHtml;
  assert.doesNotMatch(html, /A quiet &lt;watcher&gt;/);
  assert.doesNotMatch(html, /value="Keep the boundary" selected/);

  const restoredTextControl = h.makeElement();
  restoredTextControl.dataset = { ...textControl.dataset };
  restoredTextControl.setAttribute('form', 'builder-question-form-a');
  const restoredConfirmControl = h.makeElement();
  restoredConfirmControl.dataset = { ...confirmControl.dataset };
  restoredConfirmControl.setAttribute('form', 'builder-question-form-a');
  const restoredSubmitButton = h.makeElement();
  const restoredForm = h.makeElement();
  restoredForm.id = 'builder-question-form-a';
  restoredForm.dataset = { builderQuestionSession: 'a' };
  restoredForm.querySelector = () => restoredSubmitButton;
  const restoredContainer = h.makeElement();
  restoredContainer.querySelectorAll = (selector) => selector === '[data-builder-question-form]'
    ? [restoredForm] : [restoredTextControl, restoredConfirmControl];
  h.wireBuilderQuestionForms(restoredContainer);
  assert.equal(restoredTextControl.value, 'A quiet <watcher>');
  assert.equal(restoredConfirmControl.value, 'Keep the boundary');

  restoredForm.dispatchEvent({
    type: 'submit', defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
  });
  restoredForm.dispatchEvent({ type: 'submit', preventDefault() {} });
  await h.flush();
  assert.equal(h.calls.submissions.length, 1);
  assert.equal(h.calls.submissions[0].session_id, 'a');
  assert.equal(h.calls.submissions[0].user_text,
    'Here are my answers:\n\n1. What details should be included?\nAnswer: A quiet <watcher>\n\n' +
    '2. Include their prior history?\nAnswer: Keep the boundary');
  await h.openBuilderChat('b');
  savedReply = h.calls.submissions[0].user_text;
  h.pending.get(h.calls.submissions[0].attempt_id).resolve({ status: 'completed' });
  await h.flush();
  await h.openBuilderChat('a');
  html = h.node('chronology').dataset.chronologyHtml;
  assert.match(html, /What details should be included\?/);
  assert.match(html, /A quiet &lt;watcher&gt;/);
  assert.match(html, /A later reply closed these controls/);
  assert.doesNotMatch(html, /Submit answers/);
});

test('freeform character interview answers stay in the original widget after reopen', async () => {
  const h = harness();
  const question = {
    message_id: 'play-question', sequence: 2, kind: 'evidence', content: {
      kind: 'builder_tool_result', tool_name: 'ask_question', tool_call_id: 'play-call',
      arguments: '{"type":"text","id":"player_concept","question":"Who would you like to play?"}',
      result: { success: true, status: 'awaiting_answer', widget: { type: 'text', data: {
        id: 'player_concept', question: 'Who would you like to play?',
      } } },
    },
  };
  let savedAnswer = null;
  h.state.bridge.session_context = async () => ({ messages: [question, ...(savedAnswer ? [{
    message_id: 'saved-play-answer', sequence: 3, kind: 'user', content: savedAnswer,
  }] : [])] });
  await h.openBuilderChat('a');
  const control = h.makeElement();
  control.dataset = { builderQuestionKey: 'play-question', builderQuestionType: 'text',
    builderQuestionText: 'Who would you like to play?', builderQuestionOrder: '0' };
  control.value = '  A calm courier called Rowan <watcher>.\nKeep this exact line.  ';
  control.setAttribute('form', 'builder-question-form-a');
  const button = h.makeElement();
  const form = h.makeElement();
  form.id = 'builder-question-form-a';
  form.dataset = { builderQuestionSession: 'a' };
  form.querySelector = () => button;
  const container = h.makeElement();
  container.querySelectorAll = selector => selector === '[data-builder-question-form]'
    ? [form] : [control];
  h.wireBuilderQuestionForms(container);
  control.dispatchEvent({ type: 'input' });
  form.dispatchEvent({ type: 'submit', preventDefault() {} });
  await h.flush();
  assert.equal(h.calls.submissions.length, 1);
  const command = h.calls.submissions[0];
  // The owner contract preserves the answer and derives provenance from saved evidence.
  savedAnswer = { text: command.user_text, ...(command.builder_question_answers ? {
    kind: 'builder_question_answer', answers: command.builder_question_answers.map(answer => ({
      ...answer, tool_call_id: 'play-call', widget_id: 'player_concept', type: 'text',
      question: 'Who would you like to play?', label: answer.value,
    })),
  } : {}) };
  h.pending.get(command.attempt_id).resolve({ status: 'completed' });
  await h.flush();
  await h.openBuilderChat('b');
  await h.openBuilderChat('a');
  const html = h.node('chronology').dataset.chronologyHtml;
  const card = html.match(/<fieldset[^>]*builder-question-card[\s\S]*?<\/fieldset>/)?.[0] || '';
  assert.match(card, /Who would you like to play\?/);
  assert.match(card, /A calm courier called Rowan &lt;watcher&gt;\./);
  assert.match(card, /Answered/);
  assert.doesNotMatch(html, /Here are my answers:/);
  assert.doesNotMatch(html, /<div class="story-header">You<\/div>/);
  assert.doesNotMatch(html, /Submit answers/);
  assert.equal(command.builder_question_answers[0].question_message_id, 'play-question');
  assert.equal(command.builder_question_answers[0].value, control.value);
});

test('typed answers survive paging and repeated widget ids while literal chat remains visible', async () => {
  const h = harness();
  const question = (id, sequence) => ({ message_id: id, sequence, kind: 'evidence', content: {
    kind: 'builder_tool_result', tool_name: 'ask_question', tool_call_id: `${id}-call`, result: {
      success: true, status: 'awaiting_answer', widget: { type: 'text', data: {
        id: 'player_concept', question: 'Who would you like to play?',
      } },
    },
  } });
  const snapshot = { question_message_id: 'first-question', tool_call_id: 'first-question-call',
    widget_id: 'player_concept', question: 'Who would you like to play?', type: 'text',
    value: 'Rowan <courier>', label: 'Rowan <courier>', custom: false };
  const answer = { message_id: 'answer', sequence: 3, kind: 'user', content: {
    kind: 'builder_question_answer', text: 'Here are my answers: hidden transport text',
    answers: [snapshot],
  } };
  let messages = [answer, question('new-question', 4)];
  h.state.bridge.session_context = async () => ({ messages, latest_user_sequence: 3 });
  await h.openBuilderChat('a');
  let html = h.node('chronology').dataset.chronologyHtml;
  assert.match(html, /Rowan &lt;courier&gt;/);
  assert.equal((html.match(/Answered/g) || []).length, 1);
  assert.match(html, /Submit answers/);
  assert.doesNotMatch(html, /hidden transport text/);
  messages = [question('first-question', 2), ...messages];
  await h.openBuilderChat('a');
  html = h.node('chronology').dataset.chronologyHtml;
  assert.equal((html.match(/Rowan &lt;courier&gt;/g) || []).length, 1);
  assert.equal((html.match(/Answered/g) || []).length, 1);
  assert.match(html, /Submit answers/);
  messages.push({ message_id: 'plain', sequence: 5, kind: 'user', content: {
    text: 'Here are my answers: this is my actual typed chat.',
  } });
  await h.openBuilderChat('a');
  html = h.node('chronology').dataset.chronologyHtml;
  assert.match(html, /Here are my answers: this is my actual typed chat\./);
  assert.match(html, /<div class="story-header">You<\/div>/);
  // A corrupt or mismatched link must remain visible rather than erase player input.
  answer.content.answers = [{ ...snapshot, tool_call_id: 'wrong-call' }];
  await h.openBuilderChat('a');
  assert.match(h.node('chronology').dataset.chronologyHtml, /hidden transport text/);
});

test('rejected question submission keeps its session draft and ordinary composer text', async () => {
  const h = harness();
  h.state.bridge.session_context = async () => ({ messages: [{ message_id: 'question',
    kind: 'evidence', content: { kind: 'builder_tool_result', tool_name: 'ask_question', result: {
      success: true, status: 'awaiting_answer', widget: { type: 'text', data: {
        id: 'concept', question: 'Who would you like to play?',
      } },
    } },
  }] });
  await h.openBuilderChat('a');
  h.node('player-input').value = 'Separate composer draft';
  const control = h.makeElement();
  control.dataset = { builderQuestionKey: 'question', builderQuestionType: 'text',
    builderQuestionText: 'Who would you like to play?', builderQuestionOrder: '0' };
  control.value = 'Keep Rowan';
  control.setAttribute('form', 'builder-question-form-a');
  const form = h.makeElement();
  form.id = 'builder-question-form-a';
  form.dataset = { builderQuestionSession: 'a' };
  form.querySelector = () => h.makeElement();
  const container = h.makeElement();
  container.querySelectorAll = selector => selector === '[data-builder-question-form]'
    ? [form] : [control];
  h.wireBuilderQuestionForms(container);
  control.dispatchEvent({ type: 'input' });
  form.dispatchEvent({ type: 'submit', preventDefault() {} });
  await h.flush();
  const command = h.calls.submissions[0];
  assert.equal(h.state.sessionActivities.get('a').pendingInput, null);
  h.pending.get(command.attempt_id).reject(new Error('Question is stale'));
  await h.flush();
  assert.equal(h.state.builderQuestionDrafts.get('a').get('question').text, 'Keep Rowan');
  assert.equal(h.node('player-input').value, 'Separate composer draft');
  assert.equal(h.calls.submissions.length, 1);
});

test('paged Builder history loads on scroll, preserves position and ignores a departed page', async () => {
  const h = harness();
  const older = deferred();
  const calls = [];
  const message = (sequence) => ({ sequence, message_id: `saved-${sequence}`,
    kind: 'talk', content: { text: `Saved prose ${sequence}` } });
  h.state.bridge.session_context = async () => { throw new Error('Full history is forbidden'); };
  h.state.bridge.session_history_page = async (id, before) => {
    calls.push([id, before ?? null]);
    if (before != null) return older.promise;
    return { messages: [message(51), message(52)], next_before_sequence: 51,
      latest_sequence: 52, latest_user_sequence: 49 };
  };
  const container = h.node('chronology');
  Object.defineProperty(container, 'scrollHeight', { get() {
    return (container.dataset.chronologyHtml?.match(/story-card talk/g) || []).length * 100;
  } });
  await h.openBuilderChat('a');
  assert.deepEqual(calls, [['a', null]]);
  assert.match(container.dataset.chronologyHtml, /Saved prose 52/);
  assert.doesNotMatch(container.dataset.chronologyHtml, /Saved prose 50/);
  container.scrollTop = 10;
  container.dispatchEvent({ type: 'scroll' });
  container.dispatchEvent({ type: 'scroll' });
  assert.deepEqual(calls, [['a', null], ['a', 51]]);
  older.resolve({ messages: [message(49), message(50)], next_before_sequence: 49 });
  await h.flush();
  assert.match(container.dataset.chronologyHtml, /Saved prose 50/);
  assert.equal(container.scrollTop, 210);
  const late = deferred();
  h.state.bridge.session_history_page = async (id, before) => before != null ? late.promise : {
    messages: [{ ...message(1), content: { text: `Other session ${id}` } }],
    latest_sequence: 1, next_before_sequence: null,
  };
  container.scrollTop = 0;
  container.dispatchEvent({ type: 'scroll' });
  await h.openBuilderChat('b');
  late.resolve({ messages: [message(48)], next_before_sequence: null });
  await h.flush();
  assert.match(container.dataset.chronologyHtml, /Other session b/);
  assert.doesNotMatch(container.dataset.chronologyHtml, /Saved prose 48/);
});

for (const role of ['builder', 'dm']) test(`${role} refreshes replace the older-history handler`, async () => {
  const h = harness();
  if (role === 'dm') configureGM(h);
  const olderButton = element();
  h.node('chronology').querySelector = selector => selector === '[data-load-older]' ? olderButton : null;
  let olderReads = 0;
  h.state.bridge.session_history_page = async (id, before) => {
    if (before != null) olderReads++;
    return { latest_sequence: 80, next_before_sequence: before == null ? 79 : null,
      messages: [{ sequence: before == null ? 79 : 39, message_id: `message-${before ?? 79}`,
        kind: 'user', content: { text: before == null ? 'Recent action' : 'Earlier action' } }] };
  };
  if (role === 'dm') await h.openCampaign('campaign', 'gm');
  else await h.openBuilderChat('a');
  for (let refresh = 0; refresh < 20; refresh++) {
    if (role === 'dm') await h.renderDMSession('campaign', 'gm');
    else await h.renderBuilderSession('a');
  }
  assert.equal(olderButton.listenerCount('click'), 1,
    'unchanged history controls must not retain every prior render and its context');
  olderButton.dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(olderReads, 1);
  assert.match(h.node('chronology').dataset.chronologyHtml, /Earlier action/);
  assert.match(h.node('chronology').dataset.chronologyHtml, /Recent action/);
});

test('paged refresh fills a gap without dropping already loaded messages', async () => {
  const h = harness();
  let refreshed = false;
  const calls = [];
  const page = (sequences, cursor) => ({ messages: sequences.map((sequence) => ({ sequence,
    message_id: `m-${sequence}`, kind: 'talk', content: { text: `Line ${sequence}` } })),
    next_before_sequence: cursor, latest_sequence: refreshed ? 8 : 2 });
  h.state.bridge.session_history_page = async (id, before) => {
    calls.push(before ?? null);
    if (!refreshed) return page([1, 2], null);
    if (before === 7) return page([5, 6], 5);
    if (before === 5) return page([3, 4], 3);
    return page([7, 8], 7);
  };
  await h.openBuilderChat('a');
  refreshed = true;
  await h.renderBuilderSession('a');
  assert.deepEqual(calls, [null, null, 7, 5]);
  assert.deepEqual(Array.from(h.state.displayHistory.context.messages, (m) => m.sequence),
    [1, 2, 3, 4, 5, 6, 7, 8]);
});

test('a paged question stays closed when the later user answer is outside its page', async () => {
  const h = harness();
  h.state.bridge.session_history_page = async () => ({ latest_sequence: 80,
    latest_user_sequence: 70, next_before_sequence: 60, messages: [{
      sequence: 60, message_id: 'old-question', kind: 'evidence', content: {
        tool_name: 'ask_question', result: { success: true, status: 'awaiting_answer',
          widget: { type: 'select', data: {
            id: 'old', question: 'An answered question?', options: ['Yes'],
          } } },
      },
    }] });
  await h.openBuilderChat('a');
  assert.match(h.node('chronology').dataset.chronologyHtml, /A later reply closed these controls/);
  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /Submit answers/);
});

test('paged DM history keeps its canonical acknowledgement outside the visible page', async () => {
  const h = harness();
  configureGM(h);
  h.state.bridge.session_history_page = async () => ({ latest_sequence: 80,
    latest_canonical_sequence: 37, next_before_sequence: 79, messages: [{
      sequence: 79, message_id: 'latest-user', kind: 'user', content: { text: 'Next?' },
    }] });
  await h.openCampaign('campaign', 'gm');
  const activity = h.ensureSessionActivity({ sessionId: 'gm', sessionRole: 'dm', campaignId: 'campaign' });
  assert.equal(activity.acknowledgedSequence, 37);
});

test('canonical inventory bookkeeping stays inside current GM activity without a legacy receipt', async () => {
  const h = harness();
  configureGM(h);
  const result = { status: 'committed', tool: 'update_srd_carried_inventory',
    context: { private: 'PRIVATE INVENTORY' }, logs: [], narrative_directives: [] };
  h.state.bridge.session_context = async () => ({ messages: [
    { sequence: 1, message_id: 'input', kind: 'user', content: { text: 'Accept the tent.' } },
    { sequence: 2, message_id: 'inventory', kind: 'canonical_reference', canonical_sequence: 28,
      content: { event_type: 'tool_result', safe_payload: { status: 'committed', result } } },
    { sequence: 3, message_id: 'write', kind: 'evidence', content: {
      kind: 'provider_tool_results', results: [{ tool_call_id: 'write', name: 'update_campaign_file' }],
      activity: { attempt_id: 'a', basis_message_id: 'input', source_order: 2 } } },
    { sequence: 4, message_id: 'done', kind: 'evidence', content: {
      kind: 'gm_turn_activity', attempt_id: 'a', basis_message_id: 'input' } },
  ] });
  h.state.bridge.player_projection = async () => [{ kind: 'tool_card', sequence: 28,
    safe_payload: { status: 'committed', result, tool_name: 'update_srd_carried_inventory',
      routine_activity: 'character_update' } }];
  await h.openCampaign('campaign', 'gm');
  const html = h.node('chronology').dataset.chronologyHtml;
  assert.equal((html.match(/class="agent-activity"/g) || []).length, 1);
  assert.match(html, /Write to database/);
  assert.doesNotMatch(html, /Update Srd Carried Inventory|Operation completed|PRIVATE INVENTORY/i);
});

test('reopened package routine receipts group by their saved attempt without leaking private details', async () => {
  for (const correlated of ['evidence', 'page', null]) {
    const h = harness();
    configureGM(h);
    const result = { status: 'committed', activity_label: 'Prepare creature',
      context: { secret: 'PRIVATE CREATURE STATS' } };
    const activity = { attempt_id: 'a', basis_message_id: 'input', source_order: 1 };
    h.state.bridge.session_context = async () => ({ messages: [
      ...(correlated === 'page' ? [] : [
        { sequence: 1, message_id: 'input', kind: 'user', content: { text: 'Continue' } },
      ]),
      { sequence: 2, message_id: 'prepared', kind: 'canonical_reference', canonical_sequence: 28,
        content: { event_type: 'tool_result', safe_payload: { status: 'committed', result },
          ...(correlated === 'page' ? { activity } : {}) } },
      ...(correlated === 'evidence' ? [{ sequence: 3, message_id: 'correlation', kind: 'evidence',
        content: { kind: 'mechanics_activity', action_id: 'prepare',
          activity } }] : []),
      { sequence: 3, message_id: 'provider', kind: 'evidence', content: {
        kind: 'provider_tool_results', activity: { attempt_id: 'a', basis_message_id: 'input' },
        results: [{ name: 'custom_prepare', content: JSON.stringify({ action_id: 'prepare', result }) }],
      } },
    ] });
    h.state.bridge.player_projection = async () => [{ kind: 'tool_card', sequence: 28,
      safe_payload: { status: 'committed', result,
        action_id: 'prepare', tool_name: 'custom_prepare' } }];
    await h.openCampaign('campaign', 'gm');
    const html = h.node('chronology').dataset.chronologyHtml;
    assert.equal((html.match(/Prepare creature/g) || []).length, 1);
    assert.match(html, /GM activity/);
    assert.doesNotMatch(html, /PRIVATE CREATURE STATS|Resolve game action/);
    assert.doesNotMatch(html, /Operation |<summary>Details/); // Correlation is never invented to hide a receipt.
  }
});

test('bookkeeping classification preserves failure and authored result cards', async () => {
  for (const result of [
    { status: 'failed' }, { status: 'rejected' }, { status: 'committed', success: false },
    { status: 'committed', result_card: { version: 1, title: 'Inventory outcome',
      summary: [], details: [], actions: [] } },
  ]) {
    const h = harness();
    configureGM(h);
    h.state.bridge.session_context = async () => ({ messages: [
      { sequence: 1, message_id: 'inventory', kind: 'canonical_reference', canonical_sequence: 28,
        content: { event_type: 'tool_result', safe_payload: { status: 'committed', result } } },
    ] });
    h.state.bridge.player_projection = async () => [{ kind: 'tool_card', sequence: 28,
      safe_payload: { status: 'committed', result, tool_name: 'update_srd_carried_inventory',
        routine_activity: 'character_update' } }];
    await h.openCampaign('campaign', 'gm');
    const html = h.node('chronology').dataset.chronologyHtml;
    assert.doesNotMatch(html, /agent-operation|Operation |<summary>Details/);
    if (result.result_card) {
      assert.match(html, /story-card/);
      assert.match(html, /Inventory outcome/);
    } else {
      assert.match(html, /inline-feedback/);
      assert.match(html, /could not complete|unavailable/);
    }
  }
});

test('DM control enrichment follows the loaded history boundary when scrolling back', async () => {
  const h = harness();
  configureGM(h);
  const calls = [];
  const message = (sequence, canonical) => ({ sequence, canonical_sequence: canonical,
    message_id: `reference-${sequence}`, kind: 'canonical_reference',
    content: { event_type: 'talk', safe_payload: { text: `Narration ${sequence}` } } });
  h.state.bridge.player_projection = async () => { throw new Error('Full projection is forbidden'); };
  h.state.bridge.session_history_page = async (id, before) => ({ latest_sequence: 80,
    latest_canonical_sequence: 37, next_before_sequence: before == null ? 79 : null,
    messages: before == null ? [message(79, 37)] : [message(39, 19)] });
  h.state.bridge.player_projection_page = async (id, after) => { calls.push(after); return []; };
  await h.openCampaign('campaign', 'gm');
  assert.deepEqual(calls, [36]);
  h.node('chronology').scrollTop = 0;
  h.node('chronology').dispatchEvent({ type: 'scroll' });
  await h.flush();
  assert.deepEqual(calls, [36, 18]);
  assert.match(h.node('chronology').dataset.chronologyHtml, /Narration 39/);
  assert.match(h.node('chronology').dataset.chronologyHtml, /Narration 79/);
});

test('a later Builder question group does not collect controls superseded by an earlier reply', async () => {
  const h = harness();
  h.state.bridge.session_context = async () => ({ messages: [
    {
      message_id: 'old-question', kind: 'evidence', content: {
        tool_name: 'ask_question', result: {
          success: true, status: 'awaiting_answer', widget: { type: 'select', data: {
            id: 'old', question: 'Old question?', options: ['Old answer'],
          } },
        },
      },
    },
    { message_id: 'old-reply', kind: 'user', content: { text: 'Old answer' } },
    {
      message_id: 'new-question', kind: 'evidence', content: {
        tool_name: 'ask_question', result: {
          success: true, status: 'awaiting_answer', widget: { type: 'select', data: {
            id: 'new', question: 'New question?', options: [
              { value: 'path.navigator', label: 'Navigator' },
              { value: 'path.guardian', label: 'Guardian' },
            ],
          } },
        },
      },
    },
  ] });
  await h.openBuilderChat('a');
  const html = h.node('chronology').dataset.chronologyHtml;
  const oldCard = html.match(/data-chronology-key="old-question"[\s\S]*?<\/fieldset>/)?.[0] || '';
  assert.match(oldCard, /disabled/);
  assert.doesNotMatch(oldCard, /form="builder-question-form-a"/);

  const oldControl = h.makeElement();
  oldControl.dataset = {
    builderQuestionKey: 'old-question', builderQuestionType: 'select',
    builderQuestionText: 'Old question?', builderQuestionOrder: '0',
  };
  const newControl = h.makeElement();
  newControl.dataset = {
    builderQuestionKey: 'new-question', builderQuestionType: 'select',
    builderQuestionText: 'New question?', builderQuestionOrder: '2',
  };
  newControl.selectedOptions = [{ textContent: 'Choose an answer' }];
  newControl.setAttribute('form', 'builder-question-form-a');
  const submitButton = h.makeElement();
  const form = h.makeElement();
  form.id = 'builder-question-form-a';
  form.dataset = { builderQuestionSession: 'a' };
  form.querySelector = () => submitButton;
  const container = h.makeElement();
  container.querySelectorAll = (selector) => selector === '[data-builder-question-form]'
    ? [form] : [oldControl, newControl];
  h.wireBuilderQuestionForms(container);
  form.dispatchEvent({ type: 'submit', preventDefault() {} });
  await h.flush();
  assert.equal(h.calls.submissions.length, 0, 'the placeholder is not a player answer');
  assert.equal(newControl.focused, true);

  newControl.value = 'path.navigator';
  newControl.selectedOptions = [{ textContent: 'Navigator' }];
  form.dispatchEvent({ type: 'submit', preventDefault() {} });
  await h.flush();

  assert.equal(h.calls.submissions.length, 1);
  assert.equal(h.calls.submissions[0].user_text,
    'Here are my answers:\n\n1. New question?\nAnswer: Navigator');
});

test('two Builder chats keep their requests, drafts, steering and Stop separate', async () => {
  const h = harness();
  await h.openBuilderChat('a');
  h.node('player-input').value = 'Make a scout';
  const first = h.submitInput('Make a scout');
  await h.openBuilderChat('b');
  assert.equal(h.node('send-btn').disabled, false);
  assert.equal(h.node('stop-btn').disabled, true);
  h.node('player-input').value = 'Make a setting';
  const second = h.submitInput('Make a setting');
  await h.flush();
  assert.deepEqual(h.calls.submissions.map((c) => c.session_id), ['a', 'b']);
  assert.notEqual(h.calls.submissions[0].attempt_id, h.calls.submissions[1].attempt_id);
  assert.deepEqual(h.calls.heartbeat.map((c) => c[0]), ['a', 'b']);
  await h.submitInput('Use a desert');
  assert.equal(h.calls.steering[0][0], 'b');
  h.node('player-input').value = 'A new unsent draft';
  h.pending.get(h.calls.submissions[0].attempt_id).resolve({ status: 'completed' });
  await first;
  assert.equal(h.state.activeSessionId, 'b');
  assert.equal(h.node('player-input').value, 'A new unsent draft');
  assert.equal(h.node('stop-btn').disabled, false);
  assert.equal(h.state.sessionActivities.get('a').phase, 'completed');
  assert.equal(h.state.sessionActivities.get('b').phase, 'working');
  assert.match(h.node('builder-chat-rail').innerHTML, /Completed/);
  h.state.bridge.interrupt_provider_stream = async (...args) => {
    h.calls.stop.push(args);
    h.pending.get(args[1]).reject(new Error('cancelled'));
  };
  await h.stop();
  assert.deepEqual(h.calls.stop[0], ['b', h.calls.submissions[1].attempt_id]);
  await second;
  assert.equal(h.state.sessionActivities.get('b').phase, 'stopped');
  assert.equal(h.node('player-input').value, 'A new unsent draft');
});

test('host dictation bridge passes identity and language without audio or submission fields', async () => {
  const h = harness();
  const calls = [];
  h.window.__TAURI__ = { core: { invoke: async (...args) => { calls.push(args); } } };
  const bridge = h.createHostBridge();
  await bridge.dictation_capabilities();
  await bridge.dictation_capabilities('capture');
  await bridge.dictation_start('capture', 'installed-language');
  await bridge.dictation_stop('capture');
  await bridge.dictation_cancel('capture');
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ['dictation_capabilities'], ['dictation_capabilities', { id: 'capture' }],
    ['dictation_start', { id: 'capture', engine: 'installed-language' }],
    ['dictation_stop', { id: 'capture' }], ['dictation_cancel', { id: 'capture' }],
  ]);
});

test('host bridge carries structured question refs and exact answer values to the native command', async () => {
  const h = harness();
  const calls = [];
  h.window.__TAURI__ = { core: { invoke: async (...args) => { calls.push(args); } } };
  const bridge = h.createHostBridge();
  const answers = [{ question_message_id: 'question', value: '  Rowan\n<watcher>  ', custom: false }];
  await bridge.submit_session_input({ session_id: 'a', attempt_id: 'attempt', message_id: 'answer',
    user_text: 'genuine input', builder_question_answers: answers });
  assert.equal(calls[0][0], 'submit_session_input');
  assert.deepEqual(calls[0][1].builderQuestionAnswers, answers);
  await bridge.submit_session_input({ session_id: 'a', user_text: 'Plain chat' });
  assert.equal('builderQuestionAnswers' in calls[1][1], false);
});

test('cancel during language discovery prevents native capture and keeps the draft', async () => {
  const h = harness();
  await h.openBuilderChat('a');
  const capabilities = deferred();
  let starts = 0, loadingId;
  const cancellations = [];
  h.state.bridge.dictation_capabilities = id => { loadingId = id; return capabilities.promise; };
  h.state.bridge.dictation_start = async () => { starts += 1; };
  h.state.bridge.dictation_stop = async () => 'words';
  h.state.bridge.dictation_cancel = async id => { cancellations.push(id); };
  h.initDictation(); h.node('player-input').value = 'Keep this draft';
  const pending = h.getDictation().start();
  h.getDictation().cancel();
  capabilities.resolve({ engines: [{ id: 'test', language: 'en-GB' }] });
  await pending;
  assert.equal(starts, 0);
  assert.equal(typeof loadingId, 'string');
  assert.deepEqual(cancellations, [loadingId]);
  assert.equal(h.node('player-input').value, 'Keep this draft');
  assert.equal(h.calls.submissions.length, 0);
});

test('unavailable local speech leaves typing and explicit retry available', async () => {
  const h = harness();
  await h.openBuilderChat('a');
  let starts = 0;
  h.state.bridge.dictation_capabilities = async () => ({ engines: [] });
  h.state.bridge.dictation_start = async () => { starts += 1; };
  h.state.bridge.dictation_stop = async () => '';
  h.state.bridge.dictation_cancel = async () => {};
  h.initDictation(); h.node('player-input').value = 'Keep this draft';
  await h.getDictation().start();
  assert.equal(h.getDictation().snapshot().phase, 'error');
  assert.match(h.getDictation().snapshot().message, /Local dictation is unavailable/);
  assert.equal(starts, 0);
  assert.equal(h.node('player-input').disabled, false);
  assert.equal(h.node('player-input').value, 'Keep this draft');
});

test('missing local model offers explicit setup without capturing or sending the draft', async () => {
  const h = harness(); await h.openBuilderChat('a');
  let ready = false, starts = 0;
  const preparation = deferred();
  h.state.bridge.dictation_capabilities = async () => ({ ready, engines: [{ id: 'en', language: 'en' }],
    devices: [{ id: 'default', label: 'System default: Test microphone' }] });
  h.state.bridge.dictation_start = async () => { starts++; };
  h.state.bridge.dictation_stop = async () => 'words';
  h.state.bridge.dictation_cancel = async () => {};
  h.state.bridge.dictation_prepare = () => preparation.promise;
  h.initDictation(); h.node('player-input').value = 'Keep my typed action';
  await h.getDictation().start();
  assert.match(h.getDictation().snapshot().message, /Download the local speech model/);
  assert.equal(h.node('settings-overlay').hidden, false);
  assert.equal(h.node('settings-panel-dictation').hidden, false);
  assert.equal(h.node('dictation-download').hidden, false);
  h.node('dictation-download').dispatchEvent({ type: 'click' }); await h.flush();
  assert.match(h.node('dictation-model-status').textContent, /Downloading/);
  assert.equal(h.node('dictation-toggle').disabled, true);
  assert.equal(h.node('player-input').disabled, false);
  ready = true; preparation.resolve({ status: 'ready' }); await h.flush();
  assert.match(h.node('dictation-model-status').textContent, /ready/);
  assert.equal(starts, 0);
  assert.equal(h.calls.submissions.length, 0);
  assert.equal(h.node('player-input').value, 'Keep my typed action');
  await h.getDictation().start(); assert.equal(starts, 1);
  h.getDictation().cancel();
});

test('cancelling setup ignores late completion and keeps typing available', async () => {
  const h = harness(); await h.openBuilderChat('a');
  const preparation = deferred(), cancellations = [];
  h.state.bridge.dictation_capabilities = async () => ({ ready: false, engines: [{ id: 'en', language: 'en' }] });
  h.state.bridge.dictation_start = async () => { throw new Error('must not capture during setup'); };
  h.state.bridge.dictation_stop = async () => '';
  h.state.bridge.dictation_cancel = async id => cancellations.push(id);
  h.state.bridge.dictation_prepare = () => preparation.promise;
  h.initDictation(); h.node('player-input').value = 'My preserved draft';
  await h.getDictation().start();
  h.node('dictation-download').dispatchEvent({ type: 'click' }); await h.flush();
  h.node('dictation-download').dispatchEvent({ type: 'click' }); await h.flush();
  const before = h.node('dictation-model-status').textContent;
  assert.match(before, /cancelled/);
  preparation.resolve({ status: 'ready' }); await h.flush();
  assert.equal(h.node('dictation-model-status').textContent, before);
  assert.ok(cancellations.length >= 1);
  assert.equal(h.node('player-input').disabled, false);
  assert.equal(h.node('player-input').value, 'My preserved draft');
  assert.equal(h.calls.submissions.length, 0);
});

test('explicit language and microphone selections reach the capture contract unchanged', async () => {
  const h = harness(); await h.openBuilderChat('a'); const starts = [];
  h.state.bridge.dictation_capabilities = async () => ({ ready: true,
    engines: [{ id: 'auto', language: 'auto' }, { id: 'es', language: 'es' }],
    devices: [{ id: 'default', label: 'System default' }, { id: 'd-2-test', label: 'Selected input' }] });
  h.state.bridge.dictation_start = async (...args) => starts.push(args);
  h.state.bridge.dictation_stop = async () => '';
  h.state.bridge.dictation_cancel = async () => {};
  h.initDictation();
  h.node('dictation-language').value = 'es'; h.node('dictation-device').value = 'd-2-test';
  await h.getDictation().start();
  assert.equal(starts.length, 1);
  assert.equal(starts[0][1], 'es'); assert.equal(starts[0][2], 'd-2-test');
  h.getDictation().cancel();
});

test('a disappeared selected microphone is retained for explicit correction, never replaced by default', async () => {
  const h = harness(); await h.openBuilderChat('a'); const starts = [];
  h.state.bridge.dictation_capabilities = async () => ({ ready: true,
    engines: [{ id: 'en', language: 'en' }], devices: [{ id: 'default', label: 'System default' }] });
  h.state.bridge.dictation_start = async (...args) => { starts.push(args); throw 'device'; };
  h.state.bridge.dictation_stop = async () => '';
  h.state.bridge.dictation_cancel = async () => {};
  h.initDictation(); h.node('player-input').value = 'Keep my correction';
  h.node('dictation-device').value = 'd-2-unplugged';
  await h.getDictation().start();
  assert.equal(starts[0][2], 'd-2-unplugged');
  assert.equal(h.node('dictation-device').value, 'd-2-unplugged');
  assert.match(h.getDictation().snapshot().message, /selected microphone/);
  assert.equal(h.node('player-input').value, 'Keep my correction');
  assert.equal(h.calls.submissions.length, 0);
});

test('model download failure is redacted, preserves the draft and allows explicit retry', async () => {
  const h = harness(); await h.openBuilderChat('a'); let attempts = 0, ready = false;
  h.state.bridge.dictation_capabilities = async () => ({ ready, engines: [{ id: 'en', language: 'en' }] });
  h.state.bridge.dictation_start = async () => { throw new Error('setup must not capture'); };
  h.state.bridge.dictation_stop = async () => '';
  h.state.bridge.dictation_cancel = async () => {};
  h.state.bridge.dictation_prepare = async () => {
    attempts++; if (attempts === 1) throw 'private network details'; ready = true; return { status: 'ready' };
  };
  h.initDictation(); h.node('player-input').value = 'Keep this action';
  await h.getDictation().start();
  h.node('dictation-download').dispatchEvent({ type: 'click' }); await h.flush();
  assert.match(h.node('dictation-model-status').textContent, /download failed/);
  assert.doesNotMatch(h.node('dictation-model-status').textContent, /private/);
  assert.equal(h.node('player-input').value, 'Keep this action');
  h.node('dictation-download').dispatchEvent({ type: 'click' }); await h.flush();
  assert.equal(attempts, 2);
  assert.match(h.node('dictation-model-status').textContent, /ready/);
  assert.equal(h.calls.submissions.length, 0);
});

test('late readiness metadata cannot overwrite a cancelled setup state', async () => {
  const h = harness(); await h.openBuilderChat('a'); const metadata = deferred(); let reads = 0;
  h.state.bridge.dictation_capabilities = () => ++reads <= 2
    ? Promise.resolve({ ready: false, engines: [{ id: 'en', language: 'en' }] }) : metadata.promise;
  h.state.bridge.dictation_start = async () => {};
  h.state.bridge.dictation_stop = async () => '';
  h.state.bridge.dictation_cancel = async () => {};
  h.state.bridge.dictation_prepare = async () => ({ status: 'ready' });
  h.initDictation(); h.node('player-input').value = 'Keep my draft';
  await h.getDictation().start();
  h.node('dictation-download').dispatchEvent({ type: 'click' }); await h.flush();
  assert.equal(reads, 3); // initial attempt, actual Settings opening, then completed-download refresh
  h.node('dictation-download').dispatchEvent({ type: 'click' }); await h.flush();
  const cancelled = h.node('dictation-model-status').textContent;
  metadata.resolve({ ready: true, engines: [{ id: 'en', language: 'en' }] }); await h.flush();
  assert.match(cancelled, /cancelled/);
  assert.equal(h.node('dictation-model-status').textContent, cancelled);
  assert.equal(h.node('player-input').value, 'Keep my draft');
  assert.equal(h.calls.submissions.length, 0);
});

for (const accepted of [true, false]) for (const early of [true, false]) test(`Builder ${accepted ? 'accepted' : 'rejected'} steering preserves dictation ownership (${early ? 'during' : 'after'} request)`, async () => {
  const h = harness();
  await h.openBuilderChat('a');
  const running = h.submitInput('Make a scout');
  await h.flush();
  const transcript = deferred();
  const steering = deferred();
  let cancelled = 0;
  h.state.bridge.dictation_capabilities = async () => ({ engines: [{ id: 'test', language: 'en-GB', label: 'English' }] });
  h.state.bridge.dictation_start = async () => {};
  h.state.bridge.dictation_stop = () => transcript.promise;
  h.state.bridge.dictation_cancel = async () => { cancelled += 1; };
  h.state.bridge.steer_session = () => steering.promise;
  h.initDictation();
  const input = h.node('player-input');
  input.value = 'Use a desert'; input.setSelectionRange(input.value.length, input.value.length);
  const dictation = h.getDictation();
  await dictation.start();
  const stopping = dictation.stop();
  const sending = h.submitInput(input.value);
  if (early) { transcript.resolve('late captured words'); await stopping; }
  if (accepted) steering.resolve({}); else steering.reject(new Error('rejected'));
  await sending;
  if (!early) transcript.resolve('late captured words');
  await stopping;
  assert.equal(input.value, accepted ? '' : 'Use a desert');
  assert.equal(dictation.snapshot().transcript, '');
  assert.equal(cancelled, 1);
  assert.equal(h.calls.submissions.length, 1);
  h.pending.get(h.calls.submissions[0].attempt_id).resolve({ status: 'completed' });
  await running;
});

test('Stop waits for owner cancellation to settle before starting another request', async () => {
  const h = harness();
  await h.openBuilderChat('a');
  const oldRequest = h.submitInput('First');
  const stopping = h.stop();
  await h.flush();
  assert.equal(h.state.sessionActivities.get('a').phase, 'working');
  assert.equal(h.node('stop-btn').disabled, true);
  h.pending.get(h.calls.submissions[0].attempt_id).reject(new Error('late cancellation'));
  await stopping;
  await oldRequest;
  const newRequest = h.submitInput('Second');
  const current = h.state.sessionActivities.get('a');
  assert.equal(h.state.sessionActivities.get('a'), current);
  assert.equal(current.phase, 'working');
  assert.equal(h.node('stop-btn').disabled, false);
  assert.ok(h.timers.has(current.heartbeatTimer));
  assert.ok(h.timers.has(current.projectionTimer));
  h.pending.get(h.calls.submissions[1].attempt_id).resolve({ status: 'completed' });
  await newRequest;
});

test('a late projection cannot repaint another chat or a newer attempt', async () => {
  const h = harness();
  await h.openBuilderChat('a');
  const late = deferred();
  const originalRead = h.state.bridge.session_context;
  h.state.bridge.session_context = (id) => id === 'a' ? late.promise : originalRead(id);
  const painting = h.renderBuilderSession('a');
  await h.openBuilderChat('b');
  late.resolve({ messages: [{ kind: 'talk', content: { text: 'Late answer a' } }] });
  await painting;
  assert.match(h.node('chronology').dataset.chronologyHtml, /Answer b/);
  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /Late answer a/);
});

test('reload reconnects all working chats without navigating or losing a transient failure', async () => {
  const h = harness();
  await h.openBuilderChat('a');
  const a = h.submitInput('A');
  await h.openBuilderChat('b');
  const b = h.submitInput('B');
  await h.flush();
  h.signalActiveStreamDisconnect();
  assert.deepEqual(h.calls.disconnect.map((c) => c[0]), ['a', 'b']);
  const restored = harness(new Map(h.storage));
  const originalReconnect = restored.state.bridge.reconnect_provider_stream;
  restored.state.bridge.reconnect_provider_stream = (id, ...args) => {
    if (id === 'a') return Promise.reject(new Error('temporary observation failure'));
    return originalReconnect(id, ...args);
  };
  await restored.restoreDisconnectedStream();
  assert.equal(restored.state.activeSessionId, null);
  assert.equal(restored.state.sessionActivities.get('a').phase, 'working');
  assert.equal(restored.state.sessionActivities.get('b').phase, 'working');
  const hints = JSON.parse(restored.storage.get('sylvia-gm-disconnected-stream'));
  assert.equal(hints.streams.length, 2);
  assert.equal(restored.calls.reconnect[0][0], 'b');
  for (const command of h.calls.submissions) h.pending.get(command.attempt_id).resolve({ status: 'completed' });
  await Promise.all([a, b]);
});

test('a rejected reconnect reports only a session that is still working as Reconnecting', async () => {
  const completed = harness();
  await completed.openBuilderChat('a');
  const work = completed.submitInput('Finish this response');
  await completed.flush();
  const completedActivity = completed.state.sessionActivities.get('a');
  const reconnectResult = deferred();
  completed.state.bridge.reconnect_provider_stream = () => reconnectResult.promise;
  const reconnecting = completed.reconnect(
    completedActivity.attemptId,
    completedActivity.acknowledgedSequence,
    completedActivity.attachmentGeneration,
    completedActivity,
  );

  completed.pending.get(completedActivity.attemptId).resolve({ status: 'completed' });
  await work;
  assert.equal(completedActivity.phase, 'completed');
  assert.equal(completed.node('session-status-text').textContent, '');
  assert.equal(completed.node('session-status-text').hidden, true);

  reconnectResult.reject(new Error('late reconnect observation failure'));
  await reconnecting;

  assert.equal(completedActivity.phase, 'completed');
  assert.equal(completed.node('session-status-text').textContent, '');
  assert.equal(completed.node('session-status-text').hidden, true);
  assert.equal(completed.node('send-btn').disabled, false);
  assert.equal(completed.node('stop-btn').disabled, true);

  const working = harness();
  await working.openBuilderChat('a');
  const unfinished = working.submitInput('Keep this response running');
  await working.flush();
  const workingActivity = working.state.sessionActivities.get('a');
  working.state.bridge.reconnect_provider_stream = async () => {
    throw new Error('temporary reconnect observation failure');
  };

  await working.reconnect(
    workingActivity.attemptId,
    workingActivity.acknowledgedSequence,
    workingActivity.attachmentGeneration,
    workingActivity,
  );

  assert.equal(workingActivity.phase, 'working');
  assert.equal(working.node('session-status-text').textContent, 'Reconnecting…');
  assert.equal(working.node('send-btn').disabled, false);
  assert.equal(working.node('stop-btn').disabled, false);

  working.pending.get(workingActivity.attemptId).resolve({ status: 'completed' });
  await unfinished;
});

function configureGM(h) {
  const session = { session_id: 'gm', role: 'dm', status: 'active', provider_id: 'test', model_id: 'test' };
  h.state.campaigns = [{ campaign_id: 'campaign', display_name: 'Campaign', lifecycle: 'active',
    sessions: [session], session_id: 'gm', writer: 'dm', turn_id: 'turn' }];
  h.state.bridge.snapshot = async () => ({ campaign_id: 'campaign', display_name: 'Campaign',
    lifecycle: 'active', writer: 'dm', revision: 1 });
  h.state.bridge.player_projection = async () => [];
  h.state.bridge.stream_heartbeat = async (...args) => {
    h.calls.heartbeat.push(args); return { attachment_generation: 3 };
  };
  h.state.bridge.interrupt_stream = async (...args) => h.calls.stop.push(args);
}

for (const pendingPoll of [false, true]) {
  test(`completed GM reply refreshes history after submission (pending poll: ${pendingPoll})`, async () => {
    const h = harness();
    configureGM(h);
    h.state.bridge.session_history_page = async () => ({ messages: [], latest_sequence: 0 });
    await h.openCampaign('campaign', 'gm');
    const stale = deferred();
    let completed = false;
    const reads = [];
    const reply = { text: 'The gate opens.', response_blocks: [
      { kind: 'narration', text: 'The gate opens.' },
      { kind: 'npc_dialogue', text: 'Welcome back.', speaker_name: 'Gatekeeper' },
    ] };
    const oldPage = { latest_sequence: 2, messages: [
      { sequence: 1, message_id: 'user-1', kind: 'user', content: { text: 'Knock.' } },
      { sequence: 2, message_id: 'activity-1', kind: 'evidence',
        content: { kind: 'gm_turn_activity', status: 'turn_completed' } },
    ] };
    h.state.bridge.session_history_page = async () => {
      reads.push(completed);
      if (!completed) return pendingPoll ? stale.promise : oldPage;
      return { latest_sequence: 3, messages: [...oldPage.messages,
        { sequence: 3, message_id: 'reply-1', kind: 'canonical_reference', canonical_sequence: 1,
          content: { event_type: 'talk', safe_payload: reply } },
      ] };
    };
    h.state.bridge.player_projection_page = async () => completed
      ? [{ kind: 'talk', sequence: 1, safe_payload: reply }] : [];
    const submitting = h.submitInput('Knock.');
    await h.flush();
    completed = true;
    h.pending.get(h.calls.submissions[0].attempt_id).resolve({ status: 'completed' });
    await h.flush();
    stale.resolve(oldPage);
    await submitting;
    await h.flush();
    const html = h.node('chronology').dataset.chronologyHtml;
    assert.match(html, /The gate opens/);
    assert.match(html, /Welcome back/);
    assert.deepEqual(reads, [false, true], 'completion must obtain a post-completion history page');
    assert.equal((html.match(/The gate opens/g) || []).length, 1);
    assert.equal(h.calls.submissions.length, 1);
    assert.equal(h.state.sessionActivities.get('gm').phase, 'completed');
  });
}

test('failed final GM history read offers display recovery without repeating inference', async () => {
  const h = harness();
  configureGM(h);
  const retryDisplay = element();
  h.node('chronology').querySelector = (selector) => selector === '[data-retry-display]' ? retryDisplay : null;
  h.state.bridge.session_history_page = async () => ({ messages: [], latest_sequence: 0 });
  await h.openCampaign('campaign', 'gm');
  const stale = deferred();
  let reads = 0;
  h.state.bridge.session_history_page = async () => {
    if (++reads === 1) return stale.promise;
    throw new Error('History transport unavailable');
  };
  const submitting = h.submitInput('Look around.');
  await h.flush();
  h.pending.get(h.calls.submissions[0].attempt_id).resolve({ status: 'completed' });
  await h.flush();
  stale.reject(new Error('Old poll failed too'));
  await submitting;
  assert.equal(reads, 2, 'a failed invalidated poll must not suppress the fresh read');
  assert.match(h.node('chronology').dataset.chronologyHtml, /could not be loaded/);
  assert.match(h.node('chronology').dataset.chronologyHtml, /Retry loading/);
  assert.equal(h.state.sessionActivities.get('gm').phase, 'completed');
  h.state.bridge.session_history_page = async () => ({ latest_sequence: 1, messages: [
    { sequence: 1, message_id: 'reply', kind: 'canonical_reference', canonical_sequence: 1,
      content: { event_type: 'talk', safe_payload: { text: 'The saved room description.' } } },
  ] });
  retryDisplay.dispatchEvent({ type: 'click' });
  await h.flush();
  await h.flush();
  assert.match(h.node('chronology').dataset.chronologyHtml, /The saved room description/);
  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /could not be loaded/);
  assert.equal(h.calls.submissions.length, 1);
});

test('invalidated GM history cannot replace a newer selected chat', async () => {
  const h = harness();
  configureGM(h);
  h.state.bridge.session_history_page = async () => ({ messages: [], latest_sequence: 0 });
  await h.openCampaign('campaign', 'gm');
  const stale = deferred();
  const reads = [];
  h.state.bridge.session_history_page = async (id) => {
    reads.push(id);
    if (id === 'gm') return stale.promise;
    return { messages: [{ sequence: 1, message_id: 'builder', kind: 'talk',
      content: { text: 'New selected Builder history.' } }], latest_sequence: 1 };
  };
  const submitting = h.submitInput('Look around.');
  await h.flush();
  h.pending.get(h.calls.submissions[0].attempt_id).resolve({ status: 'completed' });
  await h.flush();
  await h.openBuilderChat('a');
  stale.resolve({ latest_sequence: 1, messages: [{ sequence: 1, message_id: 'stale',
    kind: 'canonical_reference', canonical_sequence: 1,
    content: { event_type: 'talk', safe_payload: { text: 'Late GM reply.' } } }] });
  await submitting;
  await h.flush();
  assert.equal(h.state.activeSessionId, 'a');
  assert.match(h.node('chronology').dataset.chronologyHtml, /New selected Builder history/);
  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /Late GM reply/);
  assert.deepEqual(reads, ['gm', 'a']);
  assert.equal(h.calls.submissions.length, 1);
});

test('manual GM compaction uses session transport and preserves unsent player drafts', async () => {
  const h = harness();
  configureGM(h);
  await h.openCampaign('campaign', 'gm');
  h.node('player-input').value = 'Keep my next action';
  h.node('ooc-input').value = 'Keep my correction';
  const running = h.submitInput('/compact');
  await h.flush();
  const command = h.calls.submissions[0];
  assert.equal(command.user_text, '/compact');
  assert.equal(command.ooc_text, null);
  assert.equal(h.state.sessionActivities.get('gm').pendingInput, null);
  assert.equal(h.calls.heartbeat[0][0], 'gm');
  h.state.bridge.session_context = async () => ({ messages: [
    { kind: 'evidence', message_id: 'compact', content: {
      kind: 'working_context_checkpoint', summary: 'Private villain identity',
    } },
  ], context_status_hint: 'Compacting context · saved conversation is kept' });
  await h.renderDMSession('campaign', 'gm');
  assert.match(h.node('session-status-text').textContent, /Compacting context/);
  assert.doesNotMatch(h.node('chronology').innerHTML, /Private villain/);
  h.pending.get(command.attempt_id).resolve({ status: 'context_compacted' });
  await running;
  assert.equal(h.node('player-input').value, 'Keep my next action');
  assert.equal(h.node('ooc-input').value, 'Keep my correction');
  assert.equal(h.state.sessionActivities.get('gm').contextCompaction, false);
  assert.equal(h.state.sessionActivities.get('gm').ownerId, 'campaign');
});

test('automatic action response retains its campaign and both unsent drafts after navigation', async () => {
  const h = harness();
  configureGM(h);
  await h.openCampaign('campaign', 'gm');
  h.node('player-input').value = 'My next unsent action';
  h.node('ooc-input').value = 'My unsent OOC note';
  const offer = deferred();
  h.state.bridge.campaign_panels = () => offer.promise;
  const response = h.respondToPlayerAction('campaign', 'gm', 'move');
  await h.openBuilderChat('a');
  h.node('player-input').value = 'Unsent Builder text';
  offer.resolve({ response_action: {
    action_id: 'move', session_id: 'gm', automatic: true, available: true,
  }, surfaces: [] });
  await h.flush();
  assert.equal(h.calls.submissions.length, 1);
  const command = h.calls.submissions[0];
  assert.equal(command.session_id, 'gm');
  assert.equal(command.player_action_id, 'move');
  assert.equal(command.automatic_response, true);
  assert.equal(command.user_text, '');
  assert.equal(command.ooc_text, null);
  h.pending.get(command.attempt_id).resolve({ status: 'completed' });
  await response;
  assert.equal(h.node('player-input').value, 'Unsent Builder text');
  await h.openCampaign('campaign', 'gm');
  assert.equal(h.node('player-input').value, 'My next unsent action');
  assert.equal(h.node('ooc-input').value, 'My unsent OOC note');
});

test('on-request actions wait for an explicit response without consuming typed input', async () => {
  const h = harness();
  configureGM(h);
  await h.openCampaign('campaign', 'gm');
  h.state.bridge.campaign_panels = async () => ({ response_action: {
    action_id: 'move', session_id: 'gm', automatic: false, available: true,
  }, surfaces: [] });
  await h.respondToPlayerAction('campaign', 'gm', 'move');
  assert.equal(h.calls.submissions.length, 0);
  h.node('player-input').value = 'A draft to send later';
  const response = h.submitInput('', { playerAction: {
    campaignId: 'campaign', sessionId: 'gm', actionId: 'move', automatic: false,
  } });
  const command = h.calls.submissions[0];
  assert.equal(command.automatic_response, false);
  h.pending.get(command.attempt_id).resolve({ status: 'completed' });
  await response;
  assert.equal(h.node('player-input').value, 'A draft to send later');
});

test('reopening a session uses the saved campaign name even when its snapshot title is older', async () => {
  const h = harness();
  configureGM(h);
  h.state.campaigns[0].display_name = 'PES Companion Return · Interview';
  await h.openCampaign('campaign', 'gm');
  assert.equal(h.node('session-title').textContent, 'PES Companion Return · Interview');
  assert.equal(h.node('ctx-campaign').textContent, 'PES Companion Return · Interview');
});

test('opening a saved campaign names the pending campaign before projections load', async () => {
  const h = harness();
  configureGM(h);
  const context = deferred();
  const projection = deferred();
  h.state.bridge.session_context = () => context.promise;
  h.state.bridge.player_projection = () => projection.promise;
  h.node('session-title').textContent = 'No campaign selected';
  h.node('chronology').innerHTML =
    '<div>No campaign open. Select or create a campaign from the sidebar.</div>';

  const opening = h.openCampaign('campaign', 'gm');
  await h.flush();

  assert.equal(h.node('session-title').textContent, 'Campaign');
  assert.equal(h.node('session-status-text').textContent, '');
  assert.match(h.node('chronology').dataset.chronologyHtml, /Opening Campaign…/);
  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /No campaign open/);

  context.resolve({ session: h.state.campaigns[0].sessions[0], messages: [] });
  projection.resolve([]);
  await opening;
  assert.equal(h.node('session-title').textContent, 'Campaign');
  assert.equal(h.node('session-status-text').textContent, '');
});

test('routine selected-campaign refresh keeps rendered chronology while owner replies are pending', async () => {
  const h = harness();
  configureGM(h);
  await h.openCampaign('campaign', 'gm');
  const existingHistory = '<article class="story-card">Existing history</article>';
  h.node('chronology').innerHTML = existingHistory;
  h.node('chronology').dataset.chronologyHtml = existingHistory;
  const context = deferred();
  const projection = deferred();
  h.state.bridge.session_context = () => context.promise;
  h.state.bridge.player_projection = () => projection.promise;

  const refreshing = h.refreshSelectedCampaign();
  await h.flush();

  assert.equal(h.node('chronology').dataset.chronologyHtml, existingHistory);
  assert.equal(h.node('session-status-text').textContent, '');

  context.resolve({ session: h.state.campaigns[0].sessions[0], messages: [] });
  projection.resolve([]);
  await refreshing;
});

test('chat appears while game controls load, then panels load once and input becomes available', async () => {
  const h = harness();
  configureGM(h);
  const projection = deferred(), panels = deferred();
  let panelRequests = 0;
  h.state.bridge.session_context = async () => ({ messages: [
    { kind: 'user', message_id: 'latest', content: { text: 'Saved player message' } },
  ] });
  h.state.bridge.player_projection = () => projection.promise;
  h.state.bridge.campaign_panels = () => { panelRequests++; return panels.promise; };
  const opening = h.openCampaign('campaign', 'gm');
  await h.flush();
  assert.match(h.node('chronology').dataset.chronologyHtml, /Saved player message/);
  assert.equal(h.node('send-btn').disabled, true);
  assert.match(h.node('ruleset-panels').innerHTML, /Loading game panels/);
  projection.resolve([]);
  panels.resolve({ campaign_id: 'campaign', ruleset_id: 'test', revision: 1, surfaces: [] });
  await opening;
  assert.equal(panelRequests, 1);
  assert.equal(h.node('send-btn').disabled, false);
});

test('leaving while snapshot loads skips its remaining panel work and preserves the new selection', async () => {
  const h = harness();
  configureGM(h);
  const snapshot = deferred();
  let panelRequests = 0;
  const cancelled = [];
  h.state.bridge.cancel_display_load = async (id) => cancelled.push(id);
  h.state.bridge.snapshot = () => snapshot.promise;
  h.state.bridge.campaign_panels = async () => { panelRequests++; return { surfaces: [] }; };
  const opening = h.openCampaign('campaign', 'gm');
  const loadId = h.state.campaignOpening.id;
  await h.flush();
  await h.openBuilderChat('a');
  snapshot.resolve({ campaign_id: 'campaign', lifecycle: 'active', revision: 1 });
  await opening;
  assert.equal(panelRequests, 0);
  assert.deepEqual(cancelled, [loadId]);
  assert.deepEqual(h.calls.stop, []);
  assert.equal(h.state.activeSessionId, 'a');
  assert.match(h.node('chronology').dataset.chronologyHtml, /Answer a/);
  assert.equal(h.node('send-btn').disabled, false);
});

test('Builder departure replaces pending campaign history before its own read completes', async () => {
  const h = harness();
  configureGM(h);
  const campaign = deferred(), builder = deferred();
  h.state.bridge.session_context = (id) => id === 'gm' ? campaign.promise : builder.promise;
  const opening = h.openCampaign('campaign', 'gm');
  await h.flush();
  const switching = h.openBuilderChat('a');
  await h.flush();
  assert.match(h.node('chronology').dataset.chronologyHtml, /Opening Builder a/);
  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /Opening Campaign/);
  campaign.resolve({ messages: [] });
  await opening;
  assert.match(h.node('chronology').dataset.chronologyHtml, /Opening Builder a/);
  builder.resolve({ messages: [{ kind: 'user', message_id: 'new', content: { text: 'Builder history' } }] });
  await switching;
  assert.match(h.node('chronology').dataset.chronologyHtml, /Builder history/);
});

test('failed Builder opening gives a history retry instead of keeping its loading placeholder', async () => {
  const h = harness();
  const retry = element();
  h.node('chronology').querySelector = (selector) =>
    selector === '[data-retry-builder-history]' ? retry : null;
  h.state.bridge.session_context = async () => { throw new Error('unavailable'); };
  await h.openBuilderChat('a');
  assert.match(h.node('chronology').dataset.chronologyHtml, /Saved history could not be loaded/);
  assert.match(h.node('chronology').dataset.chronologyHtml, /Retry loading/);
  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /Opening Builder/);
  h.state.bridge.session_context = async () => ({ messages: [
    { kind: 'user', message_id: 'recovered', content: { text: 'Recovered saved history' } },
  ] });
  retry.dispatchEvent({ type: 'click' });
  await h.flush();
  assert.match(h.node('chronology').dataset.chronologyHtml, /Recovered saved history/);
  assert.equal(h.calls.submissions.length, 0);
});

test('failed player projection keeps saved chat and offers a display retry without claiming ready', async () => {
  const h = harness();
  configureGM(h);
  const context = deferred();
  const projection = deferred();
  h.state.bridge.session_context = () => context.promise;
  h.state.bridge.player_projection = () => projection.promise;

  const opening = h.openCampaign('campaign', 'gm');
  await h.flush();
  assert.match(h.node('chronology').dataset.chronologyHtml, /Opening Campaign…/);

  context.resolve({ session: h.state.campaigns[0].sessions[0], messages: [
    { kind: 'user', message_id: 'saved', content: { text: 'Keep my saved message' } },
  ] });
  projection.reject(new Error('projection unavailable'));
  await opening;

  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /Opening Campaign…/);
  assert.match(h.node('chronology').dataset.chronologyHtml, /Keep my saved message/);
  assert.match(h.node('campaign-loading-status').innerHTML, /Retry loading/);
  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /The DM is ready/);
  assert.match(h.node('campaign-loading-status').innerHTML, /could not be loaded/);
  assert.equal(h.node('session-status-text').textContent, '');
  assert.equal(h.node('send-btn').disabled, true);
});

test('stopped GM accepts replacement input while preserving its saved continuation basis', async () => {
  const h = harness();
  configureGM(h);
  await h.openCampaign('campaign', 'gm');
  const running = h.submitInput('Attack the guard');
  const original = h.calls.submissions[0];
  h.state.bridge.interrupt_stream = async () => {
    h.state.bridge.player_projection = async () => [{ kind: 'retry', safe_payload: {
      failed_attempt_id: original.attempt_id, interruption_id: 'stopped-turn',
      saved_changes: true, can_redirect: true,
    } }];
    h.pending.get(original.attempt_id).resolve({ interruption: { interruption_id: 'stopped-turn' } });
  };
  await h.stop();
  await running;
  assert.equal(h.node('player-input').disabled, false);
  assert.equal(h.node('send-btn').disabled, false);
  h.node('ooc-input').value = 'Keep it brief';
  const redirecting = h.submitInput('');
  assert.equal(h.calls.submissions.length, 2);
  const replacement = h.calls.submissions[1];
  assert.equal(replacement.redirect_interruption_id, 'stopped-turn');
  assert.equal(replacement.ooc_text, 'Keep it brief');
  h.state.bridge.player_projection = async () => [];
  h.pending.get(replacement.attempt_id).resolve({ status: 'completed' });
  await redirecting;
  assert.equal(h.state.sessionActivities.get('gm').retryBasis, null);
});

test('reasoning stays unavailable while a campaign has no selected session projection', () => {
  const h = harness(new Map(), { shippedDom: true });
  h.state.activeSessionId = undefined;
  h.state.sessionReasoning = null;
  assert.doesNotThrow(() => h.renderReasoningPicker());
  assert.equal(h.node('chat-reasoning-btn').disabled, true);
  assert.equal(h.node('chat-reasoning-btn').textContent, 'Reasoning unavailable');
});

test('GM input and retry stay with the campaign while another Builder works', async () => {
  const h = harness();
  configureGM(h);
  await h.openCampaign('campaign', 'gm');
  const turn = h.submitInput('Search the room');
  assert.equal(h.node('send-btn').disabled, true);
  await h.openBuilderChat('a');
  assert.equal(h.node('send-btn').disabled, false);
  const building = h.submitInput('Create a character');
  const gmCommand = h.calls.submissions[0];
  h.pending.get(gmCommand.attempt_id).resolve({ interruption: { interruption_id: 'interruption' } });
  await turn;
  assert.equal(h.node('stop-btn').disabled, false);
  assert.equal(h.state.sessionActivities.get('a').phase, 'working');
  h.state.bridge.player_projection = async () => [{ kind: 'retry', safe_payload: {
    failed_attempt_id: gmCommand.attempt_id, interruption_id: 'interruption', saved_changes: true,
  } }];
  await h.openCampaign('campaign', 'gm');
  assert.equal(h.node('send-btn').disabled, true);
  assert.match(h.node('chronology').dataset.chronologyHtml, /data-action="manual-retry"/);
  const retryWait = deferred();
  let retryArgs;
  h.state.bridge.manual_retry_session = (...args) => { retryArgs = args; return retryWait.promise; };
  const retrying = h.retry();
  assert.equal(h.node('send-btn').disabled, true);
  await h.openBuilderChat('a');
  retryWait.resolve({ status: 'completed' });
  await retrying;
  assert.equal(retryArgs[0], 'gm');
  assert.equal(retryArgs[1], 'interruption');
  assert.equal(h.state.activeSessionId, 'a');
  assert.equal(h.node('stop-btn').disabled, false);
  assert.equal(h.state.sessionActivities.get('a').phase, 'working');
  h.pending.get(h.calls.submissions[1].attempt_id).resolve({ status: 'completed' });
  await building;
});

test('campaign opening continues in its GM session after navigation to Builder', async () => {
  const h = harness();
  configureGM(h);
  const interview = { session_id: 'interview', role: 'builder', status: 'active', provider_id: 'test', model_id: 'test' };
  h.state.campaigns[0].sessions.push(interview);
  h.state.modelChoices = [{ provider_id: 'test', model_id: 'test' }];
  h.node('chat-model-choice').value = '0';
  h.state.bridge.model_guidance = async () => ({ warning_required: false });
  const handoff = deferred(), opening = deferred();
  h.state.bridge.finalize_campaign_construction = () => handoff.promise;
  let openingArgs;
  h.state.bridge.start_dm_session = (...args) => { openingArgs = args; return opening.promise; };
  await h.openCampaign('campaign', 'interview');
  const starting = h.finalizeSelectedConstruction();
  await h.flush();
  await h.openBuilderChat('b');
  handoff.resolve({ handoff: { dm_session_id: 'gm', resulting_campaign_revision: 2 },
    dm_attempt_id: 'attempt-campaign-start-test', dm_turn_id: 'turn', plan: { plan_revision: 1 } });
  await h.flush();
  assert.deepEqual(openingArgs, ['campaign', 'gm', 'turn']);
  assert.equal(h.state.activeSessionId, 'b');
  assert.equal(h.state.sessionActivities.get('gm').phase, 'working');
  assert.equal(h.node('send-btn').disabled, false);
  h.node('player-input').value = 'My draft';
  opening.resolve({ status: 'completed' });
  await starting;
  assert.equal(h.state.activeSessionId, 'b');
  assert.equal(h.node('player-input').value, 'My draft');
  assert.equal(h.state.sessionActivities.get('gm').phase, 'completed');
  // The next interview reuses the warning controls after the first handoff leaves it.
  h.state.bridge.model_guidance = async () => ({ warning_required: true, status: 'unrated' });
  await h.openCampaign('campaign', 'interview');
  await h.finalizeSelectedConstruction();
  assert.equal(h.node('model-guidance-warning').hidden, false);
  assert.equal(h.node('continue-model-guidance-btn').disabled, false);
  assert.equal(h.node('dismiss-model-guidance-btn').disabled, false);
});

test('a failed snapshot from an earlier campaign cannot clear the selected Builder', async () => {
  const h = harness();
  configureGM(h);
  const snapshot = deferred();
  h.state.bridge.snapshot = () => snapshot.promise;
  const opening = h.openCampaign('campaign', 'gm');
  await h.flush();
  await h.openBuilderChat('b');
  snapshot.reject(new Error('not available'));
  await opening;
  assert.equal(h.node('session-title').textContent, 'Builder b');
  assert.equal(h.node('session-status-text').textContent, '');
  assert.equal(h.node('send-btn').disabled, false);
});

test('an application-started Builder targets its saved chat even after the player navigates away', async () => {
  const h = harness();
  await h.openBuilderChat('b');
  h.node('player-input').value = 'Unsent in b';
  const starting = h.submitInput('', { builderStart: true, builderSessionId: 'a' });
  assert.equal(h.calls.submissions[0].session_id, 'a');
  assert.equal(h.calls.submissions[0].builder_start, true);
  assert.equal(h.calls.submissions[0].user_text, '');
  assert.equal(h.state.activeSessionId, 'b');
  assert.equal(h.node('send-btn').disabled, false);
  assert.equal(h.node('stop-btn').disabled, true);
  h.pending.get(h.calls.submissions[0].attempt_id).resolve({ status: 'completed' });
  await starting;
  assert.equal(h.node('player-input').value, 'Unsent in b');
});

test('GM and Builder reload use their own reconnect transports and attachment identities', async () => {
  const storage = new Map([['sylvia-gm-disconnected-stream', JSON.stringify({ version: 2, streams: [
    { campaign_id: 'campaign', session_id: 'gm', session_role: 'dm', stream_owner_id: 'campaign',
      attempt_id: 'gm-attempt', acknowledged_sequence: 17, attachment_generation: 6 },
    { campaign_id: null, session_id: 'a', session_role: 'builder', stream_owner_id: 'a',
      attempt_id: 'builder-attempt', acknowledged_sequence: 0, attachment_generation: null },
  ] })]]);
  const h = harness(storage);
  configureGM(h);
  h.state.bridge.narration_attempt_status = async () => ({ status: 'active' });
  h.state.bridge.reconnect_stream = async (...args) => {
    h.calls.reconnect.push(args); return { attachment_generation: 7 };
  };
  await h.restoreDisconnectedStream();
  assert.ok(h.calls.reconnect.some((args) => args.join(',') === 'campaign,gm-attempt,17,6'));
  assert.ok(h.calls.reconnect.some((args) => args[0] === 'a' && args[1] === 'builder-attempt' && args[2] === null));
  assert.equal(h.state.sessionActivities.size, 2);
  assert.equal(h.state.activeSessionId, null);
});

test('the previous single-stream reload hint still restores its original chat', async () => {
  const h = harness(new Map([['sylvia-gm-disconnected-stream', JSON.stringify({
    campaign_id: null, session_id: 'a', session_role: 'builder', stream_owner_id: 'a',
    attempt_id: 'previous-attempt', acknowledged_sequence: 0, attachment_generation: 4,
  })]]));
  await h.restoreDisconnectedStream();
  assert.deepEqual(h.calls.reconnect[0], ['a', 'previous-attempt', 4]);
  assert.equal(h.state.activeSessionId, null);
  assert.equal(JSON.parse(h.storage.get('sylvia-gm-disconnected-stream')).version, 2);
});

test('reopening an unfinished Builder shows retained work and an interruption notice', async () => {
  const h = harness();
  h.state.bridge.session_context = async () => ({
    activity: { attempt_id: 'lost', status: 'interrupted' },
    messages: [
      { message_id: 'input', kind: 'user', content: { text: 'Create a scout', attempt_id: 'lost' } },
      { message_id: 'saved', kind: 'talk', content: { text: 'The first section is saved.' } },
    ],
  });
  await h.openBuilderChat('a');
  assert.equal(h.state.sessionActivities.get('a').phase, 'interrupted');
  assert.equal(h.node('send-btn').disabled, false);
  assert.equal(h.node('stop-btn').disabled, true);
  assert.match(h.node('chronology').dataset.chronologyHtml, /The first section is saved/);
  assert.match(h.node('chronology').dataset.chronologyHtml, /previous response did not finish/);
  assert.match(h.node('session-status-text').textContent, /interrupted/);
  assert.equal(h.calls.submissions.length, 0);
});

test('saved completion metadata stays out of conversation cards', async () => {
  const h = harness();
  h.state.bridge.session_context = async () => ({
    activity: { attempt_id: 'finished', status: 'completed' },
    messages: [
      { message_id: 'talk', kind: 'talk', content: { text: 'Character ready.' } },
      { message_id: 'terminal', kind: 'evidence', content: {
        kind: 'builder_attempt_terminal', status: 'completed', attempt_id: 'finished',
      } },
    ],
  });
  await h.openBuilderChat('a');
  assert.equal(h.state.sessionActivities.get('a').phase, 'completed');
  assert.match(h.node('chronology').dataset.chronologyHtml, /Character ready/);
  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /builder_attempt_terminal|Interrupted/);
});

test('a saved Builder step limit explains continuation without exposing an error payload', async () => {
  const h = harness();
  h.state.bridge.session_context = async () => ({
    activity: { attempt_id: 'limited', status: 'interrupted' },
    messages: [{ message_id: 'terminal', kind: 'evidence', content: {
      kind: 'builder_attempt_terminal', status: 'interrupted', attempt_id: 'limited',
      reason_code: 'tool_limit', error: 'private provider detail',
    } }],
  });
  await h.openBuilderChat('a');
  assert.match(h.node('chronology').dataset.chronologyHtml, /work limit after automatic continuation/);
  assert.match(h.node('chronology').dataset.chronologyHtml, /Saved work is retained/);
  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /private provider detail/);
  assert.equal(h.node('send-btn').disabled, false);
  assert.equal(h.calls.submissions.length, 0);
});

test('repeated Builder failures explain the interruption and keep input usable', async () => {
  const h = harness();
  h.state.bridge.session_context = async () => ({
    activity: { attempt_id: 'failed', status: 'interrupted' },
    messages: [{ message_id: 'terminal', kind: 'evidence', content: {
      kind: 'builder_attempt_terminal', status: 'interrupted', attempt_id: 'failed',
      reason_code: 'repeated_tool_failure', error: 'private provider detail',
    } }],
  });
  await h.openBuilderChat('a');
  assert.match(h.node('chronology').dataset.chronologyHtml, /repeated identical tool failures/);
  assert.match(h.node('chronology').dataset.chronologyHtml, /Review the last tool error/);
  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /private provider detail/);
  assert.equal(h.node('send-btn').disabled, false);
  assert.equal(h.calls.submissions.length, 0);
});

for(const reason_code of ['required_reference_unavailable','repeated_reference_failure']){
  test(`${reason_code} stops with source-specific recovery and never auto-submits`,async()=>{
    const h=harness();
    h.state.bridge.session_context=async()=>({
      activity:{attempt_id:'source-failed',status:'interrupted'},
      messages:[{message_id:'terminal',kind:'evidence',content:{
        kind:'builder_attempt_terminal',status:'interrupted',attempt_id:'source-failed',reason_code,
        error:'PRIVATE SOURCE DETAIL',
      }}],
    });
    await h.openBuilderChat('a');
    const html=h.node('chronology').dataset.chronologyHtml;
    assert.match(html,/selected source|same source/i);
    assert.match(html,/Saved work is retained/);
    assert.match(html,/before continuing/);
    assert.doesNotMatch(html,/PRIVATE SOURCE DETAIL|Send a message when you are ready/);
    assert.equal(h.node('send-btn').disabled,false);
    assert.equal(h.calls.submissions.length,0);
  });
}

test('a migrated independent Builder chat does not render Campaign return actions', async () => {
  const h = harness();
  h.state.bridge.session_context = async () => ({
    session: { session_id: 'a', role: 'builder', campaign_id: null },
    messages: [
      { message_id: 'old-link', kind: 'evidence', content: {
        kind: 'creation_subflow_link', campaign_id: 'old-campaign',
        parent_session_id: 'old-interview', purpose: 'player_character',
      } },
      { message_id: 'independent-launch', kind: 'evidence', content: {
        kind: 'builder_launch_context', purpose: 'player_character',
      } },
      { message_id: 'talk', kind: 'talk', content: { text: 'Character work retained.' } },
    ],
  });

  await h.openBuilderChat('a');

  assert.equal(h.state.activeCreationSubflow, null);
  assert.match(h.node('chronology').dataset.chronologyHtml, /Character work retained/);
  assert.doesNotMatch(h.node('chronology').dataset.chronologyHtml, /Use in Campaign|Return Without Using/);
});

test('reconnect reads the saved stopped outcome rather than calling it completed', async () => {
  const h = harness(new Map([['sylvia-gm-disconnected-stream', JSON.stringify({
    campaign_id: null, session_id: 'a', session_role: 'builder', stream_owner_id: 'a',
    attempt_id: 'stopped', acknowledged_sequence: 0, attachment_generation: 1,
  })]]));
  h.state.bridge.reconnect_provider_stream = async () => { throw new Error('No live lease'); };
  h.state.bridge.provider_stream_status = async () => ({ status: 'stopped' });
  await h.restoreDisconnectedStream();
  await h.flush();
  assert.equal(h.state.sessionActivities.get('a').phase, 'stopped');
  assert.equal(h.storage.has('sylvia-gm-disconnected-stream'), false);
  assert.equal(h.timers.size, 0);
});


test('renaming a Builder chat retains its target when navigation occurs before the save completes', async () => {
  const h = harness();
  const saved = deferred();
  const requests = [];
  h.window.prompt = () => 'My character';
  h.state.bridge.rename_builder_chat = (sessionId, name) => {
    requests.push([sessionId, name]);
    return saved.promise;
  };
  await h.openBuilderChat('a');
  const renaming = h.renameBuilderChat('a');
  await h.openBuilderChat('b');
  h.state.builderChats[0].display_name = 'My character';
  saved.resolve(h.state.builderChats[0]);
  await renaming;
  assert.deepEqual(requests, [['a', 'My character']]);
  assert.equal(h.node('session-title').textContent, 'Builder b');
  assert.match(h.node('builder-chat-rail').innerHTML, /My character/);
  assert.equal(h.state.activeSessionId, 'b');
});

test('deleting an independent chat clears only its draft and preserves later navigation', async () => {
  const h = harness();
  const deletion = deferred();
  const calls = [];
  h.window.prompt = () => 'DELETE';
  h.state.bridge.delete_builder_chat = (...args) => { calls.push(args); return deletion.promise; };
  await h.openBuilderChat('a');
  h.node('player-input').value = 'Discard this unfinished input';
  const removing = h.deleteBuilderChat('a');
  await h.submitInput('Cannot submit while deleting');
  assert.equal(h.calls.submissions.length, 0);
  await h.openBuilderChat('b');
  h.node('player-input').value = 'Keep this input';
  deletion.resolve({session: {session_id: 'a'}});
  await removing;
  assert.deepEqual(calls, [['a', 'permanently delete a']]);
  assert.equal(h.state.activeSessionId, 'b');
  assert.equal(h.node('session-title').textContent, 'Builder b');
  assert.equal(h.node('player-input').value, 'Keep this input');
  assert.equal(h.state.sessionDrafts.has('a'), false);
  assert.equal(h.state.sessionActivities.has('a'), false);
  assert.equal(h.state.builderChats.length, 1);
});

test('deletion requires confirmation and a cleanup failure leaves the chat available for retry', async () => {
  const h = harness();
  h.window.alert = (message) => { throw new Error(message); };
  let calls = 0;
  h.window.prompt = () => null;
  h.state.bridge.delete_builder_chat = async () => { calls++; throw new Error('Files could not be removed'); };
  await h.openBuilderChat('a');
  await h.deleteBuilderChat('a');
  assert.equal(calls, 0);
  h.window.prompt = () => 'DELETE';
  await h.deleteBuilderChat('a');
  assert.equal(calls, 1);
  assert.equal(h.state.activeSessionId, 'a');
  assert.equal(h.state.builderChats.length, 2);
  assert.equal(h.state.deletingBuilderChats.has('a'), false);
  assert.match(h.node('session-status-text').textContent, /Chat deletion failed/);
  h.state.bridge.delete_builder_chat = async () => ({session: {session_id: 'a'}});
  await h.deleteBuilderChat('a');
  assert.equal(h.state.activeSessionId, null);
  assert.equal(h.state.view, 'home');
  assert.equal(h.state.builderChats.length, 1);
});

test('a working Builder cannot be deleted through its chat control', async () => {
  const h = harness();
  let prompts = 0;
  h.window.prompt = () => { prompts++; return 'DELETE'; };
  h.state.bridge.delete_builder_chat = async () => { throw new Error('Must not delete'); };
  await h.openBuilderChat('a');
  const work = h.submitInput('Build a character');
  await h.flush();
  await h.deleteBuilderChat('a');
  assert.equal(prompts, 0);
  const attempt = h.calls.submissions[0].attempt_id;
  h.pending.get(attempt).resolve({status: 'completed'});
  await work;
});

test('library deletion requires confirmation and a late result does not repaint navigation', async () => {
  const h = harness();
  const item = {
    kind: 'companion', id: 'participant-111111111111111111111111-2222222222222222',
    name: 'Synthetic Companion', content_sha256: 'a'.repeat(64), reusable: true,
  };
  let calls = 0;
  h.state.bridge.delete_reusable_library_entry = async () => { calls++; return {deleted: true}; };
  h.state.bridge.campaign_creation_catalog = async () => [];
  h.window.confirm = () => false;
  await h.deleteReusableLibraryEntry(item);
  assert.equal(calls, 0);

  const deletion = deferred();
  h.window.confirm = () => true;
  h.state.bridge.delete_reusable_library_entry = (command) => {
    calls++;
    assert.equal(command.confirmation, `permanently delete ${item.id}`);
    return deletion.promise;
  };
  h.node('construction-panel').hidden = false;
  h.node('session-status-text').textContent = 'Campaign setup';
  const request = h.deleteReusableLibraryEntry(item);
  h.node('construction-panel').hidden = true;
  h.node('session-status-text').textContent = 'Builder ready';
  deletion.resolve({deleted: true});
  await request;
  assert.equal(calls, 1);
  assert.equal(h.node('session-status-text').textContent, 'Builder ready');
});

test('navigating away keeps renewing the original Builder attachment generation', async () => {
  const h = harness();
  await h.openBuilderChat('a');
  const pending = h.submitInput('Continue the Companion');
  await h.flush();
  const activity = h.state.sessionActivities.get('a');
  assert.equal(activity.attachmentGeneration, 1);
  const heartbeatTimer = activity.heartbeatTimer;

  await h.openBuilderChat('b');
  assert.equal(h.timers.has(heartbeatTimer), true);
  h.timers.get(heartbeatTimer).fn();
  await h.flush();
  h.timers.get(heartbeatTimer).fn();
  await h.flush();

  assert.deepEqual(h.calls.heartbeat.map((call) => [call[0], call[2]]), [
    ['a', null], ['a', 1], ['a', 1],
  ]);
  assert.equal(h.state.sessionActivities.get('a').phase, 'working');
  h.pending.get(h.calls.submissions[0].attempt_id).resolve({ status: 'completed' });
  await pending;
});

test('a claimed Builder heartbeat reconnects during owner disconnect grace', async () => {
  const h = harness();
  let heartbeatCount = 0;
  h.state.bridge.provider_stream_heartbeat = async (...args) => {
    h.calls.heartbeat.push(args);
    heartbeatCount += 1;
    if (heartbeatCount === 2) throw new Error('Provider stream is disconnected');
    return { attachment_generation: heartbeatCount === 1 ? 1 : 2 };
  };
  const pending = h.submitInput('Continue the Companion', {
    builderStart: true, builderSessionId: 'a',
  });
  await h.flush();
  const activity = h.state.sessionActivities.get('a');
  const timer = activity.heartbeatTimer;
  assert.equal(activity.attachmentGeneration, 1);

  h.timers.get(timer).fn();
  await h.flush();

  assert.deepEqual(h.calls.reconnect, [['a', h.calls.submissions[0].attempt_id, 1]]);
  assert.equal(activity.attachmentGeneration, 2);
  h.timers.get(timer).fn();
  await h.flush();
  assert.deepEqual(h.calls.heartbeat.at(-1), ['a', activity.attemptId, 2]);
  assert.equal(activity.phase, 'working');
  h.pending.get(h.calls.submissions[0].attempt_id).resolve({ status: 'completed' });
  await pending;
});
