const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const {chromium}=require('playwright');
const frontend=path.resolve(__dirname,'../src-tauri/frontend');

// The real Home page and app.js, with a scripted owner bridge standing in for saved cards and a model.
async function openApp(browser,viewport){
 const page=await browser.newPage({viewport});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.route('https://sylvia-character-chat.test/**',async route=>{
  const name=new URL(route.request().url()).pathname.slice(1)||'app.html';
  const file=path.join(frontend,name);
  if(!file.startsWith(frontend)||!fs.existsSync(file))return route.abort();
  let body=fs.readFileSync(file);
  if(name==='app.js')body=body.toString().replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });",'').replace('  // ---- Public API ----','window.chatTest={state,wireControls,showHome};\n  // ---- Public API ----');
  await route.fulfill({body,contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.png')?'image/png':'text/html'});
 });
 await page.goto('https://sylvia-character-chat.test/');
 await page.evaluate(()=>{
  const {state,wireControls,showHome}=window.chatTest;
  localStorage.setItem('sylvia-gm-home-tutorial-v1',JSON.stringify({status:'completed',step:0}));
  wireControls();
  document.getElementById('connecting-overlay').classList.add('hidden');
  document.getElementById('session-frame').style.display='flex';
  state.campaignIndexStatus='ready';
  showHome();
  state.modelChoices=[
   {provider_id:'synthetic',model_id:'model-a',provider_name:'Synthetic',model_name:'Model A'},
   {provider_id:'synthetic',model_id:'model-b',provider_name:'Synthetic',model_name:'Model B'},
  ];
  state.providerSettings={default_model:{provider_id:'synthetic',model_id:'model-b'}};
  const vela={artifact_id:'character-profile-'+'1'.repeat(24)+'-'+'a'.repeat(16),content_sha256:'a'.repeat(64),
   name:'Vela',summary:'A travelling archivist.',has_greeting:true};
  const hostile={artifact_id:'character-profile-'+'2'.repeat(24)+'-'+'b'.repeat(16),content_sha256:'b'.repeat(64),
   name:'<img src=x onerror="window.pwned=1">',summary:'<b>not bold</b>',has_greeting:false};
  const chats=new Map();const calls=[];window.calls=calls;window.failNext=false;
  // Like the owner: applied operations are remembered, removed cards stop chats, and
  // desktop errors arrive as 'code: message' text.
  const applied=new Set();window.chatStore=chats;window.cardsGone=new Set();window.loseReplyNext=false;
  const desktopError=(code,message)=>`${code}: ${message}`;
  const copy=value=>JSON.parse(JSON.stringify(value));
  const summary=chat=>({chat_id:chat.chat_id,character_name:chat.character.name,artifact_id:chat.character.artifact_id,
   message_count:chat.messages.length,updated_at:'2026-11-13T00:00:00+00:00',preview:chat.messages.at(-1)?.text||''});
  window.addReadOnlyChat=()=>{chats.set('character-chat-'+'9'.repeat(24),{chat_id:'character-chat-'+'9'.repeat(24),
   character:{...hostile},card_available:false,user_name:'You',provider_id:'synthetic',model_id:'model-a',
   messages:[{sequence:1,role:'user',text:'Are you there?'},{sequence:2,role:'character',text:'Still here.'}]});};
  state.bridge={
   character_chat:async command=>{
    calls.push(['character_chat',copy(command)]);
    if(command.action==='catalog')return {cards:[vela,hostile].filter(card=>!window.cardsGone.has(card.artifact_id)),
     chats:[...chats.values()].map(summary),unreadable_count:0};
    if(command.action==='open'){const chat=copy(chats.get(command.chat_id));
     if(window.cardsGone.has(chat.character.artifact_id))chat.card_available=false;return chat;}
    if(window.cardsGone.has(command.artifact_id))throw desktopError('character_profile_unavailable','Refresh the library and select a saved profile');
    const card=[vela,hostile].find(item=>item.artifact_id===command.artifact_id);
    const id='character-chat-'+String(chats.size+1).padStart(24,'0');
    const user=command.user_name||'You';
    const chat={chat_id:id,character:{artifact_id:card.artifact_id,content_sha256:card.content_sha256,name:card.name,summary:card.summary},
     card_available:true,user_name:user,provider_id:command.provider_id,model_id:command.model_id,
     messages:card.has_greeting?[{sequence:1,role:'character',text:`*looks up from a ledger* Hello, ${user}.`}]:[]};
    chats.set(id,chat);return copy(chat);
   },
   send_character_chat_message:async command=>{
    calls.push(['send',copy(command)]);
    if(window.holdReply)await new Promise(resolve=>{window.releaseReply=resolve;});
    else await new Promise(resolve=>setTimeout(resolve,150));
    if(window.failNext){window.failNext=false;throw Object.assign(new Error('The selected provider timed out while responding'),{code:'provider_error'});}
    const chat=chats.get(command.chat_id);
    if(applied.has(command.operation_id))return copy(chat);
    if(chat.messages.length!==command.expected_message_count)throw desktopError('character_chat_stale','This chat changed elsewhere. Reopen it to see the latest messages');
    if(window.cardsGone.has(chat.character.artifact_id))throw desktopError('character_card_unavailable','This character card is no longer in your library, so the chat is read-only');
    const n=chat.messages.length;applied.add(command.operation_id);
    chat.messages.push({sequence:n+1,role:'user',text:command.text},{sequence:n+2,role:'character',text:`You said "${command.text}". *taps the ledger*`});
    if(window.loseReplyNext){window.loseReplyNext=false;throw Object.assign(new Error('The local connection was interrupted.'),{code:'outcome_unknown'});}
    return copy(chat);
   },
  };
 });
 return {page,errors};
}

