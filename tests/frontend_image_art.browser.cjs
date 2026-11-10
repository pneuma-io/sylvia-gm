// Real source WebUI/owner, disposable synthetic data and scripted image output.
// Run with SYLVIA_IMAGE_UI_ROOT set to a fresh directory owned by this work item.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');

test('review, adopt, crop and reopen art through the source WebUI',{timeout:180000},async()=>{
  const dataRoot=process.env.SYLVIA_IMAGE_UI_ROOT;
  assert.ok(dataRoot,'Select a fresh disposable SYLVIA_IMAGE_UI_ROOT');
  assert.equal(fs.existsSync(dataRoot),false,'Never reuse or remove an existing data root');
  const source=path.resolve(__dirname,'..');
  const python=process.env.SYLVIA_TEST_PYTHON || path.join(source,'.venv/Scripts/python.exe');
  const child=spawn(python,['-m','tests.image_art_webui_fixture','--data-root',dataRoot],
    {cwd:source,windowsHide:true,stdio:['ignore','pipe','pipe']});
  let stderr='';child.stderr.on('data',data=>stderr+=data);
  let browser;
  try{
    const url=await new Promise((resolve,reject)=>{
      let output='';child.stdout.on('data',data=>{
        output+=data;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0]);
      });
      child.once('exit',code=>reject(new Error(`Fixture exited ${code}: ${stderr}`)));
    });
    browser=await chromium.launch({headless:true,
      ...(process.env.SYLVIA_TEST_CHROMIUM ? {executablePath:process.env.SYLVIA_TEST_CHROMIUM} : {})});
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(url);
    await page.getByRole('button',{name:'Continue',exact:true}).click();
    await page.getByRole('button',{name:'Images',exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'Images'}),form=dialog.locator('form');
    await form.locator('select[name=target_id]').waitFor();
    await form.locator('[name=target_id]').selectOption('operator');
    await form.locator('[name=appearance]').fill('Silver hair, brown eyes, blue coat');
    await form.locator('[name=brief]').fill('Synthetic visual calibration');
    let expected=0;
    for(const purpose of ['full_body','reference_sheet','portrait']){
      await form.locator('[name=purpose]').selectOption(purpose);
      await form.getByRole('button',{name:'Prepare request',exact:true}).click();
      await dialog.getByRole('button',{name:'Generate',exact:true}).click();
      await dialog.getByRole('button',{name:'Adopt',exact:true}).click();
      await page.waitForFunction(count=>document.querySelectorAll('.image-art-selected-preview').length===count,++expected);
    }
    const frame=dialog.locator('[data-art-frame]');
    await frame.locator('[name=x]').fill('0.42');
    await frame.locator('[name=y]').fill('0.31');
    await frame.locator('[name=zoom]').fill('1.5');
    await frame.getByRole('button',{name:'Save framing'}).click();
    await form.locator('[name=target_id]').selectOption('training-floor');
    assert.equal(await form.locator('[name=purpose]').inputValue(),'map_underlay');
    await form.getByRole('button',{name:'Prepare request',exact:true}).click();
    await dialog.getByRole('button',{name:'Generate',exact:true}).click();
    await dialog.getByRole('button',{name:'Adopt',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.image-art-selected-preview').length===4);
    await dialog.getByRole('button',{name:'Close images'}).click();
    await page.locator('.image-art-underlay').waitFor();
    assert.ok(await page.locator('.tactical-entity .image-art-portrait').count());
    assert.ok(await page.locator('.tactical-geometry').count());
    await page.screenshot({path:path.join(dataRoot,'map.png')});
    await page.reload();
    await page.getByRole('button',{name:'Continue',exact:true}).click();
    await page.locator('.image-art-underlay').waitFor();
    await page.getByRole('button',{name:'Images',exact:true}).click();
    await dialog.locator('[data-art-frame]').waitFor();
    assert.equal(await dialog.locator('[data-art-frame] [name=zoom]').inputValue(),'1.5');
    await page.screenshot({path:path.join(dataRoot,'gallery.png')});
    await dialog.getByRole('button',{name:'Close images'}).click();
    page.on('dialog',dialog=>dialog.accept());
    await page.getByRole('button',{name:'Stop local WebUI',exact:true}).click();
    await new Promise(resolve=>child.exitCode===null?child.once('exit',resolve):resolve());
    const receipt=JSON.parse(fs.readFileSync(path.join(dataRoot,'receipt.json')));
    assert.equal(receipt.audit_enabled,true);
    assert.equal(receipt.submissions,4);
    assert.deepEqual(receipt.reference_counts,[0,1,2,1]);
    assert.deepEqual(errors,[]);
  }finally{
    if(browser)await browser.close();
    if(child.exitCode===null)child.kill();
  }
});
