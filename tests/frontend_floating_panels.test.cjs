// Saved geometry for browser WebUI floating panel windows (panel-workspace.js reducer).
const test = require('node:test');
const assert = require('node:assert/strict');
const { createStore } = require('../src-tauri/frontend/panel-workspace.js');

function memoryStorage() {
  const values = new Map();
  return { getItem: (key) => values.has(key) ? values.get(key) : null, setItem: (key, value) => values.set(key, value) };
}

const surfaces = [
  { id: 'map', title: 'Map', order: 10 },
  { id: 'party', title: 'Party', order: 20 },
];

test('a floated panel keeps one identity, its frame and minimized state through save and reopen', () => {
  const storage = memoryStorage(), store = createStore(storage);
  store.select({ campaignId: 'a', rulesetId: 'r', surfaces });
  const moved = store.change({ type: 'move', panelId: 'map',
    destination: { windowId: 'new', frame: { left: 300, top: 120, width: 640, height: 480 } } });
  assert.equal(moved.windows.length, 1);
  const id = moved.windows[0].id;
  assert.deepEqual(moved.windows[0].frame, { left: 300, top: 120, width: 640, height: 480, minimized: false });
  assert.deepEqual(moved.windows[0].tree.panels, ['map']);

  store.change({ type: 'frame', windowId: id, frame: { minimized: true } });
  const moved2 = store.change({ type: 'frame', windowId: id, frame: { left: 410.4, top: 200 } });
  assert.deepEqual(moved2.windows[0].frame, { left: 410, top: 200, width: 640, height: 480, minimized: true });

  const reopened = createStore(storage).select({ campaignId: 'a', rulesetId: 'r', surfaces });
  assert.equal(reopened.windows.length, 1);
  assert.equal(reopened.windows[0].id, id);
  assert.deepEqual(reopened.windows[0].frame, moved2.windows[0].frame);
  const placed = [...reopened.windows.flatMap((item) => item.tree.panels), JSON.stringify(reopened.sidebar)];
  assert.equal(placed.filter((entry) => entry === 'map').length, 1, 'map is owned by exactly one window');
  assert.equal(JSON.stringify(reopened.sidebar).includes('"map"'), false, 'the sidebar does not duplicate the floated map');
});

test('invalid frames are rejected and closing the window returns its panel to the sidebar', () => {
  const store = createStore(memoryStorage());
  store.select({ campaignId: 'a', rulesetId: 'r', surfaces });
  const moved = store.change({ type: 'move', panelId: 'map', destination: { windowId: 'new', frame: { left: 'x' } } });
  const id = moved.windows[0].id;
  assert.equal(moved.windows[0].frame, undefined);
  assert.equal(store.change({ type: 'frame', windowId: id, frame: { minimized: true } }).windows[0].frame, undefined,
    'a partial frame needs saved geometry to merge with');
  const sized = store.change({ type: 'frame', windowId: id, frame: { left: 20, top: 40, width: 10, height: 99999 } });
  assert.deepEqual(sized.windows[0].frame, { left: 20, top: 40, width: 240, height: 10000, minimized: false });
  const closed = store.change({ type: 'close-window', windowId: id });
  assert.equal(closed.windows.length, 0);
  assert.equal(JSON.stringify(closed.sidebar).includes('"map"'), true);
});
