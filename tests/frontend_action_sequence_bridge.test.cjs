// The main-window transport must expose the executable queue's real Tauri adapters.
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
test('main Tauri bridge forwards inspected queue identities and exact confirmed execution',async()=>{
  const calls=[];
  const context={window:{__TAURI__:{core:{invoke:async(name,args)=>{
    calls.push({name,args});return {accepted:true};
  }}}},document:{addEventListener(){}}};
  let source=fs.readFileSync(path.join(__dirname,'../src-tauri/frontend/app.js'),'utf8');
  source=source.replace('  // ---- Public API ----','window.bridgeQA={createHostBridge,panelWindowBridge};\n  // ---- Public API ----');
  vm.runInNewContext(source,context);
  const bridge=context.window.bridgeQA.createHostBridge();
  assert.equal(typeof bridge.compose_action_sequence,'function');
  assert.equal(typeof bridge.execute_action_sequence,'function');
  await bridge.action_sequence({session_id:'gm',sequence_id:'queue'});
  const composition={campaign_id:'campaign',session_id:'gm',sequence_id:'queue',expected_version:0,
    expected_revision:7,steps:[{step_id:'first',source:{kind:'panel',surface_id:'map',action_id:'move'},
      selection:{actor:'hero',destination:[0,5,0],map_revision:7}}]};
  await bridge.compose_action_sequence(composition);
  const execution={campaign_id:'campaign',session_id:'gm',sequence_id:'queue',expected_version:1,
    confirmation:'exact-confirmation'};
  await bridge.execute_action_sequence(execution);
  assert.deepEqual(JSON.parse(JSON.stringify(calls)),[
    {name:'action_sequence',args:{sessionId:'gm',sequenceId:'queue'}},
    {name:'compose_action_sequence',args:{command:composition}},
    {name:'execute_action_sequence',args:{command:execution}}]);
  const child=context.window.bridgeQA.panelWindowBridge();
  assert.equal(child.compose_action_sequence,undefined,'Queue remains in its owning main window');
});

test('Builder panels never read campaign queue authority or retain an enabled queue mode',async()=>{
  const context={window:{},document:{addEventListener(){},getElementById:()=>({textContent:'',hidden:false})}};
  let source=fs.readFileSync(path.join(__dirname,'../src-tauri/frontend/app.js'),'utf8');
  source=source.replace('  // ---- Public API ----',
    'gamePanels=()=>({effectRevision:()=>null});renderRulesetPanelProjection=()=>{};window.builderQueueQA={state,renderRulesetPanels,sequenceMarkup};\n  // ---- Public API ----');
  vm.runInNewContext(source,context);
  const api=context.window.builderQueueQA;
  let queueReads=0;
  Object.assign(api.state,{activeCampaignId:'campaign',activeSessionId:'builder',activeSessionRole:'builder',
    sequenceMode:true,view:'session',bridge:{
      campaign_panels:async()=>({campaign_id:'campaign',revision:0,surfaces:[]}),
      action_sequence:async()=>{queueReads++;return null;},compose_action_sequence:async()=>{throw Error('Cannot compose here');}
    }});
  assert.equal(api.sequenceMarkup(),'');
  await api.renderRulesetPanels('campaign',true);
  assert.equal(queueReads,0,'A campaign-bound Builder must not call the DM queue API');
  api.state.activeSessionRole='dm';
  assert.notEqual(api.sequenceMarkup(),'');
  await api.renderRulesetPanels('campaign',true);
  assert.equal(queueReads,1,'The owning DM still reads its durable queue');
});


test('paused native boundary explains the next step without exposing engine tool names',()=>{
  const context={window:{},document:{addEventListener(){}}};
  let source=fs.readFileSync(path.join(__dirname,'../src-tauri/frontend/app.js'),'utf8');
  source=source.replace('  // ---- Public API ----',
    'window.pausedQueueQA={state,sequenceMarkup};\n  // ---- Public API ----');
  vm.runInNewContext(source,context);
  const api=context.window.pausedQueueQA;
  Object.assign(api.state,{activeSessionId:'gm',activeSessionRole:'dm',view:'session',
    bridge:{compose_action_sequence(){}},actionSequence:{session_id:'gm',status:'paused',
      pause_reason:'Required Ruleset decision: prepare_srd_coordinate_opportunity',
      committed_action_ids:['native-move'],steps:[
        {step_id:'first',label:'Walk',status:'committed'},
        {step_id:'next',label:'Walk',status:'pending'}]}});
  const html=api.sequenceMarkup();
  assert.match(html,/Paused for a rules decision\. Resolve it before continuing\./);
  assert.doesNotMatch(html,/prepare_srd_coordinate_opportunity|Required Ruleset decision/);
  assert.match(html,/Committed/);assert.match(html,/Pending/);
  assert.equal((html.match(/data-sequence-remove=/g)||[]).length,1);
  assert.doesNotMatch(html,/data-sequence-run/,'A paused suffix cannot silently execute');
});


