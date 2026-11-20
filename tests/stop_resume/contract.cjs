// Contract fixtures for Stop, Retry and reopen, run through any owner transport.
//
// A transport is invoke(command, args): the desktop host's command names and camelCase
// arguments, resolving with the owner's result or rejecting with "code: message". The same
// fixtures run against the scripted owner (frontend_stop_resume_contract.test.cjs) and the real
// owner through the WebUI command adapter (test_stop_resume_owner_contract.py), so the scripted
// owner the browser journeys use cannot drift from the real one unnoticed. To check another
// owner, such as the Rust port, give runFixtures an invoke that reaches it.
'use strict';

const assert = require('node:assert/strict');

// The campaigns the fixtures use; the harness starts each as a No Ruleset campaign.
const CAMPAIGNS = ['Contract Stop', 'Contract Retry', 'Contract Apart', 'Contract Beside'];

const reply = text => 'The Game Master answers: ' + text.replace(/\[(hold|fail|late)\]/g, '').trim();
const keys = value => Object.keys(value).sort();
const INTERRUPTION_KEYS = ['campaign_id', 'failed_attempt_id', 'interruption_id', 'last_action_id',
  'resolved_sequence', 'resolving_retry_id', 'retryable', 'turn_id'];
const RETRY_PROJECTION_KEYS = ['action_id', 'attempt_id', 'collapsed_by_default', 'kind', 'provisional',
  'safe_payload', 'sequence', 'text'];
const RETRY_PAYLOAD_KEYS = ['can_redirect', 'failed_attempt_id', 'interruption_id', 'resolving_retry_id',
  'saved_changes'];
const RETRY_INPUT_KEYS = ['action_receipt', 'campaign_id', 'canonical_high_water', 'failed_attempt_id',
  'interruption_id', 'retry_id', 'turn_id'];

// A thin client over a transport, naming each step as the frontend takes it.
function client(invoke) {
  let serial = 0;
  const id = prefix => `${prefix}-contract-${Date.now().toString(36)}-${++serial}`;
  const self = {
    invoke,
    async campaign(name) {
      const row = (await invoke('campaign_index', {})).find(entry => entry.display_name === name);
      assert.ok(row, `campaign ${name} exists`);
      return row;
    },
    // Start a message; returns its identities and the pending result, which is settled later.
    submit(row, text, redirectInterruptionId = null) {
      const command = { sessionId: row.session_id, turnId: row.turn_id, attemptId: id('attempt'),
        messageId: id('message'), userText: text, oocText: null, allowPublicWeb: false, builderStart: false,
        playerActionId: null, automaticResponse: false, redirectInterruptionId, campaignEndingChoice: null };
      const result = invoke('submit_session_input', command);
      result.catch(() => {});
      return { ...command, result };
    },
    history: row => invoke('session_history_page', { sessionId: row.session_id, beforeSequence: null, limit: 50 }),
    // Narration after `after` plus live controls (the Retry offer). With null the owner sends
    // live controls only: the frontend asks from the newest sequence its history page holds.
    projection: (row, after = 0) => invoke('player_projection_page', { campaignId: row.campaign_id,
      afterSequence: after, loadId: null }),
    // Wait until the owner reports this attempt as working. Reads owner state; no fixed delay.
    async working(row, attemptId) {
      for (let tries = 0; tries < 500; tries++) {
        const page = await self.history(row);
        if (page.activity_progress?.attempt_id === attemptId && page.activity_progress.phase === 'working') return;
        await new Promise(resolve => setImmediate(resolve));
      }
      assert.fail(`attempt ${attemptId} never started working`);
    },
    retryId: () => id('retry'),
  };
  return self;
}

async function rejection(promise) {
  try { await promise; } catch (error) { return String(error?.message ?? error); }
  assert.fail('expected the owner to refuse');
}

const users = page => page.messages.filter(message => message.kind === 'user');
const terminals = page => page.messages.filter(message => message.content?.kind === 'session_attempt_terminal');
const talk = projection => projection.filter(entry => entry.kind === 'talk').map(entry => entry.text);

