const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const settle = () => new Promise(resolve => setImmediate(resolve));

function fixture(initial = { format_version: 1, sessions: [] }) {
  const timers = new Map(), calls = [], failures = [];
  const window = { setTimeout(fn) { timers.set(1, fn); return 1; }, clearTimeout(id) { timers.delete(id); } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,
    '../src-tauri/frontend/desktop-drafts.js'), 'utf8'), { window });
  const f = { sessions: new Map(), questions: new Map(), timers, calls, failures,
    read: async () => initial, write: async () => {},
    tick() { const work = [...timers.values()]; timers.clear(); work.forEach(fn => fn()); },
  };
  f.store = window.SylviaDesktopDrafts.mount({
    load_desktop_drafts: () => { calls.push('load'); return f.read(); },
    save_desktop_drafts: async value => {
      calls.push(JSON.parse(JSON.stringify(value)));
      await f.write(value);
    },
  }, { sessionDrafts: f.sessions, builderQuestionDrafts: f.questions,
    failed: error => failures.push(error) });
  return f;
}

test('reopen restores exact session text and question fields without submission', async () => {
  const f = fixture();
  await f.store.load();
  f.sessions.set('dm-a', { text: 'Unsent action 🦉', oocText: 'Private note', oocOpen: true });
  f.sessions.set('builder-b', { text: 'Another draft', oocText: '', oocOpen: false });
  f.questions.set('builder-b', new Map([['q1', { text: 'Answer' }],
    ['q2', { selection: 'custom', custom: 'Another choice' }]]));
  f.store.changed();
  await f.store.flush();
  assert.equal(f.timers.size, 0);
  const saved = f.calls.at(-1);
  const reopened = fixture(saved);
  await reopened.store.load();
  assert.equal(reopened.sessions.get('dm-a').text, 'Unsent action 🦉');
  assert.equal(reopened.sessions.get('dm-a').oocOpen, true);
  assert.equal(reopened.questions.get('builder-b').get('q2').custom, 'Another choice');
  assert.deepEqual(reopened.calls, ['load']);
});

test('concurrent flush waits for newer text and never overlaps writes', async () => {
  const f = fixture();
  await f.store.load();
  const releases = [];
  let concurrent = 0, peak = 0;
  f.write = async () => {
    peak = Math.max(peak, ++concurrent);
    await new Promise(resolve => releases.push(resolve));
    concurrent -= 1;
  };
  f.sessions.set('a', { text: 'old' });
  f.store.changed();
  const first = f.store.flush();
  await settle();
  f.sessions.set('a', { text: 'new' });
  f.store.changed();
  let completed = false;
  const second = f.store.flush().then(() => { completed = true; });
  releases.shift()();
  await settle();
  assert.equal(completed, false);
  assert.equal(f.calls.at(-1).sessions[0].text, 'new');
  releases.shift()();
  await Promise.all([first, second]);
  assert.equal(peak, 1);
  assert.equal(concurrent, 0);
  assert.equal(completed, true);
});

test('update presentation is saved with current unsent text and bound to one operation', async () => {
  const f = fixture();
  assert.equal(await f.store.load(), true);
  f.sessions.set('dm-a', { text: 'Current unsent action' });
  await f.store.rememberUpdate('a'.repeat(32), {
    kind: 'session', campaign_id: 'campaign-a', session_id: 'dm-a',
  });
  const saved = f.calls.at(-1);
  assert.equal(saved.sessions[0].text, 'Current unsent action');
  assert.equal(saved.update_view.campaign_id, 'campaign-a');
  const reopened = fixture(saved);
  assert.equal(await reopened.store.load(), true);
  assert.equal(reopened.store.updateView('b'.repeat(32)), null);
  assert.equal(reopened.store.updateView('a'.repeat(32)).session_id, 'dm-a');
  assert.deepEqual(reopened.calls, ['load']);
});

