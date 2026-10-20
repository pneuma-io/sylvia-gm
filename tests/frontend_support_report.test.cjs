const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(overrides = {}) {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {
      value: '', hidden: true, disabled: false, textContent: '', handlers: {},
      addEventListener(event, handler) { this.handlers[event] = handler; },
      focus() { this.focused = true; },
    });
    return elements.get(id);
  };
  get('local-report-kind').value = 'model_feedback';
  get('local-report-reproduction').value = '<script>PRIVATE deliberate feedback</script>';
  const calls = [];
  const bridge = {
    async preview_local_report(fields) {
      calls.push(['preview', fields]);
      return { preview_id: 'synthetic-id', content: JSON.stringify(fields), bytes: 200 };
    },
    async cancel_local_report(id) { calls.push(['cancel', id]); },
    async save_local_report(id) { calls.push(['save', id]); return { status: 'saved' }; },
    ...overrides,
  };
  const window = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/support-report.js'), 'utf8'), {
    window, document: { getElementById: get, querySelectorAll: () => [get('settings-tab-about'), get('settings-tab-models')] },
  });
  const controller = window.SylviaSupportReport.init(bridge);
  return { get, calls, controller, click: id => get(id).handlers.click() };
}

test('exact preview uses textContent, saves its frozen identity and clears entered text', async () => {
  const f = fixture();
  await f.click('preview-local-report-btn');
  assert.equal(f.get('local-report-preview').hidden, false);
  assert.match(f.get('local-report-content').textContent, /<script>/);
  assert.equal(f.calls[0][1].kind, 'model_feedback');
  await f.click('save-local-report-btn');
  assert.deepEqual(f.calls.find(call => call[0] === 'save'), ['save', 'synthetic-id']);
  assert.equal(f.get('local-report-preview').hidden, true);
  assert.equal(f.get('local-report-reproduction').value, '');
  assert.match(f.get('local-report-status').textContent, /saved to your chosen file/);
});

test('cancel, editing and Settings close invalidate preview without saving', async () => {
  for (const action of ['cancel', 'edit', 'close']) {
    const f = fixture();
    await f.click('preview-local-report-btn');
    if (action === 'cancel') await f.click('cancel-local-report-btn');
    if (action === 'edit') f.get('local-report-feedback').handlers.input();
    if (action === 'close') f.controller.close();
    await f.click('save-local-report-btn');
    assert.equal(f.get('local-report-preview').hidden, true);
    assert.equal(f.calls.filter(call => call[0] === 'save').length, 0);
    assert.deepEqual(f.calls.find(call => call[0] === 'cancel'), ['cancel', 'synthetic-id']);
  }
});

test('late preview after Settings close is discarded; save chooser cancel clears the preview', async () => {
  let resolvePreview;
  const f = fixture({ preview_local_report: () => new Promise(resolve => { resolvePreview = resolve; }) });
  const preparing = f.click('preview-local-report-btn');
  f.controller.close();
  resolvePreview({ preview_id: 'late', content: 'late content', bytes: 12 });
  await preparing;
  assert.equal(f.get('local-report-preview').hidden, true);
  assert.equal(f.get('local-report-content').textContent, '');
  assert.deepEqual(f.calls, [['cancel', 'late']]);

  const cancelled = fixture({ async save_local_report() { return { status: 'cancelled' }; } });
  await cancelled.click('preview-local-report-btn');
  await cancelled.click('save-local-report-btn');
  assert.equal(cancelled.get('local-report-preview').hidden, true);
  assert.match(cancelled.get('local-report-status').textContent, /Cancelled/);
});

test('save failure keeps preview and permits retry without showing exception content', async () => {
  let attempt = 0;
  const f = fixture({ async save_local_report() {
    attempt += 1;
    if (attempt === 1) throw new Error('PRIVATE path or token');
    return { status: 'saved' };
  } });
  await f.click('preview-local-report-btn');
  await f.click('save-local-report-btn');
  assert.equal(f.get('local-report-preview').hidden, false);
  assert.doesNotMatch(f.get('local-report-status').textContent, /PRIVATE/);
  await f.click('save-local-report-btn');
  assert.equal(attempt, 2);
});

test('closing after chooser confirmation waits for save completion and retains the result', async () => {
  let completeSave;
  let chooserReturned = false;
  const f = fixture({ async save_local_report() {
    chooserReturned = true;
    await new Promise(resolve => { completeSave = resolve; });
    return { status: 'saved' };
  } });
  await f.click('preview-local-report-btn');
  const saving = f.click('save-local-report-btn');
  assert.equal(chooserReturned, true);
  assert.equal(f.get('close-settings-btn').disabled, true);
  assert.equal(f.get('settings-tab-models').disabled, true);
  assert.equal(f.get('cancel-local-report-btn').disabled, true);
  assert.equal(f.controller.close(), false);
  await f.click('cancel-local-report-btn');
  assert.equal(f.get('local-report-preview').hidden, false);
  assert.match(f.get('local-report-status').textContent, /Saving report/);
  assert.equal(f.calls.filter(call => call[0] === 'cancel').length, 0);
  completeSave(); await saving;
  assert.equal(f.get('close-settings-btn').disabled, false);
  assert.equal(f.get('settings-tab-models').disabled, false);
  const result = f.get('local-report-status').textContent;
  assert.match(result, /saved to your chosen file/);
  assert.equal(f.controller.close(), true);
  assert.equal(f.get('local-report-status').textContent, result);
});

test('failed save never claims cancellation undid a possibly committed file', async () => {
  const f = fixture({ async save_local_report() { throw Error('PRIVATE transport failure'); } });
  await f.click('preview-local-report-btn');
  await f.click('save-local-report-btn');
  assert.match(f.get('local-report-status').textContent, /could not confirm|Could not confirm/);
  await f.click('cancel-local-report-btn');
  assert.doesNotMatch(f.get('local-report-status').textContent, /No report was saved/);
  assert.match(f.get('local-report-status').textContent, /Check your chosen file/);
});
