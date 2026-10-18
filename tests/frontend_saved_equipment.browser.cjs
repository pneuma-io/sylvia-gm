const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'src-tauri/frontend');
const record = JSON.parse(fs.readFileSync(path.join(root, 'rulesets/srd-lite/package-record.json'), 'utf8'));
const declaration = record.ui_surfaces.find(surface => surface.renderer === 'inventory');

test('saved physical copies and authored versatile grips render and bind without mechanics', { timeout:30000 }, async () => {
 const browser = await chromium.launch({ headless:true, args:['--disable-gpu'], channel:process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':undefined) });
 try {
  const page = await browser.newPage();
  await page.route('https://sylvia-equipment.test/**',async route => {
   const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
   if (!['app.html','app.css','app.js','panel-workspace.js','shot-playback.js','panel-host.js','assets/sylvia-portrait.png'].includes(name)) return route.abort();
   let body = fs.readFileSync(path.join(frontend,name));
   if (name==='app.js') body = body.toString().replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });",'').replace('  // ---- Public API ----','window.equipmentQA={state,renderPreparedEquipment,inventoryEquipmentSelection};\n  // ---- Public API ----');
   await route.fulfill({body,contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.png')?'image/png':'text/html'});
  });
  await page.goto('https://sylvia-equipment.test/');
  const policy = declaration.source.fields.equipment.value;
  for (const specimen of [
   {weapon:'quarterstaff',quantity:1,main:1,off:1,refs:null},
   {weapon:'quarterstaff',quantity:2,main:1,off:2,refs:{main_hand:1,off_hand:2}},
   {weapon:'dagger',quantity:2,main:1,off:2,refs:null},
  ]) {
   const result = await page.evaluate(({policy,declaration,specimen}) => {
    const participant={id:'hero',data:{name:'Equipment fixture'}};
    const entity={inventory:[specimen.weapon],inventory_quantities:{[specimen.weapon]:specimen.quantity},weapon_sets:{format:1,active_set:'melee',melee:{main_hand:specimen.weapon,off_hand:specimen.weapon},ranged:{main_hand:null,off_hand:null}}};
    if(specimen.refs) entity.weapon_copy_refs={melee:specimen.refs};
    const surface={...declaration,data:{equipment:policy}};
    window.equipmentQA.state.equipmentDrafts=new Map();
    const host=document.createElement('div');host.innerHTML=window.equipmentQA.renderPreparedEquipment(participant,entity,surface);
    const main=host.querySelector('[data-equipment-set="melee"][data-equipment-slot="main_hand"]').value;
    const off=host.querySelector('[data-equipment-set="melee"][data-equipment-slot="off_hand"]').value;
    const action=declaration.actions.find(action=>action.id==='prepare-melee');
    return {main,off,selection:window.equipmentQA.inventoryEquipmentSelection(surface,action,'hero')};
   },{policy,declaration,specimen});
   assert.deepEqual(JSON.parse(result.main),[specimen.weapon,specimen.main]);
   assert.deepEqual(JSON.parse(result.off),[specimen.weapon,specimen.off]);
   assert.deepEqual(result.selection.declared_action,[{set_name:'melee',main_hand:specimen.weapon,off_hand:specimen.weapon,main_copy:specimen.main,off_copy:specimen.off}]);
  }
 } finally { await browser.close(); }
});
