const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function app() {
  const sandbox = { window: {}, document: { addEventListener() {} } };
  const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8')
    .replace('    toolResultMarkup,', '    toolResultMarkup, renderProjection,');
  vm.runInNewContext(source, sandbox);
  return sandbox.window.SylviaGMFrontend;
}

test('historical thinking uses the current disclosure without old boxes or a text preview', () => {
  const projection = { kind: 'think', sequence: 7, attempt_id: 'saved-attempt',
    text: 'Recorded <private> reasoning', collapsed_by_default: false, safe_payload: {} };
  const before = JSON.stringify(projection);
  const html = app().renderProjection(projection);
  assert.match(html, /<details class="agent-thinking"/);
  assert.match(html, /<summary>Thinking <span class="thinking-warning">May contain spoilers<\/span><\/summary>/);
  assert.match(html, /<div class="thinking-body">Recorded &lt;private&gt; reasoning<\/div>/);
  assert.doesNotMatch(html, /story-card|story-collapse|collapse-hint|<private>|\sopen[\s>]/);
  assert.equal(JSON.stringify(projection), before);
  assert.equal(app().renderProjection(JSON.parse(before)), html);
});

function currentPresentation(html) {
  assert.match(html, /class="story-card result-card result-card-(inline|compact)"/);
  assert.doesNotMatch(html, /class="[^"]*(?:tool-card|skill-check-|story-collapse|collapse-hint)/);
}

test('generic committed and pending receipts are absent without mutating saved evidence', () => {
  const api = app();
  for (const tool_name of ['advance', 'resolve_combat_attack', 'spend_srd_resource', 'cast_spell', 'custom_result']) {
    for (const status of ['committed', 'completed', 'success', 'pending']) {
      for (const presentation of [{}, { result_card: null }]) {
        const content = { tool_name, result: { status, context: { durable_state: 'KEPT' }, ...presentation } };
        const before = JSON.stringify(content);
        assert.equal(api.toolResultMarkup(content, ''), '');
        const projection = { kind: 'tool_card', sequence: 1, action_id: 'saved-action', safe_payload: content };
        assert.equal(api.renderProjection(projection), '');
        assert.equal(api.renderProjection(JSON.parse(JSON.stringify(projection))), '');
        assert.equal(JSON.stringify(content), before);
      }
    }
  }
});

test('failed, rejected and invalid saved results retain visible feedback without generic receipt chrome', () => {
  const render = app().toolResultMarkup;
  for (const status of ['failed', 'error', 'rejected']) {
    const content = { tool_name: 'advance', result: { status, error: 'PRIVATE DETAIL' } };
    const before = JSON.stringify(content);
    const html = render(content, ' data-chronology-key="saved-failure"');
    assert.match(html, /class="inline-feedback" role="status"/);
    assert.match(html, /Try again|try again/);
    assert.doesNotMatch(html, /Operation |result-card|Details|PRIVATE DETAIL|Hit|Victory/);
    assert.equal(JSON.stringify(content), before);
  }
  const html = render({ tool_name: 'advance', result: { status: 'committed', result_card: { version: 999 } } }, '');
  assert.match(html, /The saved Ruleset result cannot be displayed/);
  assert.doesNotMatch(html, /Operation |result-card|Details/);
});

test('application guidance is readable without a generic Details disclosure', () => {
  const html = app().toolResultMarkup({ tool_name: 'read_file', result: { status: 'completed', path: 'plan.md' } }, '');
  assert.match(html, /File reviewed/);
  assert.match(html, /Source text is omitted from ordinary chat history for privacy/);
  assert.doesNotMatch(html, /<details|Operation /);
});

test('Builder reference failures explain source access instead of rejecting character choices', () => {
  const render = app().toolResultMarkup;
  for(const tool_name of ['inspect_builder_references','copy_builder_reference']){
    const content={tool_name,result:{success:false,code:tool_name==='inspect_builder_references'?'reference_unavailable':'reference_copy_unavailable',
      reason:'package_file_oversized',error:'PRIVATE PATH AND SOURCE'}};
    const before=JSON.stringify(content);
    const html=render(content,'');
    assert.match(html,/selected (reference|source)|source file/i);
    assert.match(html,/saved (work|draft)|draft.*kept/i);
    assert.doesNotMatch(html,/unavailable with these choices|PRIVATE PATH AND SOURCE/);
    assert.equal(JSON.stringify(content),before);
  }
});