const texts=page=>page.locator('#character-chat-log .cc-message p').allInnerTexts();

test('pick a card, talk in character, survive a failed send, then reopen the saved chat',async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge',args:['--disable-gpu']});
 const shots=process.env.SYLVIA_QA_SCREENSHOTS;if(shots)fs.mkdirSync(shots,{recursive:true});
 try{
  const {page,errors}=await openApp(browser,{width:1280,height:860});
  await page.locator('#home-character-chat-btn').click();
  await page.waitForFunction(()=>document.getElementById('character-chat-dialog')?.open&&
   document.querySelectorAll('#character-chat-card option').length===2);
  // Card text is shown as text: the hostile name neither renders markup nor runs script.
  assert.deepEqual(await page.locator('#character-chat-card option').allInnerTexts(),['Vela','<img src=x onerror="window.pwned=1">']);
  assert.equal(await page.locator('#character-chat-dialog img').count(),0);
  assert.equal(await page.locator('#character-chat-model option:checked').innerText(),'Synthetic · Model B');
  assert.equal(await page.locator('#character-chat-card-summary').innerText(),'A travelling archivist.');
  if(shots)await page.locator('#character-chat-dialog').screenshot({path:path.join(shots,'character-chat-picker.png')});

  await page.locator('#character-chat-user-name').fill('Mira');
  await page.locator('#character-chat-start').click();
  await page.waitForFunction(()=>document.querySelectorAll('#character-chat-log .cc-message').length===1);
  const create=await page.evaluate(()=>window.calls.find(call=>call[1].action==='create')[1]);
  assert.equal(create.artifact_id,'character-profile-'+'1'.repeat(24)+'-'+'a'.repeat(16));
  assert.equal(create.expected_content_sha256,'a'.repeat(64));
  assert.deepEqual([create.provider_id,create.model_id,create.user_name],['synthetic','model-b','Mira']);
  assert.equal(await page.locator('#character-chat-name').innerText(),'Vela');
  assert.deepEqual(await texts(page),['looks up from a ledger Hello, Mira.']);
  assert.equal(await page.locator('#character-chat-log .cc-character em').innerText(),'looks up from a ledger');

  // Keep this reply pending until the writing-state assertions have observed it.
  await page.evaluate(()=>{window.holdReply=true;});
  await page.locator('#character-chat-input').fill('Hi there');
  await page.locator('#character-chat-input').press('Enter');
  await page.waitForFunction(()=>!document.getElementById('character-chat-typing').hidden);
  assert.equal(await page.locator('#character-chat-typing').innerText(),'Vela is writing…');
  assert.equal(await page.locator('#character-chat-send').isDisabled(),true);
  await page.evaluate(()=>{window.holdReply=false;window.releaseReply();});
  await page.waitForFunction(()=>document.querySelectorAll('#character-chat-log .cc-message').length===3);
  assert.deepEqual((await texts(page)).slice(1),['Hi there','You said "Hi there". taps the ledger']);
  assert.equal(await page.locator('#character-chat-input').inputValue(),'');
  assert.equal(await page.locator('#character-chat-typing').isVisible(),false);

  // A failed reply keeps the text, saves nothing, and an unchanged resend keeps its identity.
  await page.evaluate(()=>{window.failNext=true;});
  await page.locator('#character-chat-input').fill('Tell me about the archive.');
  await page.locator('#character-chat-send').click();
  await page.waitForFunction(()=>/not sent/.test(document.getElementById('character-chat-status').textContent));
  assert.match(await page.locator('#character-chat-status').innerText(),/timed out/);
  assert.equal(await page.locator('#character-chat-input').inputValue(),'Tell me about the archive.');
  assert.equal(await page.locator('#character-chat-log .cc-sending').count(),0);
  assert.equal((await texts(page)).length,3);
  await page.locator('#character-chat-send').click();
  await page.waitForFunction(()=>document.querySelectorAll('#character-chat-log .cc-message').length===5);
  const sends=await page.evaluate(()=>window.calls.filter(call=>call[0]==='send').map(call=>call[1]));
  assert.equal(sends.length,3);
  assert.equal(sends[1].operation_id,sends[2].operation_id);
  assert.notEqual(sends[0].operation_id,sends[1].operation_id);
  assert.deepEqual(sends.map(call=>call.expected_message_count),[1,3,3]);
  assert.equal(await page.locator('#character-chat-status').innerText(),'');
  if(shots)await page.locator('#character-chat-dialog').screenshot({path:path.join(shots,'character-chat-conversation.png')});

  // Close and reopen from Home: the saved chat is listed and opens with its whole history.
  await page.locator('#character-chat-close').click();
  assert.equal(await page.evaluate(()=>document.getElementById('character-chat-dialog').open),false);
  await page.locator('#home-character-chat-btn').click();
  await page.waitForFunction(()=>document.querySelectorAll('#character-chat-saved .cc-saved-chat').length===1);
  assert.match(await page.locator('#character-chat-saved .cc-saved-chat').innerText(),/Vela[\s\S]*taps the ledger/);
  await page.locator('#character-chat-saved .cc-saved-chat').click();
  await page.waitForFunction(()=>document.querySelectorAll('#character-chat-log .cc-message').length===5);
  assert.deepEqual((await texts(page)).slice(3),['Tell me about the archive.','You said "Tell me about the archive.". taps the ledger']);
  assert.equal(await page.evaluate(()=>window.pwned),undefined);
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});

