// The Stop and resume contract fixtures against the real owner, through the WebUI command adapter.
// Run it through pytest tests/test_stop_resume_owner_contract.py, which starts the owner.
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { FIXTURES, runFixtures } = require('./stop_resume/contract.cjs');

const base = process.env.SYLVIA_TEST_OWNER_URL;
assert.ok(base, 'Run this helper through pytest tests/test_stop_resume_owner_contract.py');

// The WebUI's transport, outside a browser: one signed-in local session; errors become the
// desktop host's "code: message" strings, so the fixtures see the same contract either way.
async function webuiTransport(url) {
  const page = await fetch(`${url}/app.html`);
  assert.equal(page.status, 200);
  const cookie = page.headers.getSetCookie().map(value => value.split(';')[0]).join('; ');
  const started = Date.now();
  return async (method, params) => {
    // One line per command, so a stalled fixture shows where it stopped.
    const at = () => `${((Date.now() - started) / 1000).toFixed(2)}s`;
    console.log(`# ${at()} → ${method}`);
    const response = await fetch(`${url}/api/command`, { method: 'POST', headers: { Origin: url,
      'X-Sylvia-WebUI': '1', 'Content-Type': 'application/json', Cookie: cookie },
    body: JSON.stringify({ id: crypto.randomUUID(), method, params }) });
    const envelope = await response.json();
    console.log(`# ${at()} ← ${method} ${envelope.ok ? 'ok' : envelope.error?.code}`);
    if (!envelope.ok) throw `${envelope.error?.code || 'command_rejected'}: ${envelope.error?.message || ''}`;
    return envelope.result;
  };
}

test('the real owner keeps the Stop and resume contract fixtures', { timeout: 120000 }, async () => {
  const passed = await runFixtures(await webuiTransport(base));
  assert.deepEqual(passed, FIXTURES.map(fixture => fixture.name));
});
