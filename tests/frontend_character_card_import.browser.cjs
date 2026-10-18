const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const {chromium}=require('playwright');
const frontend=path.resolve(__dirname,'../src-tauri/frontend');
test('card preview is escaped, explicit, guarded against duplicate clicks and refreshes general-source discovery',async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge',args:['--disable-gpu']});
 try {const page=await browser.newPage({viewport:{width:1280,height:900}});
 await page.route('https://sylvia-card.test/**',async route=>{
  const name=new URL(route.request().url()).pathname.slice(1)||'app.html';
  if(!['app.html','app.js','app.css','panel-workspace.js','shot-playback.js','panel-host.js','assets/sylvia-portrait.png'].includes(name))return route.abort();
  let body=fs.readFileSync(path.join(frontend,name));if(name==='app.js')body=body.toString().replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });",'').replace('  // ---- Public API ----','window.cardTest={state,wireControls,renderCharacterProfiles};\n  // ---- Public API ----');
  await route.fulfill({body,contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.png')?'image/png':'text/html'});
 });
 await page.goto('https://sylvia-card.test/');
 await page.evaluate(()=>{
  const {state,wireControls,renderCharacterProfiles}=window.cardTest;wireControls();
  state.campaignCreationCatalog=[];renderCharacterProfiles();
  document.getElementById('connecting-overlay').style.display='none';
  document.getElementById('character-card-preview').hidden=false;
  window.cardCalls=[];state.bridge={preview_character_card_file:async()=>{cardCalls.push('preview');await new Promise(resolve=>setTimeout(resolve,30));return {status:'preview',source_sha256:'a'.repeat(64),name:'<img src=x onerror=alert(1)>',conversion_preview:{imported:['description'],transformed:[],preserved_only:['system_prompt'],unsupported:['foreign execution'],omitted:[]}}},confirm_character_card_import:async sha=>{cardCalls.push(sha);return {status:'imported',profile:{id:'source',name:'Vela'}}},campaign_creation_catalog:async()=>[{kind:'character_profile',id:'source',name:'Vela',summary:'A saved universal source.'}]};
  document.getElementById('settings-overlay').style.display='flex';
  document.querySelectorAll('[data-settings-tab]').forEach(el=>el.hidden=false);
  document.getElementById('import-character-card-btn').closest('[hidden]')?.removeAttribute('hidden');
 });
 await page.locator('#import-character-card-btn').evaluate(el=>{el.click();el.click()});
 await page.waitForFunction(()=>window.cardCalls.length===1&&window.cardTest.state.pendingCharacterCard);
 assert.match(await page.locator('#character-card-preview-title').innerText(),/Universal character card/);
 assert.equal(await page.locator('#character-card-preview-status').innerText(),'Needs ruleset adaptations');
 assert.equal(await page.locator('#character-card-preview-details').getAttribute('open'),null);
 assert.match(await page.locator('#character-card-preview-notices').innerText(),/foreign execution/);
 await page.locator('#character-card-preview-details > summary').focus();
 await page.keyboard.press('Enter');
 assert.match(await page.locator('#character-card-preview-detail-text').innerText(),/preserved only: system_prompt/);
 assert.equal(await page.locator('#character-card-preview img').count(),0);
 for(const width of [1280,962,390]){
  await page.setViewportSize({width,height:900});
  await page.locator('#character-card-preview-details > summary').focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#character-card-preview-details').getAttribute('open'),null);
  assert.equal(await page.locator('#character-card-preview-detail-text').isVisible(),false);
  const size=await page.locator('#character-card-preview').evaluate(el=>({scroll:el.scrollWidth,client:el.clientWidth}));
  assert(size.scroll<=size.client+1,`Preview overflow at ${width}`);
  await page.keyboard.press('Enter');
  assert.match(await page.locator('#character-card-preview-detail-text').innerText(),/preserved only: system_prompt/);
 }
 const output=process.env.SYLVIA_QA_SCREENSHOTS;if(output){fs.mkdirSync(output,{recursive:true});await page.locator('#character-card-preview').screenshot({path:path.join(output,'card-import-preview.png')});}
 await page.locator('#confirm-character-card-btn').evaluate(el=>el.click());
 await page.waitForFunction(()=>window.cardCalls.length===2&&!window.cardTest.state.pendingCharacterCard);
 assert.equal(await page.evaluate(()=>window.cardCalls[1]),'a'.repeat(64));
 assert.match(await page.locator('#package-install-status').innerText(),/Saved Vela as a Universal character card/);
 assert.deepEqual(await page.evaluate(()=>window.cardTest.state.campaignCreationCatalog),[{kind:'character_profile',id:'source',name:'Vela',summary:'A saved universal source.'}]);
 // The import happened while the interview picker already existed. Its real options
 // must refresh, rather than merely enabling a stale placeholder-only select.
 assert.deepEqual(await page.locator('#character-profile-select option').evaluateAll(options=>options.map(option=>option.value)),['','source']);
 assert.match(await page.locator('#character-profile-select option[value=source]').innerText(),/Vela/);
 }finally{await browser.close();}
});