test('narrow windows switch between the list and the chat, and a removed card is read-only',async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge',args:['--disable-gpu']});
 const shots=process.env.SYLVIA_QA_SCREENSHOTS;
 try{
  const {page,errors}=await openApp(browser,{width:390,height:780});
  await page.evaluate(()=>window.addReadOnlyChat());
  await page.locator('#home-character-chat-btn').click();
  await page.waitForFunction(()=>document.querySelectorAll('#character-chat-saved .cc-saved-chat').length===1);
  assert.equal(await page.locator('#character-chat-side').isVisible(),true);
  assert.equal(await page.locator('#character-chat-back').isVisible(),false);
  await page.locator('#character-chat-saved .cc-saved-chat').click();
  await page.waitForFunction(()=>document.querySelectorAll('#character-chat-log .cc-message').length===2);
  assert.equal(await page.locator('#character-chat-side').isVisible(),false);
  assert.equal(await page.locator('#character-chat-back').isVisible(),true);
  assert.match(await page.locator('#character-chat-summary').innerText(),/no longer in your library/);
  assert.equal(await page.locator('#character-chat-input').isDisabled(),true);
  assert.equal(await page.locator('#character-chat-send').isDisabled(),true);
  const size=await page.locator('#character-chat-dialog').evaluate(el=>({scroll:el.scrollWidth,client:el.clientWidth}));
  assert(size.scroll<=size.client+1,'Character chat overflows a narrow window');
  if(shots)await page.screenshot({path:path.join(shots,'character-chat-narrow.png')});
  await page.locator('#character-chat-back').click();
  assert.equal(await page.locator('#character-chat-side').isVisible(),true);
  assert.equal(await page.locator('#character-chat-conversation').isVisible(),false);
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});


