// The Stop and resume contract fixtures against the scripted owner the browser journeys use.
// tests/stop_resume_owner_contract.cjs runs the same fixtures against the real owner.
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { CAMPAIGNS, FIXTURES, runFixtures } = require('./stop_resume/contract.cjs');
const { ScriptedOwner } = require('./stop_resume/scripted-owner.cjs');

test('the scripted owner keeps the Stop and resume contract fixtures', async () => {
  const owner = new ScriptedOwner(CAMPAIGNS);
  const passed = await runFixtures((command, args) => owner.invoke(command, args).catch(error => {
    throw String(error.message);
  }));
  assert.deepEqual(passed, FIXTURES.map(fixture => fixture.name));
  assert.deepEqual(owner.unsupported, []);
});
