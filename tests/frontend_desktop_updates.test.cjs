const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const settle = () => new Promise(resolve => setImmediate(resolve));

function fixture(initial = {}) {
  const elements = new Map(), calls = [], timers = new Map();
  let nextTimer = 0;
  function element(name) {
    if (!elements.has(name)) elements.set(name, {
      textContent: '', hidden: true, disabled: false, handlers: {}, attrs: {},
      addEventListener(event, callback) { this.handlers[event] = callback; },
      setAttribute(key, value) { this.attrs[key] = value; },
      removeAttribute(key) { delete this[key]; },
      focus() { this.focused = true; },
      set innerHTML(_value) { throw new Error('Release text must not enter HTML'); },
    });
    return elements.get(name);
  }
  const root = { querySelector(selector) { return element(selector.match(/"(.+)"/)[1]); }, setAttribute() {} };
  const window = { setTimeout(callback) { timers.set(++nextTimer, callback); return nextTimer; }, clearTimeout(id) { timers.delete(id); } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/desktop-updates.js'), 'utf8'), { window });
  const f = { value: { phase: 'idle', current_version: '0.3.0-alpha.1', channel: 'alpha',
    distribution: 'portable', target: 'windows-x86_64', busy: false, can_apply: false,
    operation_id: 'a'.repeat(32), received: 0, total: null, ...initial },
    calls, timers, element, invoke: null,
    click(name) { return element(name).handlers.click(); },
    tick() { const work = [...timers.values()]; timers.clear(); for (const callback of work) callback(); },
  };
  const bridge = {};
  for (const action of ['state', 'check', 'download', 'cancel', 'defer']) {
    bridge[`desktop_update_${action}`] = (...args) => {
      calls.push([action, ...args]);
      return f.invoke ? f.invoke(action) : Promise.resolve({ ...f.value });
    };
  }
  f.installs = [];
  f.install = null;
  f.ui = window.SylviaDesktopUpdates.mount(root, bridge, async identity => {
    f.installs.push(identity);
    if (f.install) await f.install();
  });
  return f;
}

test('opening/reopening About reads state only; check and download require explicit choices', async () => {
  const f = fixture();
  assert.deepEqual(f.calls, []);
  f.ui.setVisible(true); await settle();
  assert.deepEqual(f.calls, [['state']]);
  assert.equal(f.timers.size, 0);
  f.value.phase = 'available'; f.value.version = '0.3.0-alpha.2';
  f.click('check'); f.click('check'); await settle();
  assert.deepEqual(f.calls, [['state'], ['check']]);
  f.click('download'); await settle();
  assert.deepEqual(f.calls.at(-1), ['download', 'a'.repeat(32)]);
  f.ui.setVisible(false); f.ui.setVisible(true); await settle();
  assert.deepEqual(f.calls.at(-1), ['state']);
  assert.deepEqual(f.installs, []);
});

test('release notes stay inert and long untrusted content stays bounded', async () => {
  const notes = '<script>PRIVATE EXECUTION</script>\n'.repeat(3000);
  const f = fixture({ phase: 'available', notes, version: '0.3.0-alpha.2' });
  f.ui.setVisible(true); await settle();
  assert.equal(f.element('notes').textContent, notes.slice(0, 65536));
  assert.equal(f.element('version').textContent, 'Available 0.3.0-alpha.2');
  assert.match(f.element('identity').textContent, /portable/);
});

test('known and unknown lengths use truthful progress without creating an apply action', async () => {
  const f = fixture({ phase: 'downloading', busy: true, received: 42, total: 100 });
  f.ui.setVisible(true); await settle();
  assert.equal(f.element('progress').value, 42);
  assert.equal(f.element('progress').max, 100);
  assert.equal(f.timers.size, 1);
  f.value.total = null;
  f.tick(); await settle();
  assert.equal(f.element('progress').value, undefined);
  assert.match(f.element('bytes').textContent, /42 bytes downloaded/);
  assert.equal(f.element('install').hidden, true);
  f.click('cancel'); await settle();
  assert.deepEqual(f.calls.at(-1), ['cancel', 'a'.repeat(32)]);
  f.ui.setVisible(false);
  assert.equal(f.timers.size, 0);
});

test('stalled status has one call, no poll spin, and hidden old results cannot repaint', async () => {
  const f = fixture({ phase: 'checking', busy: true });
  let release;
  f.invoke = () => new Promise(resolve => { release = resolve; });
  f.ui.setVisible(true);
  f.tick(); f.tick();
  assert.equal(f.calls.length, 1);
  assert.equal(f.timers.size, 0);
  f.ui.setVisible(false);
  release({ ...f.value, notes: 'stale notes' }); await settle();
  assert.equal(f.element('notes').textContent, '');
  assert.equal(f.timers.size, 0);
  f.invoke = null;
  f.ui.setVisible(true); await settle();
  assert.equal(f.calls.length, 2);
  assert.equal(f.timers.size, 1);
});

test('failed operation stops polling and never displays raw exception content', async () => {
  const f = fixture();
  f.invoke = () => Promise.reject(new Error('PRIVATE credentials and paths'));
  f.ui.setVisible(true); await settle();
  assert.equal(f.timers.size, 0);
  assert.doesNotMatch(f.element('status').textContent, /PRIVATE/);
  assert.equal(f.element('check').disabled, false);
});

test('restart requires explicit second consent and duplicate clicks cannot submit twice', async () => {
  const f = fixture({ phase: 'verified', can_apply: true, downloaded: true });
  let release;
  f.install = () => new Promise(resolve => { release = resolve; });
  f.ui.setVisible(true); await settle();
  f.click('install');
  assert.equal(f.element('confirm').hidden, false);
  assert.deepEqual(f.installs, []);
  f.click('not-now');
  assert.equal(f.element('confirm').hidden, true);
  f.click('install');
  f.click('confirm-install'); f.click('confirm-install');
  assert.deepEqual(f.installs, ['a'.repeat(32)]);
  release(); await settle();
  assert.equal(f.element('confirm').hidden, true);
  assert.equal(f.calls.filter(call => call[0] === 'check').length, 0);
});

test('bootstrap warning does not pretend an unsigned build can update', async () => {
  const f = fixture({ phase: 'unavailable', message: 'Manual bootstrap required.' });
  f.ui.setVisible(true); await settle();
  assert.equal(f.element('check').disabled, true);
  assert.equal(f.element('install').hidden, true);
  assert.equal(f.element('status').textContent, 'Manual bootstrap required.');
});

test('Not now cannot imply cancellation once the reviewed restart is preparing', async () => {
  const f = fixture({ phase: 'verified', can_apply: true, downloaded: true });
  let release;
  f.install = () => new Promise(resolve => { release = resolve; });
  f.ui.setVisible(true); await settle();
  f.click('install');
  const preparing = f.click('confirm-install');
  assert.equal(f.element('not-now').disabled, true);
  assert.match(f.element('status').textContent, /Saving drafts and preparing a safe restart/);
  f.click('not-now');
  assert.equal(f.element('confirm').hidden, false);
  assert.deepEqual(f.installs, ['a'.repeat(32)]);
  release(); await preparing;
  assert.equal(f.element('confirm').hidden, true);
});

test('restoring stored download status only polls status and never starts a network check', async () => {
  const f = fixture({ phase: 'recovering', busy: true });
  f.ui.setVisible(true); await settle();
  assert.equal(f.element('check').disabled, true);
  assert.match(f.element('status').textContent, /Reading previous update status/);
  assert.equal(f.timers.size, 1);
  f.value.phase = 'idle'; f.value.busy = false;
  f.tick(); await settle();
  assert.deepEqual(f.calls, [['state'], ['state']]);
  assert.equal(f.element('check').disabled, false);
  assert.equal(f.timers.size, 0);
});

test('previous installer retention labels its own progress and does not claim apply readiness', async () => {
  const f = fixture({ phase: 'downloading', busy: true, transfer_kind: 'previous_installer',
    received: 10, total: 30 });
  f.ui.setVisible(true); await settle();
  assert.match(f.element('status').textContent, /current installer for recovery/);
  assert.equal(f.element('progress').value, 10);
  assert.equal(f.element('progress').max, 30);
  assert.equal(f.element('install').hidden, true);
  f.ui.setVisible(false);
});

test('owned-process closing status blocks another check or install without a fake percentage', async () => {
  const f = fixture({ phase: 'closing', busy: true });
  f.ui.setVisible(true); await settle();
  assert.match(f.element('status').textContent, /Waiting for Sylvia windows/);
  assert.equal(f.element('check').disabled, true);
  assert.equal(f.element('install').hidden, true);
  assert.equal(f.element('progress').hidden, true);
  f.ui.setVisible(false);
});
