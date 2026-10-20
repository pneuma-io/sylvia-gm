const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/piece-table.js'), 'utf8'), context);
const render = context.SylviaPieceTable.render;
const selection = context.SylviaPieceTable.selection;

test('piece binding identifies only a currently exposed piece advertising that action', () => {
  const action = {id: 'run', label: 'Run program', available: true, bindings: {selected: 'piece'}};
  const data = {format: 1, title: 'Relay', areas: [{position: 'bottom', label: 'Operator', groups: [
    {label: 'Programs', pieces: [{id: 'ghost', label: 'Ghost route', action_id: 'run'},
      {id: 'spent', label: 'Spent route'}]},
    {label: 'Remote programs', concealed: true, pieces: [{id: 'secret', action_id: 'run'}]},
  ]}]};
  assert.match(render(data, [action]), /data-panel-piece-id="ghost"/);
  assert.equal(JSON.stringify(selection(data, action, 'ghost')), '{"piece":"ghost"}');
  for (const id of ['secret', 'spent', 'unknown', '', null]) assert.equal(selection(data, action, id), null);
  assert.equal(selection(data, {...action, bindings: undefined}, 'ghost'), null);
  assert.equal(selection(data, {...action, bindings: {actor: 'actor'}}, 'ghost'), null);
});

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
  data.areas[1].groups[0].count = 1;
  assert.match(render(data), /1 concealed piece<\/p>/);
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
