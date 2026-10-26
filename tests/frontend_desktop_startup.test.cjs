const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Run the real composer initialization with only its external UI/owner dependencies replaced.
const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
const start = source.indexOf('  async function init() {');
const end = source.indexOf('\n  // Restore presentation', start);
assert.ok(start > 0 && end > start);
const initialize = source.slice(start, end);
const restoreStart = source.indexOf('  async function restoreUpdateView(');
const restoreEnd = source.indexOf('\n  function wireControls()', restoreStart);
const restoration = source.slice(restoreStart, restoreEnd);

function fixture(startup, confirmation = { status: 'confirmed' }) {
  const calls = [], nodes = new Map();
  let installer;
  const node = id => {
    if (!nodes.has(id)) {
      const classes = new Set();
      const text = { textContent: '' };
      nodes.set(id, {
        style: {}, inert: false, text, classList: {
          add: name => classes.add(name), remove: name => classes.delete(name),
          contains: name => classes.has(name),
        }, querySelector: () => text,
      });
    }
    return nodes.get(id);
  };
  const bridge = {
    desktop_update_startup_state: async () => { calls.push('startup-state'); return startup?.pending === true
      ? { operation_id: 'a'.repeat(32), ...startup } : startup; },
    desktop_update_confirm_startup: async () => {
      calls.push('confirm');
      if (confirmation instanceof Error || typeof confirmation === 'string') throw confirmation;
      return confirmation;
    },
    desktop_update_install: async () => { calls.push('native-install'); return { status: 'closing' }; },
  };
  const drafts = {
    load: async () => { calls.push('drafts-loaded'); return true; },
    flush: async () => { calls.push('drafts-flushed'); },
    rememberUpdate: async (operation, value) => {
      drafts.view = { operation_id: operation, ...value }; await drafts.flush();
    },
    updateView: operation => drafts.view?.operation_id === operation ? drafts.view : null,
  };
  const context = {
    state: { sessionDrafts: new Map(), builderQuestionDrafts: new Map(), campaigns: [], builderChats: [] }, $: node,
    window: {
      __sylviaGMBridge: bridge,
      SylviaDesktopDrafts: { mount: () => drafts },
      SylviaDesktopUpdates: { mount: (_root, _bridge, install) => { installer = install; return {}; } },
    },
    gamePanels: () => ({ init: async () => { calls.push('panels-ready'); } }),
    createHostBridge: () => null, localSupportReport: null,
    setInterval: () => { calls.push('timer'); },
    setTimeout: () => { throw new Error('unexpected automatic retry'); },
    wireControls: () => { calls.push('controls'); },
    saveSelectedSessionDraft: () => { calls.push('capture-selected-draft'); },
    setInlineStatus: value => { calls.push(value); },
    openCampaign: async (...args) => { calls.push(['open-campaign', ...args]); },
    openBuilderChat: async id => { calls.push(['open-builder', id]); },
    initTheme: () => {}, initSidebarResize: () => {},
    refreshApplicationInfo: async () => { calls.push('version-ready'); },
    refreshProviderSettings: async () => { calls.push('settings-ready'); },
    refreshModelChoices: async () => { calls.push('models-ready'); },
    refreshNavigationIndexes: async () => { calls.push('navigation-ready'); },
    restoreDisconnectedStream: async () => { calls.push('reconnect'); },
    showHome: () => { calls.push('home'); }, anySessionIsWorking: () => false,
  };
  vm.createContext(context);
  context.restoreUpdateView = vm.runInContext(`(${restoration})`, context);
  const run = vm.runInContext(`(${initialize})`, context);
  return { run, calls, node, drafts, bridge, context, install: () => installer('a'.repeat(32)) };
}

test('update startup waits for drafts, panels and navigation; never reconnects saved model work', async () => {
  const f = fixture({ pending: true });
  await f.run();
  for (const before of ['drafts-loaded', 'panels-ready', 'version-ready', 'navigation-ready', 'home'])
    assert.ok(f.calls.indexOf(before) < f.calls.indexOf('confirm'));
  assert.equal(f.calls.filter(call => call === 'confirm').length, 1);
  assert.equal(f.calls.includes('reconnect'), false);
  assert.equal(f.node('connecting-overlay').classList.contains('hidden'), true);
  assert.equal(f.node('session-frame').inert, false);
});

