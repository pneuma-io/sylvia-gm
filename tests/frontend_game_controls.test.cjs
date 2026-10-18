const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/td-delivered-map-controls.json')));
function setup(){const context={window:{SylviaPanelHost:{create:()=>({snapshot:()=>null,effects:{preferences:()=>({visual:'off',volume:0,muted:true})}})}},document:{addEventListener(){}}};let source=fs.readFileSync(path.join(__dirname,'../src-tauri/frontend/app.js'),'utf8');
 source=source.replace('  // ---- Public API ----','window.controlsQA={state,panelMarkup,tacticalEdgeLineStyle,tacticalBoxStyle,tacticalDimensions,panelPlayerReason,panelSize,tacticalPreviewMarkup,panelActionCheckKey};\n  // ---- Public API ----');
 vm.runInNewContext(source,context);const api=context.window.controlsQA,projection=JSON.parse(JSON.stringify(fixture));api.state.panelProjection=projection;
 const surface=projection.surfaces.find(s=>s.renderer==='tactical_grid');api.state.tacticalSelections[surface.id]={map_revision:surface.data.map_revision,actor:'hex'};api.state.tacticalViews[surface.id]={previewActionId:'move'};
 return {api,surface,projection};}
test('Map shows declared action choices and one commit; character detail stays separate',()=>{const {api,surface}=setup();const before=JSON.stringify(surface.data),html=api.panelMarkup(surface);
 assert.match(html,/data-tactical-action="move"/);assert.match(html,/data-tactical-action="fire"/);assert.match(html,/Commit Move/);assert.match(html,/End team turn/);
 assert.doesNotMatch(html,/data-tactical-preview-action|Center actor|Actor:|map-actor-card|HACKER|Local Hacking/);
 assert.doesNotMatch(html,/data-tactical-zone|data-tactical-device/);assert.match(html,/aria-label="Map zoom">100%/);assert.match(html,/<summary>Map tools<\/summary><span class="map-revision">/);assert.equal(JSON.stringify(surface.data),before);
 const declared=new Set(surface.actions.map(a=>a.id));for(const match of html.matchAll(/data-tactical-action="([^"]+)"/g))assert.ok(declared.has(match[1]));
});
test('Required zone and device choices appear only for the selected declared action',()=>{const {api,surface}=setup();
 api.state.tacticalViews[surface.id].previewActionId='overwatch';let html=api.panelMarkup(surface);assert.match(html,/data-tactical-zone/);assert.doesNotMatch(html,/data-tactical-device/);
 api.state.tacticalViews[surface.id].previewActionId='fpv-strike';html=api.panelMarkup(surface);assert.match(html,/data-tactical-device/);assert.doesNotMatch(html,/data-tactical-zone/);
});
test('Delivered edge barriers align ink with actual contained grid boundaries, preserving exact physical extents',()=>{const {api,surface}=setup(),d=api.tacticalDimensions(surface.data);
 const wall=surface.data.geometry.find(x=>x.id==='full-wall'),door=surface.data.geometry.find(x=>x.id==='security-door'),object=surface.data.geometry.find(x=>x.id==='partial-cover');
 assert.equal(api.tacticalBoxStyle(wall.volume,d),'left:192px;top:160px;width:16px;height:96px');
 assert.equal(api.tacticalEdgeLineStyle(wall,d),'left:0px;top:0;height:100%;width:0');assert.equal(api.tacticalEdgeLineStyle(door,d),'left:0px;top:0;height:100%;width:0');
 assert.equal(api.tacticalEdgeLineStyle(object,d),null);const html=api.panelMarkup(surface);assert.match(html,/class="tactical-edge-line"/);
});
test('Horizontal, centred and free-standing edge geometry preserve position and orientation without snapping outside bounds',()=>{const {api}=setup(),d={lower:[0,0,0],cellSize:1};
 const item=(min,max)=>({occupies_cells:false,volume:{kind:'box',min,max}});
 assert.equal(api.tacticalEdgeLineStyle(item([2,4,0],[5,4.25,1]),d),'top:0px;left:0;width:100%;height:0');
 assert.equal(api.tacticalEdgeLineStyle(item([5.9,2,0],[6.1,5,1]),d),'left:3.1999999999999886px;top:0;height:100%;width:0');
 assert.equal(api.tacticalEdgeLineStyle(item([6.2,2,0],[6.4,5,1]),d),'left:3.200000000000017px;top:0;height:100%;width:0');
 assert.equal(api.tacticalBoxStyle({min:[2,4,0],max:[2.04,6,1]},d),'left:64px;top:128px;width:1.2800000000000011px;height:64px');
});
test('Fresh game default exposes distinct primary panels and keeps saved user layouts',()=>{const {projection}=setup(),workspace=require('../src-tauri/frontend/panel-workspace.js');
 const values=new Map();const storage={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value)};const store=workspace.createStore(storage);const layout=store.select({campaignId:'layout-proof',rulesetId:'td',surfaces:projection.surfaces});assert.equal(layout.sidebar.kind,'split');assert.equal(layout.sidebar.axis,'row');
 const changed=store.change({type:'hide',panelId:'operations.party'});const restored=store.select({campaignId:'layout-proof',rulesetId:'td',surfaces:projection.surfaces});assert.deepEqual(restored.hidden,['operations.party']);
});

