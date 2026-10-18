// Exercise the shipped choice handler across an intermediate and final answer.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('successive choices carry their sequence and narrate only after commit', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const start = source.indexOf('  function wireMechanicsChoiceControls(');
  const end = source.indexOf('  function renderProjection(', start);
  assert(start >= 0 && end > start);
  const commands = [], renders = [], responses = [];
  const button = { dataset: { actionId: 'action', basisRevision: '7',
    choiceId: 'save', optionId: 'reroll', choiceSequence: '0' }, disabled: false };
  const container = { querySelectorAll: () => [button] };
  let status = 'awaiting_choice';
  const context = {
    state: { activeCampaignId: 'campaign', activeSessionId: 'session', bridge: {
      resolve_mechanics_choice: async (command) => { commands.push(command); return { status }; },
    } },
    replaceClickHandler: (node, callback) => { node.click = callback; },
    renderPlayerProjection: async (...args) => renders.push(args),
    respondToPlayerAction: async (...args) => responses.push(args),
  };
  vm.runInNewContext(source.slice(start, end), context);
  context.wireMechanicsChoiceControls(container);
  await button.click();
  assert.equal(commands[0].choiceSequence, 0);
  assert.equal(commands[0].expectedRevision, 7);
  assert.equal(responses.length, 0);
  assert.equal(renders.length, 1);
  // The next projection can reuse the same public request and option IDs.
  button.dataset.choiceSequence = '1';
  button.disabled = false;
  status = 'committed';
  await button.click();
  assert.equal(commands[1].choiceSequence, 1);
  assert.notEqual(commands[0].resolutionId, commands[1].resolutionId);
  assert.equal(renders.length, 2);
  assert.deepEqual(responses, [['campaign', 'session', 'action']]);
});
