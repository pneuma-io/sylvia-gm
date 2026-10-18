const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/td-delivered-character-selection.json')));
function render(actor,missing=false){
 const context={window:{},document:{addEventListener(){}}};
 let source=fs.readFileSync(path.join(__dirname,'../src-tauri/frontend/app.js'),'utf8');
 source=source.replace('  // ---- Public API ----','window.characterQA={state,renderCharacterPanel};\n  // ---- Public API ----');
 vm.runInNewContext(source,context);
 const api=context.window.characterQA,character=JSON.parse(JSON.stringify(fixture.character));
 if(missing)delete character.data.entities[actor];
 api.state.panelProjection={player_control:fixture.player_control,surfaces:[fixture.map,character]};
 api.state.tacticalSelections[fixture.map.id]={actor};
 if(missing)api.state.tacticalSelections[character.id]={actor};
 return api.renderCharacterPanel(character);
}
for(const actor of ['vesper','autodrone_alpha'])test('Delivered entity-only controlled actor '+actor+' never displays Hex',()=>{
 const html=render(actor);
 assert.doesNotMatch(html,/data-panel-entity="hex"|value="hex" selected|<strong>Hex<\/strong>/);
 assert.match(html,new RegExp('data-character-entity="'+actor+'"'));
 assert.match(html,/Full character details are unavailable/);
 const entity=fixture.character.data.entities[actor];
 if(Number.isFinite(entity.ap))assert.match(html,new RegExp('AP '+entity.ap));
});
test('Unknown explicit selection is unavailable instead of another participant',()=>{
 const html=render('not-exposed',true);
 assert.doesNotMatch(html,/<strong>Hex<\/strong>|data-panel-entity="hex"/);
 assert.match(html,/data-character-unavailable="not-exposed"/);
});
test('Admitted selected Hex retains its own character record',()=>{
 const html=render('hex');assert.match(html,/data-panel-entity="hex"/);assert.doesNotMatch(html,/Full character details are unavailable/);
});

