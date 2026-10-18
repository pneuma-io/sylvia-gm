const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');
const {chromium}=require('playwright');
const frontend=path.resolve(__dirname,'../src-tauri/frontend');
for(const renderer of ['coordinate_map','tactical_grid'])for(const [width,height,sidebar]of [[1400,900,700],[960,640,400],[960,480,400]]){
 test(`${renderer}: readiness and toolbar remain separate at ${width}x${height}`,{timeout:30000},async()=>{
  const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined,args:['--disable-gpu']});
  try{
   const page=await browser.newPage({viewport:{width,height}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('https://sylvia-header.test/**',async route=>{
    const name=new URL(route.request().url()).pathname.slice(1)||'app.html';
    if(!['app.html','app.css','app.js','panel-workspace.js','shot-playback.js','panel-host.js','campaign-plan-browser.js','assets/sylvia-portrait.png'].includes(name))return route.abort();
    let body=fs.readFileSync(path.join(frontend,name));
    if(name==='app.js')body=body.toString().replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });",'').replace('  // ---- Public API ----','window.headerTest={state,renderRulesetPanelProjection,updateTacticalSelection};\n  // ---- Public API ----');
    await route.fulfill({body,contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.png')?'image/png':'text/html'});
   });
   await page.goto('https://sylvia-header.test/');
   await page.evaluate(({renderer,sidebar})=>{
    document.getElementById('connecting-overlay').classList.add('hidden');const frame=document.getElementById('session-frame');frame.style.display='flex';frame.classList.add('left-sidebar-collapsed');frame.style.setProperty('--sidebar-width',`${sidebar}px`);
    const api=window.headerTest;Object.assign(api.state,{activeCampaignId:'header',activeSessionId:'header-dm',activeSessionRole:'dm',view:'session',activeSnapshot:{revision:47}});
    const entity={name:'Journey Hero',hp:20,max_hp:20,bonus_action_available:true,reaction_available:true,resources:{},conditions:[]};
    const map={id:'map',title:'Combat map',renderer,minimum_size:96,default_size:440,actions:[],data:{map_revision:1,map:{id:'practice',bounds:{min:[-20,-20,0],max:[30,30,20]},grid:{cell_size:5,unit:'ft',origin:[-20,-20]}},selection:{actor:'hero'},entities:[{id:'hero',position:[0,0,0],height:6,facing:[1,0],footprint:{kind:'box',half_extents:[.4,.4]},display:{label:'Journey Hero'}}],geometry:[],terrain:[],zones:[],objectives:[]}};
    const projection={campaign_id:'header',revision:47,shot_effects:[],player_control:{available:true,actors:[{id:'hero',name:'Journey Hero'}]},surfaces:[
     {id:'party',title:'Party',renderer:'party',actions:[],data:{participants:[{id:'hero',kind:'character',data:{name:'Journey Hero',level:2}}],entities:{hero:entity}}},
     {id:'encounter',title:'Encounter',renderer:'encounter',actions:[],data:{combat:{encounter_active:true,turn_active:true,active_actor:'hero',action_available:false,movement_remaining:30}}},map]};
    api.renderRulesetPanelProjection(projection);api.updateTacticalSelection(map,{actor:'hero'});api.renderRulesetPanelProjection(projection);
   },{renderer,sidebar});
   const panel=page.locator('.ruleset-map-panel');await panel.scrollIntoViewIfNeeded();
   const geometry=await panel.evaluate(node=>{
    const rect=q=>{const r=node.querySelector(q).getBoundingClientRect();return {top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
    const content=node.querySelector('.ruleset-panel-content');return {strip:rect('.map-turn-strip'),readiness:rect('[data-action-readiness]'),toolbar:rect('.tactical-toolbar'),map:rect('.tactical-scroll'),clientWidth:content.clientWidth,scrollWidth:content.scrollWidth};
   });
   assert.ok(geometry.readiness.bottom<=geometry.strip.bottom+.5,`Readiness escaped its header: ${JSON.stringify(geometry)}`);
   assert.ok(geometry.strip.bottom<=geometry.toolbar.top+.5,`Header overlaps toolbar: ${JSON.stringify(geometry)}`);
   assert.ok(geometry.toolbar.bottom<=geometry.map.top+.5,`Toolbar overlaps map: ${JSON.stringify(geometry)}`);
   assert.ok(geometry.map.height>=119,`Declared120px map minimum shrank: ${JSON.stringify(geometry)}`);
   assert.ok(geometry.scrollWidth<=geometry.clientWidth+1,`Map controls overflow horizontally: ${JSON.stringify(geometry)}`);
   assert.match(await panel.locator('[data-action-readiness]').innerText(),/Action spent.*Bonus ready.*Reaction ready.*Move remaining 30/);
   await panel.getByRole('button',{name:'Cover info',exact:true}).click();assert.deepEqual(errors,[]);
  }finally{await browser.close()}
 });
}
