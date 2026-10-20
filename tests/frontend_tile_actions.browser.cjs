const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require('playwright');
const {fixture} = require('./fixtures/tile-table.cjs');
const frontend=path.resolve(__dirname,'../src-tauri/frontend');
test('tile selection is inert; current exact choice commits once; stale preview and rejection preserve state', {timeout:30000}, async()=>{
  const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});
  try {
    const page=await browser.newPage({viewport:{width:1400,height:950}}), errors=[];
    page.setDefaultTimeout(5000);
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('https://tiles.test/**',async route=>{
      const name=new URL(route.request().url()).pathname.slice(1)||'app.html',file=path.join(frontend,name);
      if(!fs.existsSync(file))return route.abort();
      let body=fs.readFileSync(file);
      if(name==='app.js')body=body.toString().replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });",'')
        .replace('  // ---- Public API ----','window.tileQA={state,renderRulesetPanelProjection,ensureSessionActivity,updateModelSwitchButtons};\n  // ---- Public API ----');
      await route.fulfill({body,contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.png')?'image/png':'text/html'});
    });
    await page.goto('https://tiles.test/');
    await page.evaluate(data=>{
      document.getElementById('connecting-overlay').classList.add('hidden');
      const frame=document.getElementById('session-frame');frame.style.display='flex';frame.classList.add('left-sidebar-collapsed');frame.style.setProperty('--sidebar-width','750px');
      window.projection={campaign_id:'tiles',ruleset_id:'tile-fixture',revision:1,
        player_control:{available:true,actors:[{id:'you',name:'You'}]},surfaces:[{id:'table',title:'Table',renderer:'piece_table',default_size:850,minimum_size:300,data,
          actions:[{id:'play',label:'Play',available:true,checks_required:true,params:{},bindings:{choice:'piece'}}]}]};
      window.calls={preview:[],execute:[],checks:[]};window.reject=false;window.hold=false;
      const bridge={
        snapshot:async()=>({revision:window.projection.revision}),
        campaign_panels:async()=>window.projection,
        campaign_index:async()=>[],
        player_action_availability:async args=>{window.calls.checks.push(args);return {campaign_id:args.campaign_id,revision:args.expected_revision,results:args.requests.map(()=>({available:true}))};},
        preview_panel_action:async args=>{window.calls.preview.push(args);if(window.hold)await new Promise(resolve=>window.release=resolve);return {confirmation_required:false,confirmation:{fixture:true}};},
        execute_panel_action:async args=>{window.calls.execute.push(args);if(window.reject)throw new Error('Illegal or stale choice');return {status:'committed'};},
      };
      Object.assign(window.tileQA.state,{activeCampaignId:'tiles',activeSessionId:'gm',activeSessionRole:'dm',view:'session',activeSnapshot:{revision:1},bridge});
      window.tileQA.ensureSessionActivity();
      window.tileQA.renderRulesetPanelProjection(window.projection);
    },fixture());
    const first=page.locator('[data-table-select]').first(), discard=page.locator('[data-table-discard]');
    await first.click();await page.waitForFunction(()=>!document.querySelector('[data-table-discard]').disabled);
    await page.evaluate(()=>window.tileQA.updateModelSwitchButtons());
    assert.equal(await discard.isEnabled(),true,'Model-control refresh preserves the selected piece binding');
    assert.equal(await page.evaluate(()=>window.calls.preview.length+window.calls.execute.length),0);
    await discard.click();
    await page.waitForFunction(()=>window.calls.execute.length===1 && !window.tileQA.state.panelIntentPending).catch(async error=>{
      throw new Error(JSON.stringify(await page.evaluate(()=>({calls:window.calls,
        pending:window.tileQA.state.panelIntentPending,revision:window.tileQA.state.panelProjection?.revision,
        status:document.getElementById('session-status-text')?.textContent,
        dataset:{...document.querySelector('[data-table-discard]')?.dataset}}))) + '\n' + JSON.stringify(errors),{cause:error});
    });
    const command=await page.evaluate(()=>window.calls.execute[0]);
    assert.deepEqual(command.selection,{piece:'discard-0'});assert.equal(command.surface_id,'table');
    assert.equal(await page.evaluate(()=>window.calls.preview.length),1);
    await page.locator('[data-table-select]').first().click();await page.waitForFunction(()=>!document.querySelector('[data-table-discard]').disabled);
    await page.evaluate(()=>window.hold=true);await page.locator('[data-table-discard]').click();
    await page.waitForFunction(()=>typeof window.release==='function');
    await page.locator('[data-table-select]').nth(1).click();
    assert.equal(await page.locator('[data-table-discard]').getAttribute('data-panel-piece-id'),'discard-0','Captured pending intent cannot be silently rebound');
    await page.evaluate(()=>{window.projection={...window.projection,revision:2};window.tileQA.renderRulesetPanelProjection(window.projection);window.release();window.hold=false;});
    await page.waitForFunction(()=>!window.tileQA.state.panelIntentPending);
    assert.equal(await page.evaluate(()=>window.calls.execute.length),1,'Stale preview cannot commit');
    await page.evaluate(()=>{window.reject=true;});
    await page.locator('[data-table-select]').nth(2).click();await page.waitForFunction(()=>!document.querySelector('[data-table-discard]').disabled);
    await page.locator('[data-table-discard]').click();
    await page.waitForFunction(()=>window.calls.execute.length===2 && !window.tileQA.state.panelIntentPending);
    assert.equal(await page.evaluate(()=>window.projection.revision),2);
    assert.deepEqual(await page.evaluate(()=>window.calls.execute[1].selection),{piece:'discard-2'});
    assert.equal(await page.evaluate(()=>window.calls.execute.length),2,'Rejected action is not retried under a new identity');
    assert.equal(await page.locator('[data-table-select]').count(),14);
    assert.deepEqual(errors,[]);
  } finally {await browser.close();}
});
