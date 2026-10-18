const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const sandbox = {window:{},document:{addEventListener(){}}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src-tauri/frontend/app.js'),'utf8'),sandbox);
const {withCampaignEnding} = sandbox.window.SylviaGMFrontend;
const ending = {ending_id:'safe-return',act_number:6,plan_title:'The example',after_message_id:'reply',open_world:true,has_after_story:true,choice:null};
const messages = [{message_id:'reply'},{message_id:'later'}];

test('earned completion follows its narration with real choice controls and no future ending prose',()=>{
  const html=withCampaignEnding(messages,['AFTERMATH','LATER'],ending).join('');
  assert(html.indexOf('AFTERMATH') < html.indexOf('Campaign complete'));
  assert(html.indexOf('Finish the story') < html.indexOf('LATER'));
  assert.match(html,/data-campaign-ending-choice="continue"/);
  assert.match(html,/data-campaign-ending-choice="finish"/);
  assert.doesNotMatch(html,/disabled/);
  assert.match(withCampaignEnding(messages,['A','B'],ending,true).join(''),/disabled/);
  assert.equal(withCampaignEnding([{message_id:'later'}],['B'],ending).join(''),'B');
});

test('saved choices become quiet markers; non-continuable and no-after-story endings stay honest',()=>{
  for(const choice of ['continue','finish']){
    const html=withCampaignEnding(messages,['A','B'],{...ending,choice}).join('');
    assert.match(html,/campaign-ending-marker/);
    assert.doesNotMatch(html,/data-campaign-ending-choice/);
  }
  const html=withCampaignEnding(messages,['A','B'],{...ending,open_world:false,has_after_story:false,plan_title:'<script>'}).join('');
  assert.doesNotMatch(html,/Continue adventuring|years ahead|<script>/);
  assert.match(html,/Finish the story/);
  assert.match(html,/Leave your adventure here/);
});
