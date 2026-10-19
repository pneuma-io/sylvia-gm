const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const frontend=path.resolve(__dirname,'../src-tauri/frontend');

function fixture(){
 const participants=[{id:'asha',kind:'character',data:{name:'Asha',party_role:'player_character',class_id:'ranger',level:4}},
  {id:'bram',kind:'character',data:{name:'Bram',party_role:'companion',class_id:'wizard',level:3}}];
 const entities={asha:{hp:21,max_hp:25,inventory:['long_bow'],conditions:[],skills:{tracking:5}},
  bram:{hp:12,max_hp:19,inventory:['silver_dagger'],conditions:['Poisoned'],resources:{focus:7},
   weapon_sets:{active_set:'melee',melee:{main_hand:'silver_dagger'}},skills:{lore:4},aptitudes:{resolve:2}}};
 const surface=(id,renderer,title)=>({id,renderer,title,actions:[],minimum_size:96,default_size:260,data:{participants,entities}});
 return {campaign_id:'inspection',ruleset_id:'fixture',revision:1,player_control:{available:true,focused_actor_id:'asha',actors:[{id:'asha',name:'Asha'}]},surfaces:[
  surface('party','party','Party'),surface('character','unit_list','Character'),surface('inventory','inventory','Inventory'),
  {id:'map',title:'Combat map',renderer:'coordinate_map',actions:[],data:{map_revision:1,map:{id:'practice',bounds:{min:[-10,-10,0],max:[20,20,10]},grid:{cell_size:5,unit:'ft'}},selection:{actor:'asha',target:'bram'},
   entities:participants.map((p,i)=>({id:p.id,position:[i*5,0,0],height:6,footprint:{kind:'box',half_extents:[.4,.4]},display:{label:p.data.name}})),geometry:[],terrain:[],zones:[],objectives:[]}}
 ]};
}

for(const width of [1440,960])test(`shared character inspection, keyboard and readable details at ${width}px`,{timeout:30000},async()=>{
 const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});
 try{
  const page=await browser.newPage({viewport:{width,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('https://inspection.test/**',async route=>{
   const name=new URL(route.request().url()).pathname.slice(1)||'app.html',file=path.join(frontend,name);
   if(!fs.existsSync(file))return route.abort();
   let body=fs.readFileSync(file);
   if(name==='app.js')body=body.toString().replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });",'')
    .replace('  // ---- Public API ----','window.inspectionQA={state,renderRulesetPanelProjection,gamePanels};\n  // ---- Public API ----');
   await route.fulfill({body,contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.png')?'image/png':'text/html'});
  });
  await page.goto('https://inspection.test/');
  await page.evaluate(projection=>{
   document.getElementById('connecting-overlay').classList.add('hidden');
   const frame=document.getElementById('session-frame');frame.style.display='flex';frame.classList.add('left-sidebar-collapsed');frame.style.setProperty('--sidebar-width','780px');
   const qa=window.inspectionQA;Object.assign(qa.state,{activeCampaignId:'inspection',activeSessionId:'gm',activeSessionRole:'dm',view:'session',activeSnapshot:{revision:1},bridge:{}});
   window.testProjection=projection;qa.renderRulesetPanelProjection(projection);
  },fixture());
  const inspect=page.getByRole('button',{name:'Inspect Bram',exact:true});
  await inspect.focus();await page.keyboard.press('Enter');
  await page.getByRole('button',{name:'Inspecting Bram',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Inspecting Bram');
  assert.equal(await page.locator('[data-character-actor]').count(),0,'Character has no independent selector when Party exists');
  const details=page.getByLabel('Details for Bram',{exact:true});await details.focus();await page.keyboard.press('Enter');
  assert.equal(await page.locator('[data-character-expansion="bram"]').evaluate(e=>e.open),true);
  await page.getByLabel('Details for Asha',{exact:true}).focus();await page.keyboard.press('Enter');
  for(const member of ['asha','bram'])assert.equal(await page.locator(`[data-character-expansion="${member}"]`).evaluate(e=>e.open),true);
  assert.equal(await page.evaluate(()=>window.inspectionQA.state.characterInspection.id),'bram','Expanding another card does not inspect it');
  for(const owner of [page.locator('[data-character-expansion="bram"]'),page.locator('[data-panel-id="character"]')]){
   for(const group of ['skills','aptitudes']){
    await owner.locator('summary').filter({hasText:new RegExp(`^${group}$`)}).focus();
    await page.keyboard.press('Enter');
   }
  }
  for(const expected of ['wizard','Level 3','Silver Dagger','lore','resolve','focus']){
   assert.ok((await page.locator('[data-character-expansion="bram"]').innerText()).includes(expected),expected);
   assert.ok((await page.locator('[data-panel-id="character"]').innerText()).includes(expected),expected);
  }
  const selection=await page.evaluate(()=>window.inspectionQA.state.tacticalSelections.map);
  assert.equal(selection.actor,'asha');assert.equal(selection.target,'bram');
  await page.evaluate(()=>window.inspectionQA.renderRulesetPanelProjection(window.testProjection));
  for(const member of ['asha','bram'])assert.equal(await page.locator(`[data-character-expansion="${member}"]`).evaluate(e=>e.open),true);
  await page.getByRole('button',{name:'Inventory',exact:true}).click();
  assert.match(await page.locator('[data-panel-id="inventory"]').innerText(),/Bram.*Silver Dagger/s);
  await page.getByRole('button',{name:'Character',exact:true}).click();
  await page.evaluate(()=>window.inspectionQA.gamePanels().changed({type:'hide',panelId:'party'}));
  await page.getByRole('button',{name:'Choose in Party',exact:true}).click();
  await page.getByRole('button',{name:'Inspecting Bram',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Inspecting Bram');
  assert.equal(await page.locator('[data-character-expansion="bram"]').evaluate(e=>e.open),true);
  await page.evaluate(()=>window.inspectionQA.gamePanels().changed({type:'hide',panelId:'character'}));
  assert.equal(await page.locator('[data-panel-id="character"]').count(),0,'Standalone Character can be hidden independently');
  assert.equal(await page.locator('[data-character-expansion="bram"]').evaluate(e=>e.open),true);
  await page.evaluate(()=>window.inspectionQA.gamePanels().changed({type:'show',panelId:'character'}));
  await page.getByRole('button',{name:'Character',exact:true}).click();
  assert.match(await page.locator('[data-panel-id="character"]').innerText(),/Bram.*Silver Dagger/s);
  await page.getByLabel('Details for Asha',{exact:true}).focus();await page.keyboard.press('Enter');
  assert.equal(await page.locator('[data-character-expansion="asha"]').evaluate(e=>e.open),false);
  assert.equal(await page.locator('[data-character-expansion="bram"]').evaluate(e=>e.open),true,'Collapsing Asha leaves Bram expanded');
  const retained=await page.evaluate(()=>window.inspectionQA.state.tacticalSelections.map);
  assert.equal(retained.actor,'asha');assert.equal(retained.target,'bram');
  const overflow=await page.locator('.ruleset-roster-panel .ruleset-panel-content').evaluate(e=>e.scrollWidth>e.clientWidth+1);
  assert.equal(overflow,false);assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});
