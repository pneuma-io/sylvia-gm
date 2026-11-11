// The desktop bridge forwards Character File transfers to registered native commands unchanged.
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

test('main Tauri bridge forwards preview, decisions and the selected export identity',async()=>{
  const calls=[];
  const context={window:{__TAURI__:{core:{invoke:async(name,args)=>{
    calls.push({name,args});return {status:'ok'};
  }}}},document:{addEventListener(){}}};
  let source=fs.readFileSync(path.join(__dirname,'../src-tauri/frontend/app.js'),'utf8');
  source=source.replace('  // ---- Public API ----','window.transferQA={createHostBridge};\n  // ---- Public API ----');
  vm.runInNewContext(source,context);
  const bridge=context.window.transferQA.createHostBridge();
  await bridge.preview_character_bundle_file();
  await bridge.confirm_character_bundle_import('a'.repeat(64));
  await bridge.cancel_character_bundle_import('b'.repeat(64));
  await bridge.export_character_file('export-character-one','vela','c'.repeat(64),'companion','Vela');
  assert.deepEqual(JSON.parse(JSON.stringify(calls)),[
    {name:'preview_character_bundle_file'},
    {name:'confirm_character_bundle_import',args:{sourceSha256:'a'.repeat(64)}},
    {name:'cancel_character_bundle_import',args:{sourceSha256:'b'.repeat(64)}},
    {name:'export_character_file',args:{exportId:'export-character-one',artifactId:'vela',
      expectedContentSha256:'c'.repeat(64),kind:'companion',suggestedName:'Vela'}},
  ]);
  const rust=fs.readFileSync(path.join(__dirname,'../src-tauri/src/lib.rs'),'utf8');
  const handlers=rust.slice(rust.indexOf('generate_handler!['));
  for(const {name} of calls){
    assert.match(rust,new RegExp(`async fn ${name}\\(`),`${name} has a native command`);
    assert.match(handlers,new RegExp(`\\b${name},`),`${name} is registered`);
  }
});
