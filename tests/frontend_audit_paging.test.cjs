const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('large audit can browse older pages, pause, and return to latest with bounded loads', async () => {
  function element() {
    return {
      value: '', checked: false, children: [], handlers: {},
      classList: { add() {}, remove() {} },
      append(...items) { this.children.push(...items); },
      appendChild(item) { this.children.push(item); },
      replaceChildren(...items) { this.children = items; },
      addEventListener(name, handler) { this.handlers[name] = handler; },
      querySelector() { return null; },
    };
  }
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  get('empty-template').content = { cloneNode: element };
  get('auto-follow').checked = true;
  let total = 8997;
  const loads = [];
  let tick;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/audit.js'), 'utf8'), {
    document: { getElementById: get, createElement: element, createDocumentFragment: element },
    window: {
      setInterval(fn) { tick = fn; },
      __TAURI__: { core: { async invoke(command, args) {
        if (command === 'admin_full_audit_status') return { enabled: true, trace_count: total };
        loads.push(args.index);
        return { trace: { kind: 'synthetic', index: args.index } };
      } } },
    },
  });
  const settle = () => new Promise(resolve => setImmediate(resolve));
  const click = async id => { get(id).handlers.click(); await settle(); };
  await settle();
  assert.deepEqual(loads, Array.from({ length: 50 }, (_, i) => 8947 + i));
  loads.length = 0;
  await click('older-btn');
  assert.deepEqual(loads, Array.from({ length: 50 }, (_, i) => 8897 + i));
  assert.match(get('audit-status').textContent, /8898–8947 of 8997/);
  loads.length = 0;
  total += 10;
  tick(); await settle();
  assert.equal(loads.length, 0, 'older page remains stable as new records arrive');
  await click('pause-btn');
  await click('latest-btn');
  assert.equal(loads.length, 50, 'explicit navigation works while paused');
  assert.equal(loads.at(-1), 9006);
  loads.length = 0;
  total += 10;
  tick(); await settle();
  assert.equal(loads.length, 0, 'paused polling does not load records');
});
