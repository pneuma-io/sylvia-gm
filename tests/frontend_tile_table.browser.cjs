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
    await page.setContent(`<html><head><style>${fs.readFileSync(path.join(frontend,'app.css'),'utf8')}</style><style>body{margin:20px}main{width:${width}px}</style><style>${fs.readFileSync(path.join(frontend,'tile-table.css'),'utf8')}</style></head><body><main></main></body></html>`);
    for (const name of ['tile-faces.js','tile-table.js','piece-table.js']) await page.addScriptTag({content:fs.readFileSync(path.join(frontend,name),'utf8')});
    await page.evaluate(data=>{
      window.changed=0;window.busy=false;
      document.querySelector('main').innerHTML=window.SylviaPieceTable.render(data,[{id:'play',label:'Play',available:true,bindings:{choice:'piece'}}]);
      window.SylviaTileTable.attach(document.querySelector('main'),()=>{window.changed++;document.querySelector('[data-table-discard]').disabled=false;},()=>!window.busy);
    },fixture());
    assert.equal(await page.locator('.table-seat').count(),4);
    assert.equal(await page.locator('.table-tile-back').count(),39);
    assert.equal(await page.locator('.table-rack .tile-drawn').count(),1);
    assert.equal((await page.locator('.tile-drawn button').getAttribute('aria-label')).match(/Drawn tile/g).length,1);
    assert.equal(await page.locator('.table-pond .tile-sideways').count(),4);
    assert.equal(await page.locator('.table-pond .table-tile-slot').count(),28,'Called discards have no second visible tile');
    assert.equal(await page.locator('.table-meld .tile-sideways').count(),1);
    assert.equal(await page.getByRole('group',{name:'Added kan, called from across',exact:true}).count(),1);
    const called=await page.locator('.table-meld .tile-sideways .table-tile').boundingBox();
    const added=await page.locator('.table-meld .tile-stacked .table-tile').boundingBox();
    assert.ok(Math.abs(called.y+called.height/2-added.y-added.height/2)<1,'Added kan aligns with its called tile in the rotated rack');
    assert.ok(Math.abs(called.x-added.x)>called.width*.8 && Math.abs(called.x-added.x)<called.width*1.2,'Added kan stacks one tile above its called tile');
    assert.equal(await page.getByRole('button',{name:'Win with your drawn tile',exact:true}).count(),1);
    const first=page.locator('[data-table-select]').first();await first.focus();await page.keyboard.press('Enter');
    assert.equal(await first.getAttribute('aria-pressed'),'true');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('[data-table-select]').nth(1).getAttribute('aria-pressed'),'true');
    assert.equal(await page.evaluate(()=>window.changed),2,'Selection only changes the local intent');
    await page.evaluate(()=>window.busy=true);await page.locator('[data-table-select]').nth(2).click();
    assert.equal(await page.locator('[data-table-discard]').getAttribute('data-panel-piece-id'),'discard-1','Busy commit locks local selection');
    const box=await page.locator('.table-board').boundingBox();
    const occupied=await page.locator('.table-rack, .table-pond').evaluateAll(nodes=>nodes.map(node=>{
      const b=node.getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom};
    }));
    for(const badge of await page.locator('.table-seat-badge').all()){
      const b=await badge.boundingBox();
      assert.ok(occupied.every(r=>Math.min(b.x+b.width,r.right)-Math.max(b.x,r.left)<=.5 ||
        Math.min(b.y+b.height,r.bottom)-Math.max(b.y,r.top)<=.5),'Seat score badges stay clear of racks and ponds');
    }
    for(const control of await page.locator('[data-table-select]').all()) {
      const tile=await control.boundingBox();assert.ok(tile.x>=box.x && tile.x+tile.width<=box.x+box.width+1,'Player tiles remain in board');
    }
    const main=await page.locator('main').evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth}));
    assert.equal(main.scroll,main.width,'Narrow panel has no horizontal overflow');
    assert.doesNotMatch(await page.locator('.table-board').innerText(),/Diagnostic label/,'Codes are not primary tile display');
    if(process.env.SYLVIA_TILE_EVIDENCE){fs.mkdirSync(process.env.SYLVIA_TILE_EVIDENCE,{recursive:true});await page.locator('main').screenshot({path:path.join(process.env.SYLVIA_TILE_EVIDENCE,`component-${width}.png`)});}
    await page.locator('main').evaluate(el=>el.style.height='460px');
    const fit=await page.evaluate(()=>{
      const box=selector=>{const b=document.querySelector(selector).getBoundingClientRect();return {width:b.width,height:b.height,bottom:b.bottom}};
      return {host:box('main'),board:box('.table-board'),deck:box('.table-command-deck')};
    });
    assert.ok(Math.abs(fit.board.width-fit.board.height)<1,'Tiles stay on a square table when height is constrained');
    assert.ok(fit.deck.bottom<=fit.host.bottom+1,'The action deck stays visible in a short panel');
    if(process.env.SYLVIA_TILE_EVIDENCE)await page.locator('main').screenshot({path:path.join(process.env.SYLVIA_TILE_EVIDENCE,`component-short-${width}.png`)});
  } finally {await browser.close();}
});