test('ordinary startup preserves its existing stream attachment behavior without update confirmation', async () => {
  const f = fixture({ pending: false });
  await f.run();
  assert.equal(f.calls.filter(call => call === 'reconnect').length, 1);
  assert.equal(f.calls.includes('confirm'), false);
});

test('failed update confirmation keeps the overlay and never retries or reconnects automatically', async () => {
  const f = fixture({ pending: true }, new Error('controlled owner rejection'));
  await f.run();
  assert.equal(f.node('connecting-overlay').classList.contains('hidden'), false);
  assert.match(f.node('connecting-overlay').text.textContent, /could not be confirmed/);
  assert.equal(f.calls.filter(call => call === 'confirm').length, 1);
  assert.equal(f.calls.includes('reconnect'), false);
  assert.equal(f.calls.includes('timer'), false);
  assert.equal(f.node('session-frame').inert, true);
});

test('startup rejection shows only its bounded error code, without the raw native message', async () => {
  for (const rejection of ['journal_failed: private native details must stay hidden',
    new Error('checkpoint_unavailable: private native details must stay hidden')]) {
    const f = fixture({ pending: true }, rejection);
    await f.run();
    const text = f.node('connecting-overlay').text.textContent;
    const expected = typeof rejection === 'string' ? 'journal_failed' : 'checkpoint_unavailable';
    assert.match(text, new RegExp(`Error code: ${expected}\\.`));
    assert.doesNotMatch(text, /private native details/);
    assert.equal(f.node('session-frame').inert, true);
    assert.equal(f.calls.filter(call => call === 'confirm').length, 1);
    assert.equal(f.calls.includes('reconnect'), false);
  }
});

test('unstructured startup rejection remains generic and does not expose arbitrary content', async () => {
  const f = fixture({ pending: true }, new Error('<private-fixture-text>'));
  await f.run();
  const text = f.node('connecting-overlay').text.textContent;
  assert.doesNotMatch(text, /private-fixture-text|Error code:/);
  assert.equal(f.node('session-frame').inert, true);
});

test('invalid native startup state leaves controls unwired and cannot mark an update successful', async () => {
  const f = fixture({ pending: 'untrusted' });
  await f.run();
  assert.deepEqual(f.calls, ['startup-state']);
  assert.equal(f.node('connecting-overlay').classList.contains('hidden'), false);
});

test('actual install hook flushes drafts and keeps editing frozen until native close completes', async () => {
  const f = fixture({ pending: false });
  await f.run();
  let release;
  f.drafts.flush = () => new Promise(resolve => { release = resolve; });
  const installed = f.install();
  assert.equal(f.node('session-frame').inert, true);
  assert.equal(f.calls.includes('native-install'), false);
  release();
  await installed;
  assert.equal(f.calls.filter(call => call === 'native-install').length, 1);
  assert.equal(f.node('session-frame').inert, true);
});

test('failed or missing draft saver cannot reach native install and restores editing', async () => {
  for (const missing of [false, true]) {
    const f = fixture({ pending: false });
    await f.run();
    if (missing) f.context.state.desktopDrafts = null;
    else f.drafts.flush = async () => { throw new Error('controlled failed save'); };
    await assert.rejects(f.install());
    assert.equal(f.calls.includes('native-install'), false);
    assert.equal(f.node('session-frame').inert, false);
  }
});

test('native rejection restores editing without another installation attempt', async () => {
  const f = fixture({ pending: false });
  await f.run();
  f.bridge.desktop_update_install = async () => {
    f.calls.push('rejected-install'); throw new Error('controlled rejection');
  };
  await assert.rejects(f.install());
  assert.equal(f.node('session-frame').inert, false);
  assert.equal(f.calls.filter(call => call === 'rejected-install').length, 1);
});

