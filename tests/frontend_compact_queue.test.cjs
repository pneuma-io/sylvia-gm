const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=process.env.SYLVIA_QA_SOURCE||path.join(__dirname,'..');
function harness(queue){
  const context={window:{},document:{addEventListener(){}}};
  const source=fs.readFileSync(path.join(root,'src-tauri/frontend/app.js'),'utf8')
    .replace('  // ---- Public API ----','window.compactQueueQA={state,sequenceMarkup};\n  // ---- Public API ----');
  vm.runInNewContext(source,context);
  const api=context.window.compactQueueQA;
  Object.assign(api.state,{activeSessionId:'gm',activeSessionRole:'dm',view:'session',
    bridge:{compose_action_sequence(){}},actionSequence:queue});
  return api;
}
const queue=steps=>({session_id:'gm',status:'ready',steps,committed_action_ids:[]});
const step=(id,label,extra={})=>({step_id:id,label,status:'pending',...extra});

test('empty queue has a count and one disabled Execute without action selection controls',()=>{
  const html=harness(null).sequenceMarkup();
  assert.match(html,/aria-label="0 queued actions">0</);
  assert.equal((html.match(/data-sequence-execute/g)||[]).length,1);
  assert.match(html,/<button[^>]*data-sequence-execute[^>]*disabled[^>]*>Execute<\/button>/);
  assert.doesNotMatch(html,/data-sequence-run|<select|End Turn|data-panel-action/);
  assert.doesNotMatch(html,/Choose actions|sequence-empty|Commit all|Sylvia narrates/);
  assert.match(html,/<ol class="sequence-cards" aria-label="Actions in execution order" ><\/ol>/);
});
test('wide cards retain declared order and count only uncommitted intentions',()=>{
  const value=queue([step('a','Move',{status:'committed'}),step('b','Longsword'),step('c','Guard')]);
  const before=JSON.stringify(value),html=harness(value).sequenceMarkup();
  assert.match(html,/aria-label="2 queued actions">2</);
  assert.ok(html.indexOf('data-sequence-step="a"')<html.indexOf('data-sequence-step="b"'));
  assert.ok(html.indexOf('data-sequence-step="b"')<html.indexOf('data-sequence-step="c"'));
  assert.equal((html.match(/data-sequence-run/g)||[]).length,1);
  assert.equal((html.match(/data-sequence-remove=/g)||[]).length,2);
  assert.equal(JSON.stringify(value),before);
});
test('target and cost share one short secondary row; target label comes from current panels',()=>{
  const api=harness(queue([step('a','Longsword',{selection:{target:'guard-1'},
    preview:{summary:[{label:'Action cost',value:'1 action'},{label:'Unrelated detail',value:'not needed'}]}})]));
  api.state.panelProjection={surfaces:[{data:{entities:[{id:'guard-1',display:{label:'Guardian'}}]}}]};
  const html=api.sequenceMarkup();
  assert.match(html,/Target: Guardian · Action cost: 1 action/);
  assert.equal((html.match(/class="sequence-detail"/g)||[]).length,1);
  assert.doesNotMatch(html,/Unrelated detail|not needed/);
});
test('failed suffix keeps an accessible compact explanation and existing review control',()=>{
  const value=queue([step('done','Move',{status:'committed'}),step('later','Strike',{error:'The action is already spent.'})]);
  value.status='failed';value.error='The action is already spent.';
  const html=harness(value).sequenceMarkup();
  assert.equal(html.split('The action is already spent.').length-1,1);
  assert.match(html,/<details class="sequence-issue"><summary>Review this action<\/summary>/);
  assert.doesNotMatch(html,/<details[^>]* open/);
  assert.match(html,/data-sequence-reconfirm/);
  assert.doesNotMatch(html,/data-sequence-run/);
});
test('a reaction blocks Execute and keeps its exact saved decision available',()=>{
  const value=queue([step('a','Move',{status:'awaiting_choice'})]);
  value.status='paused';value.pause_summary='Movement Reaction decision: choose whether to react.';
  const html=harness(value).sequenceMarkup();
  assert.match(html,/Awaiting choice/);
  assert.match(html,/Resolve the decision to continue/);
  assert.match(html,/Movement Reaction decision: choose whether to react\./);
  assert.doesNotMatch(html,/data-sequence-run/);
});
test('only newly queued cards request entrance motion on a panel refresh',()=>{
  const api=harness(queue([step('a','Move'),step('b','Strike')]));
  assert.equal((api.sequenceMarkup(new Set(['a'])).match(/data-sequence-enter/g)||[]).length,1);
  assert.equal((api.sequenceMarkup(new Set(['a','b'])).match(/data-sequence-enter/g)||[]).length,0);
});
test('generic ruleset costs and labels render without a D&D-specific adapter',()=>{
  const value=queue([step('a','Hack terminal',{actor:'pilot',actor_name:'Pilot',selection:{target:'Relay'},
    preview:{summary:[{label:'Energy cost',value:'2 charges'}]}}),step('b','Hold course',{actor:'navigator',actor_name:'Navigator'})]);
  const html=harness(value).sequenceMarkup();
  assert.match(html,/Hack terminal/);assert.match(html,/Pilot · Target: Relay · Energy cost: 2 charges/);
  assert.doesNotMatch(html,/spell slot|sylvia\.srd/);
});
test('untrusted labels and targets are escaped in card text and accessible removal names',()=>{
  const html=harness(queue([step('a','<img src=x onerror=bad>',{selection:{target:'"<script>bad</script>'}})])).sequenceMarkup();
  assert.doesNotMatch(html,/<img|<script|onerror="bad"/);
  assert.match(html,/aria-label="Remove &lt;img/);
  assert.match(html,/Target: &quot;&lt;script&gt;/);
});

test('single-actor cards reserve their secondary line for target and declared distance',()=>{
  const api=harness(queue([step('a','Strike',{actor:'pilot',actor_name:'Pilot',selection:{target:'Relay'},
    preview:{summary:[{label:'Distance',value:'5 ft'}]}})]));
  const html=api.sequenceMarkup();
  assert.match(html,/title="Pilot: Strike"/);
  assert.match(html,/Target: Relay · Distance: 5 ft/);
  assert.doesNotMatch(html,/Pilot · Target/);
  assert.match(html,/aria-label="Step 1: Pending"/);
});
