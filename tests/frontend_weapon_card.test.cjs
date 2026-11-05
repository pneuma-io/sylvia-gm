// The ready-weapon card shows carried weapon facts; prices belong to the owner's action preview.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function weaponCard(entity) {
  let source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const insertion = source.lastIndexOf('  return {');
  source = source.slice(0, insertion) + '  window.weaponTest = { weaponCardMarkup };\n' + source.slice(insertion);
  const sandbox = { window: {}, document: { addEventListener() {} } };
  vm.runInNewContext(source, sandbox);
  return sandbox.window.weaponTest.weaponCardMarkup(entity);
}

test('base reload is not presented as the price an operator would pay', () => {
  // A RIFLEMAN with TD's Operator specialty entry (rifleman_entries.OPERATOR) carries base
  // reload_ap 2; the owner's reload preview applies the specialty discount and prices it lower.
  const html = weaponCard({ class_id: 'RIFLEMAN', specialties: ['specialty.operator.entry.weapon_manipulation'],
    weapon: { id: 'service_rifle', reload_ap: 2,
    feed: 'DETACHABLE_MAGAZINE', magazine: { rounds: 30 }, damage: 5 } });
  assert.match(html, /<dt>Base reload<\/dt><dd>2 AP<\/dd>/);
  assert.doesNotMatch(html, /<dt>Reload<\/dt>/);
  assert.doesNotMatch(html, /1 AP/);
});
