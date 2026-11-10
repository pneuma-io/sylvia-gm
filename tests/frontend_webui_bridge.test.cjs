const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness({ files = [{ name: 'selected.txt' }], failure, reply } = {}) {
  const calls = [], inputs = [], links = [];
  const document = {
    addEventListener() {},
    body: { appendChild() {} },
    createElement(kind) {
      if (kind === 'a') {
        const link = { clicked: false, click() { link.clicked = true; }, remove() {} };
        links.push(link);
        return link;
      }
      assert.equal(kind, 'input');
      const events = {};
      const input = {
        files, removed: false,
        addEventListener(name, callback) { events[name] = callback; },
        click() { events.change(); },
        remove() { input.removed = true; },
      };
      inputs.push(input);
      return input;
    },
  };
  class SelectedFormData {
    constructor() { this.fields = []; }
    append(name, value) { this.fields.push([name, value]); }
  }
  const context = {
    window: {}, document, TypeError, FormData: SelectedFormData,
    crypto: { randomUUID: () => 'one-request' },
    async fetch(url, options) {
      calls.push({ url, options });
      if (failure) throw failure;
      return reply || { ok: true, async json() { return { ok: true, result: { status: 'done' } }; } };
    },
  };
  vm.runInNewContext(fs.readFileSync(require.resolve('../src-tauri/frontend/webui-bridge.js'), 'utf8'), context);
  return { invoke: context.window.__sylviaBrowserInvoke, calls, inputs, links };
}

test('lost upload response explains uncertain saved work without retry or false cancellation', async () => {
  const h = harness({ failure: new TypeError('Failed to fetch') });
  await assert.rejects(h.invoke('ingest_builder_sources', { sessionId: 'chosen', ingestionId: 'same-import' }),
    /local connection was interrupted.*request was not repeated/i);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, '/api/upload/builder');
  assert.equal(h.inputs[0].removed, true);
});

test('lost RPC response keeps the existing one-request uncertainty message', async () => {
  const h = harness({ failure: new TypeError('Failed to fetch') });
  await assert.rejects(h.invoke('campaign_index'), /request was not repeated/i);
  assert.equal(h.calls.length, 1);
});

test('cancelled file selection does not send a request and releases the input', async () => {
  const h = harness({ files: [] });
  assert.equal((await h.invoke('install_package_file', { operationId: 'chosen' })).status, 'cancelled');
  assert.equal(h.calls.length, 0);
  assert.equal(h.inputs[0].removed, true);
});

test('selected upload preserves bytes, operation inputs and same-origin transport', async () => {
  const files = [{ name: 'one.md' }, { name: 'two.md' }];
  const params = { sessionId: 'selected-session', ingestionId: 'selected-ingestion' };
  const h = harness({ files });
  assert.equal((await h.invoke('ingest_builder_sources', params)).status, 'done');
  const { options } = h.calls[0];
  assert.equal(options.method, 'POST');
  assert.equal(options.credentials, 'same-origin');
  assert.equal(options.headers['X-Sylvia-WebUI'], '1');
  assert.deepEqual(JSON.parse(options.body.fields[0][1]), params);
  assert.equal(options.body.fields[1][1], files[0]);
  assert.equal(options.body.fields[2][1], files[1]);
  assert.equal(h.calls.length, 1);
});

test('owner rejection stays distinct from a lost upload response', async () => {
  const h = harness({ reply: { ok: false, async json() {
    return { ok: false, error: { message: 'Selected package rejected.' } };
  } } });
  await assert.rejects(h.invoke('install_package_file'), /Selected package rejected\./);
  assert.equal(h.calls.length, 1);
});

test('expired authentication requests sign-in without retrying a command', async () => {
  const h = harness({ reply: { ok: false, status: 401, async json() {
    throw new Error('Unauthorized responses may be plain text');
  } } });
  await assert.rejects(h.invoke('execute_panel_action'), error =>
    error.code === 'authentication_required' && /request was not repeated/.test(error.message));
  assert.equal(h.calls.length, 1);
});

for (const code of ['outcome_unknown', 'webui_unavailable', 'command_rejected']) {
  test(`HTTP bridge retains ${code} without retrying`, async () => {
    const h = harness({ reply: { ok: false, status: 400, async json() {
      return { ok: false, error: { code, message: 'Actual host category' } };
    } } });
    await assert.rejects(h.invoke('save_provider_settings'), error =>
      error.code === code && error.message === 'Actual host category');
    assert.equal(h.calls.length, 1);
  });
}

test('Character File import uploads one chosen Sylvia character file for preview only', async () => {
  const h = harness({ files: [{ name: 'vela.sylvia-gm-character' }] });
  assert.equal((await h.invoke('preview_character_bundle_file')).status, 'done');
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, '/api/upload/character_bundle');
  assert.equal(h.inputs[0].accept, '.sylvia-gm-character');
  assert.equal(h.inputs[0].multiple, false);
  assert.deepEqual(JSON.parse(h.calls[0].options.body.fields[0][1]), {});
});

test('Character File export sends the selected identity and downloads the owner-bound file', async () => {
  const reply = { status: 'exported', download: '/api/download/one', filename: 'sylvia.sylvia-gm-character',
    export: { image_count: 1, archive_sha256: 'b'.repeat(64) } };
  const h = harness({ reply: { ok: true, async json() { return { ok: true, result: reply }; } } });
  const selected = { exportId: 'export-character-one', artifactId: 'vela-0123456789abcdef',
    expectedContentSha256: 'a'.repeat(64), kind: 'character_profile' };
  assert.deepEqual(await h.invoke('export_character_file', { ...selected, suggestedName: 'Vela' }), reply);
  const body = JSON.parse(h.calls[0].options.body);
  assert.equal(h.calls[0].url, '/api/command');
  assert.equal(body.method, 'export_character_file');
  assert.deepEqual(body.params, selected, 'The desktop-only file name never reaches the owner');
  assert.equal(h.links.length, 1);
  assert.equal(h.links[0].href, '/api/download/one');
  assert.equal(h.links[0].download, 'sylvia.sylvia-gm-character');
  assert.equal(h.links[0].clicked, true);
});