test('successful helper-wait replies only poll completion and never enable editing early', async () => {
  const f = fixture({ pending: true });
  let attempts = 0;
  f.bridge.desktop_update_confirm_startup = async () => {
    f.calls.push('confirm');
    return { status: ++attempts === 1 ? 'waiting_for_helper' : 'confirmed' };
  };
  f.context.setTimeout = resolve => {
    assert.equal(f.node('session-frame').inert, true);
    f.calls.push('wait-helper'); resolve();
  };
  await f.run();
  assert.equal(attempts, 2);
  assert.equal(f.calls.includes('reconnect'), false);
  assert.equal(f.node('session-frame').inert, false);
});

test('post-shutdown handoff failure keeps editing frozen and gives recovery instructions', async () => {
  const f = fixture({ pending: false });
  await f.run();
  f.bridge.desktop_update_install = async () => ({ status: 'owner_unavailable' });
  await f.install();
  assert.equal(f.node('session-frame').inert, true);
  assert.equal(f.node('connecting-overlay').classList.contains('hidden'), false);
  assert.match(f.node('connecting-overlay').text.textContent, /cannot continue.*recovery/);
});

test('failed saved-state load cannot acknowledge startup or reconnect model work', async () => {
  for (const component of ['drafts', 'panels', 'navigation']) {
    const f = fixture({ pending: true });
    const fail = async () => { throw new Error('controlled load failure'); };
    if (component === 'drafts') f.drafts.load = fail;
    if (component === 'panels') f.context.gamePanels = () => ({ init: fail });
    if (component === 'navigation') f.context.refreshNavigationIndexes = fail;
    await f.run();
    assert.equal(f.calls.includes('confirm'), false);
    assert.equal(f.calls.includes('reconnect'), false);
    assert.equal(f.node('session-frame').inert, true);
    assert.match(f.node('connecting-overlay').text.textContent, /could not finish opening/);
  }
});

test('actual draft loader failure cannot be acknowledged as a successful update startup', async () => {
  const f = fixture({ pending: true });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/desktop-drafts.js'), 'utf8'), f.context);
  f.bridge.load_desktop_drafts = async () => { throw new Error('controlled read failure'); };
  f.bridge.save_desktop_drafts = async () => { assert.fail('failed read must not save'); };
  await f.run();
  assert.equal(f.calls.includes('confirm'), false);
  assert.equal(f.node('session-frame').inert, true);
  assert.match(f.node('connecting-overlay').text.textContent, /could not finish opening/);
});

test('update restores only a matching saved campaign/session with read-only opening', async () => {
  const f = fixture({ pending: true });
  f.drafts.view = { operation_id: 'a'.repeat(32), kind: 'session', campaign_id: 'campaign-a', session_id: 'dm-a' };
  f.context.state.campaigns = [{ campaign_id: 'campaign-a', lifecycle: 'active', sessions: [{ session_id: 'dm-a' }] }];
  await f.run();
  const opened = f.calls.find(row => Array.isArray(row) && row[0] === 'open-campaign');
  assert.equal(opened[1], 'campaign-a'); assert.equal(opened[2], 'dm-a');
  assert.equal(opened[3].restoreOnly, true);
  assert.equal(f.calls.includes('reconnect'), false);
  assert.ok(f.calls.indexOf('confirm') < f.calls.indexOf(opened));
});

test('missing sessions or another operation cannot be opened by saved presentation', async () => {
  for (const operation of ['a', 'b']) {
    const f = fixture({ pending: true });
    f.drafts.view = { operation_id: operation.repeat(32), kind: 'session', campaign_id: null, session_id: 'gone' };
    await f.run();
    assert.equal(f.calls.some(row => Array.isArray(row)), false);
    assert.equal(f.node('session-frame').inert, false);
  }
});

test('Builder view restore displays history but never starts or reconnects model work', async () => {
  const f = fixture({ pending: true });
  f.drafts.view = { operation_id: 'a'.repeat(32), kind: 'session', campaign_id: null, session_id: 'builder-a' };
  f.context.state.builderChats = [{ session: { session_id: 'builder-a', status: 'active' } }];
  await f.run();
  assert.deepEqual(f.calls.find(row => Array.isArray(row)), ['open-builder', 'builder-a']);
  assert.equal(f.calls.includes('reconnect'), false);
});
