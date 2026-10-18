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
      dispatchPanelWindowRequest,wireSequence,queueView,queueResponseAction,queueProjection,nativePanelProjection,queueOperation,panelWindowBridge};
    renderRulesetPanels=async()=>{};refreshSelectedCampaign=async()=>{};
    submitInput=async(text,options)=>{window.responses.push({text,options});window.onNarration?.();};
    refreshQueuePresentation=()=>window.queuePresentations.push(queueProjection({campaign_id:'campaign',revision:3,surfaces:[]}).surfaces.at(-1).data.busy);
    // ---- Public API ----`);
  ctx.window.responses=[];ctx.window.queuePresentations=[];vm.runInNewContext(source,ctx);
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
  return {api,context:ctx,action,reads,compositions,executions,responses:ctx.window.responses,
    saved:()=>saved,setSaved:value=>saved=plain(value)};
}


const queueId='sylvia.action-queue';
const scope=()=>({campaign_id:'campaign',session_id:'gm',current:()=>true,owns:id=>id===queueId});
function command(h,operation,extra={}){return {campaign_id:'campaign',session_id:'gm',surface_id:queueId,operation,sequence_id:h.saved().sequence_id,expected_version:h.saved().version,...extra};}
test('app-owned queue registers once and replaces reserved-ID collisions without changing the input',async()=>{
 const h=harness();await h.api.appendQueuedAction(h.action('move'));const projection={campaign_id:'campaign',revision:3,surfaces:[{id:queueId,renderer:'generic',data:{forged:true}},{id:'map'}]};const before=JSON.stringify(projection);
 const result=h.api.queueProjection(projection);assert.equal(result.surfaces.filter(s=>s.id===queueId).length,1);
 const q=result.surfaces.find(s=>s.id===queueId);assert.equal(q.renderer,'action_queue');assert.equal(q.default_size,220);assert.equal(q.minimum_size,120);assert.deepEqual(plain(q.allowed_placements),['docked','floating','window']);assert.equal(JSON.stringify(projection),before);
 assert.equal(h.api.queueProjection(result).surfaces.filter(s=>s.id===queueId).length,1);
});
test('read-only queue publication omits confirmation, native receipts, tool names and parameters',async()=>{
 const h=harness();await h.api.appendQueuedAction(h.action('move'));const raw={...h.saved(),confirmation:'PRIVATE-CONSENT'};raw.steps[0]={...raw.steps[0],tool:'private_tool',receipt:{hidden:'private-result'},operation_id:'native-id',parameters:{secret:1}};h.api.state.actionSequence=raw;
 const view=h.api.queueView(),text=JSON.stringify(view);assert.equal(view.steps.length,1);assert.doesNotMatch(text,/PRIVATE-CONSENT|private_tool|private-result|native-id|parameters/);assert.equal(raw.confirmation,'PRIVATE-CONSENT');
});
test('queue versions and lane pending state advance presentation without a game revision',async()=>{
 const h=harness(),projection={campaign_id:'campaign',revision:3,surfaces:[]};const empty=h.api.queueProjection(projection);
 const gate=defer(),snapshot=h.api.state.bridge.snapshot;h.api.state.bridge.snapshot=async()=>{await gate.promise;return snapshot();};
 const pending=h.api.appendQueuedAction(h.action('move'));const busy=h.api.queueProjection(projection);assert.equal(busy.surfaces.at(-1).data.busy,true);assert.notEqual(busy.presentation_key,empty.presentation_key);gate.resolve();await pending;
 const ready=h.api.queueProjection(projection);assert.equal(ready.revision,3);assert.equal(ready.surfaces.at(-1).data.busy,false);assert.notEqual(ready.presentation_key,busy.presentation_key);
});
test('loading and session/role changes never publish an old queue',async()=>{
 const h=harness();await h.api.appendQueuedAction(h.action('move'));h.api.state.actionSequence=h.saved();let result=h.api.queueProjection({campaign_id:'campaign',loading:'loading',surfaces:[{id:queueId,data:{queue:h.saved()}}]});assert.equal(h.api.state.actionSequence,null);assert.equal(result.surfaces[0].data,undefined);assert.equal(result.presentation_key,null);
 h.api.state.actionSequence=h.saved();h.api.state.activeSessionId='other';result=h.api.queueProjection({campaign_id:'campaign',revision:3,surfaces:[]});assert.equal(result.surfaces.at(-1).data.queue,null);
 h.api.state.activeSessionRole='builder';assert.equal(h.api.queueProjection({campaign_id:'campaign',surfaces:[]}).surfaces.length,0);
});
test('a child renders the published queue and exposes only bounded forwarded queue operations',async()=>{
 const h=harness();await h.api.appendQueuedAction(h.action('move'));const view={queue:h.api.queueView(h.saved()),busy:false};h.context.window.__sylviaPanelContext={window_id:'queue'};h.api.state.bridge={queue_sequence_operation(){}};h.api.state.actionSequence=null;
 assert.match(h.api.sequenceMarkup(new Set(),view),/data-sequence-run/);const bridge=h.api.panelWindowBridge();assert.equal(typeof bridge.queue_sequence_operation,'function');assert.equal(bridge.compose_action_sequence,undefined);assert.equal(bridge.execute_action_sequence,undefined);
});
test('detached duplicate Execute uses one main-owned execution and one narration',async()=>{
 const h=harness();await h.api.appendQueuedAction(h.action('move'));const args=command(h,'execute'),s=scope();
 await Promise.all([h.api.dispatchPanelWindowRequest('queue_sequence_operation',args,s),h.api.dispatchPanelWindowRequest('queue_sequence_operation',args,s)]);
 assert.equal(h.executions.length,1);assert.equal(h.responses.length,1);
});
test('detached removal and review use current main authority without spending resources',async()=>{
 const h=harness();await h.api.appendQueuedAction(h.action('move'));await h.api.appendQueuedAction(h.action('guard'));
 await h.api.dispatchPanelWindowRequest('queue_sequence_operation',command(h,'remove',{step_id:'move'}),scope());assert.deepEqual(h.saved().steps.map(s=>s.step_id),['guard']);
 await h.api.dispatchPanelWindowRequest('queue_sequence_operation',command(h,'review'),scope());assert.equal(h.executions.length,0);assert.equal(h.responses.length,0);
});
test('detached requests reject foreign ownership, wrong sessions and forged/native fields',async()=>{
 for(const patch of [{campaign_id:'foreign'},{session_id:'other'},{surface_id:'map'},{operation:'native'},{expected_version:'1'},{confirmation:'forged'},{steps:[]},{operation:'execute',step_id:'move'}]){
  const h=harness();await h.api.appendQueuedAction(h.action('move'));await assert.rejects(h.api.dispatchPanelWindowRequest('queue_sequence_operation',{...command(h,'execute'),...patch},scope()),/invalid|moved|session/);assert.equal(h.executions.length,0);
 }
 const h=harness();await h.api.appendQueuedAction(h.action('move'));await assert.rejects(h.api.dispatchPanelWindowRequest('queue_sequence_operation',command(h,'execute'),{...scope(),owns:()=>false}),/invalid|moved/);
});
test('detached stale versions and lost context cannot spend or retarget actions',async()=>{
 const h=harness();await h.api.appendQueuedAction(h.action('move'));const stale=command(h,'execute');await h.api.appendQueuedAction(h.action('guard'));
 await assert.rejects(h.api.dispatchPanelWindowRequest('queue_sequence_operation',stale,scope()),/changed/);assert.equal(h.executions.length,0);
 await assert.rejects(h.api.dispatchPanelWindowRequest('queue_sequence_operation',command(h,'execute'),{...scope(),current:()=>false}),/selected/);
 const gate=defer(),read=h.api.state.bridge.action_sequence;let owned=true;h.api.state.bridge.action_sequence=async args=>{await gate.promise;return read(args);};
 const result=h.api.dispatchPanelWindowRequest('queue_sequence_operation',command(h,'execute'),{...scope(),owns:()=>owned});await new Promise(setImmediate);owned=false;gate.resolve();await assert.rejects(result,/selected/);assert.equal(h.executions.length,0);
});
test('a child cannot remove committed steps or receive a native confirmation in its result',async()=>{
 const h=harness();await h.api.appendQueuedAction(h.action('move'));let value=h.saved();value.steps[0].status='committed';h.setSaved(value);
 await assert.rejects(h.api.dispatchPanelWindowRequest('queue_sequence_operation',command(h,'remove',{step_id:'move'}),scope()),/pending/);
 value=h.saved();value.steps[0].status='pending';h.setSaved(value);const result=await h.api.dispatchPanelWindowRequest('queue_sequence_operation',command(h,'execute'),scope());assert.equal(result.confirmation,undefined);assert.equal(result.steps[0].receipt,undefined);
});

