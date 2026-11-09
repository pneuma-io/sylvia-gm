const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Regression probes for the confirmed activity contract.
const sandbox = { window: {}, document: { addEventListener() {} } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname,
  '../src-tauri/frontend/app.js'), 'utf8'), sandbox);
const group = (messages, role, live = null) =>
  sandbox.window.SylviaGMFrontend.groupedAgentActivity(
    messages, messages.map(m => m.kind === 'user' ? 'USER' : ''), {}, role, live,
  ).join('');
const input = { message_id: 'input', kind: 'user', content: { attempt_id: 'a', text: 'Continue' } };
const activity = { attempt_id: 'a', basis_message_id: 'input', source_order: 1 };

for (const role of ['dm', 'builder']) {
  test(`${role}: admitted live attempt has an activity row before its first result`, () => {
    const html = group([input], role, { attemptId: 'a', phase: 'working' });
    assert.match(html, /class="agent-activity"/);
  });
  test(`${role}: a saved Stop remains Stopped inside its activity row`, () => {
    const html = group([input, { message_id: 'stop', kind: 'evidence', content: {
      kind: role === 'dm' ? 'session_attempt_terminal' : 'builder_attempt_terminal',
      attempt_id: 'a', status: role === 'dm' ? 'interrupted' : 'stopped',
      ...(role === 'dm' ? { failure_category: 'user_cancelled' } : {}),
    } }], role);
    assert.match(html, /agent-activity-status">Stopped</);
    assert.doesNotMatch(html, /agent-activity-status">Interrupted</);
  });
  test(`${role}: a completed attempt retains the owner's View activity row`, () => {
    const html = group([input, { message_id: 'done', kind: 'evidence', content: {
      kind: role === 'dm' ? 'session_attempt_terminal' : 'builder_attempt_terminal',
      attempt_id: 'a', status: 'completed',
    } }], role);
    assert.match(html, /<summary>View activity/);
  });
}

test('GM: a rejected read result never appears as a Done operation', () => {
  const html = group([input, { message_id: 'rejection', kind: 'evidence', content: {
    kind: 'provider_tool_results', activity,
    results: [{ tool_call_id: 'read', name: 'read_campaign_reference',
      content: JSON.stringify({ status: 'rejected', success: false, private: 'SECRET SOURCE' }) }],
  } }], 'dm');
  assert.match(html, /Read campaign data/);
  assert.doesNotMatch(html, /<span>Done<\/span>/);
  assert.doesNotMatch(html, /SECRET SOURCE/);
});

// Match ProviderSessionAdapter's round_result and read_session_history error envelopes.
for (const [name, result, expected] of [
  ['failed mechanic', { status: 'action_failed', action_id: 'action-failed', campaign_revision: 2,
    safe_payload: { error: 'PRIVATE ENGINE DETAIL' }, result: {}, current_entities: {} }, 'Failed'],
  ['committed mechanic', { status: 'action_committed', action_id: 'action-committed', campaign_revision: 3,
    safe_payload: {}, result: { success: false, description: 'PRIVATE GAME RESULT' },
    current_entities: { private_entity: { name: 'PRIVATE NAME' } } }, 'Committed'],
  ['invalid history read', { error: 'Invalid history request' }, 'Failed'],
  ['returned history data', { source_id: 'current', offset: 0, content: 'PRIVATE HISTORICAL DATA',
    total_characters: 23, next_offset: null }, 'Returned'],
  ['history source error', { error: 'Unknown source in this session; use source_id=list' }, 'Failed'],
  ['history search without a match', { found: false, source_id: 'current' }, 'Returned'],
  ['explicitly rejected call', { status: 'rejected', code: 'unavailable_catalog_tool',
    state_changed: false, requested_provider_tool_name: 'PRIVATE TOOL', available_operations: [] }, 'Rejected'],
]) test(`GM producer envelope: ${name} is ${expected}`, () => {
  const html = group([input, { message_id: 'producer-result', kind: 'evidence', content: {
    kind: 'provider_tool_results', inference_id: 'inference-real-shaped', activity,
    results: [{ role: 'tool', tool_call_id: 'call-real-shaped',
      name: name.includes('history') ? 'read_session_history' : 'tool_123456789abc_resolve_action',
      content: JSON.stringify(result) }],
  } }], 'dm');
  assert.match(html, new RegExp(`<span>${expected}<\\/span>`));
  assert.doesNotMatch(html, /PRIVATE|Invalid history request|Response complete/);
  if (expected !== 'Returned') assert.doesNotMatch(html, /<span>Returned<\/span>/);
});

test('GM: an unfinished tool call remains visible while waiting for its result', () => {
  const html = group([input, { message_id: 'call', kind: 'evidence', content: {
    kind: 'provider_tool_calls', activity,
    assistant: { tool_calls: [{ id: 'read', function: { name: 'read_campaign_reference',
      arguments: '{"private":"SECRET SOURCE"}' } }] },
  } }], 'dm', { attemptId: 'a', phase: 'working' });
  assert.match(html, /class="agent-activity"/);
  assert.match(html, /Read campaign data/);
  assert.doesNotMatch(html, /SECRET SOURCE|<span>Done<\/span>/);
});

test('only owner timing reaches the row and elapsed time stays outside the live announcement', () => {
  const app = sandbox.window.SylviaGMFrontend;
  const live = { attemptId: 'a', phase: 'working' };
  const observed = { attempt_id: 'a', basis_message_id: 'input', phase: 'provider',
    elapsed_ms: 65000, quiet_ms: 12000, operation: null, last_operation: 'read' };
  const html = app.groupedAgentActivity([input], ['USER'], { activity_progress: observed }, 'dm', live).join('');
  assert.match(html, /Waiting for model/);
  assert.match(html, /data-activity-time>1m 05s/);
  assert.match(html, /No new activity for 12s\. Last observed: Reading campaign data/);
  assert.doesNotMatch(html, /\b(stall|estimated|ETA)\b|%|<span[^>]*role="status"[^>]*>[^<]*1m/i);
  const stale = app.groupedAgentActivity([input], ['USER'], {
    activity_progress: { ...observed, attempt_id: 'old' },
  }, 'dm', live).join('');
  assert.doesNotMatch(stale, /1m 05s|No new activity|Waiting for model/);
  const firstTick = app.groupedAgentActivity([input], ['USER'], {
    activity_progress: { ...observed, quiet_ms: 500 },
  }, 'dm', live).join('');
  assert.doesNotMatch(firstTick, /No new activity for 0s/);
});

test('an old completed provider reasoning record cannot complete an unfinished attempt', () => {
  const html = sandbox.window.SylviaGMFrontend.groupedAgentActivity([input], ['USER'], {
    thinking: [{ attempt_id: 'a', response_id: 'r', basis_message_id: 'input',
      source_order: 1, status: 'completed', text: 'PRIVATE REASONING' }],
  }, 'dm').join('');
  assert.match(html, /Recorded/);
  assert.doesNotMatch(html, /View activity|>Completed<|PRIVATE REASONING/);
  const unsaved = sandbox.window.SylviaGMFrontend.groupedAgentActivity([input], ['USER'], {
    activity_progress: { attempt_id: 'a', phase: 'completed', elapsed_ms: 1000 },
  }, 'dm', { attemptId: 'a', phase: 'working' }).join('');
  assert.doesNotMatch(unsaved, /View activity|>Completed</, 'only a durable terminal completes the row');
});

test('tool continuation call IDs remain distinct and a final failure preserves incomplete calls', () => {
  const messages = [input];
  for (const inference_id of ['one', 'two']) {
    messages.push({ message_id: inference_id, kind: 'evidence', content: {
      kind: 'provider_tool_calls', inference_id, activity,
      assistant: { tool_calls: [{ id: 'reused', function: { name: 'read_file' } }] },
    } });
    if (inference_id === 'one') messages.push({ message_id: 'result', kind: 'evidence', content: {
      kind: 'provider_tool_results', inference_id, activity,
      results: [{ tool_call_id: 'reused', name: 'read_file', content: '{"success":true}' }],
    } });
  }
  messages.push({ message_id: 'failed', kind: 'evidence', content: {
    kind: 'session_attempt_terminal', attempt_id: 'a', status: 'interrupted',
    failure_category: 'provider_unavailable', activity_timing: { phase: 'failed', elapsed_ms: 80000 },
  } });
  const html = group(messages, 'dm');
  assert.equal((html.match(/class="agent-operation"/g) || []).length, 2);
  assert.equal((html.match(/<span>Done<\/span>/g) || []).length, 1);
  assert.match(html, /<span>Incomplete<\/span>/);
  assert.match(html, />Failed<|>Failed<\/span>/);
  assert.match(html, /data-activity-time>1m 20s/);
});

test('manual Retry retains two ordered attempts around their original input without changing result cards', () => {
  const messages = [input,
    { message_id: 'read', kind: 'evidence', content: { tool_name: 'read_file', result: { success: true }, activity } },
    { message_id: 'fail', kind: 'evidence', content: { kind: 'session_attempt_terminal', attempt_id: 'a', status: 'interrupted' } },
    { message_id: 'retry', kind: 'evidence', content: { kind: 'gm_turn_activity', attempt_id: 'retry-a', basis_message_id: 'input' } },
    { message_id: 'talk', kind: 'talk', content: { text: 'Meaningful output' } }];
  const html = sandbox.window.SylviaGMFrontend.groupedAgentActivity(messages,
    ['USER', '', 'RECOVERY CONTROL', '', 'MEANINGFUL OUTPUT'], {}, 'dm').join('');
  assert.equal((html.match(/class="agent-activity"/g) || []).length, 2);
  assert(html.indexOf('activity-a"') < html.indexOf('activity-retry-a"'));
  assert(html.includes('RECOVERY CONTROL') && html.endsWith('MEANINGFUL OUTPUT'));
});

test('a failed Retry with no prior activity event stays beside its input after reopen', () => {
  const messages = [input,
    { message_id: 'earlier-output', kind: 'talk', content: { text: 'Earlier original output' } },
    { message_id: 'original-terminal', kind: 'evidence', content: {
      kind: 'session_attempt_terminal', attempt_id: 'a', message_id: 'input', status: 'interrupted',
    } }];
  const cards = ['USER', 'EARLIER ORIGINAL OUTPUT', 'ORIGINAL RECOVERY'];
  const app = sandbox.window.SylviaGMFrontend;
  const live = app.groupedAgentActivity(messages, cards, { activity_progress: {
    attempt_id: 'retry-empty', basis_message_id: 'input', phase: 'provider', elapsed_ms: 5000,
  } }, 'dm', { attemptId: 'retry-empty', phase: 'working' }).join('');
  assert(live.indexOf('data-chronology-key="activity-retry-empty"') < live.indexOf('EARLIER ORIGINAL OUTPUT'));
  const terminal = { message_id: 'retry-terminal', kind: 'evidence', content: {
    kind: 'session_attempt_terminal', attempt_id: 'retry-empty', message_id: 'input',
    status: 'interrupted', failure_category: 'transient',
    activity_timing: { phase: 'failed', elapsed_ms: 5000 },
  } };
  const reopened = app.groupedAgentActivity([...messages, terminal], [...cards, 'RETRY RECOVERY'],
    {}, 'dm').join('');
  assert(reopened.indexOf('data-chronology-key="activity-retry-empty"') < reopened.indexOf('EARLIER ORIGINAL OUTPUT'));
  assert.equal((reopened.match(/data-chronology-key="activity-retry-empty"/g) || []).length, 1);
  assert.match(reopened, /data-activity-time>5s/);
  assert(reopened.endsWith('RETRY RECOVERY'));
});

test('Builder details retain checks and saves while the publication result remains separate', () => {
  const messages = [input,
    { message_id: 'check', kind: 'evidence', content: { tool_name: 'check_participant_draft',
      result: { success: true }, activity } },
    { message_id: 'save', kind: 'evidence', content: { tool_name: 'publish_participant_artifact',
      result: { success: true }, activity: { ...activity, source_order: 2 } } },
    { message_id: 'done', kind: 'evidence', content: { kind: 'builder_attempt_terminal',
      attempt_id: 'a', status: 'completed' } }];
  const html = sandbox.window.SylviaGMFrontend.groupedAgentActivity(messages,
    ['USER', '', 'MEANINGFUL PUBLICATION RESULT', ''], {}, 'builder').join('');
  assert(html.indexOf('Check character') < html.indexOf('Save character'));
  assert(html.indexOf('MEANINGFUL PUBLICATION RESULT') > html.lastIndexOf('</details>'));
});

function syncHarness(reduced = false) {
  const environment = { window: { matchMedia: () => ({ matches: reduced }) },
    document: { addEventListener() {} } };
  let source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const insertion = source.lastIndexOf('  return {');
  source = source.slice(0, insertion) + 'window.syncActivityForTest = syncActivityRow;\n' + source.slice(insertion);
  vm.runInNewContext(source, environment);
  return environment.window.syncActivityForTest;
}

for (const reduced of [false, true]) test(`polling preserves summary/animation identity and honors reduced motion=${reduced}`, () => {
  let animations = 0;
  const status = { textContent: 'Waiting for model', animate() { animations++; },
    getAttribute() { return 'polite'; }, setAttribute() {} };
  const time = { textContent: '1s' };
  const summary = { firstChild: { nodeValue: 'GM activity' }, focused: true,
    querySelector: selector => selector === '[data-activity-status]' ? status : time };
  const motion = {};
  const current = { dataset: { activityPhase: 'provider' }, motion,
    querySelector: selector => selector === 'summary' ? summary : null };
  const nextSummary = { firstChild: { nodeValue: 'GM activity' }, querySelector: selector => ({
    textContent: selector === '[data-activity-status]' ? 'Reading campaign data' : '2s',
    getAttribute() { return 'polite'; },
  }) };
  const next = { dataset: { activityPhase: 'tool' },
    querySelector: selector => selector === 'summary' ? nextSummary : null };
  const sync = syncHarness(reduced);
  assert.equal(sync(current, next), true);
  assert.equal(current.querySelector('summary'), summary);
  assert.equal(current.motion, motion);
  assert(summary.focused);
  assert.equal(status.textContent, 'Reading campaign data');
  assert.equal(time.textContent, '2s');
  assert.equal(animations, reduced ? 0 : 1);
  sync(current, next);
  assert.equal(animations, reduced ? 0 : 1, 'unchanged status is not reanimated by elapsed polling');
});

test('retained activity status follows live announcements without replacing keyboard focus', () => {
  const status = { textContent: 'Recorded', attributes: { 'aria-live': 'off' },
    getAttribute(name) { return this.attributes[name]; },
    setAttribute(name, value) { this.attributes[name] = value; } };
  const time = { textContent: '1s' };
  const summary = { firstChild: { nodeValue: 'GM activity' }, focused: true,
    querySelector: selector => selector === '[data-activity-status]' ? status : time };
  const current = { dataset: { activityPhase: 'recorded' },
    querySelector: selector => selector === 'summary' ? summary : null };
  const projected = (phase, label, live) => ({ dataset: { activityPhase: phase },
    querySelector: selector => selector === 'summary' ? {
      firstChild: { nodeValue: 'GM activity' },
      querySelector: selected => selected === '[data-activity-status]' ? {
        textContent: label, getAttribute: name => name === 'aria-live' ? live : null,
      } : { textContent: '2s' },
    } : null });
  const sync = syncHarness(true);
  sync(current, projected('provider', 'Waiting for model', 'polite'));
  assert.equal(status.getAttribute('aria-live'), 'polite');
  assert.equal(status.textContent, 'Waiting for model');
  // Announcement mode must also change when the visible text is unchanged.
  sync(current, projected('recorded', 'Waiting for model', 'off'));
  assert.equal(status.getAttribute('aria-live'), 'off');
  assert.equal(current.querySelector('summary'), summary);
  assert(summary.focused);
  assert.deepEqual(Object.keys(status.attributes), ['aria-live']);
  assert.equal(time.textContent, '2s');
});
