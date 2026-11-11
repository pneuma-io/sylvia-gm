// Weapon rig drawing and the single Reload plan over the delivered TD character fixture (owner projection).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/td-delivered-character-selection.json')));

function load({ sequence = true } = {}) {
  const context = { window: {}, document: { addEventListener() {} } };
  let source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  source = source.replace('  // ---- Public API ----',
    'window.weaponQA={state,renderCharacterPanel,weaponReloadPlan,weaponRigMarkup,rigWeaponSnapshot,rigManipulations,rigPhasePlan};\n  // ---- Public API ----');
  vm.runInNewContext(source, context);
  const api = context.window.weaponQA;
  api.state.activeSessionRole = 'dm';
  if (sequence) api.state.bridge = { compose_action_sequence() {}, action_sequence() {} };
  return api;
}
const character = () => JSON.parse(JSON.stringify(fixture.character));
const weapon = (id, patch = {}) => ({ ...JSON.parse(JSON.stringify(fixture.character.data.entities[id].weapon)), ...patch });
const ids = (plan) => Array.from(plan.steps, (action) => action.id);

test('a spent manual action needs only the declared cycle (the repaired AP2 counterexample state)', () => {
  const { weaponReloadPlan } = load();
  const plan = weaponReloadPlan(weapon('hostile_alpha'), fixture.character.actions);
  assert.deepEqual(ids(plan), ['cycle']);
  assert.equal(plan.summary, 'Cycle weapon');
});

test('a full self-loading weapon queues nothing and says so', () => {
  const { weaponReloadPlan } = load();
  const plan = weaponReloadPlan(weapon('vesper'), fixture.character.actions);
  assert.deepEqual(ids(plan), []);
  assert.match(plan.summary, /Loaded/);
});

test('a fuller spare adds the declared magazine change; a manual action without a live round then cycles', () => {
  const { weaponReloadPlan } = load();
  assert.deepEqual(ids(weaponReloadPlan(weapon('hostile_alpha', { magazine: { rounds: 1 } }), fixture.character.actions)),
    ['reload-retain', 'cycle']);
  assert.deepEqual(ids(weaponReloadPlan(weapon('vesper', { magazine: { rounds: 3 } }), fixture.character.actions)),
    ['reload-retain']);
  // A spare that is not fuller never triggers a magazine change.
  assert.deepEqual(ids(weaponReloadPlan(weapon('vesper', { magazine: { rounds: 30 }, spare_magazines: [{ rounds: 12 }] }),
    fixture.character.actions)), []);
});

test('tube feeds and undeclared actions stay honest', () => {
  const { weaponReloadPlan } = load();
  const tube = weapon('hostile_alpha', { feed: 'TUBE', needs_cycle: false, chamber: 'LIVE', magazine: { rounds: 2 } });
  const plan = weaponReloadPlan(tube, fixture.character.actions);
  assert.deepEqual(ids(plan), []);
  assert.match(plan.summary, /no declared action/);
  const undeclared = fixture.character.actions.filter((action) => !['cycle_weapon', 'reload_weapon'].includes(action.intent));
  assert.deepEqual(ids(weaponReloadPlan(weapon('hostile_alpha'), undeclared)), []);
});

test('a tube top-up names the actor-bound shell control without adding a Reload step', () => {
  const { weaponReloadPlan } = load();
  const tube = weapon('hostile_alpha', { feed: 'TUBE', needs_cycle: false, chamber: 'LIVE', magazine: { rounds: 2 } });
  const shell = { id: 'load-shell', intent: 'load_one_shell', label: 'Load one shell', bindings: { actor_id: 'actor' } };
  const plan = weaponReloadPlan(tube, [...fixture.character.actions, shell]);
  assert.deepEqual(ids(plan), []);
  assert.equal(plan.summary, 'Use Load one shell to top up the tube');
  const unbound = { ...shell, bindings: { actor_id: 'target' } };
  assert.match(weaponReloadPlan(tube, [...fixture.character.actions, unbound]).summary, /no declared action/);
});

