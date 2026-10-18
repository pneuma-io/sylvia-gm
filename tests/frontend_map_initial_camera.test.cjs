const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
const start = source.indexOf('  function tacticalInitialScroll(');
const end = source.indexOf('  function wireTacticalGrid(', start);
assert(start >= 0 && end > start);
const context = {};
vm.runInNewContext(source.slice(start, end), context);
const camera = context.tacticalInitialScroll;
const dimensions = {lower: [-100, -100, -100], upper: [100, 100, 100], cellSize: 5};
const viewport = {width: 400, height: 300};
const view = {zoom: 1};

test('public bodies at the origin are visible immediately inside large negative map bounds', () => {
  const data = {entities: [{id: 'hero', position: [0, 0, 0]}, {id: 'guard', position: [5, 0, 3]}]};
  const offset = camera(data, dimensions, view, viewport);
  assert(offset.left > 400 && offset.top > 400, 'The camera must leave the empty minimum-bound corner.');
  for (const entity of data.entities) {
    const x = (entity.position[0] + 100) * 6.4 - offset.left;
    const y = (entity.position[1] + 100) * 6.4 - offset.top;
    assert(x > 0 && x < viewport.width && y > 0 && y < viewport.height);
  }
});

test('widely separated bodies focus the declared actor instead of an empty midpoint', () => {
  const data = {selection: {actor: 'hero'}, entities: [
    {id: 'guard', position: [-90, -90, 0]}, {id: 'hero', position: [90, 90, 0]}]};
  const offset = camera(data, dimensions, view, viewport);
  assert.equal((190 * 6.4 - offset.left), viewport.width / 2);
  assert.equal((190 * 6.4 - offset.top), viewport.height / 2);
});

test('empty, malformed or hidden-sized maps do not invent a camera target', () => {
  assert.equal(camera({entities: []}, dimensions, view, viewport), null);
  assert.equal(camera({entities: [{position: [NaN, 0]}, {position: [999, 0]}]}, dimensions, view, viewport), null);
  assert.equal(camera({entities: [{position: [0, 0]}]}, dimensions, view, {width: 0, height: 0}), null);
});