test('lost replies, another window, a removed card and a stale card list all recover without losing text',async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge',args:['--disable-gpu']});
 const shots=process.env.SYLVIA_QA_SCREENSHOTS;
 try{
  const {page,errors}=await openApp(browser,{width:1280,height:860});
  const vela='character-profile-'+'1'.repeat(24)+'-'+'a'.repeat(16);
  await page.locator('#home-character-chat-btn').click();
  await page.waitForFunction(()=>document.querySelectorAll('#character-chat-card option').length===2);
  await page.locator('#character-chat-start').click();
  await page.waitForFunction(()=>document.querySelectorAll('#character-chat-log .cc-message').length===1);
  const input=page.locator('#character-chat-input');
  const sends=()=>page.evaluate(()=>window.calls.filter(call=>call[0]==='send').map(call=>call[1]));

  // 1. The owner saved the message but the reply was lost: say so, keep the text, and an
  //    unchanged resend returns the saved messages once.
  await page.evaluate(()=>{window.loseReplyNext=true;});
  await input.fill('Did you get this?');
  await input.press('Enter');
  await page.waitForFunction(()=>/could not confirm/.test(document.getElementById('character-chat-status').textContent));
  assert.equal(await input.inputValue(),'Did you get this?');
  assert.equal((await texts(page)).length,1);
  await page.locator('#character-chat-send').click();
  await page.waitForFunction(()=>document.querySelectorAll('#character-chat-log .cc-message').length===3);
  let sent=await sends();
  assert.equal(sent[0].operation_id,sent[1].operation_id);
  assert.deepEqual((await texts(page)).slice(1),['Did you get this?','You said "Did you get this?". taps the ledger']);
  assert.equal(await page.evaluate(()=>[...window.chatStore.values()][0].messages.length),3);
  assert.equal(await input.inputValue(),'');

  // 2. Another window added messages: the send is refused, the chat reloads, the text stays.
  await page.evaluate(()=>{const chat=[...window.chatStore.values()][0];
   chat.messages.push({sequence:4,role:'user',text:'From the other window'},{sequence:5,role:'character',text:'Hello again.'});});
  await input.fill('Still there?');
  await page.locator('#character-chat-send').click();
  await page.waitForFunction(()=>/changed in another window/.test(document.getElementById('character-chat-status').textContent));
  assert.deepEqual((await texts(page)).slice(3),['From the other window','Hello again.']);
  assert.equal(await input.inputValue(),'Still there?');
  await page.locator('#character-chat-send').click();
  await page.waitForFunction(()=>document.querySelectorAll('#character-chat-log .cc-message').length===7);
  sent=await sends();
  assert.equal(sent.at(-1).expected_message_count,5);
  assert.notEqual(sent.at(-1).operation_id,sent.at(-2).operation_id);

  // 3. The card was removed while the chat was open: the chat turns read-only, text kept.
  await page.evaluate(id=>window.cardsGone.add(id),vela);
  await input.fill('Are you still here?');
  await page.locator('#character-chat-send').click();
  await page.waitForFunction(()=>/no longer in your library/.test(document.getElementById('character-chat-status').textContent));
  assert.equal(await input.inputValue(),'Are you still here?');
  assert.equal(await input.isDisabled(),true);
  assert.equal(await page.locator('#character-chat-send').isDisabled(),true);
  assert.match(await page.locator('#character-chat-summary').innerText(),/no longer in your library/);
  assert.equal((await texts(page)).length,7);
  if(shots)await page.locator('#character-chat-dialog').screenshot({path:path.join(shots,'character-chat-card-removed.png')});

  // 4. Starting from a stale card list: the list refreshes and the removed card is gone.
  await page.locator('#character-chat-card').selectOption(vela);
  await page.locator('#character-chat-start').click();
  await page.waitForFunction(()=>/changed or was removed/.test(document.getElementById('character-chat-status').textContent));
  assert.deepEqual(await page.locator('#character-chat-card option').allInnerTexts(),['<img src=x onerror="window.pwned=1">']);
  assert.equal(await page.evaluate(()=>window.chatStore.size),1);
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});
