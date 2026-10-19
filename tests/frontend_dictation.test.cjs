const { test } = require('node:test');
const assert = require('node:assert/strict');
const { create } = require('../src-tauri/frontend/dictation.js');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function fixture(text = 'I open the door.') {
  let selected = 'campaign-a/session-a', revision = 0;
  let value = text, start = value.length, end = start;
  let stops = 0, cancels = 0, requests = 0, signal;
  const result = deferred(), began = deferred(), states = [], timers = new Set();
  const capture = { stop: () => { stops++; return result.promise; }, cancel: () => { cancels++; } };
  const api = create({
    backend: { start: ({ signal: abort }) => { requests++; signal = abort; return began.promise; } },
    context: () => selected,
    draft: () => ({ text: value, revision, start, end }),
    replace: (next, caret) => { value = next; start = end = caret; revision++; },
    changed: state => states.push(state),
    timer: fn => { timers.add(fn); return fn; }, clear: fn => timers.delete(fn),
  });
  return { api, began, capture, result, states, timers,
    get value() { return value; }, get requests() { return requests; },
    get stops() { return stops; }, get cancels() { return cancels; }, get signal() { return signal; },
    select(a, b = a) { start = a; end = b; },
    type(next) { value = next; start = end = next.length; revision++; api.sync(); },
    switch() { selected = 'campaign-b/session-b'; value = 'Other draft'; revision++; api.sync(); },
    async record() { const pending = api.start(); began.resolve(capture); await pending; },
  };
}

test('explicit start/stop inserts at selection once and offers undo without submitting', async () => {
  const f = fixture('I open the door.'); f.select(7, 16);
  const first = f.api.start();
  assert.equal(f.api.snapshot().phase, 'requesting');
  await f.api.start(); assert.equal(f.requests, 1);
  f.began.resolve(f.capture); await first;
  assert.equal(f.api.snapshot().phase, 'recording');
  const stop = f.api.stop(); await f.api.stop(); assert.equal(f.stops, 1);
  assert.equal(f.api.snapshot().phase, 'transcribing');
  f.result.resolve('the window.'); await stop;
  assert.equal(f.value, 'I open the window.');
  assert.equal(f.api.snapshot().phase, 'ready');
  assert.equal(f.api.snapshot().canUndo, true);
  await f.api.stop(); assert.equal(f.stops, 1);
  f.api.undo(); assert.equal(f.value, 'I open the door.');
  assert.equal(f.timers.size, 0); assert.equal(f.signal.aborted, true);
});

test('typing while transcribing preserves the new draft and requires explicit insertion', async () => {
  const f = fixture(); await f.record(); const stop = f.api.stop();
  f.type('I stay here.'); f.result.resolve('and look around'); await stop;
  assert.equal(f.value, 'I stay here.');
  assert.equal(f.api.snapshot().transcript, 'and look around');
  assert.equal(f.api.insertReview('and listen'), true);
  assert.equal(f.value, 'I stay here. and listen');
  assert.equal(f.api.insertReview('duplicate'), false);
});

test('typing and returning to the old text still requires explicit review', async () => {
  const f = fixture(); await f.record(); const stop = f.api.stop();
  f.type('changed'); f.type('I open the door.');
  f.result.resolve('quietly'); await stop;
  assert.equal(f.value, 'I open the door.');
  assert.equal(f.api.snapshot().transcript, 'quietly');
});

test('cancel while permission is pending cancels a late capture without changing the draft', async () => {
  const f = fixture(); const start = f.api.start(); f.api.cancel();
  assert.equal(f.signal.aborted, true);
  f.began.resolve(f.capture); await start;
  assert.ok(f.cancels > 0); assert.equal(f.value, 'I open the door.');
  assert.equal(f.api.snapshot().phase, 'idle');
});

test('cancel ignores late transcription and preserves typing', async () => {
  const f = fixture(); await f.record(); const stop = f.api.stop();
  f.api.cancel(); f.type('Keep this correction.');
  f.result.resolve('discard this'); await stop;
  assert.equal(f.value, 'Keep this correction.');
  assert.equal(f.api.snapshot().transcript, '');
});

test('switching campaigns releases capture and cannot receive the departed transcript', async () => {
  const f = fixture(); await f.record(); const stop = f.api.stop();
  f.switch(); f.result.resolve('old campaign'); await stop;
  assert.equal(f.value, 'Other draft'); assert.equal(f.api.snapshot().transcript, '');
  assert.ok(f.cancels > 0);
});

for (const code of ['permission', 'device', 'unavailable']) {
  test(`${code} failure preserves the draft and allows a new explicit start`, async () => {
    const f = fixture(); const start = f.api.start(); f.began.reject({ code }); await start;
    assert.equal(f.value, 'I open the door.');
    assert.equal(f.api.snapshot().phase, 'error');
    assert.match(f.api.snapshot().message, /draft is kept/);
    await f.api.start(); assert.equal(f.requests, 2);
  });
}

test('provider errors are redacted and empty results leave typed input intact', async () => {
  const f = fixture(); await f.record(); const stop = f.api.stop();
  f.result.reject(new Error('secret provider diagnostic and transcript')); await stop;
  assert.equal(f.api.snapshot().phase, 'error');
  assert.doesNotMatch(f.api.snapshot().message, /secret/);
  assert.equal(f.value, 'I open the door.');
  const empty = fixture(); await empty.record(); const pending = empty.api.stop();
  empty.result.resolve('  '); await pending;
  assert.equal(empty.value, 'I open the door.'); assert.match(empty.api.snapshot().message, /No speech/);
});

test('timeout stops capture and ignores eventual results', async () => {
  const f = fixture(); await f.record(); const stop = f.api.stop();
  [...f.timers][0](); f.result.resolve('too late'); await stop;
  assert.equal(f.api.snapshot().phase, 'error'); assert.equal(f.signal.aborted, true);
  assert.equal(f.value, 'I open the door.'); assert.equal(f.timers.size, 0);
});

test('undo cannot discard a correction typed after dictation', async () => {
  const f = fixture(''); await f.record(); const stop = f.api.stop();
  f.result.resolve('I walk north.'); await stop;
  f.type('I walk south.'); f.api.undo();
  assert.equal(f.value, 'I walk south.'); assert.equal(f.api.snapshot().canUndo, false);
});

test('redo replaces only the previous dictated span and cancellation preserves the previous result', async () => {
  let result = 'north', cancelled = 0, value = 'I walk ', revision = 0, start = value.length, end = start;
  const api = create({ backend: { start: async () => ({ stop: async () => result, cancel: () => cancelled++ }) },
    context: () => 'a', draft: () => ({ text: value, revision, start, end }),
    replace: (next, caret) => { value = next; start = end = caret; revision++; },
    changed() {}, timer: () => 1, clear() {} });
  await api.start(); await api.stop(); assert.equal(value, 'I walk north');
  await api.start({ redo: true }); api.cancel(); assert.equal(value, 'I walk north');
  result = 'south'; await api.start({ redo: true }); await api.stop(); assert.equal(value, 'I walk south');
  result = 'carefully'; await api.start(); await api.stop(); assert.equal(value, 'I walk south carefully');
  assert.ok(cancelled >= 3);
});
