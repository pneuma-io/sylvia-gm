const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sandbox = {window: {}, document: {addEventListener() {}}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8'), sandbox);
const render = sandbox.window.SylviaGMFrontend.toolCardMarkup;
const attack = {label: 'guard', value: 'Attack roll', emphasis: false,
  equation: {die_sides: 20, roll: 4, modifier: 10, total: 14}};
const damage = {label: 'guard', value: 'Force damage', emphasis: false,
  equation: {die_sides: 10, roll: 4, rolls: [4], modifier: 0, total: 4}};

function diceCount(summary, equations, details) {
  const card = {version: 1, title: 'Independent spell rolls', summary, equations, details, actions: []};
  const markup = render({tool_name: 'cast_spell', result: {status: 'committed', result_card: card}}, '');
  return (markup.match(/class="result-die"/g) || []).length;
}

// One featured copy replaces one detail occurrence, not every equal independent roll.
assert.equal(diceCount([], [attack, damage], [attack, damage, attack, damage]), 4);
assert.equal(diceCount([], [attack, attack], [attack, attack, attack]), 3);
assert.equal(diceCount([damage], [], [damage, damage]), 2);
assert.equal(diceCount([], [attack], [attack]), 1);
assert.equal(diceCount([], [], [attack, attack]), 2);
assert.equal(diceCount([damage], [attack], [damage, attack]), 2);
process.stdout.write('Equal independent rolls stay visible; corresponding featured copies appear once.\n');
