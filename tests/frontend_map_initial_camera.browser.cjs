const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

test('negative-bounds empty maps focus arriving bodies and preserve user camera through refresh and reopen', {timeout: 45000}, async () => {
  const browser = await chromium.launch({headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined,
    args: ['--disable-gpu']});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
    await page.route('https://sylvia-map-focus.test/**', async route => {
      const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
      if (!['app.html','app.js','app.css','panel-host.js','panel-workspace.js','shot-playback.js','campaign-plan-browser.js','assets/sylvia-portrait.png'].includes(name)) return route.abort();
      let body = fs.readFileSync(path.join(frontend, name));
      if (name === 'app.js') body = body.toString().replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
        .replace('  // ---- Public API ----', 'window.mapFocusTest={state,renderRulesetPanelProjection};\n  // ---- Public API ----');
      await route.fulfill({body,contentType: name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.png')?'image/png':'text/html'});
    });
    const load = async (populated) => {
      await page.evaluate(populated => {
        document.getElementById('connecting-overlay').classList.add('hidden');
        document.getElementById('session-frame').style.display='flex';
        const api=window.mapFocusTest;
        Object.assign(api.state,{activeCampaignId:'negative-bounds',activeSessionId:'map-session',activeSessionRole:'dm',activeSnapshot:{revision:1},view:'session'});
        window.mapFocusProjection={campaign_id:'negative-bounds',ruleset_id:'map-focus',revision:1,shot_effects:[],
          player_control:{available:false,actors:[]},surfaces:[{id:'map',title:'Map',renderer:'coordinate_map',minimum_size:320,default_size:620,
            actions:[],data:{map_revision:1,map:{id:'negative-scene',bounds:{min:[-100,-100,-100],max:[100,100,100]},grid:{cell_size:5,unit:'ft',origin:[-100,-100]}},
              selection:{},geometry:[],terrain:[],zones:[],objectives:[],entities:populated?[{id:'hero',position:[0,0,0],height:6,footprint:{kind:'box',half_extents:[1,1]},display:{label:'Hero'}},{id:'guard',position:[5,0,0],height:6,footprint:{kind:'box',half_extents:[1,1]},display:{label:'Guard'}}]:[]}}]};
        api.renderRulesetPanelProjection(window.mapFocusProjection);
      },populated);
    };
    const camera = () => page.locator('.tactical-scroll').evaluate(node => ({left:node.scrollLeft,top:node.scrollTop}));
    await page.goto('https://sylvia-map-focus.test/');await load(false);
    assert.equal(await page.locator('[data-tactical-entity]').count(),0);
    assert.equal(await page.evaluate(()=>window.mapFocusTest.state.tacticalViews.map.initialFocus),true);
    await load(true);
    const bodies = await page.locator('.tactical-scroll').evaluate(node=>{
      const view=node.getBoundingClientRect();return [...node.querySelectorAll('[data-tactical-entity]')].map(body=>{
        const r=body.getBoundingClientRect();return r.right>view.left&&r.left<view.right&&r.bottom>view.top&&r.top<view.bottom;
      });
    });
    assert.deepEqual(bodies,[true,true]);
    await page.locator('[data-tactical-zoom="in"]').click();
    await page.locator('.tactical-scroll').evaluate(node=>{node.scrollLeft=400;node.scrollTop=350});
    await page.waitForFunction(()=>window.mapFocusTest.state.tacticalViews.map.scrollLeft===400&&window.mapFocusTest.state.tacticalViews.map.scrollTop===350);
    const saved=await camera();const zoom=await page.evaluate(()=>window.mapFocusTest.state.tacticalViews.map.zoom);
    await load(true);assert.deepEqual(await camera(),saved);
    await page.reload();await load(true);assert.deepEqual(await camera(),saved);
    assert.equal(await page.evaluate(()=>window.mapFocusTest.state.tacticalViews.map.zoom),zoom);
    // A deliberate pan in an empty scene must remain the user's camera choice.
    await page.evaluate(()=>localStorage.clear());await page.reload();await load(false);
    await page.locator('.tactical-scroll').evaluate(node=>{node.scrollLeft=200;node.scrollTop=180});
    await page.waitForFunction(()=>window.mapFocusTest.state.tacticalViews.map.initialFocus===false);
    const chosen=await camera();await load(true);assert.deepEqual(await camera(),chosen);
  } finally {await browser.close();}
});

for (const renderer of ['coordinate_map', 'tactical_grid']) {
test(`zoom preserves the chosen world centre and visible bodies (${renderer})`, {timeout: 30000}, async () => {
  const browser = await chromium.launch({headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined,
    args: ['--disable-gpu']});
  try {
    const page = await browser.newPage({viewport: {width: 1402, height: 932}});
    await page.route('https://sylvia-map-zoom.test/**', async route => {
      const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
      if (!['app.html','app.js','app.css','panel-host.js','panel-workspace.js','shot-playback.js','campaign-plan-browser.js','assets/sylvia-portrait.png'].includes(name)) return route.abort();
      let body = fs.readFileSync(path.join(frontend, name));
      if (name === 'app.js') body = body.toString().replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
        .replace('  // ---- Public API ----', 'window.mapZoomTest={state,renderRulesetPanelProjection};\n  // ---- Public API ----');
      await route.fulfill({body,contentType: name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.png')?'image/png':'text/html'});
    });
    await page.goto('https://sylvia-map-zoom.test/');
    await page.evaluate(renderer => {
      document.getElementById('connecting-overlay').classList.add('hidden');
      document.getElementById('session-frame').style.display='flex';
      const api=window.mapZoomTest;
      Object.assign(api.state,{activeCampaignId:'zoom-camera',activeSessionId:'zoom-session',activeSessionRole:'dm',activeSnapshot:{revision:1},view:'session'});
      api.renderRulesetPanelProjection({campaign_id:'zoom-camera',ruleset_id:'zoom-camera',revision:1,shot_effects:[],
        player_control:{available:false,actors:[]},surfaces:[{id:'map',title:'Map',renderer,minimum_size:320,default_size:620,
          actions:[],data:{map_revision:1,map:{id:'negative-scene',bounds:{min:[-100,-100,-5],max:[100,100,5]},
            grid:{cell_size:5,unit:'ft',origin:[-100,-100]}},selection:{},geometry:[],terrain:[],zones:[],objectives:[],
            entities:[{id:'hero',position:[0,0,0],height:6,footprint:{kind:'box',half_extents:[1,1]},display:{label:'Hero'}},
              {id:'guard',position:[7.5,0,0],height:6,footprint:{kind:'box',half_extents:[1,1]},display:{label:'Guard'}}]}}]});
    },renderer);
    const camera = () => page.locator('.tactical-scroll').evaluate(node => {
      const viewport=node.getBoundingClientRect(), world=node.querySelector('.tactical-world').getBoundingClientRect();
      return [(viewport.left+node.clientLeft+node.clientWidth/2-world.left)/world.width,
        (viewport.top+node.clientTop+node.clientHeight/2-world.top)/world.height];
    });
    const visible = () => page.locator('.tactical-scroll').evaluate(node => {
      const view=node.getBoundingClientRect();
      return [...node.querySelectorAll('[data-tactical-entity]')].map(body => {
        const r=body.getBoundingClientRect();return r.right>view.left&&r.left<view.right&&r.bottom>view.top&&r.top<view.bottom;
      });
    });
    await page.locator('.tactical-scroll').evaluate(node => {node.scrollLeft+=37;node.scrollTop+=23;});
    await page.waitForFunction(() => {
      const node=document.querySelector('.tactical-scroll'), view=window.mapZoomTest.state.tacticalViews.map;
      return view.scrollLeft===node.scrollLeft&&view.scrollTop===node.scrollTop;
    });
    assert.deepEqual(await visible(),[true,true]);
    const before=await camera();
    for (const direction of ['in','out']) {
      await page.locator(`[data-tactical-zoom="${direction}"]`).click();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const after=await camera();
      assert(after.every((value,axis)=>Math.abs(value-before[axis])<0.001),
        `Zoom ${direction} moved the world centre from ${before} to ${after}`);
      assert.deepEqual(await visible(),[true,true],`Zoom ${direction} must keep the framed actors visible`);
    }
  } finally {await browser.close();}
});
}