test('Player-facing disabled reasons preserve the native reason while replacing technical actor terminology',()=>{const {api}=setup();assert.equal(api.panelPlayerReason('This actor is already in that stance.'),'This character is already in that stance.');});

test('Roster preferred height includes controlled entity-only characters',()=>{const {api,projection}=setup();const party=projection.surfaces.find(s=>s.renderer==='party');assert.equal(party.data.participants.length,1);assert.equal(api.panelSize(party),300);});

test('An admitted action without a declared preview makes the cost gap explicit',()=>{const {api,surface}=setup();const action=surface.actions.find(a=>a.id==='stance-standing');api.state.tacticalViews[surface.id].previewActionId=action.id;api.state.panelActionChecks.set(api.panelActionCheckKey(surface,action),{available:true,preview:null,map_preview:null});assert.match(api.tacticalPreviewMarkup(surface,api.state.tacticalViews[surface.id]),/Cost preview unavailable/);});


test('coordinate Tab entry retains Map tools and focus only in the same campaign, session and panel', async () => {
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--disable-gpu'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const frontend = path.join(__dirname, '../src-tauri/frontend');
    await page.route('https://map-tools.test/**', async route => {
      const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
      if (!['app.html', 'app.js', 'app.css', 'panel-workspace.js', 'panel-host.js',
          'shot-playback.js', 'assets/sylvia-portrait.png'].includes(name)) return route.abort();
      let body = fs.readFileSync(path.join(frontend, name));
      if (name === 'app.js') body = body.toString()
        .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
        .replace('  // ---- Public API ----',
          'window.mapToolsTest={state,gamePanels,renderRulesetPanelProjection};\n  // ---- Public API ----');
      await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript'
        : name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
    });
    await page.goto('https://map-tools.test/');
    const projection = structuredClone(fixture);
    projection.surfaces = projection.surfaces.filter(surface => surface.renderer === 'tactical_grid');
    projection.surfaces[0].renderer = 'coordinate_map';
    projection.surfaces[0].data.map.bounds.max[2] = 4;
    const panelId = projection.surfaces[0].id;
    await page.evaluate(projection => {
      const api = window.mapToolsTest;
      document.getElementById('connecting-overlay').style.display = 'none';
      document.getElementById('session-frame').style.display = 'flex';
      Object.assign(api.state, { view: 'session', activeCampaignId: projection.campaign_id,
        activeSessionId: 'map-tools-gm', activeSessionRole: 'dm', campaignIndexStatus: 'ready', bridge: {} });
      api.renderRulesetPanelProjection(projection);
      window.mapToolsProjection = projection;
    }, projection);
    const panel = page.locator('section[data-panel-id="' + panelId + '"]');
    const details = panel.locator('.map-detail-controls');
    await details.locator(':scope > summary').click();
    for (const [axis, value] of [[0, '1'], [1, '1.5'], [2, '0.25']]) {
      const field = panel.locator('[data-coordinate-axis="' + axis + '"]');
      await field.fill(value);
      await field.press('Tab');
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
      assert.equal(await details.evaluate(node => node.open), true, 'Map tools stays open after committing an axis');
      if (axis < 2) assert.equal(await page.evaluate(() => document.activeElement.dataset.coordinateAxis),
        String(axis + 1), 'Tab reaches the next replacement coordinate field');
    }
    assert.deepEqual(await page.evaluate(id => window.mapToolsTest.state.tacticalSelections[id].aim_point, panelId), [1, 1.5, 0.25]);
    assert.deepEqual(await page.evaluate(() => window.mapToolsProjection), projection,
      'Editing intent does not change authoritative projection or geometry');
    assert.equal(await page.evaluate(() => Boolean(window.mapToolsTest.state.actionSequence)), false);

    await page.setViewportSize({ width: 420, height: 900 });
    const height = panel.locator('[data-coordinate-axis="2"]');
    await height.focus();
    await page.evaluate(() => window.mapToolsTest.renderRulesetPanelProjection({ ...window.mapToolsProjection, revision: 99 }));
    assert.equal(await details.evaluate(node => node.open), true);
    assert.equal(await page.evaluate(() => document.activeElement.dataset.coordinateAxis), '2');
    assert.equal(await height.inputValue(), '0.25');
    await height.press('Shift+Tab');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.coordinateAxis), '1');

    await details.locator(':scope > summary').click();
    await page.evaluate(() => window.mapToolsTest.renderRulesetPanelProjection(window.mapToolsProjection));
    assert.equal(await details.evaluate(node => node.open), false, 'A deliberately closed disclosure stays closed');

    for (const boundary of ['session', 'campaign', 'projection']) {
      const outcome = await page.evaluate(boundary => {
        const api = window.mapToolsTest;
        const projection = structuredClone(window.mapToolsProjection);
        Object.assign(api.state, { activeSessionId: 'map-tools-gm', activeCampaignId: projection.campaign_id });
        api.renderRulesetPanelProjection(projection);
        const originalFrame = window.requestAnimationFrame;
        const callbacks = [];
        window.requestAnimationFrame = callback => { callbacks.push(callback); return callbacks.length; };
        try {
          const details = document.querySelector('.map-detail-controls');
          details.open = true;
          const input = details.querySelector('[data-coordinate-axis="0"]');
          input.focus();
          input.value = '2.5';
          input.dispatchEvent(new Event('change', { bubbles: true }));
          if (boundary === 'session') {
            api.state.activeSessionId = 'another-gm';
            api.renderRulesetPanelProjection(projection);
          } else if (boundary === 'campaign') {
            api.state.activeCampaignId = 'another-campaign';
          } else api.renderRulesetPanelProjection({ ...projection, revision: 100 });
          const currentField = document.querySelector('[data-coordinate-axis="0"]');
          callbacks.forEach(callback => callback(performance.now()));
          return { queued: callbacks.length,
            nodeRetained: currentField === document.querySelector('[data-coordinate-axis="0"]') };
        } finally {
          window.requestAnimationFrame = originalFrame;
        }
      }, boundary);
      assert.ok(outcome.queued > 0, 'The real change handler deferred its replacement');
      assert.equal(outcome.nodeRetained, true, 'A stale callback must not replace the current DOM across ' + boundary);
    }

    for (const boundary of ['session', 'campaign', 'panel']) {
      await page.evaluate(boundary => {
        const api = window.mapToolsTest;
        const projection = structuredClone(window.mapToolsProjection);
        api.state.activeSessionId = 'map-tools-gm';
        api.state.activeCampaignId = projection.campaign_id;
        api.renderRulesetPanelProjection(projection);
        const details = document.querySelector('.map-detail-controls');
        details.open = true;
        details.querySelector('[data-coordinate-axis="1"]').focus();
        if (boundary === 'session') api.state.activeSessionId = 'another-gm';
        if (boundary === 'campaign') projection.campaign_id = api.state.activeCampaignId = 'another-campaign';
        if (boundary === 'panel') projection.surfaces[0].id = 'another-map';
        api.renderRulesetPanelProjection(projection);
      }, boundary);
      assert.equal(await page.locator('.map-detail-controls').evaluate(node => node.open), false,
        'Disclosure state must not cross ' + boundary + ' identity');
      assert.equal(await page.evaluate(() => document.activeElement.hasAttribute('data-coordinate-axis')), false,
        'Focus must not cross ' + boundary + ' identity');
    }
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
