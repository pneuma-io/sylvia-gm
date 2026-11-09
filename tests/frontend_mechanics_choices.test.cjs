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

test('a refused choice says it was not applied while the saved result repaints', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const start = source.indexOf('  function wireMechanicsChoiceControls(');
  const end = source.indexOf('  function renderProjection(', start);
  const statuses = [], renders = [], responses = [];
  const button = { dataset: { actionId: 'move', basisRevision: '3', choiceId: 'movement-trigger-order',
    optionId: 'OVERWATCH', choiceSequence: '0' }, disabled: false };
  const context = {
    state: { activeCampaignId: 'campaign', activeSessionId: 'session', bridge: {
      // The owner's exact refusal when another window already resolved this saved choice.
      resolve_mechanics_choice: async () => { throw new Error('Mechanics choice reservation is unavailable'); },
    } },
    replaceClickHandler: (node, callback) => { node.click = callback; },
    renderPlayerProjection: async (...args) => renders.push(args),
    respondToPlayerAction: async (...args) => responses.push(args),
    setInlineStatus: (message) => statuses.push(message),
    friendlyCommandError: (fallback, error) => fallback + ': ' + error.message,
  };
  vm.runInNewContext(source.slice(start, end), context);
  context.wireMechanicsChoiceControls({ querySelectorAll: () => [button] });
  await button.click();
  assert.equal(renders.length, 1);
  assert.equal(responses.length, 0, 'a refused choice requests no narration');
  assert.deepEqual(statuses, ['Your choice was not applied: Mechanics choice reservation is unavailable. The saved result is shown.']);
});

// Run the shipped choice handler once against a scripted owner reply and repaint.
async function chooseWith({ reply, repaint = async () => {}, queueRead = null }) {
  const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const start = source.indexOf('  function wireMechanicsChoiceControls(');
  const end = source.indexOf('  function renderProjection(', start);
  const calls = [], statuses = [], renders = [], responses = [], events = [];
  const button = { dataset: { actionId: 'move', basisRevision: '3', choiceId: 'movement-trigger-order',
    optionId: 'SUPPRESSION', choiceSequence: '0' }, disabled: false };
  const context = {
    state: { activeCampaignId: 'campaign', activeSessionId: 'session', bridge: {
      resolve_mechanics_choice: async (command) => { calls.push(command); events.push('resolve'); return reply(); },
      ...(queueRead ? { action_sequence: async (command) => {
        events.push(`read:${command.session_id}`); return queueRead(context.state); } } : {}),
    } },
    replaceClickHandler: (node, callback) => { node.click = callback; },
    renderPlayerProjection: async (...args) => { renders.push(args); events.push('render'); await repaint(context.state); },
    respondToPlayerAction: async (...args) => { responses.push(args); events.push('narrate'); },
    setInlineStatus: (message) => statuses.push(message),
    friendlyCommandError: (fallback, error) => fallback + ': ' + error.message,
  };
  vm.runInNewContext(source.slice(start, end), context);
  context.wireMechanicsChoiceControls({ querySelectorAll: () => [button] });
  await button.click();
  return { calls, statuses, renders, responses, events };
}

test('a lost reply after a committed choice is unconfirmed, never "not applied", and is not resent', async () => {
  // The owner committed the choice, then the WebUI bridge lost the response (its outcome_unknown error).
  let saved = 'awaiting_choice';
  const result = await chooseWith({
    reply: () => { saved = 'committed'; throw Object.assign(new Error('The local connection was interrupted.'), { code: 'outcome_unknown' }); },
    repaint: async () => { assert.equal(saved, 'committed', 'the repaint reads back the committed choice'); },
  });
  assert.equal(result.calls.length, 1, 'the resolution is not sent again');
  assert.equal(result.renders.length, 1);
  assert.equal(result.responses.length, 0, 'an unconfirmed choice requests no narration');
  assert.equal(result.statuses.length, 1);
  assert.doesNotMatch(result.statuses[0], /not applied/);
  assert.match(result.statuses[0], /did not confirm your choice/);
  assert.match(result.statuses[0], /not sent again; the saved result is shown/);
});

test('an owner refusal still reports the choice as not applied, with the owner reason', async () => {
  const result = await chooseWith({ reply: () => { throw new Error('Mechanics choice reservation is unavailable'); } });
  assert.equal(result.calls.length, 1);
  assert.equal(result.responses.length, 0);
  assert.deepEqual(result.statuses,
    ['Your choice was not applied: Mechanics choice reservation is unavailable. The saved result is shown.']);
});

for (const [label, change] of [['session', { activeSessionId: 'other-session' }], ['campaign', { activeCampaignId: 'other-campaign' }]]) {
  test(`switching ${label} during the repaint leaves the new selection without the old choice notice`, async () => {
    const result = await chooseWith({
      reply: () => { throw new Error('Mechanics choice reservation is unavailable'); },
      repaint: async (state) => { Object.assign(state, change); },
    });
    assert.equal(result.renders.length, 1);
    assert.deepEqual(result.statuses, [], 'no notice is written into another selection');
    assert.equal(result.responses.length, 0);
  });
}

// The owner records a queued step's resolved choice in session history when its queue is read.
const savedQueue = () => ({ status: 'paused', steps: [{ step_id: 'move', status: 'committed' }] });

test('a committed choice reads the saved queue before Story repaints, without resending', async () => {
  const result = await chooseWith({ reply: () => ({ status: 'committed' }), queueRead: savedQueue });
  assert.deepEqual(result.events, ['resolve', 'read:session', 'render', 'narrate']);
  assert.equal(result.calls.length, 1, 'the resolution is sent once');
  assert.deepEqual(result.statuses, []);
});

test('an unconfirmed or refused choice also reads the saved queue before showing the saved result', async () => {
  for (const error of [Object.assign(new Error('The local connection was interrupted.'), { code: 'outcome_unknown' }),
    new Error('Mechanics choice reservation is unavailable')]) {
    const result = await chooseWith({ reply: () => { throw error; }, queueRead: savedQueue });
    assert.deepEqual(result.events, ['resolve', 'read:session', 'render']);
    assert.equal(result.calls.length, 1);
    assert.equal(result.statuses.length, 1);
    assert.match(result.statuses[0], error.code ? /did not confirm your choice/ : /not applied/);
  }
});

test('an awaiting follow-up choice still refreshes Story without narrating', async () => {
  const result = await chooseWith({ reply: () => ({ status: 'awaiting_choice' }), queueRead: savedQueue });
  assert.deepEqual(result.events, ['resolve', 'read:session', 'render']);
});

for (const [label, change] of [['session', { activeSessionId: 'other-session' }], ['campaign', { activeCampaignId: 'other-campaign' }]]) {
  test(`switching ${label} while the saved queue is read leaves the new selection unpainted`, async () => {
    for (const reply of [() => ({ status: 'committed' }), () => { throw new Error('Mechanics choice reservation is unavailable'); }]) {
      const result = await chooseWith({ reply, queueRead: (state) => { Object.assign(state, change); return savedQueue(); } });
      assert.deepEqual(result.renders, [], 'the old selection is not repainted over the new one');
      assert.deepEqual(result.statuses, []);
      assert.equal(result.calls.length, 1);
    }
  });
}

test('a failed queue read still repaints the saved Story', async () => {
  const result = await chooseWith({ reply: () => ({ status: 'committed' }),
    queueRead: () => { throw new Error('Queue unavailable'); } });
  assert.deepEqual(result.events, ['resolve', 'read:session', 'render', 'narrate']);
});
