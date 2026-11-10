// Renderer composition only: public projections and scripted managed-asset receipts.
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),{chromium}=require('playwright');
const frontend=path.resolve(__dirname,'../src-tauri/frontend');

test('one saved crop is reused in Party, Character, Map and labelled dialogue',{timeout:30000},async()=>{
 const browser=await chromium.launch({headless:true,
  ...(process.env.SYLVIA_TEST_CHROMIUM?{executablePath:process.env.SYLVIA_TEST_CHROMIUM}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('https://image-surfaces.test/**',async route=>{
   const name=new URL(route.request().url()).pathname.slice(1)||'app.html',file=path.join(frontend,name);
   if(!fs.existsSync(file))return route.abort();
   let body=fs.readFileSync(file);
   if(name==='app.js')body=body.toString()
    .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });",'')
    .replace('  // ---- Public API ----','window.artSurfaceQA={state,renderRulesetPanelProjection,dispatchPanelWindowRequest};\n  // ---- Public API ----');
   await route.fulfill({body,contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html'});
  });
  await page.goto('https://image-surfaces.test/');
  await page.evaluate(()=>{
   const portrait='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC';
   const projection={campaign_id:'image-surfaces',ruleset_id:'fixture',revision:1,
    player_control:{available:true,focused_actor_id:'bram',actors:[{id:'bram',name:'Bram'}]},
    surfaces:[...['party','unit_list'].map((renderer,index)=>({id:index?'character':'party',renderer,title:index?'Character':'Party',actions:[],
     data:{participants:[{id:'bram',kind:'character',data:{name:'Bram',party_role:'companion'}}],entities:{bram:{hp:10,max_hp:10}}}})),
     {id:'map',renderer:'coordinate_map',title:'Map',actions:[],data:{map_revision:1,actor_reference:'participant_ref',
      map:{id:'practice',bounds:{min:[0,0,0],max:[10,10,3]},grid:{cell_size:1,unit:'m'}},
      entities:[{id:'bram-body',actor_ref:'bram',position:[2,2,0],height:1.8,footprint:{kind:'circle',radius:.4},display:{label:'Bram'}}],
      geometry:[],terrain:[],zones:[],objectives:[],selection:{actor:'bram-body'}}}]};
   const art={settings:{mode:'off'},targets:[{id:'bram',kind:'character',name:'Bram'}],requests:[],
    selections:[{target_id:'bram',purpose:'portrait',sha256:'a'.repeat(64),frame:{x:.42,y:.31,zoom:1.5}}]};
   document.getElementById('connecting-overlay').classList.add('hidden');
   const frame=document.getElementById('session-frame');frame.style.display='flex';frame.classList.add('left-sidebar-collapsed');
   Object.assign(window.artSurfaceQA.state,{activeCampaignId:'image-surfaces',activeSessionId:'gm',activeSessionRole:'dm',
    view:'session',activeSnapshot:{revision:1},bridge:{image_art:async params=>params.action==='asset'?{data_url:portrait}:art}});
   window.artSurfaceQA.renderRulesetPanelProjection(projection);
  });
  await page.locator('.tactical-entity .image-art-portrait').waitFor({state:'attached'});
  await page.evaluate(()=>{
   const dialogue=document.createElement('div');
   dialogue.innerHTML=window.SylviaGMFrontend.renderResponseBlocks([
    {kind:'npc_dialogue',speaker_label:'Bram',text:'Ready.'},
    {kind:'npc_dialogue',speaker_label:'Unknown speaker',text:'Wait.'}], '');
   document.body.appendChild(dialogue);
  });
  for(const selector of ['.ruleset-roster-panel','.ruleset-character-panel','.tactical-entity','.npc-dialogue']){
   const image=page.locator(`${selector} .image-art-portrait img`).first();
   assert.ok(await image.count(),selector);
   const style=await image.getAttribute('style');
   assert.match(style,/object-position:42% 31%/);assert.match(style,/scale\(1.5\)/);
  }
  assert.equal(await page.locator('.npc-dialogue .image-art-portrait').count(),1);
  const reads=await page.evaluate(async()=>{
   const scope={campaign_id:'image-surfaces',session_id:'gm',current:()=>true};
   const call=window.artSurfaceQA.dispatchPanelWindowRequest;
   const read=await call('image_art',{action:'view',session_id:'gm'},scope);
   let mutation=false,foreign=false;
   try{await call('image_art',{action:'generate',session_id:'gm'},scope);}catch(_){mutation=true;}
   try{await call('image_art',{action:'asset',session_id:'foreign',sha256:'a'.repeat(64)},scope);}catch(_){foreign=true;}
   return {selected:read.selections.length,mutation,foreign};
  });
  assert.deepEqual(reads,{selected:1,mutation:true,foreign:true});
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});
