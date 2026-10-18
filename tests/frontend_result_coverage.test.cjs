const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function app() {
  const sandbox = { window: {}, document: { addEventListener() {} } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8')
    .replace('    toolResultMarkup,', '    toolResultMarkup, renderProjection,'), sandbox);
  return sandbox.window.SylviaGMFrontend;
}

const receipts = [
  ['check_character_profile_draft', {success:true,valid:true,published:false}, 'Character Profile checked', 'Draft is valid'],
  ['publish_character_profile', {success:true,published:true,name:'Vela',campaign_ready:false}, 'Character Profile saved', 'Vela'],
  ['open_character_profile', {success:true,published:false,path:'vela-copy.json'}, 'Character Profile draft opened', 'vela-copy.json'],
  ['check_participant_draft', {success:true,status:'valid_draft',published:false,complete_entity_state:{private:'SECRET'}}, 'Character draft checked', 'selected Ruleset'],
  ['publish_participant_artifact', {success:true,status:'published',kind:'companion',title:'Vela'}, 'Companion saved', 'Vela'],
  ['publish_participant_artifact', {success:true,status:'published',kind:'player_character',title:'Hero'}, 'Player Character saved', 'Hero'],
  ['check_campaign_setting_draft', {success:true,status:'valid_draft'}, 'Setting draft checked', 'Draft is valid'],
  ['publish_campaign_setting', {success:true,title:'Relay world'}, 'Setting saved', 'Relay world'],
  ['preview_character_card_import', {success:true,published:false,profile_preview:{name:'Vela',foreign_configuration:'SECRET'},conversion_preview:{imported:['SECRET'],transformed:[],preserved_only:['SECRET'],unsupported:[],omitted:[]}}, 'Character Card preview', 'preserved only: 1'],
  ['import_character_card', {success:true,published:true,name:'Vela'}, 'Character Card imported', 'Adapt this source'],
  ['complete_creation_subflow', {success:true,status:'ready',summary:'Vela ready',boundary_conflicts:[]}, 'Creation ready for review', 'Review and select'],
  ['complete_creation_subflow', {success:false,status:'blocked',summary:'Review boundaries',boundary_conflicts:['Keep <original> history']}, 'Creation needs changes', 'Keep &lt;original&gt; history'],
];

for (const [tool_name,result,title,value] of receipts) test(`saved ${tool_name} ${result.status||title} survives reopen outside activity`,()=>{
  const api=app(), content={tool_name,result,activity:{attempt_id:'attempt',basis_message_id:'input'}};
  const before=JSON.stringify(content), card=api.toolResultMarkup(content,'');
  assert.match(card,/result-card-inline/);
  assert(card.includes(title),title); assert(card.includes(value),value);
  assert.doesNotMatch(card,/SECRET|Operation completed|tool-card|skill-check-/);
  assert.equal(api.toolResultMarkup(JSON.parse(before),''),card);
  const html=api.groupedAgentActivity([
    {message_id:'input',kind:'user',content:{text:'Check and save'}},
    {message_id:'receipt',kind:'evidence',content},
    {message_id:'terminal',kind:'evidence',content:{kind:'builder_attempt_terminal',attempt_id:'attempt',status:'completed'}}
  ],['USER',card,''],{},'builder').join('');
  assert(html.indexOf('</details>')<html.indexOf(title), 'result follows closed activity');
  assert.equal(JSON.stringify(content),before);
});

test('rejected Builder checks and publications never claim successful validation or save',()=>{
  const render=app().toolResultMarkup;
  for (const [tool_name] of receipts) {
    const html=render({tool_name,result:{success:false,status:'rejected',valid:true,published:true,name:'SECRET'}},'');
    assert.match(html,/inline-feedback/);
    assert.doesNotMatch(html,/SECRET|Profile saved|Character saved|Draft is valid|ready for review|Setting saved|Card imported/);
  }
});

test('malformed falsy declarations remain visible while absent and null routine cards stay quiet',()=>{
  const render=app().toolResultMarkup;
  for(const result_card of [false,0,'',[],{version:999}]) {
    assert.match(render({tool_name:'custom',result:{status:'committed',result_card}},''),/cannot be displayed/);
  }
  for(const result of [{status:'committed'},{status:'committed',result_card:null}])
    assert.equal(render({tool_name:'custom',result},''),'');
});

test('legacy saved bare-ability names and recorded facts survive without arithmetic',()=>{
  const render=app().toolResultMarkup;
  const content={tool_name:'d20_test',parameters:{ability:'constitution',target_number:12},result:{context:{_outcome:'failure',_adjusted_total:7,_d20_test:{results:[6],total:6,modifier:1},actor:{data:{name:'Vela'}}}}};
  const before=JSON.stringify(content),html=render(content,'');
  for(const value of ['Constitution check','Constitution','Vela','Failure','7','12'])assert(html.includes(value),value);
  assert.doesNotMatch(html,/D20 check|result-equation/);
  assert.equal(JSON.stringify(content),before);
});

test('malformed saved cards cannot be folded into routine activity or shared-write silence',()=>{
  const api=app();
  for(const result_card of [false,0,'']) {
    const content={tool_name:'custom',routine_activity:'character_update',status:'committed',action_id:'a',activity:{attempt_id:'attempt',basis_message_id:'input'},result:{status:'committed',result_card,activity_label:'Prepare creature'}};
    const card=api.toolResultMarkup(content,'');
    assert.match(api.renderProjection({kind:'tool_card',safe_payload:content}),/cannot be displayed/);
    const html=api.groupedAgentActivity([{message_id:'input',kind:'user',content:{}},{message_id:'receipt',kind:'evidence',content}],['USER',card],{},'dm').join('');
    assert.match(html,/cannot be displayed/);
  }
});
