const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {webcrypto}=require('node:crypto');
const root=process.env.SYLVIA_QA_SOURCE||path.join(__dirname,'..');
const plain=value=>JSON.parse(JSON.stringify(value));
const defer=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};
function harness(){
  const ctx={window:{},crypto:webcrypto,document:{addEventListener(){},getElementById:()=>({textContent:'',hidden:false})},
    localStorage:{getItem:()=>null},console,setTimeout,clearTimeout};
  let source=fs.readFileSync(path.join(root,'src-tauri/frontend/app.js'),'utf8');
  source=source.replace('  // ---- Public API ----',`
    window.defaultQueueQA={state,ensureSessionActivity,sequenceMarkup,sequenceModeActive,
      appendQueuedAction:typeof appendQueuedAction==='function'?appendQueuedAction:null,
      flushQueuedActions:typeof flushQueuedActions==='function'?flushQueuedActions:null,
      editQueuedActions:typeof editQueuedActions==='function'?editQueuedActions:null,
      dispatchPanelWindowRequest,wireSequence};
    renderRulesetPanels=async()=>{};refreshSelectedCampaign=async()=>{};
    submitInput=async(text,options)=>window.responses.push({text,options});
    // ---- Public API ----`);
  ctx.window.responses=[];vm.runInNewContext(source,ctx);
  const api=ctx.window.defaultQueueQA,reads=[],compositions=[],executions=[];
  let saved=null,revision=3;
  Object.assign(api.state,{activeCampaignId:'campaign',activeSessionId:'gm',activeSessionRole:'dm',
    activeSnapshot:{revision},view:'session',panelProjection:{surfaces:[{id:'map',actions:[{id:'move',available:true}]}]}});
  api.ensureSessionActivity({sessionId:'gm',sessionRole:'dm',campaignId:'campaign'});
  api.state.bridge={
    action_sequence:async command=>{reads.push(plain(command));return saved&&plain(saved);},
    snapshot:async()=>({revision}),
    compose_action_sequence:async command=>{
      assert.equal(command.expected_version,command.sequence_id===saved?.sequence_id?saved?.version||0:0);
      compositions.push(plain(command));
      saved={...plain(command),version:command.expected_version+1,status:'ready',confirmation:'consent',
        steps:plain(command.steps).map(step=>({...plain(step),label:step.source.action_id||'Card',status:'pending',
          response_policy:step.source.action_id==='end'?'automatic':'on_request'})),committed_action_ids:[]};
      return plain(saved);
    },
    execute_action_sequence:async command=>{
      executions.push(plain(command));revision++;
      saved={...saved,version:saved.version+1,status:'completed',
        steps:saved.steps.map(step=>({...step,status:'committed',receipt:{action_id:'saved-'+step.step_id,status:'committed'}})),
        committed_action_ids:saved.steps.map(step=>'saved-'+step.step_id)};
      return plain(saved);
    },
    campaign_panels:async()=>({campaign_id:'campaign',surfaces:[]}),
  };
  const action=(id,operation=id)=>({source:{kind:'panel',surface_id:'map',action_id:id},selection:null,operation_id:operation});
  return {api,action,reads,compositions,executions,responses:ctx.window.responses,
    saved:()=>saved,setSaved:value=>saved=plain(value)};
}

