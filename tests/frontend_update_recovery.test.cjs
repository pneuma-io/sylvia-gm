const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/update-recovery.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture(value = {}) {
  const nodes = new Map(), calls = [];
  const node = name => {
    if (!nodes.has(name)) nodes.set(name, {
      hidden: false, disabled: false, checked: false, textContent: '', listeners: {},
      setAttribute(name, value) { this[name] = value; }, focus() { this.focused = true; },
      addEventListener(name, fn) { this.listeners[name] = fn; },
      async emit(event) { await this.listeners[event]?.(); },
    });
    return nodes.get(name);
  };
  const root = { querySelector: selector => node(selector.replace('#recovery-', '')),
    setAttribute(name, value) { this[name] = value; } };
  const snapshot = { operation_id: 'a'.repeat(32), can_keep_data: true, can_recover: true,
    previous_version: '0.3.0-alpha.1', restart_nonce: null, ...value };
  const bridge = {
    state: async () => { calls.push(['state']); return snapshot; },
    recover: async (operation, choice, nonce) => { calls.push(['recover', operation, choice, nonce]); return { status: 'closing' }; },
  };
  const context = { window: {}, document: { addEventListener: () => {} } };
  vm.runInNewContext(source, context);
  const mounted = context.window.SylviaUpdateRecovery.mount(root, bridge);
  return { node, root, snapshot, calls, bridge, mounted };
}

test('opening recovery reads status only and never selects or executes an option', async () => {
  const f = fixture(); await tick();
  assert.deepEqual(f.calls, [['state']]);
  assert.equal(f.node('keep').checked, false);
  assert.equal(f.node('restore').checked, false);
  assert.equal(f.node('review').disabled, true);
  assert.equal(f.node('choices').hidden, false);
});

test('restore warns about lost progress and requires review and separate explicit commitment', async () => {
  const f = fixture({ can_keep_data: false,
    restore_warning: 'Restoring the pre-update checkpoint loses newer saved progress.' }); await tick();
  assert.match(f.node('warning').textContent, /loses newer saved progress/);
  assert.equal(f.node('keep').disabled, true);
  f.node('keep').checked = true;
  await f.node('review').emit('click');
  assert.equal(f.node('confirm').hidden, true);
  f.node('keep').checked = false; f.node('restore').checked = true;
  await f.node('restore').emit('change');
  await f.node('review').emit('click');
  assert.match(f.node('confirm-text').textContent, /loses progress.*retained locally/);
  assert.equal(f.calls.length, 1);
  await f.node('cancel').emit('click');
  await f.node('commit').emit('click');
  assert.equal(f.calls.length, 1);
  await f.node('review').emit('click');
  await f.node('commit').emit('click');
  assert.deepEqual(f.calls[1], ['recover', 'a'.repeat(32), 'restore', null]);
  assert.equal(f.node('refresh').disabled, true);
});

test('choice changes and refreshed operation identity invalidate earlier consent', async () => {
  const f = fixture(); await tick();
  f.node('keep').checked = true;
  await f.node('review').emit('click');
  f.node('keep').checked = false; f.node('restore').checked = true;
  await f.node('restore').emit('change');
  await f.node('commit').emit('click');
  assert.equal(f.calls.length, 1);
  await f.node('review').emit('click');
  f.snapshot.operation_id = 'b'.repeat(32);
  await f.mounted.refresh();
  await f.node('commit').emit('click');
  assert.equal(f.calls.filter(row => row[0] === 'recover').length, 0);
});

test('a pending recovery has one request and cannot launch duplicates', async () => {
  const f = fixture(); await tick();
  f.node('keep').checked = true; await f.node('review').emit('click');
  let release;
  f.bridge.recover = async (...args) => { f.calls.push(['recover', ...args]); return new Promise(resolve => { release = resolve; }); };
  const first = f.node('commit').emit('click');
  await f.node('commit').emit('click');
  await f.mounted.refresh();
  assert.equal(f.calls.filter(row => row[0] === 'recover').length, 1);
  release({ status: 'closing' }); await first;
  assert.equal(f.node('refresh').disabled, true);
});

test('rejected recovery needs a fresh status check without automatic retries or private errors', async () => {
  const f = fixture(); await tick();
  f.node('restore').checked = true; await f.node('review').emit('click');
  f.bridge.recover = async () => { throw new Error('private path must not be rendered'); };
  await f.node('commit').emit('click');
  assert.match(f.node('status').textContent, /not admitted/);
  assert.doesNotMatch(f.node('status').textContent, /private path/);
  assert.equal(f.calls.length, 1);
  assert.equal(f.node('choices').hidden, true);
  assert.equal(f.node('refresh').disabled, false);
});

test('unavailable recovery owner keeps all mutation controls disabled without claiming clean exit', async () => {
  const f = fixture(); await tick();
  f.node('keep').checked = true; await f.node('review').emit('click');
  f.bridge.recover = async () => ({ status: 'owner_unavailable' });
  await f.node('commit').emit('click');
  assert.match(f.node('status').textContent, /handoff did not finish/);
  assert.doesNotMatch(f.node('status').textContent, /server closed/);
  assert.equal(f.node('refresh').disabled, true);
  assert.equal(f.node('commit').disabled, true);
});

test('unqualified build cannot recover even if controls are invoked programmatically', async () => {
  const f = fixture({ can_recover: false }); await tick();
  assert.equal(f.node('choices').hidden, true);
  assert.equal(f.node('review').hidden, true);
  f.node('restore').checked = true;
  await f.node('review').emit('click'); await f.node('commit').emit('click');
  assert.deepEqual(f.calls, [['state']]);
});

test('restart retry explains current data preservation and binds the reviewed nonce', async () => {
  const f = fixture({ restart_nonce: 'b'.repeat(32),
    restore_warning: 'Restoring the pre-update checkpoint loses newer saved progress.' }); await tick();
  assert.equal(f.node('restore-option').hidden, true);
  assert.match(f.node('warning').textContent, /keeps current saved data/);
  assert.doesNotMatch(f.node('warning').textContent, /loses newer saved progress/);
  assert.match(f.node('keep-text').textContent, /already restored/);
  f.node('restore').checked = true;
  await f.node('review').emit('click'); await f.node('commit').emit('click');
  assert.deepEqual(f.calls, [['state']]);
  f.node('restore').checked = false; f.node('keep').checked = true;
  await f.node('review').emit('click');
  assert.match(f.node('confirm-text').textContent, /No data will be restored again/);
  await f.node('commit').emit('click');
  assert.deepEqual(f.calls[1], ['recover', 'a'.repeat(32), 'keep', 'b'.repeat(32)]);
});

test('another restart generation invalidates old consent even within the same operation', async () => {
  const f = fixture({ restart_nonce: 'b'.repeat(32) }); await tick();
  f.node('keep').checked = true; await f.node('review').emit('click');
  f.snapshot.restart_nonce = 'c'.repeat(32);
  await f.node('commit').emit('click');
  assert.deepEqual(f.calls, [['state']]);
});