test('campaign picker and rendered Ready summary use the package-friendly Lite name with exact saved bindings',async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge',args:['--disable-gpu']});
 try {
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  await page.route('https://sylvia-setup-name.test/**',async route=>{
   const name=new URL(route.request().url()).pathname.slice(1)||'app.html';
   if(!['app.html','app.js','app.css','panel-workspace.js','shot-playback.js','panel-host.js','campaign-plan-browser.js','assets/sylvia-portrait.png'].includes(name))return route.abort();
   let body=fs.readFileSync(path.join(frontend,name));
   if(name==='app.js')body=body.toString().replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });",'').replace('  // ---- Public API ----','window.setupNameTest={state,showConstructionPanel,showConstructionStep,constructionReviewText};\n  // ---- Public API ----');
   await route.fulfill({body,contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.png')?'image/png':'text/html'});
  });
  await page.goto('https://sylvia-setup-name.test/');
  const result=await page.evaluate(async()=>{
   const {state,showConstructionPanel,showConstructionStep}=window.setupNameTest;
   document.getElementById('connecting-overlay').style.display='none';
   document.getElementById('session-frame').style.display='flex';
   Object.assign(state,{activeCampaignId:'synthetic',activeSessionId:'interview',activeSessionRole:'builder',view:'session'});
   const ruleset={id:'sylvia.srd-lite.bg3',version:'0.3.11',content_sha256:'a'.repeat(64)};
   const setting={id:'test.setting',version:'1',content_sha256:'b'.repeat(64)};
   const catalog=[
    {kind:'ruleset',...ruleset,selection_id:ruleset.id,title:'D&D 5e SRD 5.2.1',compatible_settings:[{...setting,ruleset}]},
    {kind:'ruleset',id:'test.other',version:'9',content_sha256:'d'.repeat(64),selection_id:'test.other',title:'Another authored ruleset',compatible_settings:[]},
    {kind:'campaign_setting',...setting,title:'Synthetic Setting'},
    {kind:'campaign_plan',id:'test-plan',version:'1',content_sha256:'c'.repeat(64),title:'Synthetic Plan',ruleset,campaign_setting:setting},
   ];
   const before=JSON.stringify(catalog);
   state.bridge={campaign_creation_catalog:async()=>catalog,campaign_party_options:async()=>({player_archetypes:[],companions:[]})};
   await showConstructionPanel('test-plan');
   document.getElementById('construction-name').value='Synthetic setup';
   showConstructionStep(7);
   return {selected:document.getElementById('construction-package').value,labels:Array.from(document.getElementById('construction-package').options,option=>[option.value,option.textContent]),before,after:JSON.stringify(catalog)};
  });
  assert.equal(result.selected,'sylvia.srd-lite.bg3');
  assert.deepEqual(result.labels,[['','No Ruleset — narrative play'],['sylvia.srd-lite.bg3','SRD Lite'],['test.other','Another authored ruleset']]);
  assert.equal(result.after,result.before,'Display naming must not rewrite package IDs, versions, hashes or bindings');
  assert.match(await page.locator('#construction-review').innerText(),/Ruleset: SRD Lite/);
  assert.doesNotMatch(await page.locator('#construction-review').innerText(),/D&D 5e SRD/);
 }finally{await browser.close();}
});
