// Real source WebUI/owner pairs, disposable synthetic data and no image adapter.
// Run with SYLVIA_CHARACTER_TRANSFER_ROOT set to a fresh directory owned by this work item.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');

const source=path.resolve(__dirname,'..');
const python=process.env.SYLVIA_TEST_PYTHON || path.join(source,'.venv/Scripts/python.exe');
const launch=()=>chromium.launch({headless:true,
  ...(process.env.SYLVIA_TEST_CHROMIUM ? {executablePath:process.env.SYLVIA_TEST_CHROMIUM} : {})});

function startFixture(dataRoot,extra=[]){
  const child=spawn(python,['-m','tests.character_transfer_webui_fixture','--data-root',dataRoot,...extra],
    {cwd:source,windowsHide:true,stdio:['ignore','pipe','pipe']});
  let stderr='';child.stderr.on('data',data=>stderr+=data);
  const url=new Promise((resolve,reject)=>{
    let output='';child.stdout.on('data',data=>{
      output+=data;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0]);
    });
    child.once('exit',code=>reject(new Error(`Fixture exited ${code}: ${stderr}`)));
  });
  return {child,url};
}

async function openLibrary(page,url){
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(url);
  await page.locator('#tutorial-welcome').waitFor({state:'visible'});
  await page.locator('#tutorial-welcome-explore').click();
  await page.getByRole('button',{name:'Library',exact:true}).click();
  await page.locator('#settings-panel-packages').waitFor();
  return errors;
}

const optionValues=page=>page.locator('#character-export-select option').evaluateAll(
  options=>options.map(option=>option.value).filter(Boolean));
const status=page=>page.locator('#package-install-status').innerText();

async function waitForExportChoice(page,value,present=true){
  await page.waitForFunction(([value,present])=>[...document.querySelectorAll('#character-export-select option')]
    .some(option=>option.value===value)===present,[value,present],{timeout:120000});
}

async function exportSelected(page,value,target){
  await page.locator('#character-export-select').selectOption(value);
  await page.locator('#character-export-status').evaluate(element=>{element.textContent='';});
  const [download]=await Promise.all([page.waitForEvent('download'),
    page.locator('#export-character-file-btn').click()]);
  await download.saveAs(target);
  await page.waitForFunction(()=>document.getElementById('character-export-status').textContent);
  return page.locator('#character-export-status').innerText();
}

async function previewFile(page,file){
  const [chooser]=await Promise.all([page.waitForEvent('filechooser'),
    page.locator('#import-character-file-btn').click()]);
  await chooser.setFiles(file);
  await page.locator('#character-file-preview').waitFor({state:'visible'});
  return page.locator('#character-file-preview').innerText();
}

async function stop(page,child){
  page.once('dialog',dialog=>dialog.accept());
  await page.getByRole('button',{name:'Stop local WebUI',exact:true}).click();
  await new Promise(resolve=>child.exitCode===null?child.once('exit',resolve):resolve());
}