const FIXTURES = [
  {
    name: 'Stop settles a running reply as a Retry offer that a new message can redirect',
    async run(owner) {
      const row = await owner.campaign('Contract Stop');
      const held = owner.submit(row, '[hold] I listen at the door.');
      await owner.working(row, held.attemptId);
      const stopped = await owner.invoke('interrupt_stream', { campaignId: row.campaign_id, attemptId: held.attemptId });
      assert.deepEqual(keys(stopped), INTERRUPTION_KEYS);
      assert.equal(stopped.failed_attempt_id, held.attemptId);
      assert.equal(stopped.retryable, true);
      const settled = await held.result;
      assert.deepEqual(settled.canonical, [], 'a stopped reply commits no narration');
      assert.deepEqual(settled.interruption, stopped);

      const [offer, ...rest] = await owner.projection(row);
      assert.deepEqual(rest, []);
      assert.deepEqual(keys(offer), RETRY_PROJECTION_KEYS);
      assert.deepEqual(keys(offer.safe_payload), RETRY_PAYLOAD_KEYS);
      assert.deepEqual([offer.kind, offer.sequence, offer.attempt_id], ['retry', null, held.attemptId]);
      assert.equal(offer.safe_payload.interruption_id, stopped.interruption_id);
      assert.equal(offer.safe_payload.can_redirect, true, 'a Stop can be redirected');
      const history = await owner.history(row);
      assert.deepEqual(users(history).map(message => message.content.text), ['[hold] I listen at the door.']);
      assert.deepEqual(terminals(history).map(message => [message.content.status, message.content.failure_category,
        message.content.attempt_id]), [['interrupted', 'user_cancelled', held.attemptId]]);
      assert.deepEqual(await owner.projection(row), [offer], 'reading again gives the same offer');
      assert.deepEqual(await owner.projection(row, null), [offer], 'the offer is a live control');

      const redirect = owner.submit(row, 'I knock instead.', stopped.interruption_id);
      const answered = await redirect.result;
      assert.deepEqual(answered.canonical.map(event => [event.event_type, event.safe_payload.text]),
        [['talk', reply('I knock instead.')]]);
      assert.deepEqual(talk(await owner.projection(row)), [reply('I knock instead.')]);
      assert.deepEqual(await owner.projection(row, null), [], 'null asks for live controls only');
      assert.deepEqual(await owner.projection(row, 1), [], 'nothing after the newest sequence');
      assert.equal((await owner.projection(row)).some(entry => entry.kind === 'retry'), false);
      const redirected = users(await owner.history(row)).at(-1).content;
      assert.equal(redirected.redirect_interruption_id, stopped.interruption_id);
    },
  },
  {
    name: 'a failed reply needs Retry, which answers once and replays its saved result',
    async run(owner) {
      const row = await owner.campaign('Contract Retry');
      const failed = owner.submit(row, '[fail] I open the door.');
      assert.match(await rejection(failed.result), /^provider_error: /);
      const [offer] = await owner.projection(row);
      assert.equal(offer.kind, 'retry');
      assert.equal(offer.safe_payload.can_redirect, false, 'a failure cannot be redirected');
      assert.equal(offer.safe_payload.failed_attempt_id, failed.attemptId);
      assert.deepEqual(terminals(await owner.history(row)).map(message => [message.content.status,
        message.content.failure_category]), [['interrupted', 'provider_unavailable']]);

      const basis = await owner.invoke('retryable_interruption', { campaignId: row.campaign_id,
        failedAttemptId: failed.attemptId });
      assert.deepEqual(keys(basis), INTERRUPTION_KEYS);
      assert.equal(basis.interruption_id, offer.safe_payload.interruption_id);
      assert.equal(await rejection(owner.submit(row, 'I walk away.').result),
        'command_rejected: Retry or resolve the interrupted turn first', 'plain input is refused until Retry');
      assert.equal(await rejection(owner.submit(row, 'I walk away.', basis.interruption_id).result),
        'command_rejected: Only stopped narration accepts replacement input', 'a failure cannot be redirected');
      assert.equal(users(await owner.history(row)).length, 1, 'refused input saves nothing');

      const retryId = owner.retryId();
      const retried = await owner.invoke('manual_retry_session', { sessionId: row.session_id,
        interruptionId: basis.interruption_id, retryId });
      assert.deepEqual(keys(retried.input), RETRY_INPUT_KEYS);
      assert.equal(retried.replayed, false);
      assert.deepEqual(retried.canonical.map(event => event.safe_payload.text), [reply('[fail] I open the door.')]);
      const again = await owner.invoke('manual_retry_session', { sessionId: row.session_id,
        interruptionId: basis.interruption_id, retryId });
      assert.equal(again.replayed, true, 'repeating a Retry identity replays it');
      assert.deepEqual(again.canonical, retried.canonical);
      assert.deepEqual(talk(await owner.projection(row)), [reply('[fail] I open the door.')]);
      assert.equal(users(await owner.history(row)).length, 1, 'Retry did not save the message again');
    },
  },
  {
    name: 'a reply running in one campaign neither blocks nor appears in another',
    async run(owner) {
      const apart = await owner.campaign('Contract Apart');
      const beside = await owner.campaign('Contract Beside');
      const held = owner.submit(apart, '[hold] I wait by the gate.');
      await owner.working(apart, held.attemptId);
      const other = owner.submit(beside, 'I light a lantern.');
      assert.deepEqual((await other.result).canonical.map(event => event.safe_payload.text),
        [reply('I light a lantern.')]);
      assert.deepEqual(talk(await owner.projection(apart)), []);
      assert.deepEqual(talk(await owner.projection(beside)), [reply('I light a lantern.')]);
      await owner.invoke('interrupt_stream', { campaignId: apart.campaign_id, attemptId: held.attemptId });
      await held.result;
      assert.deepEqual(talk(await owner.projection(beside)), [reply('I light a lantern.')]);
    },
  },
];

// Run every fixture through `invoke` and return the names that passed; the first failure throws.
async function runFixtures(invoke) {
  const owner = client(invoke);
  const passed = [];
  for (const fixture of FIXTURES) {
    try { await fixture.run(owner); }
    catch (error) { error.message = `${fixture.name}: ${error.message}`; throw error; }
    passed.push(fixture.name);
  }
  return passed;
}

module.exports = { CAMPAIGNS, FIXTURES, runFixtures };
