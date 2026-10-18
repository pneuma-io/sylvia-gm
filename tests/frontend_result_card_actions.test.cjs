// Exercise saved result-card controls against the real frontend module.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { webcrypto } = require('node:crypto');

function element() {
  const listeners = new Map();
  return {
    listeners, dataset: {}, children: [], innerHTML: '', disabled: false, hidden: false,
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener(name, handler) { listeners.set(name, handler); },
    removeEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
    appendChild(child) { child.parentElement = this; this.children.push(child); },
    remove() { this.parentElement.children = this.parentElement.children.filter((x) => x !== this); },
    showModal() { this.open = true; },
    close(value) { this.returnValue = value; this.open = false; listeners.get('close')(); },
  };
}

function harness({ confirmationRequired = true } = {}) {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  const body = element();
  const context = {
    window: {}, crypto: webcrypto, console,
    document: { body, getElementById: node, addEventListener() {}, createElement: element },
    localStorage: { getItem: () => null, setItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    URL,
  };
  let source = fs.readFileSync(
    path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8'
  );
  const insertion = source.lastIndexOf('  return {');
  source = source.slice(0, insertion) + `
    window.resultCardTest = { state, ensureSessionActivity, wireResultCardActions,
      rulesetCardMarkup, toolPresentation };
    refreshSelectedCampaign = async () => {};
    renderPlayerProjection = async () => {};
  ` + source.slice(insertion);
  source = ['panel-workspace.js', 'shot-playback.js', 'panel-host.js'].map((name) =>
    fs.readFileSync(path.join(__dirname, '../src-tauri/frontend', name), 'utf8')).join('\n') + '\n' + source;
  context.setTimeout ||= setTimeout;
  context.clearTimeout ||= clearTimeout;
  context.window.innerWidth = 1400;
  vm.runInNewContext(source, context);
  const api = context.window.resultCardTest;
  Object.assign(api.state, {
    activeCampaignId: 'campaign', activeSessionId: 'gm', activeSessionRole: 'dm',
    activeSnapshot: { revision: 1 }, view: 'session', campaigns: [],
  });
  const activity = api.ensureSessionActivity({
    sessionId: 'gm', sessionRole: 'dm', campaignId: 'campaign',
  });
  const previews = [];
  const executions = [];
  api.state.bridge = {
    snapshot: async () => ({ revision: 12 }),
    preview_player_action: async (command) => {
      previews.push(command);
      return {
        confirmation_required: confirmationRequired,
        confirmation: 'bound-confirmation', label: 'Take cover', selection: null, preview: null,
      };
    },
    execute_player_action: async (command) => {
      executions.push(command);
      return { status: 'committed' };
    },
  };
  const button = element();
  button.dataset.resultCardSourceActionId = 'saved-action-4';
  button.dataset.resultCardButtonIndex = '1';
  const container = element();
  container.querySelectorAll = (selector) =>
    selector === '[data-result-card-button-index]' ? [button] : [];
  api.wireResultCardActions(container);
  return {
    api, activity, body, button, previews, executions,
    click: () => button.listeners.get('click')(),
  };
}

test('cancel preserves the exact saved-source request without execution', async () => {
  const h = harness();
  const pending = h.click();
  await new Promise(setImmediate);
  assert.deepEqual(JSON.parse(JSON.stringify(h.previews[0].source)), {
    kind: 'result_card', action_id: 'saved-action-4', button_index: 1,
  });
  assert.equal(h.previews[0].selection, null);
  assert.equal(h.previews[0].expected_revision, 12);
  assert.equal(h.previews[0].session_id, 'gm');
  h.body.children[0].close('cancel');
  await pending;
  assert.equal(h.executions.length, 0);
});

test('confirmation executes the same request and owner confirmation', async () => {
  const h = harness();
  const pending = h.click();
  await new Promise(setImmediate);
  h.body.children[0].close('confirm');
  await pending;
  assert.equal(h.executions.length, 1);
  assert.equal(h.executions[0].operation_id, h.previews[0].operation_id);
  assert.equal(h.executions[0].expected_revision, 12);
  assert.equal(h.executions[0].confirmation, 'bound-confirmation');
  assert.deepEqual(JSON.parse(JSON.stringify(h.executions[0].source)), {
    kind: 'result_card', action_id: 'saved-action-4', button_index: 1,
  });
});

test('session navigation during confirmation fences execution', async () => {
  const h = harness();
  const pending = h.click();
  await new Promise(setImmediate);
  h.api.state.activeSessionId = 'other-session';
  h.body.children[0].close('confirm');
  await pending;
  assert.equal(h.executions.length, 0);
});

test('working sessions and false or missing current availability disable card actions', () => {
  const h = harness();
  const card = {
    version: 1, summary: [],
    details: [{ label: 'Roll <detail>', value: '<kept>', emphasis: false }],
    actions: [{ label: '<Act>', tool: 'ignored', parameters: { secret: 'ignored' } }],
  };
  const available = h.api.rulesetCardMarkup(card, '', 'saved-action', [true]);
  assert.match(available, />&lt;Act&gt;<\/button>/);
  assert.match(available, /<summary>Details<\/summary>/);
  assert.match(available, /Roll &lt;detail&gt;/);
  assert.match(available, /&lt;kept&gt;/);
  assert.doesNotMatch(available, /data-result-card-tool|data-result-card-parameters| disabled/);
  assert.match(h.api.rulesetCardMarkup(card, '', 'saved-action', [false]), /disabled/);
  const withReason = h.api.rulesetCardMarkup(card, '', 'saved-action', [false], ['AP < 1']);
  assert.match(withReason, /disabled title="AP &lt; 1" aria-description="AP &lt; 1"/);
  assert.match(h.api.rulesetCardMarkup(card, ''), /disabled/);
  h.activity.phase = 'working';
  assert.match(h.api.rulesetCardMarkup(card, '', 'saved-action', [true]), /disabled/);
});

test('a card click made unavailable by active GM work does not reach the bridge', async () => {
  const h = harness({ confirmationRequired: false });
  h.activity.phase = 'working';
  await h.click();
  assert.equal(h.previews.length, 0);
  assert.equal(h.executions.length, 0);
});


test('saved sentence values do not repeat their own result label',()=>{
  const h = harness();
  const card = {version:1,title:'Attack',summary:[
    {label:'Outcome',value:'Hit',emphasis:false},
    {label:'Damage',value:'Damage 10.',emphasis:false}],details:[],actions:[]};
  const before = JSON.stringify(card);
  const html = h.api.rulesetCardMarkup(card,'');
  assert.equal((html.match(/Damage/g)||[]).length,1,'Saved Damage 10. already supplies its label');
  assert.match(html,/>Damage 10\.<\/span>/);
  assert.match(html,/result-card-label">Outcome<\/span>/);
  assert.equal(JSON.stringify(card),before,'The saved card remains immutable');
});

test('result labels remain for numeric values, distinct text and recorded equations',()=>{
  const h = harness();
  const html = h.api.rulesetCardMarkup({version:1,summary:[
    {label:'Damage',value:10,emphasis:false},
    {label:'Outcome',value:'Hit',emphasis:false},
    {label:'Concentration',value:'success',emphasis:false,
      equation:{die_sides:20,roll:4,modifier:6,total:10}}],details:[],actions:[]},'');
  assert.match(html,/result-card-label">Damage<\/span>/);
  assert.match(html,/result-card-label">Outcome<\/span>/);
  assert.match(html,/result-card-label">Concentration<\/span>/);
  assert.match(html,/D20 roll: 4/);
  assert.match(html,/result-modifier">\+6</);
  assert.match(html,/result-total">10</);
});

test('the actual saved-card listener queues its source identity without a per-selection narration', async()=>{
  const h=harness();const composed=[];
  Object.assign(h.api.state.bridge,{action_sequence:async()=>null,
    compose_action_sequence:async command=>{composed.push(command);return {...command,version:1,status:'ready',steps:command.steps};}});
  await h.click();
  assert.equal(composed.length,1);assert.equal(h.previews.length,0);assert.equal(h.executions.length,0);
  assert.deepEqual(JSON.parse(JSON.stringify(composed[0].steps[0].source)),
    {kind:'result_card',action_id:'saved-action-4',button_index:1});
  assert.equal(composed[0].steps[0].selection,null);
});

test('native profile validation renders saved field/correction guidance without source values',()=>{
  const h=harness();const presentation=h.api.toolPresentation({tool_name:'check_character_profile_draft',result:{
    success:false,validation_errors:[{path:'$.assets[0].uri',message:'Invalid asset digest.',
      correction:'Use exactly 64 lowercase hexadecimal digits.',submitted_value:'PRIVATE-SOURCE'}],
    raw_source:'PRIVATE-SOURCE',tool_arguments:{secret:'PRIVATE-SOURCE'}}});
  assert.equal(presentation.feedback,true);
  assert.match(presentation.summary,/\$\.assets\[0\]\.uri: Invalid asset digest\./);
  assert.match(presentation.summary,/64 lowercase hexadecimal digits/);
  assert.doesNotMatch(JSON.stringify(presentation),/PRIVATE-SOURCE|tool_arguments|raw_source/);
});
