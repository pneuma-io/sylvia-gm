/* Model, effort and speed for a new chat: shared option lists and the choice shown before
   a chat whose first model call would otherwise start on its own. Opening, changing or
   cancelling this choice never sends anything to a model. */
(function (root) {
  'use strict';

  const STANDARD = 'standard';
  const MODE_DEFAULT = 'mode_default';
  const PROVIDER_DEFAULT = 'provider_default';

  function effortLabel(value) {
    if (!value || value === PROVIDER_DEFAULT) return 'Provider default';
    if (value === 'xhigh') return 'X-High';
    return value.replace(/_/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
  }

  // What "Mode default" means for this model: one role, or both when no chat exists yet.
  function modeDefaultLabel(choice, role) {
    const mapped = choice?.reasoning_mode_defaults || {};
    if (role === 'dm' || role === 'builder') return `Mode default (${effortLabel(mapped[role])})`;
    if (!mapped.dm && !mapped.builder) return 'Mode default (Provider default)';
    return `Mode default (Game Master ${effortLabel(mapped.dm)} · Builder ${effortLabel(mapped.builder)})`;
  }

  // Options come from the model's capability data; a model name never implies a level.
  function effortOptions(choice, role) {
    const native = Array.isArray(choice?.supported_reasoning_efforts)
      ? choice.supported_reasoning_efforts : [];
    return [
      [MODE_DEFAULT, modeDefaultLabel(choice, role)],
      [PROVIDER_DEFAULT, 'Provider default'],
      ...native.map(value => [value, effortLabel(value)]),
    ];
  }

  function speedTiers(choice) {
    return Array.isArray(choice?.speed_options)
      ? choice.speed_options.filter(tier => tier && typeof tier.id === 'string' && tier.id) : [];
  }

  function speedOptions(choice) {
    return [[STANDARD, 'Standard'], ...speedTiers(choice).map(tier => [tier.id, tier.name || tier.id])];
  }

  function speedLabel(choice, value) {
    if (!value || value === STANDARD) return 'Standard';
    const tier = speedTiers(choice).find(item => item.id === value);
    return tier ? tier.name || tier.id : value;
  }

  // One honest sentence about the selected speed; never a promise of faster replies.
  function speedNote(choice, value) {
    const tiers = speedTiers(choice);
    if (!tiers.length) return 'This model’s connection has no speed choice. Replies use the provider’s standard speed.';
    if (!value || value === STANDARD) return 'Standard sends no speed setting; the provider’s own default applies.';
    const tier = tiers.find(item => item.id === value);
    if (!tier) return 'This speed is not available for this model. Choose another.';
    // Provider wording is shown as given, closed with a full stop so the next sentence reads cleanly.
    const description = (tier.description || 'requests this provider speed tier').replace(/[.\s]+$/, '');
    return `${tier.name || tier.id}: ${description}. ` +
      'Sylvia requests it; the provider decides the speed it actually uses.';
  }

  // What a new chat on this model inherits from Settings. A default the model does not
  // support falls back to the mode default effort or standard speed, as the owner does.
  function inherited(choice, defaults) {
    const effort = defaults?.reasoning_effort;
    const speed = defaults?.speed;
    const efforts = Array.isArray(choice?.supported_reasoning_efforts)
      ? choice.supported_reasoning_efforts : [];
    return {
      reasoning_effort: effort === PROVIDER_DEFAULT || efforts.includes(effort) ? effort : MODE_DEFAULT,
      speed: speedTiers(choice).some(tier => tier.id === speed) ? speed : STANDARD,
    };
  }

  function summary(choice, start, role) {
    const effort = start.reasoning_effort === MODE_DEFAULT
      ? modeDefaultLabel(choice, role) : effortLabel(start.reasoning_effort);
    return `${choice?.model_name || choice?.model_id || 'No model'} · ${effort} · ${speedLabel(choice, start.speed)} speed`;
  }

  function fill(select, options, value) {
    select.replaceChildren();
    for (const [optionValue, label] of options) {
      const option = select.ownerDocument.createElement('option');
      option.value = optionValue;
      option.textContent = label;
      select.append(option);
    }
    select.value = options.some(([optionValue]) => optionValue === value) ? value : options[0][0];
  }

  function create(options) {
    const node = id => options.document.getElementById(id);
    const dialog = node('chat-start-dialog');
    let settle = null;
    let role = null;
    let models = [];
    const selectedModel = () => models[Number(node('chat-start-model').value)] || null;

    function renderChoices(keep) {
      const choice = selectedModel();
      const base = inherited(choice, options.defaults());
      const effort = effortOptions(choice, role);
      const speed = speedOptions(choice);
      // Keep what the person already picked when the newly selected model still offers it.
      fill(node('chat-start-effort'), effort,
        keep && effort.some(([value]) => value === keep.reasoning_effort) ? keep.reasoning_effort : base.reasoning_effort);
      fill(node('chat-start-speed'), speed,
        keep && speed.some(([value]) => value === keep.speed) ? keep.speed : base.speed);
      node('chat-start-speed').disabled = speed.length < 2;
      renderNote();
    }

    function renderNote() {
      node('chat-start-speed-note').textContent = speedNote(selectedModel(), node('chat-start-speed').value);
    }

    function finish(value) {
      const resolve = settle;
      settle = null;
      if (dialog.open) dialog.close();
      if (resolve) resolve(value);
    }

    // Resolves with the chosen start, or null when cancelled, closed or already open.
    function choose(request) {
      models = options.models();
      if (settle || !models.length) return Promise.resolve(null);
      role = request.role;
      node('chat-start-title').textContent = request.title;
      node('chat-start-lead').textContent = request.lead ||
        'This new chat starts with your defaults from Settings. Change anything here for this chat only.';
      node('chat-start-confirm').textContent = request.action;
      const modelSelect = node('chat-start-model');
      fill(modelSelect, models.map((choice, index) => [String(index),
        `${choice.provider_name || choice.provider_id} · ${choice.model_name || choice.model_id}`]), '');
      const initial = models.findIndex(choice => choice.provider_id === request.model?.provider_id &&
        choice.model_id === request.model?.model_id);
      modelSelect.value = String(initial >= 0 ? initial : 0);
      renderChoices(null);
      return new Promise((resolve) => {
        settle = resolve;
        dialog.showModal();
        node('chat-start-confirm').focus();
      });
    }

    node('chat-start-model').addEventListener('change', () => renderChoices({
      reasoning_effort: node('chat-start-effort').value, speed: node('chat-start-speed').value,
    }));
    node('chat-start-speed').addEventListener('change', renderNote);
    node('chat-start-confirm').addEventListener('click', () => {
      const choice = selectedModel();
      if (!choice) return;
      finish({
        provider_id: choice.provider_id,
        model_id: choice.model_id,
        reasoning_effort: node('chat-start-effort').value,
        speed: node('chat-start-speed').value,
      });
    });
    node('chat-start-cancel').addEventListener('click', () => finish(null));
    // Escape and any other native close also cancel without sending anything.
    dialog.addEventListener('close', () => finish(null));
    return { choose };
  }

  const api = {
    create, inherited, effortLabel, effortOptions, modeDefaultLabel, speedOptions, speedLabel,
    speedNote, summary, STANDARD, MODE_DEFAULT, PROVIDER_DEFAULT,
  };
  root.SylviaChatStart = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window === 'undefined' ? globalThis : window);
