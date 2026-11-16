// A Ruleset's panel look is data validated over a named base (architecture contract §10.5.1): unknown styles, bases
// and fields fall back, colours must be #rrggbb and readable, and nothing declared becomes code or free-form CSS.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const context = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/panel-looks.js'), 'utf8'), context);
const looks = context.SylviaPanelLooks;

test('SRD Lite resolves to the fantasy look with its declared ability order', () => {
  const look = looks.resolve('sylvia.srd-lite.bg3');
  assert.equal(look.base, 'fantasy');
  assert.equal(look.theme.heading, 'serif');
  assert.equal(look.widgets.party.style, 'adventurer-cards');
  assert.equal(look.widgets.unit_list.style, 'character-sheet');
  assert.deepEqual([...look.widgets.unit_list.fields.abilities],
    ['strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma']);
  assert.equal(look.widgets.encounter.style, 'initiative-ladder');
  assert.equal(look.widgets.coordinate_map.style, 'battlefield');
});

test('Trigger Discipline resolves to the tactical look', () => {
  const look = looks.resolve('pneuma-io.trigger-discipline');
  assert.equal(look.base, 'tactical');
  assert.equal(look.widgets.party.style, 'squad-strip');
  assert.equal(look.widgets.unit_list.style, 'operator');
  assert.equal(look.widgets.tactical_grid.style, 'tactical-grid');
});

test('a Ruleset without a declaration, or with none at all, gets the neutral look', () => {
  for (const id of [null, undefined, '', 'example.pathfinder', 'constructor', '__proto__']) {
    const look = looks.resolve(id);
    assert.equal(look.base, 'neutral', String(id));
    assert.equal(look.rulesetId, null);
    assert.equal(look.widgets.party.style, 'roster');
    assert.equal(look.widgets.unit_list.style, 'sheet');
    assert.equal(look.theme.heading, 'sans');
  }
});

test('a declared fantasy look keeps readable colours and drops unreadable or malformed ones', () => {
  const look = looks.fromDeclaration({ version: 1, theme: { base: 'fantasy', accent: '#7FD1FF', ally: '#202020', hostile: 'red',
    resource: 'url(javascript:alert(1))', heading: 'condensed' }, widgets: { party: 'roster' } });
  assert.equal(look.base, 'fantasy');
  assert.equal(look.theme.accent, '#7fd1ff');
  assert.equal(look.theme.ally, looks.BASES.fantasy.theme.ally, 'too dark to read on a panel card');
  assert.equal(look.theme.hostile, looks.BASES.fantasy.theme.hostile, 'named colours are not accepted');
  assert.equal(look.theme.resource, looks.BASES.fantasy.theme.resource);
  assert.equal(look.theme.heading, 'condensed');
  assert.equal(look.widgets.party.style, 'roster', 'a Ruleset may pick any built-in style');
  assert.equal(look.widgets.unit_list.style, 'character-sheet', 'unchosen renderers keep the base style');
});

test('unknown styles, bases, fields and versions fall back instead of failing', () => {
  const unknownStyle = looks.fromDeclaration({ version: 1, theme: { base: 'tactical' }, widgets: { party: 'my-custom-html', encounter: { style: 'x' } } });
  assert.equal(unknownStyle.widgets.party.style, 'squad-strip');
  assert.equal(unknownStyle.widgets.encounter.style, 'turn-list');
  const unknownBase = looks.fromDeclaration({ version: 1, theme: { base: 'cyberpunk', accent: '#ffffff' }, widgets: {} });
  assert.equal(unknownBase.base, 'neutral');
  assert.equal(unknownBase.theme.accent, looks.BASES.neutral.theme.accent, 'values for an unknown base are ignored');
  assert.equal(looks.fromDeclaration({ version: 2, theme: { base: 'fantasy' } }).base, 'neutral');
  const badFields = looks.fromDeclaration({ version: 1, theme: { base: 'fantasy' },
    widgets: { unit_list: { style: 'character-sheet', fields: { abilities: ['strength', '<b>dex</b>'], script: ['x'] } } } });
  assert.deepEqual({ ...badFields.widgets.unit_list.fields }, {}, 'invalid identities and undeclared fields are dropped');
});

test('apply writes only the base name and the validated values to the root', () => {
  const written = {};
  const root = { dataset: {}, style: { setProperty: (name, value) => { written[name] = value; } } };
  looks.apply(root, looks.resolve('sylvia.srd-lite.bg3'));
  assert.equal(root.dataset.panelLook, 'fantasy');
  assert.deepEqual(Object.keys(written).sort(), ['--look-accent', '--look-ally', '--look-heading-font', '--look-hostile', '--look-resource']);
  assert.equal(written['--look-heading-font'], 'var(--font-serif)');
  assert.ok(Object.values(written).every((value) => /^(#[0-9a-f]{6}|var\(--font-[a-z]+\))$/.test(value)));
});
