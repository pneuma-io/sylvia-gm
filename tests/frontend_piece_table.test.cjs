const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/piece-table.js'), 'utf8'), context);
const render = context.SylviaPieceTable.render;

test('table renders closed owner projection with accessible action references', () => {
  const data = {format: 1, title: 'Relay', status: [{label: 'Cycle', value: '3'}], areas: [
    {label: 'Operator', position: 'bottom', groups: [{label: 'Programs', pieces: [
      {label: 'Ghost route', detail: '1 charge', action_id: 'run'}
    ]}]},
    {label: 'Remote', position: 'top', groups: [{label: 'Programs', concealed: true, count: 13}]}
  ]};
  const markup = render(data, [{id: 'run', label: 'Run program', available: true}]);
  assert.match(markup, /aria-label="Run program: Ghost route"/);
  assert.match(markup, /data-panel-action-id="run"/);
  assert.match(markup, /13 concealed pieces/);
  assert.match(markup, /<dt>Cycle<\/dt><dd>3<\/dd>/);
  assert.doesNotMatch(markup, /disabled/);
  assert.match(render(data, [{id: 'run', label: 'Run program', available: false}]), /disabled/);
});

test('all authored text is escaped, positions are bounded, unknown controls stay inert', () => {
  const markup = render({format: 1, title: '<script>', areas: [{
    label: '" onfocus="bad', position: '" bad', groups: [{label: '<img>', pieces: [
      {label: '<svg>', detail: '&secret', action_id: 'unknown', emphasis: '" bad'}
    ]}]
  }]});
  assert.doesNotMatch(markup, /<script>|<svg>|<img>| onfocus="bad|data-panel-action-id/);
  assert.match(markup, /piece-table-center/);
  assert.match(markup, /&amp;secret/);
  assert.match(render(null), /Table unavailable/);
});
