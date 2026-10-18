const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function group(messages, cards, context, role) {
  const sandbox = { window: {}, document: { addEventListener() {} } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8'), sandbox);
  return sandbox.window.SylviaGMFrontend.groupedAgentActivity(messages, cards, context, role).join('');
}

test('GM activity labels identify current provider aliases for reads and campaign writes', () => {
  const names = [
    'tool_123456789abc_read_campaign_reference',
    'tool_abcdef123456_search_campaign_knowledge',
    'tool_987654321abc_update_campaign_file',
  ];
  const messages = [
    { message_id: 'input', kind: 'user', content: { text: 'Continue' } },
    { message_id: 'calls', kind: 'evidence', content: { kind: 'provider_tool_calls',
      assistant: { tool_calls: names.map((name, i) => ({ id: `call-${i}`,
        function: { name, arguments: '{"private":"PRIVATE ARGUMENTS"}' } })) },
      activity: { attempt_id: 'a', basis_message_id: 'input', source_order: 1 } } },
    { message_id: 'results', kind: 'evidence', content: { kind: 'provider_tool_results',
      results: names.map((name, i) => ({ tool_call_id: `call-${i}`, name,
        content: '{"status":"committed","private":"PRIVATE RESULT"}' })),
      activity: { attempt_id: 'a', basis_message_id: 'input', source_order: 2 } } },
  ];
  const html = group(messages, ['USER', '', ''], {}, 'dm');
  assert.equal((html.match(/Read campaign data/g) || []).length, 2);
  assert.match(html, /Write to database/);
  assert.doesNotMatch(html, /Resolve game action|PRIVATE|tool_123/);
});

test('GM activity can identify saved results when their call is outside the loaded history page', () => {
  const html = group([{ message_id: 'results', kind: 'evidence', content: {
    kind: 'provider_tool_results',
    results: [{ name: 'tool_123456789abc_read_campaign_reference', tool_call_id: 'earlier-call',
      content: '{"status":"completed"}' }],
    activity: { attempt_id: 'a', basis_message_id: 'earlier-input', source_order: 2 },
  } }], [''], {}, 'dm');
  assert.match(html, /Read campaign data/);
  assert.doesNotMatch(html, /Resolve game action/);
});

test('attached source receipts show completed ingestion without exposing source metadata', () => {
  const sandbox = { window: {}, document: { addEventListener() {} } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8'), sandbox);
  const card = sandbox.window.SylviaGMFrontend.toolResultMarkup;
  for (const sourceCount of [1, 2]) {
    const html = card({ tool_name: 'attached_local_sources', result: {
      status: 'ingested', source_count: sourceCount,
      sources: [{ workspace_path: 'PRIVATE SOURCE PATH', text: 'PRIVATE SOURCE TEXT' }],
      authoritative: false,
    } }, '');
    assert.match(html, new RegExp(`${sourceCount} local source${sourceCount === 1 ? '' : 's'} attached`));
    assert.doesNotMatch(html, /pending|No completed result|PRIVATE/);
  }
  assert.doesNotMatch(card({ tool_name: 'attached_local_sources', result: { status: 'rejected' } }, ''),
    /local sources? attached/);
});

test('saved failed operations render a terminal failure instead of pending or success', () => {
  const sandbox = { window: {}, document: { addEventListener() {} } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8'), sandbox);
  const card = sandbox.window.SylviaGMFrontend.toolResultMarkup;
  for (const tool_name of ['fpv_fragmentation_strike', 'write_file', 'test_ruleset_draft', 'publish_ruleset_candidate']) {
    for (const content of [
      { tool_name, result: { status: 'failed', code: 'tool_execution_failed', error: 'PRIVATE DETAIL' } },
      { tool_name, status: 'failed', result: { code: 'tool_execution_failed' } },
    ]) {
      const html = card(content, '');
      assert.match(html, /Sylvia could not complete this action. Try again/);
      assert.match(html, /inline-feedback/);
      assert.doesNotMatch(html, /Operation |result-card|<details/);
      assert.doesNotMatch(html, /Operation pending|Operation completed|File saved|Ruleset tests passed|Test details|PRIVATE DETAIL/);
    }
  }
});

test('participant correction stays visible and later success creates no generic receipt', () => {
  const sandbox = { window: {}, document: { addEventListener() {} } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8'), sandbox);
  const app = sandbox.window.SylviaGMFrontend;
  const failure = { tool_name: 'check_participant_draft', parameters: { authored_profile: 'PRIVATE INPUT' },
    result: { success: false, status: 'rejected', message: 'The Ruleset calculates these resources.',
      validation_errors: [{ path: 'entity_state.resources.second_wind', message: 'Remove the authored value.',
        guidance: 'Supply starting character inputs; the Ruleset computes resources.', actual: 'PRIVATE VALUE' }],
      source: 'PRIVATE SOURCE', reasoning: 'PRIVATE REASONING' },
  };
  const success = { tool_name: 'check_participant_draft', result: { success: true } };
  const html = app.groupedAgentActivity([
    { message_id: 'rejected', kind: 'evidence', content: failure },
    { message_id: 'accepted', kind: 'evidence', content: success },
  ], [app.toolResultMarkup(failure, ''), app.toolResultMarkup(success, '')], {}, 'builder').join('');
  assert.match(html, /entity_state.resources.second_wind: Remove the authored value/);
  assert.match(html, /Supply starting character inputs/);
  assert.match(html, /inline-feedback/);
  assert.doesNotMatch(html, /Operation |result-card|<summary>Details/);
  assert.equal(app.toolResultMarkup(success, ''), '');
  assert.doesNotMatch(html, /PRIVATE|was corrected|This operation did not complete/);
});

test('participant rejection escapes and bounds diagnostics and accepts schema issues', () => {
  const sandbox = { window: {}, document: { addEventListener() {} } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8'), sandbox);
  const card = sandbox.window.SylviaGMFrontend.toolResultMarkup;
  const html = card({ tool_name: 'check_participant_draft', result: { success: false,
    message: '<script>bad</script>', validation_errors: Array.from({ length: 10 }, (_, i) => ({
      path: `field-${i}`, message: 'x'.repeat(2000), guidance: '<fix>', actual: 'PRIVATE VALUE',
    })) } }, '');
  assert.match(html, /&lt;script&gt;bad&lt;\/script&gt;/);
  assert.match(html, /&lt;fix&gt;/);
  assert.match(html, /Further fields also need correction/);
  assert.doesNotMatch(html, /field-4|PRIVATE VALUE|<script>/);
  assert(html.length < 4000);
  const schema = card({ tool_name: 'check_participant_draft', result: { success: false,
    validation_issues: [{ path: 'participant.level', kind: 'wrong_type', expected_type: 'integer' }],
  } }, '');
  assert.match(schema, /participant.level: Wrong Type Expected integer/);
});

for (const role of ['builder', 'dm']) {
  test(`${role} groups ordered activity while results and interruption remain outside`, () => {
    const messages = [
      { message_id: 'input', kind: 'user', content: { text: 'Play' } },
      { message_id: 'read', kind: 'evidence', content: { tool_name: 'read_file',
        activity: { attempt_id: 'a', basis_message_id: 'input', source_order: 1 } } },
      { message_id: 'result', kind: 'evidence', content: { tool_name: 'publish_campaign_plan',
        result: { success: true },
        activity: { attempt_id: 'a', basis_message_id: 'input', source_order: 3 } } },
      { message_id: 'terminal', kind: 'evidence', content: {
        kind: role === 'builder' ? 'builder_attempt_terminal' : 'session_attempt_terminal',
        attempt_id: 'a', status: 'interrupted' } },
    ];
    const html = group(messages, ['USER', 'RAW READ RECEIPT', '<article>RESULT</article>', 'RETRY'], {
      thinking: [{ attempt_id: 'a', response_id: '<response>', basis_message_id: 'input',
        source_order: 2, status: 'interrupted', text: 'MUST NEVER APPEAR' }],
    }, role);
    assert.equal((html.match(/class="agent-activity"/g) || []).length, 1);
    assert(html.includes(role === 'builder' ? 'Builder activity' : 'GM activity'));
    assert(html.includes('Read campaign data'));
    assert(html.indexOf('Read campaign data') < html.indexOf('May contain spoilers'));
    assert(html.includes('data-thinking-response="&lt;response&gt;"'));
    assert(!html.includes('MUST NEVER APPEAR') && !html.includes('RAW READ RECEIPT'));
    assert(!html.includes('<details open'));
    assert(html.indexOf('<article>RESULT</article>') > html.lastIndexOf('</details>'));
    assert(html.endsWith('RETRY'));
  });
}

test('no supplied reasoning means no Thinking, and failed Builder work stays actionable', () => {
  const messages = [
    { message_id: 'input', kind: 'user', content: {} },
    { message_id: 'write', kind: 'evidence', content: { tool_name: 'write_file',
      result: { success: false }, activity: { attempt_id: 'a', basis_message_id: 'input', source_order: 0 } } },
    { message_id: 'done', kind: 'evidence', content: { kind: 'gm_turn_activity',
      attempt_id: 'a', basis_message_id: 'input' } },
  ];
  const html = group(messages, ['USER', 'ACTIONABLE ERROR', 'Turn resolved'], {}, 'dm');
  assert(!html.includes('Thinking') && !html.includes('Turn resolved'));
  assert(html.includes('ACTIONABLE ERROR'));
});

test('actual Builder reference tools remain inside the collapsed activity group', () => {
  const names = ['get_content_definition', 'inspect_builder_references', 'copy_builder_reference'];
  const messages = [{ message_id: 'input', kind: 'user', content: {} },
    ...names.map((tool_name, i) => ({ message_id: `tool-${i}`, kind: 'evidence', content: {
      tool_name, result: { success: true },
      activity: { attempt_id: 'a', basis_message_id: 'input', source_order: i },
    } })),
    { message_id: 'done', kind: 'evidence', content: {
      kind: 'builder_attempt_terminal', attempt_id: 'a', status: 'completed',
    } },
  ];
  const html = group(messages, ['USER', ...names.map(n => `RAW ${n}`), ''], {}, 'builder');
  assert.equal((html.match(/class="agent-activity"/g) || []).length, 1);
  assert(!html.includes('RAW '));
  assert.equal((html.match(/class="agent-operation"/g) || []).length, 3);
  assert(!html.includes('<details open'));
});

test('failed reference copies and meaningful result cards remain visible', () => {
  for (const result of [{ success: false }, { status: 'rejected' },
    { success: true, result_card: { title: 'Created artifact' } }]) {
    const messages = [{ message_id: 'input', kind: 'user', content: {} },
      { message_id: 'copy', kind: 'evidence', content: {
        tool_name: 'copy_builder_reference', result,
        activity: { attempt_id: 'a', basis_message_id: 'input', source_order: 0 },
      } }];
    assert(group(messages, ['USER', 'ACTIONABLE RESULT'], {}, 'builder')
      .includes('ACTIONABLE RESULT'));
  }
});
