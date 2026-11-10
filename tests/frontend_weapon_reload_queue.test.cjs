// One Reload against the owner's real queue lifecycle: it reads the current durable queue for the selected
// campaign/session before composing, adds only the declared steps that are not already queued for that actor,
// and never executes unless the owner's declared response policy is automatic. Synthetic stateful owner queue
// (versioned compose like the owner); not native or provider acceptance.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/td-delivered-character-selection.json')));
const SURFACE = fixture.character.id;

function load() {
  const context = { window: {}, document: { addEventListener() {}, getElementById: () => null, querySelector: () => null,
    querySelectorAll: () => [] }, crypto: { randomUUID: (() => { let n = 0; return () => `uuid-${++n}`; })() } };
  let source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  source = source.replace('  // ---- Public API ----',
    'renderRulesetPanels=async()=>{};refreshQueuePresentation=()=>{};' +
    'window.reloadQueueQA={state,queueWeaponReloadSteps,ensureSessionActivity};\n  // ---- Public API ----');
  vm.runInNewContext(source, context);
  return context.window.reloadQueueQA;
}

// A per-session versioned queue that behaves like the owner's compose: it replaces the intended steps when the
// expected version matches, rejects a stale version, and can be told to fail or to apply-then-lose the reply.
function owner() {
  const queues = new Map();
  const calls = { compose: 0, execute: 0 };
  let mode = null;
  const queueFor = (session) => queues.get(session) || null;
  const bridge = {
    snapshot: async () => ({ revision: 7 }),
    action_sequence: async ({ session_id }) => JSON.parse(JSON.stringify(queueFor(session_id))),
    compose_action_sequence: async (command) => {
      calls.compose += 1;
      if (mode === 'reject') { mode = null; throw new Error('Owner refused the composition'); }
      const current = queueFor(command.session_id);
      if ((current?.version || 0) !== command.expected_version) throw new Error('Stale queue version');
      const next = { campaign_id: command.campaign_id, session_id: command.session_id, sequence_id: command.sequence_id,
        version: (current?.version || 0) + 1, status: 'ready', expected_revision: command.expected_revision, committed_action_ids: [],
        steps: command.steps.map((step) => ({ ...step, status: 'pending', actor: step.selection?.actor,
          label: step.source.action_id, response_policy: 'on_request' })) };
      queues.set(command.session_id, next);
      if (mode === 'lost-reply') { mode = null; throw new Error('The owner did not reply'); }
      return JSON.parse(JSON.stringify(next));
    },
    execute_action_sequence: async () => { calls.execute += 1; throw new Error('Reload must not execute the queue'); },
    player_action_availability: async (command) => ({ revision: 7, results: command.requests.map(() => ({ available: true })) }),
  };
  return { bridge, calls, queues, fail: (value) => { mode = value; }, queueFor };
}

function session(api, own, { sessionId = 'gm-1', weapons = {} } = {}) {
  const surface = JSON.parse(JSON.stringify(fixture.character));
  // Vesper and Hex both carry a spent manual rifle with a fuller spare: the plan is reload-retain then cycle.
  for (const id of ['vesper', 'hex']) surface.data.entities[id].weapon = { ...JSON.parse(JSON.stringify(surface.data.entities.hostile_alpha.weapon)),
    magazine: { id: `${id}-m0`, rounds: 1 }, ...weapons[id] };
  Object.assign(api.state, { activeCampaignId: 'campaign', activeSessionId: sessionId, activeSessionRole: 'dm', view: 'session',
    activeSnapshot: { revision: 7 }, bridge: own.bridge,
    panelProjection: { campaign_id: 'campaign', revision: 7, player_control: fixture.player_control, surfaces: [fixture.map, surface] } });
  api.ensureSessionActivity({ sessionId, sessionRole: 'dm', campaignId: 'campaign' });
  return surface;
}
const queued = (own, sessionId = "gm-1") => Array.from(own.queueFor(sessionId)?.steps || [], (step) => `${step.actor}:${step.source.action_id}`);

test('a repeated Reload adds nothing that is already queued and never executes', async () => {
  const api = load(), own = owner();
  session(api, own);
  const first = await api.queueWeaponReloadSteps(SURFACE, 'vesper');
  assert.deepEqual(Array.from(first.queued), ['Reload · retain magazine', 'Cycle weapon']);
  assert.deepEqual(queued(own), ['vesper:reload-retain', 'vesper:cycle']);
  const again = await api.queueWeaponReloadSteps(SURFACE, 'vesper');
  assert.deepEqual(Array.from(again.queued), []);
  assert.deepEqual(Array.from(again.present), ['Reload · retain magazine', 'Cycle weapon']);
  assert.deepEqual(queued(own), ['vesper:reload-retain', 'vesper:cycle']);
  assert.equal(own.calls.compose, 1);
  assert.equal(own.calls.execute, 0);
});

