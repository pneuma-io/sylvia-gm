const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const {fixture, face} = require('./fixtures/tile-table.cjs');
for (const width of [750,280]) test(`tile artwork, ponds and local keyboard selection at ${width}px`, {timeout:30000}, async()=>{
  const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});
  try {
    const page=await browser.newPage({viewport:{width:Math.max(820,width+40),height:1000}});
    await page.setContent(`<html><head><style>body{margin:20px;background:#10211e}main{width:${width}px}</style><style>${fs.readFileSync(path.join(frontend,'tile-table.css'),'utf8')}</style></head><body><main></main></body></html>`);
    for (const name of ['tile-faces.js','tile-table.js','piece-table.js']) await page.addScriptTag({content:fs.readFileSync(path.join(frontend,name),'utf8')});
    await page.evaluate(data=>{
      window.changed=0;window.busy=false;
      document.querySelector('main').innerHTML=window.SylviaPieceTable.render(data,[{id:'play',label:'Play',available:true,bindings:{choice:'piece'}}]);
      window.SylviaTileTable.attach(document.querySelector('main'),()=>{window.changed++;document.querySelector('[data-table-discard]').disabled=false;},()=>!window.busy);
    },fixture());
    assert.equal(await page.locator('.table-seat').count(),4);
    assert.equal(await page.locator('.table-tile-back').count(),39);
    assert.equal(await page.locator('.table-rack .tile-drawn').count(),1);
    assert.equal(await page.locator('.table-pond .tile-sideways').count(),4);
    assert.equal(await page.locator('.table-pond .table-tile-slot').count(),28,'Called discards have no second visible tile');
    assert.equal(await page.locator('.table-meld .tile-sideways').count(),1);
    assert.equal(await page.getByRole('button',{name:'Win with your drawn tile',exact:true}).count(),1);
    const first=page.locator('[data-table-select]').first();await first.focus();await page.keyboard.press('Enter');
    assert.equal(await first.getAttribute('aria-pressed'),'true');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('[data-table-select]').nth(1).getAttribute('aria-pressed'),'true');
    assert.equal(await page.evaluate(()=>window.changed),2,'Selection only changes the local intent');
    await page.evaluate(()=>window.busy=true);await page.locator('[data-table-select]').nth(2).click();
    assert.equal(await page.locator('[data-table-discard]').getAttribute('data-panel-piece-id'),'discard-1','Busy commit locks local selection');
    const box=await page.locator('.table-board').boundingBox();
    for(const control of await page.locator('[data-table-select]').all()) {
      const tile=await control.boundingBox();assert.ok(tile.x>=box.x && tile.x+tile.width<=box.x+box.width+1,'Player tiles remain in board');
    }
    const main=await page.locator('main').evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth}));
    assert.equal(main.scroll,main.width,'Narrow panel has no horizontal overflow');
    assert.doesNotMatch(await page.locator('.table-board').innerText(),/Diagnostic label/,'Codes are not primary tile display');
    if(process.env.SYLVIA_TILE_EVIDENCE){fs.mkdirSync(process.env.SYLVIA_TILE_EVIDENCE,{recursive:true});await page.locator('main').screenshot({path:path.join(process.env.SYLVIA_TILE_EVIDENCE,`component-${width}.png`)});}
  } finally {await browser.close();}
});

test('all 34 faces and red fives are original bounded artwork; concealed pieces never render faces', async()=>{
  const vm=require('node:vm'),context={};
  for(const name of ['tile-faces.js','tile-table.js','piece-table.js'])vm.runInNewContext(fs.readFileSync(path.join(frontend,name),'utf8'),context);
  for(let kind=0;kind<34;kind++)assert.match(context.SylviaTileFaces.render(face(kind)),/<svg/);
  for(const kind of [4,13,22])assert.match(context.SylviaTileFaces.render(face(kind,true)),/table-tile-red/);
  assert.equal(context.SylviaTileFaces.render(face(34)),'');
  const data=fixture();data.areas[1].groups[0].pieces=[{label:'SECRET',face:face(33),action_id:'play'}];
  const html=context.SylviaPieceTable.render(data,[]);
  assert.doesNotMatch(html,/SECRET/);assert.doesNotMatch(html,/data-panel-action-id/,'No unavailable contextual actions');
});
