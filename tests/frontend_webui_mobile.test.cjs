const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { install } = require('../src-tauri/frontend/webui-mobile.js');

function harness() {
  const eventTarget = () => ({
    events: {},
    addEventListener(name, handler) { this.events[name] = handler; },
  });
  const element = () => ({ ...eventTarget(), children: [], attributes: {}, dataset: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    appendChild(value) { this.children.push(value); },
    focus() { doc.activeElement = this; },
  });
  const frame = element(), bar = element(), home = element(), properties = {}, changes = [];
  home.click = () => { changes.push('home'); home.events.click?.(); };
  bar.getBoundingClientRect = () => ({ height: 120 });
  const doc = { ...eventTarget(), hidden: false, body: {},
    documentElement: { style: { setProperty(key, value) { properties[key] = value; } } },
    getElementById: id => id === 'session-frame' ? frame : home,
    querySelector: () => bar,
    createElement: element,
  };
  const win = { ...eventTarget(), innerHeight: 800,
    visualViewport: { ...eventTarget(), height: 800, offsetTop: 0 },
    SylviaGMFrontend: { async setBrowserVisibility(value) { changes.push(value); } },
  };
  return { doc, win, frame, bar, home, properties, changes };
}

test('narrow navigation changes only the visible workspace and Home restores chat', () => {
  const h = harness(); install(h.win, h.doc);
  const nav = h.bar.children[0];
  assert.equal(nav.attributes['aria-label'], 'Workspace');
  assert.equal(h.frame.dataset.browserPane, 'chat');
  nav.children[1].onclick();
  assert.equal(h.frame.dataset.browserPane, 'panels');
  assert.equal(nav.children[1].attributes['aria-pressed'], 'true');
  nav.children[2].onclick();
  assert.equal(h.frame.dataset.browserPane, 'sessions');
  assert.deepEqual(h.changes, ['home']);
  h.home.events.click();
  assert.equal(h.frame.dataset.browserPane, 'chat');
  assert.deepEqual(h.changes, ['home']);
});

test('choosing an existing campaign or Builder from Sessions returns to chat', () => {
  for (const target of ['campaign', 'builder']) {
    const h = harness(); install(h.win, h.doc);
    h.bar.children[0].children[2].onclick();
    const nestedLabel = { closest(selector) {
      assert.equal(selector, '[data-home-campaign-id], [data-builder-session-id]');
      return { target };
    } };
    h.doc.events.click({ target: nestedLabel });
    assert.equal(h.frame.dataset.browserPane, 'chat');
    assert.deepEqual(h.changes, ['home']);
  }
});

test('keyboard opening from Sessions moves focus from the hidden row to the Chat pane button', () => {
  const row = (rects) => ({ closest: () => ({}), getClientRects: () => rects });
  for (const [label, detail, focused, moves] of [
    ['keyboard, row now hidden', 0, row([]), true],
    ['keyboard, focus already lost', 0, null, true],
    ['pointer click', 1, row([]), false],
    ['programmatic open from a visible control', 0, row([{}]), false],
  ]) {
    const h = harness(); install(h.win, h.doc);
    const chat = h.bar.children[0].children[0];
    h.bar.children[0].children[2].onclick();
    h.doc.activeElement = focused || h.doc.body;
    h.doc.events.click({ target: row([]), detail });
    assert.equal(h.frame.dataset.browserPane, 'chat', label);
    assert.equal(h.doc.activeElement === chat, moves, label);
    assert.equal(chat.attributes['aria-pressed'], 'true', label);
  }
});

test('visual viewport changes reserve keyboard space without replacing the draft or panels', () => {
  const h = harness(); install(h.win, h.doc);
  h.win.visualViewport.height = 320; h.win.visualViewport.offsetTop = 40;
  h.win.visualViewport.events.resize();
  assert.equal(h.properties['--webui-viewport-height'], '320px');
  assert.equal(h.properties['--webui-viewport-top'], '40px');
  assert.equal(h.properties['--webui-bar-height'], '120px');
  assert.equal(h.frame.dataset.browserPane, 'chat');
  assert.deepEqual(h.changes, []);
});

test('background and persisted resume use lifecycle callbacks, never a submit command', async () => {
  const h = harness(); install(h.win, h.doc);
  h.doc.hidden = true; h.doc.events.visibilitychange();
  h.doc.hidden = false; h.win.events.pageshow({ persisted: true });
  await Promise.resolve();
  assert.deepEqual(h.changes, [false, true]);
  h.win.SylviaWebUI = { isStopped: () => true };
  h.doc.events.visibilitychange();
  assert.deepEqual(h.changes, [false, true]);
});

function visibilityContext(reconnect) {
  const source = fs.readFileSync(require.resolve('../src-tauri/frontend/app.js'), 'utf8');
  const start = source.indexOf('  let browserVisibilityGeneration = 0;');
  const end = source.indexOf('\n  async function restoreDisconnectedStream()', start);
  const activity = { attemptId: 'original-attempt', acknowledgedSequence: 4, attachmentGeneration: 2 };
  const calls = [];
  const context = { window: { __sylviaBrowserInvoke: () => {} }, document: { hidden: false },
    state: { sessionActivities: new Map([['session', activity]]), desktopDrafts: { async flush() { calls.push('flush'); } } },
    activityIsWorking: () => true, currentActivity: () => true,
    saveSelectedSessionDraft: () => calls.push('draft'),
    signalActiveStreamDisconnect: () => calls.push('disconnect'), reconnect,
    startStreamHeartbeat: () => calls.push('heartbeat'), startProjectionPolling: () => calls.push('poll'),
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end) + '\n globalThis.change = setBrowserVisibility;', context);
  return { context, calls };
}

test('a late reconnect cannot restart heartbeats after another background transition', async () => {
  let release;
  const reconnect = new Promise(resolve => { release = resolve; });
  const h = visibilityContext(() => reconnect);
  const resuming = h.context.change(true);
  h.context.document.hidden = true;
  await h.context.change(false);
  release(); await resuming;
  assert.deepEqual(h.calls, ['draft', 'disconnect', 'flush', 'disconnect']);
});

test('foreground resume reattaches the original attempt and restores canonical polling', async () => {
  const requests = [];
  const h = visibilityContext(async (...args) => { requests.push(args); });
  await h.context.change(true);
  assert.deepEqual(requests[0].slice(0, 3), ['original-attempt', 4, 2]);
  assert.deepEqual(h.calls, ['heartbeat', 'poll']);
});