test('after the player clears or edits the queue at the same revision, Reload can queue again', async () => {
  const api = load(), own = owner();
  session(api, own);
  await api.queueWeaponReloadSteps(SURFACE, 'vesper');
  // Edit: the player removes the cycle step in the queue panel (owner compose with the remaining step).
  const edited = own.queueFor('gm-1');
  own.queues.set('gm-1', { ...edited, version: edited.version + 1, steps: edited.steps.filter((step) => step.source.action_id !== 'cycle') });
  const restored = await api.queueWeaponReloadSteps(SURFACE, 'vesper');
  assert.deepEqual(Array.from(restored.queued), ['Cycle weapon']);
  assert.deepEqual(queued(own), ['vesper:reload-retain', 'vesper:cycle']);
  // Clear: the queue empties (or completes); the same click at the same revision queues the plan again.
  own.queues.set('gm-1', { ...own.queueFor('gm-1'), version: own.queueFor('gm-1').version + 1, steps: [] });
  const again = await api.queueWeaponReloadSteps(SURFACE, 'vesper');
  assert.deepEqual(Array.from(again.queued), ['Reload · retain magazine', 'Cycle weapon']);
  assert.equal(own.calls.execute, 0);
});

test('two controlled actors keep separate steps; switching back does not duplicate', async () => {
  const api = load(), own = owner();
  session(api, own);
  await api.queueWeaponReloadSteps(SURFACE, 'vesper');
  await api.queueWeaponReloadSteps(SURFACE, 'hex');
  await api.queueWeaponReloadSteps(SURFACE, 'vesper');
  assert.deepEqual(queued(own), ['vesper:reload-retain', 'vesper:cycle', 'hex:reload-retain', 'hex:cycle']);
});

test('each session reads and composes its own queue', async () => {
  const api = load(), own = owner();
  session(api, own, { sessionId: 'gm-1' });
  await api.queueWeaponReloadSteps(SURFACE, 'vesper');
  session(api, own, { sessionId: 'gm-2' });
  await api.queueWeaponReloadSteps(SURFACE, 'vesper');
  assert.deepEqual(queued(own, 'gm-1'), ['vesper:reload-retain', 'vesper:cycle']);
  assert.deepEqual(queued(own, 'gm-2'), ['vesper:reload-retain', 'vesper:cycle']);
});

test('a refused composition queues nothing and a retry composes once', async () => {
  const api = load(), own = owner();
  session(api, own);
  own.fail('reject');
  await assert.rejects(api.queueWeaponReloadSteps(SURFACE, 'vesper'), /refused/);
  assert.deepEqual(queued(own), []);
  await api.queueWeaponReloadSteps(SURFACE, 'vesper');
  assert.deepEqual(queued(own), ['vesper:reload-retain', 'vesper:cycle']);
});

test('an unknown reply is resolved by reading the owner queue, never by appending again', async () => {
  const api = load(), own = owner();
  session(api, own);
  own.fail('lost-reply');
  await assert.rejects(api.queueWeaponReloadSteps(SURFACE, 'vesper'), /did not reply/);
  // The owner did apply it; the retry sees the steps and adds nothing.
  const retry = await api.queueWeaponReloadSteps(SURFACE, 'vesper');
  assert.deepEqual(Array.from(retry.queued), []);
  assert.deepEqual(queued(own), ['vesper:reload-retain', 'vesper:cycle']);
  assert.equal(own.calls.compose, 1);
});

test('a partly queued plan adds only its missing step; concurrent clicks share one lane', async () => {
  const api = load(), own = owner();
  session(api, own);
  // The player queued the magazine change by hand first.
  await own.bridge.compose_action_sequence({ campaign_id: 'campaign', session_id: 'gm-1', sequence_id: 'q', expected_version: 0,
    expected_revision: 7, steps: [{ step_id: 'manual', source: { kind: 'panel', surface_id: SURFACE, action_id: 'reload-retain' }, selection: { actor: 'vesper' } }] });
  const [one, two] = await Promise.all([api.queueWeaponReloadSteps(SURFACE, 'vesper'), api.queueWeaponReloadSteps(SURFACE, 'vesper')]);
  assert.deepEqual([...one.queued, ...two.queued], ['Cycle weapon']);
  assert.deepEqual(queued(own), ['vesper:reload-retain', 'vesper:cycle']);
  assert.equal(own.calls.execute, 0);
});
