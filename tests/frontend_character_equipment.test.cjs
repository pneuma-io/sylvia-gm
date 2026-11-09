const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=process.env.SYLVIA_SOURCE_ROOT || path.join(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(path.join(root,'tests/fixtures/td-delivered-character-selection.json')));
function setup(actor){
 const context={window:{},document:{addEventListener(){}}};
 let source=fs.readFileSync(path.join(root,'src-tauri/frontend/app.js'),'utf8');
 source=source.replace('  // ---- Public API ----','window.panelQA={state,renderCharacterPanel,renderInventoryPanel,synchronizePanelActor,rulesetCardMarkup};\n  // ---- Public API ----');
 vm.runInNewContext(source,context);
 const api=context.window.panelQA,character=JSON.parse(JSON.stringify(fixture.character));
 const loadout={id:'operations.loadout',renderer:'inventory',data:character.data,actions:[]};
 api.state.panelProjection={player_control:fixture.player_control,surfaces:[fixture.map,character,loadout]};
 api.state.tacticalSelections[fixture.map.id]={actor};
 return {api,character,loadout};
}
test('Selected Character exposes the admitted native equipment controls',()=>{
 const {api,character}=setup('vesper'),html=api.renderCharacterPanel(character);
 for(const id of ['cycle','reload-retain','recover-recoil'])assert.match(html,new RegExp('data-panel-action-id="'+id+'"'));
 assert.match(html,/data-panel-actor-id="vesper"/);
});
test('Loadout uses the selected entity and the owning Character surface actions',()=>{
 const {api,loadout}=setup('vesper'),html=api.renderInventoryPanel(loadout.data,loadout);
 assert.match(html,/<strong>Vesper<\/strong>/);assert.doesNotMatch(html,/<strong>Hex<\/strong>/);
 assert.match(html,/data-panel-id="operations.character"/);assert.match(html,/data-panel-action-id="reload-retain"/);
 api.synchronizePanelActor('autodrone_alpha');assert.equal(api.state.characterInspection.id,'autodrone_alpha');
 assert.match(api.renderInventoryPanel(loadout.data,loadout),/<strong>Autodrone Alpha<\/strong>/);
});
test('Legacy Stance receipt displays Character without rewriting the native receipt',()=>{
 const {api}=setup('hex'),card={version:1,title:'Stance',summary:[{label:'Actor',value:'Hex',emphasis:false}],details:[],actions:[]};
 assert.match(api.rulesetCardMarkup(card,''),/>Character<\/span>/);assert.equal(card.summary[0].label,'Actor');
});