test('export, preview, cancel, refused import, recovery and refresh through the source WebUI',
  {timeout:600000},async()=>{
  const root=process.env.SYLVIA_CHARACTER_TRANSFER_ROOT;
  assert.ok(root,'Select a fresh disposable SYLVIA_CHARACTER_TRANSFER_ROOT');
  assert.equal(fs.existsSync(root),false,'Never reuse or remove an existing data root');
  fs.mkdirSync(path.join(root,'downloads'),{recursive:true});
  const packages=path.join(root,'lite.sylvia-gm-package');
  const sender=startFixture(path.join(root,'source'),['--package-out',packages]);
  const receiver=startFixture(path.join(root,'recipient'));
  let browser;
  try{
    const [senderUrl,receiverUrl]=await Promise.all([sender.url,receiver.url]);
    const seed=JSON.parse(fs.readFileSync(path.join(root,'source/server.json'))).seed;
    browser=await launch();
    const page=await (await browser.newContext({acceptDownloads:true})).newPage({viewport:{width:1280,height:900}});
    const senderErrors=await openLibrary(page,senderUrl);
    const profileValue=`character_profile/${seed.profile.id}`;
    const player=seed.playable;
    const playerValue=`player_character/${player.id}`;
    await waitForExportChoice(page,profileValue);
    await waitForExportChoice(page,playerValue);
    assert.match(await page.locator(`#character-export-select option[value="${playerValue}"]`).innerText(),
      /Saved Ruleset: SRD Lite/);
    const profileFile=path.join(root,'downloads/profile.sylvia-gm-character');
    const playerFile=path.join(root,'downloads/player.sylvia-gm-character');
    assert.match(await exportSelected(page,profileValue,profileFile),
      new RegExp(`^Exported ${seed.profile.name} with 1 image and its original card\\.$`));
    assert.match(await exportSelected(page,playerValue,playerFile),
      /with 3 images\. Whoever imports it needs the same Ruleset and Setting installed\./);
    assert.equal(senderErrors.length,0,senderErrors.join('\n'));
    await stop(page,sender.child);

    const recipient=await (await browser.newContext({acceptDownloads:true})).newPage({viewport:{width:1280,height:900}});
    const receiverErrors=await openLibrary(recipient,receiverUrl);
    await recipient.waitForFunction(()=>!document.getElementById('character-export-select').disabled ||
      /No saved characters/.test(document.getElementById('character-export-select').textContent));
    const before=await optionValues(recipient);
    const previews=path.join(root,'recipient/character-bundle-previews');
    const pending=()=>fs.existsSync(previews)?fs.readdirSync(previews).filter(name=>/^[0-9a-f]{64}$/.test(name)):[];

    // A damaged file is refused before anything is staged or shown for import.
    const broken=path.join(root,'downloads/broken.sylvia-gm-character');
    fs.writeFileSync(broken,fs.readFileSync(profileFile).subarray(0,200));
    const [chooser]=await Promise.all([recipient.waitForEvent('filechooser'),
      recipient.locator('#import-character-file-btn').click()]);
    await chooser.setFiles(broken);
    await recipient.waitForFunction(()=>/Character File preview failed/.test(document.getElementById('package-install-status').textContent));
    assert.equal(await recipient.locator('#character-file-preview').isVisible(),false);
    assert.deepEqual(pending(),[]);

    // Unbound: preview, cancel without saving, preview again, then import and refresh.
    let preview=await previewFile(recipient,profileFile);
    assert.match(preview,/Universal character card/);
    assert.match(preview,/Needs ruleset adaptations/);
    assert.match(preview,/Includes 1 image and the original imported card\. Importing does not create new artwork\./);
    assert.match(preview,/stays unbound/);
    assert.equal(pending().length,1);
    await recipient.locator('#cancel-character-file-btn').click();
    await recipient.locator('#character-file-preview').waitFor({state:'hidden'});
    assert.match(await status(recipient),/Cancelled importing .*Nothing was saved\./);
    assert.deepEqual(pending(),[]);
    assert.deepEqual(await optionValues(recipient),before);
    await previewFile(recipient,profileFile);
    await recipient.locator('#confirm-character-file-btn').click();
    await recipient.waitForFunction(()=>/^Imported /.test(document.getElementById('package-install-status').textContent));
    assert.match(await status(recipient),/as a Universal character card\. Needs ruleset adaptations\./);
    await recipient.waitForFunction(count=>document.querySelectorAll('#character-export-select option[value^="character_profile/"]').length===count,1);
    const importedProfile=(await optionValues(recipient)).find(value=>value.startsWith('character_profile/'));
    assert.deepEqual(await recipient.locator('#character-profile-select option').evaluateAll(
      options=>options.map(option=>option.value)),['',importedProfile.slice('character_profile/'.length)]);
    await recipient.locator('#character-export-select').selectOption(importedProfile);

    // Playable: missing exact packages refuse import, keep the preview and the current selection.
    preview=await previewFile(recipient,playerFile);
    assert.match(preview,new RegExp(`${player.name} — Player Character`));
    assert.match(preview,/Playable with Ruleset SRD Lite /);
    assert.match(preview,/Includes 3 images\./);
    assert.match(preview,/Install exactly this Ruleset and Setting before importing/);
    await recipient.locator('#confirm-character-file-btn').click();
    await recipient.waitForFunction(()=>/import failed/.test(document.getElementById('package-install-status').textContent));
    assert.match(await status(recipient),/Install this character's matching Ruleset and Setting packages/);
    assert.match(await status(recipient),/The preview is kept; import it again or cancel\./);
    assert.equal(await recipient.locator('#character-file-preview').isVisible(),true);
    assert.equal(await recipient.locator('#character-export-select').inputValue(),importedProfile);
    assert.deepEqual((await optionValues(recipient)).filter(value=>!before.includes(value)),[importedProfile]);
    assert.equal(pending().length,1);

    // Install the exact packages through the same upload route, then import the kept preview.
    const install=await recipient.request.post(`${receiverUrl}/api/upload/package`,{
      headers:{'X-Sylvia-WebUI':'1',Origin:receiverUrl},timeout:180000,
      multipart:{params:'{"installId":"recipient-packages"}',
        file:{name:'lite.sylvia-gm-package',mimeType:'application/zip',buffer:fs.readFileSync(packages)}},
    });
    assert.equal(install.status(),200,await install.text());
    await recipient.locator('#confirm-character-file-btn').click();
    await recipient.waitForFunction(()=>/as a Player Character/.test(document.getElementById('package-install-status').textContent));
    await recipient.locator('#character-file-preview').waitFor({state:'hidden'});
    assert.deepEqual(pending(),[]);
    // Import saves a new library identity for the same character data.
    // Installing the packages also lists their built-in starters; the import is the saved copy.
    const importedOption=recipient.locator('#character-export-select option[value^="player_character/"]',
      {hasText:new RegExp(`^${player.name} — Saved Ruleset: SRD Lite`)});
    await importedOption.waitFor({state:'attached',timeout:120000});
    assert.equal(await importedOption.count(),1);
    const importedPlayer=await importedOption.getAttribute('value');
    assert.equal(before.includes(importedPlayer),false);
    assert.equal(await recipient.locator('#character-export-select').inputValue(),importedProfile,
      'Refreshing the library keeps the current selection');
    const forwarded=path.join(root,'downloads/forwarded.sylvia-gm-character');
    assert.match(await exportSelected(recipient,importedPlayer,forwarded),/with 3 images\./);

    // A selection that changed underneath the page is refused, reported and refreshed away.
    const item=await recipient.evaluate(id=>window.SylviaWebUI.rpc('campaign_creation_catalog',{includeParticipants:true})
      .then(rows=>rows.find(row=>row.kind==='player_character'&&row.id===id)),importedPlayer.slice('player_character/'.length));
    await recipient.evaluate(item=>window.SylviaWebUI.rpc('delete_reusable_library_entry',{kind:item.kind,
      artifactId:item.id,expectedContentSha256:item.content_sha256,confirmation:`permanently delete ${item.id}`}),item);
    await recipient.locator('#character-export-status').evaluate(element=>{element.textContent='';});
    await recipient.locator('#export-character-file-btn').click();
    await recipient.waitForFunction(()=>/Could not export/.test(document.getElementById('character-export-status').textContent));
    await waitForExportChoice(recipient,importedPlayer,false);
    await recipient.waitForFunction(()=>/Saved characters were refreshed\./.test(document.getElementById('character-export-status').textContent));
    assert.match(await recipient.locator('#character-export-status').innerText(),/unavailable/i);
    const text=await recipient.locator('body').innerText();
    assert.equal(text.includes(root)||text.includes(root.replaceAll('\\','/')),false,'No host paths reach the page');
    // The playable preview and saved-character list fit desktop and phone widths.
    await previewFile(recipient,playerFile);
    const shots=process.env.SYLVIA_QA_SCREENSHOTS;
    if(shots)fs.mkdirSync(shots,{recursive:true});
    for(const width of [1280,390]){
      await recipient.setViewportSize({width,height:900});
      for(const selector of ['#character-file-preview','#character-export-select']){
        const size=await recipient.locator(selector).evaluate(element=>({scroll:element.scrollWidth,client:element.clientWidth}));
        assert(size.scroll<=size.client+1,`${selector} overflows at ${width}`);
      }
      await recipient.locator('#character-file-preview').scrollIntoViewIfNeeded();
      if(shots)await recipient.screenshot({path:path.join(shots,`character-transfer-${width}.png`)});
    }
    await recipient.locator('#cancel-character-file-btn').click();
    await recipient.locator('#character-file-preview').waitFor({state:'hidden'});
    assert.deepEqual(pending(),[]);
    assert.equal(receiverErrors.length,0,receiverErrors.join('\n'));
    await stop(recipient,receiver.child);
    for(const side of ['source','recipient']){
      const receipt=JSON.parse(fs.readFileSync(path.join(root,side,'receipt.json')));
      assert.deepEqual(receipt,{image_adapters:0,image_requests:0,audit_enabled:true});
    }
  }finally{
    await browser?.close();
    for(const {child} of [sender,receiver])if(child.exitCode===null)child.kill();
  }
});

// Setup's party and Universal card choices export their exact selected library entries.
test('setup export buttons send the selected identity without toggling party choices',async()=>{
  const frontend=path.join(source,'src-tauri/frontend');
  const browser=await launch();
  try{
    const page=await browser.newPage({viewport:{width:1280,height:900}});
    await page.route('https://sylvia-transfer-setup.test/**',async route=>{
      const name=new URL(route.request().url()).pathname.slice(1)||'app.html';
      if(!['app.html','app.js','app.css','panel-workspace.js','shot-playback.js','panel-host.js','assets/sylvia-portrait.png'].includes(name))return route.abort();
      let body=fs.readFileSync(path.join(frontend,name));
      if(name==='app.js')body=body.toString().replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });",'')
        .replace('  // ---- Public API ----','window.setupTransfer={state,wireControls,renderCampaignPartyOptions,renderCharacterProfiles,campaignPartyRequestKey};\n  // ---- Public API ----');
      await route.fulfill({body,contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.png')?'image/png':'text/html'});
    });
    await page.goto('https://sylvia-transfer-setup.test/');
    await page.evaluate(()=>{
      const {state,wireControls,renderCampaignPartyOptions,renderCharacterProfiles,campaignPartyRequestKey}=window.setupTransfer;
      wireControls();
      document.getElementById('connecting-overlay').style.display='none';
      const panel=document.getElementById('construction-panel');document.body.appendChild(panel);panel.hidden=false;
      document.getElementById('rift-construction-fields').hidden=false;
      const target={ruleset:{id:'sylvia.srd-lite.bg3',version:'0.3.15',content_sha256:'c'.repeat(64)},
        campaign_setting:{id:'synthetic.setting',version:'1',content_sha256:'d'.repeat(64)}};
      for(const [id,value] of [['construction-package','ruleset'],['construction-setting','setting'],['construction-template','plan']]){
        const select=document.getElementById(id);select.add(new Option(value,value));select.value=value;
      }
      window.exports=[];window.catalogReads=0;window.partyReads=0;
      state.campaignCreationCatalog=[{kind:'character_profile',id:'card-profile',name:'Vela',summary:'A universal card.',
        content_sha256:'e'.repeat(64),reusable:true}];
      state.bridge={
        export_character_file:async(...args)=>{
          window.exports.push(args);
          if(window.failNext){window.failNext=false;throw new Error('The selected character changed; refresh its library entry');}
          return {status:'exported',file_name:'chosen.sylvia-gm-character',export:{image_count:3,original_card_included:false}};
        },
        campaign_creation_catalog:async()=>{window.catalogReads++;return state.campaignCreationCatalog;},
      };
      const options={template_id:'plan',player_archetypes:[
        {kind:'player_character',id:'saved-hero',name:'Hero',summary:'A saved hero.',reusable:true,campaign_ready:true,content_sha256:'a'.repeat(64),...target},
      ],companions:[
        {kind:'companion',id:'saved-friend',name:'Friend',summary:'A saved friend.',reusable:true,campaign_ready:true,content_sha256:'b'.repeat(64),...target},
        {kind:'companion',id:'required-ally',name:'Ally',summary:'Required by the Plan.',reusable:false,campaign_ready:true,source_ref:{id:'required-ally',path:'characters/ally.json'}},
      ],starting_party:{player_character:{mode:'compatible'},required_companions:[],custom_companions:{minimum:0,maximum:5}}};
      state.bridge.campaign_party_options=async()=>{window.partyReads++;return options;};
      state.campaignPartyOptionsStatus='ready';state.campaignPartyOptionsKey=campaignPartyRequestKey();
      renderCampaignPartyOptions(options);
      renderCharacterProfiles();
    });
    assert.equal(await page.locator('#export-reusable-player-btn').isVisible(),false);
    await page.locator('#rift-archetype').selectOption('saved-hero');
    await page.locator('#export-reusable-player-btn').click();
    await page.waitForFunction(()=>/Exported Hero with 3 images\./.test(document.getElementById('construction-status').textContent));
    assert.equal(await page.locator('[data-export-reusable-companion]').count(),1,'Only saved library companions export');
    await page.locator('[data-export-reusable-companion="saved-friend"]').click();
    await page.waitForFunction(()=>window.exports.length===2);
    assert.equal(await page.locator('#rift-companions input[value="saved-friend"]').isChecked(),false);
    await page.locator('#character-profile-select').selectOption('card-profile');
    await page.locator('#export-character-profile-btn').click();
    await page.waitForFunction(()=>window.exports.length===3);
    const calls=await page.evaluate(()=>window.exports.map(([id,...rest])=>[/^export-character-[0-9a-f-]{36}$/.test(id),...rest]));
    assert.deepEqual(calls,[
      [true,'saved-hero','a'.repeat(64),'player_character','Hero'],
      [true,'saved-friend','b'.repeat(64),'companion','Friend'],
      [true,'card-profile','e'.repeat(64),'character_profile','Vela'],
    ]);
    await page.evaluate(()=>{window.failNext=true;});
    await page.locator('#export-reusable-player-btn').click();
    await page.waitForFunction(()=>/Saved characters were refreshed\./.test(document.getElementById('construction-status').textContent));
    assert.match(await page.locator('#construction-status').innerText(),/Could not export Hero: The selected character changed/);
    assert.equal(await page.evaluate(()=>window.catalogReads),1);
    await page.waitForFunction(()=>window.partyReads===1&&document.getElementById('rift-archetype').value==='saved-hero');
  }finally{await browser.close();}
});
