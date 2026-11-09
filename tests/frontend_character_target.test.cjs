const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
test('reusable character target comes from saved binding and preserves old authored version text',()=>{
 const sandbox={window:{},document:{addEventListener(){}}};
 const source=fs.readFileSync(path.join(__dirname,'../src-tauri/frontend/app.js'),'utf8').replace('    toolResultMarkup,','    toolResultMarkup, reusableCharacterTargetLabel,');
 vm.runInNewContext(source,sandbox);const label=sandbox.window.SylviaGMFrontend.reusableCharacterTargetLabel;
 const item={reusable:true,summary:'Vela — corrected Lite 0.3.4 / Last Cart Through',ruleset:{id:'sylvia.srd-lite.bg3',version:'0.3.6'},campaign_setting:{id:'pneuma.severed-lands',version:'1.0.0-alpha.2'}};
 const before=JSON.stringify(item);
 assert.equal(label(item),'Saved Ruleset: SRD Lite 0.3.6 · Setting: pneuma.severed-lands 1.0.0-alpha.2');
 assert.equal(item.ruleset.id,'sylvia.srd-lite.bg3');
 assert.equal(item.ruleset.version,'0.3.6');
 assert.equal(item.campaign_setting.id,'pneuma.severed-lands');
 assert.equal(JSON.stringify(item),before);
 assert.equal(label({...item,reusable:false}),'');assert.equal(label({reusable:true,summary:item.summary}),'');
 assert.equal(label({...item,ruleset:{id:'source-only'},campaign_setting:null}),'');
});
