const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const settle = () => new Promise(resolve => setImmediate(resolve));

function fixture(invoke = () => Promise.resolve({})) {
  const handlers = {}, calls = [], timers = new Map();
  let now = 0, timerId = 0, maximumTimers = 0;
  const window = {
    addEventListener(name, handler) { (handlers[name] ||= []).push(handler); },
    setTimeout(callback, delay) {
      timers.set(++timerId, { callback, due: now + delay });
      maximumTimers = Math.max(maximumTimers, timers.size);
      return timerId;
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/local-diagnostics.js'), 'utf8'), { window });
  const bridge = { record_local_fault(...args) { calls.push(args); return invoke(...args); } };
  window.SylviaLocalDiagnostics.init(bridge);
  window.SylviaLocalDiagnostics.init(bridge);
  return {
    calls, handlers, timers,
    get maximumTimers() { return maximumTimers; },
    emit(category, event = {}) { handlers[category][0](event); },
    tick(milliseconds) {
      const end = now + milliseconds;
      while (true) {
        const next = [...timers].filter(([, item]) => item.due <= end).sort((a, b) => a[1].due - b[1].due)[0];
        if (!next) break;
        now = next[1].due;
        timers.delete(next[0]);
        next[1].callback();
      }
      now = end;
    },
  };
}

test('fixed category counters never inspect event objects and listeners install once', async () => {
  const f = fixture();
  const privateError = { message: 'PRIVATE input and personal path', reason: { token: 'PRIVATE' },
    toJSON() { throw Error('event must never be inspected or serialized'); } };
  assert.equal(f.handlers.error.length, 1);
  assert.equal(f.handlers.unhandledrejection.length, 1);
  f.emit('error', privateError);
  f.emit('unhandledrejection', privateError);
  assert.equal(f.calls.length, 0);
  f.tick(1000); await settle();
  f.tick(1000); await settle();
  assert.deepEqual(f.calls, [['renderer_error', 1], ['renderer_rejection', 1]]);
  assert.equal(f.maximumTimers, 1);
});

test('10,000 events behind a stalled bridge use one invocation and recover exact counts', async () => {
  let release;
  let unresolved = 0, maximumUnresolved = 0;
  const f = fixture(() => {
    if (f.calls.length > 1) return Promise.resolve({});
    unresolved += 1;
    maximumUnresolved = Math.max(maximumUnresolved, unresolved);
    return new Promise(resolve => { release = () => { unresolved -= 1; resolve({}); }; });
  });
  f.emit('error');
  f.tick(1000);
  for (let i = 1; i < 10000; i += 1) f.emit(i % 2 ? 'unhandledrejection' : 'error', { private: i });
  f.tick(600000); await settle();
  assert.equal(f.calls.length, 1);
  assert.equal(f.timers.size, 0, 'no timer spins while the IPC remains unresolved');
  assert.equal(maximumUnresolved, 1);
  release(); await settle();
  f.tick(1000); await settle();
  f.tick(1000); await settle();
  assert.deepEqual(f.calls, [['renderer_error', 1], ['renderer_rejection', 5000], ['renderer_error', 4999]]);
  assert.equal(f.calls.reduce((sum, [, delta]) => sum + delta, 0), 10000);
  assert.equal(f.maximumTimers, 1);
  assert.equal(f.timers.size, 0);
});

test('both category counters saturate without queuing work or leaking event contents', async () => {
  let release;
  const f = fixture(() => f.calls.length === 1 ? new Promise(resolve => { release = resolve; }) : Promise.resolve({}));
  f.emit('error'); f.tick(1000);
  for (let i = 0; i < 100000; i += 1) {
    f.emit('error'); f.emit('unhandledrejection');
  }
  f.tick(3600000);
  assert.equal(f.calls.length, 1);
  release({}); await settle();
  f.tick(1000); await settle();
  f.tick(1000); await settle();
  assert.deepEqual(f.calls, [['renderer_error', 1], ['renderer_rejection', 65535], ['renderer_error', 65535]]);
  assert.equal(f.maximumTimers, 1);
  assert.equal(f.timers.size, 0);
});

test('sync throws and rejected promises drop the uncertain batch and back off without retries', async () => {
  for (const failure of ['sync', 'async']) {
    let failing = true;
    const f = fixture(() => {
      if (!failing) return Promise.resolve({});
      if (failure === 'sync') throw Error('PRIVATE');
      return Promise.reject(Error('PRIVATE'));
    });
    f.emit('error'); f.tick(1000); await settle();
    assert.equal(f.calls.length, 1);
    f.tick(3600000); await settle();
    assert.equal(f.calls.length, 1, 'a failed batch is not retried');
    failing = false;
    f.emit('error'); f.emit('error');
    f.tick(29999); await settle();
    assert.equal(f.calls.length, 1);
    f.tick(1); await settle();
    assert.deepEqual(f.calls, [['renderer_error', 1], ['renderer_error', 2]]);
    assert.equal(f.timers.size, 0);
    assert.equal(f.maximumTimers, 1);
  }
});