test('the rig draws only projected state and the Reload control heads the granular actions', () => {
  const api = load();
  // Vesper carries the projected spent manual rifle (fire, then a granular magazine change: the AP2 counterexample).
  const surface = character();
  surface.data.entities.vesper.weapon = weapon('hostile_alpha');
  api.state.panelProjection = { player_control: fixture.player_control, surfaces: [fixture.map, surface] };
  api.state.tacticalSelections[fixture.map.id] = { actor: 'vesper' };
  const spent = api.renderCharacterPanel(surface);
  assert.match(spent, /<svg class="weapon-rig"[^>]*data-rig="bolt"/);
  assert.match(spent, /data-rig-chamber="spent"/);
  assert.match(spent, /aria-label="Weapon: chamber spent, 5 in magazine"/);
  assert.match(spent, /data-weapon-reload="vesper" ><span class="suite-ico"[^]*?<span>Reload<\/span>/);
  assert.match(spent, /Queues Cycle weapon/);
  assert.ok(spent.indexOf('data-weapon-reload') < spent.indexOf('data-panel-action-id="cycle"'), 'granular Cycle stays available');

  const full = character();
  api.state.panelProjection = { player_control: fixture.player_control, surfaces: [fixture.map, full] };
  const loaded = api.renderCharacterPanel(full);
  assert.match(loaded, /data-rig="rifle"/);
  assert.match(loaded, /data-rig-chamber="live"/);
  assert.match(loaded, /data-weapon-reload="vesper" disabled><span class="suite-ico"[^]*?<span>Reload<\/span>/);
  assert.match(loaded, /Loaded · nothing to queue/);
});

test('without the owner action queue there is no Reload shortcut; the declared actions remain', () => {
  const api = load({ sequence: false });
  const surface = character();
  surface.data.entities.vesper.weapon = weapon('hostile_alpha');
  api.state.panelProjection = { player_control: fixture.player_control, surfaces: [fixture.map, surface] };
  api.state.tacticalSelections[fixture.map.id] = { actor: 'vesper' };
  const markup = api.renderCharacterPanel(surface);
  assert.doesNotMatch(markup, /data-weapon-reload/);
  assert.match(markup, /data-panel-action-id="cycle"/);
});

test('rig motion is derived only from two committed states of the same weapon, in the order it happens', () => {
  const { rigWeaponSnapshot: snap, rigManipulations: diff, rigPhasePlan: plan } = load();
  const kinds = (a, b) => Array.from(diff(snap(a), snap(b)), (step) => step.kind);
  const spent = weapon('hostile_alpha', { magazine: { id: 'm0', rounds: 1 } });
  const changed = weapon('hostile_alpha', { magazine: { id: 'm1', rounds: 5 }, retained_magazines: [{ id: 'm0', rounds: 1 }] });
  const cycled = { ...changed, magazine: { id: 'm1', rounds: 4 }, chamber: 'LIVE', needs_cycle: false };
  assert.deepEqual(kinds(spent, changed), ['reload']);
  assert.equal(diff(snap(spent), snap(changed))[0].retained, true);
  assert.deepEqual(kinds(changed, cycled), ['cycle']);
  assert.deepEqual(kinds(spent, cycled), ['reload', 'cycle'], 'a batched commit plays both, reload first');
  const live = weapon('vesper');
  assert.deepEqual(kinds(live, { ...live, magazine: { ...live.magazine, rounds: 29 } }), ['fire']);
  assert.deepEqual(kinds(live, live), [], 'no change, no motion');
  // A manual action fires the chambered round: the magazine is unchanged until the cycle.
  const ready = weapon('hostile_alpha', { chamber: 'LIVE', needs_cycle: false, magazine: { id: 'm1', rounds: 5 } });
  const fired = { ...ready, chamber: 'SPENT', needs_cycle: true };
  assert.deepEqual(kinds(ready, fired), ['fire']);
  assert.deepEqual(kinds(fired, { ...ready, magazine: { id: 'm1', rounds: 4 } }), ['cycle']);
  // Across a revision gap the number and order of shots are unknown: no shot is drawn; a magazine change still is.
  assert.deepEqual(Array.from(diff(snap(ready), snap(fired), { gap: 3 }), (step) => step.kind), []);
  assert.deepEqual(Array.from(diff(snap(live), snap({ ...live, magazine: { ...live.magazine, rounds: 25 } }), { gap: 2 }), (step) => step.kind), []);
  assert.deepEqual(Array.from(diff(snap(spent), snap(changed), { gap: 2 }), (step) => step.kind), ['reload']);
  assert.deepEqual(kinds(live, weapon('hostile_alpha')), [], 'a different weapon is not animated as a manipulation');
  assert.deepEqual(Array.from(diff(null, snap(live))), [], 'first sight (open or reopen) never plays');
  assert.deepEqual(Array.from(plan({ kind: 'cycle', ejected: 'SPENT' }, 'bolt'), (phase) => phase.label),
    ['Lift bolt', 'Draw back · eject case', 'Push forward · feed round', 'Lock down']);
  assert.deepEqual(Array.from(plan({ kind: 'cycle', ejected: 'SPENT' }, 'pump'), (phase) => phase.label),
    ['Pump back · eject shell', 'Pump forward · chamber shell']);
});
