const face = (kind, red=false) => ({family:'mahjong', kind, red});
function fixture() {
  const positions = ['bottom','right','top','left'];
  const areas = positions.map((position, seat) => ({id:`seat-${seat}`, position, label:seat ? `Opponent ${seat}` : 'You',
    badge:['東','南','西','北'][seat], score:'25000', active:seat===0, groups:[
      {id:'hand',role:'rack',label:'Hand',...(seat ? {concealed:true,count:13} : {pieces:[0,1,2,3,4,5,9,10,12,18,19,22,27,28].map((kind,index) =>
        ({id:`discard-${index}`,label:'Diagnostic label',face:face(kind,kind===4),action_id:'play',flags:index===13?['drawn']:[]}))})},
      {id:'discards',role:'pond',label:'Discards',pieces:[0,8,10,28,33,14,19,27].map((kind,index)=>
        ({id:`pond-${seat}-${index}`,label:'Tile',face:face(kind),flags:index===4?['sideways']:index===7?['latest']:index===2?['claimed']:[]}))},
      ...(seat===1 ? [{id:'kan',role:'meld',source:'across',label:'Added kan',pieces:[
        {id:'meld-0',label:'3 of circles',face:face(11),flags:[]},
        {id:'meld-1',label:'3 of circles',face:face(11),flags:['sideways']},
        {id:'meld-added',label:'3 of circles',face:face(11),flags:['stacked']},
        {id:'meld-2',label:'3 of circles',face:face(11),flags:[]},
      ]}] : []),
    ]}));
  areas.push({id:'shared',position:'center',label:'Your turn',groups:[
    {role:'indicators',label:'Dora indicators',pieces:[{id:'dora-0',label:'Dora',face:face(11)}]},
    {role:'actions',pieces:[{id:'win',label:'Tsumo',detail:'Win with your drawn tile',action_id:'play'}]},
    {role:'message',pieces:[{label:'A controlled renderer fixture. Engine acceptance uses a real campaign.'}]},
  ]});
  return {format:1,layout:'four_sided',title:'Riichi Mahjong',areas,status:[{label:'Round',value:'East 1'},
    {label:'Honba',value:'1'},{label:'Riichi sticks',value:'2'},{label:'Live wall',value:'39'}]};
}

module.exports = {fixture, face};
