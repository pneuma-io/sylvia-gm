// Failed creation must report its error on the surface where it was requested.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

for (const homeVisible of [true, false]) {
  test(`Campaign creation failure is visible with Home ${homeVisible ? 'open' : 'closed'}`, async () => {
    const source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
    const start = source.indexOf('  async function startCampaignBuilder() {');
    const end = source.indexOf('  async function beginConstruction()', start);
    assert.ok(start >= 0 && end > start);
    const nodes = {
      'home-view': { hidden: !homeVisible },
      'session-status-text': { textContent: '' },
      'home-action-status': { textContent: '', hidden: true },
    };
    const notices = [];
    const context = {
      crypto: webcrypto,
      state: { view: homeVisible ? 'home' : 'session', bridge: { create_guided_campaign_draft: async () => {
        throw new Error('Saved campaign could not be verified');
      } } },
      $: (id) => nodes[id],
      hideConstructionPanel() {},
      defaultModelChoice: () => ({ provider_id: 'provider', model_id: 'model' }),
      friendlyCommandError: (label, error) => `${label}: ${error.message}`,
      window: { alert: (message) => notices.push(message) },
    };
    const feedbackStart = source.indexOf('  function setInlineStatus(');
    const feedbackEnd = source.indexOf('  function selectedStatus(', feedbackStart);
    vm.runInNewContext(source.slice(feedbackStart, feedbackEnd) + source.slice(start, end), context);
    await context.startCampaignBuilder();
    const feedback = nodes[homeVisible ? 'home-action-status' : 'session-status-text'];
    assert.match(feedback.textContent, /Saved campaign could not be verified/);
    assert.equal(feedback.hidden, false);
    assert.deepEqual(notices, []);
  });
}
// Component regression; native QA separately exercises the production bridge.
for (const configured of [true, false]) {
  test(`Ready to Begin honours selected chat model without global default: ${configured}`, async () => {
    const source=fs.readFileSync(path.join(__dirname,'../src-tauri/frontend/app.js'),'utf8');
    const start=source.indexOf('  async function beginConstruction() {');
    const end=source.indexOf('  async function finalizeSelectedConstruction(',start);
    const nodes={};
    for(const id of ['construction-name','construction-package','construction-boundaries','construction-preferences','construction-premise','construction-setting','rift-archetype','construction-template']) nodes[id]={value:''};
    nodes['construction-name'].value='Selected model QA'; nodes['construction-package'].value='sylvia.srd-lite.bg3';nodes['rift-archetype'].value='tavin-bell';
    nodes['construction-no-requests']={checked:true};nodes['rift-companions']={querySelectorAll:()=>[]};
    for(const id of ['begin-construction-btn','cancel-construction-btn','construction-next-btn','construction-back-btn']) nodes[id]={disabled:false};
    const calls=[],feedback=[];
    const context={state:{constructionAnswers:{},constructionDraftId:'draft',activeSessionId:'interview',bridge:{start_guided_campaign:async command=>{calls.push(command);return {campaign_id:'campaign',builder_session_id:'interview'};}}},
      $:id=>nodes[id],selectedModelChoice:()=>configured?{provider_id:'chatgpt_subscription',model_id:'chatgpt-subscription/gpt-6.1-sol'}:null,
      defaultModelChoice:()=>{throw new Error('No global default must be required');},setInlineStatus:(text)=>feedback.push(text),campaignPartySelectionProblem:()=>null,
      usesBundledCampaignSetup:()=>false,selectedCampaignPlan:()=>({id:'a-debt-in-iron'}),hideConstructionPanel(){},refreshCampaignIndex:async()=>{},openCampaign:async()=>{},finalizeSelectedConstruction:async()=>calls.push('finalize'),friendlyCommandError:(_label,e)=>e.message,updateCampaignPartyActionAvailability(){}};
    vm.runInNewContext(source.slice(start,end),context);await context.beginConstruction();
    if(configured){assert.equal(calls.length,2);assert.equal(calls[0].player_character_id,'tavin-bell');assert.equal(calls[1],'finalize');assert.ok(!feedback.includes('No configured models'));}
    else {assert.equal(calls.length,0);assert.ok(feedback.includes('No configured models'));}
    assert.equal(nodes['begin-construction-btn'].disabled,false);
  });
}