function presentationQueue(queue) {
  const context = {window:{},document:{addEventListener(){}}};
  const source = fs.readFileSync(path.join(__dirname,'../src-tauri/frontend/app.js'),'utf8')
    .replace('  // ---- Public API ----', 'window.queuePresentationQA={state,sequenceMarkup};\n  // ---- Public API ----');
  vm.runInNewContext(source,context);
  const api = context.window.queuePresentationQA;
  Object.assign(api.state,{activeSessionId:'gm',activeSessionRole:'dm',view:'session',
    bridge:{compose_action_sequence(){}},actionSequence:queue});
  return api.sequenceMarkup();
}

test('queue shows a failed pending reason once and never rejudges committed receipts',()=>{
  const reason = "Casting needs this creature's available combat action.";
  const receipt = {status:'committed',action_id:'saved-cast',result:{damage:10}};
  const queue = {session_id:'gm',status:'failed',error:reason,committed_action_ids:['saved-cast'],steps:[
    {step_id:'done',label:'Magic Missile',status:'committed',receipt,
      selection:{target:'guard'},warning:'This committed action is unavailable now.'},
    {step_id:'remaining',label:'Magic Missile',status:'pending',error:reason}]};
  const before = JSON.stringify(queue);
  const html = presentationQueue(queue);
  assert.equal(html.split(reason.replaceAll("'",'&#39;')).length-1,1,'The same native failure belongs to its pending row once');
  const committed = html.match(/<li class="sequence-step sequence-committed">[\s\S]*?<\/li>/)[0];
  assert.match(committed,/Committed/);
  assert.match(committed,/Target: guard/);
  assert.doesNotMatch(committed,/unavailable|Casting needs|data-sequence-remove/);
  assert.match(html,/Review remaining actions/);
  assert.equal(JSON.stringify(queue),before,'Presentation must preserve every saved receipt and queue field');
});

test('queue keeps distinct queue-wide failures and pending-step warnings visible',()=>{
  const html = presentationQueue({session_id:'gm',status:'stale',error:'The campaign changed elsewhere.',steps:[
    {step_id:'remaining',label:'Move',status:'pending',warning:'Review this destination.'}]});
  assert.match(html,/The campaign changed elsewhere\./);
  assert.match(html,/Review this destination\./);
  assert.match(html,/data-sequence-remove=/);
});

test('queue presents a shared native pause explanation once after player wording',()=>{
  const html = presentationQueue({session_id:'gm',status:'paused',
    pause_reason:'Required Ruleset decision: prepare_srd_coordinate_opportunity',steps:[
      {step_id:'remaining',label:'Move',status:'pending',
        warning:'Required Ruleset decision: another_native_decision'}]});
  const reason = 'Paused for a rules decision. Resolve it before continuing.';
  assert.equal(html.split(reason).length-1,1);
  assert.doesNotMatch(html,/prepare_srd_coordinate_opportunity|another_native_decision/);
});


test('committed queue rows suppress later error and warning projections without changing receipts',()=>{
  for (const field of ['error','warning']) {
    const queue = {session_id:'gm',status:'completed',committed_action_ids:['saved-move'],steps:[
      {step_id:'saved',label:'Move',status:'committed',receipt:{action_id:'saved-move',distance:10},
        [field]:'Current movement is unavailable.'}]};
    const before = JSON.stringify(queue);
    const html = presentationQueue(queue);
    assert.match(html,/Committed/);
    assert.doesNotMatch(html,/Current movement is unavailable/);
    assert.equal(JSON.stringify(queue),before);
  }
});

test('queued continuous-map points distinguish movement intentions without altering saved selections',()=>{
  const queue={session_id:'gm',status:'ready',steps:[
    {step_id:'first',label:'Move',status:'pending',selection:{actor:'hero',aim_point:[0,1,0],map_revision:7}},
    {step_id:'second',label:'Move',status:'pending',selection:{actor:'hero',aim_point:[0,2,0],map_revision:7}}]};
  const before=JSON.stringify(queue),html=presentationQueue(queue);
  assert.match(html,/Map point: \[0,1,0\]/);assert.match(html,/Map point: \[0,2,0\]/);
  assert.equal(JSON.stringify(queue),before);
});
