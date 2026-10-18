const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');

test('real saved choice commitment removes the generic receipt and retains reopened state and narration', () => {
  const database = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'sylvia-choice-receipt-')), 'authority.sqlite3');
  const fixture = path.join(__dirname, 'frontend_saved_choice_fixture.py');
  const invoke = (mode, command) => {
    const child = spawnSync(process.env.SYLVIA_TEST_PYTHON || 'python', [fixture, mode, database], {
      input: command ? JSON.stringify(command) : undefined, encoding: 'utf8', windowsHide: true,
      env: {...process.env, PYTHONPATH: [path.resolve(__dirname, '../src'), path.resolve(__dirname, '..')].join(path.delimiter)},
    });
    assert.equal(child.status, 0, child.stderr || child.error?.message);
    return JSON.parse(child.stdout);
  };
  const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const sandbox = {window: {}, document: {addEventListener() {}}};
  vm.runInNewContext(source.replace('    toolResultMarkup,', '    toolResultMarkup, renderProjection,'), sandbox);
  const api = sandbox.window.SylviaGMFrontend;
  const pending = invoke('init');
  const savedChoice = pending.projections.find(p => p.kind === 'mechanics_choice');
  assert(savedChoice);
  const pendingHTML = pending.projections.map(p => api.renderProjection(p)).join('');
  assert.match(pendingHTML, /Ground the signal/);
  assert.match(pendingHTML, /The relay hums/);
  const choice = savedChoice.safe_payload;
  const button = {dataset: {actionId: choice.action_id, basisRevision: String(choice.basis_revision),
    choiceId: choice.choice_id, optionId: 'ground', choiceSequence: String(choice.choice_sequence || 0)}, disabled: false};
  let command, committed, currentHTML, narrated;
  const container = {querySelectorAll: () => [button]};
  const context = {
    state: {activeCampaignId: 'campaign-runtime', activeSessionId: 'local-seeded-narration', bridge: {
      resolve_mechanics_choice: async value => { command = value; committed = invoke('resolve', value); return committed; },
    }},
    replaceClickHandler: (node, handler) => { node.click = handler; },
    renderPlayerProjection: async () => { currentHTML = committed.projections.map(p => api.renderProjection(p)).join(''); },
    respondToPlayerAction: async (...args) => { assert.deepEqual(args, ['campaign-runtime', 'local-seeded-narration', choice.action_id]); narrated = invoke('narrate'); },
  };
  const start = source.indexOf('  function wireMechanicsChoiceControls(');
  const end = source.indexOf('  function renderProjection(', start);
  vm.runInNewContext(source.slice(start, end), context);
  context.wireMechanicsChoiceControls(container);
  return button.click().then(() => {
    assert.equal(committed.status, 'committed');
    assert.equal(committed.revision, 1);
    assert.equal(committed.action_count, 1);
    assert.match(currentHTML, /The relay hums/);
    assert.doesNotMatch(currentHTML, /mechanics-choice-card|Operation |Advance|<summary>Details|tool-card/);
    const receipt = committed.projections.find(p => p.kind === 'tool_card');
    assert(receipt, 'Committed evidence must remain persisted even though the generic receipt is silent.');
    assert.equal(api.renderProjection(receipt), '');
    assert.equal(invoke('resolve', command).action_count, 1, 'Retry must not recommit.');
    const reopened = invoke('reopen');
    assert.deepEqual(reopened, narrated);
    const html = reopened.projections.map(p => api.renderProjection(p)).join('');
    assert.match(html, /The relay hums/);
    assert.match(html, /The grounded relay steadies/);
    assert.doesNotMatch(html, /Operation |Advance|<summary>Details|mechanics-choice-card|tool-card/);
    const target = process.env.SYLVIA_RECEIPT_EVIDENCE;
    if (target) fs.writeFileSync(target, JSON.stringify({layer: 'source frontend VM + real admitted SQLite authority; seeded local narration; no browser/native/provider',
      database, command, pending, committed, reopened, committed_html: currentHTML, reopened_html: html,
      status: 'PASS'}, null, 2));
  });
});