test('manual queue is the default without an opt-in checkbox; Builder stays excluded',()=>{
  const h=harness();assert.equal(h.api.sequenceModeActive(),true);
  assert.doesNotMatch(h.api.sequenceMarkup(),/data-sequence-mode|type="checkbox"/);
  assert.match(h.api.sequenceMarkup(),/Game panel actions|Queued actions/);
  h.api.state.activeSessionRole='builder';assert.equal(h.api.sequenceModeActive(),false);
  assert.equal(h.api.sequenceMarkup(),'');
});
test('manual intention composition has no native execution or narration',async()=>{
  const h=harness();await h.api.appendQueuedAction(h.action('attack'));
  assert.equal(h.compositions.length,1);assert.equal(h.executions.length,0);assert.equal(h.responses.length,0);
  assert.equal(h.saved().steps[0].source.action_id,'attack');
});
test('rapid selections preserve click order and fresh queue versions',async()=>{
  const h=harness(),gate=defer(),original=h.api.state.bridge.snapshot;
  let calls=0;h.api.state.bridge.snapshot=async()=>{if(++calls===1)await gate.promise;return original();};
  const first=h.api.appendQueuedAction(h.action('attack')),second=h.api.appendQueuedAction(h.action('move'));
  await new Promise(setImmediate);assert.equal(h.compositions.length,0);gate.resolve();await Promise.all([first,second]);
  assert.deepEqual(h.saved().steps.map(step=>step.source.action_id),['attack','move']);
  assert.deepEqual(h.compositions.map(value=>value.expected_version),[0,1]);
});
test('explicit execute drains prior selections and requests exactly one saved response',async()=>{
  const h=harness();const a=h.api.appendQueuedAction(h.action('attack')),b=h.api.appendQueuedAction(h.action('move'));
  const run=h.api.flushQueuedActions();await Promise.all([a,b,run]);
  assert.deepEqual(h.saved().steps.map(step=>step.source.action_id),['attack','move']);
  assert.equal(h.executions.length,1);assert.equal(h.responses.length,1);
  assert.equal(h.responses[0].text,'');assert.equal(h.responses[0].options.playerAction.automatic,false);
  assert.equal(h.responses[0].options.playerAction.actionId,h.saved().committed_action_ids.at(-1));
});
test('turn-end drains earlier appends and duplicate operation cannot append or execute twice',async()=>{
  const h=harness(),gate=defer(),snapshot=h.api.state.bridge.snapshot;
  h.api.state.bridge.snapshot=async()=>{await gate.promise;return snapshot();};
  const a=h.api.appendQueuedAction(h.action('attack'));
  const end=h.api.appendQueuedAction(h.action('end','end-operation'),{flush:true});
  const duplicate=h.api.appendQueuedAction(h.action('end','end-operation'),{flush:true});
  await assert.rejects(h.api.appendQueuedAction(h.action('late')),/execut|finishing|queued/i);
  gate.resolve();await Promise.all([a,end,duplicate]);
  assert.deepEqual(h.saved().steps.map(step=>step.source.action_id),['attack','end']);
  assert.equal(h.executions.length,1);assert.equal(h.responses.length,1);
});
test('a failed prefix preserves end-turn pending and does not narrate or execute it separately',async()=>{
  const h=harness();h.api.state.bridge.execute_action_sequence=async command=>{
    h.executions.push(plain(command));const q=h.saved();
    const result={...q,status:'failed',steps:q.steps.map((s,i)=>({...s,status:i===0?'committed':'pending'})),committed_action_ids:['first-only']};
    h.setSaved(result);return result;
  };
  await h.api.appendQueuedAction(h.action('attack'));
  await h.api.appendQueuedAction(h.action('end'),{flush:true});
  assert.equal(h.executions.length,1);assert.equal(h.responses.length,0);
  assert.equal(h.saved().steps[1].status,'pending');
});
test('native turn boundary after the final requested action still narrates committed results once',async()=>{
  const h=harness(),execute=h.api.state.bridge.execute_action_sequence;
  h.api.state.bridge.execute_action_sequence=async c=>({...await execute(c),status:'paused',pause_reason:'Native action reached a control or turn boundary'});
  await h.api.appendQueuedAction(h.action('end'),{flush:true});
  assert.equal(h.responses.length,1);assert.equal(h.executions.length,1);
});
test('navigation during a queue read cannot compose or overwrite the selected session',async()=>{
  const h=harness(),gate=defer();h.api.state.bridge.action_sequence=async()=>{await gate.promise;return null;};
  const pending=h.api.appendQueuedAction(h.action('attack'));
  await new Promise(setImmediate);h.api.state.activeSessionId='other';gate.resolve();
  await assert.rejects(pending,/session|selected/i);assert.equal(h.compositions.length,0);
  assert.equal(h.api.state.actionSequence,undefined);
});
test('navigation after native execute never sends narration into the new session',async()=>{
  const h=harness(),execute=h.api.state.bridge.execute_action_sequence;
  h.api.state.bridge.execute_action_sequence=async command=>{const q=await execute(command);h.api.state.activeSessionId='other';return q;};
  await h.api.appendQueuedAction(h.action('attack'));await assert.rejects(h.api.flushQueuedActions(),/session|selected/i);
  assert.equal(h.responses.length,0);assert.equal(h.executions.length,1);
});
test('detached and main selections share one queue; moved panels cannot submit',async()=>{
  const h=harness(),scope={campaign_id:'campaign',session_id:'gm',current:()=>true,owns:id=>id==='map'};
  const command={campaign_id:'campaign',session_id:'gm',surface_id:'map',panel_action_id:'move',operation_id:'detached-move',selection:null};
  await Promise.all([h.api.appendQueuedAction(h.action('attack')),h.api.dispatchPanelWindowRequest('queue_panel_action',command,scope)]);
  await h.api.dispatchPanelWindowRequest('queue_panel_action',command,scope);
  assert.deepEqual(h.saved().steps.map(step=>step.source.action_id),['attack','move']);
  assert.equal(h.executions.length,0);assert.equal(h.responses.length,0);
  await assert.rejects(h.api.dispatchPanelWindowRequest('queue_panel_action',{...command,surface_id:'foreign'},scope),/panel|moved/i);
});
test('detached ownership is rechecked when an accepted request reaches the lane',async()=>{
  const h=harness(),gate=defer(),snapshot=h.api.state.bridge.snapshot;let owned=true;
  h.api.state.bridge.snapshot=async()=>{await gate.promise;return snapshot();};
  const a=h.api.appendQueuedAction(h.action('attack'));
  const queued=h.api.dispatchPanelWindowRequest('queue_panel_action',{campaign_id:'campaign',session_id:'gm',surface_id:'map',panel_action_id:'move',operation_id:'child',selection:null},
    {campaign_id:'campaign',session_id:'gm',current:()=>true,owns:()=>owned});
  owned=false;gate.resolve();await a;await assert.rejects(queued,/panel|moved/i);
  assert.deepEqual(h.saved().steps.map(s=>s.source.action_id),['attack']);
});
test('Builder cannot enter the main-owned queue APIs',async()=>{
  const h=harness();h.api.state.activeSessionRole='builder';
  await assert.rejects(h.api.appendQueuedAction(h.action('attack')),/session|DM|selected/i);
  assert.equal(h.reads.length,0);assert.equal(h.compositions.length,0);
});