test('table follows existing theme tokens live in docked and pop-out documents, preserving tile faces and intent', {timeout:30000}, async()=>{
  const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});
  try {
    const pages=[];
    for(const host of ['docked','popout']){
      const page=await browser.newPage({viewport:{width:850,height:1000}});pages.push(page);
      await page.setContent(`<html><head><style>${fs.readFileSync(path.join(frontend,'app.css'),'utf8')}</style><style>main{width:750px}</style><style>${fs.readFileSync(path.join(frontend,'tile-table.css'),'utf8')}</style></head><body class="${host==='popout'?'panel-window-body':''}"><main></main></body></html>`);
      for(const name of ['tile-faces.js','tile-table.js','piece-table.js'])await page.addScriptTag({content:fs.readFileSync(path.join(frontend,name),'utf8')});
      await page.evaluate(data=>{
        document.querySelector('main').innerHTML=window.SylviaPieceTable.render(data,[{id:'play',label:'Play',available:true,bindings:{choice:'piece'}}]);
        window.SylviaTileTable.attach(document.querySelector('main'),()=>{},()=>true);
      },fixture());
      await page.locator('[data-table-select]').first().click();
    }
    const read=page=>page.evaluate(()=>{
      const style=s=>getComputedStyle(document.querySelector(s));
      return {board:style('.table-board').backgroundImage,back:style('.table-tile-back').backgroundColor,
        face:style('.table-rack .table-select-tile .table-tile').backgroundImage,
        selected:document.querySelector('[data-table-select]').getAttribute('aria-pressed'),
        action:style('[data-table-discard]').backgroundColor,selection:style('[data-table-select]').outlineStyle,
        faces:document.querySelectorAll('.table-tile svg').length,backs:document.querySelectorAll('.table-tile-back').length};
    });
    const baseline=await read(pages[0]);
    const colours=[];
    for(const theme of ['sylvia','sylvia-dark','slate']){
      for(const page of pages)await page.evaluate(theme=>document.body.setAttribute('data-theme',theme),theme);
      const docked=await read(pages[0]),popout=await read(pages[1]);
      assert.deepEqual(docked,popout,'Both documents inherit the same live tokens');
      assert.equal(docked.face,baseline.face,'Traditional ivory tile artwork stays legible');
      assert.equal(docked.selected,'true','Changing theme retains local intent');
      assert.equal(docked.selection,'solid');
      assert.equal(docked.faces,baseline.faces);assert.equal(docked.backs,39);
      colours.push(docked.board);
      if(process.env.SYLVIA_TILE_EVIDENCE)await pages[0].locator('main').screenshot({path:path.join(process.env.SYLVIA_TILE_EVIDENCE,`theme-${theme}.png`)});
    }
    assert.equal(new Set(colours).size,3,'Each existing theme changes the felt');
    assert.equal(colours[2],baseline.board,'The default restores Slate');
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