test('old saved checks retain recorded values in the shared card, without a legacy renderer or arithmetic', () => {
  const content = { tool_name: 'd20_test', parameters: { skill: 'survival', target_number: 14 },
    result: { context: { _outcome: 'failure', _adjusted_total: 99,
      _d20_test: { results: [2, 4], total: 4, modifier: 3 },
      _effective_advantage: true, _effective_disadvantage: false,
      actor: { data: { name: 'Mira' } } } } };
  const before = JSON.stringify(content);
  const html = app().toolResultMarkup(content, ' data-chronology-key="old-check"');
  currentPresentation(html);
  for (const text of ['Survival check', 'Failure', 'Mira', '99', '2, 4', 'Advantage']) {
    assert(html.includes(text), text);
  }
  assert.doesNotMatch(html, /result-equation/);
  assert.equal(JSON.stringify(content), before);
  assert.equal(app().toolResultMarkup(JSON.parse(before), ' data-chronology-key="old-check"'), html);
});

test('Builder publication retains preview and exact plan actions in the new compact presentation', () => {
  const html = app().toolResultMarkup({ tool_name: 'publish_campaign_plan', result: {
    success: true, title: 'The relay', readable_preview: '<script>unsafe</script>',
    library_artifact: { id: 'saved-plan' },
  } }, '');
  currentPresentation(html);
  assert.match(html, /data-builder-result-action="preview_campaign_plan"/);
  assert.match(html, /data-builder-result-action="use_campaign_plan"/);
  assert.match(html, /data-campaign-plan-id="saved-plan"/);
  assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

test('declared non-dice outcomes remain visible outside collapsed routine activity', () => {
  const api = app();
  const content = { tool_name: 'write_file', activity: { attempt_id: 'attempt', basis_message_id: 'input' },
    result: { status: 'committed', result_card: { version: 1, title: 'Power restored', footprint: 'inline',
      summary: [{ value: 'Relay online', emphasis: true }], details: [], actions: [] } } };
  const card = api.toolResultMarkup(content, '');
  currentPresentation(card);
  const html = api.groupedAgentActivity([
    { message_id: 'input', kind: 'user', content: { text: 'Repair it' } },
    { message_id: 'result', kind: 'evidence', content },
  ], ['USER', card], {}, 'builder').join('');
  assert.match(html, /Power restored/);
  assert.match(html, /Relay online/);
  assert.doesNotMatch(html, /D20|Operation completed/);
});

test('saved routine package results use one escaped activity entry and keep meaningful or failed results', () => {
  const api = app();
  for (const change of [{}, { result_card: { version: 1, title: 'Saving throw',
    summary: [], details: [], actions: [] } }, { status: 'failed' }, { success: false },
    { result_card: { version: 999 } }]) {
    const result = { status: 'committed', activity_label: 'Prepare <creature>', ...change };
    const content = { tool_name: 'custom_lifecycle', action_id: 'act', status: 'committed',
      activity: { attempt_id: 'attempt', basis_message_id: 'input' }, result };
    const html = api.groupedAgentActivity([
      { message_id: 'input', kind: 'user', content: { text: 'Continue' } },
      { message_id: 'receipt', kind: 'evidence', content },
      { message_id: 'provider', kind: 'evidence', content: {
        kind: 'provider_tool_results', activity: content.activity,
        results: [{ name: 'custom_lifecycle', content: JSON.stringify({ action_id: 'act', result }) }],
      } },
    ], ['USER', api.toolResultMarkup(content, ''), ''], {}, 'dm').join('');
    assert.doesNotMatch(html, /<creature>/);
    if (!Object.keys(change).length) {
      assert.equal((html.match(/Prepare &lt;creature&gt;/g) || []).length, 1);
      assert.doesNotMatch(html, /Operation completed|Resolve game action|result-card/);
    } else if (change.result_card?.version === 1) {
      assert.match(html, /result-card/);
    } else {
      assert.match(html, /inline-feedback/);
      assert.doesNotMatch(html, /Operation |result-card|<summary>Details/);
    }
  }
});
