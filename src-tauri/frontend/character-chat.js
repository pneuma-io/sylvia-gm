/* Character chat: talk one-to-one with a saved Universal character card, outside any campaign. */
(function (root) {
  'use strict';

  const MAX_TEXT = 8000;

  // Build the dialog once; all card and model text is set with textContent, never as HTML.
  function markup(document) {
    const dialog = document.createElement('dialog');
    dialog.id = 'character-chat-dialog';
    dialog.className = 'character-chat-dialog';
    dialog.setAttribute('aria-labelledby', 'character-chat-title');
    dialog.innerHTML = `
      <header class="cc-header">
        <button class="btn btn-secondary btn-sm cc-back" id="character-chat-back" type="button" hidden>Chats</button>
        <h2 id="character-chat-title">Character chat</h2>
        <button class="btn btn-secondary btn-sm" id="character-chat-close" type="button">Close</button>
      </header>
      <div class="cc-body">
        <aside class="cc-side" id="character-chat-side" aria-label="Characters and saved chats">
          <section class="cc-new" aria-labelledby="character-chat-new-title">
            <h3 id="character-chat-new-title">Start a chat</h3>
            <label for="character-chat-card">Character</label>
            <select id="character-chat-card"></select>
            <p class="cc-card-summary" id="character-chat-card-summary"></p>
            <label for="character-chat-user-name">Your name in the chat</label>
            <input id="character-chat-user-name" type="text" maxlength="60" placeholder="You" autocomplete="off">
            <label for="character-chat-model">Model</label>
            <select id="character-chat-model"></select>
            <button class="btn btn-primary" id="character-chat-start" type="button">Start chat</button>
          </section>
          <section class="cc-saved" aria-labelledby="character-chat-saved-title">
            <h3 id="character-chat-saved-title">Saved chats</h3>
            <ul id="character-chat-saved"></ul>
          </section>
        </aside>
        <section class="cc-conversation" id="character-chat-conversation" aria-labelledby="character-chat-name" hidden>
          <div class="cc-identity">
            <h3 id="character-chat-name"></h3>
            <p id="character-chat-summary"></p>
          </div>
          <ol class="cc-log" id="character-chat-log" role="log" aria-live="polite"></ol>
          <p class="cc-typing" id="character-chat-typing" hidden></p>
          <form class="cc-composer" id="character-chat-form">
            <label class="visually-hidden" for="character-chat-input">Message</label>
            <textarea id="character-chat-input" rows="2" maxlength="${MAX_TEXT}" placeholder="Say something…"></textarea>
            <button class="btn btn-primary" id="character-chat-send" type="submit">Send</button>
          </form>
        </section>
        <p class="cc-empty" id="character-chat-empty">Choose a character to start a chat, or open a saved one.</p>
      </div>
      <p class="cc-status" id="character-chat-status" role="status" aria-live="polite"></p>`;
    document.body.append(dialog);
    return dialog;
  }

  // Show *actions* in italics, the common card convention, without interpreting any markup.
  function appendText(document, parent, text) {
    const pattern = /\*([^*\n]+)\*/g;
    let last = 0;
    for (const match of text.matchAll(pattern)) {
      if (match.index > last) parent.append(document.createTextNode(text.slice(last, match.index)));
      const action = document.createElement('em');
      action.textContent = match[1];
      parent.append(action);
      last = match.index + match[0].length;
    }
    if (last < text.length) parent.append(document.createTextNode(text.slice(last)));
  }

  function create(options) {
    const document = options.document;
    const dialog = document.getElementById('character-chat-dialog') || markup(document);
    const node = id => document.getElementById(id);
    let cards = [];
    let chats = [];
    let chat = null;
    let busy = false;
    let pendingStart = null;
    let pendingSend = null;

    const status = (text, kind = '') => {
      node('character-chat-status').textContent = text;
      node('character-chat-status').dataset.kind = kind;
    };
    const fail = (fallback, error) => status(options.error(fallback, error), 'error');
    const bridge = () => {
      const value = options.bridge();
      if (!value?.character_chat) throw new Error('Character chat is not available in this window');
      return value;
    };
    const selectedCard = () => cards.find(card => card.artifact_id === node('character-chat-card').value);
    const models = () => options.models() || [];
    const selectedModel = () => {
      const value = node('character-chat-model').value;
      return value === '' ? null : models()[Number(value)] || null;
    };
    const narrow = () => dialog.classList.contains('cc-narrow');

    function lock() {
      const composerReady = Boolean(chat?.card_available) && !busy;
      node('character-chat-start').disabled = busy || !selectedCard() || !selectedModel();
      node('character-chat-input').disabled = !composerReady;
      node('character-chat-send').disabled = !composerReady;
      for (const button of node('character-chat-saved').querySelectorAll('button')) button.disabled = busy;
    }

    function layout() {
      dialog.classList.toggle('cc-narrow', (dialog.clientWidth || root.innerWidth || 1024) < 700);
      const conversationOpen = Boolean(chat);
      node('character-chat-conversation').hidden = !conversationOpen;
      node('character-chat-empty').hidden = conversationOpen || narrow();
      node('character-chat-side').hidden = narrow() && conversationOpen;
      node('character-chat-back').hidden = !(narrow() && conversationOpen);
    }

    function renderCards() {
      const select = node('character-chat-card');
      const previous = select.value;
      select.replaceChildren();
      for (const card of cards) {
        const option = document.createElement('option');
        option.value = card.artifact_id;
        option.textContent = card.name;
        select.append(option);
      }
      if (cards.some(card => card.artifact_id === previous)) select.value = previous;
      renderCardSummary();
      const modelSelect = node('character-chat-model');
      const previousModel = selectedModel();
      const fallback = options.defaultModel();
      modelSelect.replaceChildren();
      models().forEach((choice, index) => {
        const option = document.createElement('option');
        option.value = String(index);
        option.textContent = `${choice.provider_name || choice.provider_id} · ${choice.model_name || choice.model_id}`;
        modelSelect.append(option);
      });
      const wanted = previousModel || fallback;
      const index = models().findIndex(choice => wanted &&
        choice.provider_id === wanted.provider_id && choice.model_id === wanted.model_id);
      modelSelect.value = index >= 0 ? String(index) : (models().length ? '0' : '');
    }

    function renderCardSummary() {
      const card = selectedCard();
      node('character-chat-card-summary').textContent = card
        ? (card.summary || '') + (card.has_greeting ? '' : ' This card has no greeting, so you speak first.')
        : 'No Universal character cards yet. Import one in Settings → Packages → Import Character Card.';
    }

    function renderSaved() {
      const list = node('character-chat-saved');
      list.replaceChildren();
      if (!chats.length) {
        const empty = document.createElement('li');
        empty.className = 'cc-saved-empty';
        empty.textContent = 'No saved chats yet.';
        list.append(empty);
      }
      for (const row of chats) {
        const item = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'cc-saved-chat';
        button.dataset.chatId = row.chat_id;
        if (chat?.chat_id === row.chat_id) button.setAttribute('aria-current', 'true');
        const name = document.createElement('strong');
        name.textContent = row.character_name;
        const preview = document.createElement('span');
        preview.textContent = row.preview || 'No messages yet';
        button.append(name, preview);
        button.addEventListener('click', () => { void openChat(row.chat_id); });
        item.append(button);
        list.append(item);
      }
    }

    function messageItem(role, text, extraClass = '') {
      const item = document.createElement('li');
      item.className = `cc-message cc-${role}${extraClass ? ` ${extraClass}` : ''}`;
      const speaker = document.createElement('span');
      speaker.className = 'cc-speaker';
      speaker.textContent = role === 'character' ? chat.character.name : chat.user_name;
      const body = document.createElement('p');
      appendText(document, body, text);
      item.append(speaker, body);
      return item;
    }

    function renderChat() {
      node('character-chat-name').textContent = chat.character.name;
      node('character-chat-summary').textContent = chat.card_available
        ? chat.character.summary || ''
        : 'This character card is no longer in your library. You can read this chat, but not continue it.';
      const log = node('character-chat-log');
      log.replaceChildren(...chat.messages.map(message => messageItem(message.role, message.text)));
      if (!chat.messages.length) {
        const hint = document.createElement('li');
        hint.className = 'cc-hint';
        hint.textContent = `Say hello to ${chat.character.name}.`;
        log.append(hint);
      }
      log.scrollTop = log.scrollHeight;
      renderSaved();
      layout();
      lock();
    }

    async function refresh() {
      const result = await bridge().character_chat({ action: 'catalog' });
      cards = result.cards || [];
      chats = result.chats || [];
      renderCards();
      renderSaved();
      if (result.unreadable_count) {
        status(`${result.unreadable_count} saved card or chat could not be read and is hidden.`, 'warning');
      }
    }

    async function openChat(chatId) {
      if (busy) return;
      busy = true; lock();
      status('Opening chat…');
      try {
        chat = await bridge().character_chat({ action: 'open', chat_id: chatId });
        pendingSend = null;
        renderChat();
        status('');
        node('character-chat-input').focus();
      } catch (error) {
        fail('Could not open that chat', error);
      } finally {
        busy = false; lock();
      }
    }

    async function startChat() {
      const card = selectedCard();
      const model = selectedModel();
      if (busy || !card || !model) return;
      const userName = node('character-chat-user-name').value.trim();
      const basis = [card.artifact_id, card.content_sha256, model.provider_id, model.model_id, userName].join('\n');
      // Repeating the same start (for example after a lost reply) reuses its identity.
      if (pendingStart?.basis !== basis) pendingStart = { basis, operation_id: options.uuid() };
      busy = true; lock();
      status(`Starting a chat with ${card.name}…`);
      try {
        chat = await bridge().character_chat({
          action: 'create', operation_id: pendingStart.operation_id,
          artifact_id: card.artifact_id, expected_content_sha256: card.content_sha256,
          provider_id: model.provider_id, model_id: model.model_id,
          user_name: userName || undefined,
        });
        pendingStart = null;
        pendingSend = null;
        await refresh();
        renderChat();
        status('');
        node('character-chat-input').focus();
      } catch (error) {
        fail('Could not start the chat', error);
      } finally {
        busy = false; lock();
      }
    }

    async function send() {
      const input = node('character-chat-input');
      const text = input.value.trim();
      if (busy || !chat?.card_available || !text) return;
      const basis = `${chat.chat_id}\n${chat.messages.length}\n${text}`;
      // An unchanged resend after an uncertain failure keeps its identity, so it cannot duplicate.
      if (pendingSend?.basis !== basis) pendingSend = { basis, operation_id: options.uuid() };
      busy = true; lock();
      const log = node('character-chat-log');
      log.querySelector('.cc-hint')?.remove();
      const draft = messageItem('user', text, 'cc-sending');
      log.append(draft);
      log.scrollTop = log.scrollHeight;
      node('character-chat-typing').textContent = `${chat.character.name} is writing…`;
      node('character-chat-typing').hidden = false;
      status('');
      try {
        chat = await bridge().send_character_chat_message({
          chat_id: chat.chat_id, operation_id: pendingSend.operation_id,
          text, expected_message_count: chat.messages.length,
        });
        pendingSend = null;
        input.value = '';
        const row = chats.find(item => item.chat_id === chat.chat_id);
        if (row) {
          row.preview = chat.messages.at(-1)?.text.slice(0, 140) || '';
          chats = [row, ...chats.filter(item => item !== row)];
        }
        renderChat();
      } catch (error) {
        draft.remove();
        fail('Your message was not sent. Your text is still here, so you can send it again', error);
      } finally {
        node('character-chat-typing').hidden = true;
        busy = false; lock();
        if (!input.disabled) input.focus();
      }
    }

    async function open() {
      if (!dialog.open) dialog.showModal();
      layout();
      status('Loading characters…');
      busy = true; lock();
      try {
        await refresh();
        status(models().length ? '' : 'Connect a model in Settings before starting a character chat.');
      } catch (error) {
        fail('Could not load character cards', error);
      } finally {
        busy = false; lock();
      }
    }

    node('character-chat-close').addEventListener('click', () => dialog.close());
    node('character-chat-back').addEventListener('click', () => {
      chat = null; pendingSend = null; layout(); renderSaved(); lock();
    });
    node('character-chat-card').addEventListener('change', () => { renderCardSummary(); lock(); });
    node('character-chat-model').addEventListener('change', lock);
    node('character-chat-start').addEventListener('click', () => { void startChat(); });
    node('character-chat-form').addEventListener('submit', event => { event.preventDefault(); void send(); });
    node('character-chat-input').addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        void send();
      }
    });
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    root.addEventListener?.('resize', () => { if (dialog.open) layout(); });

    return { open, openChat, state: () => ({ cards, chats, chat, busy }) };
  }

  root.SylviaCharacterChat = { create };
})(window);