test('queue-only and layout decoration retain native projection identity for action checks',()=>{
 const h=harness(),raw={campaign_id:'campaign',revision:3,surfaces:[]};
 const first=h.api.queueProjection(raw),second=h.api.queueProjection(first);
 assert.equal(h.api.nativePanelProjection(first),raw);assert.equal(h.api.nativePanelProjection(second),raw);
 const fresh={...raw};assert.equal(h.api.nativePanelProjection(h.api.queueProjection(fresh)),fresh);
});

test('completed narration replacing its activity still publishes the final idle queue',async()=>{
 const h=harness();await h.api.appendQueuedAction(h.action('move'));
 h.context.window.onNarration=()=>{const prior=h.api.state.sessionActivities.get('gm');h.api.state.sessionActivities.set('gm',{...prior,phase:'completed',attemptId:'new-attempt'});};
 await h.api.dispatchPanelWindowRequest('queue_sequence_operation',command(h,'execute'),scope());
 assert.equal(h.executions.length,1);assert.equal(h.responses.length,1);
 assert.equal(h.context.window.queuePresentations.at(-1),false,'Publish the final idle state even when narration replaces the captured activity');
});

function savedResponse(h,actionId='native-prefix'){
 const projection={campaign_id:'campaign',revision:3,surfaces:[],response_action:{action_id:actionId,session_id:'gm',available:true}};
 h.api.state.panelProjection=projection;h.api.state.bridge.campaign_panels=async()=>plain(projection);return projection;
}
async function stoppedResponse(h,status='failed'){
 await h.api.appendQueuedAction(h.action('attack'));await h.api.appendQueuedAction(h.action('remaining'));
 const q=h.saved();q.status=status;q.steps[0].status='committed';q.steps[0].receipt={action_id:'native-prefix',status:'committed'};q.committed_action_ids=['native-prefix'];
 h.setSaved(q);h.api.state.actionSequence=h.saved();return savedResponse(h);
}
test('one Execute is rendered across queue states without duplicated response controls or narration copy',async()=>{
 for(const status of ['ready','running','failed','paused','completed']){
  const h=harness();await h.api.appendQueuedAction(h.action('move'));const q=h.saved();q.status=status;h.api.state.actionSequence=q;
  const html=h.api.sequenceMarkup();assert.equal((html.match(/data-sequence-execute/g)||[]).length,1);assert.match(html,/>Execute<\/button>/);
  assert.doesNotMatch(html,/Ask Sylvia|Commit all|Choose actions|Sylvia narrates|sequence-caption|data-player-action-response|>Recover<\/button>/);
 }
});
test('duplicate Execute on a failed saved prefix requests one response without replaying mechanics or the suffix',async()=>{
 const h=harness();await stoppedResponse(h);const before=plain(h.saved()),args={...command(h,'execute'),response_action_id:'native-prefix'};
 await Promise.all([h.api.dispatchPanelWindowRequest('queue_sequence_operation',args,scope()),h.api.dispatchPanelWindowRequest('queue_sequence_operation',args,scope())]);
 assert.equal(h.executions.length,0);assert.equal(h.responses.length,1);assert.equal(h.responses[0].options.playerAction.actionId,'native-prefix');assert.deepEqual(h.saved(),before);
});
test('paused and completed saved outcomes use the same Execute response path without native execution',async()=>{
 for(const status of ['paused','completed']){
  const h=harness();await stoppedResponse(h,status);const before=plain(h.saved());assert.match(h.api.sequenceMarkup(),/data-sequence-run/);
  await h.api.dispatchPanelWindowRequest('queue_sequence_operation',{...command(h,'execute'),response_action_id:'native-prefix'},scope());
  assert.equal(h.executions.length,0);assert.equal(h.responses.length,1);assert.deepEqual(h.saved(),before);
 }
});
test('a saved outcome without a queue is available through Execute with null queue identity and version zero',async()=>{
 const h=harness();savedResponse(h,'legacy-action');const html=h.api.sequenceMarkup();assert.match(html,/data-sequence-run/);assert.doesNotMatch(html,/Choose actions/);
 const args={campaign_id:'campaign',session_id:'gm',surface_id:queueId,operation:'execute',sequence_id:null,expected_version:0,response_action_id:'legacy-action'};
 await Promise.all([h.api.dispatchPanelWindowRequest('queue_sequence_operation',args,scope()),h.api.dispatchPanelWindowRequest('queue_sequence_operation',args,scope())]);
 assert.equal(h.executions.length,0);assert.equal(h.responses.length,1);assert.equal(h.responses[0].options.playerAction.actionId,'legacy-action');
});
test('a current runnable queue still executes and narrates once rather than responding before its suffix',async()=>{
 const h=harness();await h.api.appendQueuedAction(h.action('move'));savedResponse(h,'older-prefix');
 await h.api.dispatchPanelWindowRequest('queue_sequence_operation',{...command(h,'execute'),response_action_id:'older-prefix'},scope());
 assert.equal(h.executions.length,1);assert.equal(h.responses.length,1);assert.equal(h.responses[0].options.playerAction.actionId,h.saved().committed_action_ids.at(-1));
});
test('changed or unavailable response offers cannot start narration from a stale Execute control',async()=>{
 for(const patch of [{action_id:'different'},{available:false},{session_id:'other'}]){
  const h=harness();const projection=await stoppedResponse(h);Object.assign(projection.response_action,patch);
  await assert.rejects(h.api.dispatchPanelWindowRequest('queue_sequence_operation',{...command(h,'execute'),response_action_id:'native-prefix'},scope()),/response changed/);
  assert.equal(h.executions.length,0);assert.equal(h.responses.length,0);
 }
});
test('lost child ownership during the fresh response read cannot start narration',async()=>{
 const h=harness();await stoppedResponse(h);const gate=defer(),read=h.api.state.bridge.campaign_panels;let owned=true;
 h.api.state.bridge.campaign_panels=async()=>{await gate.promise;return read();};
 const pending=h.api.dispatchPanelWindowRequest('queue_sequence_operation',{...command(h,'execute'),response_action_id:'native-prefix'},{...scope(),owns:()=>owned});
 await new Promise(setImmediate);owned=false;gate.resolve();await assert.rejects(pending,/selected/);assert.equal(h.responses.length,0);assert.equal(h.executions.length,0);
});
test('response availability and identity update child presentation without changing the mechanical revision',()=>{
 const h=harness(),projection=savedResponse(h);const a=h.api.queueProjection(projection);projection.response_action.available=false;const b=h.api.queueProjection(projection);
 assert.equal(a.revision,b.revision);assert.notEqual(a.presentation_key,b.presentation_key);assert.equal(h.api.queueResponseAction(),null);
 projection.response_action.available=true;projection.response_action.action_id='later';const c=h.api.queueProjection(projection);assert.notEqual(b.presentation_key,c.presentation_key);
});
test('response request fields remain bounded and cannot turn remove/review into a response request',async()=>{
 const h=harness();await stoppedResponse(h);
 for(const args of [{...command(h,'remove',{step_id:'remaining'}),response_action_id:'native-prefix'},{...command(h,'review'),response_action_id:null},{...command(h,'execute'),response_action_id:42},{...command(h,'execute'),sequence_id:null,expected_version:1,response_action_id:'native-prefix'}])
  await assert.rejects(h.api.dispatchPanelWindowRequest('queue_sequence_operation',args,scope()),/invalid|moved/);
 assert.equal(h.executions.length,0);assert.equal(h.responses.length,0);
});