test('rejected save retains all drafts, refuses flush completion, then permits explicit retry', async () => {
  const f = fixture();
  await f.store.load();
  f.sessions.set('a', { text: 'Must survive' });
  f.store.changed();
  f.write = async () => { throw new Error('Controlled disk failure'); };
  await assert.rejects(f.store.flush(), /disk failure/);
  assert.equal(f.sessions.get('a').text, 'Must survive');
  assert.equal(f.failures.length, 1);
  assert.equal(f.timers.size, 0);
  f.write = async () => {};
  await f.store.flush();
  assert.equal(f.calls.at(-1).sessions[0].text, 'Must survive');
});

test('failed initial read cannot overwrite the retained file', async () => {
  const f = fixture();
  f.read = async () => { throw new Error('Incompatible retained file'); };
  await f.store.load();
  f.sessions.set('a', { text: 'New local text' });
  f.store.changed(); f.tick(); await settle();
  await assert.rejects(f.store.flush(), /storage is unavailable/);
  assert.deepEqual(f.calls, ['load']);
  assert.equal(f.sessions.get('a').text, 'New local text');
});

test('accepted input removes only that session while other drafts remain durable', async () => {
  const f = fixture();
  await f.store.load();
  f.sessions.set('a', { text: 'Accepted' });
  f.sessions.set('b', { text: 'Keep unsent' });
  f.questions.set('b', new Map([['question', { text: 'Keep answer' }]]));
  f.store.changed(); await f.store.flush();
  f.sessions.delete('a'); f.store.changed(); await f.store.flush();
  assert.deepEqual(f.calls.at(-1).sessions.map(row => row.session_id), ['b']);
  assert.equal(f.calls.at(-1).sessions[0].questions[0].text, 'Keep answer');
});

test('actual composer hooks preserve newer text and isolate question and accepted-input cleanup', async () => {
  const f = fixture();
  await f.store.load();
  const elements = new Map([['player-input', { value: 'First draft' }],
    ['ooc-input', { value: 'Private note' }], ['ooc-note', { open: true }]]);
  const sandbox = { window: {}, document: {
    addEventListener() {}, getElementById: id => elements.get(id),
  } };
  const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  vm.runInNewContext(source.replace('  // ---- Public API ----',
    'window.draftQA = { state, saveSelectedSessionDraft, restoreSelectedSessionDraft, ' +
    'clearAcceptedInput, rememberBuilderQuestionControl };\n  // ---- Public API ----'), sandbox);
  const qa = sandbox.window.draftQA;
  qa.state.sessionDrafts = f.sessions; qa.state.builderQuestionDrafts = f.questions;
  qa.state.desktopDrafts = f.store; qa.state.activeSessionId = 'dm-a';
  qa.state.activeSessionRole = 'dm';
  qa.saveSelectedSessionDraft();
  qa.rememberBuilderQuestionControl('builder-b', { value: 'Unsent answer',
    dataset: { builderQuestionKey: 'q', builderQuestionType: 'text' } });
  const activity = { sessionId: 'dm-a', pendingInput: {
    sessionId: 'dm-a', text: 'First draft', oocText: 'Private note',
  } };
  qa.state.sessionActivities.set('dm-a', activity);
  elements.get('player-input').value = 'Newer unsent draft';
  qa.saveSelectedSessionDraft();
  qa.clearAcceptedInput(activity);
  await f.store.flush();
  assert.equal(f.sessions.get('dm-a').text, 'Newer unsent draft');
  assert.equal(f.questions.get('builder-b').get('q').text, 'Unsent answer');
  activity.pendingInput = { sessionId: 'dm-a', text: 'Newer unsent draft', oocText: 'Private note' };
  qa.clearAcceptedInput(activity);
  await f.store.flush();
  assert.equal(elements.get('player-input').value, '');
  assert.equal(f.sessions.has('dm-a'), false);
  assert.equal(f.calls.at(-1).sessions[0].session_id, 'builder-b');
});