test('explicit execute describes only a confirmed failed prefix and leaves the suffix pending',async()=>{
  const h=harness();h.api.state.bridge.execute_action_sequence=async command=>{
    h.executions.push(plain(command));const q=h.saved();
    const result={...q,status:'failed',steps:q.steps.map((s,i)=>({...s,status:i===0?'committed':'pending'})),committed_action_ids:['native-first-only']};
    h.setSaved(result);return result;
  };
  await h.api.appendQueuedAction(h.action('first'));await h.api.appendQueuedAction(h.action('unaffordable'));
  await h.api.flushQueuedActions();
  assert.equal(h.executions.length,1);assert.equal(h.responses.length,1);
  assert.equal(h.responses[0].options.playerAction.actionId,'native-first-only');
  assert.equal(h.saved().steps[1].status,'pending');
});
test('explicit execute narrates an entirely committed native boundary with on-request policy',async()=>{
  const h=harness(),execute=h.api.state.bridge.execute_action_sequence;
  h.api.state.bridge.execute_action_sequence=async command=>({...await execute(command),status:'paused',pause_reason:'Native action reached a control or turn boundary'});
  await h.api.appendQueuedAction(h.action('legacy-boundary'));
  await h.api.flushQueuedActions();
  assert.equal(h.responses.length,1);assert.equal(h.responses[0].options.playerAction.automatic,false);
});

test('automatic End Turn response resumes required native lifecycle after all requested steps commit',async()=>{
  const h=harness(),execute=h.api.state.bridge.execute_action_sequence;
  h.api.state.bridge.execute_action_sequence=async command=>({...await execute(command),
    status:'paused',pause_reason:'Required Ruleset decision: advance_combat_encounter_turn'});
  await h.api.appendQueuedAction(h.action('attack'));
  await h.api.appendQueuedAction(h.action('end'),{flush:true});
  assert.equal(h.executions.length,1);assert.equal(h.responses.length,1);
  assert.deepEqual(h.saved().steps.map(step=>step.status),['committed','committed']);
  assert.equal(h.responses[0].options.playerAction.actionId,h.saved().committed_action_ids.at(-1));
  await h.api.flushQueuedActions();
  assert.equal(h.executions.length,1);assert.equal(h.responses.length,1);
});

test('fully committed automatic queue does not present its historical pause as an unresolved decision',async()=>{
  const h=harness(),execute=h.api.state.bridge.execute_action_sequence;
  h.api.state.bridge.execute_action_sequence=async command=>{const result={...await execute(command),
    status:'paused',pause_reason:'Required Ruleset decision: advance_combat_encounter_turn'};h.setSaved(result);return result;};
  await h.api.appendQueuedAction(h.action('attack'));
  await h.api.appendQueuedAction(h.action('end'),{flush:true});
  assert.match(h.api.sequenceMarkup(),/aria-label="0 queued actions">0</);
  assert.equal((h.api.sequenceMarkup().match(/sequence-committed/g)||[]).length,2);
  assert.doesNotMatch(h.api.sequenceMarkup(),/data-sequence-run/);
  assert.doesNotMatch(h.api.sequenceMarkup(),/Paused|Ask Sylvia|Resolve .*decision/);
  assert.equal(h.saved().status,'paused');
});

test('next intention starts a distinct queue after an automatic committed boundary without carrying old actions',async()=>{
  const h=harness(),execute=h.api.state.bridge.execute_action_sequence;
  h.api.state.bridge.execute_action_sequence=async command=>{const result={...await execute(command),
    status:'paused',pause_reason:'Required Ruleset decision: advance_combat_encounter_turn'};h.setSaved(result);return result;};
  await h.api.appendQueuedAction(h.action('attack'));
  await h.api.appendQueuedAction(h.action('end'),{flush:true});
  const previous=plain(h.saved());await h.api.appendQueuedAction(h.action('next-turn-move'));
  assert.notEqual(h.saved().sequence_id,previous.sequence_id);
  assert.deepEqual(h.saved().steps.map(s=>s.source.action_id),['next-turn-move']);
  assert.equal(h.saved().expected_version,0);assert.equal(h.executions.length,1);assert.equal(h.responses.length,1);
  assert.deepEqual(previous.steps.map(s=>s.status),['committed','committed']);
});
