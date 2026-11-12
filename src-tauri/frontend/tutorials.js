/* Optional guidance owns UI preferences only. Application commands remain with the player. */
(function (root) {
  'use strict';
  const STORAGE_KEY = 'sylvia-gm-tutorials-v1';
  const LEGACY_KEY = 'sylvia-gm-home-tutorial-v1';
  const step = (title, copy, extra = {}) => ({ title, copy, ...extra });
  const lessons = {
    home: {
      title: 'Find your way around', summary: 'Meet Sylvia and find your next step.', time: '1 min', route: 'home',
      steps: [
        step('Hi, I’m Sylvia.', 'Oh, there you are! I’m Sylvia. I’ll be your Game Master, your guide around here, and probably an accomplice to a few questionable plans. Let’s find you an adventure. You can pause this tour whenever you like.'),
        step('Ready campaigns', "Eager to get going? Me too! These adventures already have a party packed and ready. Take a peek at everyone before we make your own campaign copy.", { target: '#home-ready-campaign-btn' }),
        step('New Campaign', "This is where we make an adventure yours. Choose the rules, world and characters in the Campaign Interview. I’ve got compatible starter characters if you’d like a little head start.", { target: '#home-new-campaign-btn' }),
        step('Build Something', "Got a character idea you can’t leave alone? Bring it to Builder. We can create or change reusable characters, adventures, worlds and Rulesets here.", { target: '#home-build-btn' }),
        step('Settings and help', "My knobs and dials live here: AI connections, appearance, and the rest. And if you forget where something is, come find me in Tutorials. I’m happy to show you again.", { target: '#home-settings-btn' }),
      ],
    },
    connect: {
      title: 'Connect your AI', summary: 'Understand the choices and connect a model.', time: '2–4 min', route: 'models',
      steps: [
        step('What powers Sylvia?', "A little setup, then we play! Your chosen AI model does my thinking and storytelling during a real adventure. Running it is called inference. I send it your messages and relevant game content; your campaign is saved here on this computer."),
        step('Choose a connection', "Let’s find the connection that suits you. A supported ChatGPT subscription needs no API key. API services have their own accounts and billing. Or we can connect a model server you already run. The service sets its availability and usage limits.", { routes: [['subscription', 'ChatGPT subscription'], ['api', 'API service'], ['local', 'Local model server']] }),
        step('Set your default', "Found a model you like? Select it here and choose Save default. That’s what I’ll use for new chats. Existing chats keep their own model, so this won’t change an adventure already underway.", { target: '#model-choice', ready: 'modelReady', waiting: 'Save a default model to continue.', action: ['models', 'Open Models'] }),
      ],
    },
    subscription: {
      title: 'Connect a ChatGPT subscription', summary: 'Sign in, save a model and choose your default.', time: '2–4 min', route: 'accounts', connection: true,
      steps: [
        step('Use your ChatGPT account', "Already have a supported ChatGPT subscription? We can use it through Codex, without an API key. Your account’s eligibility and usage limits still apply. I’ll show you the steps; you decide when to sign in."),
        step('Sign in through your browser', "Choose Sign in with ChatGPT, then finish sign-in and consent in your browser. I’ll be right here when you come back. If it didn’t finish, use Refresh status or try signing in again.", { target: '[data-chatgpt-action="login"], #chatgpt-connection-status', ready: 'signedIn', waiting: 'Waiting for Sylvia to confirm that you are signed in.', action: ['accounts', 'Open ChatGPT account'] }),
        step('Save an available model', "There’s our model list! Pick one available to your account and select Save model. Advanced values can stay automatic unless you need to change them. Saving doesn’t send a campaign message.", { target: '#chatgpt-model-choice, #models-overview', ready: 'subscriptionModel', waiting: 'Save a ChatGPT model to continue.', action: ['accounts', 'Open ChatGPT account'] }),
        step('Use it for new chats', "One last little choice: open Models, select your saved model, and choose Save default. Now new chats know what to use. Your existing campaigns keep their own model.", { target: '#model-choice', ready: 'modelReady', waiting: 'Choose and save your default model.', action: ['models', 'Open Models'] }),
      ],
    },
    api: {
      title: 'Connect an API service', summary: 'Use a service account and API key.', time: '2–4 min', route: 'api', connection: true,
      steps: [
        step('An API connection', "An API key lets me make requests to a service on your behalf. It has its own access, billing and limits, so check those before our adventure. A chat subscription and API access are different connection choices."),
        step('Choose your service', "Let’s start with the service you use. Open service setup if you need its account or key page. Named services fill in their usual address; only use a custom address when your service asks for one.", { target: '#provider-preset', change: '#provider-preset', action: ['api', 'Open API setup'] }),
        step('Connect and find models', "Psst. Your API key belongs only in the API key field, never in our chat. Enter it here, then choose Find models. If discovery fails, check the connection details and the service’s error before trying again.", { target: '#provider-connection-step', ready: 'modelEditorReady', waiting: 'Find models or choose manual entry to continue.', action: ['api', 'Open API setup'] }),
        step('Save your model', "Pick a model and save it. You can leave advanced context and output values automatic when the service supplies them. One connection can hold several models, so you’re free to try another later.", { target: '#provider-model-step, #models-overview', ready: 'hasModels', waiting: 'Save a model to continue.' }),
        step('Choose your default', "Nearly there! Select your saved model and choose Save default. When we play, your messages and relevant campaign content go to that service. Existing chats keep their own model.", { target: '#model-choice', ready: 'modelReady', waiting: 'Save a default model to continue.', action: ['models', 'Open Models'] }),
      ],
    },
    local: {
      title: 'Connect a local model server', summary: 'Connect a server you already run.', time: '2–4 min', route: 'local', connection: true,
      steps: [
        step('Bring your running server', "Bringing your own model server? Start it first, then I can connect. It runs inference on its own hardware. A server on another computer receives the content we send to it. This guide won’t install or download a model."),
        step('Find the service', "Choose your server type. Detect local services can help find a running one, or you can enter its address. Keep the server’s instructions handy for authentication and model availability.", { target: '#provider-preset', change: '#provider-preset', action: ['local', 'Open local setup'] }),
        step('Find and save a model', "Choose Find models, pick one your server can run, and save it. If discovery isn’t supported, enter its exact model identifier manually. Then we can give new chats a default.", { target: '#provider-continue-btn, #provider-model-step, #models-overview', ready: 'hasModels', waiting: 'Save a model to continue.' }),
        step('Choose your default', "Nearly there! Select your saved model and choose Save default. When we play, your messages and relevant campaign content go to that service. Existing chats keep their own model.", { target: '#model-choice', ready: 'modelReady', waiting: 'Save a default model to continue.', action: ['models', 'Open Models'] }),
      ],
    },
    ready: {
      title: 'Start a ready campaign', summary: 'Preview an adventure and its prepared party.', time: '2–3 min', route: 'ready',
      steps: [
        step('Choose an adventure', 'Ooh, which adventure shall we try? Last Cart Through has a broken wagon, a driver who needs help, and trouble nearby. It’s a compact place to start, but all four prepared adventures are yours to choose from.', { target: '#ready-campaign-choice' }),
        step('Meet your party', "Come meet your travelling companions! The preview shows your character and the rest of the party. Expand a character preview to learn more. We’re only looking for now; Start makes your own editable campaign copy.", { target: '#ready-campaign-party' }),
        step('Name your campaign', 'Give our adventure a name, or keep the suggestion. Want to change the party first? Customize opens the Campaign Interview with independent copies you can edit. Until you choose Start or Customize, we’re only browsing.', { target: '#ready-campaign-name' }),
        step('Start when you’re ready', 'This is the exciting bit! Choose Start this campaign when you’re ready, then confirm the model, effort and speed I should use. The prepared state loads locally; my opening response uses your AI service. If that response fails, your campaign is still saved so we can reopen it and recover.', { target: '#ready-campaign-start', event: 'ready-started', waiting: 'Start the campaign yourself, or pause this lesson.', action: ['ready', 'Open Ready campaigns'] }),
      ],
    },
    play: {
      title: 'Play with your Game Master', summary: 'Take an action and talk directly to Sylvia.', time: '3–5 min', route: 'session',
      steps: [
        step('Open your adventure', "Ready for our first scene? Open a campaign from Home, or start a Ready campaign. You get to make your own choices here; this lesson doesn’t need a particular answer from me.", { ready: 'campaign', waiting: 'Open a Game Master campaign to continue.', action: ['home', 'Choose a campaign'] }),
        step('Read the scene', "Here’s where our story unfolds. I’ll describe the situation, then you choose what to do. Let my response finish before your next action. There’s no rush; have a look around.", { target: '#chronology', require: 'campaign' }),
        step('Make your first choice', "Your turn! Tell me what your character says or attempts, in your own words. Then choose Send. It uses your selected AI service. I won’t type or send anything for you. Go on, surprise me.", { target: '#player-input', event: 'player-message', require: 'campaign', waiting: 'Send an action in your campaign, or skip this step.' }),
        step('Talk to Sylvia out of character', "Need to talk to me instead of someone in the story? Open OOC note. A correction, preference or question there accompanies your next message without becoming your character’s dialogue. You can just look at the control for now.", { target: '#ooc-note', click: '#ooc-note > summary', require: 'campaign' }),
        step('Check your character and belongings', "Let’s check what you’ve got. Game panels show the state recorded by your game, including your character and inventory where available. Each Ruleset chooses its panels; narrative play has different controls from D&D.", { target: '#ruleset-panel-host', require: 'campaign' }),
      ],
    },
    combat: {
      title: 'Use D&D combat controls', summary: 'Learn SRD Lite selection, actions and results.', time: '5–8 min', route: 'session',
      steps: [
        step('Learn during an encounter', "Time to learn the D&D controls! Open an SRD Lite campaign, using Sylvia’s bundled D&D Ruleset. We’ll practise in your actual game, so I won’t create an encounter or take an action for you. Other Rulesets can work differently.", { ready: 'combatGuide', waiting: 'Open an SRD Lite campaign to follow this lesson.', action: ['home', 'Choose a campaign'] }),
        step('Find the map and your character', "First, find your character. The Party and map panels let you inspect resources and the current turn. Selecting a character or target doesn’t spend an action. We’re just getting our bearings.", { target: '[data-tactical-actor], [data-party-select-actor], #ruleset-panel-host', require: 'combatGuide' }),
        step('Movement and targets', "Pick a movement or attack option, then a destination or target. Take a look at the preview: range, cost, and whether the action is available. That’s our plan; nothing has been committed yet.", { target: '[data-tactical-action], [data-tactical-target], #ruleset-panel-host', click: '[data-tactical-action], [data-tactical-target]', require: 'combatGuide' }),
        step('Spend actions deliberately', "Happy with the plan? Confirm the actions you actually want. If you’ve queued several, review the sequence and use Execute. This changes your real campaign and can spend movement or resources, so the final choice is yours.", { target: '[data-sequence-run], [data-panel-action-id], #ruleset-panel-host', event: 'action-committed', require: 'combatGuide', waiting: 'Commit an available action yourself, or skip this step.' }),
        step('Read the result', "Let’s see what happened. The recorded result is what counts, whether it’s a hit, a miss, or a rejected action. Expand the calculation if you’re curious. You don’t need a lucky roll to finish my lesson.", { target: '#chronology .result-card, #chronology', require: 'combatGuide' }),
        step('Finish your turn', "Before you hand the turn over, check your remaining resources. Use the game’s turn control when it’s available. If my response fails after an action is committed, recover the reply; we don’t want to take that action twice.", { target: '.map-end-turn, #ruleset-panel-host', require: 'combatGuide' }),
      ],
    },
    customize: {
      title: 'Customize a campaign', summary: 'Choose content, starter characters and your boundaries.', time: '5–8 min', route: 'interview',
      steps: [
        step('Open a Campaign Interview', "Let’s make this one yours. Choose New Campaign, or Customize in Ready campaigns, to create a saved Draft. You can return to it later. I’ll wait for you to choose; this guide won’t create one for you.", { ready: 'interview', waiting: 'Open New Campaign or choose Customize yourself.', action: ['home', 'Open Home'] }),
        step('Choose a Ruleset', "How shall our game work? Choose a Ruleset, then Continue. SRD Lite provides D&D mechanics. No Ruleset gives us narrative play without D&D assumptions.", { target: '#construction-package', interviewUntil: 'ruleset', require: 'interview' }),
        step('Choose a Campaign Setting', "Now, where are we going? Choose a compatible world. It brings its own saved copy of the rules. Continue when you’ve chosen, or open Builder if you want to make a world of your own.", { target: '#construction-setting', interviewUntil: 'setting', require: 'interview' }),
        step('Choose an adventure', "Pick a Campaign Plan using its public premise. Some adventures need a particular protagonist or companions, so check the party requirements. I’ll keep the private GM preparation out of this preview.", { target: '#construction-template', interviewUntil: 'plan', require: 'interview' }),
        step('Answer the content questions', "A few worlds and adventures have questions of their own. Answer the ones shown. With existing compatible content and no additional requests, we can finish setup without an AI inference.", { target: '#construction-panel', interviewUntil: 'plan_contribution', require: 'interview' }),
        step('Name your campaign', "What shall we call this adventure? Keep the suggestion or give it your own name. Your Draft saves accepted answers, so we can pick this up later.", { target: '#construction-name', interviewUntil: 'name', require: 'interview' }),
        step('Use a starter character', "This is where we meet your character! Preview a compatible starter or choose a saved one. Edit a copy in Builder if you like. A fixed protagonist stays required unless you revise the Plan. Choose any permitted companions, then Continue.", { target: '#starter-character-preview, #rift-archetype, #rift-companions', interviewUntil: 'participants', require: 'interview' }),
        step('Set Boundaries and Preferences', "Tell me what’s off limits, and what you’d enjoy. Boundaries are hard limits; Preferences guide the experience more softly. Enter yours and Continue. Any unresolved Boundary conflict must be settled before we play.", { target: '#construction-boundaries', interviewUntil: 'boundaries_preferences', require: 'interview' }),
        step('Any other requests?', "Anything else you want for this adventure? Add what the earlier questions missed, or choose No additional requests. If Builder interprets a request in a way that changes your intent, review and confirm it first.", { target: '#construction-no-requests, #construction-premise', interviewUntil: 'specific_requests', require: 'interview' }),
        step('Review before beginning', "Take one last look at our spoiler-free setup. Ready to Begin starts play; Keep Building lets us prepare some more. Your independent Builder chats stay available. You can finish my lesson without starting the adventure yet.", { target: '#construction-review', require: 'interview' }),
      ],
    },
    builder: {
      title: 'Create with Builder', summary: 'Make reusable content and use it in a campaign.', time: '5–8 min', route: 'session',
      steps: [
        step('Open Builder', "Oh, I do love a good character idea. Open Build Something on Home, or Edit a copy in Builder from a starter preview. Builder uses your chosen AI service. Start the chat whenever you’re ready.", { ready: 'builder', waiting: 'Open an independent Builder chat to continue.', action: ['home', 'Open Home'] }),
        step('Describe the change', "Tell Builder what you have in mind, in ordinary language. A starter character’s personality or background is a nice first project. Review the proposed changes before accepting anything that changes your intent.", { target: '#player-input', event: 'builder-message', require: 'builder', waiting: 'Send your request yourself, or skip this step.' }),
        step('Review and save', "Let’s give your creation a proper home. Answer Builder’s questions, review the work, and ask it to validate and save the finished content to your reusable library. An unfinished chat isn’t a saved character or adventure yet.", { target: '#chronology', require: 'builder' }),
        step('Use your saved content', "Now we can bring it into a game! Return to the Campaign Interview and choose your compatible saved content. The Interview keeps its own place while you work in Builder. Editing a reusable source won’t automatically change existing campaigns.", { target: '#home-btn' }),
      ],
    },
    layout: {
      title: 'Arrange your play space', summary: 'Choose views and arrange game panels.', time: '2–4 min', route: 'session',
      steps: [
        step('Open a campaign', "Let’s get you comfortable. Open a campaign with game panels. We’re arranging your screen here, so these layout changes won’t take game actions.", { ready: 'campaign', waiting: 'Open a campaign to arrange its panels.', action: ['home', 'Choose a campaign'] }),
        step('Try Story and Board', "More room for our conversation, or more room for the game panels? Story and Board let you choose. Try either view; nothing says you have to keep it that way.", { target: '#view-switch', click: '[data-view-mode]', require: 'campaign' }),
        step('Resize your workspace', "A little more elbow room? Drag the divider between Story and the game panels. The message box resizes from its top edge too. Focus a divider to use its keyboard controls.", { target: '#sidebar-resize-handle, #composer-resize-handle', require: 'campaign' }),
        step('Arrange a panel', "Open a panel’s arrangement menu. Move it, group it into tabs, or give it a window of its own. Each panel lives in one place at a time. Close an extra game window and its panels return to the sidebar.", { target: '[data-panel-menu], #ruleset-panel-host', click: '[data-panel-menu]', require: 'campaign' }),
        step('Keep or restore a layout', "Make yourself at home. Save your Ruleset default, restore your own layout, or return to the Ruleset’s original arrangement. Campaign layout choices stay saved when you leave and come back.", { target: '.panel-layout-options, #ruleset-panel-host', require: 'campaign' }),
      ],
    },
    manage: {
      title: 'Manage and resume campaigns', summary: 'Reopen, recover, export and archive your adventures.', time: '2–3 min', route: 'home',
      steps: [
        step('Return to your adventure', "Welcome back! Home keeps your saved adventures and recent activity together. Open a campaign and we’ll continue where you left it. We’re resuming, not starting the adventure over.", { target: '#home-collections-title', ready: 'campaign', waiting: 'Open a saved campaign yourself, or skip this step.' }),
        step('Recover an interrupted reply', "A little interruption doesn’t have to end our adventure. Use the recovery controls shown with the response. Saved rolls and committed actions stay saved. Check the status before choosing your next action.", { target: '#chronology', require: 'campaign' }),
        step('Find export and recovery', "Let me show you where the backups live. Campaign data in Settings has export and recovery controls for your selected campaign. Export saves a backup file. Looking at the controls won’t change anything.", { target: '#export-campaign-btn', action: ['campaign-data', 'Open Campaign data'] }),
        step('Archive when you want to tidy up', "Making room for a new adventure? Archive keeps an item available to restore. Permanent deletion is separate and irreversible. There’s no need to archive, export or delete anything just to finish this lesson.", { target: '#settings-panel-campaign-data', action: ['campaign-data', 'Open Campaign data'] }),
      ],
    },
  };
  const interviewOrder = ['ruleset', 'setting', 'plan', 'ruleset_contribution', 'setting_contribution',
    'plan_contribution', 'name', 'participants', 'boundaries_preferences', 'specific_requests', 'review'];
  const mainLessons = ['home', 'connect', 'ready', 'play', 'combat', 'customize', 'builder', 'layout', 'manage'];
  const expressions = {
    home: ['welcome', 'point', 'point', 'sparkle', 'thumbs-up'],
    connect: ['thinking', 'reading', 'listening'],
    subscription: ['reassuring', 'listening', 'reading', 'thumbs-up'],
    api: ['thinking', 'presenting', 'shush', 'reading', 'thumbs-up'],
    local: ['thinking', 'presenting', 'reading', 'thumbs-up'],
    ready: ['presenting', 'listening', 'warm-smile', 'sparkle'],
    play: ['welcome', 'reading', 'listening', 'reassuring', 'warm-smile'],
    combat: ['confident', 'reading', 'speaking', 'confident', 'thinking', 'thumbs-up'],
    customize: ['sparkle', 'thinking', 'reading', 'presenting', 'listening',
      'speaking', 'reassuring', 'listening', 'thinking', 'thumbs-up'],
    builder: ['sparkle', 'listening', 'reading', 'celebrating'],
    layout: ['presenting', 'speaking', 'point', 'presenting', 'warm-smile'],
    manage: ['welcome', 'reassuring', 'reading', 'listening'],
  };

  function create(options) {
    const doc = options.document, win = doc.defaultView;
    const node = id => doc.getElementById(id);
    let storage;
    try { storage = options.storage || win.localStorage; } catch (_) { /* Memory-only preferences still work. */ }
    const read = key => { try { return JSON.parse(storage?.getItem(key) || 'null'); } catch (_) { return null; } };
    const saved = read(STORAGE_KEY), progress = {};
    for (const [id, value] of Object.entries(saved?.lessons || {})) {
      if (Object.hasOwn(lessons, id) && ['in_progress', 'completed', 'skipped'].includes(value?.status)) {
        progress[id] = { status: value.status, index: Math.max(0, Math.min(
          Number.isInteger(value.index) ? value.index : 0, lessons[id].steps.length - 1)) };
      }
    }
    const legacy = read(LEGACY_KEY);
    const legacyKnown = ['completed', 'skipped', 'in_progress'].includes(legacy?.status);
    if (!progress.home && legacyKnown) progress.home = { status: legacy.status, index: 0 };
    let welcomeSeen = saved?.welcomeSeen === true || legacyKnown;
    let journey = ['connect', 'ready', 'play'].includes(saved?.journey) ? saved.journey : null;
    let resumeId = Object.hasOwn(lessons, saved?.resumeId) ? saved.resumeId : null;
    let active = null, returnFocus = null, frame = null, finished = null, signature = '', generation = 0;
    const overlay = node('home-tutorial');
    const context = () => options.context();
    const persist = () => {
      try { storage?.setItem(STORAGE_KEY, JSON.stringify({ welcomeSeen, journey, resumeId, lessons: progress })); }
      catch (_) { /* No campaign data depends on tutorial persistence. */ }
    };
    const visible = el => Boolean(el && !el.closest('[hidden]') && el.getClientRects().length);
    const targetFor = selector => selector ? [...doc.querySelectorAll(selector)].find(visible) : null;
    function saveActive() {
      if (active) progress[active.id] = { status: 'in_progress', index: active.index };
      persist();
    }
    function hideGuide() {
      overlay.hidden = true;
      overlay.setAttribute('aria-hidden', 'true');
      doc.body.append(overlay);
    }
    function pause(status = 'in_progress') {
      if (!active) return;
      progress[active.id] = { status, index: active.index };
      active = null; generation += 1;
      persist(); hideGuide();
      if (returnFocus?.isConnected && visible(returnFocus)) returnFocus.focus();
      updateOffers();
    }
    async function navigate(route) {
      if (!route) return;
      try { await options.navigate(route); }
      catch (_) { node('tutorial-waiting').textContent = 'That page could not open. Open it yourself or pause and try again.'; }
      schedule();
    }
    async function start(id, { restart = false } = {}) {
      if (!Object.hasOwn(lessons, id)) return;
      saveActive();
      const selectedGeneration = ++generation;
      returnFocus = doc.activeElement;
      for (const dialog of doc.querySelectorAll('dialog[open]')) {
        if (dialog.id.startsWith('tutorial-')) dialog.close();
      }
      const previous = progress[id];
      active = { id, index: !restart && previous?.status === 'in_progress' ? previous.index : 0 };
      resumeId = id;
      welcomeSeen = true; signature = ''; saveActive();
      await navigate(lessons[id].route);
      if (selectedGeneration === generation && active) render(true);
    }
    function complete() {
      const id = active.id;
      progress[id] = { status: 'completed', index: active.index };
      active = null; hideGuide(); finished = id;
      const connectionDone = id === 'connect' || lessons[id].connection;
      if (journey === 'connect' && connectionDone && context().modelReady) {
        journey = 'ready';
        progress.connect = { status: 'completed', index: lessons.connect.steps.length - 1 };
      } else if (journey === 'ready' && id === 'ready' && context().campaign) journey = 'play';
      else if (journey === 'play' && id === 'play') journey = null;
      resumeId = journey;
      persist();
      node('tutorial-finished-title').textContent = lessons[id].title;
      node('tutorial-finished-copy').textContent = journey === 'ready'
        ? 'There we go! Now comes the fun part: choosing our adventure and meeting your party.'
        : journey === 'play' ? 'Our campaign has its own saved home now. I can show you how to play our first scene whenever you’re ready.'
        : journey === 'connect' ? 'A little setup can wait. Come back when you’re ready, and I’ll help you finish connecting.'
        : id === 'play' ? 'And that’s how we talk! I can’t wait to see what you try. If you need me to show you anything again, come find me in Tutorials.'
        : id === 'builder' ? 'I’m looking forward to seeing what you make. Bring your ideas, and we’ll find them a home in a game.'
        : 'That’s our little tour of this bit! You can replay it or pick another lesson whenever you like. I’ll be here.';
      node('tutorial-continue-btn').hidden = !journey;
      node('tutorial-continue-btn').textContent = journey === 'connect' ? 'Continue AI setup'
        : journey === 'ready' ? 'Choose an adventure' : 'Play your first scene';
      node('tutorial-finished').showModal();
      updateOffers();
    }
    function advance() {
      if (!active) return;
      if (active.index === lessons[active.id].steps.length - 1) { complete(); return; }
      active.index += 1; active.reviewing = false; signature = '';
      saveActive(); render(true);
    }
    function render(focus = false) {
      if (!active) return;
      const item = lessons[active.id], current = item.steps[active.index], ctx = context();
      // Readiness comes from current application projections, never a saved tutorial flag.
      const satisfied = current.ready ? Boolean(ctx[current.ready]) : current.interviewUntil
        ? ctx.interview && interviewOrder.indexOf(ctx.interviewStep) > interviewOrder.indexOf(current.interviewUntil)
        : false;
      if (!active.reviewing && satisfied) { advance(); return; }
      const admitted = !current.require || ctx[current.require];
      const target = admitted ? targetFor(current.target) : null;
      const modal = [...doc.querySelectorAll('dialog[open]')].find(el => !el.id.startsWith('tutorial-'));
      const parent = modal || doc.body;
      if (overlay.parentElement !== parent) parent.append(overlay);
      overlay.hidden = false; overlay.setAttribute('aria-hidden', 'false');
      const nextSignature = JSON.stringify([active.id, active.index, admitted, Boolean(target), satisfied]);
      if (nextSignature !== signature) {
        signature = nextSignature;
        node('tutorial-step').textContent = item.title + ' · ' + (active.index + 1) + ' / ' + item.steps.length;
        node('tutorial-title').textContent = current.title;
        node('tutorial-copy').textContent = current.copy;
        const waits = Boolean(((current.ready || current.interviewUntil) && !satisfied) || current.event || current.click || current.change || !admitted);
        node('tutorial-waiting').textContent = !admitted
          ? 'Return to the relevant campaign or chat to continue. You can also pause or skip this step.'
          : current.waiting || (waits ? 'Take your time. Try the highlighted control, or choose Skip step.' : '');
        node('tutorial-next-btn').disabled = waits;
        node('tutorial-next-btn').textContent = active.index === item.steps.length - 1 ? 'Finish' : 'Next';
        node('tutorial-back-btn').disabled = active.index === 0;
        node('tutorial-skip-step-btn').hidden = !waits;
        node('tutorial-action-btn').hidden = !current.action;
        node('tutorial-action-btn').textContent = current.action?.[1] || '';
        const routes = node('tutorial-routes'); routes.replaceChildren();
        for (const [id, label] of current.routes || []) {
          const button = doc.createElement('button');
          button.type = 'button'; button.className = 'btn btn-secondary'; button.textContent = label;
          button.addEventListener('click', () => void start(id));
          routes.append(button);
        }
        if (focus) node('tutorial-title').focus({ preventScroll: true });
      }
      position(target);
    }
    // Keep the card away from its target where space permits, and within the viewport.
    function position(target) {
      const guide = node('tutorial-guide'), sprite = node('tutorial-sylvia'), light = node('tutorial-spotlight');
      const margin = 14, gap = 18, width = win.innerWidth, height = win.innerHeight;
      light.hidden = !target; overlay.dataset.mode = target ? 'target' : 'intro';
      const rect = target?.getBoundingClientRect();
      if (rect) Object.assign(light.style, { left: (rect.left - 5) + 'px', top: (rect.top - 5) + 'px',
        width: (rect.width + 10) + 'px', height: (rect.height + 10) + 'px' });
      guide.dataset.placement = 'center';
      let box = guide.getBoundingClientRect(), placement = 'center';
      if (rect) {
        const rooms = { right: width - rect.right, left: rect.left, below: height - rect.bottom, above: rect.top };
        placement = ['right', 'left', 'below', 'above'].find(side =>
          rooms[side] >= (['left', 'right'].includes(side) ? Math.min(420, width - margin * 2) : box.height) + gap + margin) || 'center';
      }
      guide.dataset.placement = placement; box = guide.getBoundingClientRect();
      let left = (width - box.width) / 2, top = height - box.height - margin;
      if (placement === 'right') { left = rect.right + gap; top = rect.top; }
      if (placement === 'left') { left = rect.left - box.width - gap; top = rect.top; }
      if (placement === 'below') { left = rect.left; top = rect.bottom + gap; }
      if (placement === 'above') { left = rect.left; top = rect.top - box.height - gap; }
      guide.style.left = Math.max(margin, Math.min(left, width - box.width - margin)) + 'px';
      guide.style.top = Math.max(margin, Math.min(top, height - box.height - margin)) + 'px';
      let expression = expressions[active.id][active.index];
      if (expression === 'point') expression = placement === 'right' ? 'point-left-full'
        : placement === 'left' ? 'point-right-full' : 'presenting';
      sprite.src = 'assets/tutorial/sylvia-' + expression + '.png';
    }
    function schedule() {
      if (frame !== null) return;
      frame = win.requestAnimationFrame(() => { frame = null; render(); updateOffers(); });
    }
    function notify(event, sessionId = null) {
      if (!active) return;
      const current = lessons[active.id].steps[active.index], ctx = context();
      if (current.require && !ctx[current.require]) return;
      if (sessionId && sessionId !== ctx.sessionId) return;
      if (current.event === event) advance();
      else schedule();
    }
    function updateOffers() {
      const ctx = context();
      for (const button of doc.querySelectorAll('[data-tutorial-offer]')) {
        const required = button.dataset.tutorialRequire;
        button.hidden = Boolean(required && !ctx[required]) ||
          Boolean(button.dataset.tutorialSuggest && progress[button.dataset.tutorialOffer]?.status === 'completed');
      }
      const resumable = Object.entries(progress).find(([, value]) => value.status === 'in_progress');
      const resume = node('tutorial-resume-btn');
      resume.hidden = !journey && !resumable;
      const id = resumableLesson() || journey || resumable?.[0], text = id ? 'Resume: ' + lessons[id].title : '';
      if (resume.textContent !== text) resume.textContent = text;
    }
    function resumableLesson() {
      return resumeId && progress[resumeId]?.status === 'in_progress' ? resumeId : null;
    }
    function hub() {
      pause();
      const list = node('tutorial-lesson-list'); list.replaceChildren();
      for (const id of [...mainLessons, 'subscription', 'api', 'local']) {
        const item = lessons[id], row = doc.createElement('li'), button = doc.createElement('button');
        button.type = 'button'; button.className = 'tutorial-lesson';
        const title = doc.createElement('strong'), detail = doc.createElement('span'), status = doc.createElement('small');
        title.textContent = item.title; detail.textContent = item.summary;
        status.textContent = item.time + ' · ' + (progress[id]?.status === 'completed' ? 'Completed · Replay'
          : progress[id]?.status === 'in_progress' ? 'In progress · Resume' : progress[id]?.status === 'skipped' ? 'Skipped · Start again' : 'Start lesson');
        button.append(title, detail, status);
        button.addEventListener('click', () => void start(id));
        row.append(button); list.append(row);
      }
      node('tutorial-hub').showModal();
    }
    function maybeWelcome() {
      updateOffers();
      if (welcomeSeen || active || context().view !== 'home' || !context().loaded ||
          doc.querySelector('dialog[open]') || !node('settings-overlay').hidden) return;
      if (context().hasContent) { welcomeSeen = true; persist(); return; }
      node('tutorial-welcome').showModal();
    }
    function beginJourney() {
      welcomeSeen = true; journey = context().modelReady ? 'ready' : 'connect';
      persist(); void start(journey);
    }
    node('tutorial-next-btn').addEventListener('click', () => { if (!node('tutorial-next-btn').disabled) advance(); });
    node('tutorial-skip-step-btn').addEventListener('click', advance);
    node('tutorial-back-btn').addEventListener('click', () => {
      if (!active || !active.index) return;
      active.index -= 1; active.reviewing = true; signature = ''; saveActive(); render(true);
    });
    node('tutorial-pause-btn').addEventListener('click', () => pause());
    node('tutorial-skip-btn').addEventListener('click', () => { journey = null; pause('skipped'); });
    node('tutorial-action-btn').addEventListener('click', () => {
      if (active) void navigate(lessons[active.id].steps[active.index].action?.[0]);
    });
    doc.addEventListener('click', event => {
      const offer = event.target.closest('[data-tutorial-offer]');
      if (offer) { void start(offer.dataset.tutorialOffer); return; }
      if (!active || event.target.closest('#home-tutorial')) return;
      const current = lessons[active.id].steps[active.index];
      if (current.click && event.target.closest(current.click) && (!current.require || context()[current.require])) advance();
      schedule();
    });
    doc.addEventListener('change', event => {
      if (!active) return;
      const current = lessons[active.id].steps[active.index];
      if (current.change && event.target.matches(current.change) && !event.target.disabled) advance();
      schedule();
    });
    doc.addEventListener('keydown', event => {
      if (active && event.key === 'Escape') {
        event.preventDefault(); event.stopImmediatePropagation(); pause();
      }
    });
    for (const id of ['home-tutorials-btn', 'settings-tutorials-btn', 'session-tutorials-btn']) node(id).addEventListener('click', hub);
    node('replay-home-tutorial-btn').addEventListener('click', () => void start('home', { restart: true }));
    node('tutorial-hub-close').addEventListener('click', () => node('tutorial-hub').close());
    node('tutorial-welcome-play').addEventListener('click', beginJourney);
    node('home-get-started-btn').addEventListener('click', beginJourney);
    node('tutorial-welcome-connect').addEventListener('click', () => {
      welcomeSeen = true; journey = null; persist(); void start('connect');
    });
    const dismissWelcome = () => { welcomeSeen = true; persist(); node('tutorial-welcome').close(); };
    node('tutorial-welcome-explore').addEventListener('click', dismissWelcome);
    node('tutorial-welcome').addEventListener('cancel', dismissWelcome);
    node('tutorial-finished-close').addEventListener('click', () => node('tutorial-finished').close());
    node('tutorial-finished-hub').addEventListener('click', () => { node('tutorial-finished').close(); hub(); });
    node('tutorial-replay-btn').addEventListener('click', () => void start(finished, { restart: true }));
    node('tutorial-continue-btn').addEventListener('click', () => { if (journey) void start(journey); });
    node('tutorial-resume-btn').addEventListener('click', () => {
      const id = resumableLesson() || journey || Object.entries(progress).find(([, value]) => value.status === 'in_progress')?.[0];
      if (id) void start(id);
    });
    win.addEventListener('resize', schedule);
    doc.addEventListener('scroll', schedule, true);
    const observer = new win.MutationObserver(records => {
      if (records.some(record => !record.target.closest?.('#home-tutorial, #tutorial-hub, #tutorial-welcome, #tutorial-finished, .tutorial-entry'))) schedule();
    });
    observer.observe(doc.body, { subtree: true, childList: true, attributes: true,
      attributeFilter: ['hidden', 'open', 'disabled', 'class'] });
    persist(); updateOffers();
    return { start, pause, hub, notify, maybeWelcome, refresh: schedule };
  }
  root.SylviaTutorials = { create, lessons, mainLessons, expressions, STORAGE_KEY,
    supportsCombat: ruleset => ruleset === 'sylvia.srd-lite.bg3' };
  if (typeof module !== 'undefined') module.exports = root.SylviaTutorials;
})(typeof window === 'undefined' ? globalThis : window);
