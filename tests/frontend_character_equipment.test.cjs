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
// Inventory chips made from item ids read as names: a single-word id gets its capital, as multi-word ids already
// do. Proper names (apostrophes) need the Ruleset package to declare them; that part of issue 128 stays open.
test('Inventory chips give single-word item ids a capital, like multi-word ids',()=>{
 const {api,loadout}=setup('vesper');
 loadout.data.entities.vesper.inventory=['greatsword','flail','dungeoneers_pack','chain_mail','gp',
  {item_id:'javelin',quantity:4},{name:'Named by the owner'},'Already Named','18_gp'];
 const chips=[...api.renderInventoryPanel(loadout.data,loadout).matchAll(/<span class="panel-inventory-item"[^>]*>([^<]*)<\/span>/g)].map(match=>match[1]);
 assert.deepEqual(chips,['Greatsword','Flail','Dungeoneers Pack','Chain Mail','Gp','Javelin \u00d7 4','Named by the owner','Already Named','18 Gp']);
});
// A chip says what its item is doing, for a panel look to draw: held in the active set's hands, worn, one of the
// equipment policy's weapons, or a counted stack such as a purse. The chip's text stays the item's label.
test('Inventory chips mark held, worn, weapon and counted items without changing their text',()=>{
 const {api,loadout}=setup('vesper');
 Object.assign(loadout.data.entities.vesper,{inventory:['greatsword','flail','chain_mail','shield','rope','18_gp','10_minutes_of_rope'],
  weapon_sets:{active_set:'melee',melee:{main_hand:'greatsword',off_hand:null},ranged:{main_hand:'flail',off_hand:null}},
  worn_armor:{inventory_item:'chain_mail'},worn_shield:{inventory_item:'shield'}});
 loadout.data.equipment={sets:[],weapons:[{id:'greatsword'},{id:'flail'}]};
 const chips=Object.fromEntries([...api.renderInventoryPanel(loadout.data,loadout).matchAll(/<span class="panel-inventory-item"([^>]*)>([^<]*)<\/span>/g)]
  .map(match=>[match[2],match[1].trim()]));
 assert.deepEqual(chips,{'Greatsword':'data-inventory-held="hand" data-inventory-kind="weapon"','Flail':'data-inventory-kind="weapon"',
  'Chain Mail':'data-inventory-held="worn"','Shield':'data-inventory-held="worn"','Rope':'','18 Gp':'data-inventory-counted=""','10 Minutes Of Rope':''});
});
test('Legacy Stance receipt displays Character without rewriting the native receipt',()=>{
 const {api}=setup('hex'),card={version:1,title:'Stance',summary:[{label:'Actor',value:'Hex',emphasis:false}],details:[],actions:[]};
 assert.match(api.rulesetCardMarkup(card,''),/>Character<\/span>/);assert.equal(card.summary[0].label,'Actor');
});
