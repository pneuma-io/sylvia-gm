const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sandbox = {window: {}, document: {addEventListener() {}}};
const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8')
  .replace('  // ---- Public API ----',
    'window.zoneLegendProbe = tacticalZoneLegend; window.zoneStateProbe = tacticalZoneStateClass; // ---- Public API ----');
vm.runInNewContext(source, sandbox);
const render = sandbox.window.zoneLegendProbe;
const stateClass = sandbox.window.zoneStateProbe;
const web = {shape: {kind: 'box'}, display: {label: 'Web', state: 'intact', height_label: '20 ft high'}};
const burning = {...web, display: {...web.display, state: 'burning'}};
const cloud = {shape: {kind: 'cylinder'}, display: {label: 'Cloud of Daggers: spherical footprint',
  height_label: 'Mechanical sphere radius 5 ft'}};

const markup = render([...Array(64).fill(web), burning, cloud]);
assert.equal((markup.match(/<li\b/g) || []).length, 3);
assert(markup.includes('intact') && markup.includes('burning'));
assert.equal((markup.match(/tactical-zone-burning/g) || []).length, 1);
assert(markup.includes('Mechanical sphere radius 5 ft'));
assert.equal(render([]), '');
assert.equal(render([{shape: {kind: 'box'}}]), '');
assert.equal(render([{...web, shape: {kind: 'unsupported'}}]), '');
const escaped = render([{...web, display: {label: '<img src=x onerror=1>', state: '<script>',
  height_label: 'Height & width'}}]);
assert(!escaped.includes('<img') && !escaped.includes('<script>'));
assert(escaped.includes('&lt;img') && escaped.includes('Height &amp; width'));

// The native pending area state uses the existing preview treatment without admitting arbitrary states.
const pending = {shape: {kind: 'cylinder'}, display: {label: 'Sleep · pending preview',
  state: 'Pending; spell has not resolved',
  height_label: 'Map shows the XY footprint; height still affects actual recipients'}};
const pendingMarkup = render([pending]);
assert(pendingMarkup.includes('tactical-zone-key tactical-zone-preview'));
assert(pendingMarkup.includes(pending.display.label) && pendingMarkup.includes(pending.display.state));
assert(pendingMarkup.includes(pending.display.height_label));
for (const state of ['preview', 'pending preview', pending.display.state]) {
  assert.equal(stateClass({display: {state}}), ' tactical-zone-preview');
}
for (const state of ['pending', 'pending spell', 'pending; spell has resolved', 'intact']) {
  assert.equal(stateClass({display: {state}}), '');
}
process.stdout.write('Map effects show distinct public states once, with escaped labels, exact pending preview treatment and empty removal.\n');
