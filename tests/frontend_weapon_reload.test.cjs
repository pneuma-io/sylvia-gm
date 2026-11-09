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
    'window.weaponQA={state,renderCharacterPanel,weaponReloadPlan,weaponRigMarkup};\n  // ---- Public API ----');
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
  assert.match(spent, /data-weapon-reload="vesper" >Reload/);
  assert.match(spent, /Queues Cycle weapon/);
  assert.ok(spent.indexOf('data-weapon-reload') < spent.indexOf('data-panel-action-id="cycle"'), 'granular Cycle stays available');

  const full = character();
  api.state.panelProjection = { player_control: fixture.player_control, surfaces: [fixture.map, full] };
  const loaded = api.renderCharacterPanel(full);
  assert.match(loaded, /data-rig="rifle"/);
  assert.match(loaded, /data-rig-chamber="live"/);
  assert.match(loaded, /data-weapon-reload="vesper" disabled>Reload/);
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
