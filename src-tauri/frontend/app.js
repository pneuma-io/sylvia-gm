/* ============================================================
   Sylvia GM — Canonical Frontend (F6C tracer bullet)
   ============================================================
   The renderer is SECRETLESS:
     - No bearer token, port number, or database path appears here.
     - The Tauri host injects window.__sylviaGMBridge AFTER
       the launch handshake is verified.  The bridge is a thin JS façade
       over the in-process LocalCommandBus; its methods map 1:1 to the
       authority commands and return authoritative projections only.
     - The frontend never invents client-side truth.  Every render is a
       verbatim projection of data the owner returned.
     - Stop, manual Retry, and reconnect/replay are invoked exclusively
       through bridge commands (interrupt_stream / manual_retry_session /
       reconnect_stream), never via local fallback logic.
     - No automatic retry/fallback.  No D&D/TTRPG assumptions in any
       global or no-rules surface.
   Private diagnostics are Inspector-only and never appear in the
   player-facing chronology.
   ============================================================ */

const SylviaGMFrontend = (() => {
  'use strict';

  // ---- State: only authoritative projections; no secrets, no paths ----
  const state = {
    bridge: null,            // window.__sylviaGMBridge (injected by host)
    campaigns: [],           // authoritative campaign_index() result
    campaignIndexStatus: 'loading', // loading, ready, or a recoverable projection failure
    campaignIndexError: null,
    campaignIndexRequestId: 0,
    campaignIndexRefresh: null,
    builderChats: [],        // durable campaign-independent Builder conversations
    sidebarCollapsed: { left: false, right: false }, // presentation only, independent of campaign layout
    deletingBuilderChats: new Set(),
    archivingItems: new Set(),
    sessionDrafts: new Map(), // ephemeral compose text isolated by exact session identity
    builderQuestionDrafts: new Map(), // pending question fields isolated by Builder session
    sessionActivities: new Map(), // one stream/retry lifecycle record per exact session identity
    modelChoices: [],         // safe owner-projected model metadata
    providerSettings: null,   // safe provider metadata; credentials are never projected back
    riftTemplate: null,       // safe declarative setup metadata from the owner
    campaignPartyOptions: null, // party choices for the exact current Ruleset, Setting, and Plan
    campaignPartyOptionsKey: null,
    campaignPartyOptionsStatus: 'idle',
    campaignPartyOptionsGeneration: 0,
    campaignPartyRepair: null,
    constructionRestoreInProgress: false,
    constructionPanelGeneration: 0,
    applicationInfo: null,    // host-projected release identity; never campaign truth
    activeCampaignId: null,
    activeSessionId: null,
    activeSessionRole: null,
    activeSnapshot: null,
    campaignOpening: null, // selected display work only; never owns a running model turn
    displayHistory: null, // only the selected session's loaded display pages
    selectedTheme: 'slate',
    pendingModelGuidance: null,
    pendingModelMode: null,
    providerDiscovery: { offset: 0, total: 0, hasMore: false, models: [] },
    providerCapacity: null,
    providerModelMetadata: null,
    modelEditor: { route: 'api', providerId: null, modelId: null, step: 'connection' },
    savedModelNotice: null,
    pendingChatGPTModelId: null,
    adminAuditEnabled: false,
    adminAuditTraces: [],
    adminAuditRawTraceCount: 0,
    chatModelMode: 'current',
    sessionReasoning: null,  // owner projection, bound to the selected session identity
    reasoningPending: null,
    constructionStep: 0,
    constructionDraftId: null,
    constructionRevision: 0,
    constructionAnswers: {},
    constructionContributions: {},
    constructionNameSuggested: true,
    campaignCreationCatalog: [],
    rulesetCandidates: [],
    rulesetInspection: null,
    rulesetPackageIndex: 0,
    reusableCampaignPlans: [],
    pendingImportRepair: null,
    pendingCharacterCard: null,
    characterCardBusy: false,
    hasDerivedCampaignPlan: false,
    activeCreationSubflow: null,
    builderLaunchPending: new Set(),
    constructionPanelHome: null,
    constructionPanelNext: null,
    panelProjectionKey: null,
    panelProjectionRequestId: 0,
    panelProjection: null,
    panelActionChecks: new Map(),
    panelCommitPending: false,
    panelIntentPending: null,
    panelRecoveryAbsent: null,
    panelTabs: {},
    tacticalSelections: {},
    tacticalViews: {},
    networkSelections: {},
    crossSurfaceFocus: null,
    characterInspection: null,
    view: 'home',
    homeTutorialStep: 0,
    homeTutorialOpen: false,
    homeTutorialQueued: false,
    controlsWired: false,
    navigationRefreshBusy: false,
    campaignConfirmationPending: false,
    campaignHandoffPending: false,
    sequelDialog: null,
  };

  // ---- DOM helpers ----
  const $ = (id) => document.getElementById(id);
  let dictation = null;
  let dictationRevision = 0;
  let dictationModelJob = null;
  let dictationOptionsEpoch = 0;
  function savedDictationChoice(name) {
    try { return localStorage.getItem(`sylvia-dictation-${name}`) || ''; } catch (_) { return ''; }
  }

  // Metadata/readiness never starts audio or downloads a model.
  async function refreshDictationOptions(bridge, attemptId = null) {
    if (!bridge.dictation_capabilities) throw { code: 'unavailable' };
      const epoch = dictationOptionsEpoch;
      const capabilities = await bridge.dictation_capabilities(attemptId);
      if (epoch !== dictationOptionsEpoch) return capabilities;
      const engines = capabilities?.engines;
      if (!Array.isArray(engines) || !engines.length) throw { code: 'unavailable' };
      const select = $('dictation-language');
      const saved = select.value || savedDictationChoice('language');
      select.replaceChildren(...engines.map(engine => {
        const option = document.createElement('option');
        option.value = engine.id; option.textContent = engine.label || engine.language;
        return option;
      }));
      const preferred = engines.find(engine => engine.id === saved) || engines.find(engine =>
        engine.language === navigator.language || engine.language === (navigator.language || '').split('-')[0]);
      select.value = (preferred || engines[0]).id;
      const device = $('dictation-device'), selectedDevice = device.value || savedDictationChoice('device');
      const devices = capabilities.devices || [{ id: 'default', label: 'System default input' }];
      if (selectedDevice && selectedDevice !== 'default' && !devices.some(item => item.id === selectedDevice)) {
        devices.push({ id: selectedDevice, label: 'Selected microphone unavailable — choose an input' });
      }
      device.replaceChildren(...devices.map(item => {
        const option = document.createElement('option'); option.value = item.id; option.textContent = item.label; return option;
      }));
      device.value = devices.some(item => item.id === selectedDevice) ? selectedDevice : 'default';
      $('dictation-download').hidden = capabilities.ready !== false;
      $('dictation-model-status').textContent = capabilities.ready === false
        ? 'The local speech model is not downloaded. Download it once before dictating; no audio is sent.'
        : 'Local speech model ready. Recognition runs on CPU on this PC.';
      $('dictation-options').hidden = false;
      return capabilities;
  }

  async function dictationEngine(bridge, attemptId) {
    const capabilities = await refreshDictationOptions(bridge, attemptId);
    if (capabilities.ready === false) { openSettings('dictation'); throw { code: 'model' }; }
    return $('dictation-language').value;
  }

  function cancelDictationDownload() {
    if (!dictationModelJob) return;
    const { id, bridge } = dictationModelJob; dictationModelJob = null;
    dictationOptionsEpoch += 1;
    void Promise.resolve(bridge.dictation_cancel(id)).catch(() => {});
    $('dictation-model-status').textContent = 'Model download cancelled. Your draft is kept. You can retry.';
    $('dictation-download').textContent = 'Download local speech model';
    renderDictation();
  }

  async function prepareDictationModel() {
    if (dictationModelJob) { cancelDictationDownload(); return; }
    const bridge = state.bridge;
    if (!bridge?.dictation_prepare || !bridge.dictation_cancel) return;
    const job = { id: crypto.randomUUID(), bridge }; dictationModelJob = job;
    dictationOptionsEpoch += 1;
    $('dictation-model-status').textContent = 'Downloading the local speech model (about 148 MB). No audio is sent. You can cancel or keep typing.';
    $('dictation-download').textContent = 'Cancel model download';
    renderDictation();
    try {
      await bridge.dictation_prepare(job.id);
      if (dictationModelJob !== job) return;
      await refreshDictationOptions(bridge);
      if (dictationModelJob === job) dictation.clearError();
    } catch (error) {
      if (dictationModelJob !== job) return;
      $('dictation-model-status').textContent = (typeof error === 'string' ? error : error?.code) === 'storage'
        ? 'Not enough space for the speech model. Free some space and retry. Your draft is kept.'
        : 'Speech model download failed. Check your connection and retry. Your draft is kept.';
    } finally {
      if (dictationModelJob === job) {
        dictationModelJob = null;
        $('dictation-download').textContent = 'Download local speech model'; renderDictation();
      }
    }
  }

  // Dictation edits this composer only; the normal Send path owns all submissions.
  function initDictation() {
    if (dictation || !window.SylviaDictation || !$('dictation-toggle')) return;
    dictation = window.SylviaDictation.create({
      backend: { start: async ({ signal }) => {
        const bridge = state.bridge;
        if (!bridge?.dictation_start || !bridge.dictation_stop || !bridge.dictation_cancel) {
          throw { code: 'unavailable' };
        }
        const id = crypto.randomUUID();
        let cancelled = false;
        const cancel = () => {
          if (cancelled) return;
          cancelled = true;
          try { void Promise.resolve(bridge.dictation_cancel(id)).catch(() => {}); } catch (_) { /* Best-effort cleanup. */ }
        };
        signal.addEventListener('abort', cancel, { once: true });
        try {
          const engine = await dictationEngine(bridge, id);
          if (signal.aborted) throw { code: 'unavailable' };
          await bridge.dictation_start(id, engine, $('dictation-device').value || 'default');
          if (signal.aborted) { cancel(); throw { code: 'unavailable' }; }
          return { stop: () => bridge.dictation_stop(id), cancel: () => {
            signal.removeEventListener('abort', cancel); cancel();
          } };
        } catch (error) {
          signal.removeEventListener('abort', cancel); cancel(); throw error;
        }
      } },
      context: () => state.view === 'session' && state.activeSessionId
        ? JSON.stringify([state.activeCampaignId, state.activeSessionId, state.activeSessionRole]) : null,
      draft: () => ({ text: $('player-input').value, revision: dictationRevision,
        start: $('player-input').selectionStart, end: $('player-input').selectionEnd }),
      replace: (text, caret) => {
        const input = $('player-input');
        input.value = text;
        dictationRevision += 1;
        input.focus();
        input.setSelectionRange(caret, caret);
        saveSelectedSessionDraft();
      },
      changed: renderDictation,
    });
    $('dictation-toggle').addEventListener('click', toggleDictation);
    $('dictation-download').addEventListener('click', () => { void prepareDictationModel(); });
    for (const name of ['language', 'device']) $('dictation-' + name).addEventListener('change', () => {
      try { localStorage.setItem(`sylvia-dictation-${name}`, $('dictation-' + name).value); } catch (_) { /* Input works without storage. */ }
    });
    $('dictation-cancel').addEventListener('click', () => {
      dictation.cancel(); $('dictation-toggle').focus();
    });
    $('dictation-undo').addEventListener('click', () => dictation.undo());
    $('dictation-redo').addEventListener('click', () => { void dictation.start({ redo: true }); });
    $('dictation-insert').addEventListener('click', () => dictation.insertReview($('dictation-review').value));
    $('player-input').addEventListener('input', () => { dictationRevision += 1; dictation.sync(); });
    document.addEventListener('keydown', (event) => {
      if (event.isComposing || event.repeat) return;
      if (event.altKey && !event.ctrlKey && !event.metaKey && event.key.toLowerCase() === 'm' &&
          !$('dictation-toggle').disabled) {
        event.preventDefault(); toggleDictation();
      } else if (event.key === 'Escape' && dictationModelJob) {
        event.preventDefault(); cancelDictationDownload();
      } else if (event.key === 'Escape' && ['requesting', 'recording', 'transcribing'].includes(dictation.snapshot().phase)) {
        event.preventDefault(); dictation.cancel(); $('dictation-toggle').focus();
      }
    });
    window.addEventListener('pagehide', () => { dictation.cancel('', { announce: false }); cancelDictationDownload(); });
  }

  function toggleDictation() {
    if (!dictation || $('dictation-toggle').disabled) return;
    if (dictation.snapshot().phase === 'recording') void dictation.stop();
    else void dictation.start();
  }

  function renderDictation(value = dictation?.snapshot()) {
    if (!value || !$('dictation-toggle')) return;
    const busy = ['requesting', 'recording', 'transcribing'].includes(value.phase);
    const recording = value.phase === 'recording';
    const enabled = !$('player-input').disabled && state.view === 'session';
    $('dictation-toggle').disabled = (!enabled && !recording) ||
      ['requesting', 'transcribing'].includes(value.phase) || Boolean(dictationModelJob);
    $('dictation-toggle').setAttribute('aria-pressed', String(recording));
    $('dictation-toggle').setAttribute('aria-label', recording ? 'Stop dictation' : 'Start dictation');
    $('dictation-toggle-text').textContent = recording ? 'Stop dictation' : 'Dictate';
    if ($('dictation-status').textContent !== value.message) $('dictation-status').textContent = value.message;
    $('dictation-status').hidden = value.phase === 'idle' && !value.message;
    $('dictation-language').disabled = busy;
    $('dictation-device').disabled = busy || Boolean(dictationModelJob);
    $('dictation-download').disabled = busy;
    $('dictation-cancel').hidden = !busy && !value.transcript;
    $('dictation-undo').hidden = !value.canUndo || busy || !enabled;
    $('dictation-redo').hidden = !value.canUndo || busy || !enabled;
    $('dictation-review-panel').hidden = !value.transcript;
    if ($('dictation-review').dataset.transcript !== value.transcript) {
      $('dictation-review').value = value.transcript;
      $('dictation-review').dataset.transcript = value.transcript;
    }
  }
  let panelHost = null;
  let shotFrame = null;
  let panelRenderContext = null;
  const ACTION_QUEUE_PANEL_ID = 'sylvia.action-queue';
  const queueProjectionSources = new WeakMap();

  function gamePanels() {
    if (!panelHost) panelHost = window.SylviaPanelHost.create({
      state, renderPanel: panelMarkup,
      render: (projection = state.panelProjection) => renderRulesetPanelProjection(projection),
      dispatch: dispatchPanelWindowRequest, selection: applyPanelSelection,
      visible: (surface) => !['encounter', 'inventory'].includes(surface.renderer) || Boolean(panelContent(surface)),
      busy: () => selectedSessionIsWorking() || campaignDisplayPending(),
      status: (text) => { setInlineStatus(String(text), 'panels'); },
    });
    return panelHost;
  }

  // Redraw the current timeline into whichever map DOM exists after layout or camera changes.
  function animateShots() {
    if (shotFrame !== null) return;
    const draw = () => {
      shotFrame = null;
      const effects = gamePanels().effects;
      document.querySelectorAll?.('[data-shot-overlay]').forEach((overlay) => {
        const surfaceId = overlay.closest('[data-panel-id]')?.dataset.panelId;
        const surface = state.panelProjection?.surfaces.find((item) => item.id === surfaceId);
        const dimensions = surface && tacticalDimensions(surface.data);
        overlay.innerHTML = dimensions ? effects.frame(surface.data.map.id,
          (point) => tacticalWorldPoint(point, dimensions)) : '';
      });
      if (effects.snapshot().active.length && window.requestAnimationFrame) {
        shotFrame = window.requestAnimationFrame(draw);
      }
    };
    draw();
  }

  function shotControlsMarkup() {
    const settings = gamePanels().effects.preferences();
    return `<details class="shot-controls"><summary>Effects</summary>` +
      `<label>Visuals <select data-shot-visual aria-label="Shot visual effects">` +
      [['motion', 'Moving tracers'], ['static', 'Static lines'], ['off', 'Off']].map(([value, label]) =>
        `<option value="${value}" ${settings.visual === value ? 'selected' : ''}>${label}</option>`
      ).join('') + `</select></label>` +
      `<label>Volume <input data-shot-volume type="range" min="0" max="100" ` +
      `value="${Math.round(settings.volume * 100)}" aria-label="Effects volume"></label>` +
      `<label><input data-shot-muted type="checkbox" ${settings.muted ? 'checked' : ''}>Mute effects</label>` +
      `<button type="button" class="btn btn-secondary btn-sm" data-shot-skip>Skip current effects</button></details>`;
  }

  function panelRenderScope(projection) {
    return projection?.campaign_id && state.activeSessionId
      ? { campaignId: projection.campaign_id, sessionId: state.activeSessionId, rulesetId: projection.ruleset_id || null }
      : null;
  }

  // Owner replies can redraw a detached inspection control after its local event has finished.
  function captureCharacterFocus(container) {
    const active = document.activeElement;
    if (!active || !container.contains(active)) return null;
    const surfaceId = active.closest('[data-panel-id]')?.dataset.panelId;
    if (!surfaceId) return null;
    const attribute = ['data-party-select-actor', 'data-character-actor', 'data-inventory-character']
      .find(name => active.hasAttribute(name));
    if (attribute) return { surfaceId, selector: `[${attribute}="${CSS.escape(active.getAttribute(attribute))}"]` };
    const details = active.parentElement;
    if (active.tagName === 'SUMMARY' && details?.hasAttribute('data-character-expansion')) {
      return { surfaceId, selector: `[data-character-expansion="${CSS.escape(details.dataset.characterExpansion)}"] > summary` };
    }
    return null;
  }

  // Keep transient Effects UI state when the same panel DOM is replaced.
  function captureShotControls(container) {
    if (!panelRenderContext) return null;
    const active = document.activeElement;
    const controls = Array.from(container.querySelectorAll('.shot-controls')).map((details) => {
      const panelId = details.closest('[data-panel-id]')?.dataset.panelId;
      if (!panelId) return null;
      const focused = details.contains(active)
        ? ['visual', 'volume', 'muted', 'skip'].find((name) =>
            active?.hasAttribute?.(`data-shot-${name}`)) || null
        : null;
      return details.open || focused ? { panelId, open: details.open, focused } : null;
    }).filter(Boolean);
    return controls.length ? { scope: { ...panelRenderContext }, controls } : null;
  }

  // Restore disclosure and keyboard focus only inside the same session and panel.
  function restoreShotControls(container, saved, nextScope) {
    if (!saved || !nextScope ||
        saved.scope.campaignId !== nextScope.campaignId ||
        saved.scope.sessionId !== nextScope.sessionId) return;
    const panels = Array.from(container.querySelectorAll('[data-panel-id]'));
    saved.controls.forEach((entry) => {
      const panel = panels.find((item) => item.dataset.panelId === entry.panelId);
      const details = panel?.querySelector('.shot-controls');
      if (!details) return;
      if (entry.open || entry.focused) details.open = true;
      if (entry.focused) {
        panel.querySelector(`[data-shot-${entry.focused}]`)?.focus({ preventScroll: true });
      }
    });
  }

  // Map tools are local interaction state, scoped like the Effects disclosure above.
  function captureMapTools(container) {
    if (!panelRenderContext) return null;
    const active = document.activeElement;
    const attributes = ['data-coordinate-axis', 'data-tactical-actor',
      'data-tactical-target', 'data-network-entity'];
    const controls = Array.from(container.querySelectorAll('.map-detail-controls')).map((details) => {
      const panelId = details.closest('[data-panel-id]')?.dataset.panelId;
      if (!panelId) return null;
      let focused = null;
      if (details.contains(active)) {
        if (active === details.querySelector('summary')) focused = { summary: true };
        else {
          const attribute = attributes.find((name) => active?.hasAttribute?.(name));
          if (attribute) focused = { attribute, value: active.getAttribute(attribute) };
        }
      }
      return details.open || focused ? { panelId, open: details.open, focused } : null;
    }).filter(Boolean);
    return controls.length ? { scope: { ...panelRenderContext }, controls } : null;
  }

  function restoreMapTools(container, saved, nextScope) {
    if (!saved || !nextScope ||
        saved.scope.campaignId !== nextScope.campaignId ||
        saved.scope.sessionId !== nextScope.sessionId) return;
    const panels = Array.from(container.querySelectorAll('[data-panel-id]'));
    saved.controls.forEach((entry) => {
      const panel = panels.find((item) => item.dataset.panelId === entry.panelId);
      const details = panel?.querySelector('.map-detail-controls');
      if (!details) return;
      details.open = entry.open;
      if (entry.focused) {
        const field = entry.focused.summary ? details.querySelector('summary')
          : Array.from(details.querySelectorAll('[' + entry.focused.attribute + ']')).find((item) =>
              item.getAttribute(entry.focused.attribute) === entry.focused.value);
        field?.focus({ preventScroll: true });
      }
    });
  }

  document.addEventListener('pointerdown', () => {
    if (!window.__sylviaPanelContext) gamePanels().effects.unlockAudio();
  }, { capture: true });
  document.addEventListener('keydown', () => {
    if (!window.__sylviaPanelContext) gamePanels().effects.unlockAudio();
  }, { capture: true });

  // Extra windows submit the same declared panel commands through the currently selected main session.
  async function dispatchPanelWindowRequest(method, args, scope) {
    const current = () => scope.current() && state.activeCampaignId === scope.campaign_id &&
      state.activeSessionId === scope.session_id && state.activeSessionRole === 'dm';
    if (!current()) throw new Error('This campaign session is no longer selected.');
    if (method === 'snapshot') {
      const snapshot = await state.bridge.snapshot(scope.campaign_id);
      return { revision: snapshot.revision };
    }
    if (method === 'campaign_panels') return state.panelProjection;
    if (method === 'refresh') {
      await refreshSelectedCampaign();
      return null;
    }
    if (method === 'respond') {
      if (campaignDisplayPending()) throw new Error('Game state is still loading.');
      if (args.automatic) void respondToPlayerAction(scope.campaign_id, scope.session_id, args.actionId);
      else void submitInput('', { playerAction: { campaignId: scope.campaign_id,
        sessionId: scope.session_id, actionId: args.actionId, automatic: false } });
      return null;
    }
    if (method === 'queue_sequence_operation') {
      const allowed = ['campaign_id','session_id','surface_id','operation','sequence_id','expected_version','step_id','response_action_id'];
      if (!args || typeof args !== 'object' || Array.isArray(args) ||
          Object.keys(args).some(key => !allowed.includes(key)) ||
          args.campaign_id !== scope.campaign_id || args.session_id !== scope.session_id ||
          args.surface_id !== ACTION_QUEUE_PANEL_ID || !scope.owns(ACTION_QUEUE_PANEL_ID) ||
          !['execute','remove','review'].includes(args.operation) ||
          !(typeof args.sequence_id === 'string' && args.sequence_id && args.sequence_id.length <= 512 ||
            args.operation === 'execute' && args.sequence_id === null && args.expected_version === 0) ||
          (args.operation !== 'execute' && args.response_action_id !== undefined) ||
          (args.response_action_id !== undefined && args.response_action_id !== null &&
            (args.operation !== 'execute' || typeof args.response_action_id !== 'string' || !args.response_action_id || args.response_action_id.length > 512)) ||
          !Number.isSafeInteger(args.expected_version) || args.expected_version < 0 ||
          (args.operation === 'remove' ? typeof args.step_id !== 'string' || !args.step_id || args.step_id.length > 512 : args.step_id !== undefined)) {
        throw new Error('This queued-action panel request is invalid or the panel has moved.');
      }
      const result = await queueOperation(args.operation, args.step_id, {
        current: () => current() && scope.owns(ACTION_QUEUE_PANEL_ID),
        expected: {sequence_id:args.sequence_id,version:args.expected_version},
        responseActionId: args.response_action_id || null,
      });
      return queueView(result);
    }
    if (!['player_action_availability', 'preview_panel_action', 'queue_panel_action', 'panel_action_receipt'].includes(method)) {
      throw new Error('This operation is unavailable in a panel window.');
    }
    if (args.campaign_id !== scope.campaign_id ||
        (args.session_id && args.session_id !== scope.session_id)) {
      throw new Error('The action belongs to another session.');
    }
    const sources = method === 'player_action_availability'
      ? args.requests?.map((request) => request.source) : [{ kind: 'panel', surface_id: args.surface_id }];
    if (!Array.isArray(sources) || !sources.length || sources.some((source) =>
      source?.kind !== 'panel' || !scope.owns(source.surface_id))) {
      throw new Error('The action panel has moved or is unavailable.');
    }
    if (campaignDisplayPending()) throw new Error('Game state is still loading.');
    if (selectedSessionIsWorking()) throw new Error('Sylvia is working. Wait for her response to finish.');
    if (['preview_panel_action', 'queue_panel_action'].includes(method) && panelIntentPending()) {
      throw new Error('Finish the pending panel action before starting another.');
    }
    if (method === 'queue_panel_action') {
      const action = state.panelProjection?.surfaces.find(surface => surface.id === args.surface_id)
        ?.actions?.find(action => action.id === args.panel_action_id);
      if (!action || action.available === false) throw new Error('This declared panel action is unavailable.');
      return appendQueuedAction({operation_id:args.operation_id,
        source:{kind:'panel',surface_id:args.surface_id,action_id:args.panel_action_id},selection:args.selection},
        {flush:action.response_policy === 'automatic',current:() => current() && scope.owns(args.surface_id)});
    }
    return state.bridge[method](args);
  }

  function panelWindowBridge() {
    const request = (method, args) => gamePanels().request(method, args);
    return {
      snapshot: () => request('snapshot', {}),
      campaign_panels: () => request('campaign_panels', {}),
      player_action_availability: (command) => request('player_action_availability', command),
      preview_panel_action: (command) => request('preview_panel_action', command),
      queue_panel_action: (command) => request('queue_panel_action', command),
      queue_sequence_operation: (command) => request('queue_sequence_operation', command),
      panel_action_receipt: (command) => request('panel_action_receipt', command),
    };
  }

  // Inspection uses only admitted player-visible records and never grants action control.
  function inspectableCharacters() {
    const choices = new Map();
    for (const surface of state.panelProjection?.surfaces || []) {
      if (['party', 'unit_list', 'inventory'].includes(surface.renderer)) {
        for (const member of surface.data?.participants || []) {
          choices.set(member.id, { id: member.id, name: member.data?.name || surface.data?.entities?.[member.id]?.name || member.id });
        }
        for (const actor of state.panelProjection?.player_control?.actors || []) {
          if (surface.data?.entities?.[actor.id] && !choices.has(actor.id)) {
            choices.set(actor.id, { id: actor.id, name: surface.data.entities[actor.id].name || actor.name || actor.id });
          }
        }
      } else if (surface.renderer === 'generic_inventory') {
        for (const character of surface.data?.characters || []) choices.set(character.id, character);
      }
    }
    return Array.from(choices.values());
  }

  // One campaign/Ruleset inspection context survives panel placement, never stale roster identity.
  function characterInspection() {
    const campaignId = state.panelProjection?.campaign_id || state.activeCampaignId;
    const rulesetId = state.panelProjection?.ruleset_id || null;
    const choices = inspectableCharacters();
    let inspection = state.characterInspection;
    if (!inspection || inspection.campaignId !== campaignId || inspection.rulesetId !== rulesetId) {
      const prior = inspection ? null : state.panelProjection?.surfaces.filter((surface) => ['unit_list', 'inventory'].includes(surface.renderer))
        .map((surface) => state.tacticalSelections[surface.id]?.actor).find(Boolean) ||
        state.panelProjection?.surfaces.filter((surface) => ['tactical_grid', 'coordinate_map'].includes(surface.renderer))
          .map((surface) => tacticalActorId(surface, state.tacticalSelections[surface.id]?.actor)).find(Boolean);
      const focused = state.panelProjection?.player_control?.focused_actor_id;
      inspection = state.characterInspection = { campaignId, rulesetId,
        id: prior || (choices.some((item) => item.id === focused) ? focused : choices[0]?.id) || null,
        unavailableId: null, expanded: [] };
    }
    if (inspection.id && !choices.some((item) => item.id === inspection.id)) {
      inspection.unavailableId = inspection.id;
      inspection.id = null;
    }
    inspection.expanded = (inspection.expanded || []).filter((id) => choices.some((item) => item.id === id));
    return inspection;
  }

  function inspectCharacter(characterId) {
    if (characterId !== null && !inspectableCharacters().some((item) => item.id === characterId)) return;
    const inspection = characterInspection();
    inspection.id = characterId;
    inspection.unavailableId = null;
  }

  // Party is the shared picker; Rulesets without a roster retain an accessible common picker.
  function characterPickerMarkup() {
    const party = state.panelProjection?.surfaces.find((surface) => surface.renderer === 'party');
    if (party) return '';
    const inspection = characterInspection();
    return `<label class="character-picker">Inspect character <select data-character-actor>` +
      '<option value="">Choose a character</option>' + inspectableCharacters().map((item) =>
        `<option value="${escapeHtml(item.id)}"${item.id === inspection.id ? ' selected' : ''}>${escapeHtml(item.name)}</option>`).join('') + '</select></label>';
  }

  // Acting-character/target choices and inspection are distinct presentation requests.
  function applyPanelSelection(surfaceId, patch) {
    const surface = state.panelProjection?.surfaces.find((item) => item.id === surfaceId);
    if (!surface || !patch || typeof patch !== 'object') return;
    if ('character' in patch) {
      inspectCharacter(patch.character);
      return;
    }
    if (patch.expansion && surface.renderer === 'party' &&
        (surface.data?.participants?.some((member) => member.id === patch.expansion.id) ||
          surface.data?.entities?.[patch.expansion.id] && playerControlsActor(patch.expansion.id)) &&
        typeof patch.expansion.open === 'boolean') {
      const inspection = characterInspection();
      inspection.expanded = inspection.expanded.filter((id) => id !== patch.expansion.id);
      if (patch.expansion.open) inspection.expanded.push(patch.expansion.id);
      return;
    }
    if (['party', 'unit_list', 'inventory', 'generic_inventory'].includes(surface.renderer)) {
      if ('actor' in patch) inspectCharacter(patch.actor);
      return;
    }
    if (surface.renderer === 'network_graph') {
      const allowed = ['node', 'link', 'remote_function', 'route_step'];
      const chosen = Object.fromEntries(Object.entries(patch).filter(([key]) => allowed.includes(key)));
      if ('actor' in patch && (patch.actor === null ||
          (typeof patch.actor === 'string' && networkControlsActor(patch.actor)))) {
        chosen.actor = patch.actor;
      }
      Object.assign(networkSelection(surface), chosen);
      if ('actor' in chosen) synchronizePanelActor(chosen.actor);
      return;
    }
    const actorRef = ['tactical_grid', 'coordinate_map'].includes(surface.renderer)
      ? tacticalActorId(surface, patch.actor) : patch.actor;
    if ('actor' in patch && (patch.actor === null || playerControlsActor(actorRef))) synchronizePanelActor(actorRef);
    const maps = state.panelProjection.surfaces.filter((item) => ['tactical_grid', 'coordinate_map'].includes(item.renderer));
    maps.forEach((map) => {
      const shared = {};
      for (const key of ['actor', 'target']) {
        if (!(key in patch)) continue;
        let value = patch[key];
        if (key === 'actor' && value !== null) {
          value = map.data.entities.find((item) => tacticalActorId(map, item.id) === actorRef)?.id;
        }
        if (value === null || (typeof value === 'string' && map.data.entities.some((item) => item.id === value) &&
            (key !== 'actor' || tacticalControlsActor(map, value)))) shared[key] = value;
      }
      if (map.id === surfaceId) {
        for (const key of ['destination', 'aim_point', 'area', 'orientation']) {
          if (key in patch) shared[key] = patch[key];
        }
        for (const [key, records] of [['zone', map.data.zones], ['device', map.data.entities]]) {
          if (key in patch && (patch[key] === null || records?.some((item) => item.id === patch[key]))) {
            shared[key] = patch[key];
          }
        }
      }
      Object.assign(tacticalSelection(map), shared);
    });
  }

  // Share valid actor identities across views without moving a camera or writing campaign state.
  function synchronizePanelActor(actorId) {
    inspectCharacter(actorId);
    for (const panel of state.panelProjection?.surfaces || []) {
      if (panel.renderer === 'network_graph' && (actorId === null || networkControlsActor(actorId))) {
        networkSelection(panel).actor = actorId;
      } else if (['tactical_grid', 'coordinate_map'].includes(panel.renderer)) {
        const body = panel.data?.entities?.find((item) => tacticalActorId(panel, item.id) === actorId);
        if (actorId === null || body) tacticalSelection(panel).actor = body?.id || null;
      }
    }
  }

  const VALID_THEMES = new Set(['slate', 'sylvia', 'sylvia-dark']);
  const VALID_SETTINGS_TABS = new Set([
    'models', 'accounts', 'appearance', 'dictation', 'packages', 'campaign-data', 'admin', 'about'
  ]);
  const DISCONNECTED_STREAM_KEY = 'sylvia-gm-disconnected-stream';
  const PANEL_LAYOUT_KEY = 'sylvia-gm-panel-layout-v1';
  const HOME_TUTORIAL_KEY = 'sylvia-gm-home-tutorial-v1';
  const PENDING_PARTY_SELECTION_KEY = 'sylvia-gm-pending-party-selection-v1';
  const HOME_TUTORIAL_STEPS = [
    {
      title: 'Hi, I\u2019m Sylvia.',
      copy: 'Let me show you the important stuff real quick.',
      sprite: 'wave',
    },
    {
      targetId: 'home-new-campaign-btn',
      title: 'New Campaign',
      copy: 'Start a new personalized campaign using all the goodies you currently have installed.',
      preferredPlacements: ['right', 'below', 'left', 'above'],
    },
    {
      targetId: 'home-build-btn',
      title: 'Build Something',
      copy: 'Open Builder when you want to create or change campaign plans, settings, characters, or Rulesets.',
      preferredPlacements: ['left', 'below', 'right', 'above'],
    },
    {
      targetId: 'home-settings-btn',
      title: 'Settings',
      copy: 'Connect models and manage appearance, packages, campaign data, and app details here.',
      preferredPlacements: ['below', 'left', 'right', 'above'],
    },
  ];
  const TUTORIAL_SPRITES = {
    wave: 'assets/tutorial/sylvia-wave.png',
    left: 'assets/tutorial/sylvia-point-left.png',
    right: 'assets/tutorial/sylvia-point-right.png',
    up: 'assets/tutorial/sylvia-point-up.png',
    down: 'assets/tutorial/sylvia-point-down.png',
  };
  let settingsReturnFocus = null;
  let tutorialReturnFocus = null;

  // Keep every provider turn's renderer-owned lifecycle under its session identity.
  function ensureSessionActivity({
    sessionId = state.activeSessionId,
    sessionRole = state.activeSessionRole,
    campaignId = state.activeCampaignId,
    ownerId = null,
  } = {}) {
    if (!sessionId) return null;
    let activity = state.sessionActivities.get(sessionId);
    if (!activity) {
      activity = {
        sessionId,
        sessionRole,
        campaignId,
        ownerId: ownerId || (sessionRole === 'builder' ? sessionId : campaignId),
        phase: 'idle',
        attemptId: null,
        acknowledgedSequence: 0,
        attachmentGeneration: null,
        restored: false,
        stopRequestedAttemptId: null,
        retryBasis: null,
        interruptedAttemptId: null,
        pendingInput: null,
        projectionTimer: null,
        projectionPollBusy: false,
        heartbeatTimer: null,
        heartbeatBusy: false,
      };
      state.sessionActivities.set(sessionId, activity);
    } else {
      if (sessionRole) activity.sessionRole = sessionRole;
      if (campaignId !== undefined) activity.campaignId = campaignId;
      if (ownerId) activity.ownerId = ownerId;
      else if (!activity.ownerId) {
        activity.ownerId = activity.sessionRole === 'builder' || activity.contextCompaction
          ? sessionId : activity.campaignId;
      }
    }
    return activity;
  }

  function activityForSession(sessionId) {
    return sessionId ? state.sessionActivities.get(sessionId) || null : null;
  }

  function selectedActivity() {
    return activityForSession(state.activeSessionId);
  }

  // A new attempt owns a new object, so callbacks from an older attempt cannot change it.
  function beginSessionActivity(previous, attemptId) {
    stopStreamHeartbeat(previous);
    stopProjectionPolling(previous);
    const activity = {
      ...previous, attemptId, phase: 'working', attachmentGeneration: null,
      terminalLabel: null,
      restored: false, stopRequestedAttemptId: null, pendingInput: null,
      projectionTimer: null, projectionPollBusy: false,
      heartbeatTimer: null, heartbeatBusy: false,
    };
    state.sessionActivities.set(activity.sessionId, activity);
    persistDisconnectedActivities();
    return activity;
  }

  function currentActivity(activity) {
    return Boolean(activity && activityForSession(activity.sessionId) === activity);
  }

  function selectedActivityMatches(activity) {
    return currentActivity(activity) && state.view === 'session' &&
      state.activeSessionId === activity.sessionId;
  }

  function activityIsWorking(activity) {
    return Boolean(activity && activity.phase === 'working');
  }

  function selectedSessionIsWorking() {
    if (window.__sylviaPanelContext) return Boolean(state.panelWindowBusy);
    return activityIsWorking(selectedActivity());
  }

  function anySessionIsWorking() {
    return [...state.sessionActivities.values()].some(activityIsWorking);
  }

  function activityStatusLabel(sessionId, fallback = 'active') {
    const activity = activityForSession(sessionId);
    if (!activity || activity.phase === 'idle') return fallback;
    const labels = {
      working: 'Working…',
      completed: 'Completed',
      stopped: 'Stopped',
      interrupted: 'Interrupted',
    };
    return labels[activity.phase] || fallback;
  }

  // Put feedback beside its action; collapsed panels fall back to the visible composer.
  function setInlineStatus(message, target = 'chat') {
    let id = 'session-status-text';
    // Detached windows have one status element and none of the main shell's destinations.
    if (window.__sylviaPanelContext) id = 'session-status-text';
    else if (target === 'campaign-data') id = 'campaign-data-status';
    else if (target === 'campaign-management') id = 'campaign-management-status';
    else if (target === 'campaign-deletion') id = 'campaign-deletion-status';
    else if (target === 'construction' && !$('construction-panel').hidden) id = 'construction-status';
    else if (target === 'panels' && !state.sidebarCollapsed.right && state.view === 'session')
      id = 'panel-action-status';
    else if (state.view === 'home') id = 'home-action-status';
    if (id === 'construction-status' && $('session-status-text').textContent === 'Loading campaign Interview…') {
      $('session-status-text').textContent = '';
      $('session-status-text').hidden = true;
    }
    const output = $(id);
    output.textContent = String(message || '');
    output.hidden = !message;
  }

  function selectedStatus(activity, text, target = 'chat') {
    if (selectedActivityMatches(activity)) {
      setInlineStatus(text, target);
    }
  }

  // Saved outcomes can finish the same attempt, but cannot replace a newer local submission.
  function reconcileBuilderActivity(activity, reported) {
    if (!currentActivity(activity) || !reported || typeof reported.attempt_id !== 'string') return;
    if (activity.phase !== 'idle' && activity.attemptId !== reported.attempt_id) return;
    const phase = {
      active: 'working', completed: 'completed', stopped: 'stopped', interrupted: 'interrupted',
      unavailable: 'interrupted',
    }[reported.status];
    if (!phase) return;
    const wasIdle = activity.phase === 'idle';
    if (!wasIdle && !activityIsWorking(activity) && phase === 'working') return;
    activity.attemptId = reported.attempt_id;
    if (phase === 'working') {
      activity.phase = phase;
      if (wasIdle) {
        activity.restored = true;
        persistDisconnectedActivities();
        startStreamHeartbeat(activity, true);
        startProjectionPolling(activity);
      }
      return;
    }
    if (!wasIdle && activity.phase === phase) return;
    finishSessionActivity(activity, phase, phase === 'completed' ? 'Builder completed' :
      phase === 'stopped' ? 'Builder stopped · Saved work retained' :
        'Builder generation was interrupted · Saved work retained');
  }

  function setWebResearchPermission(enabled) {
    const button = $('allow-public-web');
    button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
    button.textContent = enabled ? 'Web on' : 'Web';
  }

  function webResearchPermission() {
    return $('allow-public-web').getAttribute('aria-pressed') === 'true';
  }

  function createHostBridge() {
    const invoke = window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke;
    if (!invoke) return null;
    return {
      application_info: () => invoke('application_info'),
      dictation_capabilities: (id = null) => id ? invoke('dictation_capabilities', { id }) : invoke('dictation_capabilities'),
      dictation_start: (id, engine, device) => invoke('dictation_start', { id, engine, device }),
      dictation_prepare: id => invoke('dictation_prepare', { id }),
      dictation_stop: (id) => invoke('dictation_stop', { id }),
      dictation_cancel: (id) => invoke('dictation_cancel', { id }),
      campaign_index: () => invoke('campaign_index'),
      campaign_creation_catalog: () => invoke('campaign_creation_catalog'),
      campaign_sequel_context: (request) => invoke('campaign_sequel_context', { request }),
      preview_campaign_sequel: (request) => invoke('preview_campaign_sequel', { request }),
      append_campaign_sequel: (request) => invoke('append_campaign_sequel', { request }),
      delete_reusable_library_entry: (command) => invoke('delete_reusable_library_entry', {
        kind: command.kind,
        artifactId: command.artifact_id,
        expectedContentSha256: command.expected_content_sha256,
        confirmation: command.confirmation,
      }),
      campaign_party_options: (command) => invoke('campaign_party_options', {
        rulesetSelection: command.ruleset_selection,
        settingId: command.setting_id,
        planId: command.plan_id,
      }),
      builder_chat_index: () => invoke('builder_chat_index'),
      create_builder_chat: (command) => invoke('create_builder_chat', {
        operationId: command.operation_id,
        providerId: command.provider_id,
        modelId: command.model_id,
        displayName: command.display_name,
      }),
      rename_builder_chat: (sessionId, displayName) =>
        invoke('rename_builder_chat', { sessionId, displayName }),
      archive_builder_chat: (sessionId) => invoke('archive_builder_chat', { sessionId }),
      restore_builder_chat: (sessionId) => invoke('restore_builder_chat', { sessionId }),
      delete_builder_chat: (sessionId, confirmation) =>
        invoke('delete_builder_chat', { sessionId, confirmation }),
      snapshot: (campaignId, loadId = null) => invoke('display_snapshot', { campaignId, loadId }),
      player_projection: (campaignId, loadId = null) => invoke('player_projection', { campaignId, loadId }),
      player_projection_page: (campaignId, afterSequence, loadId = null) =>
        invoke('player_projection_page', { campaignId, afterSequence, loadId }),
      campaign_panels: (campaignId, afterRevision = null, loadId = null) =>
        invoke('campaign_panels', { campaignId, afterRevision, loadId }),
      cancel_display_load: (loadId) => invoke('cancel_display_load', { loadId }),
      action_sequence: (command) => invoke('action_sequence', {sessionId:command.session_id, sequenceId:command.sequence_id || null}),
      compose_action_sequence: (command) => invoke('compose_action_sequence', {command}),
      execute_action_sequence: (command) => invoke('execute_action_sequence', {command}),
      player_action_availability: (command) => invoke('player_action_availability', {
        campaignId: command.campaign_id, expectedRevision: command.expected_revision,
        requests: command.requests,
      }),
      execute_panel_action: (command) => invoke('execute_panel_action', {
        campaignId: command.campaign_id,
        surfaceId: command.surface_id,
        panelActionId: command.panel_action_id,
        operationId: command.operation_id,
        expectedRevision: command.expected_revision,
        selection: command.selection || null,
        sessionId: command.session_id || null,
        confirmation: command.confirmation || null,
      }),
      preview_panel_action: (command) => invoke('preview_panel_action', {
        campaignId: command.campaign_id,
        surfaceId: command.surface_id,
        panelActionId: command.panel_action_id,
        operationId: command.operation_id,
        expectedRevision: command.expected_revision,
        selection: command.selection || null,
        sessionId: command.session_id || null,
      }),
      panel_action_receipt: (command) => invoke('panel_action_receipt', {
        campaignId: command.campaign_id,
        surfaceId: command.surface_id,
        panelActionId: command.panel_action_id,
        operationId: command.operation_id,
        expectedRevision: command.expected_revision,
        selection: command.selection || null,
        sessionId: command.session_id || null,
      }),
      execute_player_action: (command) => invoke('execute_player_action', {
        campaignId: command.campaign_id,
        sessionId: command.session_id,
        operationId: command.operation_id,
        expectedRevision: command.expected_revision,
        source: command.source,
        selection: command.selection,
        confirmation: command.confirmation || null,
      }),
      preview_player_action: (command) => invoke('preview_player_action', {
        campaignId: command.campaign_id,
        sessionId: command.session_id,
        operationId: command.operation_id,
        expectedRevision: command.expected_revision,
        source: command.source,
        selection: command.selection,
      }),
      resolve_mechanics_choice: (command) => invoke('resolve_mechanics_choice', command),
      session_context: (sessionId) => invoke('session_context', { sessionId }),
      inspect_provider_thinking: (sessionId, attemptId, responseId) =>
        invoke('inspect_provider_thinking', { sessionId, attemptId, responseId }),
      session_history_page: (sessionId, beforeSequence = null) =>
        invoke('session_history_page', { sessionId, beforeSequence, limit: 50 }),
      steer_session: (sessionId, messageId, text) => invoke('steer_session', {
        sessionId,
        messageId,
        text,
      }),
      package_candidates: () => invoke('package_candidates'),
      inspect_package_candidate: (candidateId) => invoke('inspect_package_candidate', {
        candidateId,
      }),
      package_candidate_text: (candidateId, packageId, path) =>
        invoke('package_candidate_text', { candidateId, packageId, path }),
      compare_package_candidates: (leftCandidateId, rightCandidateId) =>
        invoke('compare_package_candidates', { leftCandidateId, rightCandidateId }),
      export_package_candidate_file: (exportId, candidateId) =>
        invoke('export_package_candidate_file', { exportId, candidateId }),
      export_campaign_plan_file: (exportId, planId, expectedContentSha256) =>
        invoke('export_campaign_plan_file', { exportId, planId, expectedContentSha256 }),
      rift_template: () => invoke('rift_template'),
      start_rift_campaign: (command) => invoke('start_rift_campaign', {
        operationId: command.operation_id,
        campaignId: command.campaign_id,
        builderSessionId: command.builder_session_id,
        rulesetSelection: command.ruleset_selection,
        settingId: command.setting_id,
        planId: command.plan_id,
        displayName: command.display_name,
        lane: command.lane,
        archetypeId: command.archetype_id,
        companionIds: command.companion_ids,
        customizationNotes: command.customization_notes,
        providerId: command.provider_id,
        modelId: command.model_id,
        constructionIntent: command.construction_intent,
      }),
      start_guided_campaign: (command) => invoke('start_guided_campaign', {
        operationId: command.operation_id,
        campaignId: command.campaign_id,
        builderSessionId: command.builder_session_id,
        displayName: command.display_name,
        candidateId: command.candidate_id,
        premise: command.premise,
        settingId: command.setting_id,
        planId: command.plan_id,
        playerCharacterId: command.player_character_id,
        companionIds: command.companion_ids,
        constructionIntent: command.construction_intent,
      }),
      start_guided_creation_subflow: (command) => invoke('start_guided_creation_subflow', {
        operationId: command.operation_id,
        campaignId: command.campaign_id,
        parentSessionId: command.parent_session_id,
        expectedConstructionRevision: command.expected_construction_revision,
        purpose: command.purpose,
        visibleAnswer: command.visible_answer,
        triggerMessageId: command.trigger_message_id || null,
        characterProfileId: command.character_profile_id || null,
        characterProfileSha256: command.character_profile_sha256 || null,
      }),
      return_guided_creation_subflow: (command) => invoke('return_guided_creation_subflow', {
        operationId: command.operation_id,
        campaignId: command.campaign_id,
        parentSessionId: command.parent_session_id,
        childSessionId: command.child_session_id,
        expectedConstructionRevision: command.expected_construction_revision,
        useResult: command.use_result,
      }),
      model_choices: () => invoke('model_choices'),
      provider_settings: () => invoke('provider_settings'),
      open_provider_setup: (presetId) => invoke('open_provider_setup', { presetId }),
      chatgpt_connection: (action, model = null) => invoke('chatgpt_connection', { action, model }),
      admin_full_audit_status: () => invoke('admin_full_audit_status'),
      admin_full_audit_record: (index) => invoke('admin_full_audit_record', { index }),
      open_admin_audit_window: () => invoke('open_admin_audit_window'),
      export_admin_audit_file: () => invoke('export_admin_audit_file'),
      set_admin_full_audit: (enabled) => invoke('set_admin_full_audit', { enabled }),
      discover_provider_models: (command) => invoke('discover_provider_models', {
        providerId: command.provider_id,
        presetId: command.preset_id,
        baseUrl: command.base_url,
        credential: command.credential,
        headers: command.headers,
        query: command.query,
        offset: command.offset,
        limit: command.limit,
      }),
      detect_local_providers: () => invoke('detect_local_providers'),
      validate_provider_model: (command) => invoke('validate_provider_model', {
        providerId: command.provider_id,
        presetId: command.preset_id,
        baseUrl: command.base_url,
        credential: command.credential,
        modelId: command.model_id,
        transport: command.transport,
        headers: command.headers,
      }),
      save_provider_settings: (command) => invoke('save_provider_settings', {
        providerId: command.provider_id,
        presetId: command.preset_id,
        displayName: command.display_name,
        baseUrl: command.base_url,
        credential: command.credential,
        modelId: command.model_id,
        modelName: command.model_name,
        contextWindow: command.context_window,
        declaredContextWindow: command.declared_context_window,
        declaredContextSource: command.declared_context_source,
        runtimeContextWindow: command.runtime_context_window,
        runtimeContextSource: command.runtime_context_source,
        configuredContextWindow: command.configured_context_window,
        contextObservedAt: command.context_observed_at,
        declaredContextObservedAt: command.declared_context_observed_at,
        runtimeContextObservedAt: command.runtime_context_observed_at,
        configuredContextObservedAt: command.configured_context_observed_at,
        maxOutputTokens: command.max_output_tokens,
        declaredMaxOutputTokens: command.declared_max_output_tokens,
        builderOutputTokens: command.builder_output_tokens,
        transport: command.transport,
        headers: command.headers,
        supportsStreaming: command.supports_streaming,
        supportsTools: command.supports_tools,
      }),
      remove_provider_settings: (providerId) => invoke(
        'remove_provider_settings', { providerId }
      ),
      switch_session_model: (command) => invoke('switch_session_model', {
        sessionId: command.session_id,
        providerId: command.provider_id,
        modelId: command.model_id,
        mode: command.mode,
      }),
      switch_session_reasoning: (command) => invoke('switch_session_reasoning', {
        sessionId: command.session_id,
        effort: command.effort,
        mode: command.mode,
      }),
      set_default_model: (providerId, modelId) => invoke('set_default_model', {
        providerId,
        modelId,
      }),
      submit_session_input: (command) => invoke('submit_session_input', {
        sessionId: command.session_id,
        turnId: command.turn_id,
        attemptId: command.attempt_id,
        messageId: command.message_id,
        userText: command.user_text,
        oocText: command.ooc_text || null,
        allowPublicWeb: command.allow_public_web,
        builderStart: Boolean(command.builder_start),
        playerActionId: command.player_action_id || null,
        automaticResponse: Boolean(command.automatic_response),
        redirectInterruptionId: command.redirect_interruption_id || null,
        campaignEndingChoice: command.campaign_ending_choice || null,
        ...(command.builder_question_answers ? { builderQuestionAnswers: command.builder_question_answers } : {}),
      }),
      start_dm_session: (campaignId, sessionId, turnId) => invoke('start_dm_session', {
        campaignId,
        sessionId,
        turnId,
      }),
      ingest_builder_sources: (sessionId, ingestionId) => invoke('ingest_builder_sources', {
        sessionId,
        ingestionId,
      }),
      interrupt_stream: (campaignId, attemptId) => invoke('interrupt_stream', {
        campaignId,
        attemptId,
      }),
      interrupt_provider_stream: (campaignId, attemptId) => invoke(
        'interrupt_provider_stream', { campaignId, attemptId }
      ),
      disconnect_stream: (campaignId, attemptId, attachmentGeneration) => invoke('disconnect_stream', {
        campaignId,
        attemptId,
        attachmentGeneration,
      }),
      disconnect_provider_stream: (campaignId, attemptId, attachmentGeneration) => invoke(
        'disconnect_provider_stream', { campaignId, attemptId, attachmentGeneration }
      ),
      stream_heartbeat: (campaignId, attemptId, attachmentGeneration) => invoke('stream_heartbeat', {
        campaignId,
        attemptId,
        attachmentGeneration,
      }),
      provider_stream_heartbeat: (campaignId, attemptId, attachmentGeneration) => invoke(
        'provider_stream_heartbeat', { campaignId, attemptId, attachmentGeneration }
      ),
      narration_attempt_status: (campaignId, attemptId) => invoke(
        'narration_attempt_status', { campaignId, attemptId }
      ),
      provider_stream_status: (campaignId, attemptId) => invoke(
        'provider_stream_status', { campaignId, attemptId }
      ),
      retryable_interruption: (campaignId, failedAttemptId) => invoke('retryable_interruption', {
        campaignId,
        failedAttemptId,
      }),
      manual_retry_session: (sessionId, interruptionId, retryId) => invoke(
        'manual_retry_session', { sessionId, interruptionId, retryId }
      ),
      reconnect_stream: (
        campaignId, attemptId, acknowledgedSequence, attachmentGeneration
      ) => invoke('reconnect_stream', {
        campaignId,
        attemptId,
        acknowledgedSequence,
        attachmentGeneration,
      }),
      reconnect_provider_stream: (campaignId, attemptId, attachmentGeneration) => invoke(
        'reconnect_provider_stream', { campaignId, attemptId, attachmentGeneration }
      ),
      install_package_file: (installId) => invoke('install_package_file', { installId }),
      preview_character_card_file: () => invoke('preview_character_card_file'),
      confirm_character_card_import: (digest) => invoke('confirm_character_card_import', { sourceSha256: digest }),
      start_import_repair: (command) => invoke('start_import_repair', {
        repairId: command.repair_id, operationId: command.operation_id,
        providerId: command.provider_id, modelId: command.model_id,
      }),
      create_guided_campaign_draft: (command) => invoke('create_guided_campaign_draft', {
        operationId: command.operation_id,
        displayName: command.display_name,
        providerId: command.provider_id,
        modelId: command.model_id,
      }),
      navigate_guided_campaign_draft: (command) => invoke('navigate_guided_campaign_draft', {
        operationId: command.operation_id,
        campaignId: command.campaign_id,
        builderSessionId: command.builder_session_id,
        expectedConstructionRevision: command.expected_construction_revision,
        targetStep: command.target_step,
      }),
      advance_guided_campaign_draft: (command) => invoke('advance_guided_campaign_draft', {
        operationId: command.operation_id,
        campaignId: command.campaign_id,
        builderSessionId: command.builder_session_id,
        expectedConstructionRevision: command.expected_construction_revision,
        currentStep: command.current_step,
        visibleAnswer: command.visible_answer,
        answer: command.answer,
      }),
      guided_campaign_contribution: (builderSessionId, contributionStep) => invoke(
        'guided_campaign_contribution', {
          builderSessionId,
          contributionStep,
        }
      ),
      save_reusable_campaign_plan: (command) => invoke('save_reusable_campaign_plan', {
        operationId: command.operation_id,
        campaignId: command.campaign_id,
        builderSessionId: command.builder_session_id,
        expectedConstructionRevision: command.expected_construction_revision,
        title: command.title,
      }),
      confirm_new_campaign: (command) => invoke('confirm_new_campaign', {
        operationId: command.operation_id,
        displayName: command.display_name,
        candidateId: command.candidate_id,
        turnId: command.turn_id,
        providerId: command.provider_id,
        modelId: command.model_id,
      }),
      begin_campaign_construction: (command) => invoke('begin_campaign_construction', {
        operationId: command.operation_id,
        campaignId: command.campaign_id,
        lane: command.lane,
        expectedRevision: command.expected_revision,
        canonicalInput: command.canonical_input || {},
      }),
      model_guidance: (campaignId, modelId, overrideAccepted) => invoke(
        'model_guidance', { campaignId, modelId, overrideAccepted }
      ),
      finalize_campaign_construction: (
        campaignId, dmProviderId, dmModelId, modelGuidanceOverride
      ) => invoke(
        'finalize_campaign_construction', {
          campaignId,
          dmProviderId,
          dmModelId,
          modelGuidanceOverride,
        }
      ),
      catalog: (campaignId) => invoke('catalog', { campaignId }),
      stop_agent_session: (sessionId) => invoke('stop_agent_session', { sessionId }),
      resume_agent_session: (sessionId) => invoke('resume_agent_session', { sessionId }),
      rename_campaign: (operationId, campaignId, displayName, expectedRevision) => invoke(
        'rename_campaign', { operationId, campaignId, displayName, expectedRevision }
      ),
      archive_campaign: (operationId, campaignId, expectedRevision, designatedSessionId) => invoke(
        'archive_campaign', { operationId, campaignId, expectedRevision, designatedSessionId }
      ),
      restore_archived_campaign: (operationId, campaignId, expectedRevision, sessionId) => invoke(
        'restore_archived_campaign', { operationId, campaignId, expectedRevision, sessionId }
      ),
      trash_campaign: (operationId, campaignId, expectedRevision, designatedSessionId) => invoke(
        'trash_campaign', { operationId, campaignId, expectedRevision, designatedSessionId }
      ),
      restore_trashed_campaign: (operationId, campaignId, expectedRevision) => invoke(
        'restore_trashed_campaign', { operationId, campaignId, expectedRevision }
      ),
      purge_campaign: (operationId, campaignId, expectedRevision, confirmation) => invoke(
        'purge_campaign', { operationId, campaignId, expectedRevision, confirmation }
      ),
      backup_authority: (backupId) => invoke('backup_authority', { backupId }),
      export_campaign: (exportId, operationId, campaignId, includeThinking = false) => invoke('export_campaign', {
        exportId,
        operationId,
        campaignId,
        includeThinking,
      }),
      export_campaign_file: (exportId, operationId, campaignId, includeThinking = false) => invoke(
        'export_campaign_file', { exportId, operationId, campaignId, includeThinking }
      ),
      recover_campaign: (exportId, recoveryId) => invoke('recover_campaign', {
        exportId,
        recoveryId,
      }),
      recover_campaign_file: (recoveryId) => invoke('recover_campaign_file', { recoveryId }),
    };
  }

  // ---- Init ----
  async function init() {
    if (window.__sylviaPanelContext) {
      state.bridge = panelWindowBridge();
      try {
        await gamePanels().init();
      } catch (error) {
        setInlineStatus(`Could not load campaign panels: ${error}. Close this window to return the panels to the sidebar, then try again.`, 'panels');
      }
      return;
    }
    // The host injects window.__sylviaGMBridge once the handshake is
    // verified.  If it is absent (e.g. opened outside the Tauri shell),
    // the overlay stays visible and the frontend does nothing — it
    // never guesses a port or invents an endpoint.
    state.bridge = window.__sylviaGMBridge || createHostBridge();
    if (!state.bridge) {
      $('connecting-overlay').classList.remove('hidden');
      $('connecting-overlay').querySelector('.connecting-text').textContent =
        'Starting Sylvia GM…';
      // Poll lightly — the host may inject the bridge shortly after load.
      setTimeout(() => { void init(); }, 500);
      return;
    }
    // Handshake already verified by the host before injection.
    $('connecting-overlay').classList.add('hidden');
    $('session-frame').style.display = 'flex';
    wireControls();
    initTheme();
    initSidebarResize();
    await gamePanels().init();
    await refreshApplicationInfo();
    await refreshProviderSettings();
    await refreshModelChoices();
    await refreshNavigationIndexes();
    await restoreDisconnectedStream();
    showHome();
    // Wire the despilled portrait asset — the host resolves the local
    // path; the renderer never loads remote content.
    if (state.bridge.resolvePortraitAsset) {
      const src = state.bridge.resolvePortraitAsset();
      if (src) $('despilled-portrait').src = src;
    }
    // Periodically refresh the navigation rails without monopolizing the owner lane.
    setInterval(() => {
      // Keep the owner command lane clear for stream heartbeats. Large local-model
      // prefills can make index reads slow enough to queue past the attachment lease.
      if (anySessionIsWorking()) return;
      void refreshNavigationIndexes();
    }, 15000);
  }

  function wireControls() {
    if (state.controlsWired) return;
    state.controlsWired = true;
    initComposerResize();

    document.querySelectorAll('.theme-btn').forEach((button) => {
      button.addEventListener('click', () => setTheme(button.dataset.theme));
    });
    $('open-settings-btn').addEventListener('click', () => openSettings());
    $('close-settings-btn').addEventListener('click', closeSettings);
    document.querySelectorAll('[data-settings-tab]').forEach((button) => {
      button.addEventListener('click', () => {
        showSettingsTab(button.dataset.settingsTab);
        if (button.dataset.settingsTab === 'admin') void refreshAdminAudit();
        if (button.dataset.settingsTab === 'packages') void refreshRulesetBrowser();
      });
    });
    $('settings-overlay').addEventListener('click', (event) => {
      if (event.target === $('settings-overlay')) closeSettings();
    });
    document.addEventListener('keydown', (event) => {
      if ($('settings-overlay').hidden) return;
      if (event.key === 'Escape') {
        closeSettings();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = [...$('settings-overlay').querySelectorAll(
        'button:not(:disabled), select:not(:disabled), input:not(:disabled), ' +
        'textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
      )].filter((element) => element.offsetParent !== null);
      if (focusable.length === 0) return;
      const current = focusable.indexOf(document.activeElement);
      const next = event.shiftKey
        ? (current <= 0 ? focusable.length - 1 : current - 1)
        : (current === focusable.length - 1 ? 0 : current + 1);
      event.preventDefault();
      focusable[next].focus();
    });
    $('install-package-btn').addEventListener('click', () => { void installSelectedPackage(); });
    $('import-character-card-btn').addEventListener('click', () => { void previewSelectedCharacterCard(); });
    $('confirm-character-card-btn').addEventListener('click', () => { void confirmSelectedCharacterCard(); });
    $('cancel-character-card-btn').addEventListener('click', () => {
      if (state.characterCardBusy) return;
      state.pendingCharacterCard = null; $('character-card-preview').hidden = true;
    });
    $('repair-import-btn').addEventListener('click', () => { void startImportRepair(); });
    $('ruleset-refresh-btn').addEventListener('click', () => { void refreshRulesetBrowser(); });
    $('ruleset-candidate-select').addEventListener('change', () => {
      void inspectSelectedRulesetCandidate();
    });
    $('ruleset-compare-select').addEventListener('change', updateRulesetCompareButton);
    $('ruleset-compare-btn').addEventListener('click', () => { void compareRulesetCandidates(); });
    $('ruleset-export-btn').addEventListener('click', () => { void exportRulesetCandidate(); });
    $('ruleset-package-list').addEventListener('click', (event) => {
      const button = event.target.closest('[data-ruleset-package-index]');
      if (button) selectRulesetPackage(Number(button.dataset.rulesetPackageIndex));
    });
    $('ruleset-file-list').addEventListener('click', (event) => {
      const button = event.target.closest('[data-ruleset-file-path]');
      if (button && !button.disabled) void previewRulesetFile(button.dataset.rulesetFilePath);
    });
    $('provider-preset').addEventListener('change', () => {
      state.modelEditor = { ...state.modelEditor, providerId: null, modelId: null };
      renderSelectedProviderPreset();
    });
    $('add-model-btn').addEventListener('click', showAddModelChoices);
    document.querySelectorAll('[data-models-back]').forEach(button =>
      button.addEventListener('click', () => showModelsOverview()));
    document.querySelectorAll('[data-model-route]').forEach(button =>
      button.addEventListener('click', () => beginModelConnection(button.dataset.modelRoute)));
    $('use-existing-provider-btn').addEventListener('click', useExistingModelConnection);
    $('provider-continue-btn').addEventListener('click', () => { void continueModelConnection(); });
    $('provider-setup-btn').addEventListener('click', () => { void openProviderSetup(); });
    $('provider-edit-connection-btn').addEventListener('click', () => showModelEditorStep('connection'));
    $('provider-manual-entry-btn').addEventListener('click', () => {
      showModelEditorStep('model');
      $('provider-manual-model').open = true;
      $('provider-model-id').focus();
    });
    $('set-added-model-default-btn').addEventListener('click', () => {
      const saved = state.savedModelNotice;
      const index = state.modelChoices.findIndex(choice => choice.provider_id === saved?.provider_id &&
        choice.model_id === saved?.canonical_model_id);
      if (index < 0) return;
      $('model-choice').value = String(index);
      void setSelectedDefaultModel();
    });
    document.querySelectorAll('[data-chatgpt-action]').forEach((button) => {
      button.addEventListener('click', () => updateChatGPTConnection(button.dataset.chatgptAction));
    });
    $('open-account-signin-btn').addEventListener('click', () => {
      state.pendingChatGPTModelId = '';
      showSettingsTab('accounts');
    });
    $('account-models-btn').addEventListener('click', () => showModelsOverview());
    $('chatgpt-model-choice').addEventListener('change', selectChatGPTModel);
    ['chatgpt-model-context', 'chatgpt-model-output'].forEach(id =>
      $(id).addEventListener('input', renderChatGPTCapacity));
    $('chatgpt-automatic-limits-btn').addEventListener('click', () => {
      const facts = selectedChatGPTFacts();
      if (facts.context) $('chatgpt-model-context').value = '';
      if (facts.output) $('chatgpt-model-output').value = '';
      renderChatGPTCapacity();
    });
    $('discover-provider-models-btn').addEventListener('click', () => {
      void discoverSelectedProviderModels();
    });
    $('load-more-provider-models-btn').addEventListener('click', () => {
      void discoverSelectedProviderModels(true);
    });
    $('provider-discovered-models').addEventListener('change', selectDiscoveredProviderModel);
    $('provider-context-window').addEventListener('input', updateConfiguredProviderCapacity);
    $('provider-output-limit').addEventListener('input', () => renderProviderCapacity());
    $('provider-automatic-limits-btn').addEventListener('click', () => {
      $('provider-context-window').value = '';
      $('provider-output-limit').value = '';
      $('provider-builder-output-limit').value = '';
      updateConfiguredProviderCapacity();
    });
    $('provider-model-id').addEventListener('input', clearStaleDiscoveredProviderCapacity);
    $('detect-local-providers-btn').addEventListener('click', () => {
      void detectLocalProviders();
    });
    $('validate-provider-btn').addEventListener('click', () => {
      void validateSelectedProvider();
    });
    $('save-provider-btn').addEventListener('click', () => { void saveSelectedProvider(); });
    $('toggle-admin-audit-btn').addEventListener('click', () => { void toggleAdminAudit(); });
    $('refresh-admin-audit-btn').addEventListener('click', () => { void refreshAdminAudit(); });
    $('open-admin-audit-btn').addEventListener('click', () => { void openAdminAuditWindow(); });
    $('admin-audit-search').addEventListener('input', renderAdminAuditTraces);
    $('copy-admin-audit-btn').addEventListener('click', () => { void copyVisibleAdminAudit(); });
    $('export-admin-audit-btn').addEventListener('click', () => { void exportAdminAudit(); });
    $('configured-provider-list').addEventListener('click', (event) => {
      const model = event.target.closest('[data-edit-model]');
      if (model) {
        const provider = state.providerSettings?.configured?.find(item => item.provider_id === model.dataset.modelProvider);
        if (provider) beginModelConnection(provider.mode === 'local' ? 'local' : 'api', provider.provider_id, model.dataset.editModel);
        return;
      }
      const add = event.target.closest('[data-add-provider]');
      if (add) {
        const provider = state.providerSettings?.configured?.find(item => item.provider_id === add.dataset.addProvider);
        if (provider) beginModelConnection(provider.mode === 'local' ? 'local' : 'api', provider.provider_id);
        return;
      }
      const edit = event.target.closest('[data-edit-provider]');
      if (edit) {
        editConfiguredProvider(edit.dataset.editProvider);
        return;
      }
      const button = event.target.closest('[data-remove-provider]');
      if (button) void removeConfiguredProvider(button.dataset.removeProvider);
    });
    $('home-btn').addEventListener('click', showHome);
    for (const side of ['left', 'right']) {
      $(`toggle-${side}-sidebar-btn`).addEventListener('click', () => {
        setSidebarCollapsed(side, !state.sidebarCollapsed[side]);
      });
    }
    $('home-new-campaign-btn').addEventListener('click', () => {
      void startCampaignBuilder();
    });
    $('home-build-btn').addEventListener('click', () => { void startBuilderChat(); });
    $('campaign-sequel-btn').addEventListener('click', () => { void openCampaignSequel(); });
    $('campaign-sequel-preview').addEventListener('click', () => { void previewCampaignSequel(); });
    $('campaign-sequel-append').addEventListener('click', () => { void appendCampaignSequel(); });
    $('campaign-sequel-author').addEventListener('click', () => { void authorCampaignSequel(); });
    $('campaign-sequel-cancel').addEventListener('click', () => {
      if (!state.sequelDialog?.busy) $('campaign-sequel-dialog').close();
    });
    $('campaign-sequel-dialog').addEventListener('cancel', (event) => {
      if (state.sequelDialog?.busy) event.preventDefault();
    });
    $('campaign-sequel-dialog').addEventListener('close', () => { state.sequelDialog = null; });
    $('campaign-sequel-source').addEventListener('change', () => {
      if (!state.sequelDialog?.request) resetSequelReview();
    });
    for (const id of ['campaign-sequel-connection', 'campaign-sequel-reviewed']) {
      $(id).addEventListener('input', refreshSequelConfirmation);
    }
    $('home-settings-btn').addEventListener('click', () => openSettings());
    $('tutorial-skip-btn').addEventListener('click', () => closeHomeTutorial('skipped'));
    $('tutorial-next-btn').addEventListener('click', advanceHomeTutorial);
    $('replay-home-tutorial-btn').addEventListener('click', replayHomeTutorial);
    window.addEventListener('resize', positionHomeTutorial);
    document.addEventListener('keydown', handleHomeTutorialKeydown);
    $('new-builder-chat-btn').addEventListener('click', () => { void startBuilderChat(); });
    $('open-campaign-setup-btn').addEventListener('click', () => {
      void showConstructionPanel();
    });
    $('construction-template').addEventListener('change', updateConstructionMode);
    $('construction-reselect-party-btn').addEventListener('click', reselectCampaignParty);
    $('delete-reusable-plan-btn').addEventListener('click', () => {
      void deleteSelectedReusablePlan();
    });
    $('export-campaign-plan-btn').addEventListener('click', exportSelectedCampaignPlan);
    $('construction-package').addEventListener('change', updateConstructionCompatibility);
    $('construction-setting').addEventListener('change', updateConstructionPlans);
    $('construction-name').addEventListener('input', () => {
      state.constructionNameSuggested = false;
    });
    $('rift-archetype').addEventListener('change', () => {
      persistSelectedPendingParty();
      updateCampaignPartyActionAvailability();
      updateReusableLibraryDeleteControls();
    });
    $('delete-reusable-player-btn').addEventListener('click', () => {
      const item = (state.campaignPartyOptions?.player_archetypes || []).find(
        (candidate) => candidate.id === $('rift-archetype').value
      );
      if (item) void deleteReusableLibraryEntry(item);
    });
    $('rift-companions').addEventListener('click', (event) => {
      const button = event.target.closest('[data-delete-reusable-companion]');
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      const item = (state.campaignPartyOptions?.companions || []).find(
        (candidate) => candidate.id === button.dataset.deleteReusableCompanion
      );
      if (item) void deleteReusableLibraryEntry(item);
    });
    document.querySelectorAll('[data-start-creation-subflow]').forEach((button) => {
      button.addEventListener('click', () => {
        void startCreationSubflow(
          button.dataset.startCreationSubflow,
          button.textContent.trim(),
        );
      });
    });
    $('character-profile-select').addEventListener('change', updateCharacterProfileControls);
    $('adapt-character-profile-btn').addEventListener('click', () => {
      void adaptSelectedCharacterProfile();
    });
    $('construction-no-requests').addEventListener('change', () => {
      $('construction-premise').disabled = $('construction-no-requests').checked;
      if ($('construction-no-requests').checked) $('construction-premise').value = '';
    });
    $('construction-next-btn').addEventListener('click', advanceConstructionInterview);
    $('construction-back-btn').addEventListener('click', retreatConstructionInterview);
    $('begin-construction-btn').addEventListener('click', () => { void beginConstruction(); });
    $('save-reusable-plan-btn').addEventListener('click', () => {
      void saveReusableCampaignPlan();
    });
    $('cancel-construction-btn').addEventListener('click', showHome);
    $('rename-campaign-btn').addEventListener('click', () => { void renameSelectedCampaign(); });
    $('stop-session-btn').addEventListener('click', () => { void stopSelectedSession(); });
    $('resume-session-btn').addEventListener('click', () => { void resumeSelectedSession(); });
    $('finalize-construction-btn').addEventListener('click', () => {
      void finalizeSelectedConstruction();
    });
    $('continue-model-guidance-btn').addEventListener('click', () => {
      void finalizeSelectedConstruction(true);
    });
    $('dismiss-model-guidance-btn').addEventListener('click', clearModelGuidanceWarning);
    $('model-choice').addEventListener('change', () => {
      updateModelSwitchButtons();
    });
    $('chat-model-choice').addEventListener('change', () => {
      void switchSelectedSessionModel('current');
    });
    $('chat-model-picker-btn').addEventListener('click', () => {
      if ($('chat-model-picker-btn').disabled) return;
      closeReasoningPicker();
      const opening = $('chat-model-popover').hidden;
      $('chat-model-popover').hidden = !opening;
      $('chat-model-picker-btn').setAttribute('aria-expanded', opening ? 'true' : 'false');
      if (opening) {
        $('chat-model-search').value = '';
        renderChatModelPicker();
        $('chat-model-search').focus();
      }
    });
    $('chat-model-search').addEventListener('input', renderChatModelPicker);
    $('chat-model-results').addEventListener('click', (event) => {
      const result = event.target.closest('[data-chat-model-index]');
      if (!result) return;
      $('chat-model-choice').value = result.dataset.chatModelIndex;
      void switchSelectedSessionModel(state.chatModelMode);
    });
    document.querySelectorAll('[data-chat-model-mode]').forEach((button) => {
      button.addEventListener('click', () => {
        state.chatModelMode = button.dataset.chatModelMode;
        renderChatModelPicker();
      });
    });
    document.addEventListener('click', (event) => {
      if (!$('chat-model-picker').contains(event.target)) closeChatModelPicker();
      if (!$('chat-reasoning-picker').contains(event.target)) closeReasoningPicker();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !$('chat-model-popover').hidden) closeChatModelPicker();
      if (event.key === 'Escape' && !$('chat-reasoning-popover').hidden) {
        closeReasoningPicker();
        $('chat-reasoning-btn').focus();
      }
    });
    $('chat-reasoning-btn').addEventListener('click', () => {
      if ($('chat-reasoning-btn').disabled) return;
      const opening = $('chat-reasoning-popover').hidden;
      closeChatModelPicker();
      $('chat-reasoning-popover').hidden = !opening;
      $('chat-reasoning-btn').setAttribute('aria-expanded', String(opening));
      if (opening) $('chat-reasoning-current').focus();
    });
    ['current', 'once'].forEach((mode) => {
      $(`chat-reasoning-${mode}`).addEventListener('change', () => {
        const value = $(`chat-reasoning-${mode}`).value;
        void switchSessionReasoning(mode, value === '' || (mode === 'current' && value === 'mode_default')
          ? null : value);
      });
    });
    $('set-default-model-btn').addEventListener('click', () => {
      void setSelectedDefaultModel();
    });
    $('archive-campaign-btn').addEventListener('click', () => { void archiveSelectedCampaign(); });
    $('open-archived-items-btn').addEventListener('click', () => {
      showHome();
      $('archived-items').open = true;
      $('archived-items').scrollIntoView({ block: 'nearest' });
      $('archived-items').querySelector('summary').focus();
    });
    $('restore-campaign-btn').addEventListener('click', () => { void restoreSelectedCampaign(); });
    $('trash-campaign-btn').addEventListener('click', () => { void trashSelectedCampaign(); });
    $('purge-campaign-btn').addEventListener('click', () => { void purgeSelectedCampaign(); });
    $('export-campaign-btn').addEventListener('click', () => { void exportSelectedCampaign(); });
    $('recover-campaign-btn').addEventListener('click', () => { void recoverCampaignFile(); });
    $('attach-builder-sources-btn').addEventListener('click', () => {
      void attachBuilderSources();
    });
    $('allow-public-web').addEventListener('click', () => {
      setWebResearchPermission(!webResearchPermission());
    });
    $('send-btn').addEventListener('click', () => {
      void submitInput($('player-input').value);
    });
    $('compact-context-btn')?.addEventListener('click', () => {
      $('session-menu').open = false;
      void submitInput('/compact');
    });
    document.addEventListener('click', (event) => {
      if (!$('session-menu').contains(event.target)) $('session-menu').open = false;
      if (!$('ooc-note').contains(event.target)) $('ooc-note').open = false;
    });
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      for (const id of ['session-menu', 'ooc-note']) {
        const disclosure = $(id);
        if (!disclosure.open) continue;
        disclosure.open = false;
        disclosure.querySelector('summary')?.focus();
      }
    });
    $('player-input').addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
      event.preventDefault();
      void submitInput(event.currentTarget.value);
    });
    $('player-input').addEventListener('input', saveSelectedSessionDraft);
    initDictation();
    $('ooc-input').addEventListener('input', () => {
      saveSelectedSessionDraft();
      $('ooc-note').classList.toggle('has-note', Boolean($('ooc-input').value.trim()));
    });
    $('ooc-note').addEventListener('toggle', saveSelectedSessionDraft);
    $('stop-btn').addEventListener('click', () => { void stop(); });
    window.addEventListener('pagehide', signalActiveStreamDisconnect);
    const portrait = $('despilled-portrait');
    portrait.addEventListener('error', () => {
      portrait.hidden = true;
      $('portrait-fallback').hidden = false;
    });
  }

  function showSettingsTab(tab) {
    if (!VALID_SETTINGS_TABS.has(tab)) return;
    chatgptPageVisible = tab === 'accounts';
    clearTimeout(chatgptStatusTimer);
    document.querySelectorAll('[data-settings-tab]').forEach((button) => {
      const selected = button.dataset.settingsTab === tab;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-selected', selected ? 'true' : 'false');
    });
    document.querySelectorAll('[data-settings-panel]').forEach((panel) => {
      const selected = panel.dataset.settingsPanel === tab;
      panel.classList.toggle('active', selected);
      panel.hidden = !selected;
    });
    if (chatgptPageVisible) void updateChatGPTConnection('status');
    if (tab === 'dictation' && !dictationModelJob) {
      void refreshDictationOptions(state.bridge).catch(() => {
        $('dictation-model-status').textContent = 'Local speech is unavailable. Reinstall the updated Sylvia application, or keep typing.';
      });
    }
  }

  function openSettings(tab = 'models') {
    settingsReturnFocus = document.activeElement;
    const selectedTab = VALID_SETTINGS_TABS.has(tab) ? tab : 'models';
    showSettingsTab(selectedTab);
    $('settings-overlay').hidden = false;
    $('close-settings-btn').focus();
    if (selectedTab === 'packages') void refreshRulesetBrowser();
  }

  function closeSettings() {
    if ($('settings-overlay').hidden) return;
    $('settings-overlay').hidden = true;
    chatgptPageVisible = false;
    clearTimeout(chatgptStatusTimer);
    if (settingsReturnFocus && typeof settingsReturnFocus.focus === 'function') {
      settingsReturnFocus.focus();
    }
    settingsReturnFocus = null;
  }

  async function refreshApplicationInfo() {
    const version = $('application-version');
    const channel = $('application-channel');
    const signing = $('application-signing');
    try {
      if (!state.bridge || typeof state.bridge.application_info !== 'function') {
        throw new Error('Host supplies no application identity command');
      }
      const info = await state.bridge.application_info();
      if (!info || typeof info.version !== 'string' || !info.version) {
        throw new Error('Host returned no application version');
      }
      state.applicationInfo = info;
      version.textContent = `Version ${info.version}`;
      channel.textContent = info.release_channel === 'alpha' ? 'Alpha prerelease' : 'Prerelease';
      signing.textContent = info.signing_status === 'unsigned'
        ? 'This version is not digitally signed'
        : 'Signing status supplied by the host';
    } catch (error) {
      state.applicationInfo = null;
      version.textContent = 'Version unavailable';
      channel.textContent = 'Release information unavailable';
      signing.textContent = 'Signing status unavailable';
    }
  }

  // Keep import outcomes visible in Settings, including when opened from Home.
  function setPackageInstallStatus(message) {
    $('package-install-status').textContent = message;

  }

  function setCharacterCardBusy(busy) {
    state.characterCardBusy = busy;
    for (const id of ['import-character-card-btn', 'confirm-character-card-btn', 'cancel-character-card-btn']) $(id).disabled = busy;
  }

  async function previewSelectedCharacterCard() {
    if (state.characterCardBusy || !state.bridge?.preview_character_card_file) return;
    state.pendingCharacterCard = null; $('character-card-preview').hidden = true;
    setCharacterCardBusy(true);
    try {
      const result = await state.bridge.preview_character_card_file();
      if (result.status === 'cancelled') return;
      if (result.status !== 'preview' || !/^[0-9a-f]{64}$/.test(result.source_sha256)) throw new Error('Invalid character-card preview');
      state.pendingCharacterCard = result;
      $('character-card-preview-title').textContent = `${result.name} \u2014 Universal character card`;
      $('character-card-preview-status').textContent = 'Needs ruleset adaptations';
      $('character-card-preview-text').textContent = result.summary ||
        'The card keeps this character\'s story and personality. Adapt it to your chosen ruleset before play.';
      const report = result.conversion_preview || {};
      const details = [];
      for (const key of ['imported', 'transformed', 'preserved_only', 'unsupported', 'omitted']) {
        details.push(`${key.replaceAll('_', ' ')}: ${(report[key] || []).join('; ') || 'None'}`);
      }
      $('character-card-preview-detail-text').textContent = details.join('\n');
      $('character-card-preview-details').open = false;
      const notices = [...(report.unsupported || []), ...(report.omitted || [])].filter((notice) =>
        notice !== 'No source bytes discarded; unsupported behavior is not activated.');
      $('character-card-preview-notices').textContent = notices.map((notice) =>
        notice === 'foreign prompt precedence, macros, scripts, automatic lore activation, external asset loading'
          ? 'Imported instructions, scripts and lore stay inactive. External assets are not loaded.'
          : notice).join('; ');
      $('character-card-preview-notices').hidden = !notices.length;
      $('character-card-preview').hidden = false;
    } catch (error) { setPackageInstallStatus(friendlyCommandError('Character-card preview failed', error)); }
    finally { setCharacterCardBusy(false); }
  }

  async function confirmSelectedCharacterCard() {
    if (state.characterCardBusy || !state.pendingCharacterCard || !state.bridge?.confirm_character_card_import) return;
    const preview = state.pendingCharacterCard; setCharacterCardBusy(true);
    try {
      const result = await state.bridge.confirm_character_card_import(preview.source_sha256);
      if (result.status !== 'imported' || !result.profile?.id) throw new Error('Character-card import was not confirmed');
      state.pendingCharacterCard = null; $('character-card-preview').hidden = true;
      setPackageInstallStatus(`Saved ${result.profile.name} as a Universal character card. Needs ruleset adaptations. Choose it during campaign setup and adapt it to your selected ruleset.`);
      if (state.bridge.campaign_creation_catalog) {
        state.campaignCreationCatalog = await state.bridge.campaign_creation_catalog();
        renderCharacterProfiles();
      }
    } catch (error) { setPackageInstallStatus(friendlyCommandError('Character-card import failed', error)); }
    finally { setCharacterCardBusy(false); }
  }

  async function installSelectedPackage() {
    if (!state.bridge || !state.bridge.install_package_file) return;
    state.pendingImportRepair = null;
    $('repair-import-btn').hidden = true;
    setPackageInstallStatus('Choose package archive…');
    try {
      const result = await state.bridge.install_package_file(`install-file-${Date.now()}`);
      if (result.status === 'cancelled') {
        setPackageInstallStatus('Package installation cancelled');
        return;
      }
      if (result.status === 'repairable') {
        state.pendingImportRepair = result;
        const errors = (result.validation_errors || []).map((error) =>
          [error.file, error.path, error.message].filter(Boolean).join(' · '));
        setPackageInstallStatus([result.message || 'This content needs corrections.', ...errors]
          .join('\n'));
        $('repair-import-btn').hidden = false;
        return;
      }
      const importedPlan = result.candidate?.kind === 'campaign_plan' ? result.candidate : null;
      const draft = await startCampaignBuilder();
      if (!draft) return;
      await showConstructionPanel(importedPlan?.id || 'custom');
      const candidateId = result.candidate && result.candidate.candidate_id;
      if (candidateId) {
        const choice = resolveCampaignCreationOptions(state.campaignCreationCatalog, candidateId);
        $('construction-package').value = choice.rulesetChoice?.selection_id || '';
        updateConstructionCompatibility();
        const setting = choice.settings.find((item) => item.candidate_id === candidateId);
        if (setting) {
          $('construction-setting').value = setting.id;
          updateConstructionPlans();
        }
      }
      setPackageInstallStatus(
        `Installed ${result.file_name || 'package archive'} · available for campaign setup`
      );
      void refreshRulesetBrowser(candidateId || null);
    } catch (e) {
      setPackageInstallStatus(friendlyCommandError('Package installation failed', e));
    }
  }

  // The owner copies the selected failed import only after this explicit repair request.
  async function startImportRepair() {
    const repair = state.pendingImportRepair;
    if (!repair || !state.bridge?.start_import_repair) return;
    const selected = defaultModelChoice();
    if (!selected) {
      setPackageInstallStatus('Configure a provider before starting Builder');
      openSettings('models');
      return;
    }
    $('repair-import-btn').disabled = true;
    try {
      const chat = await state.bridge.start_import_repair({
        repair_id: repair.repair_id, operation_id: `repair-${crypto.randomUUID()}`,
        provider_id: selected.provider_id, model_id: selected.model_id,
      });
      state.pendingImportRepair = null;
      $('repair-import-btn').hidden = true;
      closeSettings();
      await refreshBuilderChats();
      await openBuilderChat(chat.session.session_id);
      await submitInput('', { builderStart: true, builderSessionId: chat.session.session_id });
    } catch (error) {
      setPackageInstallStatus(friendlyCommandError('Repair chat could not start', error));
    } finally {
      $('repair-import-btn').disabled = false;
    }
  }

  function rulesetPackageName(packageId, label = '') {
    // This saved package identity predates the current product name. Keep its
    // value for imports and saves while presenting the established display name.
    return packageId === 'sylvia.srd-lite.bg3' ? 'SRD Lite' : label || packageId;
  }

  function rulesetCandidateLabel(candidate) {
    const entries = candidate && candidate.package_lock && candidate.package_lock.entries || [];
    const primary = entries.find((entry) => entry.role === 'primary') || entries[0];
    if (!primary) return String(candidate && candidate.candidate_id || 'Installed ruleset');
    return `${rulesetPackageName(primary.package_id, primary.label)} ${primary.version}`;
  }

  function resetRulesetBrowser(message) {
    state.rulesetInspection = null;
    state.rulesetPackageIndex = 0;
    $('ruleset-inspection-status').textContent = message;
    $('ruleset-identity').hidden = true;
    $('ruleset-support-counts').parentElement.hidden = true;
    $('ruleset-browser-grid').hidden = true;
    $('ruleset-preview').hidden = true;
    $('ruleset-compare-result').hidden = true;
    $('ruleset-export-btn').disabled = true;
  }

  async function refreshRulesetBrowser(preferredCandidateId = null) {
    if (!state.bridge || typeof state.bridge.package_candidates !== 'function') return;
    resetRulesetBrowser('Loading installed rulesets…');
    const candidateSelect = $('ruleset-candidate-select');
    const compareSelect = $('ruleset-compare-select');
    candidateSelect.disabled = true;
    compareSelect.disabled = true;
    try {
      const candidates = await state.bridge.package_candidates();
      state.rulesetCandidates = Array.isArray(candidates) ? candidates : [];
      candidateSelect.innerHTML = state.rulesetCandidates.length
        ? state.rulesetCandidates.map((candidate) =>
          `<option value="${escapeHtml(candidate.candidate_id)}">` +
          `${escapeHtml(rulesetCandidateLabel(candidate))}</option>`
        ).join('')
        : '<option value="">No installed rulesets</option>';
      candidateSelect.disabled = state.rulesetCandidates.length === 0;
      const requested = preferredCandidateId || candidateSelect.value;
      if (requested && state.rulesetCandidates.some((item) => item.candidate_id === requested)) {
        candidateSelect.value = requested;
      }
      renderRulesetCompareOptions();
      if (!state.rulesetCandidates.length) {
        resetRulesetBrowser('No installed rulesets are available.');
        return;
      }
      await inspectSelectedRulesetCandidate();
    } catch (error) {
      state.rulesetCandidates = [];
      candidateSelect.innerHTML = '<option value="">Installed rulesets unavailable</option>';
      resetRulesetBrowser(friendlyCommandError('Installed rulesets are unavailable', error));
    }
  }

  function renderRulesetCompareOptions() {
    const selected = $('ruleset-candidate-select').value;
    const alternatives = state.rulesetCandidates.filter(
      (candidate) => candidate.candidate_id !== selected
    );
    $('ruleset-compare-select').innerHTML = '<option value="">Compare with…</option>' +
      alternatives.map((candidate) =>
        `<option value="${escapeHtml(candidate.candidate_id)}">` +
        `${escapeHtml(rulesetCandidateLabel(candidate))}</option>`
      ).join('');
    $('ruleset-compare-select').disabled = alternatives.length === 0;
    updateRulesetCompareButton();
  }

  function updateRulesetCompareButton() {
    $('ruleset-compare-btn').disabled = !$('ruleset-candidate-select').value ||
      !$('ruleset-compare-select').value;
  }

  async function inspectSelectedRulesetCandidate() {
    const candidateId = $('ruleset-candidate-select').value;
    if (!candidateId || !state.bridge || !state.bridge.inspect_package_candidate) return;
    resetRulesetBrowser('Loading package details…');
    renderRulesetCompareOptions();
    try {
      state.rulesetInspection = await state.bridge.inspect_package_candidate(candidateId);
      state.rulesetPackageIndex = 0;
      const packages = state.rulesetInspection && state.rulesetInspection.packages || [];
      $('ruleset-inspection-status').textContent = packages.length
        ? `${packages.length} package${packages.length === 1 ? '' : 's'} included in this ruleset.`
        : 'No package files are available to view.';
      $('ruleset-export-btn').disabled = packages.length === 0;
      $('ruleset-browser-grid').hidden = packages.length === 0;
      $('ruleset-package-list').innerHTML = packages.map((item, index) =>
        `<button type="button" data-ruleset-package-index="${index}">` +
        `${escapeHtml(rulesetPackageName(item.id, item.label))}<small>${escapeHtml(rulesetPackageName(item.id))} ` +
        `${escapeHtml(item.version)} · ${escapeHtml(item.role)}</small></button>`
      ).join('');
      if (packages.length) selectRulesetPackage(0);
    } catch (error) {
      resetRulesetBrowser(friendlyCommandError('Ruleset inspection failed', error));
    }
  }

  function plainTextNoticePath(path) {
    return /(?:^|\/)(?:LICENSE|COPYRIGHT)$/i.test(String(path || ''));
  }

  function previewableRulesetPath(path) {
    return plainTextNoticePath(path) || /\.(?:ya?ml|json|md|txt)$/i.test(String(path || ''));
  }

  function selectRulesetPackage(index) {
    const packages = state.rulesetInspection && state.rulesetInspection.packages || [];
    if (!Number.isInteger(index) || index < 0 || index >= packages.length) return;
    state.rulesetPackageIndex = index;
    const item = packages[index];
    document.querySelectorAll('[data-ruleset-package-index]').forEach((button) => {
      button.classList.toggle('active', Number(button.dataset.rulesetPackageIndex) === index);
    });
    const compatibility = item.compatibility || {};
    const actionNames = (item.public_actions || []).map((action) => action.name);
    const privateNames = item.private_mechanics || [];
    $('ruleset-identity').innerHTML =
      `<strong>${escapeHtml(rulesetPackageName(item.id, item.label))}</strong><br>` +
      `${escapeHtml(rulesetPackageName(item.id))}@${escapeHtml(item.version)}<br>` +
      `content ${escapeHtml(item.content_sha256)}<br>` +
      `Publisher: ${escapeHtml(item.publisher)}<br>` +
      `archive ${escapeHtml(item.archive_format)} · ` +
      `host ${escapeHtml(compatibility.host_contract)} · runtime ` +
      `${escapeHtml(compatibility.runtime_format)}<br>` +
      `public actions ${escapeHtml(actionNames.join(', ') || 'none')}<br>` +
      `private mechanics ${escapeHtml(privateNames.join(', ') || 'none')}`;
    $('ruleset-identity').hidden = false;
    const support = item.support_counts || {};
    const supportEntries = Object.entries(support).sort(([left], [right]) =>
      left.localeCompare(right)
    );
    $('ruleset-support-counts').innerHTML = supportEntries.length
      ? supportEntries.map(([resolution, count]) =>
        `<span class="ruleset-support-count">${escapeHtml(rulesetSupportLabel(resolution))} · ` +
        `${escapeHtml(count)}</span>`
      ).join('')
      : '<span class="ruleset-support-count">No gameplay support information provided</span>';
    $('ruleset-support-counts').parentElement.hidden = false;
    $('ruleset-file-list').innerHTML = (item.files || []).map((file) => {
      const previewable = previewableRulesetPath(file.path);
      return `<button type="button" data-ruleset-file-path="${escapeHtml(file.path)}" ` +
        `${previewable ? '' : 'disabled '}>${escapeHtml(file.path)}` +
        `<small>${escapeHtml(file.bytes)} bytes · ` +
        `${previewable ? 'read-only preview' : 'export-only'}</small></button>`;
    }).join('');
    $('ruleset-preview').hidden = true;
  }

  function rulesetSupportLabel(resolution) {
    return ({ not_applicable: 'Not applicable', engine_family: 'Shared game rules',
      engine_specialized: 'Specific game rules', hybrid: 'Game rules with GM judgement',
      gm_adjudicated: 'GM judgement', unsupported: 'Not supported' })[resolution] || panelIdentifierLabel(resolution);
  }

  async function previewRulesetFile(path) {
    const candidateId = $('ruleset-candidate-select').value;
    const packages = state.rulesetInspection && state.rulesetInspection.packages || [];
    const item = packages[state.rulesetPackageIndex];
    if (!candidateId || !item || !state.bridge || !state.bridge.package_candidate_text) return;
    $('ruleset-inspection-status').textContent = `Reading ${path}…`;
    try {
      const preview = await state.bridge.package_candidate_text(candidateId, item.id, path);
      $('ruleset-preview-title').textContent = preview.path;
      $('ruleset-preview-meta').textContent = `${preview.bytes} bytes · ${preview.sha256}`;
      $('ruleset-file-preview').textContent = preview.content;
      $('ruleset-file-preview').classList.toggle('wrap-prose', plainTextNoticePath(path) || /\.(?:md|txt)$/i.test(path));
      $('ruleset-preview').hidden = false;
      $('ruleset-inspection-status').textContent = 'Read-only installed file preview.';
      document.querySelectorAll('[data-ruleset-file-path]').forEach((button) => {
        button.classList.toggle('active', button.dataset.rulesetFilePath === path);
      });
    } catch (error) {
      $('ruleset-inspection-status').textContent = friendlyCommandError(
        'Installed file preview failed', error
      );
    }
  }

  async function compareRulesetCandidates() {
    const left = $('ruleset-candidate-select').value;
    const right = $('ruleset-compare-select').value;
    if (!left || !right || !state.bridge || !state.bridge.compare_package_candidates) return;
    try {
      const comparison = await state.bridge.compare_package_candidates(left, right);
      const changes = comparison.changes || [];
      $('ruleset-compare-result').innerHTML = changes.length
        ? `<strong>${changes.length} package change${changes.length === 1 ? '' : 's'}</strong>` +
          changes.map((change) => `<div>${escapeHtml(change.package_id)} · ` +
            `${escapeHtml(change.change)}${change.changed_files && change.changed_files.length
              ? ` · ${escapeHtml(change.changed_files.join(', '))}` : ''}</div>`).join('')
        : '<strong>The installed packages are identical.</strong>';
      $('ruleset-compare-result').hidden = false;
    } catch (error) {
      $('ruleset-compare-result').textContent = friendlyCommandError(
        'Ruleset comparison failed', error
      );
      $('ruleset-compare-result').hidden = false;
    }
  }

  async function exportRulesetCandidate() {
    const candidateId = $('ruleset-candidate-select').value;
    if (!candidateId || !state.bridge || !state.bridge.export_package_candidate_file) return;
    $('ruleset-inspection-status').textContent = 'Choose where to save the package…';
    try {
      const result = await state.bridge.export_package_candidate_file(
        `ruleset-export-${Date.now()}`,
        candidateId,
      );
      $('ruleset-inspection-status').textContent = result.status === 'cancelled'
        ? 'Package export cancelled.'
        : `Exported ${result.file_name || 'ruleset package'}.`;
    } catch (error) {
      $('ruleset-inspection-status').textContent = friendlyCommandError(
        'Package export failed', error
      );
    }
  }

  // Apply the app theme locally and publish it to existing panel windows.
  function setTheme(theme) {
    if (!VALID_THEMES.has(theme)) return;
    state.selectedTheme = theme;
    document.body.setAttribute('data-theme', theme);
    try { localStorage.setItem('sylvia-gm-theme', theme); } catch (e) { /* ignore */ }
    document.querySelectorAll('.theme-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.theme === theme);
    });
    if (panelHost && !panelHost.isChild) void panelHost.publish().catch(() => {});
  }

  function initTheme() {
    let saved = 'slate';
    try { saved = localStorage.getItem('sylvia-gm-theme') || 'slate'; } catch (e) { /* ignore */ }
    setTheme(saved);
  }

  // ---- Owner-controlled provider settings; saved credentials are never projected back ----
  // Leaving setup discards only unsaved form values, never saved connections or chat selections.
  function showModelsOverview(saved = null) {
    state.modelEditor = { route: 'api', providerId: null, modelId: null, step: 'connection' };
    $('models-overview').hidden = false;
    $('model-add-choices').hidden = true;
    $('provider-editor').hidden = true;
    $('provider-credential').value = '';
    $('provider-headers').value = '';
    if (saved) {
      state.savedModelNotice = saved;
      $('models-action-status').textContent = `${saved.name || 'Model'} saved. Your chats keep their current models.`;
      $('set-added-model-default-btn').hidden = !saved.provider_id || !saved.canonical_model_id;
    }
    showSettingsTab('models');
    $('add-model-btn').focus();
  }

  function showAddModelChoices() {
    $('models-overview').hidden = true;
    $('model-add-choices').hidden = false;
    $('provider-editor').hidden = true;
    const providers = state.providerSettings?.configured || [];
    $('model-existing-connection').hidden = !providers.length;
    $('model-existing-provider').innerHTML = providers.map(provider =>
      `<option value="${escapeHtml(provider.provider_id)}">${escapeHtml(provider.name || provider.provider_id)}</option>`
    ).join('');
    (providers.length ? $('model-existing-provider') : $('open-account-signin-btn')).focus();
  }

  // Only the selected connection route is shown; provider identity stays exact while editing.
  function beginModelConnection(route, providerId = null, modelId = null) {
    const provider = state.providerSettings?.configured?.find(item => item.provider_id === providerId);
    if (provider?.preset_id === 'chatgpt_subscription') {
      state.pendingChatGPTModelId = modelId;
      showSettingsTab('accounts');
      return;
    }
    state.modelEditor = { route, providerId, modelId, step: 'connection' };
    $('models-overview').hidden = true;
    $('model-add-choices').hidden = true;
    $('provider-editor').hidden = false;
    const presets = (state.providerSettings?.presets || []).filter(preset =>
      preset.provider_id !== 'chatgpt_subscription' &&
      (preset.provider_id === 'custom' || (route === 'local' ? preset.mode === 'local' : preset.mode !== 'local')));
    $('provider-preset').innerHTML = presets.map(preset =>
      `<option value="${escapeHtml(preset.provider_id)}">${escapeHtml(preset.name)}</option>`).join('');
    $('provider-preset').disabled = Boolean(provider);
    if (provider) $('provider-preset').value = provider.preset_id || 'custom';
    $('provider-route-help').textContent = route === 'local'
      ? 'Start your model server first. Sylvia connects to it; it does not download or start models.'
      : 'Choose your API service. Its usual address is filled in for you.';
    $('detect-local-providers-btn').hidden = route !== 'local';
    $('local-provider-detection-status').textContent = '';
    $('provider-editor-title').textContent = modelId ? 'Model settings' : 'Add a model';
    $('save-provider-btn').textContent = modelId ? 'Save model changes' : 'Add model';
    $('provider-editor-context').textContent = provider
      ? `Using ${provider.name || provider.provider_id}. Connection settings are shared by its models.` : '';
    $('provider-continue-btn').textContent = modelId ? 'Continue to model settings' : 'Find models';
    $('provider-model-advanced').open = false;
    $('provider-manual-model').open = Boolean(modelId);
    $('provider-manual-heading').textContent = modelId ? 'Model identity and name' : 'Can’t find your model?';
    $('provider-discovery-tools').hidden = Boolean(modelId);
    $('provider-validation-report').hidden = true;
    $('provider-settings-status').textContent = '';
    renderSelectedProviderPreset();
    showModelEditorStep(provider ? 'model' : 'connection');
    if (provider && !modelId) void discoverSelectedProviderModels();
  }

  function showModelEditorStep(step) {
    state.modelEditor.step = step;
    $('provider-connection-step').hidden = step !== 'connection';
    $('provider-model-step').hidden = step !== 'model';
    (step === 'connection' ? $('provider-display-name') :
      state.modelEditor.modelId ? $('provider-model-name') : $('provider-model-search')).focus();
  }

  async function continueModelConnection() {
    if (state.modelEditor.modelId) {
      showModelEditorStep('model');
    } else if (await discoverSelectedProviderModels()) {
      showModelEditorStep('model');
    }
  }

  function useExistingModelConnection() {
    const provider = state.providerSettings?.configured?.find(item =>
      item.provider_id === $('model-existing-provider').value);
    if (provider) beginModelConnection(provider.mode === 'local' ? 'local' : 'api', provider.provider_id);
  }

  // Model rows use their own identities, including when a connection has several saved models.
  function renderConfiguredProviders() {
    const list = $('configured-provider-list');
    const configured = state.providerSettings && state.providerSettings.configured || [];
    if (!configured.length) {
      list.textContent = 'No models saved. Add a model to get started.';
      return;
    }
    list.innerHTML = configured.map((provider) => {
      const models = (provider.models || []).map((model) => {
        const name = model.name || model.id;
        const capacity = normalizeProviderCapacity(model);
        const isDefault = state.providerSettings.default_model?.provider_id === provider.provider_id &&
          state.providerSettings.default_model?.model_id === model.canonical_id;
        const detail = capacity.effective === null ? 'Context limit not set' :
          `${formatTokenCapacity(capacity.effective)} context${capacity.effectiveSource === 'legacy_unverified' ? ' · unverified limit' : ''}`;
        return `<div class="saved-model-row"><div><strong>${escapeHtml(name)}</strong>` +
          `${isDefault ? '<span class="model-default-label">Default for new chats</span>' : ''}` +
          `<div class="configured-provider-meta">Saved · ${escapeHtml(detail)}</div></div>` +
          `<button class="btn btn-secondary btn-sm" type="button" data-edit-model="${escapeHtml(model.id)}" ` +
          `data-model-provider="${escapeHtml(provider.provider_id)}" aria-label="Settings for ${escapeHtml(name)}">Model settings</button></div>`;
      }).join('');
      return `<section class="saved-model-connection"><div class="models-section-heading">` +
        `<h4>${escapeHtml(provider.name || provider.provider_id)}</h4>` +
        `<details class="connection-actions"><summary>Connection</summary>` +
        `<div class="provider-actions"><button class="btn btn-secondary btn-sm" type="button" ` +
        `data-add-provider="${escapeHtml(provider.provider_id)}">Add another model</button>` +
        `<button class="btn btn-secondary btn-sm" type="button" data-edit-provider="${escapeHtml(provider.provider_id)}">Connection settings</button>` +
        `<button class="btn btn-danger btn-sm" type="button" data-remove-provider="${escapeHtml(provider.provider_id)}">Remove connection and its models</button>` +
        `</div></details></div>` +
        (provider.credential_error ? `<p class="settings-description" role="status">${escapeHtml(provider.credential_error)}</p>` : '') +
        `${models || '<p>No saved models on this connection.</p>'}</section>`;
    }).join('');
  }

  function positiveIntegerOrNull(value) {
    const parsed = Number(value);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  }

  function formatTokenCapacity(value) {
    const number = positiveIntegerOrNull(value);
    return number === null ? 'Unknown' : `${number.toLocaleString()} tokens`;
  }

  function formatAuditTime(value) {
    const timestamp = typeof value === 'string' ? new Date(value) : null;
    if (!timestamp || Number.isNaN(timestamp.getTime())) return 'time unavailable';
    return timestamp.toLocaleString();
  }

  function contextSourceLabel(source) {
    const labels = {
      runtime_reported: 'Reported by the running model server',
      user_configured: 'Configured by you',
      provider_metadata: 'Reported by the model service',
      model_catalog: 'Maximum listed in the model catalog',
      legacy_unverified: 'Saved limit has not been verified',
      unknown: 'Not reported',
    };
    return labels[source] || labels.unknown;
  }

  function normalizeProviderCapacity(model = null) {
    const source = model || {};
    const hasDistinctFacts = [
      'declared_context_window',
      'runtime_context_window',
      'configured_context_window',
      'effective_context_window',
    ].some((key) => Object.prototype.hasOwnProperty.call(source, key));
    const projectedEffectiveSource = typeof source.effective_context_source === 'string'
      ? source.effective_context_source : source.context_window_source;
    const legacy = projectedEffectiveSource === 'legacy_unverified'
      ? positiveIntegerOrNull(source.effective_context_window ?? source.context_window)
      : (!hasDistinctFacts ? positiveIntegerOrNull(source.context_window) : null);
    const declared = positiveIntegerOrNull(source.declared_context_window);
    const runtime = positiveIntegerOrNull(source.runtime_context_window);
    const configured = positiveIntegerOrNull(source.configured_context_window);
    const candidates = [];
    if (runtime !== null) candidates.push([runtime, source.runtime_context_source || 'unknown']);
    if (configured !== null) candidates.push([configured, 'user_configured']);
    if (candidates.length === 0 && legacy !== null) {
      candidates.push([legacy, 'legacy_unverified']);
    }
    let effective = null;
    let effectiveSource = 'unknown';
    if (candidates.length) {
      [effective, effectiveSource] = candidates.reduce((smallest, item) =>
        item[0] < smallest[0] ? item : smallest
      );
      if (declared !== null && declared < effective) {
        effective = declared;
        effectiveSource = source.declared_context_source || 'model_catalog';
      }
    }
    return {
      declared,
      declaredSource: declared === null ? 'unknown' : source.declared_context_source || 'unknown',
      runtime,
      runtimeSource: runtime === null ? 'unknown' : source.runtime_context_source || 'unknown',
      configured,
      effective,
      effectiveSource,
      observedAt: typeof source.context_observed_at === 'string'
        ? source.context_observed_at : null,
      declaredObservedAt: typeof source.declared_context_observed_at === 'string'
        ? source.declared_context_observed_at
        : (typeof source.context_observed_at === 'string' ? source.context_observed_at : null),
      runtimeObservedAt: typeof source.runtime_context_observed_at === 'string'
        ? source.runtime_context_observed_at
        : (typeof source.context_observed_at === 'string' ? source.context_observed_at : null),
      configuredObservedAt: typeof source.configured_context_observed_at === 'string'
        ? source.configured_context_observed_at : null,
      legacy,
      configuredRuntimeMismatch: configured !== null && runtime !== null && configured > runtime,
      configuredDeclaredMismatch: configured !== null && declared !== null && configured > declared,
    };
  }

  function renderProviderCapacity(model = undefined) {
    if (model !== undefined) state.providerCapacity = normalizeProviderCapacity(model);
    if (!state.providerCapacity) state.providerCapacity = normalizeProviderCapacity();
    const capacity = state.providerCapacity;
    $('provider-context-summary').textContent = capacity.effective !== null
      ? `Context available to Sylvia: ${formatTokenCapacity(capacity.effective)}${capacity.effectiveSource === 'legacy_unverified' ? ' (unverified saved limit)' : ''}.`
      : 'Context limit not reported. You can provide a limit in Advanced model settings.';
    $('provider-declared-context').textContent = formatTokenCapacity(capacity.declared);
    $('provider-declared-context-source').textContent = contextSourceLabel(
      capacity.declaredSource
    ) + (capacity.declaredObservedAt
      ? ` · observed ${formatAuditTime(capacity.declaredObservedAt)}` : '');
    $('provider-runtime-context').textContent = formatTokenCapacity(capacity.runtime);
    $('provider-runtime-context-source').textContent = contextSourceLabel(capacity.runtimeSource) +
      (capacity.runtimeObservedAt
        ? ` · observed ${formatAuditTime(capacity.runtimeObservedAt)}` : '');
    $('provider-configured-context-source').textContent = capacity.configured === null
      ? 'Optional. Set a lower limit for Sylvia; increasing this value cannot raise the server’s limit.'
      : `Configured by you${capacity.configuredObservedAt
        ? ` · ${formatAuditTime(capacity.configuredObservedAt)}` : ' · unsaved change'}. ` +
        'Increasing this value cannot raise the server’s limit.';
    $('provider-effective-context').textContent = formatTokenCapacity(capacity.effective);
    $('provider-effective-context-source').textContent = contextSourceLabel(
      capacity.effectiveSource
    );

    const badge = $('provider-capacity-state');
    const states = {
      runtime_reported: 'Detected',
      provider_metadata: 'Metadata',
      user_configured: 'Configured',
      model_catalog: 'Catalog-limited',
      legacy_unverified: 'Unverified',
      unknown: 'Unknown',
    };
    badge.textContent = states[capacity.effectiveSource] || 'Unknown';
    badge.dataset.source = capacity.effectiveSource;

    const warnings = [];
    if (capacity.configuredRuntimeMismatch) {
      warnings.push(
        `Your ${formatTokenCapacity(capacity.configured)} Sylvia cap exceeds the ` +
        `${formatTokenCapacity(capacity.runtime)} loaded runtime. Sylvia uses the smaller runtime ` +
        'capacity. Increase context in the local server, then refresh models and context.'
      );
    } else if (capacity.configuredDeclaredMismatch) {
      warnings.push(
        `Your ${formatTokenCapacity(capacity.configured)} Sylvia cap exceeds the model maximum ` +
        `reported as ${formatTokenCapacity(capacity.declared)}. Sylvia uses the smaller maximum.`
      );
    } else if (capacity.legacy !== null) {
      warnings.push(
        'This saved limit has not been verified. Refresh models and context, or enter a ' +
        'limit that your server supports.'
      );
    } else if (capacity.runtime === null && capacity.configured !== null) {
      warnings.push(
        'The provider did not report its loaded runtime. Sylvia can use your explicit cap, but this ' +
        'setting does not enlarge the context allocated by the local server.'
      );
    } else if (capacity.runtime === null && capacity.declared !== null) {
      warnings.push(
        'A model maximum is not proof of the context currently allocated by the server. Refresh ' +
        'runtime metadata or enter a deliberate lower operating cap.'
      );
    } else if (capacity.effective === null) {
      warnings.push(
        'The context limit was not reported. The provider will check request capacity.'
      );
    }
    const output = providerEffectiveOutput();
    const metadata = state.providerModelMetadata;
    $('provider-output-limit').placeholder = metadata?.declared_max_output_tokens
      ? `Automatic: ${formatTokenCapacity(metadata.declared_max_output_tokens)}` : 'Automatic reply allowance';
    $('provider-output-summary').textContent = output
      ? `Reply limit: ${formatTokenCapacity(output)}. ` +
        (metadata?.declared_max_output_tokens ? `Model maximum: ${formatTokenCapacity(metadata.declared_max_output_tokens)}.` : 'Model maximum not reported.')
      : 'Model maximum not reported. Sylvia uses an automatic reply allowance.';
    $('provider-defaults-source').textContent = metadata?.documented_source
      ? `Documented defaults checked ${metadata.documented_at}. Account access is verified separately.` : '';
    const requestedOutput = positiveIntegerOrNull($('provider-output-limit').value);
    if (requestedOutput && metadata?.declared_max_output_tokens && requestedOutput > metadata.declared_max_output_tokens) {
      warnings.push('Your reply cap exceeds the model maximum. Sylvia uses the smaller model maximum.');
    }
    if (output !== null && capacity.effective !== null && output > capacity.effective) {
      warnings.push('Output token limit cannot exceed the effective context capacity.');
    }
    const warning = $('provider-capacity-warning');
    warning.textContent = warnings.join(' ');
    warning.hidden = warnings.length === 0;
  }

  // A blank override uses this exact model's known maximum, never another model's allowance.
  function providerEffectiveOutput() {
    const configured = positiveIntegerOrNull($('provider-output-limit').value);
    const declared = positiveIntegerOrNull(state.providerModelMetadata?.declared_max_output_tokens);
    return configured && declared ? Math.min(configured, declared) : configured || declared;
  }

  function updateConfiguredProviderCapacity() {
    const configured = positiveIntegerOrNull($('provider-context-window').value);
    state.providerCapacity = normalizeProviderCapacity({
      declared_context_window: state.providerCapacity && state.providerCapacity.declared,
      declared_context_source: state.providerCapacity && state.providerCapacity.declaredSource,
      runtime_context_window: state.providerCapacity && state.providerCapacity.runtime,
      runtime_context_source: state.providerCapacity && state.providerCapacity.runtimeSource,
      configured_context_window: configured,
      context_observed_at: state.providerCapacity && state.providerCapacity.observedAt,
      declared_context_observed_at:
        state.providerCapacity && state.providerCapacity.declaredObservedAt,
      runtime_context_observed_at:
        state.providerCapacity && state.providerCapacity.runtimeObservedAt,
      configured_context_observed_at: null,
    });
    renderProviderCapacity();
  }

  function clearStaleDiscoveredProviderCapacity() {
    const modelId = $('provider-model-id').value.trim();
    if (state.providerModelMetadata?.id === modelId) return;
    if ($('provider-model-name').value === state.providerModelMetadata?.name) $('provider-model-name').value = '';
    $('provider-context-window').value = '';
    $('provider-output-limit').value = '';
    $('provider-builder-output-limit').value = '';
    state.providerModelMetadata = null;
    renderProviderCapacity(null);
    const defaults = state.providerSettings?.model_defaults?.find(
      model => model.preset_id === $('provider-preset').value && model.id === modelId
    );
    if (defaults) applyDiscoveredProviderModel(defaults, null, null);
  }

  function renderSelectedProviderPreset() {
    const settings = state.providerSettings;
    if (!settings) return;
    const providerId = $('provider-preset').value;
    const preset = (settings.presets || []).find((item) => item.provider_id === providerId);
    const configured = (settings.configured || []).find((item) =>
      item.provider_id === (state.modelEditor.providerId || (providerId !== 'custom' ? providerId : null)));
    if (!preset) return;
    $('provider-setup-hint').textContent = preset.setup_hint || preset.launch_hint || '';
    $('provider-setup-btn').hidden = !preset.setup_url;
    $('provider-setup-btn').textContent = preset.mode === 'local' ? 'Server setup guide' : 'API key and setup';
    if (configured) {
      state.modelEditor.providerId = configured.provider_id;
      $('provider-editor-context').textContent = `Using ${configured.name || configured.provider_id}. Connection settings are shared by its models. Leave the API key blank to keep it.`;
    } else {
      $('provider-editor-context').textContent = '';
    }
    $('provider-display-name').value = configured && configured.name || preset.name || '';
    $('provider-endpoint').value = configured && configured.base_url || preset.base_url || '';
    $('provider-endpoint').readOnly = !preset.endpoint_editable;
    renderProviderDataDisclosure(preset);
    $('provider-credential').value = '';
    $('provider-credential-optional').textContent = preset.credential_required
      ? '' : '(optional)';
    const model = configured?.models?.find(item => item.id === state.modelEditor.modelId);
    state.providerModelMetadata = model || null;
    $('provider-model-id').value = model && model.id || '';
    $('provider-model-id').readOnly = Boolean(state.modelEditor.modelId);
    $('provider-model-name').value = model && model.name || '';
    $('provider-context-window').value = model && model.configured_context_window || '';
    $('provider-output-limit').value = model
      ? (Object.hasOwn(model, 'configured_output_tokens') ? model.configured_output_tokens : model.max_output_tokens) || '' : '';
    $('provider-builder-output-limit').value = model && model.builder_output_tokens || '';
    $('provider-transport').value = model && model.transport || preset.default_transport ||
      'chat_completions';
    $('provider-headers').value = '';
    state.providerDiscovery = { offset: 0, total: 0, hasMore: false, models: [] };
    renderDiscoveredProviderModels();
    renderProviderCapacity(model || null);
  }

  function renderProviderDataDisclosure(preset) {
    const disclosure = $('provider-data-disclosure');
    if (preset.mode === 'local') {
      disclosure.textContent = 'Messages and relevant campaign or Builder content go to the server address above. ' +
        'An address on another computer sends that content off this computer. Check your server settings: ' +
        'a local server may also use a cloud service.';
      return;
    }
    const policy = preset.provider_id === 'gemini'
      ? ' Google\'s data terms differ between unpaid and paid Gemini API use. Check the terms for your project.'
      : preset.provider_id === 'openrouter'
        ? ' OpenRouter and the model providers it routes to have their own data policies. Check your routing and privacy settings.'
        : ' Check the service\'s data use and retention terms for your plan and settings.';
    disclosure.textContent = `This ${preset.name || 'model service'} connection sends messages and relevant campaign or Builder content to its configured server address.` + policy;
  }

  // The owner chooses a fixed official page; no URL from model content is opened.
  async function openProviderSetup() {
    if (!state.bridge?.open_provider_setup) return;
    const editor = state.modelEditor;
    try {
      const result = await state.bridge.open_provider_setup($('provider-preset').value);
      if (editor !== state.modelEditor) return;
      $('provider-settings-status').textContent = result.opened
        ? 'Service setup opened in your browser. Return here when ready.'
        : `Could not open your browser. Visit ${result.url}`;
    } catch (error) {
      if (editor === state.modelEditor) $('provider-settings-status').textContent = 'Could not open the service setup page.';
    }
  }

  let chatgptRequestBusy = false;
  let chatgptPageVisible = false;
  let chatgptStatusTimer = null;
  let chatgptAccountStatus = {};

  // The exact advertised model supplies automatic facts; saved explicit overrides remain separate.
  function selectedChatGPTFacts() {
    const id = $('chatgpt-model-choice').value;
    const saved = state.providerSettings?.configured?.find(
      item => item.preset_id === 'chatgpt_subscription')?.models?.find(item => item.id === id);
    const discovered = chatgptAccountStatus.models?.find(item => item.id === id);
    return { saved,
      context: discovered?.runtime_context_window || saved?.runtime_context_window,
      output: discovered?.output_reference_tokens || saved?.declared_max_output_tokens };
  }

  function renderChatGPTCapacity() {
    const facts = selectedChatGPTFacts();
    const context = positiveIntegerOrNull($('chatgpt-model-context').value);
    const output = positiveIntegerOrNull($('chatgpt-model-output').value);
    $('chatgpt-model-context').placeholder = facts.context ? `Automatic: ${formatTokenCapacity(facts.context)}` : 'Managed by ChatGPT';
    $('chatgpt-model-output').placeholder = facts.output ? `Automatic: ${formatTokenCapacity(facts.output)}` : 'Managed by ChatGPT';
    $('chatgpt-automatic-limits-btn').hidden = !facts.context && !facts.output;
    $('chatgpt-model-summary').textContent = !$('chatgpt-model-choice').value ? '' :
      `Context allowance: ${context ? formatTokenCapacity(context) : facts.context ? `automatic (${formatTokenCapacity(facts.context)})` : 'unknown'}. ` +
      `Model output reference: ${formatTokenCapacity(output || facts.output)}. ChatGPT manages the reply budget.`;
  }

  function selectChatGPTModel() {
    const { saved } = selectedChatGPTFacts();
    $('chatgpt-model-heading').textContent = saved ? '2. Model settings' : '2. Add a model';
    document.querySelector('[data-chatgpt-action="add_model"]').textContent = saved ? 'Save model changes' : 'Add model';
    $('chatgpt-model-context').value = saved?.configured_context_window || '';
    $('chatgpt-model-output').value = saved
      ? (Object.hasOwn(saved, 'configured_output_tokens') ? saved.configured_output_tokens : saved.max_output_tokens) || '' : '';
    renderChatGPTCapacity();
    $('chatgpt-model-status').textContent = '';
  }

  // Sign-in polling is confined to this page and stops when consent finishes or the page closes.
  async function updateChatGPTConnection(action) {
    const status = $(action === 'add_model' ? 'chatgpt-model-status' : 'chatgpt-connection-status');
    if (action === 'add_model') {
      if (!$('chatgpt-model-choice').value) {
        status.textContent = 'Choose a model first.';
        $('chatgpt-model-choice').focus();
        return;
      }
      const fields = ['chatgpt-model-context', 'chatgpt-model-output'];
      const invalid = fields.find(id => $(id).value.trim() && !positiveIntegerOrNull($(id).value));
      if (invalid) {
        status.textContent = 'Enter a positive whole number or leave the field empty for automatic limits.';
        $('chatgpt-model-advanced').open = true;
        $(invalid).focus();
        return;
      }
    }
    const buttons = [...document.querySelectorAll('[data-chatgpt-action]')];
    if (chatgptRequestBusy) return;
    chatgptRequestBusy = true;
    let refreshPendingLogin = false;
    clearTimeout(chatgptStatusTimer);
    buttons.forEach((button) => { button.disabled = true; });
    status.textContent = 'Connecting…';
    try {
      const model = action === 'add_model' ? {
        id: $('chatgpt-model-choice').value,
        context_window: positiveIntegerOrNull($('chatgpt-model-context').value),
        max_output_tokens: positiveIntegerOrNull($('chatgpt-model-output').value),
      } : null;
      let result = await state.bridge.chatgpt_connection(action, model);
      refreshPendingLogin = Boolean(result.login_pending);
      if (result.signed_in && !result.models) {
        result = { ...result, ...await state.bridge.chatgpt_connection('models') };
      }
      chatgptAccountStatus = result;
      $('chatgpt-model-card').hidden = !result.signed_in;
      $('chatgpt-signin-step').textContent = result.signed_in ? '1. ChatGPT connected'
        : result.login_pending ? '1. Finish sign-in in your browser' : '1. Connect ChatGPT';
      if (Array.isArray(result.models)) {
        const pendingModel = state.pendingChatGPTModelId;
        const previous = pendingModel !== null ? pendingModel : $('chatgpt-model-choice').value;
        $('chatgpt-model-choice').innerHTML = '<option value="">Choose a model</option>' +
          result.models.map((item) => `<option value="${escapeHtml(item.id)}"` +
            `${item.unavailable_reason ? ' disabled' : ''}>` +
            `${escapeHtml(item.name || item.id)}` +
            `${item.unavailable_reason ? ` · ${escapeHtml(item.unavailable_reason)}` : ''}</option>`).join('');
        $('chatgpt-model-choice').value = previous;
        if (pendingModel !== null) {
          state.pendingChatGPTModelId = null;
          selectChatGPTModel();
          $('chatgpt-model-advanced').open = false;
        }
        renderChatGPTCapacity();
      }
      if (result.saved_model) {
        await refreshProviderSettings();
        await refreshModelChoices();
        const saved = state.modelChoices.find(choice => choice.model_id === result.saved_model);
        showModelsOverview({ provider_id: saved?.provider_id, canonical_model_id: result.saved_model,
          name: saved?.model_name || 'Model' });
        status.textContent = 'Model saved.';
      } else if (result.login_pending) {
        status.textContent = 'Finish sign-in in your browser. This page will update automatically.';
      } else if (result.signed_in) {
        status.textContent = `Signed in${result.email ? ` as ${result.email}` : ''}.`;
      } else {
        status.textContent = result.login_failed ? 'Sign-in did not complete. Try again.' : 'Signed out.';
      }
    } catch (error) {
      refreshPendingLogin = false;
      status.textContent = String(error && error.message || error || 'ChatGPT connection unavailable.');
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
      buttons.forEach((button) => {
        const kind = button.dataset.chatgptAction;
        if (kind === 'cancel') button.hidden = !chatgptAccountStatus.login_pending;
        if (kind === 'logout') button.hidden = !chatgptAccountStatus.signed_in;
        if (kind === 'login') button.hidden = Boolean(
          chatgptAccountStatus.signed_in || chatgptAccountStatus.login_pending
        );
      });
      chatgptRequestBusy = false;
      if (chatgptPageVisible && refreshPendingLogin) {
        chatgptStatusTimer = setTimeout(() => void updateChatGPTConnection('status'), 1500);
      }
    }
  }

  async function refreshProviderSettings() {
    if (!state.bridge || !state.bridge.provider_settings) return;
    const status = $('models-action-status');
    try {
      state.providerSettings = await state.bridge.provider_settings();
      renderConfiguredProviders();
    } catch (e) {
      state.providerSettings = null;
      status.textContent = 'Provider settings are unavailable.';
    }
  }

  function editConfiguredProvider(providerId) {
    const configured = state.providerSettings && state.providerSettings.configured || [];
    const provider = configured.find((item) => item.provider_id === providerId);
    if (!provider) return;
    beginModelConnection(provider.mode === 'local' ? 'local' : 'api', provider.provider_id, provider.models?.[0]?.id);
    if (provider.preset_id === 'chatgpt_subscription') return;
    showModelEditorStep('connection');
    $('provider-settings-status').textContent = provider.credential_error || (provider.header_count
      ? `${provider.header_count} private headers saved. Leave them blank to keep them.`
      : 'Editing this saved connection.');
  }

  function privateProviderHeaders() {
    const headers = {};
    $('provider-headers').value.split(/\r?\n/).forEach((rawLine) => {
      const line = rawLine.trim();
      if (!line) return;
      const separator = line.indexOf(':');
      if (separator <= 0 || !line.slice(separator + 1).trim()) {
        throw new Error('Each advanced header must use Name: value');
      }
      headers[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
    });
    return headers;
  }

  function requireTextLength(value, label, maximum) {
    const count = [...value].length;
    if (count > maximum) throw new Error(label + ' allows ' + maximum + ' characters; you entered ' + count + '.');
    return value;
  }

  function providerEditorCommand({ requireOutputLimit = true } = {}) {
    const presetId = $('provider-preset').value;
    const capacity = state.providerCapacity || normalizeProviderCapacity();
    const configuredContext = positiveIntegerOrNull($('provider-context-window').value);
    const outputLimit = positiveIntegerOrNull($('provider-output-limit').value);
    for (const id of ['provider-context-window', 'provider-output-limit', 'provider-builder-output-limit']) {
      if ($(id).value.trim() && !positiveIntegerOrNull($(id).value)) {
        throw new Error('Enter a positive whole number or leave the field empty for automatic limits.');
      }
    }
    return {
      provider_id: state.modelEditor.providerId || presetId,
      preset_id: presetId,
      display_name: requireTextLength($('provider-display-name').value.trim(), 'Connection name', 120),
      base_url: $('provider-endpoint').value.trim(),
      credential: $('provider-credential').value,
      model_id: $('provider-model-id').value.trim(),
      model_name: $('provider-model-name').value.trim() || $('provider-model-id').value.trim(),
      context_window: capacity.legacy,
      declared_context_window: capacity.declared,
      declared_context_source: capacity.declaredSource,
      runtime_context_window: capacity.runtime,
      runtime_context_source: capacity.runtimeSource,
      configured_context_window: configuredContext,
      context_observed_at: capacity.observedAt,
      declared_context_observed_at: capacity.declaredObservedAt,
      runtime_context_observed_at: capacity.runtimeObservedAt,
      configured_context_observed_at: capacity.configuredObservedAt,
      max_output_tokens: outputLimit,
      declared_max_output_tokens: state.providerModelMetadata?.declared_max_output_tokens || null,
      builder_output_tokens: positiveIntegerOrNull($('provider-builder-output-limit').value),
      transport: $('provider-transport').value,
      headers: privateProviderHeaders(),
      supports_streaming: state.providerModelMetadata?.supports_streaming ?? null,
      supports_tools: state.providerModelMetadata?.supports_tools ?? null,
    };
  }

  function renderDiscoveredProviderModels() {
    const select = $('provider-discovered-models');
    select.innerHTML = state.providerDiscovery.models.map((model, index) => {
      const capacity = normalizeProviderCapacity(model);
      const detail = capacity.runtime !== null
        ? ` · loaded ${formatTokenCapacity(capacity.runtime)}`
        : (capacity.declared !== null
          ? ` · maximum ${formatTokenCapacity(capacity.declared)}`
          : ' · context unknown');
      return `<option value="${index}">${escapeHtml(model.name || model.id)} - ` +
        `${escapeHtml(model.id)}${escapeHtml(detail)}</option>`;
    }).join('');
    select.hidden = state.providerDiscovery.models.length === 0;
    $('load-more-provider-models-btn').hidden = !state.providerDiscovery.hasMore;
  }

  async function discoverSelectedProviderModels(append = false) {
    if (!state.bridge || !state.bridge.discover_provider_models) return;
    const status = $('provider-settings-status');
    const editor = state.modelEditor;
    let command;
    try {
      command = providerEditorCommand({ requireOutputLimit: false });
    } catch (e) {
      status.textContent = e.message;
      return;
    }
    command.query = $('provider-model-search').value.trim();
    command.offset = append ? state.providerDiscovery.models.length : 0;
    command.limit = 100;
    const selectedModelId = $('provider-model-id').value.trim();
    const selectedConfiguredContext = positiveIntegerOrNull(
      $('provider-context-window').value
    );
    const selectedConfiguredOutput = positiveIntegerOrNull($('provider-output-limit').value);
    const selectedBuilderOutput = positiveIntegerOrNull($('provider-builder-output-limit').value);
    status.textContent = 'Connecting and checking model limits...';
    try {
      const result = await state.bridge.discover_provider_models(command) || {};
      if (editor !== state.modelEditor) return false;
      const models = Array.isArray(result.models) ? result.models : [];
      state.providerDiscovery = {
        offset: Number(result.offset || 0),
        total: Number(result.total || 0),
        hasMore: Boolean(result.has_more),
        models: append ? state.providerDiscovery.models.concat(models) : models,
      };
      renderDiscoveredProviderModels();
      const refreshedIndex = state.providerDiscovery.models.findIndex(
        (model) => model.id === selectedModelId
      );
      if (refreshedIndex >= 0) {
        $('provider-discovered-models').value = String(refreshedIndex);
        applyDiscoveredProviderModel(
          state.providerDiscovery.models[refreshedIndex],
          selectedConfiguredContext,
          state.providerCapacity && state.providerCapacity.configuredObservedAt,
          selectedConfiguredOutput,
          selectedBuilderOutput,
        );
      }
      status.textContent = `Found ${state.providerDiscovery.total} matching models. ` +
        (state.providerDiscovery.models.length ? 'Choose one from the list.' : 'You can also enter its model identifier manually.') +
        (result.notice ? ` ${result.notice}` : '');
      return true;
    } catch (e) {
      if (editor !== state.modelEditor) return false;
      status.textContent = providerSetupError(e, 'load models');
      return false;
    }
  }

  // Show an actionable category without echoing raw network responses or private headers.
  function providerSetupError(error, action) {
    const detail = String(error?.message || error || '');
    if (/rejected the API credential|requires an API key/.test(detail)) {
      return 'Check or replace this service’s API key, then try again. Your saved models are unchanged.';
    }
    if (/HTTP 429/.test(detail)) return 'The service is rate-limiting requests. Wait briefly, then refresh models.';
    if (/HTTP 404|invalid model catalog/.test(detail)) {
      return 'This address did not provide a model list. Check the API base URL, or enter a model manually.';
    }
    if (/output.*(unknown|exceeds)|output limit|context capacity/.test(detail)) {
      return 'Check the model’s context allowance and reply cap under Advanced, then save again.';
    }
    const preset = state.providerSettings?.presets?.find(item => item.provider_id === $('provider-preset').value);
    if (preset?.mode === 'local') {
      return 'Could not reach the model server. Start it, check its address and key, then try again.';
    }
    return `Could not ${action}. Check the service address and connection, then try again. Your saved models are unchanged.`;
  }

  function selectDiscoveredProviderModel() {
    const index = Number($('provider-discovered-models').value);
    const model = state.providerDiscovery.models[index];
    if (!model) return;
    applyDiscoveredProviderModel(model, null, null);
  }

  function applyDiscoveredProviderModel(model, configuredContext, configuredObservedAt,
      configuredOutput = null, builderOutput = null) {
    state.providerModelMetadata = { ...model,
      declared_max_output_tokens: model.declared_max_output_tokens ?? model.max_output_tokens ?? null };
    $('provider-model-id').value = model.id || '';
    $('provider-model-name').value = model.name || model.id || '';
    // Custom servers use the connection's chosen protocol; discovery may supply a fallback.
    if ($('provider-preset').value !== 'custom') {
      $('provider-transport').value = model.transport || 'chat_completions';
    }
    const configured = configuredContext ?? positiveIntegerOrNull(
      model.configured_context_window
    );
    $('provider-context-window').value = configured || '';
    $('provider-output-limit').value = configuredOutput || '';
    $('provider-builder-output-limit').value = builderOutput || '';
    renderProviderCapacity({
      ...model,
      configured_context_window: configured,
      configured_context_observed_at: configuredObservedAt,
    });
  }

  async function saveSelectedProvider() {
    if (!state.bridge || !state.bridge.save_provider_settings) return;
    const status = $('provider-settings-status');
    const outputLimit = $('provider-output-limit');
    const limitFields = [
      ['provider-context-window', 'context token limit'],
      ['provider-output-limit', 'output token limit'],
      ['provider-builder-output-limit', 'Builder output token limit'],
    ];
    for (const [id] of limitFields) {
      $(id).removeAttribute('aria-invalid');
      $(id).setCustomValidity('');
    }
    for (const [id, label] of limitFields) {
      const input = $(id);
      if (input.value.trim() && !positiveIntegerOrNull(input.value)) {
        const message = `Enter a positive whole number for the ${label}, or leave it empty for automatic limits.`;
        status.textContent = message;
        $('provider-model-advanced').open = true;
        input.setAttribute('aria-invalid', 'true');
        input.setCustomValidity(message);
        input.focus();
        input.reportValidity();
        return;
      }
    }
    if (!$('provider-model-id').value.trim()) {
      status.textContent = 'Choose a model from the list, or enter its identifier.';
      $('provider-manual-model').open = true;
      $('provider-model-id').focus();
      return;
    }
    if (providerEffectiveOutput() !== null && state.providerCapacity?.effective !== null
        && providerEffectiveOutput() > state.providerCapacity?.effective) {
      status.textContent = 'The output limit must fit within the available context limit.';
      $('provider-model-advanced').open = true;
      outputLimit.setAttribute('aria-invalid', 'true');
      outputLimit.focus();
      return;
    }
    const editor = state.modelEditor;
    let command;
    status.textContent = 'Saving model.';
    try {
      command = providerEditorCommand();
      const saved = await state.bridge.save_provider_settings(command);
      if (editor !== state.modelEditor) return;
      $('provider-credential').value = '';
      await refreshProviderSettings();
      await refreshModelChoices();
      showModelsOverview({ ...saved, name: command.model_name });
    } catch (e) {
      if (editor !== state.modelEditor) return;
      status.textContent = providerSetupError(e, 'save the model');
    }
  }

  async function detectLocalProviders() {
    if (!state.bridge || !state.bridge.detect_local_providers) return;
    const status = $('local-provider-detection-status');
    const editor = state.modelEditor;
    status.textContent = 'Checking the usual local server addresses...';
    try {
      const results = await state.bridge.detect_local_providers() || [];
      if (editor !== state.modelEditor) return;
      const detected = results.filter((item) => item.reachable);
      status.textContent = detected.length
        ? `Detected: ${detected.map((item) => item.name).join(', ')}.`
        : 'No local model service was found. Start one, or enter its address manually.';
      if (detected.length === 1) {
        state.modelEditor = { ...state.modelEditor, providerId: null, modelId: null };
        $('provider-preset').value = detected[0].provider_id;
        renderSelectedProviderPreset();
      }
    } catch (e) {
      if (editor !== state.modelEditor) return;
      status.textContent = 'Local detection was unavailable. Enter the server address manually.';
    }
  }

  function providerCheckText(key, value) {
    const descriptions = {
      reachability: {
        passed: 'Server connected',
        failed: 'Server not found',
        unverified: 'Server not tested',
      },
      authentication: {
        passed: 'API key accepted',
        failed: 'API key rejected',
        not_required: 'No API key needed',
        unverified: 'API key not tested',
      },
      model_discovery: {
        passed: 'Models found',
        model_not_listed: 'Selected model was not listed',
        invalid_response: 'Model list was not understood',
        unverified: 'Models not checked',
      },
      transport: {
        passed: 'Reply format works',
        failed: 'Reply format was rejected',
        unverified: 'Reply format not checked',
      },
      streaming: {
        passed: 'Streaming replies work',
        failed: 'Streaming replies did not work',
        unverified: 'Streaming not checked',
      },
      tool_calling: {
        passed: 'Agent tools work',
        failed: 'Agent tools did not work',
        unverified: 'Agent tools not checked',
      },
    };
    return descriptions[key] && descriptions[key][value] || 'Not checked';
  }

  function renderProviderValidation(report) {
    const panel = $('provider-validation-report');
    const checks = [
      ['reachability', report.reachability],
      ['authentication', report.authentication],
      ['model_discovery', report.model_discovery],
      ['transport', report.transport],
      ['streaming', report.streaming],
      ['tool_calling', report.tool_calling],
    ];
    const connected = report.reachability === 'passed' && report.authentication !== 'failed';
    const summary = report.agent_ready
      ? 'Ready for Builder and Game Master. This model can stream replies and use agent tools.'
      : connected
        ? 'Limited compatibility. This model can chat, but it could not use agent tools or ' +
          'stream replies as Sylvia requires. Builder and Game Master tasks may not work correctly.'
        : 'Could not connect. Check that the service is running, then check its server address ' +
          'and API key.';
    panel.innerHTML = `<h4>${report.agent_ready ? 'Ready for Sylvia' :
      connected ? 'Limited compatibility' : 'Connection problem'}</h4>` +
      checks.map(([key, value]) =>
        `<div class="provider-validation-check" data-result="${escapeHtml(value || 'unverified')}">` +
        `${escapeHtml(providerCheckText(key, value || 'unverified'))}</div>`
      ).join('') + `<p>${escapeHtml(summary)}</p>`;
    panel.dataset.ready = report.agent_ready ? 'true' : 'false';
    panel.hidden = false;
  }

  async function validateSelectedProvider() {
    if (!state.bridge || !state.bridge.validate_provider_model) return;
    const status = $('provider-settings-status');
    const editor = state.modelEditor;
    if (!$('provider-model-id').value.trim()) {
      status.textContent = 'Choose a model or enter its identifier before testing.';
      $('provider-manual-model').open = true;
      $('provider-model-id').focus();
      return;
    }
    let command;
    try {
      command = providerEditorCommand({ requireOutputLimit: false });
    } catch (e) {
      status.textContent = e.message;
      return;
    }
    status.textContent = 'Testing the connection, streaming replies, and agent tools...';
    try {
      const report = await state.bridge.validate_provider_model(command);
      if (editor !== state.modelEditor) return;
      renderProviderValidation(report || {});
      status.textContent = report && report.agent_ready
        ? 'Model test complete.'
        : 'Model test complete. Review the warning above before using it for a chat.';
    } catch (e) {
      if (editor !== state.modelEditor) return;
      status.textContent = 'The model test could not run. Check that the service is available.';
    }
  }

  async function removeConfiguredProvider(providerId) {
    if (!state.bridge || !state.bridge.remove_provider_settings) return;
    if (!window.confirm(
      'Remove this connection and its saved API key? Existing chats may need it configured again.'
    )) return;
    const status = $('models-action-status');
    try {
      await state.bridge.remove_provider_settings(providerId);
      await refreshProviderSettings();
      await refreshModelChoices();
      status.textContent = 'Connection removed.';
    } catch (e) {
      status.textContent = 'Connection could not be removed.';
    }
  }

  let adminAuditRefreshing = false;

  async function refreshAdminAudit() {
    if (adminAuditRefreshing) return;
    if (!state.bridge || !state.bridge.admin_full_audit_status ||
        !state.bridge.admin_full_audit_record) return;
    adminAuditRefreshing = true;
    const status = $('admin-audit-status');
    status.textContent = 'Loading latest audit records…';
    try {
      const projection = await state.bridge.admin_full_audit_status() || {};
      state.adminAuditEnabled = projection.enabled === true;
      $('toggle-admin-audit-btn').textContent = state.adminAuditEnabled
        ? 'Disable and clear Full Audit' : 'Enable Full Audit';
      $('open-admin-audit-btn').disabled = !state.adminAuditEnabled;
      const safeDiagnostics = Array.isArray(projection.safe_mechanics_diagnostics)
        ? projection.safe_mechanics_diagnostics : [];
      const traceCount = Number.isSafeInteger(projection.trace_count) &&
        projection.trace_count >= 0 ? projection.trace_count : 0;
      state.adminAuditRawTraceCount = traceCount;
      $('admin-audit-output').hidden = !traceCount && !safeDiagnostics.length;
      const traces = [];
      let exportOnlyCount = 0;
      for (let index = Math.max(0, traceCount - 50); index < traceCount; index += 1) {
        try {
          const record = await state.bridge.admin_full_audit_record(index) || {};
          if (record.trace != null) {
            traces.push(record.trace);
          } else {
            exportOnlyCount += 1;
            traces.push({
              kind: 'audit_record_export_only',
              index,
              reason: String(record.unavailable_reason || 'live_view_unavailable'),
              bytes: Number(record.bytes) || null,
            });
          }
        } catch (error) {
          exportOnlyCount += 1;
          traces.push({kind: 'audit_record_export_only', index, reason: 'live_view_unavailable'});
        }
      }
      status.textContent = state.adminAuditEnabled
        ? `Enabled · ${traceCount} audit records · showing latest ${traces.length} · ` +
          `${safeDiagnostics.length} safe operation diagnostics · memory-only` +
          (exportOnlyCount ? ` · ${exportOnlyCount} export-only` : '') +
          ' · Export includes all records.'
        : `Full Audit disabled · ${safeDiagnostics.length} safe operation diagnostics`;
      state.adminAuditTraces = [...safeDiagnostics.slice(-50), ...traces];
      $('admin-audit-output').hidden = state.adminAuditTraces.length === 0;
      renderAdminAuditTraces();
    } catch (error) {
      status.textContent = 'Full Audit status is unavailable.';
    } finally {
      adminAuditRefreshing = false;
    }
  }

  async function openAdminAuditWindow() {
    if (!state.adminAuditEnabled || !state.bridge || !state.bridge.open_admin_audit_window) return;
    try {
      await state.bridge.open_admin_audit_window();
    } catch (error) {
      $('admin-audit-status').textContent = friendlyCommandError(
        'The live Full Audit inspector could not be opened', error
      );
    }
  }

  function visibleAdminAuditTraces() {
    const query = $('admin-audit-search').value.trim().toLowerCase();
    if (!query) return state.adminAuditTraces;
    return state.adminAuditTraces.filter((trace) =>
      JSON.stringify(trace).toLowerCase().includes(query)
    );
  }

  function renderAdminAuditTraces() {
    const container = $('admin-audit-traces');
    const traces = visibleAdminAuditTraces();
    if (!traces.length) {
      container.innerHTML = '<p class="admin-audit-empty">No matching diagnostic records.</p>';
      return;
    }
    container.innerHTML = traces.map((trace, index) => {
      const provider = trace && trace.provider || {};
      const wireRequest = trace && trace.request || {};
      const kind = String(trace && trace.kind || 'audit_record');
      const stage = String(trace && trace.input_stage || kind);
      const exchange = String(trace && trace.exchange_id || `record-${index + 1}`);
      const model = String(provider.model_id || wireRequest.model || 'model unavailable');
      const role = String(trace && trace.role || 'response');
      return `<details class="admin-audit-entry" open>` +
        `<summary><span>${escapeHtml(readableToolName(kind))}</span>` +
        `<small>${escapeHtml(readableToolName(stage))} · ${escapeHtml(model)} · ` +
        `${escapeHtml(role)} · ${escapeHtml(exchange)}</small>` +
        `</summary><pre>${escapeHtml(JSON.stringify(trace, null, 2))}</pre></details>`;
    }).join('');
  }

  async function copyVisibleAdminAudit() {
    const traces = visibleAdminAuditTraces();
    if (!traces.length) {
      $('admin-audit-status').textContent = 'There are no visible audit entries to copy.';
      return;
    }
    try {
      await navigator.clipboard.writeText(JSON.stringify(traces, null, 2));
      $('admin-audit-status').textContent = `Copied ${traces.length} visible audit record(s).`;
    } catch (error) {
      $('admin-audit-status').textContent = 'Audit entries could not be copied.';
    }
  }

  async function exportAdminAudit() {
    if (!state.bridge || !state.bridge.export_admin_audit_file ||
        !state.adminAuditRawTraceCount) {
      $('admin-audit-status').textContent = 'There are no model logs to export.';
      return;
    }
    try {
      const result = await state.bridge.export_admin_audit_file();
      $('admin-audit-status').textContent = result.status === 'cancelled'
        ? 'Model log export cancelled.'
        : `Model logs exported as ${result.file_name || 'JSON'}.`;
    } catch (error) {
      $('admin-audit-status').textContent = friendlyCommandError('Model log export failed', error);
    }
  }

  async function toggleAdminAudit() {
    if (!state.bridge || !state.bridge.set_admin_full_audit) return;
    if (anySessionIsWorking()) {
      $('admin-audit-status').textContent = 'Finish or stop the active model turn first.';
      return;
    }
    try {
      await state.bridge.set_admin_full_audit(!state.adminAuditEnabled);
      if (state.adminAuditEnabled) state.adminAuditTraces = [];
      await refreshAdminAudit();
    } catch (error) {
      $('admin-audit-status').textContent = 'Full Audit could not be changed.';
    }
  }

  // ---- Safe owner-projected configured models ----
  async function refreshModelChoices() {
    const select = $('model-choice');
    const chatSelect = $('chat-model-choice');
    const chatSelection = state.sessionReasoning?.modelState?.current ||
      (chatSelect.value === '' ? null : state.modelChoices[Number(chatSelect.value)]);
    const status = $('model-status');
    const deterministicCampaignButtons = [
      $('home-new-campaign-btn')
    ];
    const modelRequiredCreationButtons = [
      $('new-builder-chat-btn'), $('home-build-btn')
    ];
    select.disabled = true;
    chatSelect.disabled = true;
    $('set-default-model-btn').disabled = true;
    [...deterministicCampaignButtons, ...modelRequiredCreationButtons]
      .forEach((button) => { button.disabled = true; });
    state.modelChoices = [];
    if (!state.bridge || !state.bridge.model_choices) {
      select.innerHTML = '<option value="">No configured models</option>';
      chatSelect.innerHTML = select.innerHTML;
      status.textContent = 'Configured models unavailable';
      return;
    }
    try {
      const choices = await state.bridge.model_choices() || [];
      state.modelChoices = choices.filter((choice) =>
        choice && typeof choice.provider_id === 'string' && choice.provider_id &&
        typeof choice.model_id === 'string' && choice.model_id
      );
    } catch (e) {
      state.modelChoices = [];
    }
    deterministicCampaignButtons.forEach((button) => { button.disabled = false; });
    if (!state.modelChoices.length) {
      select.innerHTML = '<option value="">No configured models</option>';
      chatSelect.innerHTML = select.innerHTML;
      status.textContent = 'Add a model, then choose your default for new chats.';
      renderChatModelPicker();
      return;
    }
    const options = state.modelChoices.map((choice, index) => {
      const label = `${choice.provider_name || choice.provider_id} · ${choice.model_name || choice.model_id}`;
      return `<option value="${index}">${escapeHtml(label)}</option>`;
    }).join('');
    select.innerHTML = '<option value="">Choose a default model</option>' + options;
    chatSelect.innerHTML = '<option value="">Choose a model</option>' + options;
    const configuredDefault = state.providerSettings && state.providerSettings.default_model;
    const defaultIndex = state.modelChoices.findIndex((choice) => configuredDefault &&
      choice.provider_id === configuredDefault.provider_id &&
      choice.model_id === configuredDefault.model_id
    );
    select.value = defaultIndex >= 0 ? String(defaultIndex) : '';
    const chatIndex = state.modelChoices.findIndex(choice =>
      choice.provider_id === chatSelection?.provider_id && choice.model_id === chatSelection?.model_id);
    chatSelect.value = chatIndex >= 0 ? String(chatIndex) : '';
    select.disabled = false;
    modelRequiredCreationButtons.forEach((button) => { button.disabled = false; });
    updateModelSwitchButtons();
    renderChatModelPicker();
    status.textContent = defaultIndex >= 0
      ? `New chats use ${state.modelChoices[defaultIndex].model_name || configuredDefault.model_id}.`
      : 'Choose a default for new Builder and Game Master chats.';
  }

  function chatModelModeLabel() {
    if (state.chatModelMode === 'once') return 'Use this model for the next reply only.';
    if (state.chatModelMode === 'global') return 'Use here and make it the default for new chats.';
    return 'Switch this chat while preserving its history.';
  }

  function closeChatModelPicker() {
    $('chat-model-popover').hidden = true;
    $('chat-model-picker-btn').setAttribute('aria-expanded', 'false');
  }

  function renderChatModelPicker() {
    const selectedIndex = $('chat-model-choice').value === '' ? -1 : Number($('chat-model-choice').value);
    const current = Number.isInteger(selectedIndex) ? state.modelChoices[selectedIndex] : null;
    const modelState = state.sessionReasoning?.sessionId === state.activeSessionId
      ? state.sessionReasoning.modelState : null;
    const once = modelState?.once;
    const saved = once || modelState?.current;
    const configured = saved ? state.modelChoices.find((choice) =>
      choice.provider_id === saved.provider_id && choice.model_id === saved.model_id) : null;
    const unavailable = Boolean(saved && !configured);
    const selected = saved ? configured || saved : current;
    const emptySelectionLabel = state.modelChoices.length ? 'Choose a model' : 'No configured models';
    $('chat-model-picker-btn').textContent = selected
      ? `${unavailable ? 'Unavailable: ' : ''}${selected.model_name || selected.model_id}${once ? ' · next reply' : ''}`
      : emptySelectionLabel;
    const label = selected
      ? `${unavailable ? 'Saved model unavailable' : 'Model'}: ${selected.provider_name || selected.provider_id} · ${selected.model_name || selected.model_id}${once ? ' · next reply only' : ' · this chat'}`
      : emptySelectionLabel;
    $('chat-model-picker-btn').title = label;
    $('chat-model-picker-btn').setAttribute('aria-label', label);
    $('chat-model-hint').textContent = unavailable
      ? 'The saved model is unavailable. Choose a configured model before continuing.'
      : chatModelModeLabel();
    document.querySelectorAll('[data-chat-model-mode]').forEach((button) => {
      button.classList.toggle('active', button.dataset.chatModelMode === state.chatModelMode);
    });
    const query = $('chat-model-search').value.trim().toLowerCase();
    const matches = state.modelChoices.map((choice, index) => ({ choice, index })).filter((item) => {
      const choice = item.choice;
      const searchable = `${choice.provider_name || choice.provider_id} ` +
        `${choice.model_name || choice.model_id} ${choice.model_id}`;
      return !query || searchable.toLowerCase().includes(query);
    });
    $('chat-model-results').innerHTML = matches.length
      ? matches.map(({ choice, index }) =>
        `<button type="button" class="chat-model-result" data-chat-model-index="${index}" ` +
        `role="option" aria-selected="${index === selectedIndex ? 'true' : 'false'}">` +
        `<span>${escapeHtml(choice.model_name || choice.model_id)}</span>` +
        `<small>${escapeHtml(choice.provider_name || choice.provider_id)} · ` +
        `${escapeHtml(choice.model_id)}</small></button>`
      ).join('')
      : '<p class="chat-model-hint">No configured models match this search.</p>';
  }

  function selectedModelChoice(scope = 'chat') {
    const value = $(scope === 'default' ? 'model-choice' : 'chat-model-choice').value;
    if (value === '') return null;
    const index = Number(value);
    if (!Number.isInteger(index)) return null;
    return state.modelChoices[index] || null;
  }

  function defaultModelChoice() {
    const configuredDefault = state.providerSettings && state.providerSettings.default_model;
    if (!configuredDefault) return null;
    return state.modelChoices.find((choice) =>
      choice.provider_id === configuredDefault.provider_id &&
      choice.model_id === configuredDefault.model_id
    ) || null;
  }

  // Refresh chat controls without overriding the Ruleset's current action gates.
  function updateModelSwitchButtons() {
    const chosenDefault = selectedModelChoice('default');
    const hasIdleSession = Boolean(state.activeSessionId) && !selectedSessionIsWorking() &&
      !state.reasoningPending;
    const savedDefault = state.providerSettings?.default_model;
    $('set-default-model-btn').disabled = !chosenDefault ||
      (chosenDefault.provider_id === savedDefault?.provider_id && chosenDefault.model_id === savedDefault?.model_id);
    $('chat-model-choice').disabled = !state.modelChoices.length || !hasIdleSession;
    $('chat-model-picker-btn').disabled = !state.modelChoices.length || !hasIdleSession;
    if ($('chat-model-picker-btn').disabled) closeChatModelPicker();
    renderReasoningPicker();
    document.querySelectorAll('[data-panel-action-id]').forEach((button) => {
      const surface = state.panelProjection?.surfaces.find((item) =>
        item.id === button.closest('[data-panel-id]')?.dataset.panelId);
      const action = surface?.actions?.find((item) => item.id === button.dataset.panelActionId);
      button.disabled = !hasIdleSession || !state.activeCampaignId || !state.activeSnapshot ||
        !surface || !action || !panelActionAvailable(surface, action,
          button.dataset.panelActorId, button.dataset.panelPieceId);
    });
  }

  function closeReasoningPicker() {
    $('chat-reasoning-popover').hidden = true;
    $('chat-reasoning-btn').setAttribute('aria-expanded', 'false');
  }

  function reasoningLabel(value) {
    if (!value || value === 'provider_default') return 'Provider default';
    if (value === 'xhigh') return 'X-High';
    return value.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  // Options and defaults come from the owner catalog; model names never imply capabilities.
  function renderReasoningPicker() {
    const saved = state.sessionReasoning;
    const reasoning = saved && saved.sessionId === state.activeSessionId ? saved.value : null;
    const disabled = !reasoning || !state.activeSessionId || selectedSessionIsWorking() ||
      Boolean(state.reasoningPending);
    $('chat-reasoning-btn').disabled = disabled;
    $('chat-reasoning-current').disabled = disabled;
    $('chat-reasoning-once').disabled = disabled;
    if (disabled) closeReasoningPicker();
    if (!reasoning) {
      $('chat-reasoning-btn').textContent = 'Reasoning unavailable';
      $('chat-reasoning-btn').title = 'Reasoning unavailable';
      $('chat-reasoning-btn').setAttribute('aria-label', 'Reasoning unavailable');
      $('chat-reasoning-hint').textContent = '';
      return;
    }
    const choiceFor = (selection) => state.modelChoices.find((choice) =>
      choice.provider_id === selection?.provider_id && choice.model_id === selection?.model_id);
    const currentChoice = choiceFor(saved.modelState?.current);
    const nextChoice = choiceFor(reasoning);
    const options = (choice, mode, selected) => {
      const native = Array.isArray(choice?.supported_reasoning_efforts) ? choice.supported_reasoning_efforts : [];
      const mapped = reasoningLabel(choice?.reasoning_mode_defaults?.[state.activeSessionRole]);
      const values = [
        ...(mode === 'once' ? [['', 'Use chat setting']] : []),
        ['mode_default', `Mode default (${mapped})`], ['provider_default', 'Provider default'],
        ...native.map((value) => [value, reasoningLabel(value)]),
      ];
      if (selected && !values.some(([value]) => value === selected))
        values.push([selected, `${reasoningLabel(selected)} (unavailable — choose another)`]);
      return values.map(([value, label]) =>
        `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join('');
    };
    $('chat-reasoning-current').innerHTML = options(currentChoice, 'current', reasoning.current);
    $('chat-reasoning-current').value = reasoning.current || 'mode_default';
    $('chat-reasoning-once').innerHTML = options(nextChoice, 'once', reasoning.once);
    $('chat-reasoning-once').value = reasoning.once || '';
    const scope = reasoning.scope === 'once' ? 'next reply' : reasoning.scope === 'session' ? 'this chat' : 'mode default';
    const actual = reasoningLabel(reasoning.effective_request);
    $('chat-reasoning-btn').textContent = reasoning.valid
      ? `${actual}${reasoning.scope === 'once' ? ' · next reply' : ''}` : 'Choose a supported level';
    const nextModel = saved.modelState?.once ? ` Next reply model: ${reasoning.model_id}.` : '';
    $('chat-reasoning-hint').textContent = reasoning.valid
      ? `Next reply requests ${actual} (${scope}).${nextModel} ` +
        (reasoning.supported === null ? 'Supported levels are unknown; the provider manages reasoning.' :
          'Next-reply choices clear after an attempted reply, including a stopped or failed reply.')
      : 'The saved level is unavailable for this model. Choose an available level or Mode default.';
    $('chat-reasoning-btn').title = $('chat-reasoning-hint').textContent;
    $('chat-reasoning-btn').setAttribute('aria-label', `Reasoning: ${$('chat-reasoning-hint').textContent}`);
  }

  async function switchSessionReasoning(mode, effort) {
    const sessionId = state.activeSessionId;
    const saved = state.sessionReasoning;
    if (!sessionId || saved?.sessionId !== sessionId || selectedSessionIsWorking() ||
        state.reasoningPending || !state.bridge?.switch_session_reasoning) return;
    state.reasoningPending = sessionId;
    updateModelSwitchButtons();
    let failed = false;
    try {
      const result = await state.bridge.switch_session_reasoning({ session_id: sessionId, mode, effort });
      if (state.sessionReasoning?.sessionId === sessionId && state.activeSessionId === sessionId)
        state.sessionReasoning.value = result.reasoning_state;
    } catch (_) {
      failed = true;
    } finally {
      state.reasoningPending = null;
      updateModelSwitchButtons();
    }
    if (state.sessionReasoning !== saved || state.activeSessionId !== sessionId) return;
    if (failed) {
      $('chat-reasoning-hint').textContent = 'Reasoning could not be changed. Wait for the reply to finish, then choose an available level or Mode default.';
    }
    $('chat-reasoning-popover').hidden = false;
    $('chat-reasoning-btn').setAttribute('aria-expanded', 'true');
  }

  async function setSelectedDefaultModel() {
    const selected = selectedModelChoice('default');
    if (!selected || !state.bridge || !state.bridge.set_default_model) return;
    const status = $('model-status');
    try {
      await state.bridge.set_default_model(selected.provider_id, selected.model_id);
      await refreshProviderSettings();
      await refreshModelChoices();
      status.textContent = 'New Builder and Game Master chats will use this model.';
      $('set-added-model-default-btn').hidden = true;
    } catch (e) {
      status.textContent = 'The default model could not be changed.';
    }
  }

  async function switchSelectedSessionModel(mode) {
    const selected = selectedModelChoice();
    if (!selected || !state.activeSessionId || selectedSessionIsWorking() || state.reasoningPending ||
        !state.bridge || !state.bridge.switch_session_model) return;
    const status = $('chat-model-hint');
    const activity = selectedActivity();
    const sessionId = state.activeSessionId;
    try {
      const result = await state.bridge.switch_session_model({
        session_id: sessionId,
        provider_id: selected.provider_id,
        model_id: selected.model_id,
        mode,
      });
      if (!selectedActivityMatches(activity)) return result;
      state.pendingModelMode = null;
      if (mode === 'global') {
        await refreshProviderSettings();
        await refreshModelChoices();
      }
      if (!selectedActivityMatches(activity)) return result;
      if (state.activeSessionRole === 'builder') {
        await refreshBuilderChats();
        if (!selectedActivityMatches(activity)) return result;
        await renderBuilderSession(sessionId);
      } else {
        await renderPlayerProjection(activity.campaignId, sessionId);
      }
      if (!selectedActivityMatches(activity)) return result;
      status.textContent = mode === 'once'
        ? `Next model turn will use ${selected.model_name || selected.model_id}.`
        : `This chat now uses ${selected.model_name || selected.model_id}.`;
      closeChatModelPicker();
      renderChatModelPicker();
      return result;
    } catch (e) {
      if (!selectedActivityMatches(activity)) return null;
      const current = state.sessionReasoning?.modelState?.current;
      const index = state.modelChoices.findIndex((choice) => choice.provider_id === current?.provider_id &&
        choice.model_id === current?.model_id);
      if (index >= 0) {
        $('chat-model-choice').value = String(index);
        renderChatModelPicker();
      }
      status.textContent = 'The model could not be changed. Wait for the reply to finish. If its reasoning levels differ, clear Next reply only and select Mode default before switching.';
      $('chat-model-hint').textContent = status.textContent;
      return null;
    }
  }

  function clearModelGuidanceWarning() {
    state.pendingModelGuidance = null;
    $('model-guidance-warning').hidden = true;
  }

  function guidanceWarningText(guidance) {
    if (guidance.status === 'below') {
      return `This exact model is placed at ${guidance.placement_tier_id}, below this ` +
        `ruleset's minimum ${guidance.minimum_tier_id}. You can continue explicitly, ` +
        'but Sylvia may be less reliable with this ruleset.';
    }
    return `This exact model has no empirical Sylvia GM capability placement for this ` +
      `ruleset's minimum ${guidance.minimum_tier_id}. You can continue explicitly, ` +
      'but the model is currently unrated.';
  }

  function showModelGuidanceWarning(guidance, selected) {
    state.pendingModelGuidance = {
      campaign_id: state.activeCampaignId,
      provider_id: selected.provider_id,
      model_id: selected.model_id,
      guidance,
    };
    $('model-guidance-warning-title').textContent = guidance.status === 'below'
      ? 'Selected model is below the ruleset minimum'
      : 'Selected model is not yet rated';
    $('model-guidance-warning-text').textContent = guidanceWarningText(guidance);
    $('model-guidance-warning').hidden = false;
  }

  function renderPersistedModelGuidance(session) {
    const badge = $('model-guidance-badge');
    const guidance = session && session.model_guidance;
    if (!guidance) {
      badge.hidden = true;
      badge.textContent = '';
      badge.removeAttribute('title');
      return;
    }
    if (guidance.status === 'not_applicable') {
      badge.textContent = 'Model guidance: not applicable (no ruleset)';
      badge.title = 'No ruleset minimum applies to this campaign.';
    } else if (guidance.status === 'meets') {
      badge.textContent = `Model guidance: ${guidance.placement_tier_id} meets ` +
        `${guidance.minimum_tier_id}`;
      badge.title = 'This placement is backed by the recorded empirical evidence identity.';
    } else {
      const placement = guidance.placement_tier_id || 'unrated';
      badge.textContent = `Model guidance override recorded: ${placement} / minimum ` +
        `${guidance.minimum_tier_id}`;
      badge.title = guidanceWarningText(guidance);
    }
    badge.hidden = false;
  }

  function readHomeTutorialProgress() {
    try {
      const saved = JSON.parse(localStorage.getItem(HOME_TUTORIAL_KEY) || 'null');
      if (!saved || !['in_progress', 'completed', 'skipped'].includes(saved.status)) return null;
      return {
        status: saved.status,
        step: Number.isInteger(saved.step) ? saved.step : 0,
      };
    } catch (e) {
      return null;
    }
  }

  // Tutorial progress is a local UI preference, never campaign or session truth.
  function writeHomeTutorialProgress(status, step) {
    try {
      localStorage.setItem(HOME_TUTORIAL_KEY, JSON.stringify({ status, step }));
    } catch (e) { /* best-effort local preference */ }
  }

  function maybeStartHomeTutorial() {
    if (state.view !== 'home' || state.homeTutorialOpen || state.homeTutorialQueued) return;
    const progress = readHomeTutorialProgress();
    if (progress && ['completed', 'skipped'].includes(progress.status)) return;
    state.homeTutorialQueued = true;
    requestAnimationFrame(() => {
      state.homeTutorialQueued = false;
      if (state.view !== 'home' || state.homeTutorialOpen) return;
      startHomeTutorial(progress ? progress.step : 0);
    });
  }

  function startHomeTutorial(step = 0, restart = false) {
    const progress = restart ? null : readHomeTutorialProgress();
    if (!restart && progress && ['completed', 'skipped'].includes(progress.status)) return;
    state.homeTutorialStep = Math.max(0, Math.min(
      Number.isInteger(step) ? step : 0,
      HOME_TUTORIAL_STEPS.length - 1
    ));
    state.homeTutorialOpen = true;
    tutorialReturnFocus = $('home-btn');
    writeHomeTutorialProgress('in_progress', state.homeTutorialStep);
    $('home-tutorial').hidden = false;
    $('home-tutorial').setAttribute('aria-hidden', 'false');
    renderHomeTutorialStep();
  }

  function renderHomeTutorialStep() {
    if (!state.homeTutorialOpen) return;
    const step = HOME_TUTORIAL_STEPS[state.homeTutorialStep];
    const target = step.targetId ? $(step.targetId) : null;
    $('tutorial-step').textContent =
      `${state.homeTutorialStep + 1} / ${HOME_TUTORIAL_STEPS.length}`;
    $('tutorial-title').textContent = step.title;
    $('tutorial-copy').textContent = step.copy;
    $('tutorial-next-btn').textContent =
      state.homeTutorialStep === HOME_TUTORIAL_STEPS.length - 1 ? 'Done' : 'Next';
    $('home-tutorial').dataset.mode = target ? 'target' : 'intro';
    $('tutorial-spotlight').hidden = !target;
    $('tutorial-sylvia').src = TUTORIAL_SPRITES[step.sprite || 'up'];
    requestAnimationFrame(() => {
      positionHomeTutorial();
      $('tutorial-next-btn').focus();
    });
  }

  function positionHomeTutorial() {
    if (!state.homeTutorialOpen) return;
    const step = HOME_TUTORIAL_STEPS[state.homeTutorialStep];
    const target = step.targetId ? $(step.targetId) : null;
    const guide = $('tutorial-guide');
    const sprite = $('tutorial-sylvia');
    const margin = 14;
    const gap = 18;

    if (!target) {
      guide.dataset.placement = 'center';
      sprite.src = TUTORIAL_SPRITES.wave;
      const guideRect = guide.getBoundingClientRect();
      guide.style.left = `${Math.max(margin, (window.innerWidth - guideRect.width) / 2)}px`;
      guide.style.top = `${Math.max(margin, (window.innerHeight - guideRect.height) / 2)}px`;
      return;
    }

    const targetRect = target.getBoundingClientRect();
    const spotlightPadding = 7;
    const spotlight = $('tutorial-spotlight');
    spotlight.style.left = `${targetRect.left - spotlightPadding}px`;
    spotlight.style.top = `${targetRect.top - spotlightPadding}px`;
    spotlight.style.width = `${targetRect.width + spotlightPadding * 2}px`;
    spotlight.style.height = `${targetRect.height + spotlightPadding * 2}px`;

    let guideRect = guide.getBoundingClientRect();
    const sideGuideWidth = Math.min(420, window.innerWidth - margin * 2);
    const roomLeft = targetRect.left - margin;
    const roomRight = window.innerWidth - targetRect.right - margin;
    const roomAbove = targetRect.top - margin;
    const roomBelow = window.innerHeight - targetRect.bottom - margin;
    const roomByPlacement = {
      left: roomLeft,
      right: roomRight,
      above: roomAbove,
      below: roomBelow,
    };
    const requiredRoom = {
      left: sideGuideWidth + gap,
      right: sideGuideWidth + gap,
      above: guideRect.height + gap,
      below: guideRect.height + gap,
    };
    const preferredPlacements = step.preferredPlacements || [
      'left', 'right', 'below', 'above'
    ];
    const placement = preferredPlacements.find(
      (candidate) => roomByPlacement[candidate] >= requiredRoom[candidate]
    ) || preferredPlacements.reduce((roomiest, candidate) => (
      roomByPlacement[candidate] > roomByPlacement[roomiest] ? candidate : roomiest
    ));

    guide.dataset.placement = placement;
    guideRect = guide.getBoundingClientRect();
    const spriteForPlacement = {
      left: 'right',
      right: 'left',
      below: 'up',
      above: 'down',
    }[placement];
    sprite.src = TUTORIAL_SPRITES[spriteForPlacement];

    let left;
    let top;
    if (placement === 'left') {
      left = targetRect.left - guideRect.width - gap;
      top = targetRect.top + (targetRect.height - guideRect.height) / 2;
    } else if (placement === 'right') {
      left = targetRect.right + gap;
      top = targetRect.top + (targetRect.height - guideRect.height) / 2;
    } else if (placement === 'below') {
      left = targetRect.left + (targetRect.width - guideRect.width) / 2;
      top = targetRect.bottom + gap;
    } else {
      left = targetRect.left + (targetRect.width - guideRect.width) / 2;
      top = targetRect.top - guideRect.height - gap;
    }
    guide.style.left = `${Math.max(margin, Math.min(left, window.innerWidth - guideRect.width - margin))}px`;
    guide.style.top = `${Math.max(margin, Math.min(top, window.innerHeight - guideRect.height - margin))}px`;
  }

  function advanceHomeTutorial() {
    if (!state.homeTutorialOpen) return;
    if (state.homeTutorialStep === HOME_TUTORIAL_STEPS.length - 1) {
      closeHomeTutorial('completed');
      return;
    }
    state.homeTutorialStep += 1;
    writeHomeTutorialProgress('in_progress', state.homeTutorialStep);
    renderHomeTutorialStep();
  }

  function closeHomeTutorial(status) {
    if (!state.homeTutorialOpen) return;
    state.homeTutorialOpen = false;
    state.homeTutorialQueued = false;
    $('home-tutorial').hidden = true;
    $('home-tutorial').setAttribute('aria-hidden', 'true');
    if (status) writeHomeTutorialProgress(status, state.homeTutorialStep);
    if (tutorialReturnFocus && typeof tutorialReturnFocus.focus === 'function') {
      tutorialReturnFocus.focus();
    }
    tutorialReturnFocus = null;
  }

  function handleHomeTutorialKeydown(event) {
    if (!state.homeTutorialOpen) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeHomeTutorial('skipped');
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [$('tutorial-skip-btn'), $('tutorial-next-btn')];
    const current = focusable.indexOf(document.activeElement);
    const next = event.shiftKey
      ? (current <= 0 ? focusable.length - 1 : current - 1)
      : (current === focusable.length - 1 ? 0 : current + 1);
    event.preventDefault();
    focusable[next].focus();
  }

  function replayHomeTutorial() {
    closeSettings();
    showHome();
    startHomeTutorial(0, true);
  }

  function showSessionView() {
    state.view = 'session';
    for (const id of ['session-status-text', 'construction-status', 'panel-action-status']) {
      $(id).textContent = '';
      $(id).hidden = true;
    }
    $('session-frame').classList.remove('home-active');
    $('home-view').hidden = true;
  }

  function showHome() {
    dictation?.cancel('', { announce: false });
    state.displayHistory = null;
    cancelCampaignOpening();
    gamePanels().leave();
    hideConstructionPanel(false);
    saveSelectedSessionDraft();
    state.view = 'home';
    setInlineStatus('');
    state.activeCampaignId = null;
    state.activeSessionId = null;
    state.activeSessionRole = null;
    state.activeSnapshot = null;
    state.campaignOpening = null;
    state.sessionReasoning = null;
    updateModelSwitchButtons();
    $('campaign-loading-status').hidden = true;
    $('session-frame').classList.add('home-active');
    $('home-view').hidden = false;
    renderBuilderChatRail();
    renderSidebarState();
    renderHome();
    maybeStartHomeTutorial();
  }

  function recentCampaign(campaigns) {
    return [...campaigns].sort((left, right) =>
        Number(right.session_activity_ordinal || 0) -
          Number(left.session_activity_ordinal || 0) ||
        Number(right.revision || 0) - Number(left.revision || 0) ||
        String(left.display_name || '').localeCompare(String(right.display_name || ''))
      )[0] || null;
  }

  function concisePackageLabel(label, kind) {
    let value = String(label || '').trim();
    if (!value) return kind === 'setting' ? 'No campaign setting' : 'No rules';
    if (kind === 'setting' && value.toLowerCase() === 'no campaign setting') return value;
    if (kind === 'setting') value = value.replace(/\s+Campaign Setting$/i, '');
    value = value.replace(/\s+Alpha[-\s].*$/i, '').replace(/\s+Package$/i, '');
    return value.trim() || String(label);
  }

  function preferredCampaignSession(campaign) {
    const sessions = Array.isArray(campaign && campaign.sessions) ? campaign.sessions : [];
    return sessions.find((session) => session.role === 'dm' && session.status === 'active') ||
      sessions.find((session) => session.role === 'builder' && session.status === 'active') ||
      sessions.find((session) => session.role === 'dm') || sessions[0] || null;
  }

  function sessionSummary(session) {
    if (!session) return 'No session';
    return `${session.role === 'builder' ? 'Builder' : 'DM'} session · ${session.status}`;
  }

  function wireHomeCampaignLinks() {
    document.querySelectorAll('[data-home-campaign-id]').forEach((button) => {
      button.addEventListener('click', () => {
        void openCampaign(button.dataset.homeCampaignId, button.dataset.homeSessionId || null);
      });
    });
    document.querySelectorAll('[data-archive-campaign]').forEach((button) => {
      button.addEventListener('click', () => { void setCampaignArchived(button.dataset.archiveCampaign, true); });
    });
  }

  function renderHome() {
    renderArchivedItems();
    const campaigns = state.campaigns.filter((campaign) => campaign.lifecycle === 'active');
    const count = campaigns.length;
    $('home-campaign-count').textContent =
      `${count} ongoing campaign${count === 1 ? '' : 's'}`;

    const recent = recentCampaign(campaigns);
    if (!recent) {
      $('home-recent').innerHTML =
        '<h2 class="visually-hidden" id="home-recent-title">Most recently updated campaign</h2>' +
        '<div class="home-empty-copy">Your most recently updated campaign will appear here.</div>';
    } else {
      const session = preferredCampaignSession(recent);
      const isDraft = recent.availability === 'draft';
      const ruleset = isDraft
        ? 'Campaign Draft'
        : concisePackageLabel(recent.ruleset_label, 'ruleset');
      const setting = isDraft
        ? 'Under construction'
        : concisePackageLabel(recent.campaign_setting_label, 'setting');
      const streaming = Boolean(session && activityIsWorking(activityForSession(session.session_id)));
      $('home-recent').innerHTML =
        '<div class="home-recent-copy"><div class="home-recent-kicker">Most recently updated</div>' +
        `<h2 id="home-recent-title">${escapeHtml(recent.display_name)}</h2>` +
        `<div class="home-recent-path">${escapeHtml(ruleset)}` +
        '<span aria-hidden="true">→</span>' + `${escapeHtml(setting)}</div>` +
        '<div class="home-recent-meta">' +
        `<span>${escapeHtml(sessionSummary(session))}</span>` +
        `<span>Revision ${escapeHtml(recent.revision)}</span>` +
        `${streaming ? '<span class="home-working"><span class="builder-stream-spinner" aria-hidden="true"></span>Working…</span>' : ''}` +
        '</div></div>' +
        `<button class="btn btn-secondary home-continue" type="button" ` +
        `data-home-campaign-id="${escapeHtml(recent.campaign_id)}" ` +
        `data-home-session-id="${escapeHtml(session && session.session_id)}" ` +
        `${session ? '' : 'disabled'}>Continue</button>`;
    }

    if (!campaigns.length) {
      $('campaign-shelves').innerHTML =
        '<div class="home-empty-copy">No ongoing campaigns yet.</div>';
      wireHomeCampaignLinks();
      return;
    }

    const rulesets = new Map();
    campaigns.forEach((campaign) => {
      const isDraft = campaign.availability === 'draft';
      const rulesetLabel = isDraft
        ? 'Campaign Drafts'
        : concisePackageLabel(campaign.ruleset_label, 'ruleset');
      const settingLabel = isDraft
        ? 'Under construction'
        : concisePackageLabel(campaign.campaign_setting_label, 'setting');
      const rulesetKey = `${campaign.ruleset_id || 'none'}:${rulesetLabel}`;
      const settingKey = `${campaign.campaign_setting_id || 'none'}:${settingLabel}`;
      if (!rulesets.has(rulesetKey)) {
        rulesets.set(rulesetKey, { label: rulesetLabel, settings: new Map() });
      }
      const ruleset = rulesets.get(rulesetKey);
      if (!ruleset.settings.has(settingKey)) {
        ruleset.settings.set(settingKey, { label: settingLabel, campaigns: [] });
      }
      ruleset.settings.get(settingKey).campaigns.push(campaign);
    });

    $('campaign-shelves').innerHTML = [...rulesets.values()].map((ruleset) => {
      const rulesetCount = [...ruleset.settings.values()].reduce(
        (total, setting) => total + setting.campaigns.length, 0
      );
      const settings = [...ruleset.settings.values()].map((setting) => {
        const tiles = setting.campaigns
          .sort((left, right) => String(left.display_name).localeCompare(String(right.display_name)))
          .map((campaign) => {
            const session = preferredCampaignSession(campaign);
            const streaming = Boolean(
              session && activityIsWorking(activityForSession(session.session_id))
            );
            return `<div class="campaign-shelf-entry"><button class="campaign-shelf-item" type="button" ` +
              `data-home-campaign-id="${escapeHtml(campaign.campaign_id)}" ` +
              `data-home-session-id="${escapeHtml(session && session.session_id)}">` +
              `<span class="campaign-shelf-name">${escapeHtml(campaign.display_name)}</span>` +
              `<span class="campaign-shelf-meta">${escapeHtml(sessionSummary(session))} · ` +
              `Revision ${escapeHtml(campaign.revision)}</span>` +
              `${streaming ? '<span class="campaign-shelf-working"><span class="builder-stream-spinner" aria-hidden="true"></span>Working…</span>' : ''}` +
              '</button>' + `<button class="archive-link campaign-archive-link" type="button" ` +
              `data-archive-campaign="${escapeHtml(campaign.campaign_id)}" ` +
              `aria-label="Archive campaign ${escapeHtml(campaign.display_name)}" ` +
              `${state.archivingItems.has(campaign.campaign_id) ? 'disabled' : ''}>Archive</button></div>`;
          }).join('');
        return `<section class="setting-shelf"><h3>${escapeHtml(setting.label)}</h3>` +
          `<div class="campaign-shelf-items">${tiles}</div></section>`;
      }).join('');
      return '<section class="ruleset-shelf">' +
        `<div class="ruleset-shelf-heading"><h2>${escapeHtml(ruleset.label)}</h2>` +
        `<span>${rulesetCount} campaign${rulesetCount === 1 ? '' : 's'}</span></div>` +
        settings + '</section>';
    }).join('');
    wireHomeCampaignLinks();
  }

  // ---- Campaign index and Builder chat navigation ----
  async function refreshBuilderChats() {
    if (!state.bridge || !state.bridge.builder_chat_index) return;
    try {
      state.builderChats = await state.bridge.builder_chat_index() || [];
      // Publication can rename a saved chat while it remains open.
      if (!state.activeCampaignId && state.activeSessionRole === 'builder') {
        const selected = state.builderChats.find(
          (chat) => chat.session?.session_id === state.activeSessionId
        );
        if (selected) $('session-title').textContent = selected.display_name || 'Builder Chat';
      }
    } catch (e) {
      state.builderChats = [];
    }
    renderBuilderChatRail();
    renderArchivedItems();
  }

  async function refreshNavigationIndexes() {
    // A large local index can take longer than the timer interval. Keep at most one
    // paired refresh in flight so periodic reads never form an unbounded owner queue.
    if (state.navigationRefreshBusy) return;
    state.navigationRefreshBusy = true;
    try {
      await refreshCampaignIndex();
      await refreshBuilderChats();
    } finally {
      state.navigationRefreshBusy = false;
    }
  }

  function renderBuilderChatRail() {
    const rail = $('builder-chat-rail');
    const chats = [
      ...state.builderChats.filter((chat) => chat.session?.status !== 'stopped').map((chat) => ({
        campaignId: null,
        displayName: chat.display_name || 'New chat',
        session: chat.session || {},
        context: 'Independent Builder',
      })),
      ...state.campaigns.filter((campaign) => campaign.lifecycle === 'active').flatMap((campaign) =>
        (Array.isArray(campaign.sessions) ? campaign.sessions : [])
          .filter((session) => session.role === 'builder' && session.status !== 'stopped')
          .map((session) => ({
            campaignId: campaign.campaign_id,
            displayName: campaign.display_name,
            session,
            context: campaign.availability === 'draft' ? 'Campaign Interview' : 'Campaign Builder',
          }))
      ),
    ];
    if (!chats.length) {
      rail.dataset.markup = '';
      rail.innerHTML = '<div class="builder-chat-empty">No Builder sessions yet.</div>';
      return;
    }
    const markup = chats.map((chat) => {
      const session = chat.session;
      const active = session.session_id === state.activeSessionId ? ' active' : '';
      const streaming = activityIsWorking(activityForSession(session.session_id));
      const activityLabel = activityStatusLabel(session.session_id, session.status || 'active');
      const description = `${chat.displayName} · ${activityLabel} · ${chat.context}`;
      return `<div class="builder-chat-row"><button type="button" class="builder-chat-item${active}" ` +
        `title="${escapeHtml(description)}" aria-label="${escapeHtml(description)}" ` +
        `aria-current="${active ? 'true' : 'false'}" ` +
        `data-builder-session-id="${escapeHtml(session.session_id || '')}" ` +
        `data-builder-campaign-id="${escapeHtml(chat.campaignId)}">` +
        `<div class="builder-chat-title">${streaming ? '<span class="builder-stream-spinner" aria-hidden="true"></span>' : ''}` +
        `<span class="builder-chat-name">${escapeHtml(chat.displayName)}</span></div></button>` +
        (chat.campaignId ? '' : `<details class="builder-chat-controls"><summary aria-label="Actions for ${escapeHtml(chat.displayName)}">···</summary><div>` +
          `<button type="button" data-archive-builder="${escapeHtml(session.session_id)}" ` +
          `${state.archivingItems.has(session.session_id) ? 'disabled' : ''}>Archive</button>` +
          `<button type="button" class="builder-chat-rename" ` +
          `data-rename-builder="${escapeHtml(session.session_id)}" ` +
          `aria-label="Rename ${escapeHtml(chat.displayName)}">Rename</button>` +
          `<button type="button" class="builder-chat-delete" ` +
          `data-delete-builder="${escapeHtml(session.session_id)}" ` +
          `aria-label="Delete ${escapeHtml(chat.displayName)}" ` +
          `${streaming || state.deletingBuilderChats.has(session.session_id) ? 'disabled' : ''}>` +
          `Delete</button></div></details>`) + '</div>';
    }).join('');
    if (rail.dataset.markup === markup) return;
    rail.dataset.markup = markup;
    rail.innerHTML = markup;
    rail.querySelectorAll('[data-builder-session-id]').forEach((button) => {
      button.addEventListener('click', () => {
        if (button.dataset.builderCampaignId) {
          void openCampaign(button.dataset.builderCampaignId, button.dataset.builderSessionId);
        } else {
          void openBuilderChat(button.dataset.builderSessionId);
        }
      });
    });
    rail.querySelectorAll('[data-rename-builder]').forEach((button) => {
      button.addEventListener('click', () => { void renameBuilderChat(button.dataset.renameBuilder); });
    });
    rail.querySelectorAll('[data-delete-builder]').forEach((button) => {
      button.addEventListener('click', () => { void deleteBuilderChat(button.dataset.deleteBuilder); });
    });
    rail.querySelectorAll('[data-archive-builder]').forEach((button) => {
      button.addEventListener('click', () => { void setBuilderArchived(button.dataset.archiveBuilder, true); });
    });
  }

  function archiveStatus(id, message) {
    $(id).textContent = message;
    $(id).hidden = !message;
  }

  // Stopped independent chats use the existing durable inactive lifecycle; stopping generation
  // alone leaves the saved session active. Campaign-owned sessions follow their campaign instead.
  function renderArchivedItems() {
    const chats = state.builderChats.filter((chat) => chat.session?.status === 'stopped');
    const campaigns = state.campaigns.filter((campaign) => campaign.lifecycle === 'archived');
    $('archived-items-count').textContent = `(${chats.length + campaigns.length})`;
    const markup = [
      ...chats.map((chat) => ({ id: chat.session.session_id, name: chat.display_name, type: 'Builder chat', action: 'builder' })),
      ...campaigns.map((campaign) => ({ id: campaign.campaign_id, name: campaign.display_name,
        type: campaign.availability === 'draft' ? 'Campaign draft · includes Interview' : 'Campaign · includes saved sessions', action: 'campaign' })),
    ].map((item) => `<div class="archived-item"><div><strong>${escapeHtml(item.name)}</strong>` +
      `<small>${escapeHtml(item.type)}</small></div><button class="btn btn-secondary btn-sm" ` +
      `data-restore-${item.action}="${escapeHtml(item.id)}" ` +
      `${state.archivingItems.has(item.id) ? 'disabled' : ''}>Restore</button></div>`).join('') ||
      '<p class="home-empty-copy">No archived items.</p>';
    const list = $('archived-items-list');
    if (list.dataset.markup === markup) return;
    list.dataset.markup = markup;
    list.innerHTML = markup;
    for (const type of ['builder', 'campaign']) list.querySelectorAll(`[data-restore-${type}]`).forEach((button) => {
      button.addEventListener('click', () => {
        if (type === 'builder') void setBuilderArchived(button.dataset.restoreBuilder, false);
        else void setCampaignArchived(button.dataset.restoreCampaign, false);
      });
    });
  }

  async function setBuilderArchived(sessionId, archived) {
    const chat = state.builderChats.find((entry) => entry.session?.session_id === sessionId);
    const method = archived ? 'archive_builder_chat' : 'restore_builder_chat';
    const statusId = archived ? 'builder-archive-status' : 'archive-action-status';
    if (!chat || !state.bridge?.[method] || state.archivingItems.has(sessionId)) return;
    if (activityIsWorking(activityForSession(sessionId)) || state.deletingBuilderChats.has(sessionId)) {
      archiveStatus(statusId, 'Finish or stop this Builder’s work before archiving it.');
      return;
    }
    state.archivingItems.add(sessionId);
    renderBuilderChatRail(); renderArchivedItems(); refreshSessionInputAdmission();
    try {
      const saved = await state.bridge[method](sessionId);
      state.builderChats = state.builderChats.map((entry) => entry.session?.session_id === sessionId ? saved : entry);
      if (archived && state.activeSessionId === sessionId) showHome();
      archiveStatus(statusId, `“${chat.display_name}” ${archived ? 'archived' : 'restored'}.`);
    } catch (_) {
      archiveStatus(statusId, `${archived ? 'Archive' : 'Restore'} failed. Finish or stop any work in this chat, then try again. Your saved chat is kept.`);
    } finally {
      state.archivingItems.delete(sessionId);
      renderBuilderChatRail(); renderArchivedItems(); refreshSessionInputAdmission();
    }
  }

  async function setCampaignArchived(campaignId, archived) {
    const campaign = state.campaigns.find((entry) => entry.campaign_id === campaignId);
    const method = archived ? 'archive_campaign' : 'restore_archived_campaign';
    if (!campaign || !state.bridge?.[method] || state.archivingItems.has(campaignId)) return;
    if (archived && [...state.sessionActivities.values()].some((activity) =>
      activity.campaignId === campaignId && activityIsWorking(activity))) {
      archiveStatus('archive-action-status', 'Finish or stop this campaign’s work before archiving it.');
      return;
    }
    state.archivingItems.add(campaignId);
    renderHome();
    try {
      const session = preferredCampaignSession(campaign);
      await state.bridge[method](`${archived ? 'archive' : 'restore'}-${crypto.randomUUID()}`,
        campaignId, campaign.revision, session?.session_id || null);
      if (archived && state.activeCampaignId === campaignId) { closeSettings(); showHome(); }
      await refreshCampaignIndex();
      archiveStatus('archive-action-status', `“${campaign.display_name}” ${archived ? 'archived' : 'restored'}.`);
    } catch (_) {
      archiveStatus('archive-action-status', `${archived ? 'Archive' : 'Restore'} failed. Finish any outstanding campaign turn or Interview work, then try again. Your campaign is kept.`);
    } finally { state.archivingItems.delete(campaignId); renderHome(); renderBuilderChatRail(); }
  }

  // Delete one explicitly confirmed independent chat; late completion keeps the selected chat intact.
  async function deleteBuilderChat(sessionId) {
    const chat = state.builderChats.find((entry) => entry.session?.session_id === sessionId);
    if (!chat || !state.bridge?.delete_builder_chat || state.deletingBuilderChats.has(sessionId) ||
        activityIsWorking(activityForSession(sessionId))) return;
    const confirmation = window.prompt(
      `Permanently delete “${chat.display_name}” and its unfinished files? ` +
      `Saved library content will remain. Type DELETE to continue.`
    );
    if (confirmation !== 'DELETE') return;
    state.deletingBuilderChats.add(sessionId);
    renderBuilderChatRail();
    refreshSessionInputAdmission();
    let deleted = false;
    try {
      await state.bridge.delete_builder_chat(sessionId, `permanently delete ${sessionId}`);
      deleted = true;
      const activity = activityForSession(sessionId);
      if (activity) {
        stopStreamHeartbeat(activity);
        stopProjectionPolling(activity);
      }
      state.sessionActivities.delete(sessionId);
      state.builderChats = state.builderChats.filter((item) => item.session?.session_id !== sessionId);
      state.sessionDrafts.delete(sessionId);
      state.builderQuestionDrafts.delete(sessionId);
      if (state.activeSessionId === sessionId) {
        state.activeSessionId = null;
        $('player-input').value = '';
        showHome();
      }
      persistDisconnectedActivities();
      await refreshCampaignIndex();
    } catch (error) {
      const message = friendlyCommandError(
        deleted ? 'Chat deleted; the display could not refresh' : 'Chat deletion failed', error
      );
      if (state.activeSessionId === sessionId) setInlineStatus(message);
      else window.alert(message);
    } finally {
      state.deletingBuilderChats.delete(sessionId);
      renderBuilderChatRail();
      refreshSessionInputAdmission();
    }
  }

  async function renameBuilderChat(sessionId) {
    const chat = state.builderChats.find((entry) => entry.session?.session_id === sessionId);
    if (!chat || !state.bridge?.rename_builder_chat) return;
    const displayName = window.prompt('Chat name', chat.display_name || '');
    if (displayName == null || !displayName.trim()) return;
    try {
      const renamed = await state.bridge.rename_builder_chat(sessionId, displayName.trim());
      await refreshCampaignIndex();
      if (state.activeSessionId === sessionId) {
        $('session-title').textContent = renamed.display_name;
      }
    } catch (error) {
      if (state.activeSessionId === sessionId) {
        setInlineStatus(friendlyCommandError('Rename rejected', error));
      }
    }
  }

  async function openBuilderChat(sessionId) {
    state.displayHistory = null;
    const chat = state.builderChats.find(
      (entry) => entry.session && entry.session.session_id === sessionId
    );
    if (!chat) return;
    if (chat.session.status === 'stopped') {
      showHome();
      $('archived-items').open = true;
      archiveStatus('archive-action-status', 'Restore this archived Builder chat to continue.');
      return;
    }
    cancelCampaignOpening();
    gamePanels().leave();
    saveSelectedSessionDraft();
    hideConstructionPanel(false);
    showSessionView();
    clearModelGuidanceWarning();
    state.activeCampaignId = null;
    state.activeSessionId = sessionId;
    state.activeSessionRole = 'builder';
    state.campaignOpening = null;
    $('campaign-loading-status').hidden = true;
    const activity = ensureSessionActivity({
      sessionId,
      sessionRole: 'builder',
      campaignId: null,
      ownerId: sessionId,
    });
    restoreSelectedSessionDraft();
    state.activeSnapshot = null;
    state.panelProjectionKey = null;
    renderRulesetPanelProjection(null);
    renderBuilderChatRail();
    renderSidebarState();
    const isCampaignSetup = chat.display_name === 'New Campaign';
    $('session-title').textContent = chat.display_name || 'Builder Chat';
    updateChronology(
      $('chronology'),
      '<div class="empty-state" data-chronology-key="opening-builder">' +
        '<div class="empty-icon">◇</div>' +
        `<div>Opening ${escapeHtml(chat.display_name || 'Builder Chat')}…</div>` +
        '<div class="muted">Saved history is loading.</div>' +
      '</div>',
      () => {},
    );
    setInlineStatus(activityIsWorking(activity)
      ? 'Builder is working…'
      : activity && ['interrupted', 'stopped'].includes(activity.phase)
          ? activity.terminalLabel || 'Builder generation was interrupted'
          : isCampaignSetup
            ? 'Choose content with Builder, then Configure Campaign'
            : '');
    $('ctx-campaign').textContent = isCampaignSetup ? 'Not created yet' : 'None';
    $('ctx-lifecycle').textContent = isCampaignSetup ? 'Campaign setup' : 'Independent chat';
    $('ctx-writer').textContent = 'Builder';
    $('export-campaign-btn').disabled = true;
    updateLifecycleControls(null, null);
    const canSubmit = refreshSessionInputAdmission();
    $('builder-input-options').hidden = false;
    updateCampaignSetupControl();
    $('attach-builder-sources-btn').disabled = !canSubmit || selectedSessionIsWorking();
    $('allow-public-web').disabled = !canSubmit || selectedSessionIsWorking();
    setWebResearchPermission(false);
    updateModelSwitchButtons();
    const loaded = await renderBuilderSession();
    if (loaded === false && state.activeSessionId === sessionId && state.activeSessionRole === 'builder') {
      updateChronology(
        $('chronology'),
        '<div class="empty-state" data-chronology-key="builder-history-error">' +
          '<div>Saved history could not be loaded.</div>' +
          '<button class="btn-ghost" data-retry-builder-history>Retry loading</button>' +
        '</div>',
        () => $('chronology').querySelector('[data-retry-builder-history]')?.addEventListener(
          'click', () => { void openBuilderChat(sessionId); },
        ),
      );
    }
  }

  function updateCampaignSetupControl() {
    const chat = state.builderChats.find(
      (entry) => entry.session && entry.session.session_id === state.activeSessionId
    );
    const available = !state.activeCampaignId && state.activeSessionRole === 'builder' &&
      chat && chat.display_name === 'New Campaign';
    const button = $('open-campaign-setup-btn');
    button.hidden = !available;
    button.disabled = !available || selectedSessionIsWorking();
  }

  // Callers that need the saved list must also wait for a newer refresh that supersedes theirs.
  async function refreshCampaignIndex() {
    if (!state.bridge || !state.bridge.campaign_index) return;
    const requestId = ++state.campaignIndexRequestId;
    state.campaignIndexStatus = 'loading';
    state.campaignIndexError = null;
    renderSidebarState();
    let pending = state.campaignIndexRefresh = (async () => {
      try {
        const projection = await state.bridge.campaign_index();
        if (requestId !== state.campaignIndexRequestId) return;
        state.campaigns = Array.isArray(projection) ? projection : [];
        state.campaignIndexStatus = 'ready';
      } catch (e) {
        if (requestId !== state.campaignIndexRequestId) return;
        state.campaignIndexStatus = 'error';
        state.campaignIndexError = e;
      }
      renderSidebarState();
      renderBuilderChatRail();
      renderHome();
    })();
    await pending;
    while (pending !== state.campaignIndexRefresh) {
      pending = state.campaignIndexRefresh;
      await pending;
    }
  }

  function panelLayout() {
    const current = panelHost?.snapshot();
    if (current) return current;
    try {
      const value = JSON.parse(localStorage.getItem(PANEL_LAYOUT_KEY) || '{}');
      return value && typeof value === 'object' ? value : {};
    } catch (e) {
      return {};
    }
  }

  function savePanelLayout(patch) {
    if (panelHost?.snapshot()) {
      if (patch.sidebar_width != null) void panelHost.changed({ type: 'sidebar-width', width: patch.sidebar_width });
      for (const [panelId, height] of Object.entries(patch.panel_sizes || {})) {
        void panelHost.changed({ type: 'size', panelId, height });
      }
      return;
    }
    const next = { ...panelLayout(), ...patch };
    localStorage.setItem(PANEL_LAYOUT_KEY, JSON.stringify(next));
  }

  // Resolve a panel's saved height within its declared package bounds.
  function panelSize(surface) {
    const minimum = Number.isInteger(surface.minimum_size) ? surface.minimum_size : 96;
    const declared = Number.isInteger(surface.default_size) ? surface.default_size : 180;
    const rosterCount = new Set([...(surface.data?.participants || []).map(item => item.id),
      ...(state.panelProjection?.player_control?.actors || []).filter(item => surface.data?.entities?.[item.id]).map(item => item.id)]).size;
    const preferred = ['tactical_grid', 'coordinate_map'].includes(surface.renderer) ? Math.max(declared, 520)
      : surface.renderer === 'unit_list' ? Math.max(declared, 300)
        : surface.renderer === 'party' && rosterCount ? Math.min(declared, Math.max(minimum, rosterCount * 90 + 30)) : declared;
    const savedSizes = panelLayout().panel_sizes;
    const saved = savedSizes && Number(savedSizes[surface.id]);
    return Math.max(minimum, Math.min(2000, Number.isFinite(saved) ? saved : preferred));
  }

  function hpPanelMarkup(entity) {
    const hp = Number.isFinite(entity && entity.hp) ? entity.hp : null;
    const maxHp = Number.isFinite(entity && entity.max_hp) ? entity.max_hp : null;
    if (hp == null) return '';
    if (maxHp == null || maxHp <= 0) return `<div class="panel-unit-meta">HP ${hp}</div>`;
    const percent = Math.max(0, Math.min(100, (hp / maxHp) * 100));
    return `<div class="panel-unit-meta">HP ${hp} / ${maxHp}</div>` +
      `<div class="panel-hp-bar"><div class="panel-hp-fill" ` +
      `style="width:${percent.toFixed(1)}%"></div></div>`;
  }

  function conditionPanelMarkup(entity) {
    const conditions = entity && Array.isArray(entity.conditions) ? entity.conditions : [];
    if (!conditions.length) return '';
    return `<div class="panel-condition-list">${conditions.map((condition) =>
      `<span class="panel-condition">${escapeHtml(condition)}</span>`).join('')}</div>`;
  }

  function actionBindsActor(action) {
    return Boolean(action && action.bindings && typeof action.bindings === 'object' &&
      Object.values(action.bindings).includes('actor'));
  }

  // The owner projects the Ruleset's current control scope; absent declarations retain legacy use.
  function playerControlsActor(actorId) {
    const scope = state.panelProjection && state.panelProjection.player_control;
    return !scope || (scope.available === true && Array.isArray(scope.actors) &&
      scope.actors.some((actor) => actor.id === actorId));
  }

  // Keep body IDs in map intent; use the owner's declared reference for character controls.
  function tacticalActorId(surface, bodyId) {
    const body = surface.data?.entities?.find((item) => item.id === bodyId);
    if (!body) return null;
    return surface.data.actor_reference ? body.actor_ref : body.id;
  }

  function tacticalControlsActor(surface, bodyId) {
    const actorId = tacticalActorId(surface, bodyId);
    return Boolean(actorId) && playerControlsActor(actorId);
  }

  function panelRecoveryKey() {
    return `sylvia-panel-recovery-v1:${state.activeCampaignId}:${state.activeSessionId}`;
  }

  function pendingPanelCommand() {
    try {
      const value = JSON.parse(localStorage.getItem(panelRecoveryKey()) || 'null');
      return value?.campaign_id === state.activeCampaignId && value.session_id === state.activeSessionId
        && typeof value.operation_id === 'string' ? value : null;
    } catch (_) { return null; }
  }

  function clearPanelRecovery() {
    localStorage.removeItem(panelRecoveryKey());
    state.panelRecoveryAbsent = null;
  }

  async function recoverPanelAction(retry = false, dismiss = false) {
    const command = pendingPanelCommand(), activity = selectedActivity();
    if (!command || state.panelCommitPending || selectedSessionIsWorking()) return;
    state.panelCommitPending = true;
    try {
      const receipt = retry ? await state.bridge.execute_panel_action(command)
        : await state.bridge.panel_action_receipt(command);
      if (!selectedActivityMatches(activity)) return;
      if (receipt) {
        clearPanelRecovery();
        selectedStatus(activity, receipt.status === 'awaiting_choice'
          ? 'Action saved; resolve the pending choice.' : 'Recovered the saved action.', 'panels');
        await refreshSelectedCampaign();
      } else if (dismiss) {
        clearPanelRecovery();
        selectedStatus(activity, 'No saved result was found. The pending send was dismissed.', 'panels');
      } else {
        state.panelRecoveryAbsent = command.operation_id;
        selectedStatus(activity, 'No saved result was found. Retry the original action or dismiss it.', 'panels');
      }
    } catch (error) {
      selectedStatus(activity, friendlyCommandError('Action recovery could not finish', error), 'panels');
    } finally {
      state.panelCommitPending = false;
      if (selectedActivityMatches(activity)) await renderRulesetPanels(command.campaign_id, true);
    }
  }

  function panelActionAvailable(surface, action, partyActorId = null, pieceId = null) {
    if (!state.activeSessionId || state.panelCommitPending || panelIntentPending() || pendingPanelCommand() ||
        selectedSessionIsWorking() || campaignDisplayPending() || action.available === false) return false;
    if ((!sequenceModeActive() || surface.renderer === 'piece_table') && action.checks_required && !state.panelActionChecks.get(
      panelActionCheckKey(surface, action, partyActorId, pieceId)
    )?.available) return false;
    const bindings = action.bindings || {};
    if (surface.renderer === 'piece_table' && Object.values(bindings).includes('piece') &&
        !window.SylviaPieceTable.selection(surface.data, action, pieceId)) return false;
    const actorParameter = action.actor_parameter ||
      Object.keys(bindings).find((key) => bindings[key] === 'actor');
    if (!actorParameter) return true;
    const actorId = bindings[actorParameter] === 'actor'
      ? ['party', 'unit_list', 'inventory'].includes(surface.renderer) ? partyActorId
        : ['tactical_grid', 'coordinate_map'].includes(surface.renderer) ? tacticalSelection(surface).actor
          : surface.renderer === 'network_graph' ? networkSelection(surface).actor : null
      : action.params && action.params[actorParameter];
    return Boolean(actorId) && (['tactical_grid', 'coordinate_map'].includes(surface.renderer)
      && bindings[actorParameter] === 'actor' ? tacticalControlsActor(surface, actorId)
        : playerControlsActor(actorId));
  }

  function panelIntentPending() {
    return state.panelIntentPending?.campaignId === state.activeCampaignId &&
      state.panelIntentPending?.sessionId === state.activeSessionId;
  }

  // A delayed preview retains its captured binding only while its original context is valid.
  function panelActionStillCurrent(command, projectionRevision) {
    if (command.campaign_id !== state.activeCampaignId || command.session_id !== state.activeSessionId) return false;
    const surface = state.panelProjection?.surfaces.find((item) => item.id === command.surface_id);
    const action = surface?.actions?.find((item) => item.id === command.panel_action_id);
    if (!action || action.available === false) return false;
    if (state.panelProjection.revision !== projectionRevision) return false;
    if (surface.renderer === 'piece_table' && Object.values(action.bindings || {}).includes('piece')) {
      return Boolean(window.SylviaPieceTable.selection(surface.data, action, command.selection?.piece));
    }
    if (['tactical_grid', 'coordinate_map'].includes(surface.renderer)) {
      if (command.selection?.map_revision !== surface.data.map_revision) return false;
      if (command.selection?.target && !surface.data.entities?.some((item) => item.id === command.selection.target)) return false;
      return !actionBindsActor(action) || tacticalControlsActor(surface, command.selection?.actor) &&
        surface.data.entities?.some((item) => item.id === command.selection?.actor);
    }
    if (surface.renderer === 'network_graph') {
      if (command.selection?.graph_revision !== surface.data.graph_revision) return false;
      if (command.selection?.node && !surface.data.nodes?.some((item) => item.id === command.selection.node)) return false;
      if (command.selection?.link && !surface.data.links?.some((item) => item.id === command.selection.link)) return false;
      return !actionBindsActor(action) || networkControlsActor(command.selection?.actor);
    }
    if (!actionBindsActor(action)) return true;
    const actor = command.selection?.actor;
    return playerControlsActor(actor) && (surface.data?.participants?.some((item) => item.id === actor) ||
      Boolean(surface.data?.entities?.[actor]));
  }

  function panelActionSelection(surface, action, partyActorId = null, pieceId = null) {
    if (surface.renderer === 'piece_table') return window.SylviaPieceTable.selection(surface.data, action, pieceId);
    if (['party', 'unit_list'].includes(surface.renderer) && partyActorId) return { actor: partyActorId };
    if (['tactical_grid', 'coordinate_map'].includes(surface.renderer)) return tacticalActionSelection(surface, action);
    if (surface.renderer === 'network_graph') return networkActionSelection(surface, action);
    if (surface.renderer === 'inventory' && partyActorId) return inventoryEquipmentSelection(surface, action, partyActorId);
    return null;
  }

  function panelActionCheckKey(surface, action, partyActorId = null, pieceId = null) {
    return JSON.stringify([state.activeCampaignId, state.activeSessionId,
      state.panelProjection?.revision, surface.id, action.id,
      panelActionSelection(surface, action, partyActorId, pieceId)]);
  }

  async function refreshPanelActionChecks(projection, container) {
    if (!projection || !state.bridge?.player_action_availability || selectedSessionIsWorking()) return;
    const sessionId = state.activeSessionId;
    const currentButtons = () => Array.from(container.querySelectorAll('[data-panel-action-id]'))
      .map((button) => {
        const surface = projection.surfaces.find((item) =>
          item.id === button.closest('[data-panel-id]')?.dataset.panelId);
        const action = surface?.actions?.find((item) => item.id === button.dataset.panelActionId);
        if (!action || (!action.checks_required && !action.map_preview_enabled)) return null;
        return { button, surface, action, actor: button.dataset.panelActorId,
          piece: button.dataset.panelPieceId,
          key: panelActionCheckKey(surface, action, button.dataset.panelActorId, button.dataset.panelPieceId) };
      }).filter(Boolean);
    const apply = () => currentButtons().forEach(({ button, surface, action, actor, piece, key }) => {
      const reason = state.panelActionChecks.get(key)?.reason || '';
      button.disabled = !panelActionAvailable(surface, action, actor, piece);
      button.title = panelPlayerReason(reason);
      button.setAttribute('aria-description', reason);
      // Show only a current owner-derived price; keep native no-confirmation policy unchanged.
      if (surface.renderer === 'unit_list' && actionBindsActor(action)) {
        const baseLabel = button.dataset.priceBaseLabel || button.textContent;
        button.dataset.priceBaseLabel = baseLabel;
        const check = state.panelActionChecks.get(key);
        const prices = (check?.preview?.summary || []).filter((row) => row.label === 'AP cost' &&
          typeof row.value === 'number' && Number.isFinite(row.value));
        button.textContent = check?.available === true && prices.length === 1
          ? baseLabel.replace(/\s*·\s*\d+(?:\.\d+)?\s+AP$/, '') + ` · ${prices[0].value} AP` : baseLabel;
      }
    });
    const pending = currentButtons().filter((item) => !state.panelActionChecks.has(item.key));
    if (!pending.length) { apply(); return; }
    const pendingEntries = new Map();
    pending.forEach(({ key }) => {
      const entry = { available: false, pending: true, reason: 'Checking action availability…' };
      pendingEntries.set(key, entry);
      state.panelActionChecks.set(key, entry);
    });
    const projectionIsCurrent = () => state.activeSessionId === sessionId &&
      state.activeCampaignId === projection.campaign_id &&
      state.panelProjection?.campaign_id === projection.campaign_id &&
      state.panelProjection?.revision === projection.revision;
    const releasePending = () => pendingEntries.forEach((entry, key) => {
      if (state.panelActionChecks.get(key) === entry) state.panelActionChecks.delete(key);
    });
    apply();
    try {
      const response = await state.bridge.player_action_availability({
        campaign_id: projection.campaign_id, expected_revision: projection.revision,
        requests: pending.map(({ surface, action, actor, piece }) => ({
          source: { kind: 'panel', surface_id: surface.id, action_id: action.id },
          selection: panelActionSelection(surface, action, actor, piece),
        })),
      });
      if (!projectionIsCurrent()) { releasePending(); return; }
      if (response.campaign_id !== projection.campaign_id || response.revision !== projection.revision ||
          !Array.isArray(response.results) || response.results.length !== pending.length) {
        throw new Error('Action availability changed; refresh the campaign.');
      }
      pending.forEach(({ key }, index) => {
        if (state.panelActionChecks.get(key) !== pendingEntries.get(key)) return;
        state.panelActionChecks.set(key, {
          available: response.results[index].available === true,
          reason: typeof response.results[index].reason === 'string' ? response.results[index].reason : '',
          preview: response.results[index].preview || null,
          map_preview: response.results[index].map_preview || null,
        });
      });
    } catch (_) {
      if (!projectionIsCurrent()) { releasePending(); return; }
      pending.forEach(({ key }) => {
        if (state.panelActionChecks.get(key) === pendingEntries.get(key)) {
          state.panelActionChecks.set(key, { available: false,
            reason: 'Could not check this action. Refresh the campaign to try again.' });
        }
      });
    }
    apply();
    projection.surfaces.filter((surface) => ['tactical_grid', 'coordinate_map'].includes(surface.renderer)).forEach((surface) => {
      const panel = container.querySelector(`[data-panel-id="${CSS.escape(surface.id)}"]`);
      if (panel) updateTacticalPreviewDisplay(panel, surface);
    });
    projection.surfaces.filter((surface) => surface.renderer === 'network_graph').forEach((surface) => {
      const panel = container.querySelector(`[data-panel-id="${CSS.escape(surface.id)}"]`);
      const preview = panel?.querySelector('[data-network-access-preview]');
      if (preview) preview.innerHTML = networkAccessPreview(surface);
    });
  }

  // Humanize identifier-only fallbacks; authored labels and ordinary names stay verbatim.
  function panelPlayerReason(value) {
    const reason = String(value || '');
    if (reason.startsWith('Required Ruleset decision:')) {
      return 'Paused for a rules decision. Resolve it before continuing.';
    }
    return reason.replace(/\bactor\b/gi, word => word[0] === 'A' ? 'Character' : 'character');
  }

  function panelIdentifierLabel(value) {
    const text = String(value ?? '');
    return /^[a-z0-9]+(?:[_-][a-z0-9]+)+$/.test(text)
      ? text.replace(/[_-]/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
      : text;
  }

  // Present only the selected actor's saved values; selection never runs mechanics.
  function selectedMapActorMarkup(surface, selection) {
    const actorId = tacticalActorId(surface, selection.actor);
    const body = surface.data.entities.find((item) => item.id === selection.actor);
    if (!body) return '<div class="map-turn-strip panel-empty">Choose an acting character on the map. Inspect characters in Party.</div>';
    const source = state.panelProjection?.surfaces.find((item) =>
      ['party', 'unit_list'].includes(item.renderer) && item.data?.entities?.[actorId]);
    const entity = source?.data.entities[actorId] || {};
    return `<div class="map-turn-strip" data-selected-actor="${escapeHtml(actorId)}">` +
      `<strong>Acting: ${escapeHtml(source?.data.participants?.find(member => member.id === actorId)?.data?.name || body.display?.label || entity.name || actorId)}</strong>` +
      personalResourceMarkup(entity) + actionReadinessMarkup(entity, actorId) + '</div>';
  }

  // Labels reflect admitted actions and the selected saved loadout, never prototype mechanics.
  function mapActionLabel(surface, action) {
    const selection = tacticalSelection(surface);
    const actorId = tacticalActorId(surface, selection.actor);
    const entity = state.panelProjection?.surfaces.find((item) =>
      ['party', 'unit_list'].includes(item.renderer) && item.data?.entities?.[actorId])?.data.entities[actorId];
    if (['fire', 'burst', 'full-auto'].includes(action.id) && entity?.weapon?.id) {
      return `${action.label || action.id} · ${panelIdentifierLabel(entity.weapon.id)}`;
    }
    const set = entity?.weapon_sets?.[entity.weapon_sets.active_set];
    if (action.id.startsWith('attack-') && set?.main_hand &&
        ((action.params?.attack_mode === 'ranged' && entity.weapon_sets.active_set === 'ranged') ||
         (action.params?.attack_mode === 'melee' && entity.weapon_sets.active_set !== 'ranged'))) {
      return `${panelIdentifierLabel(set.main_hand)} · ${action.label || action.id}`;
    }
    return action.id === 'end-turn' ? 'End turn' : action.label || action.id;
  }

  function mapActionPalette(surface) {
    const selected = tacticalPreviewState(surface, state.tacticalViews[surface.id] || {});
    const primary = new Set(['move', 'move-walk', 'fire', 'aim', 'burst', 'attack-melee-strength',
      'attack-ranged-dexterity', 'cast-fire_bolt', 'cast-magic_missile', 'dash']);
    const button = action => `<button type="button" class="map-action-choice${action.id === selected.action?.id ? ' active' : ''}" ` +
      `data-tactical-action="${escapeHtml(action.id)}" aria-pressed="${action.id === selected.action?.id}">` +
      `${escapeHtml(mapActionLabel(surface, action))}</button>`;
    const main = selected.actions.filter(action => primary.has(action.id)).map(button).join('');
    const more = selected.actions.filter(action => !primary.has(action.id)).map(button).join('');
    return `<div class="map-action-palette" aria-label="Choose an action">${main}` +
      (more ? `<details class="map-more-actions"><summary>More actions</summary><div>${more}</div></details>` : '') + '</div>';
  }

  function actionReadinessMarkup(entity, actorId) {
    const combat = state.panelProjection?.surfaces.find((item) => item.renderer === 'encounter')?.data?.combat;
    if (!combat?.encounter_active) return '';
    const active = combat.turn_active && combat.active_actor === actorId;
    const fields = [active ? `Action ${combat.action_available ? 'ready' : 'spent'}` : 'Waiting for turn',
      typeof entity.bonus_action_available === 'boolean' ? `Bonus ${entity.bonus_action_available ? 'ready' : 'spent'}` : '',
      typeof entity.reaction_available === 'boolean' ? `Reaction ${entity.reaction_available ? 'ready' : 'spent'}` : '',
      active && Number.isFinite(combat.movement_remaining) ? `Move remaining ${combat.movement_remaining}` : ''].filter(Boolean);
    return `<span class="panel-unit-meta" data-action-readiness="${escapeHtml(actorId)}">${fields.map(escapeHtml).join(' · ')}</span>`;
  }

  function weaponReadinessMarkup(entity) {
    const weapon = entity.weapon;
    if (!weapon || typeof weapon !== 'object') return '';
    const fields = [Number.isFinite(weapon.magazine?.rounds) ? `Rounds ${weapon.magazine.rounds}` : '',
      typeof weapon.chamber === 'string' ? `Chamber ${panelIdentifierLabel(weapon.chamber.toLowerCase())}` : '',
      Number.isFinite(weapon.current_recoil) ? `Recoil ${weapon.current_recoil}` : '',
      weapon.needs_cycle === true ? 'Cycle required' : ''].filter(Boolean);
    return fields.length ? `<span class="panel-unit-meta">${fields.map(escapeHtml).join(' · ')}</span>` : '';
  }

  function actorInitials(name) {
    return String(name || '?').trim().split(/\s+/).slice(0, 2).map((word) => Array.from(word)[0]).join('').toUpperCase();
  }

  function renderPartyPanel(data, surface) {
    const recorded = data && Array.isArray(data.participants) ? data.participants : [];
    const participants = [...recorded, ...(state.panelProjection?.player_control?.actors || [])
      .filter((item) => data?.entities?.[item.id] && !recorded.some((member) => member.id === item.id))
      .map((item) => ({ id: item.id, kind: '', data: { name: data.entities[item.id].name || item.name || item.id } }))];
    const entities = data && data.entities && typeof data.entities === 'object'
      ? data.entities : {};
    const playerControl = state.panelProjection && state.panelProjection.player_control;
    const controlledActorIds = playerControl && Array.isArray(playerControl.actors)
      ? new Set(playerControl.actors.map((actor) => actor.id)) : null;
    const actorActions = surface && Array.isArray(surface.actions)
      ? surface.actions.filter(actionBindsActor) : [];
    if (!participants.length) return '<div class="panel-empty">No party members.</div>';
    const ordered = [
      ...participants.filter((participant) => (participant.data?.party_role || participant.kind) === 'player_character'),
      ...participants.filter((participant) => (participant.data?.party_role || participant.kind) !== 'player_character'),
    ];
    return ordered.map((participant) => {
      const details = participant.data || {};
      const detailSurface = state.panelProjection?.surfaces.find((item) => item.renderer === 'unit_list' &&
        (item.data?.entities?.[participant.id] || item.data?.participants?.some((member) => member.id === participant.id))) || surface;
      // Namespaced content kinds identify definitions, not player-facing party roles.
      const role = details.party_role ||
        (typeof participant.kind === 'string' && !participant.kind.includes('.') ? participant.kind : '');
      const entity = detailSurface?.data?.entities?.[participant.id] || entities[participant.id] || {};
      const resources = entity.resources && typeof entity.resources === 'object'
        ? Object.entries(entity.resources).map(([name, value]) =>
          `${panelIdentifierLabel(name)}: ${value}`).join(' · ')
        : '';
      const playerControlled = controlledActorIds && controlledActorIds.has(participant.id);
      const combat = state.panelProjection?.surfaces.find((item) => item.renderer === 'encounter')?.data?.combat;
      const currentActor = combat?.encounter_active && (combat.current_actor || combat.active_actor) === participant.id;
      const controlLabels = [
        playerControlled ? 'Player controlled' : '',
        currentActor ? 'Current turn' : '',
      ].filter(Boolean);
      const controls = actorActions.map((action) => {
        const available = panelActionAvailable(surface, action, participant.id);
        return `<button class="btn btn-secondary btn-sm ruleset-panel-action" type="button" ` +
          `data-panel-action-id="${escapeHtml(action.id || '')}" ` +
          `data-panel-actor-id="${escapeHtml(participant.id)}" ` +
          `${available ? '' : 'disabled'}>${escapeHtml(action.label || action.id || 'Action')}</button>`;
      }).join('');
      const maps = state.panelProjection?.surfaces.filter((item) => ['tactical_grid', 'coordinate_map'].includes(item.renderer) &&
        Array.isArray(item.data?.entities) &&
        item.data.entities.some((member) => tacticalActorId(item, member.id) === participant.id)) || [];
      const selectedOnMap = characterInspection().id === participant.id;
      const mapBody = maps[0]?.data.entities.find((member) => tacticalActorId(maps[0], member.id) === participant.id);
      const posture = tacticalPostureText(mapBody, maps[0]?.data);
      const selectActor = inspectableCharacters().some((item) => item.id === participant.id)
        ? `<button type="button" class="btn btn-secondary btn-sm" ` +
          `data-party-select-actor="${escapeHtml(participant.id)}" aria-pressed="${selectedOnMap}" aria-label="${selectedOnMap ? 'Inspecting' : 'Inspect'} ${escapeHtml(details.name || participant.id)}">` +
          `${selectedOnMap ? 'Inspecting' : 'Inspect'}</button>` : '';
      const referencedActions = detailSurface === surface ? [] : (detailSurface.actions || [])
        .filter((action) => actionBindsActor(action) && !actorActions.some((item) => item.id === action.id));
      const detailActions = referencedActions.length ? `<div class="panel-unit-meta">` + referencedActions.map((action) =>
        `${escapeHtml(action.label || action.id)}${panelActionAvailable(detailSurface, action, participant.id) ? '' : ' (unavailable)'}`).join(' · ') +
        `</div><button type="button" class="btn btn-secondary btn-sm" data-character-action-panel="${escapeHtml(detailSurface.id)}">` +
        `Open ${escapeHtml(detailSurface.title || 'Character')} actions</button>` : '';
      const inspection = characterInspection();
      return `<div class="panel-unit${selectedOnMap ? ' character-inspected' : ''}" data-panel-entity="${escapeHtml(participant.id)}">` +
        `<div class="panel-unit-heading"><span class="panel-actor-token" aria-hidden="true">${escapeHtml(actorInitials(details.name || participant.id))}</span><strong>${escapeHtml(details.name || participant.id)}</strong>` +
        (role ? `<span class="panel-unit-role">${escapeHtml(panelIdentifierLabel(role))}</span>` : '') +
        selectActor + `</div>` +
        hpPanelMarkup(entity) + personalResourceMarkup(entity) + conditionPanelMarkup(entity) +
        `<details class="roster-inspection" data-character-expansion="${escapeHtml(participant.id)}"${inspection.expanded.includes(participant.id) ? ' open' : ''}><summary aria-label="Details for ${escapeHtml(details.name || participant.id)}">Details</summary>` +
        (controlLabels.length ? `<div class="panel-unit-meta">${controlLabels.map(escapeHtml).join(' · ')}</div>` : '') +
        characterDetailMarkup(detailSurface, participant.id, true) +
        (posture ? `<div class="panel-unit-meta">${escapeHtml(posture)}</div>` : '') +
        (resources ? `<div class="panel-unit-meta">${escapeHtml(resources)}</div>` : '') +
        (controls || detailActions ? `<details class="character-stat-group"><summary>Available actions</summary><div class="ruleset-panel-actions">${controls}</div>${detailActions}</details>` : '') + '</details>' +
        `</div>`;
    }).join('');
  }

  // Resource values come from committed state; an absent maximum never becomes a guessed bar.
  function personalResourceMarkup(entity) {
    const values = [['RES', 'res'], ['AP', 'ap']].filter(([, field]) => Number.isFinite(entity[field]));
    return values.length ? `<div class="panel-unit-meta">${values.map(([label, field]) =>
      `${label} ${entity[field]}${Number.isFinite(entity[`max_${field}`]) ? ` / ${entity[`max_${field}`]}` : ''}`
    ).join(' · ')}</div>` : '';
  }

  // Use the owning admitted surface so inventory shortcuts retain native bindings and checks.
  function characterActionMarkup(surface, actorId, equipmentOnly = false) {
    const actions = (surface.actions || []).filter((action) => actionBindsActor(action) &&
      (!equipmentOnly || ['cycle_weapon', 'reload_weapon', 'recover_recoil'].includes(action.intent)));
    if (!actions.length) return '';
    return `<div class="ruleset-panel-actions" data-panel-id="${escapeHtml(surface.id)}">` + actions.map((action) =>
      `<button type="button" class="btn btn-secondary btn-sm ruleset-panel-action" ` +
      `data-panel-action-id="${escapeHtml(action.id)}" data-panel-actor-id="${escapeHtml(actorId)}" ` +
      `${panelActionAvailable(surface, action, actorId) ? '' : 'disabled'}>${escapeHtml(action.label || action.id)}</button>`).join('') + '</div>';
  }

  // This bounded renderer focuses an admitted participant and reuses the ordinary actor controls.
  function renderCharacterPanel(surface) {
    const participants = surface.data?.participants || [];
    const inspection = characterInspection();
    const selected = inspection.id;
    const entities = surface.data?.entities || {};
    const actors = state.panelProjection?.player_control?.actors || [];
    const actorId = selected;
    if (!actorId) return characterPickerMarkup() + `<div class="panel-empty" data-character-unavailable="${escapeHtml(inspection.unavailableId || '')}">` +
      (inspection.unavailableId ? 'The previously inspected character is unavailable. ' : '') +
      'Choose a character to inspect.</div>';
    const participant = participants.find((item) => item.id === actorId);
    const entity = entities[actorId];
    const name = participant?.data?.name || entity?.name || actors.find((item) => item.id === actorId)?.name || actorId;
    if (!participant && !entity) return characterPickerMarkup() + `<div class="panel-empty" data-character-unavailable="${escapeHtml(actorId)}">` +
      `<strong>${escapeHtml(name)}</strong><p>Character details are unavailable for this character.</p></div>`;
    const selector = characterPickerMarkup();
    return selector + characterDetailMarkup(surface, actorId);
  }

  // Share the complete admitted detail presentation between Party and Character.
  function characterDetailMarkup(surface, actorId, compact = false) {
    const participant = surface.data?.participants?.find((item) => item.id === actorId);
    const entity = surface.data?.entities?.[actorId] || {};
    const name = participant?.data?.name || entity?.name || actorId;
    const details = participant?.data || {};
    const description = [details.class_id || details.class, Number.isFinite(details.level) ? `Level ${details.level}` : '']
      .filter(Boolean).map((value) => escapeHtml(panelIdentifierLabel(value))).join(' · ');
    const values = ['aptitudes', 'skills'].map((field) => {
      const entries = Object.entries(entity?.[field] || details[field] || {}).filter(([, value]) => Number.isFinite(value));
      return entries.length ? `<details class="character-stat-group" data-character-stat="${escapeHtml(JSON.stringify([actorId, field]))}"><summary>${escapeHtml(panelIdentifierLabel(field))}</summary>` +
        entries.map(([name, value]) => `<span>${escapeHtml(panelIdentifierLabel(name))} <b>${value}</b></span>`).join('') + '</details>' : '';
    }).join('');
    return `<div class="${compact ? 'character-expanded-details' : 'character-hero'}" data-character-details="${escapeHtml(actorId)}"${compact ? '' : ` ${participant ? 'data-panel-entity' : 'data-character-entity'}="${escapeHtml(actorId)}"`}>` +
      (compact ? (description ? `<p class="character-description">${description}</p>` : '') : `<div class="character-identity"><span class="panel-actor-token" aria-hidden="true">${escapeHtml(actorInitials(name))}</span>` +
      `<div><small>Inspecting</small><h3><strong>${escapeHtml(name)}</strong></h3>${description ? `<span class="character-description">${description}</span>` : ''}` +
      (details.party_role ? `<span class="panel-unit-role">${escapeHtml(panelIdentifierLabel(details.party_role))}</span>` : '') + '</div></div>') +
      (compact ? '' : `<div class="character-vitals">${hpPanelMarkup(entity)}${personalResourceMarkup(entity)}</div>` +
      conditionPanelMarkup(entity)) +
      `<div class="character-loadout"><strong>Ready equipment</strong>${weaponReadinessMarkup(entity || {})}` +
      (entity?.weapon?.id ? `<h4>${escapeHtml(panelIdentifierLabel(entity.weapon.id))}</h4>` : '') +
      (entity?.weapon_sets ? `<div>${escapeHtml(panelIdentifierLabel(entity.weapon_sets.active_set || ''))}: ` +
        `${escapeHtml([entity.weapon_sets[entity.weapon_sets.active_set]?.main_hand, entity.weapon_sets[entity.weapon_sets.active_set]?.off_hand].filter(Boolean).map(panelIdentifierLabel).join(' / '))}</div>` : '') +
      (!compact && entity?.resources ? `<div class="character-resources">${Object.entries(entity.resources).map(([key,value]) =>
        `<span>${escapeHtml(panelIdentifierLabel(key))}<b>${escapeHtml(value)}</b></span>`).join('')}</div>` : '') +
      '</div>' + (compact ? '' : characterActionMarkup(surface, actorId)) + actionReadinessMarkup(entity || {}, actorId) + '</div>' + (!participant ? '<p class="panel-unit-meta">Full character details are unavailable. These are the selected character’s current combat stats.</p>' : '') + values;
  }

  function renderEncounterPanel(data) {
    const combat = data && data.combat && typeof data.combat === 'object' ? data.combat : {};
    if (combat.encounter_active !== true) return '';
    const entities = data && data.entities && typeof data.entities === 'object' ? data.entities : {};
    const participants = data && Array.isArray(data.participants) ? data.participants : [];
    const names = Object.fromEntries([
      ...Object.entries(entities).map(([id, entity]) => [id, entity?.name || id]),
      ...participants.map((entry) => [entry.id, entry.data?.name || entities[entry.id]?.name || entry.id]),
    ]);
    const turnOrderIds = Array.isArray(combat.order) ? combat.order.map((entry) => (
      typeof entry === 'string' ? entry : entry && (entry.entity_id || entry.id)
    )).filter(Boolean) : [];
    const orderedIds = [...new Set((turnOrderIds.length ? turnOrderIds : [
      combat.order_first, combat.order_second, combat.current_actor
    ]).filter(Boolean))];
    const header = `<div class="panel-unit-meta">Round ${escapeHtml(combat.round || 1)} · ` +
      `Current: ${escapeHtml(names[combat.current_actor] || combat.current_actor || '—')}</div>`;
    const units = orderedIds.map((id) => {
      const entity = entities[id] || {};
      const active = id === combat.current_actor ? ' · Current turn' : '';
      return `<div class="panel-unit" data-panel-entity="${escapeHtml(id)}">` +
        `<div class="panel-unit-heading"><strong>${escapeHtml(names[id] || id)}</strong>` +
        `<span class="panel-unit-role">${escapeHtml(entity.creature_kind || 'creature')}${active}</span></div>` +
        hpPanelMarkup(entity) + personalResourceMarkup(entity) + conditionPanelMarkup(entity) + `</div>`;
    }).join('');
    return header + units;
  }

  function renderObjectivesPanel(data) {
    const objectives = Array.isArray(data) ? data : [];
    if (!objectives.length) return '<div class="panel-empty">No current objectives.</div>';
    return objectives.map((objective) => `<div class="panel-unit">` +
      `<div class="panel-objective-heading"><strong>${escapeHtml(objective.title || objective.id)}</strong></div>` +
      `<div class="panel-objective-detail">${escapeHtml(objective.description || '')}</div>` +
      `</div>`).join('');
  }

  function renderPreparedEquipment(participant, entity, surface) {
    const policy = surface?.data?.equipment;
    if (!policy || !Array.isArray(policy.sets) || !Array.isArray(policy.weapons)) return '';
    const inventory = Array.isArray(entity.inventory) ? entity.inventory : [];
    const sets = entity.weapon_sets || {};
    state.equipmentDrafts ||= new Map();
    const rows = policy.sets.map((set) => {
      const key = JSON.stringify([surface.id, participant.id, set.id]);
      const saved = sets[set.id] || {};
      const refs = entity.weapon_copy_refs?.[set.id] || {};
      const savedMain = policy.weapons.find((item) => item.id === saved.main_hand);
      const savedSignature = JSON.stringify([saved.main_hand || '', saved.off_hand || '', refs]);
      const priorDraft = state.equipmentDrafts.get(key);
      const draft = (priorDraft?.savedSignature === savedSignature ? priorDraft : null) || {
        savedSignature,
        main_hand: saved.main_hand || '',
        main_copy: refs.main_hand || 1,
        off_copy: refs.off_hand || (saved.main_hand === saved.off_hand && !savedMain?.versatile && savedMain?.hands !== 2 ? 2 : 1),
        off_hand: saved.off_hand || (set.id === policy.shield_set ? entity.worn_shield?.inventory_item || '' : ''),
      };
      state.equipmentDrafts.set(key, draft);
      const options = policy.weapons.filter((item) => inventory.includes(item.id) &&
        (entity.inventory_quantities?.[item.id] ?? 1) > 0 && item.sets?.includes(set.id)).flatMap((item) => {
          const quantity = entity.inventory_quantities?.[item.id] ?? 1;
          const ledger = entity.inventory_weapon_copies?.[item.id];
          const copies = ledger
            ? Array.from({ length: Math.max(0, ledger.maximum - ledger.minimum + 1) }, (_, i) => ledger.minimum + i)
              .filter((serial) => !ledger.removed.includes(serial))
            : Array.from({ length: quantity }, (_, i) => i + 1);
          return copies.map((serial) => ({ ...item, serial,
            label: quantity > 1 ? `${item.label || panelIdentifierLabel(item.id)} · copy ${serial}` : item.label }));
        });
      const main = policy.weapons.find((item) => item.id === draft.main_hand);
      const shields = set.id === policy.shield_set && entity.worn_shield?.inventory_item
        ? [{ id: entity.worn_shield.inventory_item, label: policy.shield_label || 'Shield' }] : [];
      const select = (slot, label, choices, disabled = false) =>
        `<label class="equipment-slot-label">${escapeHtml(label)}<select class="equipment-slot" ` +
        `data-equipment-owner="${escapeHtml(participant.id)}" data-equipment-set="${escapeHtml(set.id)}" ` +
        `data-equipment-slot="${slot}" aria-label="${escapeHtml(participant.data?.name || participant.id)} ${escapeHtml(set.label)} ${escapeHtml(label)}" ` +
        `${disabled ? 'disabled' : ''}><option value="">Unselected</option>` +
        choices.map((item) => `<option value="${escapeHtml(JSON.stringify([item.id, item.serial || null]))}" ${draft[slot] === item.id && (!item.serial || draft[slot === 'main_hand' ? 'main_copy' : 'off_copy'] === item.serial) ? 'selected' : ''}>` +
          `${escapeHtml(item.label || panelIdentifierLabel(item.id))}</option>`).join('') + '</select></label>';
      const prepareAction = surface.actions?.find((a) => a.id === set.prepare_action);
      const switchAction = surface.actions?.find((a) => a.id === set.switch_action);
      const controls = [[prepareAction, policy.prepare_label || 'Equip'], [switchAction, 'Use this set']].map(([action, label]) => action
        ? `<button type="button" class="btn btn-secondary btn-sm ruleset-panel-action" ` +
          `data-panel-action-id="${escapeHtml(action.id)}" data-panel-actor-id="${escapeHtml(participant.id)}" ` +
          `${panelActionAvailable(surface, action, participant.id) ? '' : 'disabled'}>${escapeHtml(label)}</button>` : '').join('');
      const offControl = main?.hands === 2
        ? `<div class="equipment-slot-label">${escapeHtml(policy.off_hand_label || 'Off hand')}<span class="equipment-occupied">Both hands: ${escapeHtml(main.label)}</span></div>`
        : select('off_hand', policy.off_hand_label || 'Off hand', [...shields, ...options]);
      return `<div class="equipment-set" data-equipment-row="${escapeHtml(set.id)}"><div class="equipment-set-heading">` +
        `<strong>${escapeHtml(set.label)}</strong>${sets.active_set === set.id ? '<span class="equipment-active">Active</span>' : ''}</div>` +
        `<div class="equipment-slots">${select('main_hand', policy.main_hand_label || 'Main hand', options)}${offControl}</div>` +
        `<div class="panel-unit-controls">${controls}</div></div>`;
    });
    return `<section class="prepared-equipment" aria-label="Prepared equipment"><h4>Equipment</h4>` +
      `${sets.active_set ? '' : '<p class="panel-unit-meta">Choose a weapon set before attacking.</p>'}${rows.join('')}${(() => {
        const item = policy.shield_inventory_item;
        if (!item || !inventory.includes(item)) return '';
        const id = entity.worn_shield ? policy.remove_shield_action : policy.wear_shield_action;
        const action = surface.actions?.find((candidate) => candidate.id === id);
        if (!action) return '';
        return `<div class="equipment-shield"><span>${entity.worn_shield ? 'Shield equipped' : 'Shield carried'}</span>` +
          `<button type="button" class="btn btn-secondary btn-sm ruleset-panel-action" data-panel-action-id="${escapeHtml(id)}" ` +
          `data-panel-actor-id="${escapeHtml(participant.id)}" ${panelActionAvailable(surface, action, participant.id) ? '' : 'disabled'}>` +
          `${entity.worn_shield ? 'Remove shield' : 'Equip shield'}</button></div>`;
      })()}` +
      `<p class="panel-unit-meta">${escapeHtml(policy.cost_note || '')}</p></section>`;
  }

  function inventoryEquipmentSelection(surface, action, actorId) {
    const bindings = action.bindings || {};
    const selection = { actor: actorId };
    if (Object.values(bindings).includes('declared_action')) {
      const set = surface.data?.equipment?.sets?.find((item) => item.prepare_action === action.id);
      if (!set) return null;
      const draft = state.equipmentDrafts?.get(JSON.stringify([surface.id, actorId, set.id]));
      if (!draft) return null;
      const main = surface.data.equipment.weapons.find((item) => item.id === draft.main_hand);
      const loadout = { set_name: set.id, main_hand: draft.main_hand, off_hand: main?.hands === 2 ? '' : draft.off_hand };
      if (draft.main_hand) loadout.main_copy = draft.main_copy;
      if (loadout.off_hand && surface.data.equipment.weapons.some((item) => item.id === loadout.off_hand)) loadout.off_copy = draft.off_copy;
      selection.declared_action = [loadout];
    }
    return selection;
  }

  function renderInventoryPanel(data, surface) {
    const participants = data && Array.isArray(data.participants) ? data.participants : [];
    const entities = data && data.entities && typeof data.entities === 'object' ? data.entities : {};
    const selected = characterInspection().id;
    if (!selected) return characterPickerMarkup() + '<div class="panel-empty">Choose a character to inspect equipment.</div>';
    const controlled = state.panelProjection?.player_control?.actors || [];
    const participant = participants.find((item) => item.id === selected);
    const knownEntity = controlled.find((item) => item.id === selected) && entities[selected];
    if (selected && !participant && !knownEntity) return characterPickerMarkup() + '<div class="panel-empty">Selected character equipment is unavailable.</div>';
    const displayed = selected ? [participant || { id: selected, data: { name: knownEntity.name || controlled.find((item) => item.id === selected)?.name || selected } }] : participants;
    const owners = displayed.map((participant) => {
      const entity = entities[participant.id] || {};
      const inventory = Array.isArray(entity.inventory) ? entity.inventory : [];
      const weapon = entity.weapon && typeof entity.weapon === 'object' ? entity.weapon : null;
      const reserves = Array.isArray(entity.reserve_weapons) ? entity.reserve_weapons
        .filter((item) => item && typeof item === 'object') : [];
      if (!inventory.length && !weapon && !reserves.length) return '';
      const name = participant.data && participant.data.name || participant.id;
      // Both slots project the same carried objects that the Ruleset's switch action moves.
      const weapons = [weapon, ...reserves].filter(Boolean).map((carried) => {
        const weaponName = carried.label || carried.name || carried.profile || carried.id || 'Firearm';
        const ammunition = [
          Number.isFinite(carried.magazine?.rounds) ? `Magazine: ${carried.magazine.rounds}${Number.isFinite(carried.magazine.capacity) ? ` / ${carried.magazine.capacity}` : ''}` : '',
          carried.chamber != null ? `Chamber: ${typeof carried.chamber === 'object' ? (carried.chamber.loaded ? 'Loaded' : 'Empty') : panelIdentifierLabel(String(carried.chamber).toLowerCase())}` : '',
          typeof carried.location === 'string' ? panelIdentifierLabel(carried.location.toLowerCase()) : '',
          typeof carried.owner_id === 'string' ? `Owner: ${participants.find((item) => item.id === carried.owner_id)?.data?.name || panelIdentifierLabel(carried.owner_id)}` : '',
        ].filter(Boolean).map(escapeHtml).join(' · ');
        return `<div class="panel-unit-meta"><strong>${escapeHtml(panelIdentifierLabel(weaponName))}</strong></div>` +
          `<div class="panel-unit-meta">${ammunition}</div>`;
      }).join('');
      return `<div class="panel-unit"><div class="panel-unit-heading"><strong>${escapeHtml(name)}</strong></div>` +
        renderPreparedEquipment(participant, entity, surface) + weapons +
        (state.panelProjection?.surfaces.filter((item) => item.renderer === 'unit_list' && item.data?.entities?.[participant.id])
          .map((owner) => characterActionMarkup(owner, participant.id, true)).join('') || '') +
        `<div class="panel-inventory-list">${inventory.map((item) => {
          const label = typeof item === 'string' ? panelIdentifierLabel(item) : item?.name || panelIdentifierLabel(item?.item_id || item?.id || 'Item');
          const count = typeof item === 'object' && Number.isFinite(item?.quantity) ? ` × ${item.quantity}` : '';
          return `<span class="panel-inventory-item">${escapeHtml(label)}${count}</span>`;
        }).join('')}</div></div>`;
    }).filter(Boolean);
    return owners.join('') || '<div class="panel-empty">No recorded equipment yet.</div>';
  }

  // Inventory is a projection of committed holdings; selection never changes campaign state.
  function renderGenericInventoryPanel(surface) {
    const characters = surface.data?.characters || [];
    const chosen = characterInspection().id;
    const character = characters.find((item) => item.id === chosen);
    if (!character) return characterPickerMarkup() + '<div class="panel-empty">Selected character inventory is unavailable.</div>';
    const selector = characterPickerMarkup();
    const rows = character.items.map((item) => `<div class="panel-unit">` +
      `<div class="panel-unit-heading"><strong>${escapeHtml(item.item_id === item.name
        ? panelIdentifierLabel(item.item_id) : item.name)}</strong>` +
      `<span>${escapeHtml(item.quantity)}</span></div>` +
      `<div class="panel-unit-meta">${item.kind === 'currency' ? 'Currency · ' : ''}` +
      `${item.carried ? 'Carried' : 'Held/stored'}: ${escapeHtml(item.holder)}` +
      `${item.owned ? '' : ` · Owner: ${escapeHtml(item.owner)}`}</div></div>`).join('');
    return selector + (rows || '<div class="panel-empty">No recorded items or currency.</div>');
  }

  function renderGenericPanel(data) {
    if (data == null) return '<div class="panel-empty">No current data.</div>';
    if (typeof data === 'string') return `<div class="panel-objective-detail">${escapeHtml(data)}</div>`;
    const rows = Array.isArray(data) ? data.map((value, index) => [String(index + 1), value])
      : Object.entries(data);
    if (!rows.length || rows.every(([, value]) => value == null || value === '')) {
      return '<div class="panel-empty">No current status yet.</div>';
    }
    const displayValue = (value) => {
      if (value == null || value === '') return 'Not available yet';
      if (typeof value === 'boolean') return value ? 'Yes' : 'No';
      if (typeof value === 'object') return JSON.stringify(value);
      return /^[A-Z][A-Z0-9_]+$/.test(String(value)) && String(value).includes('_')
        ? panelIdentifierLabel(String(value).toLowerCase()) : panelIdentifierLabel(value);
    };
    return rows.map(([name, value]) => `<div class="panel-unit">` +
      `<div class="panel-unit-heading"><strong>${escapeHtml(panelIdentifierLabel(name))}</strong></div>` +
      `<div class="panel-unit-meta">${escapeHtml(displayValue(value))}</div></div>`).join('');
  }

  function tacticalDimensions(data, continuous = false) {
    const map = data && data.map;
    const bounds = map && map.bounds;
    const grid = map && map.grid;
    if (!bounds || !Array.isArray(bounds.min) || !Array.isArray(bounds.max) ||
        !grid || !Number.isFinite(grid.cell_size) || grid.cell_size <= 0) return null;
    const width = (bounds.max[0] - bounds.min[0]) / grid.cell_size;
    const height = (bounds.max[1] - bounds.min[1]) / grid.cell_size;
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0 ||
        (continuous ? Math.max(width, height) > 128 : Math.ceil(width) * Math.ceil(height) > 4096)) return null;
    if (!continuous) return { width: Math.ceil(width), height: Math.ceil(height),
      lower: bounds.min, upper: bounds.max, cellSize: grid.cell_size };
    return { width, height, lower: bounds.min, upper: bounds.max, cellSize: grid.cell_size };
  }

  // Refresh ordinary target aim from the current body; discard stale cell-based intent.
  function tacticalSelection(surface) {
    const current = state.tacticalSelections[surface.id];
    const projected = surface.data && surface.data.selection &&
      typeof surface.data.selection === 'object' ? surface.data.selection : {};
    const sameRevision = current && current.map_revision === surface.data.map_revision;
    const selection = sameRevision ? current : {
      ...projected,
      ...(current?.actor ? { actor: current.actor } : {}),
      ...(current?.target ? { target: current.target } : {}),
      ...(current?.zone ? { zone: current.zone } : {}),
      ...(current?.device ? { device: current.device } : {}),
      map_revision: surface.data.map_revision,
    };
    if (!sameRevision) {
      for (const key of ['destination', 'aim_point', 'area', 'orientation', 'path']) {
        delete selection[key];
      }
    }
    if (selection.actor && (!tacticalControlsActor(surface, selection.actor) ||
        !surface.data.entities?.some((entity) => entity.id === selection.actor))) {
      selection.actor = null;
      delete selection.path;
    }
    if (selection.target &&
        !surface.data.entities?.some((entity) => entity.id === selection.target)) {
      selection.target = null;
      delete selection.aim_point;
    }
    if (!sameRevision && selection.target && !current?.destination &&
        (!current || Array.isArray(current.aim_point))) {
      const targetAim = tacticalTargetSelection(surface, selection.target).aim_point;
      if (targetAim) selection.aim_point = targetAim;
    }
    if (selection.zone && !surface.data.zones?.some((zone) => zone.id === selection.zone)) {
      selection.zone = null;
    }
    if (selection.device && !surface.data.entities?.some((entity) => entity.id === selection.device)) {
      selection.device = null;
    }
    state.tacticalSelections[surface.id] = selection;
    return selection;
  }

  function tacticalPositionStyle(position, dimensions, size = 0.72) {
    const x = (position[0] - dimensions.lower[0]) / dimensions.cellSize;
    const y = (position[1] - dimensions.lower[1]) / dimensions.cellSize;
    return `left:${x * 32}px;top:${y * 32}px;width:${size * 32}px;height:${size * 32}px`;
  }

  function tacticalBoxStyle(volume, dimensions) {
    const x = (volume.min[0] - dimensions.lower[0]) / dimensions.cellSize;
    const y = (volume.min[1] - dimensions.lower[1]) / dimensions.cellSize;
    const width = (volume.max[0] - volume.min[0]) / dimensions.cellSize;
    const height = (volume.max[1] - volume.min[1]) / dimensions.cellSize;
    return `left:${x * 32}px;top:${y * 32}px;width:${width * 32}px;height:${height * 32}px`;
  }

  // Edge-barrier ink lies on an actual grid boundary inside its physical volume.
  // Occupied-cell objects keep their authored footprint; no rules or cover-side queries change.
  function tacticalEdgeLineStyle(item, dimensions) {
    if (item.occupies_cells !== false || item.volume?.kind !== 'box') return null;
    const volume = item.volume;
    const vertical = volume.max[1] - volume.min[1] >= volume.max[0] - volume.min[0];
    const axis = vertical ? 0 : 1;
    const low = (volume.min[axis] - dimensions.lower[axis]) / dimensions.cellSize;
    const high = (volume.max[axis] - dimensions.lower[axis]) / dimensions.cellSize;
    const nearest = Math.round((low + high) / 2);
    const coordinate = nearest >= low && nearest <= high ? nearest : (low + high) / 2;
    const offset = (coordinate - low) * 32;
    return vertical ? `left:${offset}px;top:0;height:100%;width:0` :
      `top:${offset}px;left:0;width:100%;height:0`;
  }

  function tacticalOccupiedCellsStyle(volume, dimensions) {
    const minX = Math.max(0, Math.floor(
      (volume.min[0] - dimensions.lower[0]) / dimensions.cellSize
    ));
    const minY = Math.max(0, Math.floor(
      (volume.min[1] - dimensions.lower[1]) / dimensions.cellSize
    ));
    const maxX = Math.min(dimensions.width, Math.ceil(
      (volume.max[0] - dimensions.lower[0]) / dimensions.cellSize
    ));
    const maxY = Math.min(dimensions.height, Math.ceil(
      (volume.max[1] - dimensions.lower[1]) / dimensions.cellSize
    ));
    return `left:${minX * 32}px;top:${minY * 32}px;` +
      `width:${Math.max(0, maxX - minX) * 32}px;height:${Math.max(0, maxY - minY) * 32}px`;
  }

  function tacticalGeometryInfo(item, data) {
    const display = item.display && typeof item.display === 'object' ? item.display : {};
    const label = display.label || item.id;
    const height = item.volume.max[2] - item.volume.min[2];
    const unit = data.map.grid.unit || '';
    const heightText = `${height}${unit ? ` ${unit}` : ''}`;
    const shortHeight = Number(height.toFixed(2)).toString();
    const authoredHeight = typeof display.height_label === 'string' && display.height_label.trim()
      ? display.height_label.trim() : null;
    const grid = data.map.grid;
    const origin = grid.origin || data.map.bounds.min;
    const occupiedWidths = [0, 1].map((axis) => Math.ceil(
      (item.volume.max[axis] - origin[axis]) / grid.cell_size
    ) - Math.floor((item.volume.min[axis] - origin[axis]) / grid.cell_size));
    const reservedCells = occupiedWidths[0] * occupiedWidths[1];
    return {
      label,
      heightText,
      footprintSize: [0, 1].map((axis) =>
        Number((item.volume.max[axis] - item.volume.min[axis]).toPrecision(12))
      ).join(' × ') + (unit ? ` ${unit}` : ''),
      material: typeof display.material_label === 'string' && display.material_label.trim()
        ? display.material_label.trim() : 'Not specified',
      shortHeight: `${shortHeight}${unit ? ` ${unit}` : ''}`,
      authoredHeight,
      occupiesCells: item.occupies_cells !== false,
      footprint: item.occupies_cells !== false ? `${reservedCells} reserved grid ${reservedCells === 1 ? 'cell' : 'cells'}` : 'Physical edge barrier',
      movement: item.movement || 'unspecified',
      visibility: item.visibility || 'unspecified',
    };
  }

  function tacticalPreviewActions(surface) {
    return Array.isArray(surface && surface.actions)
      ? surface.actions.filter((action) => action.map_preview_enabled === true || actionBindsActor(action)).sort((a,b) =>
        Number(['move', 'move-walk'].includes(b.id)) - Number(['move', 'move-walk'].includes(a.id))) : [];
  }

  function tacticalPreviewState(surface, view) {
    const actions = tacticalPreviewActions(surface);
    if (!actions.length) return { actions, action: null, check: null };
    const selected = actions.find((action) => action.id === view.previewActionId) || actions[0];
    view.previewActionId = selected.id;
    return {
      actions,
      action: selected,
      check: state.panelActionChecks.get(panelActionCheckKey(surface, selected)) || null,
    };
  }

  function tacticalCurrentMapPreview(surface, check) {
    const preview = check?.map_preview || null;
    if (!preview || preview.map_revision !== surface.data.map_revision) return null;
    if (surface.data.map.id && preview.map_id !== surface.data.map.id) return null;
    return preview;
  }

  // Explain missing declared map inputs before treating a preview as a failed action.
  function tacticalSelectionGuidance(surface, action) {
    const bindings = action?.bindings;
    if (!bindings || typeof bindings !== 'object') return null;
    const selection = tacticalSelection(surface);
    const missing = new Set(Object.values(bindings).filter((source) =>
      !['path', 'declared_action'].includes(source) && selection[source] == null
    ));
    if (!missing.size) return null;
    const prompts = [];
    if (missing.has('actor')) prompts.push('Choose an acting character');
    if (missing.has('target')) prompts.push('Select a target');
    if (missing.has('zone')) prompts.push('Select a zone');
    if (missing.has('device')) prompts.push('Select a device');
    if (['destination', 'area', 'orientation'].some((source) => missing.has(source)) ||
        (missing.has('aim_point') && !missing.has('target'))) prompts.push(surface.renderer === 'coordinate_map' ? 'Select a map point' : 'Select a map cell');
    const instruction = prompts.length ? prompts.join(' and ') : 'Choose the required selections';
    return `${instruction} to preview ${action.label || action.id}.`;
  }

  function tacticalWorldPoint(point, dimensions) {
    if (!Array.isArray(point) || point.length !== 3 || !point.every(Number.isFinite)) return null;
    return [
      ((point[0] - dimensions.lower[0]) / dimensions.cellSize) * 32,
      ((point[1] - dimensions.lower[1]) / dimensions.cellSize) * 32,
    ];
  }

  function tacticalMapPreviewDetails(mapPreview, dimensions) {
    if (!mapPreview || typeof mapPreview !== 'object') return { svg: '', highlighted: new Set() };
    const highlighted = new Set();
    if (Array.isArray(mapPreview.exposure?.cover_ids)) {
      mapPreview.exposure.cover_ids.forEach((id) => highlighted.add(String(id)));
    }
    const contacts = Array.isArray(mapPreview.ray?.contacts) ? mapPreview.ray.contacts : [];
    contacts.forEach((contact) => {
      if (contact && contact.id !== undefined) highlighted.add(String(contact.id));
    });
    const pathPoints = Array.isArray(mapPreview.path?.points)
      ? mapPreview.path.points.map((point) => tacticalWorldPoint(point, dimensions)).filter(Boolean) : [];
    const rayOrigin = tacticalWorldPoint(mapPreview.ray?.origin, dimensions);
    const rayDestination = tacticalWorldPoint(mapPreview.ray?.destination, dimensions);
    const contactMarkers = contacts.map((contact) => tacticalWorldPoint(contact.point, dimensions))
      .filter(Boolean).map((point) => `<circle class="tactical-preview-contact" ` +
        `cx="${point[0]}" cy="${point[1]}" r="4"></circle>`).join('');
    const zoneMarkers = (Array.isArray(mapPreview.crossed_zones) ? mapPreview.crossed_zones : [])
      .map((zone) => tacticalWorldPoint(zone.point, dimensions)).filter(Boolean)
      .map((point) => `<circle class="tactical-preview-zone" cx="${point[0]}" ` +
        `cy="${point[1]}" r="3"></circle>`).join('');
    return {
      highlighted,
      svg: (pathPoints.length > 1
        ? `<polyline class="tactical-preview-route" points="${pathPoints.map(
          (point) => `${point[0]},${point[1]}`
        ).join(' ')}"></polyline>` : '') +
        (rayOrigin && rayDestination ? `<line class="tactical-preview-ray" ` +
          `x1="${rayOrigin[0]}" y1="${rayOrigin[1]}" x2="${rayDestination[0]}" ` +
          `y2="${rayDestination[1]}"></line>` : '') + contactMarkers + zoneMarkers,
    };
  }

  // Explain public visibility and ray contacts without interpreting projectile penetration.
  function tacticalFiringExplanation(preview, data) {
    const lines = [];
    const publicLabel = (kind, id) => {
      const items = kind === 'geometry' ? data.geometry : kind === 'entity' ? data.entities : [];
      const item = (items || []).find((entry) => entry.id === id);
      if (!item) return null;
      return typeof item.display?.label === 'string' && item.display.label.trim()
        ? item.display.label.trim() : kind === 'geometry' ? 'Barrier' : 'Unit';
    };
    const names = (labels) => labels.slice(0, 4).join(' → ') +
      (labels.length > 4 ? ` (+${labels.length - 4} more)` : '');
    const fraction = preview.exposure?.fraction;
    if (Number.isFinite(fraction)) {
      lines.push(fraction <= 0 ? 'Target hidden from this angle.' :
        fraction >= 1 ? 'Target fully visible from this angle.' :
        'Target partly visible from this angle.');
      const cover = fraction < 1 && Array.isArray(preview.exposure.cover_ids)
        ? preview.exposure.cover_ids.map((id) => publicLabel('geometry', id)).filter(Boolean) : [];
      if (cover.length) lines.push(`Obscuring cover: ${names(cover)}.`);
    }
    const contacts = Array.isArray(preview.ray?.contacts)
      ? preview.ray.contacts.map((contact) => publicLabel(contact.kind, contact.id)).filter(Boolean) : [];
    if (contacts.length) lines.push(`Firing line meets: ${names(contacts)}.`);
    return lines.map((line) => `<div>${escapeHtml(line)}</div>`).join('');
  }

  function tacticalPreviewMarkup(surface, view) {
    const selected = tacticalPreviewState(surface, view);
    if (!selected.action) return 'No Ruleset map preview is available.';
    const guidance = tacticalSelectionGuidance(surface, selected.action);
    if (guidance) return `<div class="tactical-preview-guidance">${escapeHtml(guidance)}</div>`;
    if (!selected.check || selected.check.pending) {
      return `Checking ${escapeHtml(selected.action.label || selected.action.id)} preview…`;
    }
    const preview = selected.check.map_preview;
    const unavailable = !selected.check.available
      ? `<div class="tactical-preview-unavailable">${escapeHtml(
        panelPlayerReason(selected.check.reason) || 'This action is unavailable.'
      )}</div>` : '';
    if (!preview) return unavailable || rulesetCardMarkup(selected.check.preview, '') ||
      'Cost preview unavailable. Committing spends the action’s normal resources.';
    if (preview.map_revision !== surface.data.map_revision ||
        (surface.data.map.id && preview.map_id !== surface.data.map.id)) {
      return 'This map preview is out of date. Change the selection to check again.';
    }
    const parts = [];
    if (preview.path) {
      parts.push(preview.path.truncated ? 'Route search reached its limit' :
        preview.path.found === false ? 'No route found' :
        `Route cost ${Number(preview.path.cost.toFixed(2))}`);
    }
    if (preview.ray) parts.push(`Objects along firing line: ${Array.isArray(preview.ray.contacts) ? preview.ray.contacts.length : 0}`);
    if (preview.exposure && Number.isFinite(preview.exposure.fraction)) {
      parts.push(`Exposed silhouette: ${Math.round(preview.exposure.fraction * 100)}%`);
    }
    if (Array.isArray(preview.crossed_zones)) parts.push(`${preview.crossed_zones.length} crossed zones`);
    const card = rulesetCardMarkup(selected.check.preview, '');
    return unavailable + (card || '') +
      `<div>${parts.map(escapeHtml).join(' · ') || 'Map preview available.'}</div>` +
      tacticalFiringExplanation(preview, surface.data) +
      `<div>${escapeHtml(preview.notice || '')}</div>`;
  }

  // Read only the map's public posture and physical height; rulesets own their meaning.
  function tacticalPostureText(entity, data) {
    if (!entity) return '';
    const stance = typeof entity.display?.stance === 'string' ? entity.display.stance : '';
    const height = Number.isFinite(entity.height)
      ? `${Number(entity.height.toFixed(2))}${data?.map?.grid?.unit ? ` ${data.map.grid.unit}` : ''} high` : '';
    return [stance, height].filter(Boolean).join(', ');
  }

  // Map only public display states to fixed visual classes.
  function tacticalZoneStateClass(zone) {
    const status = typeof zone?.display?.state === 'string'
      ? zone.display.state.trim().toLowerCase() : '';
    if (status === 'burning') return ' tactical-zone-burning';
    if (status === 'preview' || status === 'pending preview' ||
        status === 'pending; spell has not resolved') return ' tactical-zone-preview';
    return '';
  }

  // Project the admitted public query volume onto XY; no recipient or mechanical queries run here.
  function tacticalZoneDisplayPath(shape, dimensions) {
    const unit = vector => {
      if (!Array.isArray(vector) || vector.length !== 3 || !vector.every(Number.isFinite)) return null;
      const norm = Math.hypot(...vector);
      return norm > 0 && Number.isFinite(norm) ? vector.map(value => value / norm) : null;
    };
    const cross = (a, b) => [a[1] * b[2] - a[2] * b[1],
      a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const point = value => tacticalWorldPoint(value, dimensions);
    const pathPoint = value => `${value[0]},${value[1]}`;
    const positive = value => Number.isFinite(value) && value > 0;
    const axis = unit(shape?.direction);
    if (!axis || !positive(shape.length)) return '';
    if (shape.kind === 'prism') {
      const origin = point(shape.origin);
      const reference = unit(shape.up);
      const side = reference && unit(cross(reference, axis));
      const up = side && unit(cross(axis, side));
      if (!origin || !up || !positive(shape.width) || !positive(shape.height)) return '';
      const corners = [];
      for (const length of [0, shape.length]) {
        for (const width of [-shape.width / 2, shape.width / 2]) {
          for (const height of [-shape.height / 2, shape.height / 2]) {
            const corner = point(shape.origin.map((value, i) =>
              value + axis[i] * length + side[i] * width + up[i] * height));
            if (!corner) return '';
            corners.push(corner);
          }
        }
      }
      const points = corners.sort((a, b) => a[0] - b[0] || a[1] - b[1])
        .filter((value, i, rows) => !i || value[0] !== rows[i - 1][0] || value[1] !== rows[i - 1][1]);
      const turn = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) -
        (b[1] - a[1]) * (c[0] - a[0]);
      const half = rows => {
        const hull = [];
        for (const value of rows) {
          while (hull.length > 1 && turn(hull[hull.length - 2], hull[hull.length - 1], value) <= 0) hull.pop();
          hull.push(value);
        }
        return hull.slice(0, -1);
      };
      const hull = [...half(points), ...half([...points].reverse())];
      return hull.length > 2 ? `M${hull.map(pathPoint).join(' L')} Z` : '';
    }
    if (shape.kind !== 'cone' || !positive(shape.radius)) return '';
    const apex = point(shape.apex);
    if (!apex) return '';
    const base = point(shape.apex.map((value, i) => value + axis[i] * shape.length));
    if (!base) return '';
    const horizontal = Math.hypot(axis[0], axis[1]);
    const u = horizontal ? [axis[0] / horizontal, axis[1] / horizontal] : [1, 0];
    const v = [-u[1], u[0]];
    const radius = shape.radius * 32 / dimensions.cellSize;
    const minor = radius * Math.abs(axis[2]);
    const distance = shape.length * horizontal * 32 / dimensions.cellSize;
    const rotate = Math.atan2(u[1], u[0]) * 180 / Math.PI;
    const at = (x, y) => [base[0] + u[0] * x + v[0] * y, base[1] + u[1] * x + v[1] * y];
    if (![radius, minor, distance].every(Number.isFinite)) return '';
    if (minor === 0) {
      return `M${pathPoint(apex)} L${pathPoint(at(0, radius))} L${pathPoint(at(0, -radius))} Z`;
    }
    if (distance <= minor) {
      const right = pathPoint(at(minor, 0));
      const left = pathPoint(at(-minor, 0));
      return `M${right} A${minor},${radius} ${rotate} 1 1 ${left} ` +
        `A${minor},${radius} ${rotate} 1 1 ${right} Z`;
    }
    const x = -minor * (minor / distance);
    const y = radius * Math.sqrt(Math.max(0, 1 - (minor / distance) ** 2));
    return `M${pathPoint(apex)} L${pathPoint(at(x, y))} ` +
      `A${minor},${radius} ${rotate} 1 0 ${pathPoint(at(x, -y))} Z`;
  }

  // Draw declared display geometry in the map's coordinate frame, clipped and pass-through.
  function tacticalZoneDisplayMarkup(zone, dimensions) {
    const path = tacticalZoneDisplayPath(zone?.display?.display_geometry, dimensions);
    if (!path) return '';
    const stateClass = tacticalZoneStateClass(zone);
    const color = stateClass.includes('preview') ? '#a69bea' :
      stateClass.includes('burning') ? '#ed9d45' : '#66cc99';
    const label = [zone.display.label, zone.display.state].filter(Boolean).join(' · ');
    return `<svg class="tactical-map-preview-overlay tactical-zone-display-overlay" ` +
      `viewBox="0 0 ${dimensions.width * 32} ${dimensions.height * 32}" role="img" ` +
      `aria-label="${escapeHtml(label)}" style="z-index:3;overflow:hidden;pointer-events:none">` +
      `<path class="tactical-zone-exact${stateClass}" d="${path}" fill="${color}" ` +
      `fill-opacity="0.13" stroke="${color}" stroke-width="2" ` +
      `stroke-dasharray="5 4" vector-effect="non-scaling-stroke"></path></svg>`;
  }

  // Describe public map zones once per presentation, keeping cell overlays pass-through.
  function tacticalZoneLegend(zones) {
    const entries = new Map();
    (Array.isArray(zones) ? zones : []).forEach((zone) => {
      if (!zone.shape || !['box', 'cylinder'].includes(zone.shape.kind)) return;
      const display = zone.display;
      if (!display || typeof display.label !== 'string' || !display.label.trim()) return;
      const details = [display.state, display.height_label].filter(
        (value) => typeof value === 'string' && value.trim()
      );
      const key = JSON.stringify([display.label, ...details]);
      entries.set(key, `<li class="tactical-zone-key${tacticalZoneStateClass(zone)}">` +
        `<strong>${escapeHtml(display.label)}</strong>` +
        (details.length ? `<span>${details.map(escapeHtml).join(' · ')}</span>` : '') + '</li>');
    });
    return entries.size ? '<div class="tactical-zone-legend" aria-label="Map effects">' +
      '<span>Map effects</span><ul>' + Array.from(entries.values()).join('') + '</ul></div>' : '';
  }

  function renderTacticalGridPanel(data, surfaceId) {
    if (typeof data?.unavailable_reason === 'string') {
      return `<div class="panel-empty" role="status">${escapeHtml(data.unavailable_reason)}</div>`;
    }
    const continuous = state.panelProjection?.surfaces.find((item) => item.id === surfaceId)?.renderer === 'coordinate_map';
    const dimensions = tacticalDimensions(data, continuous);
    if (!dimensions) return '<div class="panel-empty">Tactical map is unavailable or too large.</div>';
    const surface = state.panelProjection && state.panelProjection.surfaces.find(
      (entry) => entry.id === surfaceId
    );
    const selection = surface ? tacticalSelection(surface) : { map_revision: data.map_revision };
    const view = state.tacticalViews[surfaceId] || {
      zoom: 1, scrollLeft: 0, scrollTop: 0, coverInfo: false, selectedGeometry: null, initialFocus: true,
    };
    state.tacticalViews[surfaceId] = view;
    view.zoom = Number.isFinite(view.zoom) ? Math.max(0.5, Math.min(2, view.zoom)) : 1;
    const previewState = surface ? tacticalPreviewState(surface, view) : {
      actions: [], action: null, check: null,
    };
    const currentMapPreview = surface ? tacticalCurrentMapPreview(surface, previewState.check) : null;
    const previewDetails = tacticalMapPreviewDetails(currentMapPreview, dimensions);
    const cells = [];
    for (let y = 0; !continuous && y < dimensions.height; y += 1) {
      for (let x = 0; x < dimensions.width; x += 1) {
        const selected = Array.isArray(selection.destination) &&
          selection.destination[0] === x && selection.destination[1] === y;
        cells.push(`<button type="button" class="tactical-cell${selected ? ' selected' : ''}" ` +
          `data-tactical-cell="${x},${y}" data-cell-x="${x}" data-cell-y="${y}" ` +
          `aria-label="Cell ${x + 1}, ${y + 1}" aria-pressed="${selected}"></button>`);
      }
    }
    const terrain = Array.isArray(data.terrain) ? data.terrain.map((item) => {
      const cell = item.cell;
      return `<span class="tactical-terrain" aria-hidden="true" style="left:${cell[0] * 32}px;` +
        `top:${cell[1] * 32}px" title="Terrain cost ${escapeHtml(item.cost)}"></span>`;
    }).join('') : '';
    const geometry = Array.isArray(data.geometry) ? data.geometry.map((item) => {
      if (!item.active || !item.volume || item.volume.kind !== 'box') return '';
      const kind = String(item.display && item.display.kind || 'barrier')
        .toLowerCase().replace(/[^a-z0-9_-]/g, '');
      const info = tacticalGeometryInfo(item, data);
      const selected = view.selectedGeometry === item.id;
      const heightDescription = info.authoredHeight
        ? `${info.authoredHeight}, ${info.heightText} high` : `${info.heightText} high`;
      const occupiedCells = !continuous && info.occupiesCells
        ? `<span class="tactical-occupied-cells" aria-hidden="true" ` +
          `style="${tacticalOccupiedCellsStyle(item.volume, dimensions)}"></span>` : '';
      const previewHighlight = previewDetails.highlighted.has(String(item.id));
      return occupiedCells + `<button type="button" class="tactical-geometry tactical-${escapeHtml(kind)} ` +
        `${info.occupiesCells ? 'occupies-cells' : 'edge-barrier'}${selected ? ' selected' : ''}` +
        `${previewHighlight ? ' preview-highlight' : ''}" ` +
        `aria-label="${escapeHtml(info.label)}, ${escapeHtml(heightDescription)}, ` +
        `${info.occupiesCells ? 'occupies grid cells' : 'physical edge barrier'}" ` +
        `aria-pressed="${selected}" title="${escapeHtml(info.label)} · ` +
        `${escapeHtml(heightDescription)}" ` +
        `data-tactical-geometry="${escapeHtml(item.id)}" ` +
        `style="${tacticalBoxStyle(item.volume, dimensions)}">` +
        (!continuous && !info.occupiesCells ? `<span class="tactical-edge-line" aria-hidden="true" style="${tacticalEdgeLineStyle(item, dimensions)}"></span>` : '') +
        `<span class="tactical-height-indicator" aria-hidden="true">${escapeHtml(info.shortHeight)}</span>` +
        `<span class="tactical-cover-label" aria-hidden="true">${escapeHtml(info.label)} · ` +
        `${escapeHtml(info.authoredHeight || info.heightText)}</span></button>`;
    }).join('') : '';
    const zones = Array.isArray(data.zones) ? data.zones.map((item) => {
      if (!item.shape) return '';
      if (continuous && item.display?.display_geometry) {
        const exact = tacticalZoneDisplayMarkup(item, dimensions);
        if (exact) return exact;
      }
      if (item.shape.kind === 'cylinder' && continuous) {
        const shape = item.shape;
        const box = { min: [shape.center[0] - shape.radius, shape.center[1] - shape.radius],
          max: [shape.center[0] + shape.radius, shape.center[1] + shape.radius] };
        return `<span class="tactical-zone coordinate-round${tacticalZoneStateClass(item)}" ` +
          `aria-label="${escapeHtml([item.display.label, item.display.state].filter(Boolean).join(' · '))}" ` +
          `title="${escapeHtml(item.display.label)} · ${escapeHtml(item.display.state || '')} · height ${escapeHtml(shape.height)}" ` +
          `style="${tacticalBoxStyle(box, dimensions)}"></span>`;
      }
      if (item.shape.kind !== 'box') return '';
      return `<span class="tactical-zone${tacticalZoneStateClass(item)}" ` +
        `aria-label="${escapeHtml([item.display.label, item.display.state].filter(Boolean).join(' · '))}" ` +
        `style="${tacticalBoxStyle(item.shape, dimensions)}"></span>`;
    }).join('') : '';
    const objectives = Array.isArray(data.objectives) ? data.objectives.map((item) =>
      `<span class="tactical-objective" title="${escapeHtml(item.label)}" ` +
      `style="${tacticalPositionStyle(item.position, dimensions, 0.5)}">◆</span>`
    ).join('') : '';
    const entities = Array.isArray(data.entities) ? data.entities.map((item) => {
      const actor = selection.actor === item.id;
      const target = selection.target === item.id;
      const crossFocused = state.crossSurfaceFocus &&
        state.crossSurfaceFocus.surfaceId === surfaceId &&
        state.crossSurfaceFocus.entityId === item.id;
      const label = item.display && item.display.label || item.id;
      const posture = [tacticalPostureText(item, data), continuous
        ? `at ${item.position.join(', ')} ${data.map.grid.unit}` : ''].filter(Boolean).join('; ');
      return `<button type="button" class="tactical-entity${actor ? ' actor' : ''}` +
        `${target ? ' target' : ''}${crossFocused ? ' cross-focused' : ''}" ` +
        `data-tactical-entity="${escapeHtml(item.id)}" ` +
        `aria-label="${escapeHtml(label)}${posture ? `, ${escapeHtml(posture)}` : ''}" ` +
        `title="${escapeHtml(label)}${posture ? `, ${escapeHtml(posture)}` : ''}" ` +
        `aria-pressed="${actor || target}" style="${tacticalPositionStyle(
          item.position, dimensions
        )}${continuous ? coordinateBodyStyle(item, dimensions) : ''}"><span class="map-token-label" aria-hidden="true">${escapeHtml(actorInitials(label))}</span>` +
        (continuous && item.position[2] !== 0 ? `<span class="coordinate-height-label" aria-hidden="true">` +
          `${escapeHtml(item.position[2])} ${escapeHtml(data.map.grid.unit)}</span>` : '') + '</button>';
    }).join('') : '';
    const entityLabel = (id) => data.entities.find((entity) => entity.id === id)?.display?.label || id;
    const selectedEntityLabel = (id) => {
      const entity = data.entities.find((item) => item.id === id);
      const posture = [tacticalPostureText(entity, data), continuous && entity
        ? `at ${entity.position.join(', ')} ${data.map.grid.unit}` : ''].filter(Boolean).join('; ');
      return `${entity?.display?.label || id}${posture ? ` (${posture})` : ''}`;
    };
    const actorOptions = data.entities.filter((entity) => tacticalControlsActor(surface, entity.id));
    const usesZone = Object.values(previewState.action?.bindings || {}).includes('zone');
    const zonePicker = usesZone ? `<label>Zone <select data-tactical-zone aria-label="Prepared fire zone">` +
      `<option value="">Select zone</option>` + (data.zones || []).map((zone) =>
        `<option value="${escapeHtml(zone.id)}" ${zone.id === selection.zone ? 'selected' : ''}>` +
        `${escapeHtml(zone.display?.label || zone.id)}</option>`).join('') + `</select></label>` : '';
    const usesDevice = Object.values(previewState.action?.bindings || {}).includes('device');
    const devicePicker = usesDevice ? `<label>Device <select data-tactical-device aria-label="Action device">` +
      `<option value="">Select device</option>` + data.entities.map((entity) =>
        `<option value="${escapeHtml(entity.id)}" ${entity.id === selection.device ? 'selected' : ''}>` +
        `${escapeHtml(entityLabel(entity.id))}</option>`).join('') + `</select></label>` : '';
    const scope = state.panelProjection && state.panelProjection.player_control;
    const selectedGeometry = Array.isArray(data.geometry) && data.geometry.find(
      (item) => item.active && item.id === view.selectedGeometry && item.volume && item.volume.kind === 'box'
    );
    const geometryInspection = selectedGeometry ? (() => {
      const info = tacticalGeometryInfo(selectedGeometry, data);
      return `<div class="tactical-geometry-inspection" aria-live="polite">` +
        `<strong>${escapeHtml(info.label)}</strong><span>Height: ${escapeHtml(info.heightText)}` +
        `${info.authoredHeight ? ` (${escapeHtml(info.authoredHeight)})` : ''}</span>` +
        `<span>Footprint: ${escapeHtml(info.footprintSize)}</span>` +
        `<span>Material: ${escapeHtml(info.material)}</span>` +
        `<span>${escapeHtml(continuous ? 'Physical bounds shown on the map' : info.footprint)}</span>` +
        `<span>Movement: ${escapeHtml(info.movement)} · Visibility: ` +
        `${escapeHtml(info.visibility)}</span></div>`;
    })() : `<div class="tactical-geometry-inspection" aria-live="polite">` +
      `Select map geometry to inspect its physical height${continuous ? '' : ' and grid occupancy'}.</div>`;
    const selectedText = selection.target ? `Target: ${selectedEntityLabel(selection.target)}` : '';
    const networkReturns = Array.isArray(data.entities) ? data.entities.flatMap((entity) =>
      Array.isArray(entity.network_bindings) ? entity.network_bindings.map((binding) =>
        `<button type="button" class="btn btn-secondary btn-sm network-return" ` +
        `data-network-return-surface="${escapeHtml(binding.surface_id)}" ` +
        `data-network-return-node="${escapeHtml(binding.node_id)}" ` +
        `aria-label="Open linked network node for ${escapeHtml(
          entity.display && entity.display.label || entity.id
        )}">Network: ${escapeHtml(entity.display && entity.display.label || entity.id)}</button>`
      ) : []
    ).join('') : '';
    const secondaryChoices = `<label>Acting character <select data-tactical-actor aria-label="Choose acting character" ` +
      `${actorOptions.length ? '' : 'disabled'}><option value="">Choose character</option>` +
      actorOptions.map((entity) => `<option value="${escapeHtml(entity.id)}" ` +
        `${entity.id === selection.actor ? 'selected' : ''}>${escapeHtml(entityLabel(entity.id))}</option>`).join('') +
      `</select></label><label>Target <select data-tactical-target aria-label="Choose target">` +
      `<option value="">Choose target</option>` + data.entities.map((entity) =>
        `<option value="${escapeHtml(entity.id)}" ${entity.id === selection.target ? 'selected' : ''}>` +
        `${escapeHtml(entityLabel(entity.id))}</option>`).join('') + '</select></label>';

    return (surface ? selectedMapActorMarkup(surface, selection) : '') +
      `<div class="tactical-toolbar" role="toolbar" aria-label="Tactical map controls">` +
      `<button type="button" class="btn btn-secondary btn-sm" data-tactical-zoom="out" ` +
      `aria-label="Zoom out">−</button><span class="map-zoom-level" aria-label="Map zoom">${Math.round(view.zoom * 100)}%</span>` +
      `<button type="button" class="btn btn-secondary btn-sm" data-tactical-zoom="in" ` +
      `aria-label="Zoom in">+</button>` +
      `<button type="button" class="btn btn-secondary btn-sm" data-tactical-center ` +
        `${selection.actor ? '' : 'disabled'} aria-label="Focus on ${escapeHtml(selection.actor ? entityLabel(selection.actor) : 'selected character')}">Focus</button>` +
      `<button type="button" class="btn btn-secondary btn-sm" data-tactical-cover-info ` +
      `aria-pressed="${Boolean(view.coverInfo)}">Cover info</button>` +
      (continuous ? '' : shotControlsMarkup()) +
      '</div>' +
      (usesZone || usesDevice ? `<div class="map-action-inputs">${zonePicker}${devicePicker}</div>` : '') +
      (selectedText ? `<div class="tactical-selection-summary" aria-live="polite">${escapeHtml('Target: ' + entityLabel(selection.target))}</div>` : '') +
      `<div class="tactical-scroll" tabindex="0" ` +
       `aria-label="${continuous ? 'Coordinate map; click to select a point, drag to pan' : 'Tactical grid; drag to pan, arrow keys move cell focus'}">` +
       `<div class="tactical-world${continuous ? ' coordinate-world' : ''}${view.coverInfo ? ' show-cover-info' : ''}" ` +
      `style="width:${dimensions.width * 32}px;` +
      `height:${dimensions.height * 32}px;--tactical-zoom:${view.zoom}">` +
       cells.join('') + terrain + zones + geometry + objectives + entities +
       (continuous && Array.isArray(selection.aim_point) ? `<span class="coordinate-point-marker" ` +
         `style="${tacticalPositionStyle(selection.aim_point, dimensions, 0.35)}" aria-hidden="true">+</span>` : '') +
      `<svg class="tactical-map-preview-overlay" data-tactical-map-preview-overlay ` +
      `viewBox="0 0 ${dimensions.width * 32} ${dimensions.height * 32}" ` +
      `aria-hidden="true">${previewDetails.svg}</svg>` +
      `<svg class="tactical-shot-overlay" data-shot-overlay ` +
      `viewBox="0 0 ${dimensions.width * 32} ${dimensions.height * 32}" ` +
      `aria-hidden="true"></svg></div></div>` +
       `<details class="map-detail-controls map-choice-fallback"><summary>Map tools</summary><span class="map-revision">Saved map revision ${escapeHtml(data.map_revision)}</span>${secondaryChoices}` +
      (continuous ? coordinatePointControls(data, selection) : '') + geometryInspection + tacticalZoneLegend(data.zones) +
      (networkReturns ? `<div class="network-return-list">${networkReturns}</div>` : '') + '</details>' +
      (continuous && !previewState.actions.length ? '' :
        `<div class="tactical-preview" data-tactical-preview aria-live="polite">` +
        (surface ? tacticalPreviewMarkup(surface, view) : 'No Ruleset map preview is available.') +
        `</div>`);
  }

  // Render the body's actual footprint around its authoritative position.
  function coordinateBodyStyle(entity, dimensions) {
    const footprint = entity.footprint;
    const scale = 32 / dimensions.cellSize;
    const width = footprint.kind === 'circle' ? footprint.radius * 2 : footprint.half_extents[0] * 2;
    const depth = footprint.kind === 'circle' ? footprint.radius * 2 : footprint.half_extents[1] * 2;
    return `;width:${width * scale}px;height:${depth * scale}px;` +
      `border-radius:${footprint.kind === 'circle' ? '50%' : '3px'};transform:translate(-50%, -50%)`;
  }

  // Coordinate fields provide precise and keyboard-accessible 3D intent without grid snapping.
  function coordinatePointControls(data, selection) {
    const actor = data.entities.find((item) => item.id === selection.actor);
    const point = selection.aim_point || actor?.position || data.map.bounds.min;
    return `<fieldset class="coordinate-point-controls"><legend>Selected point (${escapeHtml(data.map.grid.unit)})</legend>` +
      ['X', 'Y', 'Height'].map((label, index) => `<label>${label}<input type="number" step="any" ` +
        `data-coordinate-axis="${index}" aria-label="Map point ${label}" ` +
        `min="${data.map.bounds.min[index]}" max="${data.map.bounds.max[index]}" ` +
        `value="${escapeHtml(point[index])}"></label>`).join('') + '</fieldset>';
  }

  function updateTacticalSelection(surface, patch) {
    const selection = tacticalSelection(surface);
    Object.assign(selection, patch);
    gamePanels().choose(surface.id, patch);
  }

  // Use the visible body's center for ordinary targeting; a later cell click can aim elsewhere.
  function tacticalTargetSelection(surface, targetId) {
    const patch = { target: targetId || null, aim_point: null, destination: null,
      area: null, orientation: null, path: null };
    if (!targetId) return patch;
    const target = surface.data.entities.find((entity) => entity.id === targetId);
    if (!target || !Array.isArray(target.position) || target.position.length !== 3 ||
        !target.position.every(Number.isFinite) || !Number.isFinite(target.height) ||
        target.height <= 0) return patch;
    patch.aim_point = [target.position[0], target.position[1], target.position[2] + target.height / 2];
    return patch;
  }

  // Reserve the last preview's space while refreshing so layout cannot clamp the map camera.
  function captureTacticalPreviewHeights(container, nextProjection) {
    const heights = new Map();
    if (panelRenderContext?.campaignId !== nextProjection?.campaign_id ||
        panelRenderContext?.sessionId !== state.activeSessionId) return heights;
    container.querySelectorAll('.ruleset-map-panel').forEach((panel) => {
      const id = panel.dataset.panelId;
      const previous = state.panelProjection?.surfaces.find((surface) => surface.id === id);
      const next = nextProjection?.surfaces.find((surface) => surface.id === id);
      const preview = panel.querySelector('[data-tactical-preview]');
      if (preview && ['tactical_grid', 'coordinate_map'].includes(next?.renderer) &&
          previous?.data?.map?.id === next.data?.map?.id) {
        heights.set(id, preview.getBoundingClientRect().height);
      }
    });
    return heights;
  }

  function tacticalPreviewPending(surface, selected) {
    return selected.action && !tacticalSelectionGuidance(surface, selected.action) &&
      (!selected.check || selected.check.pending);
  }

  function updateTacticalPreviewDisplay(panel, surface) {
    const dimensions = tacticalDimensions(surface.data, surface.renderer === 'coordinate_map');
    const view = state.tacticalViews[surface.id];
    if (!dimensions || !view) return;
    const selected = tacticalPreviewState(surface, view);
    const mapPreview = tacticalCurrentMapPreview(surface, selected.check);
    const details = tacticalMapPreviewDetails(mapPreview, dimensions);
    const previewHost = panel.querySelector('[data-tactical-preview]');
    if (previewHost) {
      previewHost.style.minHeight = tacticalPreviewPending(surface, selected)
        ? `${previewHost.getBoundingClientRect().height}px` : '';
      previewHost.innerHTML = tacticalPreviewMarkup(surface, view);
    }
    const overlay = panel.querySelector('[data-tactical-map-preview-overlay]');
    if (overlay) overlay.innerHTML = details.svg;
    panel.querySelectorAll('[data-tactical-geometry]').forEach((button) => {
      button.classList.toggle('preview-highlight',
        details.highlighted.has(String(button.dataset.tacticalGeometry)));
    });
  }

  // A new map opens on public bodies instead of an empty corner of large bounds.
  // Restored views and subsequent refreshes retain the player's chosen camera.
  function tacticalInitialScroll(data, dimensions, view, viewport) {
    const positions = (Array.isArray(data.entities) ? data.entities : []).filter((entity) =>
      Array.isArray(entity.position) && entity.position.length >= 2 &&
      entity.position.slice(0, 2).every((value, axis) => Number.isFinite(value) &&
        value >= dimensions.lower[axis] && value <= dimensions.upper[axis]));
    if (!positions.length || viewport.width <= 0 || viewport.height <= 0) return null;
    const scale = 32 / dimensions.cellSize * view.zoom;
    const points = positions.map((entity) => entity.position);
    const lower = [0, 1].map((axis) => Math.min(...points.map((point) => point[axis])));
    const upper = [0, 1].map((axis) => Math.max(...points.map((point) => point[axis])));
    const nearby = (upper[0] - lower[0]) * scale + 32 <= viewport.width &&
      (upper[1] - lower[1]) * scale + 32 <= viewport.height;
    const focus = nearby ? lower.map((value, axis) => (value + upper[axis]) / 2) :
      (positions.find((entity) => entity.id === data.selection?.actor) || positions[0]).position;
    return { left: (focus[0] - dimensions.lower[0]) * scale - viewport.width / 2,
      top: (focus[1] - dimensions.lower[1]) * scale - viewport.height / 2 };
  }

  function wireTacticalGrid(container, projection, previewHeights = new Map()) {
    container.querySelectorAll('[data-panel-id]').forEach((panel) => {
      const surface = projection.surfaces.find((entry) => entry.id === panel.dataset.panelId);
      if (!surface || !['tactical_grid', 'coordinate_map'].includes(surface.renderer)) return;
      const continuous = surface.renderer === 'coordinate_map';
      const dimensions = tacticalDimensions(surface.data, continuous);
      if (!dimensions) return;
      const scroll = panel.querySelector('.tactical-scroll');
      const view = state.tacticalViews[surface.id] || {
        zoom: 1, scrollLeft: 0, scrollTop: 0, initialFocus: true,
      };
      state.tacticalViews[surface.id] = view;
      const preview = panel.querySelector('[data-tactical-preview]');
      if (preview && previewHeights.has(surface.id) &&
          tacticalPreviewPending(surface, tacticalPreviewState(surface, view))) {
        preview.style.minHeight = `${previewHeights.get(surface.id)}px`;
      }
      const maximumScrollLeft = () => Math.max(0, scroll.scrollWidth - scroll.clientWidth);
      const maximumScrollTop = () => Math.max(0, scroll.scrollHeight - scroll.clientHeight);
      const retainScroll = () => {
        view.initialFocus = false;
        view.scrollLeft = Math.max(0, Math.min(maximumScrollLeft(), scroll.scrollLeft));
        view.scrollTop = Math.max(0, Math.min(maximumScrollTop(), scroll.scrollTop));
        gamePanels().saveView(surface, view);
      };
      const initial = view.initialFocus ? tacticalInitialScroll(surface.data, dimensions, view,
        { width: scroll.clientWidth, height: scroll.clientHeight }) : null;
      scroll.scrollLeft = Math.max(0, Math.min(maximumScrollLeft(), initial?.left ?? view.scrollLeft ?? 0));
      scroll.scrollTop = Math.max(0, Math.min(maximumScrollTop(), initial?.top ?? view.scrollTop ?? 0));
      if (view.initialFocus && initial) {
        view.initialFocus = false;
        retainScroll();
      }
      scroll.addEventListener('scroll', retainScroll, { passive: true });
      panel.querySelector('[data-tactical-center]')?.addEventListener('click', () => {
        const actor = surface.data.entities.find((item) => item.id === tacticalSelection(surface).actor);
        if (!actor) return;
        const scale = 32 / dimensions.cellSize * view.zoom;
        scroll.scrollLeft = Math.max(0, Math.min(maximumScrollLeft(),
          (actor.position[0] - dimensions.lower[0]) * scale - scroll.clientWidth / 2));
        scroll.scrollTop = Math.max(0, Math.min(maximumScrollTop(),
          (actor.position[1] - dimensions.lower[1]) * scale - scroll.clientHeight / 2));
        retainScroll();
      });

      let pan = null;
      let suppressSelectionUntil = 0;
      scroll.addEventListener('pointerdown', (event) => {
        if (event.button !== 0 || !event.isPrimary) return;
        pan = {
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          scrollLeft: scroll.scrollLeft,
          scrollTop: scroll.scrollTop,
          dragging: false,
        };
      });
      scroll.addEventListener('pointermove', (event) => {
        if (!pan || pan.pointerId !== event.pointerId) return;
        const deltaX = event.clientX - pan.startX;
        const deltaY = event.clientY - pan.startY;
        if (!pan.dragging && Math.hypot(deltaX, deltaY) < 4) return;
        if (!pan.dragging) {
          pan.dragging = true;
          scroll.setPointerCapture(event.pointerId);
          scroll.classList.add('panning');
        }
        event.preventDefault();
        scroll.scrollLeft = pan.scrollLeft - deltaX;
        scroll.scrollTop = pan.scrollTop - deltaY;
        retainScroll();
      });
      const finishPan = (event) => {
        if (!pan || pan.pointerId !== event.pointerId) return;
        if (pan.dragging) {
          suppressSelectionUntil = performance.now() + 100;
          if (scroll.hasPointerCapture(event.pointerId)) {
            scroll.releasePointerCapture(event.pointerId);
          }
        }
        scroll.classList.remove('panning');
        retainScroll();
        pan = null;
      };
      scroll.addEventListener('pointerup', finishPan);
      scroll.addEventListener('pointercancel', finishPan);
      panel.querySelector('[data-tactical-actor]')?.addEventListener('change', (event) => {
        const actorId = event.target.value;
        if (actorId && (!tacticalControlsActor(surface, actorId) ||
            !surface.data.entities.some((entity) => entity.id === actorId))) return;
        updateTacticalSelection(surface, { actor: actorId || null, target: null });
        renderRulesetPanelProjection(projection);
      });
      panel.querySelector('[data-tactical-target]')?.addEventListener('change', (event) => {
        const targetId = event.target.value;
        if (targetId && !surface.data.entities.some((entity) => entity.id === targetId)) return;
        updateTacticalSelection(surface, tacticalTargetSelection(surface, targetId));
        renderRulesetPanelProjection(projection);
      });
      panel.querySelector('[data-tactical-zone]')?.addEventListener('change', (event) => {
        const zone = event.target.value;
        if (zone && !surface.data.zones?.some((item) => item.id === zone)) return;
        updateTacticalSelection(surface, { zone: zone || null });
        renderRulesetPanelProjection(projection);
      });
      panel.querySelector('[data-tactical-device]')?.addEventListener('change', (event) => {
        const device = event.target.value;
        if (device && !surface.data.entities?.some((item) => item.id === device)) return;
        updateTacticalSelection(surface, { device: device || null });
        renderRulesetPanelProjection(projection);
      });
      panel.querySelectorAll('[data-tactical-entity]').forEach((button) => {
        button.addEventListener('click', () => {
          if (performance.now() <= suppressSelectionUntil) return;
          const selection = tacticalSelection(surface);
          const entityId = button.dataset.tacticalEntity;
          const hasScope = Boolean(projection.player_control);
          const chooseActor = tacticalControlsActor(surface, entityId) &&
            (hasScope || !selection.actor || selection.actor === entityId);
          updateTacticalSelection(surface, chooseActor
            ? { actor: entityId, target: null } : tacticalTargetSelection(surface, entityId));
          renderRulesetPanelProjection(projection);
        });
      });
      panel.querySelector('[data-tactical-cover-info]')?.addEventListener('click', () => {
        view.coverInfo = !view.coverInfo;
        gamePanels().saveView(surface, view);
        renderRulesetPanelProjection(projection);
      });
      panel.querySelectorAll('[data-tactical-action]').forEach((button) => button.addEventListener('click', () => {
        const actionId = button.dataset.tacticalAction;
        if (!tacticalPreviewActions(surface).some((action) => action.id === actionId)) return;
        view.previewActionId = actionId;
        renderRulesetPanelProjection(projection);
      }));
      panel.querySelectorAll('[data-tactical-geometry]').forEach((button) => {
        button.addEventListener('click', (event) => {
          if (performance.now() <= suppressSelectionUntil) return;
          event.stopPropagation();
          view.selectedGeometry = button.dataset.tacticalGeometry;
          gamePanels().saveView(surface, view);
          renderRulesetPanelProjection(projection);
        });
      });
      if (continuous) {
        const selectPoint = (point, afterFocusChange = false) => {
          if (!point.every((value, axis) => Number.isFinite(value) &&
              value >= dimensions.lower[axis] && value <= dimensions.upper[axis])) return;
          updateTacticalSelection(surface, { aim_point: point, target: null, destination: null });
          if (afterFocusChange && window.requestAnimationFrame) {
            const scope = panelRenderScope(projection);
            // Let native Tab/Shift+Tab finish before replacing its destination field.
            window.requestAnimationFrame(() => {
              if (scope && state.activeCampaignId === scope.campaignId &&
                  state.activeSessionId === scope.sessionId && state.panelProjection === projection) {
                renderRulesetPanelProjection(projection);
              }
            });
          } else renderRulesetPanelProjection(projection);
        };
        panel.querySelectorAll('[data-coordinate-axis]').forEach((input) => {
          input.addEventListener('change', () => {
            const point = Array.from(panel.querySelectorAll('[data-coordinate-axis]'),
              (field) => field.value.trim() ? Number(field.value) : NaN);
            selectPoint(point, true);
          });
        });
        panel.querySelector('.tactical-world')?.addEventListener('click', (event) => {
          if (event.target !== event.currentTarget || performance.now() <= suppressSelectionUntil) return;
          const rect = event.currentTarget.getBoundingClientRect();
          const actor = surface.data.entities.find((item) => item.id === tacticalSelection(surface).actor);
          const height = tacticalSelection(surface).aim_point?.[2] ?? actor?.position[2] ?? dimensions.lower[2];
          selectPoint([
            dimensions.lower[0] + (event.clientX - rect.left) / rect.width * (dimensions.upper[0] - dimensions.lower[0]),
            dimensions.lower[1] + (event.clientY - rect.top) / rect.height * (dimensions.upper[1] - dimensions.lower[1]),
            height,
          ]);
        });
      }
      panel.querySelectorAll('[data-tactical-cell]').forEach((button) => {
        button.addEventListener('click', () => {
          if (performance.now() <= suppressSelectionUntil) return;
          const x = Number(button.dataset.cellX);
          const y = Number(button.dataset.cellY);
          const actor = surface.data.entities.find(
            (item) => item.id === tacticalSelection(surface).actor
          );
          const center = [
            dimensions.lower[0] + (x + 0.5) * dimensions.cellSize,
            dimensions.lower[1] + (y + 0.5) * dimensions.cellSize,
            dimensions.lower[2],
          ];
          const orientation = actor
            ? [center[0] - actor.position[0], center[1] - actor.position[1]] : [1, 0];
          updateTacticalSelection(surface, {
            destination: [x, y],
            aim_point: center,
            area: {
              kind: 'box',
              min: [center[0] - dimensions.cellSize / 2,
                center[1] - dimensions.cellSize / 2, dimensions.lower[2]],
              max: [center[0] + dimensions.cellSize / 2,
                center[1] + dimensions.cellSize / 2, dimensions.upper[2]],
            },
            orientation,
          });
          renderRulesetPanelProjection(projection);
        });
        button.addEventListener('keydown', (event) => {
          const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0],
            ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
          if (!delta) return;
          event.preventDefault();
          const x = Number(button.dataset.cellX) + delta[0];
          const y = Number(button.dataset.cellY) + delta[1];
          panel.querySelector(`[data-tactical-cell="${x},${y}"]`)?.focus();
        });
      });
      panel.querySelectorAll('[data-tactical-zoom]').forEach((button) => {
        button.addEventListener('click', () => {
          retainScroll();
          const delta = button.dataset.tacticalZoom === 'in' ? 0.25 : -0.25;
          view.zoom = Math.max(0.5, Math.min(2, view.zoom + delta));
          state.tacticalViews[surface.id] = view;
          gamePanels().saveView(surface, view);
          renderRulesetPanelProjection(projection);
        });
      });
    });
  }

  function tacticalActionSelection(surface, action) {
    const bindings = action && action.bindings;
    if (!bindings || typeof bindings !== 'object') return null;
    const current = tacticalSelection(surface);
    const selected = { map_revision: surface.data.map_revision };
    Object.values(bindings).forEach((source) => {
      if (source === 'path') return;
      selected[source] = source === 'declared_action' ? action.id : current[source];
    });
    return selected;
  }

  // Network actor choices require an explicit current Ruleset control scope.
  function networkControlsActor(actorId) {
    return state.panelProjection?.player_control?.available === true && playerControlsActor(actorId);
  }

  // Keep visible choices across graph updates; readiness is rechecked against the new revision.
  function networkSelection(surface) {
    const current = state.networkSelections[surface.id];
    const sameGraph = current && current.graph_id === surface.data.graph_id;
    if (sameGraph && current.graph_revision === surface.data.graph_revision) {
      if (current.actor && !networkControlsActor(current.actor)) current.actor = null;
      return current;
    }
    const projected = surface.data && surface.data.selection &&
      typeof surface.data.selection === 'object' ? surface.data.selection : {};
    const selection = {
      ...projected,
      ...(current?.actor ? { actor: current.actor } : {}),
      graph_id: surface.data.graph_id,
      graph_revision: surface.data.graph_revision,
    };
    if (sameGraph) {
      const node = surface.data.nodes.find(item => item.id === current.node);
      if (node) {
        selection.node = node.id;
        selection.remote_function = node.functions?.some(item => item.id === current.remote_function)
          ? current.remote_function : undefined;
      }
      if (surface.data.links.some(item => item.id === current.link)) selection.link = current.link;
      const route = current.route_step;
      if (Array.isArray(route) && route.length === 3 &&
          route.slice(0, 2).every(id => surface.data.nodes.some(item => item.id === id))) {
        const link = surface.data.links.find(item => item.id === route[2]);
        if (link && ((link.source === route[0] && link.target === route[1]) ||
            (link.direction === 'bidirectional' && link.target === route[0] && link.source === route[1]))) {
          selection.route_step = [...route];
        }
      }
    }
    if (selection.actor && !networkControlsActor(selection.actor)) selection.actor = null;
    state.networkSelections[surface.id] = selection;
    return selection;
  }

  function openBoundSurface(surfaceId, selector) {
    const target = state.panelProjection && state.panelProjection.surfaces.find(
      (surface) => surface.id === surfaceId
    );
    if (!target) return;
    void gamePanels().reveal(surfaceId);
    requestAnimationFrame(() => document.querySelector(selector)?.focus());
  }

  // Use the selected actor's current owner-checked preview; topology alone grants no access.
  function networkAccessPreview(surface) {
    if (!surface) return '';
    const selection = networkSelection(surface);
    if (!selection.actor) return '';
    const actions = surface.actions || [];
    const selectedFunction = selection.remote_function
      ? actions.filter(action => Object.values(action.bindings || {}).includes('remote_function')) : [];
    const candidates = selectedFunction.length ? selectedFunction : actions;
    let unavailableReason = '';
    for (const action of candidates) {
      const check = state.panelActionChecks.get(panelActionCheckKey(surface, action));
      if (check?.available && check.preview?.summary?.length) {
        return rulesetCardMarkup(check.preview, '') || '';
      }
      if (selectedFunction.length && check?.available === false && check.reason) {
        unavailableReason ||= check.reason;
      }
    }
    return unavailableReason ? `<p class="network-access-reason">${escapeHtml(unavailableReason)}</p>` : '';
  }

  function renderNetworkGraphPanel(data, surfaceId) {
    if (!data || !Array.isArray(data.nodes) || !Array.isArray(data.links)) {
      return '<div class="panel-empty">Network graph is unavailable.</div>';
    }
    const surface = state.panelProjection && state.panelProjection.surfaces.find(
      (entry) => entry.id === surfaceId
    );
    const selection = surface ? networkSelection(surface) : {
      graph_revision: data.graph_revision,
    };
    const playerControl = state.panelProjection && state.panelProjection.player_control;
    const actorOptions = playerControl?.available === true && Array.isArray(playerControl.actors)
      ? playerControl.actors.filter((actor) => typeof actor?.id === 'string' && actor.id)
      : [];
    const actorControl = surface?.actions?.some(actionBindsActor)
      ? `<label>Actor <select data-network-actor aria-label="Controlled network actor" ` +
        `${actorOptions.length ? '' : 'disabled'}><option value="">Select actor</option>` +
        actorOptions.map((actor) => `<option value="${escapeHtml(actor.id)}" ` +
          `${actor.id === selection.actor ? 'selected' : ''}>` +
          `${escapeHtml(actor.name || actor.label || actor.id)}` +
          `${playerControl.focused_actor_id === actor.id ? ' (current)' : ''}</option>`).join('') +
        `</select></label>`
      : '';
    const groupLabels = new Map(
      Array.isArray(data.groups) ? data.groups.map((group) => [group.id, group.label]) : []
    );
    const diagram = networkTopologyMarkup(data, selection);
    const grouped = new Map();
    data.nodes.forEach((node) => {
      if (diagram && node.id !== selection.node) return;
      const groupId = node.group_id || '__ungrouped';
      if (!grouped.has(groupId)) grouped.set(groupId, []);
      grouped.get(groupId).push(node);
    });
    const groups = Array.from(grouped.entries()).map(([groupId, nodes]) => {
      const cards = nodes.map((node) => {
        const selected = selection.node === node.id;
        const functions = Array.isArray(node.functions) ? node.functions.map((operation) => {
          const functionSelected = selection.remote_function &&
            selection.node === node.id && selection.remote_function === operation.id;
          return `<button type="button" class="network-function${functionSelected ? ' selected' : ''}" ` +
            `data-network-function="${escapeHtml(operation.id)}" ` +
            `data-network-function-node="${escapeHtml(node.id)}" ` +
            `aria-pressed="${functionSelected}" ${operation.enabled ? '' : 'disabled'}>` +
            `${escapeHtml(operation.label)}${operation.state ? ` · ${escapeHtml(operation.state)}` : ''}` +
            `</button>`;
        }).join('') : '';
        const binding = node.physical_binding;
        const physical = binding
          ? `<button type="button" class="network-physical-link" ` +
            `data-network-physical-surface="${escapeHtml(binding.surface_id)}" ` +
            `data-network-physical-entity="${escapeHtml(binding.entity_id)}">Locate on map</button>`
          : '';
        const states = ['status', 'access', 'control', 'detection']
          .filter((field) => node[field])
          .map((field) => `<span><strong>${escapeHtml(field)}</strong> ` +
            `${escapeHtml(node[field])}</span>`).join('');
        const heading = diagram ? `<div class="network-device-heading"><strong>${escapeHtml(node.label)}</strong></div>`
          : `<button type="button" class="network-node-select" data-network-node="${escapeHtml(node.id)}" aria-pressed="${selected}">` +
            `<strong>${escapeHtml(node.label)}</strong><small>${escapeHtml(panelIdentifierLabel(node.kind))}</small></button>`;
        return `<article class="network-node${selected ? ' selected' : ''}" ` +
          `data-network-node-card="${escapeHtml(node.id)}">` +
          heading +
          (states ? `<div class="network-node-state">${states}</div>` : '') +
          (functions ? `<div class="network-functions" aria-label="Available functions">` +
            `${functions}</div>` : '') + physical + `</article>`;
      }).join('');
      const label = groupId === '__ungrouped' ? 'Network' : groupLabels.get(groupId) || groupId;
      return `<section class="network-segment" aria-label="${escapeHtml(label)}">` +
        `<h4>${escapeHtml(label)}</h4><div class="network-node-list">${cards}</div></section>`;
    }).join('');
    const links = data.links.map((link) => {
      const selected = selection.link === link.id;
      const source = data.nodes.find((node) => node.id === link.source);
      const target = data.nodes.find((node) => node.id === link.target);
      const arrow = link.direction === 'directed' ? '→' : '↔';
      return `<button type="button" class="network-link${selected ? ' selected' : ''}" ` +
        `data-network-link="${escapeHtml(link.id)}" data-network-source="${escapeHtml(link.source)}" ` +
        `data-network-target="${escapeHtml(link.target)}" aria-pressed="${selected}" ` +
        `aria-label="${escapeHtml(source && source.label || link.source)} ${arrow} ` +
        `${escapeHtml(target && target.label || link.target)}">` +
        `${escapeHtml(source && source.label || link.source)} ${arrow} ` +
        `${escapeHtml(target && target.label || link.target)}` +
        `${link.label ? ` · ${escapeHtml(link.label)}` : ''}</button>`;
    }).join('');
    return `<div class="network-toolbar"><span>${escapeHtml(data.nodes.length)} known devices</span>` +
      actorControl +
      `</div><div data-network-access-preview aria-live="polite">${networkAccessPreview(surface)}</div>` +
      `<div class="network-graph" aria-label="Visible network topology">${diagram}${groups ||
        `<div class="panel-empty">${data.nodes.length ? 'Select a device to inspect its state and functions.' : 'No known devices yet.'}</div>`}</div>` +
      `<details class="network-connections"><summary>Connections</summary>` +
      `<div class="network-link-list" aria-label="Visible network links">${links || 'No known connections'}</div></details>`;
  }

  // Lay out only the bounded public graph. Connections never imply access or privilege.
  function networkTopologyMarkup(data, selection) {
    if (!data.nodes.length || data.nodes.length > 64) return '';
    const neighbors = new Map(data.nodes.map((node) => [node.id, []]));
    for (const link of data.links) {
      if (!neighbors.has(link.source) || !neighbors.has(link.target)) continue;
      neighbors.get(link.source).push(link.target);
      neighbors.get(link.target).push(link.source);
    }
    const levels = new Map();
    for (const root of data.nodes) {
      if (levels.has(root.id)) continue;
      levels.set(root.id, 0);
      const queue = [root.id];
      for (let index = 0; index < queue.length; index++) {
        const id = queue[index];
        for (const neighbor of neighbors.get(id)) {
          if (levels.has(neighbor)) continue;
          levels.set(neighbor, Math.min(5, levels.get(id) + 1));
          queue.push(neighbor);
        }
      }
    }
    const rows = new Map(), positions = new Map();
    for (const node of data.nodes) {
      const column = levels.get(node.id), row = rows.get(column) || 0;
      positions.set(node.id, { x: 12 + column * 194, y: 12 + row * 94 });
      rows.set(column, row + 1);
    }
    const width = Math.max(...levels.values()) * 194 + 194;
    const height = Math.max(...rows.values()) * 94 + 12;
    const lines = data.links.map((link) => {
      const from = positions.get(link.source), to = positions.get(link.target);
      if (!from || !to) return '';
      const forward = to.x > from.x;
      const x1 = from.x + (forward ? 166 : 0), x2 = to.x + (forward ? 0 : 166);
      const y1 = from.y + 35, y2 = to.y + 35;
      return `<path d="M ${x1} ${y1} C ${(x1 + x2) / 2} ${y1}, ${(x1 + x2) / 2} ${y2}, ${x2} ${y2}" ` +
        `class="${selection.link === link.id ? 'selected' : ''}"/>`;
    }).join('');
    const nodes = data.nodes.map((node) => {
      const position = positions.get(node.id), selected = node.id === selection.node;
      return `<button type="button" class="network-diagram-node${selected ? ' selected' : ''}" ` +
        `data-network-node="${escapeHtml(node.id)}" aria-pressed="${selected}" title="${escapeHtml(node.label)}" ` +
        `style="left:${position.x}px;top:${position.y}px">` +
        `<strong>${escapeHtml(node.label)}</strong>` +
        `<small>${escapeHtml(panelIdentifierLabel(node.kind))}</small></button>`;
    }).join('');
    return `<div class="network-diagram-scroll"><div class="network-diagram" style="width:${width}px;height:${height}px">` +
      `<svg aria-hidden="true" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">${lines}</svg>${nodes}</div></div>`;
  }

  function wireNetworkGraph(container, projection) {
    container.querySelectorAll('[data-panel-id]').forEach((panel) => {
      const surface = projection.surfaces.find((entry) => entry.id === panel.dataset.panelId);
      if (!surface || surface.renderer !== 'network_graph') return;
      panel.querySelector('[data-network-actor]')?.addEventListener('change', (event) => {
        const actorId = event.target.value;
        if (actorId && !networkControlsActor(actorId)) return;
        const selection = networkSelection(surface);
        selection.actor = actorId || null;
        gamePanels().choose(surface.id, { actor: selection.actor });
        renderRulesetPanelProjection(projection);
      });
      panel.querySelectorAll('[data-network-node]').forEach((button) => {
        button.addEventListener('click', () => {
          const selection = networkSelection(surface);
          selection.node = button.dataset.networkNode;
          selection.remote_function = null;
          gamePanels().choose(surface.id, selection);
          renderRulesetPanelProjection(projection);
        });
      });
      panel.querySelectorAll('[data-network-function]').forEach((button) => {
        button.addEventListener('click', () => {
          const selection = networkSelection(surface);
          selection.node = button.dataset.networkFunctionNode;
          selection.remote_function = button.dataset.networkFunction;
          gamePanels().choose(surface.id, selection);
          renderRulesetPanelProjection(projection);
        });
      });
      panel.querySelectorAll('[data-network-link]').forEach((button) => {
        button.addEventListener('click', () => {
          const selection = networkSelection(surface);
          selection.link = button.dataset.networkLink;
          selection.route_step = [
            button.dataset.networkSource,
            button.dataset.networkTarget,
            button.dataset.networkLink,
          ];
          gamePanels().choose(surface.id, selection);
          renderRulesetPanelProjection(projection);
        });
      });
      panel.querySelectorAll('[data-network-physical-surface]').forEach((button) => {
        button.addEventListener('click', () => {
          state.crossSurfaceFocus = {
            surfaceId: button.dataset.networkPhysicalSurface,
            entityId: button.dataset.networkPhysicalEntity,
          };
          openBoundSurface(
            button.dataset.networkPhysicalSurface,
            `[data-tactical-entity="${CSS.escape(button.dataset.networkPhysicalEntity)}"]`
          );
        });
      });
    });
    container.querySelectorAll('[data-network-return-surface]').forEach((button) => {
      button.addEventListener('click', () => {
        const network = projection.surfaces.find(
          (surface) => surface.id === button.dataset.networkReturnSurface
        );
        if (!network) return;
        networkSelection(network).node = button.dataset.networkReturnNode;
        openBoundSurface(
          network.id,
          `[data-network-node="${CSS.escape(button.dataset.networkReturnNode)}"]`
        );
      });
    });
  }

  function networkActionSelection(surface, action) {
    const bindings = action && action.bindings;
    if (!bindings || typeof bindings !== 'object') return null;
    const current = networkSelection(surface);
    const selected = { graph_revision: surface.data.graph_revision };
    Object.values(bindings).forEach((source) => {
      selected[source] = source === 'declared_action' ? action.id : current[source];
    });
    return selected;
  }

  function panelContent(surface) {
    if (surface.renderer === 'piece_table') {
      return window.SylviaPieceTable.render(surface.data, surface.actions);
    }
    if (surface.id === ACTION_QUEUE_PANEL_ID && surface.renderer === 'action_queue')
      return sequenceMarkup(state.queuePreviousCards || new Set(), surface.data);
    if (surface.renderer === 'party') return renderPartyPanel(surface.data, surface);
    if (surface.renderer === 'unit_list' && Array.isArray(surface.data?.participants)) {
      return renderCharacterPanel(surface);
    }
    if (surface.renderer === 'encounter') return renderEncounterPanel(surface.data);
    if (surface.renderer === 'inventory') return renderInventoryPanel(surface.data, surface);
    if (surface.renderer === 'generic_inventory') return renderGenericInventoryPanel(surface);
    if (surface.renderer === 'objectives') return renderObjectivesPanel(surface.data);
    if (['tactical_grid', 'coordinate_map'].includes(surface.renderer)) {
      return renderTacticalGridPanel(surface.data, surface.id);
    }
    if (surface.renderer === 'network_graph') {
      return renderNetworkGraphPanel(surface.data, surface.id);
    }
    return renderGenericPanel(surface.data);
  }

  // Show the Ruleset's proposal before authorizing the exact prepared request.
  function confirmPlayerAction(proposal) {
    if (!proposal.confirmation_required) return Promise.resolve(true);
    const dialog = document.createElement('dialog');
    dialog.className = 'player-action-dialog';
    const actorName = proposal.actor?.name;
    const actor = actorName
      ? `<dl><dt>Actor</dt><dd>${escapeHtml(actorName)}</dd></dl>` : '';
    const fallback = Object.entries(proposal.selection || {})
      .filter(([key]) => !key.endsWith('_revision') && !(actorName && key === 'actor'))
      .map(([key, value]) => `<dt>${escapeHtml(key.replace(/_/g, ' '))}</dt>` +
        `<dd>${escapeHtml(typeof value === 'string' ? value : JSON.stringify(value))}</dd>`).join('');
    const preview = rulesetCardMarkup(proposal.preview, 'action-preview') || `<dl>${fallback}</dl>`;
    dialog.innerHTML = `<h2>${escapeHtml(proposal.label || 'Confirm action')}</h2>${actor}${preview}` +
      '<form method="dialog"><button class="btn btn-secondary" value="cancel">Cancel</button>' +
      '<button class="btn btn-primary" value="confirm">Confirm</button></form>';
    document.body.appendChild(dialog);
    return new Promise((resolve) => {
      dialog.addEventListener('close', () => {
        const accepted = dialog.returnValue === 'confirm';
        dialog.remove();
        resolve(accepted);
      }, { once: true });
      dialog.showModal();
    });
  }

  function panelMarkup(surface, includeHeader = true) {
    if (surface.loading) return `<section class="ruleset-panel" data-panel-id="${escapeHtml(surface.id)}">` +
      `<p class="panel-empty" role="status">${surface.loading === 'error'
        ? 'Game panels could not be loaded. Retry loading in the campaign chat.'
        : 'Loading game panels…'}</p></section>`;
    const content = panelContent(surface);
    if (['encounter', 'inventory'].includes(surface.renderer) && !content) return '';
    const minimum = Number.isInteger(surface.minimum_size) ? surface.minimum_size : 96;
    const preferred = panelSize(surface);
    const header = includeHeader
      ? `<div class="ruleset-panel-header"><span class="ruleset-panel-icon">` +
        `${escapeHtml(surface.icon || '◆')}</span>${escapeHtml(surface.title || surface.id)}</div>`
      : '';
    const footerActions = Array.isArray(surface.actions) ? surface.actions.filter(
      (action) => (!['party', 'unit_list', 'inventory'].includes(surface.renderer) || !actionBindsActor(action)) &&
        !(surface.renderer === 'piece_table' && Object.values(action.bindings || {}).includes('piece'))
    ) : [];
    const mapSurface = ['tactical_grid', 'coordinate_map'].includes(surface.renderer);
    const selectedAction = mapSurface ? tacticalPreviewState(surface, state.tacticalViews[surface.id] || {}).action : null;
    const shownActions = mapSurface ? footerActions.filter((action) => !actionBindsActor(action)) : footerActions;
    const contextual = mapSurface ? (state.panelProjection?.surfaces || [])
      .filter((item) => item.id !== surface.id && ['party', 'unit_list', 'encounter'].includes(item.renderer))
      .flatMap((owner) => (owner.actions || []).filter((action) => !actionBindsActor(action) && /end.*turn/i.test(action.label || action.id))
        .map((action) => `<span data-panel-id="${escapeHtml(owner.id)}"><button type="button" class="btn btn-secondary ruleset-panel-action map-end-turn" ` +
          `data-panel-action-id="${escapeHtml(action.id)}" ${panelActionAvailable(owner, action) ? '' : 'disabled'}>` +
          `${escapeHtml(action.label || action.id)}</button></span>`)).join('') : '';
    const actions = shownActions.map((action) =>
      `<button class="btn btn-secondary ruleset-panel-action${mapSurface ? ' map-end-turn' : ''}" type="button" ` +
      `data-panel-action-id="${escapeHtml(action.id || '')}" ${panelActionAvailable(surface, action) ? '' : 'disabled'}>` +
      `${escapeHtml(mapSurface ? mapActionLabel(surface, action) : action.label || action.id || 'Action')}</button>`).join('');
    const commit = mapSurface && selectedAction ? `<div class="map-commit-strip">` +
      `<span><strong>${escapeHtml(mapActionLabel(surface, selectedAction))}</strong>` +
      `<small>${escapeHtml(tacticalSelection(surface).target ? 'Target: ' + (surface.data.entities.find((entity) => entity.id === tacticalSelection(surface).target)?.display?.label || tacticalSelection(surface).target) :
        tacticalSelectionGuidance(surface, selectedAction) || 'Review the preview before committing.')}</small></span>` +
      `<button type="button" class="btn btn-primary ruleset-panel-action" data-panel-action-id="${escapeHtml(selectedAction.id)}" ` +
      `${panelActionAvailable(surface, selectedAction) ? '' : 'disabled'}>${sequenceModeActive() ? 'Queue' : 'Commit'} ${escapeHtml(mapActionLabel(surface, selectedAction))}</button></div>` : '';
    return `<section class="ruleset-panel ${surface.id === ACTION_QUEUE_PANEL_ID ? 'ruleset-queue-panel' : ''} ${['tactical_grid', 'coordinate_map'].includes(surface.renderer) ? 'ruleset-map-panel' : surface.renderer === 'party' ? 'ruleset-roster-panel' : surface.renderer === 'unit_list' ? 'ruleset-character-panel' : ''}" data-panel-id="${escapeHtml(surface.id)}" ` +
      `style="min-height:${minimum}px"><div>${header}` +
      `<div class="ruleset-panel-content" data-panel-size-id="${escapeHtml(surface.id)}" ` +
      `style="height:${preferred}px;min-height:${minimum}px">${content}</div>` +
      (mapSurface ? `<div class="map-command-deck"><div class="map-command-row">${mapActionPalette(surface)}${actions}${contextual}</div>${commit}</div>` :
        actions ? `<div class="ruleset-panel-actions contextual-action-bar" aria-label="Available actions">${actions}</div>` : '') +
      `</div></section>`;
  }

  function renderRulesetPanelProjection(projection) {
    projection = queueProjection(projection);
    if (window.__sylviaPanelContext && state.activeSessionId) ensureSessionActivity();
    const host = $('ruleset-panel-host');
    const container = $('ruleset-panels');
    const savedShotControls = captureShotControls(container);
    const savedMapTools = captureMapTools(container);
    const savedPreviewHeights = captureTacticalPreviewHeights(container, projection);
    const nextPanelRenderContext = panelRenderScope(projection);
    const sameCharacterScope = panelRenderContext && nextPanelRenderContext &&
      panelRenderContext.campaignId === nextPanelRenderContext.campaignId &&
      panelRenderContext.sessionId === nextPanelRenderContext.sessionId &&
      panelRenderContext.rulesetId === nextPanelRenderContext.rulesetId;
    const savedCharacterFocus = sameCharacterScope ? captureCharacterFocus(container) : null;
    // Native disclosure toggles can still be queued when another projection redraws the DOM.
    if (sameCharacterScope && !window.__sylviaPanelContext) {
      container.querySelectorAll('[data-character-expansion]').forEach((details) => {
        applyPanelSelection(details.closest('[data-panel-id]')?.dataset.panelId,
          { expansion: { id: details.dataset.characterExpansion, open: details.open } });
      });
    }
    const savedCharacterGroups = sameCharacterScope ? Array.from(container.querySelectorAll('[data-character-stat]'), (details) => ({
      surfaceId: details.closest('[data-panel-id]')?.dataset.panelId, stat: details.dataset.characterStat,
      open: details.open, focused: document.activeElement === details.querySelector('summary'),
    })) : [];
    const nativeProjection = nativePanelProjection(projection);
    if (state.panelProjectionSource !== nativeProjection) state.panelActionChecks.clear();
    state.panelProjectionSource = nativeProjection;
    state.panelProjection = projection;
    state.queuePreviousCards = new Set(Array.from(container.querySelectorAll('[data-sequence-step]'), node => node.dataset.sequenceStep));
    const markup = gamePanels().markup(projection);
    applySidebarWidth();
    const effectsNotice = projection?.shot_effects?.some((record) => record.unavailable)
      ? '<p class="shot-effects-notice" role="status">Some shot effects are unavailable. The action result is saved.</p>' : '';
    const pendingCommand = pendingPanelCommand();
    const recovery = pendingCommand && state.bridge?.panel_action_receipt
      ? `<div class="panel-action-recovery" role="status"><span>The last action's reply was not confirmed.</span>` +
        `<button type="button" class="btn btn-secondary btn-sm" data-panel-recover ${state.panelCommitPending || selectedSessionIsWorking() ? 'disabled' : ''}>Recover action</button>` +
        (state.panelRecoveryAbsent === pendingCommand.operation_id
          ? '<button type="button" class="btn btn-secondary btn-sm" data-panel-retry-original>Retry original action</button><button type="button" class="btn btn-secondary btn-sm" data-panel-dismiss-recovery>Dismiss</button>' : '') + '</div>' : '';
    const queueIssueKey = issue => JSON.stringify([
      issue.closest('.action-sequence')?.dataset.sequenceId,
      issue.closest('.sequence-step')?.querySelector('[data-sequence-step]')?.dataset.sequenceStep || null,
      issue.querySelector('p')?.textContent,
    ]);
    const openQueueIssues = new Set(Array.from(container.querySelectorAll('.sequence-issue[open]'), queueIssueKey));
    const previousList = container.querySelector('.sequence-cards');
    const queueScroll = previousList ? {top:previousList.scrollTop,
      atEnd:previousList.scrollTop + previousList.clientHeight >= previousList.scrollHeight - 2} : null;
    container.innerHTML = recovery + markup + effectsNotice;
    for (const saved of savedCharacterGroups) {
      const panel = container.querySelector(`[data-panel-id="${CSS.escape(saved.surfaceId)}"]`);
      const details = panel?.querySelector(`[data-character-stat="${CSS.escape(saved.stat)}"]`);
      if (details) {
        details.open = saved.open;
        if (saved.focused) details.querySelector('summary')?.focus({ preventScroll: true });
      }
    }
    if (savedCharacterFocus) {
      container.querySelector(`[data-panel-id="${CSS.escape(savedCharacterFocus.surfaceId)}"]`)
        ?.querySelector(savedCharacterFocus.selector)?.focus({ preventScroll: true });
    }
    container.querySelectorAll('.sequence-issue').forEach(issue => { issue.open = openQueueIssues.has(queueIssueKey(issue)); });
    const nextList = container.querySelector('.sequence-cards');
    if (nextList && queueScroll) nextList.scrollTop = queueScroll.atEnd ? nextList.scrollHeight : queueScroll.top;
    wireSequence(container);
    container.querySelector('[data-panel-recover]')?.addEventListener('click', () => void recoverPanelAction());
    container.querySelector('[data-panel-retry-original]')?.addEventListener('click', () => void recoverPanelAction(true));
    container.querySelector('[data-panel-dismiss-recovery]')?.addEventListener('click', () => void recoverPanelAction(false, true));
    panelRenderContext = nextPanelRenderContext;
    restoreShotControls(container, savedShotControls, nextPanelRenderContext);
    restoreMapTools(container, savedMapTools, nextPanelRenderContext);
    host.hidden = !(markup || recovery || effectsNotice);
    gamePanels().wire(container);
    container.querySelectorAll('[data-equipment-slot]').forEach((select) => {
      select.addEventListener('change', () => {
        const surfaceId = select.closest('[data-panel-id]')?.dataset.panelId;
        const key = JSON.stringify([surfaceId, select.dataset.equipmentOwner, select.dataset.equipmentSet]);
        const draft = state.equipmentDrafts.get(key);
        if (!draft) return;
        const [item, serial] = select.value ? JSON.parse(select.value) : ['', null];
        draft[select.dataset.equipmentSlot] = item;
        draft[select.dataset.equipmentSlot === 'main_hand' ? 'main_copy' : 'off_copy'] = serial;
        if (select.dataset.equipmentSlot === 'main_hand' && projection.surfaces.find((item) => item.id === surfaceId)?.data?.equipment?.weapons?.find((weapon) => weapon.id === item)?.hands === 2) {
          draft.off_hand = item;
          draft.off_copy = serial;
        }
        renderRulesetPanelProjection(projection);
      });
    });
    container.querySelectorAll('[data-inventory-character]').forEach((select) => {
      select.addEventListener('change', () => {
        const surfaceId = select.closest('[data-panel-id]')?.dataset.panelId;
        gamePanels().choose(surfaceId, { character: select.value });
        renderRulesetPanelProjection(projection);
      });
    });
    container.querySelectorAll('[data-shot-visual], [data-shot-volume], [data-shot-muted]')
      .forEach((control) => control.addEventListener('change', () => {
        const patch = control.hasAttribute('data-shot-visual') ? { visual: control.value }
          : control.hasAttribute('data-shot-volume') ? { volume: Number(control.value) / 100 }
            : { muted: control.checked };
        void Promise.resolve(gamePanels().configureEffects(patch)).catch((error) => {
          setInlineStatus(friendlyCommandError('Effects settings could not be updated', error), 'panels');
        });
      }));
    container.querySelectorAll('[data-shot-skip]').forEach((button) => {
      button.addEventListener('click', () => {
        void Promise.resolve(gamePanels().skipEffects()).then(animateShots);
      });
    });
    container.querySelectorAll('[data-character-actor]').forEach((select) => {
      select.addEventListener('change', () => {
        const surfaceId = select.closest('[data-panel-id]')?.dataset.panelId;
        gamePanels().choose(surfaceId, { character: select.value || null });
        renderRulesetPanelProjection(projection);
        container.querySelector('[data-character-actor]')?.focus({ preventScroll: true });
      });
    });
    container.querySelectorAll('[data-party-select-actor]').forEach((button) => {
      button.addEventListener('click', () => {
        const surfaceId = button.closest('[data-panel-id]')?.dataset.panelId;
        gamePanels().choose(surfaceId, { character: button.dataset.partySelectActor });
        renderRulesetPanelProjection(projection);
        container.querySelector(`[data-party-select-actor="${CSS.escape(button.dataset.partySelectActor)}"]`)?.focus({ preventScroll: true });
      });
    });
    container.querySelectorAll('[data-character-action-panel]').forEach((button) => {
      button.addEventListener('click', () => void gamePanels().reveal(button.dataset.characterActionPanel));
    });
    container.querySelectorAll('[data-character-expansion]').forEach((details) => {
      details.addEventListener('toggle', () => {
        if (!details.isConnected) return;
        const id = details.dataset.characterExpansion;
        if (characterInspection().expanded.includes(id) === details.open) return;
        gamePanels().choose(details.closest('[data-panel-id]')?.dataset.panelId,
          { expansion: { id, open: details.open } });
      });
    });
    window.SylviaTileTable?.attach(container, () => void refreshPanelActionChecks(projection, container),
      () => !state.panelCommitPending && !panelIntentPending() && !pendingPanelCommand() &&
        !selectedSessionIsWorking() && !campaignDisplayPending());
    new Set([...container.querySelectorAll('[data-panel-action-id]'),
      ...container.querySelectorAll('[data-table-discard]')]).forEach((button) => {
      button.addEventListener('click', async () => {
        if (button.disabled || !state.activeCampaignId || !state.activeSnapshot || selectedSessionIsWorking() ||
            (!sequenceModeActive() && (!state.bridge.execute_panel_action || !state.bridge.preview_panel_action))) return;
        const activity = selectedActivity();
        const campaignId = state.activeCampaignId;
        const panel = button.closest('[data-panel-id]');
        const sessionId = state.activeSessionId;
        const surface = projection.surfaces.find((entry) => entry.id === panel.dataset.panelId);
        const action = surface && Array.isArray(surface.actions)
          ? surface.actions.find((entry) => entry.id === button.dataset.panelActionId) : null;
        if (!action || !panelActionAvailable(surface, action, button.dataset.panelActorId, button.dataset.panelPieceId)) return;
        button.disabled = true;
        const selection = JSON.parse(JSON.stringify(panelActionSelection(surface, action,
          button.dataset.panelActorId, button.dataset.panelPieceId)));
        const projectionRevision = projection.revision;
        const pendingIntent = { campaignId, sessionId };
        state.panelIntentPending = pendingIntent;
        try {
          if (sequenceModeActive()) {
            const command = {campaign_id:campaignId,session_id:sessionId,surface_id:surface.id,
              panel_action_id:action.id,operation_id:`panel-${crypto.randomUUID()}`,selection};
            if (window.__sylviaPanelContext) await state.bridge.queue_panel_action(command);
            else await appendQueuedAction({operation_id:command.operation_id,
              source:{kind:'panel',surface_id:surface.id,action_id:action.id},selection},
              {flush:action.response_policy === 'automatic'});
            return;
          }
          // Streamed mechanics can advance after the last full campaign refresh.
          const actionSnapshot = await state.bridge.snapshot(campaignId);
          if (selectedActivityMatches(activity)) state.activeSnapshot = actionSnapshot;
          const command = {
            campaign_id: campaignId,
            surface_id: panel.dataset.panelId,
            panel_action_id: button.dataset.panelActionId,
            operation_id: `panel-${crypto.randomUUID()}`,
            expected_revision: actionSnapshot.revision,
            selection,
            session_id: sessionId,
          };
          if (!selectedActivityMatches(activity) || !panelActionStillCurrent(command, projectionRevision)) return;
          const proposal = await state.bridge.preview_panel_action(command);
          if (!selectedActivityMatches(activity) || !panelActionStillCurrent(command, projectionRevision) || !await confirmPlayerAction(proposal)) return;
          if (!selectedActivityMatches(activity) || selectedSessionIsWorking() || !panelActionStillCurrent(command, projectionRevision)) return;
          const confirmedCommand = { ...command, confirmation: proposal.confirmation };
          localStorage.setItem(panelRecoveryKey(), JSON.stringify(confirmedCommand));
          state.panelCommitPending = true;
          const receipt = await state.bridge.execute_panel_action(confirmedCommand);
          if (selectedActivityMatches(activity)) clearPanelRecovery();
          if (receipt.status !== 'failed' && surface.renderer === 'inventory') state.equipmentDrafts?.clear();
          selectedStatus(activity, receipt.status === 'failed' ? 'Panel action failed' : 'Panel action completed', 'panels');
          if (selectedActivityMatches(activity)) {
            try { await refreshSelectedCampaign(); }
            catch (error) { selectedStatus(activity, 'Action saved; refresh the campaign display', 'panels'); }
          }
          await respondToPlayerAction(campaignId, sessionId, command.operation_id);
        } catch (error) {
          selectedStatus(activity, friendlyCommandError('Panel action could not be completed', error), 'panels');
        } finally {
          if (state.panelIntentPending === pendingIntent) state.panelIntentPending = null;
          state.panelCommitPending = false;
          button.disabled = false;
          if (selectedActivityMatches(activity)) await renderRulesetPanels(campaignId, true);
        }
      });
    });
    wireTacticalGrid(container, projection, savedPreviewHeights);
    wireNetworkGraph(container, projection);
    void refreshPanelActionChecks(projection, container);
    container.querySelectorAll('[data-panel-size-id]').forEach((content) => {
      if (window.__sylviaPanelContext) return;
      let resizeStart = null;
      content.addEventListener('mousedown', (event) => {
        const rect = content.getBoundingClientRect();
        resizeStart = event.button === 0 && getComputedStyle(content).resize !== 'none' &&
          event.clientX >= rect.right - 18 && event.clientY >= rect.bottom - 18
          ? Math.round(rect.height) : null;
      });
      content.addEventListener('mouseup', () => {
        const previousHeight = resizeStart;
        resizeStart = null;
        if (previousHeight === null) return;
        const height = Math.round(content.getBoundingClientRect().height);
        if (height === previousHeight) return;
        void gamePanels().changed({ type: 'size', panelId: content.dataset.panelSizeId,
          height });
      });
    });
    // Let replacement maps paint before the shared audio and visual timeline starts.
    if (!gamePanels().presentEffects(projection, animateShots)) animateShots();
  }

  // App-owned presentation participates in the same workspace as package panels.
  // Child windows receive this view, never the native confirmation or full receipts.
  function queueView(queue = state.actionSequence) {
    if (!queue || queue.session_id !== state.activeSessionId) return null;
    return {
      session_id:queue.session_id, sequence_id:queue.sequence_id, version:queue.version,
      status:queue.status, error:panelPlayerReason(queue.error),
      pause_summary:panelPlayerReason(queue.pause_summary), pause_reason:panelPlayerReason(queue.pause_reason),
      committed_action_ids:(queue.committed_action_ids || []).slice(0,32),
      steps:(queue.steps || []).slice(0,32).map(step => ({
        step_id:step.step_id, label:step.label, status:step.status, actor:step.actor,
        actor_name:step.actor_name, response_policy:step.response_policy,
        selection:step.selection ? Object.fromEntries(['target','destination','aim_point']
          .filter(key => Object.hasOwn(step.selection,key)).map(key => [key,step.selection[key]])) : null,
        preview:step.preview ? {summary:(step.preview.summary || []).filter(row => /cost|distance/i.test(row.label || ''))} : null,
        error:step.status === 'committed' ? null : panelPlayerReason(step.error),
        warning:step.status === 'committed' ? null : panelPlayerReason(step.warning),
      })),
    };
  }

  function queueProjection(projection) {
    if (!projection || projection.loading) {
      state.actionSequence = null;
      return projection ? {...projection,presentation_key:null,surfaces:projection.surfaces.map(surface =>
        surface.id === ACTION_QUEUE_PANEL_ID ? {...surface,data:undefined} : surface)} : null;
    }
    if (window.__sylviaPanelContext) return projection;
    const surfaces = projection.surfaces.filter(surface => surface.id !== ACTION_QUEUE_PANEL_ID);
    if (state.activeSessionRole !== 'dm' || !state.activeSessionId ||
        projection.campaign_id !== state.activeCampaignId ||
        !state.bridge?.compose_action_sequence || !state.bridge?.action_sequence) {
      const result = {...projection,surfaces};
      queueProjectionSources.set(result,nativePanelProjection(projection));
      return result;
    }
    const lane = sequenceLanes.get(JSON.stringify([state.activeCampaignId,state.activeSessionId]));
    const data = {queue:queueView(),busy:Boolean(lane?.pending || state.sequencePending || selectedSessionIsWorking() || campaignDisplayPending())};
    surfaces.push({id:ACTION_QUEUE_PANEL_ID,title:'Queued actions',renderer:'action_queue',icon:'≡',
      order:99,default_placement:'docked',allowed_placements:['docked','floating','window'],
      default_size:220,minimum_size:120,actions:[],data});
    const result = {...projection,surfaces,presentation_key:JSON.stringify([data,projection.response_action &&
      [projection.response_action.action_id,projection.response_action.session_id,projection.response_action.available]])};
    queueProjectionSources.set(result,nativePanelProjection(projection));
    return result;
  }

  function nativePanelProjection(projection) {
    return queueProjectionSources.get(projection) || projection;
  }

  function refreshQueuePresentation() {
    if (panelHost && state.panelProjection && !state.panelProjection.loading && !window.__sylviaPanelContext)
      renderRulesetPanelProjection(state.panelProjection);
  }

  function sequenceModeActive() {
    return state.activeSessionRole === 'dm' && Boolean(window.__sylviaPanelContext
      ? state.bridge?.queue_panel_action
      : state.bridge?.compose_action_sequence && state.bridge?.action_sequence);
  }

  function automaticQueueCommitted(queue) {
    return Boolean(queue?.steps.length && queue.steps.every(step => step.status === 'committed') &&
      queue.steps.at(-1)?.response_policy === 'automatic');
  }

  function queueResponseAction(projection = state.panelProjection) {
    const offer = projection?.response_action;
    return state.activeSessionRole === 'dm' && !projection?.loading &&
      projection?.campaign_id === state.activeCampaignId && offer?.available === true &&
      typeof offer.action_id === 'string' && offer.action_id.length > 0 &&
      (!offer.session_id || offer.session_id === state.activeSessionId) ? offer : null;
  }

  function sequenceMarkup(previousSteps = new Set(), view = null) {
    if (state.activeSessionRole !== 'dm' || !state.activeSessionId ||
        !(window.__sylviaPanelContext ? state.bridge?.queue_sequence_operation : state.bridge?.compose_action_sequence)) return '';
    const value = view ? view.queue : state.actionSequence;
    const queue = value?.session_id === state.activeSessionId ? value : null;
    const busy = view?.busy || state.sequencePending || selectedSessionIsWorking() || campaignDisplayPending();
    const steps = queue?.steps || [];
    const pending = steps.filter(step => step.status !== 'committed').length;
    const multipleActors = new Set(steps.map(step => step.actor).filter(Boolean)).size > 1;
    const stepReason = (step) => step.status === 'committed' ? '' : panelPlayerReason(step.error || step.warning);
    const queueReason = panelPlayerReason(queue?.error || queue?.pause_summary || queue?.pause_reason);
    const showQueueReason = queueReason && !automaticQueueCommitted(queue) && !steps.some(step => stepReason(step) === queueReason);
    const explanation = (reason, label) => reason ? `<details class="sequence-issue"><summary>${escapeHtml(label)}</summary><p role="status">${escapeHtml(reason)}</p></details>` : '';
    const targetLabel = (id) => {
      const entities = (state.panelProjection?.surfaces || []).flatMap(surface => Array.isArray(surface.data?.entities) ? surface.data.entities : []);
      const target = entities.find(entity => entity.id === id || entity.participant_ref === id);
      return target?.display?.label || target?.name || id;
    };
    const rows = steps.map((step, index) => {
      const status = step.status === 'committed' ? 'Committed' : step.status === 'awaiting_choice' ? 'Awaiting choice' : step.status === 'executing' ? 'Sending' : 'Pending';
      const detail = [
        multipleActors ? step.actor_name : null,
        step.selection?.target ? `Target: ${targetLabel(step.selection.target)}` : '',
        step.selection?.destination ? `Destination: ${JSON.stringify(step.selection.destination)}` : '',
        step.selection?.aim_point ? `Map point: ${JSON.stringify(step.selection.aim_point)}` : '',
        ...(step.preview?.summary || []).filter(row => /cost|distance/i.test(row.label || '')).map(row => `${row.label}: ${row.value}`),
      ].filter(Boolean).join(' · ');
      return `<li class="sequence-step sequence-${step.status}">
        <span class="sequence-number" aria-label="Step ${index + 1}: ${status}" data-sequence-step="${escapeHtml(step.step_id)}" ${previousSteps.has(step.step_id) ? '' : 'data-sequence-enter'}>${index + 1}</span>
        <div class="sequence-card-copy"><div class="sequence-card-heading"><strong title="${escapeHtml(step.actor_name ? step.actor_name + ': ' + step.label : step.label)}">${escapeHtml(step.label)}</strong>${status === 'Pending' ? '' : `<span class="sequence-status">${status}</span>`}</div>
          <small title="${escapeHtml(detail)}">${detail ? `<span class="sequence-detail">${escapeHtml(detail)}</span>` : status}</small></div>
        ${step.status === 'pending' ? `<button type="button" class="sequence-remove" data-sequence-remove="${escapeHtml(step.step_id)}" aria-label="Remove ${escapeHtml(step.label)}" title="Remove pending action" ${busy ? 'disabled' : ''}>×</button>` : ''}
        ${explanation(stepReason(step), step.status === 'awaiting_choice' ? 'Resolve this choice to continue' : 'Review this action')}
      </li>`;
    }).join('');
    const canRun = (steps.some(step => ['pending','executing'].includes(step.status)) &&
      ['ready','running'].includes(queue?.status)) || Boolean(queueResponseAction());
    return `<section class="action-sequence" aria-label="Queued actions" data-sequence-id="${escapeHtml(queue?.sequence_id || '')}">
      <div class="sequence-heading"><span class="sequence-count" aria-label="${pending} queued actions">${pending}</span><span>queued</span></div>
      <ol class="sequence-cards" aria-label="Actions in execution order" ${rows ? 'tabindex="0"' : ''}>${rows}</ol>
      ${explanation(showQueueReason ? queueReason : '', queue?.status === 'paused' ? 'Resolve the decision to continue' : 'Review the stopped queue')}
      ${steps.some(step => step.status === 'pending') && ['failed','paused','stale'].includes(queue?.status)
        ? `<button type="button" class="sequence-review btn btn-secondary btn-sm" data-sequence-reconfirm ${busy ? 'disabled' : ''}>Review remaining actions</button>` : ''}
      <button type="button" class="sequence-execute btn btn-primary btn-sm" data-sequence-execute ${canRun ? 'data-sequence-run' : ''} ${busy || !canRun ? 'disabled' : ''}>Execute</button>
    </section>`;
  }

  // One lane owns the selected session's intentions, including requests from detached panels.
  // Capture scope before awaiting: late replies cannot retarget another campaign or session.
  const sequenceLanes = new Map();

  function queueContext(extraCurrent = () => true) {
    const campaignId = state.activeCampaignId, sessionId = state.activeSessionId;
    const activity = selectedActivity();
    const current = () => state.activeCampaignId === campaignId && state.activeSessionId === sessionId &&
      state.activeSessionRole === 'dm' && !window.__sylviaPanelContext &&
      selectedActivityMatches(activity) && extraCurrent();
    const check = () => {
      if (!campaignId || !sessionId || !current()) throw new Error('This campaign session or action panel is no longer selected.');
      if (selectedSessionIsWorking() || campaignDisplayPending() || state.panelCommitPending || pendingPanelCommand())
        throw new Error('Finish the current session operation before editing queued actions.');
    };
    check();
    return { campaignId, sessionId, current, check };
  }

  function sequenceLane(context) {
    const key = JSON.stringify([context.campaignId, context.sessionId]);
    if (!sequenceLanes.has(key)) sequenceLanes.set(key, { tail: Promise.resolve(), flushing: false, operations: new Map() });
    return sequenceLanes.get(key);
  }

  function inSequenceLane(context, work, { flush = false, operationId = null } = {}) {
    const lane = sequenceLane(context);
    if (operationId && lane.operations.has(operationId)) return lane.operations.get(operationId);
    lane.pending = (lane.pending || 0) + 1;
    if (lane.flushing) {
      lane.pending -= 1;
      return flush ? lane.flushPromise : Promise.reject(new Error('Queued actions are executing. Wait for this sequence to finish.'));
    }
    if (flush) { lane.flushing = true; state.sequencePending = lane; }
    refreshQueuePresentation();
    const task = lane.tail.then(async () => { context.check(); return work(context); });
    const result = task.finally(async () => {
      lane.pending -= 1;
      if (flush) {
        lane.flushing = false;
        if (state.sequencePending === lane) state.sequencePending = false;
      }
      // Narration starts a new activity object; publish the selected queue's idle state anyway.
      refreshQueuePresentation();
      if (context.current()) await renderRulesetPanels(context.campaignId, true);
    });
    lane.tail = result.catch(() => {});
    if (flush) lane.flushPromise = result;
    if (operationId) {
      lane.operations.set(operationId, result);
      void result.catch(() => { lane.operations.delete(operationId); });
    }
    return result;
  }

  async function readQueuedActions(context) {
    const queue = await state.bridge.action_sequence({session_id:context.sessionId});
    context.check();
    return queue;
  }

  function intendedSteps(queue) {
    return (queue?.steps || []).map(({step_id,source,selection}) => ({step_id,source,selection}));
  }

  async function composeQueuedActions(context, queue, steps) {
    const snapshot = await state.bridge.snapshot(context.campaignId);
    context.check();
    const result = await state.bridge.compose_action_sequence({
      campaign_id:context.campaignId, session_id:context.sessionId,
      sequence_id:queue?.sequence_id || `queue-${crypto.randomUUID()}`,
      expected_version:queue?.version || 0, expected_revision:snapshot.revision, steps,
    });
    context.check();
    state.actionSequence = result;
    refreshQueuePresentation();
    return result;
  }

  async function executeQueuedActions(context, queue, {describeCommittedPrefix = false} = {}) {
    if (!queue?.steps.some(step => ['pending','executing'].includes(step.status))) return queue;
    if (!['ready','running'].includes(queue.status))
      throw new Error('Review the stopped sequence and resolve its saved decision before executing.');
    context.check();
    const result = await state.bridge.execute_action_sequence({
      campaign_id:context.campaignId, session_id:context.sessionId, sequence_id:queue.sequence_id,
      expected_version:queue.version, confirmation:queue.confirmation,
    });
    context.check();
    state.actionSequence = result;
    refreshQueuePresentation();
    await refreshSelectedCampaign();
    context.check();
    // A turn boundary can pause the native executor after its final requested step.
    // A reaction, failed step or uncertain send must keep the suffix pending.
    const complete = result.status === 'completed' || (result.status === 'paused' &&
      result.steps.at(-1)?.response_policy === 'automatic');
    const confirmedPrefix = describeCommittedPrefix && result.status === 'failed' &&
      !result.steps.some(step => ['executing','awaiting_choice'].includes(step.status));
    const confirmedBoundary = describeCommittedPrefix && result.status === 'paused' &&
      result.pause_reason === 'Native action reached a control or turn boundary';
    if (((complete || confirmedBoundary) && result.steps.every(step => step.status === 'committed') || confirmedPrefix) &&
        result.committed_action_ids?.length) {
      await submitInput('', {playerAction:{campaignId:context.campaignId,sessionId:context.sessionId,
        actionId:result.committed_action_ids.at(-1),automatic:false}});
    }
    return result;
  }

  async function appendQueuedAction(command, {flush = false, current = () => true} = {}) {
    const context = queueContext(current);
    return inSequenceLane(context, async () => {
      let queue = await readQueuedActions(context);
      // A detached RPC timeout does not prove failure. Its stable operation identifies the intention.
      if (!queue?.steps.some(step => step.step_id === command.operation_id)) {
        if (queue?.status === 'completed' || automaticQueueCommitted(queue)) queue = null;
        const steps = intendedSteps(queue);
        steps.push({step_id:command.operation_id,source:command.source,selection:command.selection});
        queue = await composeQueuedActions(context, queue, steps);
      }
      return flush || queue.steps.at(-1)?.response_policy === 'automatic'
        ? executeQueuedActions(context, queue) : queue;
    }, {flush,operationId:command.operation_id});
  }

  function checkQueueVersion(queue, expected) {
    if (expected && ((queue?.sequence_id || null) !== expected.sequence_id || (queue?.version || 0) !== expected.version))
      throw new Error('The queued actions changed. Wait for the current queue before trying again.');
  }

  async function flushQueuedActions({current = () => true, expected = null, operationId = null, responseActionId = null} = {}) {
    const context = queueContext(current);
    return inSequenceLane(context, async () => {
      const queue = await readQueuedActions(context);
      checkQueueVersion(queue,expected);
      if (queue?.steps.some(step => ['pending','executing'].includes(step.status)) &&
          ['ready','running'].includes(queue.status))
        return executeQueuedActions(context,queue,{describeCommittedPrefix:true});
      if (!responseActionId) return executeQueuedActions(context,queue,{describeCommittedPrefix:true});
      const projection = await state.bridge.campaign_panels(context.campaignId);
      context.check();
      const offer = queueResponseAction(projection);
      if (!offer || offer.action_id !== responseActionId)
        throw new Error('The saved action response changed. Wait for the current game state before trying again.');
      await submitInput('', {playerAction:{campaignId:context.campaignId,sessionId:context.sessionId,
        actionId:offer.action_id,automatic:false}});
      return queue;
    }, {flush:true,operationId});
  }

  async function editQueuedActions(transform, {current = () => true, expected = null, operationId = null} = {}) {
    const context = queueContext(current);
    return inSequenceLane(context, async () => {
      const queue = await readQueuedActions(context);
      checkQueueVersion(queue,expected);
      return composeQueuedActions(context, queue, transform(intendedSteps(queue),queue));
    }, {operationId});
  }

  function queueOperation(operation, stepId, {current = () => true, expected = null, responseActionId = null} = {}) {
    const operationId = expected ? JSON.stringify(['queue-control',expected.sequence_id,expected.version,operation,stepId || null,responseActionId]) : null;
    const options = {current,expected,operationId,responseActionId};
    if (operation === 'execute') return flushQueuedActions(options);
    return editQueuedActions((steps,queue) => {
      if (operation === 'review') return steps;
      if (operation !== 'remove' || !queue?.steps.some(step => step.step_id === stepId && step.status === 'pending'))
        throw new Error('Only a pending queued action can be removed.');
      return steps.filter(step => step.step_id !== stepId);
    },options);
  }

  function wireSequence(container) {
    const campaignId = state.activeCampaignId, sessionId = state.activeSessionId;
    const view = state.panelProjection?.surfaces.find(surface => surface.id === ACTION_QUEUE_PANEL_ID)?.data;
    const queue = view?.queue || state.actionSequence;
    const expected = {sequence_id:queue?.sequence_id || null,version:queue?.version || 0};
    const responseActionId = queueResponseAction()?.action_id || null;
    const current = () => state.activeCampaignId === campaignId && state.activeSessionId === sessionId &&
      state.activeSessionRole === 'dm' && gamePanels().ownsPanel(ACTION_QUEUE_PANEL_ID);
    const run = async (operation, stepId) => {
      try {
        if (!current()) throw new Error('This queued-action panel is no longer current.');
        if (window.__sylviaPanelContext) {
          await state.bridge.queue_sequence_operation({campaign_id:campaignId,session_id:sessionId,
            surface_id:ACTION_QUEUE_PANEL_ID,operation,sequence_id:expected.sequence_id,
            expected_version:expected.version,...(operation === 'execute' ? {response_action_id:responseActionId} : {}),...(operation === 'remove' ? {step_id:stepId} : {})});
        } else await queueOperation(operation,stepId,{current,expected,responseActionId});
      } catch (error) { setInlineStatus(friendlyCommandError('Queued actions could not be updated', error),'panels'); }
    };
    container.querySelectorAll('[data-sequence-remove]').forEach(button =>
      button.addEventListener('click', () => void run('remove',button.dataset.sequenceRemove)));
    container.querySelector('[data-sequence-reconfirm]')?.addEventListener('click', () => void run('review'));
    container.querySelector('[data-sequence-run]')?.addEventListener('click', () => void run('execute'));
  }

  async function renderRulesetPanels(campaignId, force = false, loadId = null) {
    const requestId = ++state.panelProjectionRequestId;
    if (!campaignId || !state.bridge || !state.bridge.campaign_panels) {
      renderRulesetPanelProjection(null);
      return true;
    }
    const selectedSession = state.activeSessionId;
    try {
      const projection = await state.bridge.campaign_panels(campaignId, gamePanels().effectRevision(), loadId);
      const sequence = selectedSession && state.activeSessionRole === 'dm' && state.bridge.action_sequence && !window.__sylviaPanelContext
        ? await state.bridge.action_sequence({session_id:selectedSession}) : null;

      if (requestId !== state.panelProjectionRequestId ||
          state.activeCampaignId !== campaignId || state.activeSessionId !== selectedSession) return;
      state.actionSequence = sequence;
      if (state.panelProjection?.campaign_id === campaignId &&
          projection.revision < state.panelProjection.revision) return;
      // Finishing a response changes control availability without changing saved game state.
      const key = JSON.stringify([campaignId, selectedSession, projection?.revision,
        selectedSessionIsWorking(), Boolean(state.sequencePending), sequence?.sequence_id, sequence?.version, sequence?.status, projection?.player_control, projection?.response_action,
        projection?.surfaces.map((surface) => [surface.id,
          surface.actions?.map((action) => [action.id, action.available])]),
      ]);
      if (!force && state.panelProjectionKey === key) return;
      state.panelProjectionKey = key;
      renderRulesetPanelProjection(projection);
      return true;
    } catch (e) {
      if (requestId !== state.panelProjectionRequestId ||
          state.activeCampaignId !== campaignId || state.activeSessionId !== selectedSession) return;
      state.panelProjectionKey = null;
      if (!loadId) renderRulesetPanelProjection(null);
      return false;
    }
  }

  function selectedCampaignOpening() {
    const opening = state.campaignOpening;
    return opening && state.view === 'session' && state.activeCampaignId === opening.campaignId &&
      state.activeSessionId === opening.sessionId ? opening : null;
  }

  function cancelCampaignOpening() {
    const opening = state.campaignOpening;
    state.campaignOpening = null;
    if (opening?.id && state.bridge?.cancel_display_load)
      void state.bridge.cancel_display_load(opening.id).catch(() => {});
  }

  // Display readiness never interrupts a saved turn; it only gates controls using unfinished data.
  function campaignDisplayPending() {
    const opening = selectedCampaignOpening();
    return Boolean(opening && ['history', 'snapshot', 'panels'].some((part) => opening[part] !== 'ready'));
  }

  function updateCampaignOpening(opening, part, result) {
    if (!opening || selectedCampaignOpening() !== opening) return;
    opening[part] = result;
    const failed = ['history', 'snapshot', 'panels'].filter((key) => opening[key] === 'error');
    const loading = ['history', 'snapshot', 'panels'].filter((key) => opening[key] === 'loading');
    const notice = $('campaign-loading-status');
    notice.hidden = !failed.length && !loading.length;
    notice.innerHTML = failed.length ? 'Some campaign data could not be loaded. Saved history is kept. ' +
      '<button type="button" class="btn btn-secondary btn-sm" data-retry-display>Retry loading</button>' :
      loading.length ? 'Loading game state and panels…' : '';
    notice.querySelector('[data-retry-display]')?.addEventListener('click', () => {
      if (selectedCampaignOpening() === opening) void openCampaign(opening.campaignId, opening.sessionId);
    });
    refreshSessionInputAdmission();
  }

  // ---- Open a campaign session and render its authoritative projection ----
  async function openCampaign(campaignId, sessionId = null) {
    state.displayHistory = null;
    const retryDisplay = campaignDisplayPending();
    cancelCampaignOpening();
    const previousView = state.view;
    const previousCampaignId = state.activeCampaignId, previousSessionId = state.activeSessionId;
    saveSelectedSessionDraft();
    hideConstructionPanel(false);
    showSessionView();
    clearModelGuidanceWarning();
    setWebResearchPermission(false);
    state.activeCampaignId = campaignId;
    const indexEntry = state.campaigns.find((campaign) => campaign.campaign_id === campaignId);
    const sessions = indexEntry && Array.isArray(indexEntry.sessions) ? indexEntry.sessions : [];
    const selectedSession = sessions.find((session) => session.session_id === sessionId) ||
      sessions.find((session) => session.session_id === state.activeSessionId) ||
      sessions.find((session) => indexEntry && session.session_id === indexEntry.session_id) || null;
    state.activeSessionId = selectedSession ? selectedSession.session_id : indexEntry && indexEntry.session_id;
    state.activeSessionRole = selectedSession ? selectedSession.role : indexEntry && indexEntry.writer;
    if (previousCampaignId !== campaignId || previousSessionId !== state.activeSessionId || state.activeSessionRole !== 'dm') {
      gamePanels().leave();
      state.panelProjectionKey = null;
      renderRulesetPanelProjection(null);
    }
    const openingSelection = previousView !== 'session' || previousCampaignId !== campaignId ||
      previousSessionId !== state.activeSessionId;
    const opening = openingSelection || retryDisplay ? {
      id: crypto.randomUUID(), campaignId, sessionId: state.activeSessionId,
      history: 'loading', snapshot: 'loading', panels: state.activeSessionRole === 'dm' ? 'loading' : 'ready',
    } : null;
    state.campaignOpening = opening;
    $('campaign-loading-status').hidden = true;
    if (openingSelection) {
      const openingName = indexEntry?.display_name || 'Campaign';
      $('session-title').textContent = openingName;
      $('ctx-campaign').textContent = openingName;
      updateChronology(
        $('chronology'),
        '<div class="empty-state" data-chronology-key="opening-campaign">' +
          '<div class="empty-icon">◇</div>' +
          `<div>Opening ${escapeHtml(openingName)}…</div>` +
          '<div class="muted">Saved history and game panels are loading.</div>' +
        '</div>',
        () => {},
      );
    }
    ensureSessionActivity({
      sessionId: state.activeSessionId,
      sessionRole: state.activeSessionRole,
      campaignId,
    });
    const openingSessionId = state.activeSessionId;
    const stillSelected = () => state.view === 'session' && state.activeCampaignId === campaignId &&
      state.activeSessionId === openingSessionId && (!opening || state.campaignOpening === opening);
    restoreSelectedSessionDraft();
    renderPersistedModelGuidance(selectedSession);
    state.activeSnapshot = null;
    refreshSessionInputAdmission();
    updateLifecycleControls(null, null);
    if (opening && state.activeSessionRole === 'dm')
      renderRulesetPanelProjection(gamePanels().loading(campaignId, openingSessionId));
    $('export-campaign-btn').disabled = !indexEntry;
    renderBuilderChatRail();
    renderSidebarState();
    $('builder-input-options').hidden = state.activeSessionRole !== 'builder';
    updateCampaignSetupControl();
    updateModelSwitchButtons();
    // Start the small state read first, then let conversation and panels finish independently.
    const loadState = async () => {
      try {
        const snap = await state.bridge.snapshot(campaignId, opening?.id);
        if (!stillSelected()) return;
        if (!snap) throw new Error('Snapshot unavailable');
        state.activeSnapshot = snap;
        const displayName = selectedIndexEntry()?.display_name || snap.display_name;
        $('ctx-campaign').textContent = displayName || '—';
        $('ctx-lifecycle').textContent = (snap.lifecycle && snap.lifecycle.value) || snap.lifecycle || '—';
        $('ctx-writer').textContent = (snap.writer && snap.writer.value) || snap.writer || '—';
        $('session-title').textContent = displayName || 'Campaign';
        updateLifecycleControls(indexEntry, snap);
        updateCampaignOpening(opening, 'snapshot', 'ready');
      } catch (e) {
        if (!stillSelected()) return;
        updateCampaignOpening(opening, 'snapshot', 'error');
        if (!opening) setInlineStatus('Snapshot unavailable');
        updateLifecycleControls(null, null);
        return;
      }
      if (!stillSelected()) return;
      if (state.activeSessionRole === 'dm') {
        const loaded = await renderRulesetPanels(campaignId, true, opening?.id);
        if (!stillSelected()) return;
        updateCampaignOpening(opening, 'panels', loaded ? 'ready' : 'error');
        if (!loaded && opening)
          renderRulesetPanelProjection(gamePanels().loading(campaignId, openingSessionId, 'error'));
      }
    };
    await Promise.all([
      loadState(),
      renderPlayerProjection(campaignId, openingSessionId, { loadPanels: false, opening }),
    ]);
    if (!stillSelected()) return;
    const canSubmit = refreshSessionInputAdmission();
    const builderCanSubmit = canSubmit && state.activeSessionRole === 'builder';
    $('attach-builder-sources-btn').disabled = !builderCanSubmit || selectedSessionIsWorking();
    $('allow-public-web').disabled = !builderCanSubmit || selectedSessionIsWorking();
    if (!campaignDisplayPending()) {
      opening?.refreshHistory?.();
      const activity = selectedActivity();
      setInlineStatus(activityIsWorking(activity)
        ? `${state.activeSessionRole === 'builder' ? 'Builder' : 'Game Master'} is working…`
        : activity && ['interrupted', 'stopped'].includes(activity.phase)
          ? activity.terminalLabel || 'Generation was interrupted' : '');
      if (state.panelProjection) renderRulesetPanelProjection(state.panelProjection);
    }
    if (indexEntry && indexEntry.availability === 'draft' &&
        state.activeSessionRole === 'builder' && !state.activeCreationSubflow) {
      try {
        await resumeGuidedCampaignDraft(campaignId, state.activeSessionId);
      } catch (e) {
        if (!stillSelected()) return;
        setInlineStatus('Campaign Draft could not be resumed');
      }
    }
  }

  function updateLifecycleControls(indexEntry, snapshot) {
    const lifecycle = snapshot && ((snapshot.lifecycle && snapshot.lifecycle.value) || snapshot.lifecycle);
    const selected = Boolean(snapshot);
    const session = selectedSessionEntry(indexEntry);
    const canRename = selected && lifecycle === 'active';
    const canStop = selected && Boolean(session) && session.status === 'active';
    const canResume = selected && lifecycle === 'active' && Boolean(session) &&
      session.status === 'stopped';
    const canFinalize = indexEntry && indexEntry.availability !== 'draft' && selected &&
      lifecycle === 'active' && Boolean(session) &&
      session.role === 'builder' && session.status === 'active';
    $('rename-campaign-btn').disabled = !canRename;
    for (const [controlId, available] of [
      ['stop-session-btn', canStop],
      ['resume-session-btn', canResume],
      ['finalize-construction-btn', canFinalize],
    ]) {
      const control = $(controlId);
      control.disabled = !available;
      control.hidden = !available;
    }
    $('lifecycle-actions').hidden = !(canResume || canFinalize);
    $('archive-campaign-btn').disabled = !selected || lifecycle !== 'active';
    $('restore-campaign-btn').disabled = !selected || !['archived', 'tombstoned'].includes(lifecycle);
    $('trash-campaign-btn').disabled = !selected || !['active', 'archived'].includes(lifecycle);
    $('purge-campaign-btn').disabled = !selected || lifecycle !== 'tombstoned';
    $('settings-campaign-context').textContent = selected
      ? `Selected: ${snapshot.display_name || snapshot.campaign_id} · ${lifecycle}`
      : 'Select a campaign to export, rename, archive, or delete.';
  }

  function clearCampaignFrame() {
    state.activeSessionRole = null;
    if ($('compact-context-btn')) $('compact-context-btn').disabled = true;
    clearModelGuidanceWarning();
    renderPersistedModelGuidance(null);
    $('ctx-campaign').textContent = '—';
    $('ctx-lifecycle').textContent = '—';
    $('ctx-writer').textContent = '—';
    $('session-title').textContent = 'No campaign selected';
    $('player-input').disabled = true;
    $('send-btn').disabled = true;
    $('builder-input-options').hidden = true;
    updateCampaignSetupControl();
    $('attach-builder-sources-btn').disabled = true;
    $('allow-public-web').disabled = true;
    setWebResearchPermission(false);
    $('export-campaign-btn').disabled = true;
    updateLifecycleControls(null, null);
    $('settings-campaign-context').textContent =
      'Select a campaign to export, rename, archive, or delete.';
    updateModelSwitchButtons();
    state.panelProjectionKey = null;
    renderRulesetPanelProjection(null);
  }

  function renderSessionModelState(context) {
    const modelState = context && context.model_state;
    state.sessionReasoning = { sessionId: state.activeSessionId,
      value: context?.reasoning_state || null, modelState };
    renderReasoningPicker();
    const current = modelState && modelState.current;
    if (!current) return;
    const index = state.modelChoices.findIndex((choice) =>
      choice.provider_id === current.provider_id && choice.model_id === current.model_id
    );
    $('chat-model-choice').value = index >= 0 ? String(index) : '';
    renderChatModelPicker();
    updateModelSwitchButtons();
  }

  // Update only chronology cards whose authoritative projection changed.
  // Keeping stable card elements prevents streaming polls from replaying every
  // entrance animation or resetting the player's expanded cards and scroll.
  function updateChronology(container, html, wireControls) {
    if (container.dataset.chronologyHtml === html || container.innerHTML === html) return false;

    const previousScrollTop = container.scrollTop;
    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    const followLatest = distanceFromBottom <= 48;
    const expandedKeys = new Set(
      Array.from(container.querySelectorAll('.story-card.expanded[data-chronology-key]'))
        .map((card) => card.dataset.chronologyKey)
        .filter(Boolean)
    );
    const openDisclosures = new Set(
      Array.from(container.querySelectorAll('details[data-disclosure-key]'))
        .filter((element) => element.open).map((element) => element.dataset.disclosureKey)
    );
    const template = document.createElement('template');
    template.innerHTML = html;
    const nextCards = Array.from(template.content.children);

    nextCards.forEach((nextCard, index) => {
      const currentCard = container.children[index];
      if (!currentCard) {
        container.appendChild(nextCard);
        return;
      }
      const currentKey = currentCard.dataset.chronologyKey || '';
      const nextKey = nextCard.dataset.chronologyKey || '';
      if (currentKey && currentKey === nextKey) {
        if (currentCard.innerHTML !== nextCard.innerHTML) {
          currentCard.className = nextCard.className;
          currentCard.innerHTML = nextCard.innerHTML;
        }
        return;
      }
      if (currentCard.outerHTML !== nextCard.outerHTML) currentCard.replaceWith(nextCard);
    });
    while (container.children.length > nextCards.length) {
      container.lastElementChild.remove();
    }
    container.querySelectorAll('[data-chronology-key]').forEach((card) => {
      if (expandedKeys.has(card.dataset.chronologyKey)) card.classList.add('expanded');
    });
    container.dataset.chronologyHtml = html;
    container.querySelectorAll('details[data-disclosure-key]').forEach((element) => {
      element.open = openDisclosures.has(element.dataset.disclosureKey);
    });
    wireControls();
    wireThinkingInspection(container);
    if (followLatest) container.scrollTop = container.scrollHeight;
    else container.scrollTop = previousScrollTop;
    return true;
  }

  function chronologyKeyAttribute(key) {
    return ` data-chronology-key="${escapeHtml(String(key))}"`;
  }

  function replaceClickHandler(element, handler) {
    if (element.sylviaClickHandler) {
      element.removeEventListener('click', element.sylviaClickHandler);
    }
    element.sylviaClickHandler = handler;
    element.addEventListener('click', handler);
  }

  function readableToolName(name) {
    return String(name || 'ruleset').replace(/^tool_\d+_/, '').replaceAll('_', ' ')
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  // Show bounded validator guidance, never submitted values, source text or tool arguments.
  function participantRejectionDetail(result) {
    const text = (value) => typeof value === 'string' ? value.trim().slice(0, 500) : '';
    const diagnostics = Array.isArray(result.validation_errors) ? result.validation_errors : [];
    const issues = Array.isArray(result.validation_issues) ? result.validation_issues : [];
    const lines = [...diagnostics, ...issues].slice(0, 4).map((issue) => {
      if (!issue || typeof issue !== 'object') return '';
      const reason = text(issue.message) || panelIdentifierLabel(text(issue.kind));
      const guidance = text(issue.guidance) || text(issue.correction) || (text(issue.expected_type)
        ? `Expected ${text(issue.expected_type)}.` : '');
      const description = [reason, guidance].filter(Boolean).join(' ');
      return description ? [text(issue.path), description].filter(Boolean).join(': ') : '';
    }).filter(Boolean);
    if (diagnostics.length + issues.length > 4) lines.push('Further fields also need correction.');
    return [text(result.message), ...lines].filter(Boolean).join('\n') ||
      'This operation did not complete. Builder can check the draft and try again.';
  }

  // Read only the bounded display fields from a saved ask_question result.
  function builderQuestionFromEvidence(content, messageKey, messageIndex) {
    if (String(content && content.tool_name || '') !== 'ask_question') return null;
    const result = content && content.result;
    if (!result || typeof result !== 'object' || result.success !== true) return null;
    const widget = result.widget;
    const data = widget && typeof widget === 'object' && widget.data;
    const type = String(widget && widget.type || '');
    if (!data || typeof data !== 'object' ||
        !['select', 'cascade', 'text', 'confirm'].includes(type) ||
        typeof data.id !== 'string' || !data.id.trim() ||
        typeof data.question !== 'string' || !data.question.trim()) return null;
    let options = [];
    const normalizedOptions = (rawOptions) => rawOptions.map((option) => {
        if (typeof option === 'string' && option.length) {
          return { value: option, label: option };
        }
        if (!option || typeof option !== 'object' || typeof option.value !== 'string' ||
            !option.value.length || typeof option.label !== 'string' || !option.label.length) {
          return null;
        }
        return { value: option.value, label: option.label };
      }).filter(Boolean);
    if (type === 'select' || type === 'cascade') {
      if (!Array.isArray(data.options)) return null;
      options = normalizedOptions(data.options);
      if (!options.length) return null;
    } else if (type === 'confirm') {
      options = Array.isArray(data.options) && data.options.length
        ? normalizedOptions(data.options)
        : [{ value: 'Yes', label: 'Yes' }, { value: 'No', label: 'No' }];
      if (!options.length) return null;
    }
    return {
      id: data.id,
      question: data.question,
      type,
      options,
      awaiting: result.status === 'awaiting_answer',
      key: String(messageKey || content.tool_call_id || `${messageIndex}-${data.id}`),
      toolCallId: content.tool_call_id,
      index: messageIndex,
    };
  }

  function builderQuestionDraft(sessionId, questionKey) {
    return state.builderQuestionDrafts.get(sessionId)?.get(questionKey) || {};
  }

  function builderQuestionCardMarkup(question, key, {
    sessionId, groupId, active, busy,
  }) {
    if (question.answer) {
      return `<fieldset class="story-card builder-question-card"${key}>` +
        `<legend class="story-header">Builder question</legend>` +
        `<div class="builder-question-text">${escapeHtml(question.question)}</div>` +
        `<div class="story-text builder-question-answer" aria-label="Answer">` +
        `${escapeHtml(question.answer.label)}</div>` +
        `<div class="builder-question-note">Answered</div></fieldset>`;
    }
    const disabled = !active || busy;
    const controlId = `builder-question-${question.index}`;
    const labelId = `${controlId}-label`;
    const formAttribute = active ? ` form="${escapeHtml(groupId)}"` : '';
    const controlAttributes =
      ` data-builder-question-control data-builder-question-key="${escapeHtml(question.key)}"` +
      ` data-builder-question-type="${escapeHtml(question.type)}"` +
      ` data-builder-question-text="${escapeHtml(question.question)}"` +
      ` data-builder-question-order="${question.index}"` +
      `${formAttribute} aria-labelledby="${escapeHtml(labelId)}"` +
      (disabled ? ' disabled' : '');
    let controls = '';
    if (question.type === 'text') {
      controls = `<textarea id="${escapeHtml(controlId)}" rows="3"` +
        `${controlAttributes} placeholder="Write your answer"></textarea>`;
    } else {
      const options = question.options.map((option) =>
        `<option value="${escapeHtml(option.value)}">` +
        `${escapeHtml(option.label)}</option>`
      ).join('');
      const customId = `${controlId}-custom`;
      controls = `<select id="${escapeHtml(controlId)}"${controlAttributes}>` +
        `<option value="">Choose an answer</option>${options}</select>` +
        `<label for="${escapeHtml(customId)}" class="builder-question-custom-label">` +
        `Or write your own answer</label>` +
        `<input id="${escapeHtml(customId)}" type="text" value=""` +
        ` data-builder-question-control data-builder-question-key="${escapeHtml(question.key)}"` +
        ` data-builder-question-type="custom"${formAttribute}` +
        ` aria-label="Your own answer for ${escapeHtml(question.question)}"` +
        (disabled ? ' disabled' : '') + `>`;
    }
    const note = active ? '' :
      '<div class="builder-question-note">A later reply closed these controls.</div>';
    return `<fieldset class="story-card builder-question-card"${key}>` +
      `<legend class="story-header">Builder question</legend>` +
      `<div class="builder-question-text" id="${escapeHtml(labelId)}">` +
      `${escapeHtml(question.question)}</div><div class="builder-question-controls">` +
      `${controls}</div>${note}</fieldset>`;
  }

  function builderQuestionFormMarkup(sessionId, groupId, busy) {
    return `<form id="${escapeHtml(groupId)}" class="builder-question-submit"` +
      ` data-builder-question-form data-builder-question-session="${escapeHtml(sessionId)}">` +
      `<button type="submit" class="btn btn-primary btn-sm"${busy ? ' disabled' : ''}>` +
      `Submit answers</button></form>`;
  }

  function rememberBuilderQuestionControl(sessionId, control) {
    if (!sessionId || !control || !control.dataset.builderQuestionKey) return;
    let sessionDraft = state.builderQuestionDrafts.get(sessionId);
    if (!sessionDraft) {
      sessionDraft = new Map();
      state.builderQuestionDrafts.set(sessionId, sessionDraft);
    }
    const key = control.dataset.builderQuestionKey;
    const draft = { ...(sessionDraft.get(key) || {}) };
    if (control.dataset.builderQuestionType === 'custom') draft.custom = control.value;
    else if (control.dataset.builderQuestionType === 'text') draft.text = control.value;
    else draft.selection = control.value;
    sessionDraft.set(key, draft);
  }

  function restoreBuilderQuestionControl(sessionId, control) {
    const draft = builderQuestionDraft(sessionId, control.dataset.builderQuestionKey);
    const type = control.dataset.builderQuestionType;
    const value = type === 'custom' ? draft.custom : type === 'text' ? draft.text : draft.selection;
    if (typeof value === 'string' && control.value !== value) control.value = value;
  }

  function builderQuestionAnswerMessage(answers) {
    return 'Here are my answers:\n\n' + answers.map((answer, index) =>
      `${index + 1}. ${answer.question}\nAnswer: ${answer.answer}`
    ).join('\n\n');
  }

  function selectedBuilderQuestionAnswer(control) {
    const value = String(control.value || '');
    if (!value) return '';
    const selected = Array.from(control.selectedOptions || [])[0];
    const label = String(selected?.textContent || '') || value;
    const duplicateLabels = Array.from(control.options || []).filter((option) =>
      String(option.value || '').length && String(option.textContent || '') === label
    ).length;
    return duplicateLabels > 1 && value && value !== label ? `${label} (${value})` : label;
  }

  // Keep genuine form input linked to the saved questions in this exact Builder session.
  function wireBuilderQuestionForms(container) {
    const controls = Array.from(container.querySelectorAll('[data-builder-question-control]'));
    container.querySelectorAll('[data-builder-question-form]').forEach((form) => {
      const sessionId = form.dataset.builderQuestionSession;
      const formControls = controls.filter((control) => control.getAttribute('form') === form.id);
      formControls.forEach((control) => {
        restoreBuilderQuestionControl(sessionId, control);
        const handler = () => rememberBuilderQuestionControl(sessionId, control);
        if (control.sylviaQuestionDraftHandler) {
          control.removeEventListener('input', control.sylviaQuestionDraftHandler);
          control.removeEventListener('change', control.sylviaQuestionDraftHandler);
        }
        control.sylviaQuestionDraftHandler = handler;
        control.addEventListener('input', handler);
        control.addEventListener('change', handler);
      });
      const submitHandler = (event) => {
        event.preventDefault();
        const activity = selectedActivity();
        const submitButton = form.querySelector('button[type="submit"]');
        if (!sessionId || state.activeSessionId !== sessionId ||
            state.activeSessionRole !== 'builder' || !selectedActivityMatches(activity) ||
            activityIsWorking(activity) || state.deletingBuilderChats.has(sessionId) ||
            !state.bridge?.submit_session_input || submitButton?.disabled) return;
        const primaryControls = formControls.filter((control) =>
          control.dataset.builderQuestionType !== 'custom'
        ).sort((left, right) =>
          Number(left.dataset.builderQuestionOrder) - Number(right.dataset.builderQuestionOrder)
        );
        const answers = [];
        for (const control of primaryControls) {
          const custom = formControls.find((candidate) =>
            candidate.dataset.builderQuestionKey === control.dataset.builderQuestionKey &&
            candidate.dataset.builderQuestionType === 'custom'
          );
          const customAnswer = String(custom?.value || '');
          const isCustom = Boolean(customAnswer.trim());
          const value = isCustom ? customAnswer : String(control.value || '');
          const answer = isCustom ? customAnswer : (control.dataset.builderQuestionType === 'text'
            ? value
            : selectedBuilderQuestionAnswer(control));
          if (!answer.trim()) {
            control.focus();
            selectedStatus(activity, 'Answer each question before submitting');
            return;
          }
          answers.push({
            question: control.dataset.builderQuestionText || 'Builder question',
            answer,
            question_message_id: control.dataset.builderQuestionKey,
            value,
            custom: isCustom,
          });
        }
        if (!answers.length) return;
        if (submitButton) submitButton.disabled = true;
        formControls.forEach((control) => { control.disabled = true; });
        void submitInput(builderQuestionAnswerMessage(answers), {
          builderQuestionAnswers: answers.map(({ question_message_id, value, custom }) =>
            ({ question_message_id, value, custom })),
        });
      };
      if (form.sylviaQuestionSubmitHandler) {
        form.removeEventListener('submit', form.sylviaQuestionSubmitHandler);
      }
      form.sylviaQuestionSubmitHandler = submitHandler;
      form.addEventListener('submit', submitHandler);
    });
  }

  function builderQuestionFallbackMarkup(content, key) {
    const result = content && content.result && typeof content.result === 'object'
      ? content.result : {};
    const complete = result.status === 'completed' || result.widget?.type === 'complete';
    const awaiting = result.status === 'awaiting_answer';
    const question = result.widget?.data && typeof result.widget.data.question === 'string'
      ? result.widget.data.question : '';
    return applicationResultMarkup({
      title: 'Builder question',
      summary: question || (complete ? 'Question step completed' :
        awaiting ? 'Reply in the message field' : 'Question unavailable'),
      detail: complete ? 'No further answer is requested.' : 'You can continue with an ordinary message.',
    }, key);
  }

  function toolPresentation(content) {
    const toolName = String(content && content.tool_name || '');
    const result = content && content.result && typeof content.result === 'object'
      ? content.result : content || {};
    const tests = toolName === 'test_ruleset_draft' ? result :
      toolName === 'publish_ruleset_candidate' ? result.tests : null;
    // Failed assertions are a completed test report, not a failed tool execution.
    const failedTests = tests?.status === 'failed' && Array.isArray(tests.scenarios);
    if (['failed', 'error'].includes(result.status || content && content.status) && !failedTests) {
      return {
        feedback: true,
        summary: 'Sylvia could not complete this action. Try again.',
      };
    }
    if (tests && typeof tests === 'object') {
      const scenarios = Array.isArray(tests.scenarios) ? tests.scenarios : [];
      const passed = scenarios.filter((item) => item.status === 'passed').length;
      const status = String(tests.status || 'error');
      const labels = {
        passed: 'Ruleset tests passed', failed: 'Ruleset tests failed',
        incomplete_coverage: 'More Ruleset tests needed', missing_tests: 'No saved Ruleset tests',
        cancelled: 'Ruleset tests cancelled', timed_out: 'Ruleset tests timed out',
      };
      const lines = [];
      const value = (item) => JSON.stringify(item)?.slice(0, 2000) ?? 'unavailable';
      for (const scenario of scenarios) {
        for (const step of scenario.steps || []) {
          for (const failure of step.failures || []) {
            lines.push(`${scenario.id} · Step ${Number(step.step) + 1}: ` +
              (failure.message || `${failure.subject} ${value(failure.path)}\n` +
                `Expected: ${value(failure.expected)}\nActual: ` +
                (failure.missing ? 'missing' : value(failure.actual))));
          }
        }
        for (const failure of scenario.failures || []) {
          lines.push(`${scenario.id}: ${failure.message || 'Scenario failed'}`);
        }
      }
      const coverage = tests.coverage || {};
      for (const [field, label] of [
        ['missing_actions', 'Actions needing outcome checks'],
        ['missing_rejections', 'Actions needing rejection checks'],
        ['missing_preconditions', 'Actions needing precondition checks'],
        ['unproven_claims', 'Unproven coverage claims'],
      ]) {
        if (coverage[field]?.length) lines.push(`${label}: ${coverage[field].join(', ')}`);
      }
      if (coverage.missing_interaction) lines.push('Add a scenario checking actions used together.');
      if (coverage.missing_handoff) lines.push('Check narration being held and released across a GM handoff.');
      if (tests.message) lines.push(String(tests.message));
      return {
        title: labels[status] || 'Ruleset tests unavailable',
        summary: `${passed}/${scenarios.length} scenarios passed`,
        detail: toolName === 'publish_ruleset_candidate' && result.success === true
          ? 'The tested Ruleset was saved to the library.'
          : 'The draft is preserved. Builder can correct it and run the tests again.',
        testDetails: lines.slice(0, 50).join('\n\n') +
          (lines.length > 50 ? '\n\nFurther details are available to Builder.' : ''),
      };
    }
    if (content && content.kind === 'gm_turn_activity') {
      const opening = content.activity === 'campaign_opened';
      return {
        title: opening ? 'Campaign opened' : 'Game Master',
        summary: opening ? 'Starting scene prepared' : 'Turn resolved',
        detail: opening
          ? 'Sylvia checked campaign context and continuity. Private GM notes remain hidden.'
          : 'Sylvia resolved the turn and checked campaign continuity. Private GM notes remain hidden; Ruleset outcomes appear separately.',
      };
    }
    if (toolName === 'publish_campaign_plan') {
      const rejected = result.success === false || result.status === 'rejected';
      const savedCopy = savedPlanCopyLabel(result.library_artifact);
      return {
        title: rejected ? 'Campaign Plan rejected' : 'Campaign Plan ready',
        summary: rejected
          ? (result.message || result.code || 'The draft did not pass validation')
          : [result.title || 'Structured Campaign Plan validated and saved', savedCopy]
            .filter(Boolean).join(' · '),
        detail: rejected
          ? 'Builder can correct the retained draft and try again.'
          : 'The readable plan and its structured artifact are ready to use.',
        preview: rejected ? '' : String(result.readable_preview || ''),
        actions: rejected ? [] : ['preview_campaign_plan', 'use_campaign_plan'],
        campaignPlanId: String(result.library_artifact?.id || ''),
      };
    }
    if (toolName === 'discover_campaign_content') {
      const items = Array.isArray(result.items) ? result.items : [];
      const plans = items.filter((item) => item && item.kind === 'campaign_plan');
      const directSetup = plans.length === 1 ? plans[0] : null;
      return {
        title: 'Campaign content searched',
        summary: plans.length
          ? plans.map((item) => item.title || item.id).join(', ')
          : (items.length ? `${items.length} matching artifacts` : 'No matching content'),
        detail: directSetup
          ? 'This Campaign Plan is ready for deterministic app setup.'
          : 'Builder searched the content available in this Sylvia GM installation.',
        actions: directSetup ? ['configure_campaign'] : [],
        campaignPlanId: directSetup ? String(directSetup.id || '') : '',
        setupId: directSetup ? String(directSetup.setup_id || '') : '',
      };
    }
    if (toolName === 'prepare_campaign_setup') {
      const rejected = result.success === false || result.status === 'rejected';
      return {
        title: rejected ? 'Campaign setup unavailable' : 'Campaign setup ready',
        summary: rejected
          ? (result.code || 'The selected Campaign Plan is unavailable')
          : (result.title || result.campaign_plan_id || 'Selected Campaign Plan'),
        detail: rejected
          ? 'Choose a Campaign Plan returned by content discovery.'
          : 'Confirm the player choices in app setup. Gameplay has not started.',
        actions: rejected ? [] : ['configure_campaign'],
        campaignPlanId: rejected ? '' : String(result.campaign_plan_id || ''),
        setupId: rejected ? '' : String(result.setup_id || ''),
      };
    }
    // Application-owned Builder results use the same presentation as Ruleset cards.
    // Read only explicit saved receipt fields; source/profile bodies remain private.
    if (toolName === 'complete_creation_subflow' &&
        ['ready', 'blocked'].includes(result.status) && Array.isArray(result.boundary_conflicts)) {
      const ready = result.success === true && result.status === 'ready' &&
        result.boundary_conflicts.length === 0;
      return {
        title: ready ? 'Creation ready for review' : 'Creation needs changes',
        summary: typeof result.summary === 'string' ? result.summary : '',
        detail: ready ? 'Review and select the saved artifact to continue.' :
          result.boundary_conflicts.filter((item) => typeof item === 'string')
            .slice(0, 16).join('\n'),
      };
    }
    if (result.success === true) {
      if (toolName === 'check_character_profile_draft' && result.valid === true &&
          result.published === false) return {
        title: 'Character Profile checked', summary: 'Draft is valid',
        detail: 'Ready to save as an unbound source.',
      };
      if (['publish_character_profile', 'import_character_card'].includes(toolName) &&
          result.published === true) return {
        title: toolName === 'import_character_card' ? 'Character Card imported' : 'Character Profile saved',
        summary: result.title || result.name || 'Reusable source saved',
        detail: 'Adapt this source to the selected Ruleset before campaign use.',
      };
      if (['open_character_profile', 'open_character_draft'].includes(toolName) && result.published === false) return {
        title: toolName === 'open_character_profile' ? 'Character Profile draft opened' : 'Character draft opened', summary: result.path || 'Workspace copy opened',
        detail: 'The saved source was copied to this Builder workspace.',
      };
      if (toolName === 'preview_character_card_import' && result.published === false &&
          result.conversion_preview && typeof result.conversion_preview === 'object') return {
        title: 'Character Card preview', summary: result.profile_preview?.name || 'Source inspected',
        detail: ['imported', 'transformed', 'preserved_only', 'unsupported', 'omitted']
          .filter((category) => Array.isArray(result.conversion_preview[category]))
          .map((category) => `${category.replaceAll('_', ' ')}: ${result.conversion_preview[category].length}`)
          .join(' · ') + '. Review the conversion before importing.',
      };
      if (toolName === 'check_participant_draft' && result.status === 'valid_draft' &&
          result.published === false) return {
        title: 'Character draft checked', summary: 'Draft is valid for the selected Ruleset',
        detail: 'The computed starting values are ready for publication.',
      };
      if (toolName === 'publish_participant_artifact' && result.status === 'published' &&
          ['companion', 'player_character'].includes(result.kind)) return {
        title: result.kind === 'companion' ? 'Companion saved' : 'Player Character saved',
        summary: result.title || result.name || 'Reusable character saved',
        detail: result.campaign_ready === false ?
          'Saved source needs adaptation before campaign use.' : 'Available for selection in campaign setup.',
      };
      if (toolName === 'check_campaign_setting_draft' && result.status === 'valid_draft') return {
        title: 'Setting draft checked', summary: result.title || 'Draft is valid',
        detail: 'Ready to publish as a reusable Setting.',
      };
      if (toolName === 'publish_campaign_setting') return {
        title: 'Setting saved', summary: result.title || 'Reusable Setting saved',
        detail: 'Available for selection in campaign setup.',
      };
    }
    // File and package summaries must not turn a rejected operation into a success claim.
    if ((result.success === false || result.status === 'rejected') &&
        ['read_file', 'write_file', 'search_files', 'publish_ruleset_candidate'].includes(toolName)) {
      return {
        feedback: true,
        summary: 'Builder could not use this request. Check it and try again.',
      };
    }
    if (toolName === 'publish_ruleset_candidate') {
      return {
        title: 'Ruleset package ready',
        summary: 'Ruleset candidate validated',
        detail: 'The package is available for selection. It was not activated automatically.',
      };
    }
    if (toolName === 'write_file') {
      return {
        title: 'File saved',
        summary: result.path || 'Builder workspace updated',
        detail: 'The file remains available in this Builder chat workspace.',
      };
    }
    if (toolName === 'read_file') {
      return {
        title: 'File reviewed',
        summary: result.path || 'Builder source read',
        detail: 'Source text is omitted from ordinary chat history for privacy.',
      };
    }
    if (toolName === 'search_files') {
      return {
        title: 'Files searched',
        summary: 'Builder workspace search completed',
        detail: 'Matched source text is omitted from ordinary chat history for privacy.',
      };
    }
    if (toolName === 'attached_local_sources' && result.status === 'ingested' &&
        Number.isInteger(result.source_count) && result.source_count > 0) {
      return {
        title: 'Sources attached',
        summary: `${result.source_count} local source${result.source_count === 1 ? '' : 's'} attached`,
        detail: 'Available to the Builder in this chat.',
      };
    }
    const status = String(result.status || content && content.status || '');
    const failed = ['failed', 'error'].includes(status);
    const rejected = status === 'rejected' || (!failed && result.success === false);
    if (result.success === false && result.code === 'not_executed' &&
        ['required_reference_unavailable', 'repeated_reference_failure'].includes(result.reason)) {
      return { feedback: true, summary: 'This action did not run because a selected source could not be read. Saved work is kept.' };
    }
    if (rejected && ['check_participant_draft', 'publish_participant_artifact',
      'check_character_profile_draft', 'publish_character_profile'].includes(toolName)) {
      return { feedback: true, summary: participantRejectionDetail(result) };
    }
    if (result.success === false && ['inspect_builder_references', 'copy_builder_reference'].includes(toolName) &&
        ['reference_unavailable', 'reference_copy_unavailable'].includes(result.code)) {
      return { feedback: true, summary: result.reason === 'package_file_oversized'
        ? 'The selected source file exceeds Builder’s supported size. Saved work is kept. Check the selected content before continuing.'
        : `Builder could not ${toolName === 'copy_builder_reference' ? 'copy' : 'read'} a selected reference. Saved work is kept. Check the selected source${toolName === 'copy_builder_reference' ? ' and draft workspace' : ''} before continuing.` };
    }
    if (failed || rejected) {
      return { feedback: true, summary: failed
        ? 'Sylvia could not complete this action. Try again.'
        : 'This action was rejected. It is unavailable with these choices. Check them and try again.' };
    }
    // A persisted status is not a player-facing outcome. The declared Ruleset card,
    // pending choice and narration have their own renderers; keep this receipt silent.
    return null;
  }

  function finiteProjectedNumber(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }


  // Render a committed player-safe d20 result without reinterpreting the Ruleset outcome.


  // Display saved Ruleset fields without reading current entities or calculating an outcome.
  // Show only the recorded equation; the renderer never rolls, sums modifiers or judges success.
  function recordedCheckPresentation(content) {
    const parameters = content && content.parameters && typeof content.parameters === 'object'
      ? content.parameters : {};
    const result = content && content.result && typeof content.result === 'object'
      ? content.result : {};
    const context = result.context && typeof result.context === 'object' ? result.context : {};
    const dice = context._d20_test && typeof context._d20_test === 'object'
      ? context._d20_test : {};
    const rawSkill = typeof parameters.skill === 'string' ? parameters.skill : '';
    const displayName = (value) => value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
    const skill = displayName(rawSkill);
    const ability = typeof parameters.ability === 'string' ? displayName(parameters.ability) : '';
    const actorData = context.actor && typeof context.actor === 'object' &&
      context.actor.data && typeof context.actor.data === 'object' ? context.actor.data : {};
    const actor = typeof actorData.name === 'string' ? actorData.name.trim() : '';
    const rawOutcome = String(context._outcome || '').toLowerCase();
    const rejected = result.status === 'rejected' || result.success === false ||
      content && content.status === 'rejected';
    const checkResolved = !rejected && ['success', 'failure'].includes(rawOutcome);
    const outcome = !checkResolved ? 'Check not resolved' :
      (rawOutcome === 'success' ? 'Success' : 'Failure');
    const adjustedTotal = finiteProjectedNumber(context._adjusted_total);
    const dieTotal = finiteProjectedNumber(dice.total);
    const dc = finiteProjectedNumber(parameters.target_number);
    const results = Array.isArray(dice.results)
      ? dice.results.map(finiteProjectedNumber).filter((value) => value !== null)
      : [];
    const modifier = finiteProjectedNumber(dice.modifier);
    const guidance = finiteProjectedNumber(context._guidance_bonus);
    const lasting = finiteProjectedNumber(context._lasting_d20_modifier);
    const exhaustion = finiteProjectedNumber(context._exhaustion_penalty);
    const advantage = context._effective_advantage === true;
    const disadvantage = context._effective_disadvantage === true;
    const selection = advantage && disadvantage ? 'Advantage and disadvantage cancel' :
      advantage ? 'Advantage' : disadvantage ? 'Disadvantage' : 'Normal';
    const row = (label, value, emphasis = false) => ({ label, value, emphasis });
    const summary = [row('Outcome', outcome, true)];
    if (actor) summary.push(row('Actor', actor));
    if (ability) summary.push(row('Ability', ability));
    if (checkResolved && dieTotal !== null) summary.push(row('Roll', dieTotal));
    if (checkResolved && adjustedTotal !== null) summary.push(row('Total', adjustedTotal, true));
    if (dc !== null) summary.push(row('DC', dc));
    const details = [];
    if (checkResolved) {
      if (results.length) details.push(row('D20 results', results));
      details.push(row('Roll mode', selection));
      for (const [label, value] of [['Modifier', modifier], ['Guidance', guidance],
        ['Ongoing effects', lasting], ['Exhaustion penalty', exhaustion]]) {
        if (value !== null && value !== 0) details.push(row(label, value));
      }
      if (dieTotal === null) details.push(row('Resolution', rawOutcome === 'failure' &&
        String(context._automatic_failure_reason || 'none') !== 'none'
        ? 'Automatic failure' : 'No selected die was recorded'));
    }
    const title = parameters.kind === 'attack_roll' ? 'Attack roll' :
      parameters.kind === 'saving_throw' ? 'Saving throw' : `${skill || ability || 'D20'} check`;
    return { version: 1, footprint: 'compact', title, summary, details,
      details_label: 'Calculation', actions: [] };
  }

  function toolResultMarkup(content, key) {
    if (content?.kind === 'working_context_checkpoint') {
      return applicationResultMarkup({ title: 'Context compacted',
        summary: 'The saved conversation is kept.',
        detail: 'Sylvia will continue with a shorter working context.' }, key);
    }
    if (content?.kind === 'context_compaction_requested') return '';
    if (content?.kind === 'context_compaction_terminal') {
      return content.status === 'completed' ? '' : applicationResultMarkup({
        title: 'Compaction interrupted',
        summary: 'Saved conversation and the previous continuation note were kept.' }, key);
    }
    const savedCard = content && content.result && content.result.result_card;
    const cardMarkup = rulesetCardMarkup(
      savedCard,
      key,
      content && content.action_id,
      content && content.result_card_action_availability,
      content && content.result_card_action_reasons
    );
    if (cardMarkup !== null) return cardMarkup;
    if (['d20_test', 'unbound_d20_test'].includes(String(content && content.tool_name || '')) &&
        !Object.hasOwn(content && content.result || {}, 'result_card')) {
      return rulesetCardMarkup(recordedCheckPresentation(content), key);
    }
    const presentation = toolPresentation(content || {});
    if (presentation) return applicationResultMarkup(presentation, key);
    if (savedCard !== undefined && savedCard !== null) return applicationResultMarkup({ feedback: true,
      summary: 'The saved Ruleset result cannot be displayed.' }, key);
    return '';
  }

  function applicationResultMarkup(presentation, key) {
    if (!presentation) return '';
    if (presentation.feedback) {
      return `<div class="inline-feedback" role="status"${key}>` +
        `${escapeHtml(presentation.summary || '')}</div>`;
    }
    const actions = Array.isArray(presentation.actions) ? presentation.actions : [];
    const actionMarkup = actions.length ? `<div class="result-actions">` + actions.map((action) => {
      const label = action === 'preview_campaign_plan'
        ? 'Preview'
        : (action === 'configure_campaign' ? 'Configure Campaign' : 'Use for Campaign');
      const setup = ['configure_campaign', 'use_campaign_plan'].includes(action)
        ? ` data-campaign-plan-id="${escapeHtml(presentation.campaignPlanId || '')}"` +
          ` data-campaign-setup-id="${escapeHtml(presentation.setupId || '')}"`
        : '';
      return `<button type="button" class="btn btn-secondary btn-sm" ` +
        `data-builder-result-action="${escapeHtml(action)}"${setup}>${label}</button>`;
    }).join('') + `</div>` : '';
    const preview = presentation.preview
      ? `<div class="campaign-plan-preview" hidden>${escapeHtml(presentation.preview)}</div>`
      : '';
    const testDetails = presentation.testDetails
      ? `<details class="result-card-details"><summary>Test details</summary>` +
        `<pre>${escapeHtml(presentation.testDetails)}</pre></details>` : '';
    return rulesetCardMarkup({ version: 1,
      footprint: actions.length || presentation.testDetails ? 'compact' : 'inline',
      title: presentation.title,
      summary: [presentation.summary, presentation.detail].filter(Boolean)
        .map((value) => ({ value, emphasis: false })),
      details: [],
      actions: [],
    }, key, null, null, null, actionMarkup + preview + testDetails);
  }

  function wireBuilderResultActions(container) {
    container.querySelectorAll('[data-builder-result-action="preview_campaign_plan"]').forEach(
      (button) => replaceClickHandler(button, () => {
        const preview = button.closest('.result-card').querySelector('.campaign-plan-preview');
        if (!preview) return;
        preview.hidden = !preview.hidden;
        button.textContent = preview.hidden ? 'Preview' : 'Hide preview';
      })
    );
    for (const action of ['configure_campaign', 'use_campaign_plan']) {
      container.querySelectorAll(`[data-builder-result-action="${action}"]`).forEach(
        (button) => replaceClickHandler(button, async () => {
          const planId = button.dataset.campaignPlanId;
          if (button.disabled) return;
          if (!planId) {
            if (action === 'use_campaign_plan') await finalizeSelectedConstruction();
            return;
          }
          button.disabled = true;
          try {
            if (!state.activeCampaignId || state.constructionDraftId !== state.activeCampaignId) {
              const draft = await startCampaignBuilder();
              if (!draft) return;
            }
            await showConstructionPanel(planId);
          } finally {
            button.disabled = false;
          }
        })
      );
    }
  }

  function packageActivityLabel(content) {
    const result = content.result || {};
    const label = result.activity_label;
    if (result.status !== 'committed' || result.success === false || result.result_card != null ||
        ['rejected', 'error', 'failed', 'pending'].includes(content.status)) return null;
    return typeof label === 'string' && label.trim() && label.length <= 80 ? label : null;
  }

  function resultEquationMarkup(equation) {
    if (!equation) return '';
    const { die_sides: sides, roll, modifier, total } = equation;
    const shapes = {
      4: '<polygon points="24,3 45,43 3,43"/>',
      6: '<rect x="5" y="5" width="38" height="38" rx="5"/>',
      8: '<polygon points="24,2 45,24 24,46 3,24"/>',
      10: '<polygon points="24,2 44,17 37,40 11,40 4,17"/>',
      12: '<polygon points="14,3 34,3 46,19 39,40 24,46 9,40 2,19"/>',
      20: '<polygon points="24,2 44,12 44,35 24,46 4,35 4,12"/>' +
        '<path class="result-die-facets" d="M24 2L24 9L4 12L8 35L24 46L40 35L44 12L24 9L8 35L40 35L24 9M4 35L8 35M40 35L44 35"/>',
    };
    const signed = modifier >= 0 ? `+${modifier}` : String(modifier);
    const die = (face) => {
      const label = `D${sides} roll: ${face}`;
      return `<svg class="result-die" viewBox="0 0 48 48" role="img" aria-label="${escapeHtml(label)}">` +
        `<title>${escapeHtml(label)}</title><g class="result-die-shape">` +
        `${shapes[sides] || '<circle cx="24" cy="24" r="21"/>'}</g>` +
        `<text x="24" y="30">${escapeHtml(String(face))}</text></svg>`;
    };
    const pool = Array.isArray(equation.rolls);
    const dice = pool ? `<div class="result-dice-pool">${equation.rolls.map(die).join('')}</div>` : die(roll);
    const rollLabel = pool ? `${equation.rolls.length}d${sides} · Rolled ${roll}` : `D${sides} roll`;
    return `<div class="result-equation"><div class="result-roll">` +
      `${dice}<div class="result-equation-label">${escapeHtml(rollLabel)}</div></div>` +
      `<div class="result-equation-math">` +
      `<div class="result-modifier">${escapeHtml(signed)}<div class="result-equation-label">Modifiers</div></div>` +
      `<div class="result-equals" aria-hidden="true">=</div>` +
      `<div class="result-total">${escapeHtml(String(total))}<div class="result-equation-label">Total</div></div></div></div>`;
  }

  function rulesetCardMarkup(card, key, sourceActionId = null, actionAvailability = null, actionReasons = null, extraMarkup = '') {
    const scalar = (value) => value === null || typeof value === 'string' ||
      typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value));
    const equationValid = (equation) => equation && Number.isInteger(equation.die_sides) &&
      equation.die_sides >= 2 && equation.die_sides <= 1000 &&
      ['roll', 'modifier', 'total'].every((field) =>
        typeof equation[field] === 'number' && Number.isFinite(equation[field])) &&
      (equation.rolls === undefined || (Array.isArray(equation.rolls) &&
        equation.rolls.length >= 1 && equation.rolls.length <= 64 &&
        equation.rolls.every((face) => Number.isInteger(face) && face >= 1 && face <= equation.die_sides)));
    const rowsValid = (rows) => Array.isArray(rows) && rows.every((row) => row &&
      typeof row === 'object' && Object.hasOwn(row, 'value') &&
      (row.label === undefined || typeof row.label === 'string') &&
      typeof row.emphasis === 'boolean' &&
      (row.equation === undefined || equationValid(row.equation)) &&
      (scalar(row.value) || (Array.isArray(row.value) && row.value.every(scalar))));
    if (!card || card.version !== 1 || (card.title !== undefined && typeof card.title !== 'string') ||
        (card.footprint !== undefined && !['inline', 'compact'].includes(card.footprint)) ||
        (card.details_label !== undefined && typeof card.details_label !== 'string') ||
        (card.subtitle !== undefined && typeof card.subtitle !== 'string') ||
        (card.equation !== undefined && !equationValid(card.equation)) ||
        (card.equations !== undefined && (!rowsValid(card.equations) ||
          card.equations.length > 16 || card.equations.some((row) => !row.equation))) ||
        !rowsValid(card.summary) || !rowsValid(card.details) || !Array.isArray(card.actions) ||
        !card.actions.every((action) => action && typeof action.label === 'string' &&
          typeof action.tool === 'string' && action.parameters &&
          typeof action.parameters === 'object' && !Array.isArray(action.parameters))) return null;
    const valueText = (value) => value === null ? '—' : String(value);
    const equationRowKey = (row) => JSON.stringify([row.label, row.value,
      row.equation.die_sides, row.equation.roll, row.equation.modifier,
      row.equation.total, row.equation.rolls || null]);
    // Suppress one featured copy per detail occurrence; equal independent rolls remain visible.
    const promotedEquations = new Map();
    [...card.summary, ...(card.equations || [])].filter((row) => row.equation).forEach((row) => {
      const identity = equationRowKey(row);
      promotedEquations.set(identity, (promotedEquations.get(identity) || 0) + 1);
    });
    const rowsMarkup = (rows, textOnlyEquations = null) => rows.map((row) => {
      const value = Array.isArray(row.value) ? row.value.map(valueText).join(', ') : valueText(row.value);
      const label = row.label === 'Actor' ? 'Character' : row.label;
      const valueIncludesLabel = label && typeof row.value === 'string' &&
        row.value.trimStart().toLowerCase().startsWith(label.toLowerCase() + ' ');
      const labelMarkup = label === undefined || valueIncludesLabel ? '' :
        `<span class="result-card-label">${escapeHtml(label)}</span>`;
      const identity = row.equation && textOnlyEquations ? equationRowKey(row) : null;
      const remaining = identity === null ? 0 : (textOnlyEquations.get(identity) || 0);
      if (remaining) textOnlyEquations.set(identity, remaining - 1);
      if (row.equation && !remaining) {
        return `<div class="result-card-roll-row${row.emphasis ? ' result-card-emphasis' : ''}">` +
          `<div class="result-card-roll-heading">` +
          labelMarkup +
          `<span class="result-card-value">${escapeHtml(value)}</span></div>` +
          `${resultEquationMarkup(row.equation)}</div>`;
      }
      return `<div class="result-card-row${row.emphasis ? ' result-card-emphasis' : ''}">` +
        labelMarkup +
        `<span class="result-card-value">${escapeHtml(value)}</span></div>`;
    }).join('');
    const details = card.details.length
      ? `<details class="result-card-details"><summary>${escapeHtml(card.details_label || 'Details')}</summary>` +
        `${rowsMarkup(card.details, promotedEquations)}</details>` : '';
    const authoritativeActions = typeof sourceActionId === 'string' && sourceActionId.length > 0 &&
      Array.isArray(actionAvailability) && actionAvailability.length === card.actions.length;
    const actions = card.actions.length ? `<div class="result-actions">` +
      card.actions.map((action, index) => {
        const available = authoritativeActions && actionAvailability[index] === true &&
          !selectedSessionIsWorking() && !campaignDisplayPending();
        const reason = campaignDisplayPending() ? 'Game state is still loading' : selectedSessionIsWorking() ? 'Sylvia is responding' :
          typeof actionReasons?.[index] === 'string' ? actionReasons[index] : 'This action is unavailable.';
        return `<button type="button" class="btn btn-secondary btn-sm" ` +
          `data-result-card-source-action-id="${escapeHtml(sourceActionId || '')}" ` +
          `data-result-card-button-index="${index}" ` +
          `${available ? '' : `disabled title="${escapeHtml(reason)}" aria-description="${escapeHtml(reason)}"`}>` +
          `${escapeHtml(action.label)}</button>`;
      }).join('') +
      `</div>` : '';
    return `<div class="story-card result-card result-card-${card.footprint || 'compact'}"${key}>` +
      `<div class="result-card-top"><div>` +
      (card.title ? `<div class="story-header">${escapeHtml(card.title)}</div>` : '') +
      (card.subtitle ? `<div class="result-card-subtitle">${escapeHtml(card.subtitle)}</div>` : '') +
      `</div>${resultEquationMarkup(card.equation)}</div>` +
      `${rowsMarkup(card.summary)}${rowsMarkup(card.equations || [])}${details}${actions}${extraMarkup}</div>`;
  }

  // Execute saved card requests through current owner admission, never through DOM tool arguments.
  function wireResultCardActions(container) {
    container.querySelectorAll('[data-result-card-button-index]').forEach((button) => {
      replaceClickHandler(button, async () => {
        const sourceActionId = button.dataset.resultCardSourceActionId;
        const buttonIndex = Number(button.dataset.resultCardButtonIndex);
        const activity = selectedActivity();
        const campaignId = state.activeCampaignId;
        const sessionId = state.activeSessionId;
        if (button.disabled || !sourceActionId || !Number.isSafeInteger(buttonIndex) ||
            buttonIndex < 0 || !campaignId || !sessionId || selectedSessionIsWorking() || campaignDisplayPending() ||
            !state.bridge?.snapshot || !state.bridge.preview_player_action ||
            !state.bridge.execute_player_action) return;
        button.disabled = true;
        try {
          if (sequenceModeActive()) {
            await appendQueuedAction({operation_id:`result-card-${crypto.randomUUID()}`,
              source:{kind:'result_card',action_id:sourceActionId,button_index:buttonIndex},selection:null});
            return;
          }
          const actionSnapshot = await state.bridge.snapshot(campaignId);
          if (!selectedActivityMatches(activity) || selectedSessionIsWorking()) return;
          state.activeSnapshot = actionSnapshot;
          const command = {
            campaign_id: campaignId,
            session_id: sessionId,
            operation_id: `result-card-${crypto.randomUUID()}`,
            expected_revision: actionSnapshot.revision,
            source: {
              kind: 'result_card',
              action_id: sourceActionId,
              button_index: buttonIndex,
            },
            selection: null,
          };
          const proposal = await state.bridge.preview_player_action(command);
          if (!selectedActivityMatches(activity) || !await confirmPlayerAction(proposal)) return;
          if (!selectedActivityMatches(activity) || selectedSessionIsWorking()) return;
          await state.bridge.execute_player_action({
            ...command,
            confirmation: proposal.confirmation,
          });
          selectedStatus(activity, 'Action completed');
          if (selectedActivityMatches(activity)) {
            try { await refreshSelectedCampaign(); }
            catch (_error) { selectedStatus(activity, 'Action saved; refresh the campaign display'); }
          }
          await respondToPlayerAction(campaignId, sessionId, command.operation_id);
        } catch (error) {
          selectedStatus(activity, friendlyCommandError('Action could not be completed', error));
        } finally {
          button.disabled = false;
          if (selectedActivityMatches(activity)) await renderPlayerProjection(campaignId, sessionId);
        }
      });
    });
  }


  // ---- Render the authoritative selected-session projection ----
  // Display pages never replace model context; completion invalidates pre-terminal reads.
  async function readDisplayHistory(sessionId) {
    if (!state.bridge.session_history_page) return state.bridge.session_context(sessionId);
    let history = state.displayHistory;
    if (!history || history.sessionId !== sessionId) {
      history = { sessionId, context: null, cursor: null, loadingOlder: false, error: false, generation: 0 };
      state.displayHistory = history;
    }
    if (history.refresh) return history.refresh;
    const current = () => state.displayHistory === history && state.activeSessionId === sessionId;
    const loadPage = async () => {
      const page = await state.bridge.session_history_page(sessionId);
      if (!current()) return null;
      let incoming = page.messages || [];
      let cursor = page.next_before_sequence;
      const previousLatest = history.context?.latest_sequence;
      // A long-running turn may add more than one page between display refreshes.
      while (previousLatest != null && cursor != null &&
          incoming.length && incoming[0].sequence > previousLatest + 1) {
        const gap = await state.bridge.session_history_page(sessionId, cursor);
        if (!current()) return null;
        incoming = [...(gap.messages || []), ...incoming];
        cursor = gap.next_before_sequence;
      }
      return { page, incoming, cursor };
    };
    history.refresh = (async () => {
      while (current()) {
        const generation = history.generation;
        let loaded;
        try { loaded = await loadPage(); } catch (error) {
          if (!current()) return null;
          if (generation !== history.generation) continue;
          throw error;
        }
        if (!current()) return null;
        // All joined renderers wait for the replacement read, including a poll already in flight.
        if (generation !== history.generation) continue;
        const { page, incoming, cursor } = loaded;
        const messages = new Map((history.context?.messages || []).map((m) => [m.sequence, m]));
        incoming.forEach((m) => messages.set(m.sequence, m));
        if (!history.context) history.cursor = cursor;
        history.context = { ...page, messages: [...messages.values()].sort((a, b) => a.sequence - b.sequence) };
        return history.context;
      }
      return null;
    })();
    try { return await history.refresh; } finally { history.refresh = null; }
  }

  function historyLoaderMarkup() {
    const history = state.displayHistory;
    if (!history || history.sessionId !== state.activeSessionId || history.cursor == null) return '';
    return '<div class="panel-empty" data-chronology-key="older-history" role="status">' +
      (history.loadingOlder ? 'Loading older messages…' :
        `<button type="button" class="btn btn-secondary btn-sm" data-load-older>${
          history.error ? 'Could not load older messages. Retry' : 'Load older messages'}</button>`) + '</div>';
  }

  // Restore the viewport after prepending, rather than jumping to the newest message.
  function wireHistoryLoader(redraw, enrich = null) {
    const history = state.displayHistory;
    const container = $('chronology');
    if (!history || history.sessionId !== state.activeSessionId) return;
    history.redraw = redraw;
    const repaint = () => {
      const top = container.scrollTop;
      const height = container.scrollHeight;
      history.redraw(history.context);
      container.scrollTop = top + container.scrollHeight - height;
    };
    const load = async () => {
      if (state.displayHistory !== history || history.loadingOlder || history.cursor == null) return;
      history.loadingOlder = true;
      history.error = false;
      repaint();
      try {
        const page = await state.bridge.session_history_page(history.sessionId, history.cursor);
        if (state.displayHistory !== history) return;
        const merged = new Map(history.context.messages.map((m) => [m.sequence, m]));
        (page.messages || []).forEach((m) => { if (!merged.has(m.sequence)) merged.set(m.sequence, m); });
        const nextContext = { ...history.context,
          messages: [...merged.values()].sort((a, b) => a.sequence - b.sequence) };
        if (enrich) await enrich(nextContext);
        if (state.displayHistory !== history) return;
        history.context = nextContext;
        history.cursor = page.next_before_sequence;
      } catch (error) {
        history.error = true;
      } finally {
        history.loadingOlder = false;
        if (state.displayHistory === history) {
          repaint();
        }
      }
    };
    if (container.sylviaHistoryScroll) container.removeEventListener('scroll', container.sylviaHistoryScroll);
    container.sylviaHistoryScroll = () => {
      if (container.scrollTop <= 48 && !history.error) void load();
    };
    container.addEventListener('scroll', container.sylviaHistoryScroll);
    // Keyed chronology preserves this button across polls; replace its captured history loader.
    const olderButton = container.querySelector('[data-load-older]');
    if (olderButton) replaceClickHandler(olderButton, () => { void load(); });
  }

  async function renderBuilderSession(sessionId = state.activeSessionId) {
    if (!state.bridge || !state.bridge.session_context || !sessionId ||
        state.activeSessionId !== sessionId) return;
    const activity = ensureSessionActivity({ sessionId, sessionRole: 'builder' });
    let context = null;
    try {
      context = await readDisplayHistory(sessionId);
    } catch (e) {
      context = null;
    }
    if (!selectedActivityMatches(activity) || state.activeSessionRole !== 'builder') return;
    if (!context) return false;
    renderBuilderHistory(activity, context);
  }

  // Only known routine operations are folded away; meaningful or unknown Builder outputs stay visible.
  function routineActivityLabel(name) {
    // Hosted providers identify package tools with this stable alias prefix.
    if (typeof name !== 'string') return null;
    name = name.replace(/^tool_[a-f0-9]{12}_/, '');
    if (['read_file', 'list_files', 'search_files', 'read_reference', 'search_knowledge',
      'read_campaign_file', 'read_campaign_reference', 'search_campaign_knowledge',
      'read_session_history', 'list_directory', 'search_library',
      'discover_library', 'get_library_item', 'get_content_definition',
      'inspect_builder_references'].includes(name)) return 'Read campaign data';
    if (['write_file', 'edit_file', 'create_file', 'copy_builder_reference'].includes(name)) return 'Write to file';
    if (['update_character', 'update_character_state', 'update_campaign_state',
      'commit_campaign_file', 'update_campaign_file'].includes(name)) return 'Write to database';
    return null;
  }

  function builderProgressStage(name) {
    if (['get_content_definition', 'inspect_builder_references', 'read_file'].includes(name)) return 'Reviewed references';
    if (['write_file', 'edit_file', 'copy_builder_reference'].includes(name)) return 'Updated draft';
    if (['check_participant_draft', 'check_character_profile_draft'].includes(name)) return 'Checked character';
    if (['publish_participant_artifact', 'publish_character_profile'].includes(name)) return 'Saved character';
    return null;
  }

  // Correlation comes from saved ingress metadata, never arrival timing or narration text.
  function groupedAgentActivity(messages, cards, context, role, liveActivity = null) {
    const groups = new Map();
    const hidden = new Set();
    const calls = new Map();
    const routineActions = new Set(messages.filter((message) =>
      message.content?.activity?.attempt_id && message.content?.action_id &&
      packageActivityLabel(message.content)).map((message) =>
      `${message.content.activity.attempt_id}:${message.content.action_id}`));
    const groupFor = (attempt, basis, position) => {
      if (typeof attempt !== 'string' || !attempt) return null;
      if (!groups.has(attempt)) {
        const basisIndex = messages.findIndex((message) => message.message_id === basis);
        groups.set(attempt, { attempt, position: basisIndex >= 0 ? basisIndex + 1 : position,
          entries: [], thinking: [], status: null });
      } else {
        const basisIndex = messages.findIndex((message) => message.message_id === basis);
        if (basisIndex >= 0) groups.get(attempt).position = basisIndex + 1;
      }
      return groups.get(attempt);
    };
    messages.forEach((message, index) => {
      const content = message.content || {};
      const correlation = typeof content.activity === 'object' ? content.activity : null;
      const attempt = correlation?.attempt_id ||
        (['gm_turn_activity', 'builder_attempt_terminal', 'session_attempt_terminal'].includes(content.kind)
          ? content.attempt_id : null);
      const group = groupFor(attempt, correlation?.basis_message_id || content.basis_message_id, index);
      if (content.kind === 'provider_tool_calls') {
        for (const call of content.assistant?.tool_calls || []) calls.set(call.id, call.function?.name);
      }
      if (!group) return;
      if (content.kind === 'gm_turn_activity') {
        group.status = 'completed'; hidden.add(index);
      } else if (['builder_attempt_terminal', 'session_attempt_terminal'].includes(content.kind)) {
        group.status = content.status === 'completed' ? 'completed' : 'interrupted';
      } else if (content.kind === 'provider_tool_results') {
        for (const result of content.results || []) {
          let recorded = {};
          try { recorded = JSON.parse(result.content || '{}') || {}; } catch (_error) { /* Older opaque receipt. */ }
          if (routineActions.has(`${attempt}:${recorded.action_id}`)) continue;
          const label = packageActivityLabel(recorded) ||
            routineActivityLabel(result.name || calls.get(result.tool_call_id)) || 'Resolve game action';
          group.entries.push({ label, order: correlation?.source_order ?? index });
        }
      } else {
        const label = packageActivityLabel(content) || routineActivityLabel(content.tool_name);
        const result = content.result || {};
        const failed = result.success === false || ['rejected', 'error', 'failed'].includes(result.status) ||
          ['rejected', 'error', 'failed', 'pending'].includes(content.status);
        if (role === 'builder' && !failed && content.tool_name) {
          const stage = builderProgressStage(content.tool_name);
          if (stage) group.lastStage = stage;
        }
        if (label && !failed && result.result_card == null) {
          group.entries.push({ label, order: correlation?.source_order ?? index });
          hidden.add(index);
        }
      }
    });
    for (const thinking of context?.thinking || []) {
      const basisIndex = messages.findIndex((message) => message.message_id === thinking.basis_message_id);
      if (basisIndex < 0 && !groups.has(thinking.attempt_id)) continue;
      const group = groupFor(thinking.attempt_id, thinking.basis_message_id, basisIndex + 1);
      group.thinking.push(thinking);
      if (!group.status && thinking.status !== 'partial') group.status = thinking.status;
    }
    const additions = new Map();
    for (const group of groups.values()) {
      const live = liveActivity?.attemptId === group.attempt && activityIsWorking(liveActivity);
      const status = live ? (role === 'builder' && group.lastStage ? `Working · Last: ${group.lastStage}` : 'Working') : group.status === 'interrupted' ? 'Interrupted' :
        group.status === 'completed' ? 'Done' : 'Recorded';
      const entries = group.entries.map((entry) => ({ order: entry.order,
        html: `<div class="agent-operation"><span>${escapeHtml(entry.label)}</span><span>Done</span></div>` }));
      for (const thinking of group.thinking) {
        const identity = `thinking-${group.attempt}-${thinking.response_id}`;
        entries.push({ order: thinking.source_order, html:
          `<details class="agent-thinking" data-disclosure-key="${escapeHtml(identity)}" ` +
          `data-thinking-attempt="${escapeHtml(group.attempt)}" ` +
          `data-thinking-version="${Number(thinking.chunk_count) || 0}" ` +
          `data-thinking-response="${escapeHtml(thinking.response_id)}">` +
          '<summary>Thinking <span class="thinking-warning">May contain spoilers</span></summary>' +
          '<div class="thinking-body"></div></details>' });
      }
      if (!entries.length && !group.status) continue;
      const label = role === 'builder' ? 'Builder activity' : 'GM activity';
      const html = `<details class="agent-activity" data-chronology-key="activity-${escapeHtml(group.attempt)}" ` +
        `data-disclosure-key="activity-${escapeHtml(group.attempt)}">` +
        `<summary>${label}<span class="agent-activity-status">${escapeHtml(status)}</span></summary>` +
        `<div class="agent-activity-trail">${entries.sort((a, b) => a.order - b.order)
          .map((entry) => entry.html).join('')}</div></details>`;
      additions.set(group.position, (additions.get(group.position) || '') + html);
    }
    return [...cards.map((card, index) => (additions.get(index) || '') +
      (hidden.has(index) ? '' : card)), additions.get(cards.length) || ''];
  }

  // Fetch private text only for a deliberately opened control, and never paint a different session.
  function wireThinkingInspection(container) {
    const sessionId = state.activeSessionId;
    container.querySelectorAll('details[data-thinking-response]').forEach((element) => {
      const inspect = async () => {
        const body = element.querySelector('.thinking-body');
        if (!element.open || !body || body.dataset.loaded || body.dataset.loading) return;
        body.dataset.loading = 'true';
        body.textContent = 'Loading thinking…';
        try {
          const response = await state.bridge.inspect_provider_thinking(
            sessionId, element.dataset.thinkingAttempt, element.dataset.thinkingResponse,
          );
          if (state.activeSessionId !== sessionId || !element.isConnected) return;
          body.textContent = typeof response?.text === 'string' ? response.text : 'Thinking unavailable.';
          body.dataset.loaded = 'true';
        } catch (_error) {
          if (state.activeSessionId === sessionId && element.isConnected) {
            body.textContent = 'Could not load thinking. Close and reopen to retry.';
          }
        } finally { delete body.dataset.loading; }
      };
      element.ontoggle = inspect;
      if (element.open) void inspect();
    });
  }

  function renderBuilderHistory(activity, context) {
    const sessionId = activity.sessionId;
    const container = $('chronology');
    reconcileBuilderActivity(activity, context && context.activity);
    if (context?.context_status_hint) selectedStatus(activity, context.context_status_hint);
    renderSessionModelState(context);
    const constructionPanel = $('construction-panel');
    // The attached Interview owns this area while loading as well as when its form is visible.
    if (constructionPanel.parentElement === container) return;
    const messages = context && Array.isArray(context.messages) ? context.messages : [];
    if (activity.pendingInput &&
        messages.some((message) => message.message_id === activity.pendingInput.messageId)) {
      clearAcceptedInput(activity);
      // The owner saves the opening title before inference; refresh it once on input acceptance.
      if (!activity.campaignId && state.builderChats.some((chat) =>
        chat.session?.session_id === sessionId && chat.display_name === 'New chat')) {
        void refreshBuilderChats();
      }
    }
    const launchMessages = context.launch_messages || messages;
    const subflowLink = launchMessages.find(
      (message) => message.kind === 'evidence' &&
        message.content && message.content.kind === 'creation_subflow_link'
    );
    const hasIndependentLaunch = launchMessages.some(
      (message) => message.kind === 'evidence' &&
        message.content && message.content.kind === 'builder_launch_context'
    );
    const hasActiveCampaignLink = context?.session?.campaign_id != null ||
      !hasIndependentLaunch;
    state.activeCreationSubflow = subflowLink && hasActiveCampaignLink ? {
      campaign_id: subflowLink.content.campaign_id,
      parent_session_id: subflowLink.content.parent_session_id,
      child_session_id: sessionId,
      purpose: subflowLink.content.purpose,
      resume_step: subflowLink.content.resume_step,
    } : null;
    const provisional = context && Array.isArray(context.provisional)
      ? context.provisional
      : [];
    if (!messages.length && !provisional.length) {
      updateChronology(container, '<div class="empty-state" data-chronology-key="empty-builder">' +
        '<div class="empty-icon">◆</div><div>The Builder is ready.</div></div>', () => {});
      return;
    }
    let lastUserIndex = -1;
    messages.forEach((message, index) => {
      if (message.kind === 'user') lastUserIndex = index;
    });
    const builderQuestions = messages.map((message, index) =>
      message.kind === 'evidence'
        ? builderQuestionFromEvidence(message.content || {}, message.message_id, index)
        : null
    );
    // Only explicit durable answer events complete a widget or replace its chat bubble.
    const savedAnswers = new Map();
    messages.forEach((message, messageIndex) => {
      const content = message.content || {};
      if (message.kind !== 'user' || content.kind !== 'builder_question_answer' ||
          !Array.isArray(content.answers) || !content.answers.length) return;
      const seen = new Set();
      const valid = content.answers.every((answer) => {
        if (!answer || ['question_message_id', 'tool_call_id', 'widget_id', 'question', 'value', 'label']
          .some((field) => typeof answer[field] !== 'string' || !answer[field].length) ||
          !['text', 'select', 'cascade', 'confirm'].includes(answer.type) ||
          typeof answer.custom !== 'boolean' || seen.has(answer.question_message_id)) return false;
        seen.add(answer.question_message_id);
        const question = builderQuestions.find((item) => item?.key === answer.question_message_id);
        return !question || (question.toolCallId === answer.tool_call_id &&
          question.id === answer.widget_id && question.type === answer.type &&
          question.question === answer.question && question.index < messageIndex);
      });
      if (!valid) return;
      savedAnswers.set(message, content.answers);
      content.answers.forEach((answer) => {
        const question = builderQuestions.find((item) => item?.key === answer.question_message_id);
        if (question && !question.answer) question.answer = answer;
      });
    });
    const activeQuestions = builderQuestions.filter((question) =>
      question && !question.answer && question.awaiting && (context.latest_user_sequence != null
        ? messages[question.index].sequence > context.latest_user_sequence
        : question.index > lastUserIndex)
    );
    if (context !== null && !activeQuestions.length && lastUserIndex >= 0) {
      state.builderQuestionDrafts.delete(sessionId);
    }
    const questionGroupId = `builder-question-form-${sessionId}`;
    const questionBusy = selectedSessionIsWorking() || state.deletingBuilderChats.has(sessionId);
    const committed = messages.map((message, messageIndex) => {
      const content = message.content || {};
      const key = chronologyKeyAttribute(message.message_id ||
        `${message.kind}-${message.canonical_sequence ?? ''}-${content.text || ''}`);
      if (message.kind === 'user') {
        if (savedAnswers.has(message)) {
          const detached = savedAnswers.get(message).filter((answer) =>
            !builderQuestions.some((question) => question?.key === answer.question_message_id));
          return detached.length ? `<div class="builder-answered-questions"${key}>` +
            detached.map((answer) => builderQuestionCardMarkup({
              question: answer.question, answer,
            }, '', {})).join('') + `</div>` : '';
        }
        return `<div class="story-card talk"${key}><div class="story-header">You</div>` +
          `<div class="story-text">${escapeHtml(content.text || '')}</div></div>`;
      }
      if (message.kind === 'talk') {
        return `<div class="story-card talk"${key}><div class="story-header">Builder</div>` +
          `<div class="story-text">${renderNarrationMarkdown(content.text || '')}</div></div>`;
      }
      if (message.kind === 'evidence') {
        const question = builderQuestions[messageIndex];
        if (question) {
          return builderQuestionCardMarkup(question, key, {
            sessionId,
            groupId: questionGroupId,
            active: activeQuestions.includes(question),
            busy: questionBusy,
          });
        }
        if (String(content.tool_name || '') === 'ask_question') {
          return builderQuestionFallbackMarkup(content, key);
        }
        if (content.kind === 'builder_title_preference') return '';
        if (content.kind === 'builder_attempt_terminal') {
          if (content.status === 'completed') return '';
          const explanation = content.status === 'interrupted' && content.reason_code === 'tool_limit'
            ? 'Builder reached its work limit after automatic continuation. Saved work is retained. Send a message to continue.'
            : content.status === 'interrupted' && content.reason_code === 'repeated_tool_failure'
            ? 'Builder stopped after repeated identical tool failures. Saved work is retained. Review the last tool error before continuing.'
            : content.status === 'interrupted' && content.reason_code === 'required_reference_unavailable'
            ? 'Builder stopped because a selected source could not be read. Saved work is retained. Check the selected source content before continuing.'
            : content.status === 'interrupted' && content.reason_code === 'repeated_reference_failure'
            ? 'Builder stopped after repeated attempts to read the same source. Saved work is retained. Review the source error before continuing.'
            : 'Saved work is retained. Send a message when you are ready to continue.';
          return `<div class="story-card builder-interruption"${key}>` +
            `<div class="story-header">${content.status === 'stopped' ? 'Stopped' : 'Interrupted'}</div>` +
            `<div class="story-text">${explanation}</div></div>`;
        }
        if (content.kind === 'creation_subflow_link' ||
            content.kind === 'creation_subflow_return' ||
            content.kind === 'builder_launch_context' || content.kind === 'builder_start') return '';
        if (content.kind === 'campaign_construction_brief') {
          const premise = content.input && content.input.premise
            ? content.input.premise
            : 'No starting premise was supplied.';
          return `<div class="story-card talk"${key}><div class="story-header">Campaign brief</div>` +
            `<div class="story-text">${escapeHtml(premise)}</div></div>`;
        }
        return toolResultMarkup(content, key);
      }
      return '';
    });
    const subflowActions = state.activeCreationSubflow
      ? `<div class="story-card creation-subflow-card" data-chronology-key="creation-subflow-actions">` +
        `<div class="story-header">Linked Creation Subflow</div>` +
        `<div class="story-text">Build and validate this ${escapeHtml(
          String(state.activeCreationSubflow.purpose || '').replaceAll('_', ' ')
        )}, then choose what returns to the Campaign Interview.</div>` +
        `<div class="result-actions">` +
        `<button type="button" class="btn btn-primary btn-sm" data-subflow-return="use">Use in Campaign &amp; Return</button>` +
        `<button type="button" class="btn btn-secondary btn-sm" data-subflow-return="discard">Return Without Using</button>` +
        `<button type="button" class="btn btn-secondary btn-sm" data-subflow-return="stay">Keep Building</button>` +
        `</div></div>`
      : '';
    const live = provisional.map((projection) => renderProjection(projection, activity));
    const questionForm = activeQuestions.length
      ? builderQuestionFormMarkup(sessionId, questionGroupId, questionBusy)
      : '';
    const unfinished = context && context.activity && context.activity.status === 'interrupted' &&
      !messages.some((message) => message.content &&
        message.content.kind === 'builder_attempt_terminal' &&
        message.content.attempt_id === context.activity.attempt_id)
      ? '<div class="story-card builder-interruption" data-chronology-key="builder-restart-interruption">' +
        '<div class="story-header">Generation interrupted</div>' +
        '<div class="story-text">The previous response did not finish. Saved work is retained. ' +
        'Send a message when you are ready to continue.</div></div>' : '';
    updateChronology(container,
      [historyLoaderMarkup(), subflowActions,
        ...groupedAgentActivity(messages, committed, context, 'builder', activity),
        questionForm, ...live, unfinished].filter(Boolean).join(''), () => {
      wireBuilderResultActions(container);
      wireBuilderQuestionForms(container);
      container.querySelectorAll('[data-subflow-return]').forEach((button) => {
        replaceClickHandler(button, () => {
          if (button.dataset.subflowReturn === 'stay') {
            $('player-input').focus();
            return;
          }
          void returnCreationSubflow(button.dataset.subflowReturn === 'use');
        });
      });
    });
    wireHistoryLoader((next) => renderBuilderHistory(activity, next));
  }

  function readDMProjection(campaignId, context, loadId = null) {
    if (!state.bridge.player_projection_page)
      return state.bridge.player_projection ? state.bridge.player_projection(campaignId, loadId) : [];
    const sequences = (context?.messages || []).map((m) => m.canonical_sequence).filter(Number.isInteger);
    const after = sequences.length ? Math.min(...sequences) - 1 : null;
    return state.bridge.player_projection_page(campaignId, after, loadId);
  }

  async function renderDMSession(campaignId, sessionId = state.activeSessionId, { opening = null } = {}) {
    if (!state.bridge || !state.bridge.session_context || !sessionId ||
        state.activeSessionId !== sessionId) return;
    const activity = ensureSessionActivity({ sessionId, sessionRole: 'dm', campaignId });
    const settle = (promise) => Promise.resolve(promise).then(
      (value) => ({ value }), (error) => ({ error }));
    const contextRequest = settle(readDisplayHistory(sessionId));
    const projectionRequest = settle(state.bridge.player_projection_page
      ? contextRequest.then((loaded) => state.activeSessionId === sessionId && !loaded.error
        ? readDMProjection(campaignId, loaded.value, opening?.id) : [])
      : readDMProjection(campaignId, null, opening?.id));
    const loadedContext = await contextRequest;
    const stillSelected = () => selectedActivityMatches(activity) && state.activeSessionRole === 'dm' &&
      state.activeCampaignId === campaignId && (!opening || selectedCampaignOpening() === opening);
    if (opening && stillSelected() && !loadedContext.error)
      renderDMHistory(activity, loadedContext.value, [], 'loading');
    let loadedProjection = await projectionRequest;
    if (!stillSelected()) return;
    const currentContext = () => state.displayHistory?.sessionId === sessionId
      ? state.displayHistory.context : loadedContext.value;
    if (state.bridge.player_projection_page && currentContext() !== loadedContext.value) {
      loadedProjection = await settle(readDMProjection(campaignId, currentContext(), opening?.id));
      if (!stillSelected()) return;
    }
    const failed = Boolean(loadedContext.error || loadedProjection.error);
    updateCampaignOpening(opening, 'history', failed ? 'error' : 'ready');
    if (opening && !failed) opening.refreshHistory = () =>
      renderDMHistory(activity, currentContext(), loadedProjection.value || []);
    renderDMHistory(activity, currentContext(), loadedProjection.value || [], failed ? 'error' : null);
  }

  function renderDMHistory(activity, context, projections, displayStatus = null) {
    const campaignId = activity.campaignId;
    const container = $('chronology');
    if (!selectedActivityMatches(activity) || state.activeSessionRole !== 'dm' ||
        state.activeCampaignId !== campaignId) return;
    if (context?.context_status_hint) selectedStatus(activity, context.context_status_hint);
    renderSessionModelState(context);
    let messages = context && Array.isArray(context.messages) ? context.messages : [];
    const terminalsByAttempt = new Map(
      messages
        .filter((message) => message.kind === 'evidence' &&
          message.content && message.content.kind === 'session_attempt_terminal')
        .map((message) => [message.content.attempt_id, message.content])
    );
    projections = (Array.isArray(projections) ? projections : []).map((projection) => {
      if (projection.kind !== 'retry') return projection;
      const terminal = terminalsByAttempt.get(
        projection.safe_payload && projection.safe_payload.failed_attempt_id
      ) || {};
      return {
        ...projection,
        safe_payload: { ...terminal, ...(projection.safe_payload || {}) },
      };
    });
    const projectionsBySequence = new Map(
      (Array.isArray(projections) ? projections : [])
        .filter((projection) => !projection.provisional && Number.isInteger(projection.sequence))
        .map((projection) => [projection.sequence, projection])
    );
    const activityByAction = new Map();
    for (const message of messages) {
      const content = message.content || {};
      if (content.kind === 'mechanics_activity' && content.action_id &&
          content.activity?.attempt_id && !activityByAction.has(content.action_id)) {
        activityByAction.set(content.action_id, content.activity);
      }
    }
    // Enrich only from the saved event projection; do not correlate by arrival order.
    messages = messages.map((message) => {
      const projection = message.kind === 'canonical_reference'
        ? projectionsBySequence.get(message.canonical_sequence) : null;
      if (!projection || message.content?.event_type !== 'tool_result') return message;
      return { ...message, content: { ...message.content, ...(projection.safe_payload || {}),
        activity: message.content.activity || activityByAction.get(projection.action_id ||
          projection.safe_payload?.action_id) || (projection.attempt_id
          ? { attempt_id: projection.attempt_id } : null) } };
    });
    const canonicalSequences = messages
      .filter((message) => Number.isInteger(message.canonical_sequence))
      .map((message) => message.canonical_sequence);
    activity.acknowledgedSequence = context?.latest_canonical_sequence ?? (canonicalSequences.length
      ? Math.max(...canonicalSequences)
      : 0);
    if (!displayStatus) {
      activity.retryBasis = null;
      activity.interruptedAttemptId = null;
    }

    const committed = messages.map((message) => {
      const content = message.content || {};
      const key = chronologyKeyAttribute(message.message_id ||
        `${message.kind}-${message.canonical_sequence ?? ''}-${content.text || ''}`);
      if (message.kind === 'user') {
        return `<div class="story-card talk"${key}><div class="story-header">You</div>` +
          (content.text ? `<div class="story-text player-message-text">${escapeHtml(content.text)}</div>` : '') +
          (content.ooc_text ? `<div class="ooc-message response-block">` +
            `<div class="response-label">OOC · You</div>` +
            `<div class="story-text">${escapeHtml(content.ooc_text)}</div></div>` : '') + '</div>';
      }
      if (message.kind === 'evidence') {
        if (content.kind === 'campaign_start' || content.kind === 'player_action_response') return '';
        if (content.kind === 'gm_system_contract' ||
            content.kind === 'mechanics_activity' ||
            content.kind === 'ready_to_begin_reference' ||
            String(content.kind || '').startsWith('provider_')) return '';
        // The authoritative retry projection owns this notice; evidence only enriches it.
        if (content.kind === 'session_attempt_terminal') return '';
        return toolResultMarkup(content, key);
      }
      if (message.kind === 'canonical_reference') {
        const eventType = String(content.event_type || '');
        if (!['talk', 'think', 'tool_result'].includes(eventType)) return '';
        const canonicalProjection = projectionsBySequence.get(message.canonical_sequence);
        const projectedPayload = canonicalProjection && canonicalProjection.safe_payload
          ? canonicalProjection.safe_payload : {};
        return renderProjection({
          kind: eventType === 'tool_result' ? 'tool_card' : eventType,
          sequence: message.canonical_sequence,
          text: content.safe_payload && content.safe_payload.text,
          safe_payload: { ...(content.safe_payload || {}), ...projectedPayload },
          collapsed_by_default: eventType !== 'talk',
          provisional: false,
          attempt_id: canonicalProjection && canonicalProjection.attempt_id,
        });
      }
      return '';
    });
    const live = (Array.isArray(projections) ? projections : [])
      .filter((projection) => projection.provisional ||
        projection.kind === 'retry' || projection.kind === 'mechanics_choice')
      .map((projection) => renderProjection(projection, activity));
    if (activity.pendingInput &&
        messages.some((message) => message.message_id === activity.pendingInput.messageId)) {
      clearAcceptedInput(activity);
    }
    refreshSessionInputAdmission();
    const notice = displayStatus === 'error' && selectedCampaignOpening()?.history !== 'error'
      ? '<div class="story-card" data-chronology-key="loading-error" role="status">' +
        'Some campaign data could not be loaded. Saved history is kept. ' +
        '<button type="button" class="btn btn-secondary btn-sm" data-retry-display>Retry loading</button></div>'
      : '';
    const cards = [historyLoaderMarkup(), notice,
      ...withCampaignEnding(messages, withActMarkers(messages,
        groupedAgentActivity(messages, committed, context, 'dm', activity),
        (context?.act_markers || []).filter(marker => !context?.campaign_ending ||
          marker.status !== 'complete' || marker.act_number !== context.campaign_ending.act_number)),
        context?.campaign_endings?.length ? context.campaign_endings : context?.campaign_ending,
        activityIsWorking(activity) || Boolean(activity.retryBasis)),
      ...live].filter(Boolean);
    if (!cards.length) {
      const openingPending = activityIsWorking(activity) &&
        String(activity.attemptId || '').startsWith('attempt-campaign-start-');
      const message = displayStatus === 'loading'
        ? '<div>Loading saved game controls…</div>' : openingPending
        ? '<div>Game Master is opening the campaign…</div>' +
          '<div class="muted">The opening narration is still in progress.</div>'
        : '<div>The DM is ready.</div>';
      updateChronology(container, '<div class="empty-state" data-chronology-key="empty-dm">' +
        `<div class="empty-icon">${openingPending ? '◇' : '◆'}</div>${message}</div>`, () => {});
      return;
    }
    updateChronology(container, cards.join(''), () => {
      container.querySelector('[data-retry-display]')?.addEventListener('click', () => {
        if (selectedActivityMatches(activity)) void openCampaign(campaignId, activity.sessionId);
      });
      container.querySelectorAll('[data-action="manual-retry"]').forEach((button) => {
        replaceClickHandler(button, () => { void retry(); });
      });
      wireMechanicsChoiceControls(container);
      container.querySelectorAll('[data-campaign-ending-choice]').forEach((button) => {
        replaceClickHandler(button, () => {
          if (!button.disabled) void submitInput('', { campaignEndingChoice: {
            ending_id: button.dataset.endingId, choice: button.dataset.campaignEndingChoice,
          } });
        });
      });
      wireResultCardActions(container);
    });
    const history = state.displayHistory;
    if (history?.sessionId === activity.sessionId) history.projections = projections;
    wireHistoryLoader(
      (next) => renderDMHistory(activity, next, history?.projections || projections, displayStatus),
      async (next) => {
        const enriched = await readDMProjection(campaignId, next, selectedCampaignOpening()?.id);
        if (state.displayHistory === history) history.projections = enriched;
      },
    );
  }

  // The saved ending offers one real user choice after its closing narration, never private prose.
  function withCampaignEnding(messages, cards, ending, busy = false) {
    if (Array.isArray(ending)) return ending.reduce(
      (result, item) => withCampaignEnding(messages, result, item, busy), cards);
    const result = [...cards];
    if (!ending || (ending.act_number !== null &&
        (!Number.isInteger(ending.act_number) || ending.act_number < 1)) ||
        typeof ending.ending_id !== 'string') return result;
    const position = messages.findIndex(message => message.message_id === ending.after_message_id);
    if (position < 0) return result;
    const key = chronologyKeyAttribute(`campaign-ending-${ending.after_message_id}`);
    let markup;
    if (ending.choice || ending.historical) {
      markup = `<div class="campaign-ending-marker"${key} aria-label="Campaign complete">` +
        `<span>✓ ${escapeHtml(ending.plan_title || 'Campaign')} · Complete</span></div>`;
    } else {
      const disabled = busy ? ' disabled' : '';
      const id = escapeHtml(ending.ending_id);
      const continuing = ending.open_world === true ?
        '<div class="campaign-ending-option">' +
        `<button type="button" class="btn btn-primary" data-campaign-ending-choice="continue" data-ending-id="${id}"${disabled}>` +
        'Continue adventuring <span aria-hidden="true">→</span></button>' +
        '<p>Keep playing in this world.</p></div>' : '';
      markup = `<section class="campaign-ending-card"${key} aria-label="Campaign complete">` +
        (ending.act_number === null ? '' :
          `<div class="campaign-ending-kicker">✓ Act ${ending.act_number} complete</div>`) +
        '<h2>Campaign complete</h2>' +
        `<p class="campaign-ending-summary">${ending.open_world === true
          ? `${escapeHtml(ending.plan_title || 'Your campaign')} has reached its conclusion.`
          : 'This ending brings your adventures here to a close.'}</p>` +
        `<div class="campaign-ending-options${continuing ? '' : ' single'}">${continuing}` +
        '<div class="campaign-ending-option">' +
        `<button type="button" class="btn ${continuing ? 'btn-secondary' : 'btn-primary'}" data-campaign-ending-choice="finish" data-ending-id="${id}"${disabled}>Finish the story</button>` +
        `<p>${ending.has_after_story ? 'Discover what the years ahead hold.' : 'Leave your adventure here.'}</p>` +
        '</div></div></section>';
    }
    result[position] = (result[position] || '') + markup;
    return result;
  }

  // Separate beginnings introduce their saved narration; endings and combined transitions follow it.
  function withActMarkers(messages, cards, markers) {
    const result = [...cards];
    const anchors = new Map();
    const seen = new Set();
    for (const marker of Array.isArray(markers) ? markers : []) {
      if (!Number.isInteger(marker.act_number) || marker.act_number < 1 ||
          !['complete', 'begins'].includes(marker.status)) continue;
      const id = `act-${marker.act_number}-${marker.status}`;
      if (seen.has(id)) continue;
      const position = messages.findIndex((message) => message.message_id === marker.after_message_id);
      if (position < 0) continue;
      seen.add(id);
      if (!anchors.has(position)) anchors.set(position, []);
      anchors.get(position).push({ ...marker, id });
    }
    for (const [position, group] of anchors) {
      group.sort((a, b) => a.act_number - b.act_number ||
        (a.status === 'complete' ? -1 : 1));
      const labels = group.map((marker) => `Act ${marker.act_number} ${marker.status}`);
      const key = chronologyKeyAttribute(group.map((marker) => marker.id).join('-'));
      const lines = labels.map((label, index) => index === labels.length - 1
        ? `<h3>${label}</h3>` : `<div class="act-transition-previous">${label}</div>`).join('');
      const chapter = `<section class="act-transition"${key} aria-label="${labels.join('. ')}">` +
        '<div class="act-transition-ornament" aria-hidden="true">◆</div>' + lines + '</section>';
      const narration = result[position] || '';
      result[position] = group.every((marker) => marker.status === 'begins')
        ? chapter + narration : narration + chapter;
    }
    return result;
  }

  // TALK → chronological card; THINK → collapsed card; TOOL_CARD → collapsed
  // outcome card. RETRY → retry pill. Provisional → streaming card.
  async function renderPlayerProjection(campaignId, sessionId = state.activeSessionId,
    { loadPanels = true, opening = null } = {}) {
    if (!sessionId || state.activeSessionId !== sessionId) return;
    const sessionRole = state.activeSessionRole;
    if (sessionRole === 'builder') {
      const loaded = await renderBuilderSession(sessionId);
      updateCampaignOpening(opening, 'history', loaded === false ? 'error' : 'ready');
      if (state.activeSessionId !== sessionId || state.activeSessionRole !== sessionRole) return;
      renderRulesetPanelProjection(null);
      return;
    }
    if (sessionRole === 'dm') {
      await renderDMSession(campaignId, sessionId, { opening });
      if (state.activeSessionId !== sessionId || state.activeCampaignId !== campaignId) return;
      if (loadPanels) await renderRulesetPanels(campaignId);
      return;
    }
    if (!state.bridge || !state.bridge.player_projection) return;
    let projections = [];
    try {
      projections = await state.bridge.player_projection(campaignId) || [];
    } catch (e) {
      projections = [];
    }
    if (state.activeSessionId !== sessionId || state.activeCampaignId !== campaignId) return;
    const canonicalSequences = projections
      .filter((projection) => !projection.provisional && Number.isInteger(projection.sequence))
      .map((projection) => projection.sequence);
    const activity = ensureSessionActivity({ sessionId, sessionRole, campaignId });
    activity.acknowledgedSequence = canonicalSequences.length
      ? Math.max(...canonicalSequences)
      : 0;
    activity.retryBasis = null;
    activity.interruptedAttemptId = null;
    const container = $('chronology');
    if (!projections.length) {
      updateChronology(container, '<div class="empty-state" data-chronology-key="empty-campaign">' +
        '<div class="empty-icon">◆</div>' +
        '<div>The chronology is empty. The campaign is ready for its first beat.</div>' +
        '</div>', () => {});
      return;
    }
    updateChronology(container, projections.map((projection) =>
      renderProjection(projection, activity)).join(''), () => {
      // Wire controls explicitly so the CSP never depends on inline handlers.
      container.querySelectorAll('[data-action="manual-retry"]').forEach((button) => {
        replaceClickHandler(button, () => { void retry(); });
      });
      wireMechanicsChoiceControls(container);
      wireResultCardActions(container);
    });
    await renderRulesetPanels(campaignId);
  }

  function wireMechanicsChoiceControls(container) {
    container.querySelectorAll('[data-action="resolve-mechanics-choice"]').forEach((button) => {
      replaceClickHandler(button, async () => {
        if (!state.bridge || !state.bridge.resolve_mechanics_choice || !state.activeCampaignId) {
          return;
        }
        const command = {
          campaignId: state.activeCampaignId,
          actionId: button.dataset.actionId,
          resolutionId: `choice-${button.dataset.actionId}-${button.dataset.choiceSequence || '0'}-${button.dataset.optionId}`,
          expectedRevision: Number(button.dataset.basisRevision),
          choiceId: button.dataset.choiceId,
          choiceSequence: Number(button.dataset.choiceSequence || 0),
          optionId: button.dataset.optionId,
          sessionId: state.activeSessionId || null,
        };
        container.querySelectorAll('[data-action="resolve-mechanics-choice"]')
          .forEach((item) => { item.disabled = true; });
        try {
          const resolved = await state.bridge.resolve_mechanics_choice(command);
          if (state.activeSessionId === command.sessionId) {
            await renderPlayerProjection(command.campaignId, command.sessionId);
          }
          if (resolved?.status !== 'awaiting_choice') {
            await respondToPlayerAction(command.campaignId, command.sessionId, command.actionId);
          }
        } catch (error) {
          if (state.activeSessionId === command.sessionId) {
            await renderPlayerProjection(command.campaignId, command.sessionId);
          }
        }
      });
    });
  }

  function renderProjection(p, activity = selectedActivity()) {
    // p is a PlayerProjection-shaped object: {kind, sequence, text,
    // safe_payload, collapsed_by_default, provisional, action_id, attempt_id}
    const kind = String(p.kind);
    const provisional = p.provisional ? ' provisional' : '';
    const payload = p.safe_payload || {};
    const projectionIdentity = narrationProjectionIdentity(p);
    const key = chronologyKeyAttribute(projectionIdentity);

    if (kind === 'talk') {
      return `<div class="story-card talk${provisional}"${key}>` +
        renderResponseBlocks(payload.response_blocks, p.text || '') +
        `</div>`;
    }
    if (kind === 'think') {
      // Older saved text has no provider-inspection ID, but uses the same closed disclosure.
      return `<details class="agent-thinking"${key} ` +
        `data-disclosure-key="${escapeHtml(projectionIdentity)}">` +
        '<summary>Thinking <span class="thinking-warning">May contain spoilers</span></summary>' +
        `<div class="thinking-body">${escapeHtml(p.text || '')}</div></details>`;
    }
    if (kind === 'tool_card') {
      // Shared campaign writes already appear in GM activity; their internal bookkeeping
      // receipt adds no player outcome. Preserve failures and authored result cards.
      const result = payload.result || {};
      if (payload.routine_activity === 'character_update' && payload.status === 'committed' &&
          result.status === 'committed' && result.success !== false && result.result_card == null) return '';
      return toolResultMarkup(p.safe_payload || {}, key);
    }
    if (kind === 'mechanics_choice') {
      const options = Array.isArray(payload.options) ? payload.options : [];
      const buttons = options.map((option) =>
        `<button class="btn btn-primary btn-sm" data-action="resolve-mechanics-choice" ` +
        `data-action-id="${escapeHtml(payload.action_id || p.action_id || '')}" ` +
        `data-basis-revision="${escapeHtml(String(payload.basis_revision ?? ''))}" ` +
        `data-choice-id="${escapeHtml(payload.choice_id || '')}" ` +
        `data-choice-sequence="${escapeHtml(String(payload.choice_sequence ?? 0))}" ` +
        `data-option-id="${escapeHtml(option.option_id || '')}">` +
        `${escapeHtml(option.label || option.option_id || '')}</button>`
      ).join('');
      return `<div class="story-card mechanics-choice-card"${key}>` +
        `<div class="story-header">Choice required</div>` +
        `<div class="story-text">${escapeHtml(payload.prompt || '')}</div>` +
        `<div class="mechanics-choice-options">${buttons}</div></div>`;
    }
    if (kind === 'retry') {
      const working = activityIsWorking(activity);
      if (activity) activity.interruptedAttemptId =
        p.safe_payload && p.safe_payload.failed_attempt_id || p.attempt_id;
      if (activity) activity.retryBasis = {
        interruption_id: p.safe_payload && p.safe_payload.interruption_id,
        failed_attempt_id: activity.interruptedAttemptId,
        resolving_retry_id: payload.resolving_retry_id || null,
        can_redirect: payload.can_redirect === true,
      };
      const disabled = working ? ' disabled' : '';
      const openingInterrupted = payload.recovery_kind === 'opening';
      const interruptionMessage = openingInterrupted
        ? 'Campaign opening narration was interrupted. The campaign is saved; retry the opening.'
        : (payload.saved_changes === true
          ? "Game changes were saved, but Sylvia couldn't finish the narration."
          : "No game changes were saved for this turn. Sylvia couldn't finish the response.");
      return `<div class="story-card retry-card"${key}>` +
        `<div>${interruptionMessage}</div>` +
        `<button class="btn btn-secondary btn-sm" data-action="manual-retry"${disabled}>Manual Retry</button>` +
        `</div>`;
    }
    return '';
  }

  // ---- Campaign construction: confirm stable identity, then bind Builder lane ----
  function selectedCampaignPlan() {
    const planId = $('construction-template').value;
    return state.campaignCreationCatalog.find(
      (item) => item.kind === 'campaign_plan' && item.id === planId
    ) || null;
  }

  function updateConstructionNameSuggestion(plan = selectedCampaignPlan()) {
    if (!state.constructionNameSuggested || !plan?.title) return;
    $('construction-name').value = plan.title;
  }

  function updateReusableLibraryDeleteControls() {
    const plan = selectedCampaignPlan();
    $('delete-reusable-plan-btn').hidden = !(plan?.reusable && plan.content_sha256);
    $('export-campaign-plan-btn').hidden = !(plan?.content_sha256);
    const player = (state.campaignPartyOptions?.player_archetypes || []).find(
      (item) => item.id === $('rift-archetype').value
    );
    $('delete-reusable-player-btn').hidden = !(player?.reusable && player.content_sha256);
  }

  async function deleteReusableLibraryEntry(item) {
    if (!item?.reusable || !item.id || !item.content_sha256 ||
        !state.bridge?.delete_reusable_library_entry) return;
    const typeLabel = item.kind === 'campaign_plan'
      ? 'Campaign Plan' : item.kind === 'companion' ? 'Companion' : 'Character';
    const name = item.title || item.name || item.label || item.id;
    if (!window.confirm(
      `Permanently delete the saved ${typeLabel} “${name}”? ` +
      'Its Builder chat and existing campaigns will remain.'
    )) return;
    const constructionGeneration = state.constructionPanelGeneration;
    const campaignId = state.activeCampaignId;
    const sessionId = state.activeSessionId;
    const stillViewingSetup = () => constructionGeneration === state.constructionPanelGeneration &&
      campaignId === state.activeCampaignId && sessionId === state.activeSessionId &&
      !$('construction-panel').hidden;
    let result;
    try {
      result = await state.bridge.delete_reusable_library_entry({
        kind: item.kind,
        artifact_id: item.id,
        expected_content_sha256: item.content_sha256,
        confirmation: `permanently delete ${item.id}`,
      });
    } catch (e) {
      if (!stillViewingSetup()) return;
      setInlineStatus(friendlyCommandError(
        `Could not delete saved ${typeLabel}`, e
      ), 'construction');
      return;
    }
    if (stillViewingSetup()) {
      setInlineStatus(result?.deleted
        ? `Deleted saved ${typeLabel} “${name}”`
        : `Saved ${typeLabel} “${name}” was already absent`, 'construction');
    }
    try {
      const catalog = await state.bridge.campaign_creation_catalog() || [];
      state.campaignCreationCatalog = catalog;
      state.reusableCampaignPlans = catalog.filter(
        (entry) => entry.kind === 'campaign_plan' && entry.reusable === true
      );
      if (!stillViewingSetup()) return;
      updateConstructionPlans();
    } catch (e) {
      if (!stillViewingSetup()) return;
      setInlineStatus(result?.deleted
        ? `Deleted saved ${typeLabel} “${name}”; refresh the library to update this list`
        : `Saved ${typeLabel} “${name}” was already absent; library refresh failed`, 'construction');
    }
  }

  async function deleteSelectedReusablePlan() {
    const plan = selectedCampaignPlan();
    if (plan) await deleteReusableLibraryEntry(plan);
  }

  async function exportSelectedCampaignPlan() {
    const plan = selectedCampaignPlan();
    if (!plan?.content_sha256 || !state.bridge?.export_campaign_plan_file) return;
    const sessionId = state.activeSessionId;
    const stillSelected = () => sessionId === state.activeSessionId &&
      selectedCampaignPlan()?.id === plan.id;
    $('export-campaign-plan-btn').disabled = true;
    try {
      const result = await state.bridge.export_campaign_plan_file(
        `export-plan-${crypto.randomUUID()}`, plan.id, plan.content_sha256,
      );
      if (stillSelected()) {
        setInlineStatus(result.status === 'cancelled'
          ? 'Campaign Plan export cancelled'
          : `Exported ${result.file_name || plan.title} with its Setting and Ruleset`, 'construction');
      }
    } catch (error) {
      if (stillSelected()) setInlineStatus('Campaign Plan export failed', 'construction');
    } finally {
      $('export-campaign-plan-btn').disabled = false;
    }
  }

  function usesBundledCampaignSetup() {
    const plan = selectedCampaignPlan();
    return Boolean(plan?.setup_id && plan.setup_id === state.riftTemplate?.template_id);
  }

  function campaignPartyRequestKey() {
    return JSON.stringify([
      $('construction-package').value,
      $('construction-setting').value,
      $('construction-template').value,
    ]);
  }

  function selectedPartySourceIdentity() {
    const plan = selectedCampaignPlan();
    if (!plan?.id || !plan.content_sha256) return null;
    const binding = (source) => source && source.id && source.version && source.content_sha256
      ? { id: source.id, version: source.version, content_sha256: source.content_sha256 }
      : null;
    return {
      plan_id: plan.id,
      plan_sha256: plan.content_sha256,
      ruleset: binding(plan.ruleset),
      setting: binding(plan.campaign_setting),
    };
  }

  function pendingPartyStorageId(campaignId, sessionId) {
    return JSON.stringify([campaignId, sessionId]);
  }

  // Persistence may reorder JSON keys; compare the exact identity values, not serialization order.
  function partySourceIdentityMatches(left, right) {
    if (!left || !right) return false;
    const bindingMatches = (a, b) => !a || !b ? a === b :
      a.id === b.id && a.version === b.version && a.content_sha256 === b.content_sha256;
    return left.plan_id === right.plan_id && left.plan_sha256 === right.plan_sha256 &&
      bindingMatches(left.ruleset, right.ruleset) && bindingMatches(left.setting, right.setting);
  }

  function pendingPartySelections(storage) {
    try {
      const saved = JSON.parse(storage.getItem(PENDING_PARTY_SELECTION_KEY) || '{}');
      return saved && saved.version === 1 && saved.interviews &&
        typeof saved.interviews === 'object' ? saved.interviews : {};
    } catch (_error) {
      return {};
    }
  }

  function savePendingPartySelection(storage, campaignId, sessionId, source, selection) {
    if (!storage || !campaignId || !sessionId || !source || !selection) return;
    const interviews = pendingPartySelections(storage);
    interviews[pendingPartyStorageId(campaignId, sessionId)] = {
      source,
      selection: {
        archetype_id: String(selection.archetype_id || ''),
        companion_ids: Array.isArray(selection.companion_ids)
          ? selection.companion_ids.map(String) : [],
      },
    };
    try {
      storage.setItem(PENDING_PARTY_SELECTION_KEY, JSON.stringify({ version: 1, interviews }));
    } catch (_error) { /* best-effort UI draft persistence */ }
  }

  function loadPendingPartySelection(storage, campaignId, sessionId, source) {
    if (!storage || !campaignId || !sessionId || !source) return null;
    const saved = pendingPartySelections(storage)[pendingPartyStorageId(campaignId, sessionId)];
    return saved && partySourceIdentityMatches(saved.source, source)
      ? saved.selection : null;
  }

  function clearPendingPartySelection(storage, campaignId, sessionId) {
    if (!storage || !campaignId || !sessionId) return;
    const interviews = pendingPartySelections(storage);
    delete interviews[pendingPartyStorageId(campaignId, sessionId)];
    try {
      storage.setItem(PENDING_PARTY_SELECTION_KEY, JSON.stringify({ version: 1, interviews }));
    } catch (_error) { /* best-effort UI draft persistence */ }
  }

  function persistSelectedPendingParty() {
    const source = selectedPartySourceIdentity();
    if (!source || !state.constructionDraftId || !state.activeSessionId) return;
    savePendingPartySelection(
      localStorage, state.constructionDraftId, state.activeSessionId, source,
      selectedCampaignParty(),
    );
  }

  function selectedCampaignParty() {
    return {
      archetype_id: $('rift-archetype').value,
      companion_ids: [...$('rift-companions').querySelectorAll(
        'input[type="checkbox"]:checked'
      )].map((input) => input.value),
    };
  }

  function campaignPartyOptionsReady() {
    return state.campaignPartyOptionsStatus === 'ready' &&
      state.campaignPartyOptionsKey === campaignPartyRequestKey();
  }

  function selectedPlanRequiresPartyOptions() {
    return Boolean(selectedCampaignPlan());
  }

  function campaignPartySelectionProblem() {
    if (!selectedPlanRequiresPartyOptions()) return null;
    if (currentCampaignPartyRepair()) return currentCampaignPartyRepair().message;
    if (!campaignPartyOptionsReady()) {
      return state.campaignPartyOptionsStatus === 'loading'
        ? 'Campaign party choices are still loading'
        : 'Campaign party choices are unavailable for the current selections';
    }
    const options = state.campaignPartyOptions || {};
    const policy = selectedStartingPartyPolicy();
    const player = (options.player_archetypes || []).find(
      (item) => item.id === $('rift-archetype').value
    );
    if (!player || player.campaign_ready === false) {
      return 'A campaign-ready Player Character is required';
    }
    const required = Array.isArray(policy.required_companions)
      ? policy.required_companions : [];
    const missingRequired = required.some((reference) => {
      const item = (options.companions || []).find((candidate) =>
        referenceMatches(candidate.source_ref, reference)
      );
      return !item || item.campaign_ready === false;
    });
    if (missingRequired) return 'A required Companion is unavailable or incomplete';
    const minimum = Number(policy.custom_companions?.minimum ?? 0);
    const maximum = Number(policy.custom_companions?.maximum ?? 0);
    const count = selectedCustomCompanionInputs().length;
    return count < minimum || count > maximum
      ? `Choose between ${minimum} and ${maximum} custom companions`
      : null;
  }

  function updateCampaignPartyActionAvailability() {
    const repair = currentCampaignPartyRepair();
    $('construction-party-repair').hidden = !repair;
    $('construction-party-repair-message').textContent = repair?.message || '';
    updateCharacterProfileControls();
    const ready = !selectedPlanRequiresPartyOptions() ||
      (campaignPartyOptionsReady() && !campaignPartySelectionProblem());
    if (currentConstructionStepId() === 'participants') {
      $('construction-next-btn').disabled = !ready;
    }
    if (currentConstructionStepId() === 'review') {
      $('begin-construction-btn').disabled = !ready || state.campaignConfirmationPending;
    }
  }

  // Keep repair scoped to the selected Interview and content, never another open session.
  function currentCampaignPartyRepair() {
    const repair = state.campaignPartyRepair;
    return repair && repair.campaignId === state.activeCampaignId &&
      repair.sessionId === state.activeSessionId && repair.key === campaignPartyRequestKey()
      ? repair : null;
  }

  // Reselection changes only the visible draft; the ordinary Continue action saves the answer.
  function reselectCampaignParty() {
    if (!currentCampaignPartyRepair() || !campaignPartyOptionsReady()) return;
    state.campaignPartyRepair = null;
    renderCampaignPartyOptions(state.campaignPartyOptions, null);
    if (constructionStepIds.indexOf(currentConstructionStepId()) >
        constructionStepIds.indexOf('participants')) {
      void navigateConstructionInterview('participants');
    }
    updateCampaignPartyActionAvailability();
  }

  async function refreshCampaignPartyOptions({ preserveSelection = null, savedTarget = null } = {}) {
    if (state.constructionRestoreInProgress) return null;
    const key = campaignPartyRequestKey();
    const rulesetSelection = $('construction-package').value;
    const settingId = $('construction-setting').value;
    const planId = $('construction-template').value;
    const generation = ++state.campaignPartyOptionsGeneration;
    const campaignId = state.activeCampaignId;
    const sessionId = state.activeSessionId;
    const selection = preserveSelection || selectedCampaignParty();
    const stillCurrent = () => generation === state.campaignPartyOptionsGeneration &&
      key === campaignPartyRequestKey() && campaignId === state.activeCampaignId &&
      sessionId === state.activeSessionId;
    state.campaignPartyOptionsKey = key;
    state.campaignPartyOptionsStatus = 'loading';
    renderCampaignPartyOptions(null, selection, 'Loading campaign party choices…');
    updateCampaignPartyActionAvailability();
    if (!state.bridge?.campaign_party_options || !rulesetSelection || !settingId ||
        !planId || planId === 'custom') {
      if (!stillCurrent()) return null;
      state.campaignPartyOptionsStatus = 'error';
      renderCampaignPartyOptions(null, null, 'Choose a Ruleset, Setting, and Campaign Plan first.');
      updateCampaignPartyActionAvailability();
      return null;
    }
    try {
      const options = await state.bridge.campaign_party_options({
        ruleset_selection: rulesetSelection,
        setting_id: settingId,
        plan_id: planId,
      });
      if (!stillCurrent()) return null;
      const reusableRiftTemplate = usesBundledCampaignSetup() &&
        options?.template_id === state.riftTemplate?.template_id;
      if (!options || (options.template_id !== planId && !reusableRiftTemplate)) {
        throw new Error('Campaign party choices did not match the selected Campaign Plan');
      }
      state.campaignPartyOptionsStatus = 'ready';
      const missingCompanions = (selection?.companion_ids || []).filter((id) =>
        !(options.companions || []).some((item) => item.id === id && item.campaign_ready !== false));
      const missingPlayer = selection?.archetype_id && !(options.player_archetypes || []).some(
        (item) => item.id === selection.archetype_id && item.campaign_ready !== false);
      const targetChanged = savedTarget &&
        !partySourceIdentityMatches(savedTarget, selectedPartySourceIdentity());
      if (targetChanged || missingCompanions.length || missingPlayer) {
        state.campaignPartyRepair = {
          campaignId, sessionId, key,
          message: (targetChanged ? 'The selected campaign content has changed since this Interview was saved. ' :
            'A saved Player Character or companion is unavailable for the current content. ' +
            'The selected content may have changed. ') + 'Your saved answers and character files are kept. ' +
            'Choose Reselect party to select compatible characters or adapt a Universal character card.',
        };
      }
      renderCampaignPartyOptions(options, selection);
      updateCampaignPartyActionAvailability();
      return options;
    } catch (e) {
      if (!stillCurrent()) return null;
      state.campaignPartyOptionsStatus = 'error';
      renderCampaignPartyOptions(
        null,
        null,
        friendlyCommandError('Campaign party choices are unavailable', e),
      );
      updateCampaignPartyActionAvailability();
      return null;
    }
  }

  // General sources are selectable only for adaptation, never as ready party members.
  function renderCharacterProfiles() {
    const select = $('character-profile-select');
    if (!select) return;
    const previous = select.value;
    const profiles = (state.campaignCreationCatalog || []).filter((item) => item.kind === 'character_profile');
    select.innerHTML = '<option value="">Choose a Universal character card</option>' +
      profiles.map((item) => `<option value="${escapeHtml(item.id)}">` +
        `${escapeHtml(item.name)} · ${escapeHtml(item.id.slice(-8))} — Needs adaptation</option>`).join('');
    select.value = profiles.some((item) => item.id === previous) ? previous : '';
    updateCharacterProfileControls();
  }

  // Match the Companion allowance and keep new-chat launch activity scoped to its Interview.
  function updateCharacterProfileControls() {
    const group = $('character-profile-picker'), select = $('character-profile-select');
    const button = $('adapt-character-profile-btn'), summary = $('character-profile-summary');
    if (!group || !select || !button || !summary) return;
    const profiles = (state.campaignCreationCatalog || []).filter((item) => item.kind === 'character_profile');
    const profile = profiles.find((item) => item.id === select.value);
    const maximum = Number(selectedStartingPartyPolicy().custom_companions?.maximum ?? 0);
    const allowed = campaignPartyOptionsReady() && maximum > selectedCustomCompanionInputs().length;
    const busy = state.builderLaunchPending.has(state.activeSessionId) || selectedSessionIsWorking();
    group.hidden = !allowed;
    select.disabled = busy || !profiles.length;
    button.disabled = !allowed || busy || !profile;
    $('rift-create-companion').disabled = busy;
    summary.textContent = profile ? `${profile.summary} — Needs adaptation to this campaign.` :
      profiles.length ? 'Choose a source to adapt; the original will stay unchanged.' :
        'Import a character card or create and save one in a Builder chat first.';
  }

  async function adaptSelectedCharacterProfile() {
    updateCharacterProfileControls();
    if ($('adapt-character-profile-btn').disabled) return;
    const profile = (state.campaignCreationCatalog || []).find((item) =>
      item.kind === 'character_profile' && item.id === $('character-profile-select').value);
    if (profile) await startCreationSubflow('companion', `Adapt ${profile.name} as Companion`, null, profile);
  }

  function updateConstructionMode() {
    renderCampaignPlanBrowser();
    const selection = selectedCampaignParty();
    updateConstructionNameSuggestion();
    if (state.campaignPartyOptionsKey !== campaignPartyRequestKey()) {
      state.campaignPartyOptions = null;
      state.campaignPartyOptionsStatus = 'idle';
    }
    applyStartingPartyPolicy();
    updateReusableLibraryDeleteControls();
    $('campaign-player-label').textContent = usesBundledCampaignSetup()
      ? 'Player Character archetype'
      : 'Player Character';
    if ($('construction-panel').parentElement === $('chronology') && $('construction-panel').hidden) {
      setInlineStatus('Loading campaign Interview…', 'construction');
    } else if (!usesBundledCampaignSetup()) {
      setInlineStatus('Create New opens a saved Builder chat. Return to this interview whenever you are ready.', 'construction');
    }
    void refreshCampaignPartyOptions({ preserveSelection: selection });
  }

  function selectedStartingPartyPolicy() {
    return state.campaignPartyOptions?.starting_party || selectedCampaignPlan()?.starting_party || {
      player_character: { mode: 'compatible' },
      required_companions: [],
      custom_companions: { minimum: 0, maximum: 5 },
    };
  }

  function referenceMatches(left, right) {
    return Boolean(left && right && left.id === right.id && left.path === right.path);
  }

  function selectedCustomCompanionInputs() {
    return [...$('rift-companions').querySelectorAll('input[type="checkbox"]')]
      .filter((input) => input.checked && input.dataset.required !== 'true');
  }

  function enforceCompanionMaximum() {
    const policy = selectedStartingPartyPolicy();
    const maximum = Number(policy.custom_companions?.maximum ?? 0);
    const inputs = [...$('rift-companions').querySelectorAll('input[type="checkbox"]')];
    const selectedCustom = inputs.filter(
      (input) => input.checked && input.dataset.required !== 'true'
    );
    inputs.forEach((input) => {
      if (input.dataset.profileRequired === 'true') {
        input.checked = false;
        input.disabled = true;
      } else if (input.dataset.required === 'true') {
        input.checked = true;
        input.disabled = true;
      } else {
        input.disabled = !input.checked && selectedCustom.length >= maximum;
      }
    });
    $('rift-create-companion').hidden = maximum === 0 || selectedCustom.length >= maximum;
    updateCampaignPartyActionAvailability();
  }

  function applyStartingPartyPolicy() {
    if (!state.campaignPartyOptions || !$('rift-archetype') || !$('rift-companions')) return;
    const policy = selectedStartingPartyPolicy();
    const playerPolicy = policy.player_character || { mode: 'compatible' };
    const archetypes = state.campaignPartyOptions.player_archetypes || [];
    if (playerPolicy.mode === 'fixed') {
      const fixed = archetypes.find((item) =>
        referenceMatches(item.source_ref, playerPolicy.character)
      );
      [...$('rift-archetype').options].forEach((option) => {
        option.hidden = option.value !== fixed?.id;
      });
      $('rift-archetype').value = fixed?.campaign_ready === false ? '' : fixed?.id || '';
      $('rift-archetype').disabled = true;
      $('rift-create-player').hidden = true;
    } else {
      [...$('rift-archetype').options].forEach((option) => { option.hidden = false; });
      $('rift-archetype').disabled = false;
      $('rift-create-player').hidden = false;
      const current = archetypes.find((item) => item.id === $('rift-archetype').value);
      if (!current || current.campaign_ready === false) {
        $('rift-archetype').value = '';
      }
    }

    const required = Array.isArray(policy.required_companions)
      ? policy.required_companions : [];
    const minimum = Number(policy.custom_companions?.minimum ?? 0);
    const maximum = Number(policy.custom_companions?.maximum ?? 0);
    const inputs = [...$('rift-companions').querySelectorAll('input[type="checkbox"]')];
    inputs.forEach((input) => {
      const item = (state.campaignPartyOptions.companions || []).find(
        (candidate) => candidate.id === input.value
      );
      const isRequired = required.some((reference) =>
        referenceMatches(item?.source_ref, reference)
      );
      input.dataset.required = isRequired ? 'true' : 'false';
      const profileRequired = input.dataset.profileRequired === 'true';
      input.disabled = isRequired || profileRequired;
      if (profileRequired) input.checked = false;
      else if (isRequired) input.checked = true;
      const label = input.closest('label');
      if (label) {
        label.hidden = !isRequired && maximum === 0 && input.dataset.reusable !== 'true';
      }
    });
    const custom = selectedCustomCompanionInputs();
    custom.slice(maximum).forEach((input) => { input.checked = false; });
    $('rift-companion-policy').textContent = required.length
      ? `(${required.length} required; choose ${minimum}–${maximum} additional)`
      : maximum === 0
        ? '(none allowed by this Campaign Plan)'
        : `(choose ${minimum}–${maximum})`;
    enforceCompanionMaximum();
    updateCampaignPartyActionAvailability();
  }

  function exactBindingMatches(entry, binding) {
    return Boolean(entry && binding &&
      entry.package_id === binding.id &&
      entry.version === binding.version &&
      entry.sha256 === binding.content_sha256);
  }

  function resolveCampaignCreationOptions(catalog, rulesetSelection, settingId = '') {
    const rulesetChoice = catalog.find(
      (item) => item.kind === 'ruleset' && (item.selection_id === rulesetSelection ||
        item.selection_aliases?.includes(rulesetSelection))
    ) || null;
    const sourceRuleset = rulesetChoice ? {
      id: rulesetChoice.id,
      version: rulesetChoice.version,
      content_sha256: rulesetChoice.content_sha256,
    } : null;
    const settings = rulesetChoice?.compatible_settings || [];
    const setting = settings.find((item) => item.id === settingId) || settings[0] || null;
    const ruleset = setting?.ruleset || sourceRuleset;
    const plans = catalog.filter((item) =>
      item.kind === 'campaign_plan' &&
      (setting?.ruleset
        ? item.ruleset?.id === ruleset?.id && item.campaign_setting?.id === setting.id
        : exactBindingMatches({
        package_id: ruleset?.id,
        version: ruleset?.version,
        sha256: ruleset?.content_sha256,
      }, item.ruleset) &&
      exactBindingMatches({
        package_id: setting?.id,
        version: setting?.version,
        sha256: setting?.content_sha256,
      }, item.campaign_setting))
    );
    return { rulesetChoice, ruleset, settings, setting, plans };
  }

  function selectedRulesetBinding() {
    return resolveCampaignCreationOptions(
      state.campaignCreationCatalog,
      $('construction-package').value,
      $('construction-setting').value,
    ).ruleset;
  }

  function selectedCandidateSettingBindings() {
    return resolveCampaignCreationOptions(
      state.campaignCreationCatalog,
      $('construction-package').value,
      $('construction-setting').value,
    ).settings;
  }

  function savedPlanCopyLabel(plan) {
    return plan?.reusable && typeof plan.content_sha256 === 'string'
      ? `Saved copy ${plan.content_sha256.slice(0, 12)}` : '';
  }

  // Browsing filters never write an Interview answer; only the existing Continue action does.
  let campaignPlanBrowser = null;
  function renderCampaignPlanBrowser() {
    const container = $('campaign-plan-browser');
    if (!container || !window.SylviaPlanBrowser) return;
    if (!campaignPlanBrowser) {
      campaignPlanBrowser = window.SylviaPlanBrowser.create(container, (planId) => {
        $('construction-template').value = planId;
        updateConstructionMode();
      });
    }
    $('construction-template').hidden = true;
    container.hidden = false;
    campaignPlanBrowser.update({
      plans: resolveCampaignCreationOptions(state.campaignCreationCatalog,
        $('construction-package').value, $('construction-setting').value).plans,
      selectedId: $('construction-template').value,
      contextKey: [state.activeSessionId, $('construction-package').value,
        $('construction-setting').value].join('|'),
    });
  }

  function updateConstructionPlans() {
    const previous = $('construction-template').value;
    const compatiblePlans = resolveCampaignCreationOptions(
      state.campaignCreationCatalog,
      $('construction-package').value,
      $('construction-setting').value,
    ).plans;
    $('construction-template').innerHTML = '<option value="">Choose a Campaign Plan…</option>' +
      compatiblePlans.map((item) =>
      `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)}` +
      `${item.reusable ? ` · ${escapeHtml(savedPlanCopyLabel(item) || 'Saved')}` : ''}</option>`
    ).join('') +
      '<option value="custom">Create a new Campaign Plan…</option>';
    if (compatiblePlans.some(item => item.id === previous)) {
      $('construction-template').value = previous;
    }
    updateConstructionMode();
  }

  function updateConstructionCompatibility() {
    const catalogSettings = new Map(
      state.campaignCreationCatalog
        .filter((item) => item.kind === 'campaign_setting')
        .map((item) => [`${item.id}|${item.version}`, item])
    );
    const settings = selectedCandidateSettingBindings();
    $('construction-setting').innerHTML = settings.length
      ? settings.map((binding) => {
        const item = catalogSettings.get(`${binding.id}|${binding.version}`);
        const label = item?.title || `${binding.id} ${binding.version}`;
        return `<option value="${escapeHtml(binding.id)}">${escapeHtml(label)}</option>`;
      }).join('')
      : '<option value="">No compatible installed Setting</option>';
    updateConstructionPlans();
  }

  function constructionSections() {
    return [...$('construction-panel').querySelectorAll('[data-construction-step]')].filter(
      (section) => ($('construction-package').value ||
        ['ruleset', 'name', 'boundaries_preferences', 'specific_requests', 'review']
          .includes(section.dataset.stepId)) &&
        (!section.hasAttribute('data-contribution-step') ||
          Boolean(state.constructionContributions[section.dataset.stepId]))
    );
  }

  function activeConstructionStepIds() {
    return constructionSections().map((section) => section.dataset.stepId);
  }

  function currentConstructionStepId() {
    return activeConstructionStepIds()[state.constructionStep] || 'ruleset';
  }

  function constructionReviewText() {
    const companionCount = $('rift-companions').querySelectorAll(
      'input[type="checkbox"]:checked'
    ).length;
    const ruleset = $('construction-package').selectedOptions[0]?.textContent || 'No Ruleset';
    const requests = $('construction-no-requests').checked
      ? 'No additional requests'
      : $('construction-premise').value.trim();
    return [
      `Ruleset: ${ruleset}`,
      ...($('construction-package').value ? [
        `Setting: ${$('construction-setting').selectedOptions[0]?.textContent || ''}`,
        `Plan: ${$('construction-template').selectedOptions[0]?.textContent || ''}`,
      ] : []),
      `Campaign: ${$('construction-name').value.trim()}`,
      ...($('construction-package').value ? [
        `Player Character: ${selectedRiftPlayerName()}`,
        `Companions: ${companionCount}`,
      ] : []),
      `Boundaries: ${$('construction-boundaries').value.trim() || 'None specified'}`,
      `Preferences: ${$('construction-preferences').value.trim() || 'None specified'}`,
      `Specific requests: ${requests || 'None'}`,
    ].map((line) => `<div>${escapeHtml(line)}</div>`).join('');
  }

  function showConstructionStep(step) {
    const sections = constructionSections();
    $('construction-panel').querySelectorAll('[data-construction-step]').forEach(
      (section) => { section.hidden = true; }
    );
    state.constructionStep = Math.max(0, Math.min(step, sections.length - 1));
    sections[state.constructionStep].hidden = false;
    $('construction-progress').textContent =
      `Step ${state.constructionStep + 1} of ${sections.length}`;
    $('construction-back-btn').hidden = state.constructionStep === 0;
    $('construction-back-btn').disabled = false;
    $('construction-next-btn').hidden = state.constructionStep === sections.length - 1;
    // A party restriction belongs to that step, not the earlier choices used to repair it.
    $('construction-next-btn').disabled = false;
    $('begin-construction-btn').hidden = state.constructionStep !== sections.length - 1;
    if (state.constructionStep === sections.length - 1) {
      $('construction-review').innerHTML = constructionReviewText();
      $('save-reusable-plan-btn').hidden = !state.hasDerivedCampaignPlan;
    }
    updateCampaignPartyActionAvailability();
    const focusable = sections[state.constructionStep].querySelector(
      'select:not([hidden]), input:not([hidden]), textarea:not([hidden])'
    );
    if (focusable) focusable.focus();
  }

  function showConstructionStepId(stepId) {
    const index = activeConstructionStepIds().indexOf(stepId);
    showConstructionStep(index >= 0 ? index : 0);
  }

  function constructionStepIsValid() {
    const stepId = currentConstructionStepId();
    if (stepId === 'plan' && !$('construction-template').value) {
      setInlineStatus('Choose a Campaign Plan before continuing.', 'construction');
      return false;
    }
    if (stepId === 'plan' && $('construction-template').value === 'custom') {
      void startCreationSubflow('plan', 'Create a new Campaign Plan');
      return false;
    }
    if (stepId === 'setting' && !$('construction-setting').value) {
      setInlineStatus(selectedCandidateSettingBindings().length
          ? 'Choose a Campaign Setting before continuing.'
          : 'No compatible Setting is installed. Create one or go Back.', 'construction');
      return false;
    }
    if (stepId === 'name' && !$('construction-name').value.trim()) {
      setInlineStatus('Campaign name required', 'construction');
      return false;
    }
    if (stepId === 'name') {
      try { requireTextLength($('construction-name').value.trim(), 'Campaign name', 120); }
      catch (error) { setInlineStatus(error.message, 'construction'); return false; }
    }
    if (stepId === 'participants') {
      const partyProblem = campaignPartySelectionProblem();
      if (partyProblem) {
        setInlineStatus(partyProblem, 'construction');
        return false;
      }
      if (!selectedPlanRequiresPartyOptions()) return true;
      const policy = selectedStartingPartyPolicy();
      const minimum = Number(policy.custom_companions?.minimum ?? 0);
      const maximum = Number(policy.custom_companions?.maximum ?? 0);
      const count = selectedCustomCompanionInputs().length;
      if (count < minimum || count > maximum) {
        setInlineStatus(`Choose between ${minimum} and ${maximum} custom companions`, 'construction');
        return false;
      }
    }
    if (stepId === 'specific_requests' &&
        !$('construction-no-requests').checked && !$('construction-premise').value.trim()) {
      setInlineStatus('Enter a request or choose No additional requests', 'construction');
      return false;
    }
    if (stepId.endsWith('_contribution')) {
      const contribution = state.constructionContributions[stepId];
      if (!contribution) return false;
      const answers = contributionAnswer(stepId).value.answers;
      const missing = contribution.questions.some((question) => {
        const condition = question.condition;
        const active = !condition || answers[condition.question_id] === condition.equals;
        return active && question.required && !String(answers[question.id] ?? '').trim();
      });
      if (missing) {
        setInlineStatus('Answer every required artifact question', 'construction');
        return false;
      }
    }
    return true;
  }

  const constructionStepIds = [
    'ruleset', 'setting', 'plan', 'ruleset_contribution',
    'setting_contribution', 'plan_contribution', 'name', 'participants',
    'boundaries_preferences', 'specific_requests', 'review'
  ];

  function contributionAnswer(stepId) {
    const contribution = state.constructionContributions[stepId];
    const answers = {};
    const visible = [];
    if (!contribution) return { visible: '', value: { contribution_id: '', answers } };
    contribution.questions.forEach((question) => {
      const input = document.querySelector(
        `[data-contribution-step-id="${CSS.escape(stepId)}"]` +
        `[data-contribution-question-id="${CSS.escape(question.id)}"]`
      );
      if (!input || input.closest('[data-contribution-question]').hidden) return;
      const value = input.value.trim();
      if (!value) return;
      answers[question.id] = value;
      const label = question.type === 'choice'
        ? question.options.find((option) => option.id === value)?.label || value
        : value;
      visible.push(`${question.prompt} ${label}`);
    });
    return {
      visible: visible.join('; ') || 'No additional answers',
      value: { contribution_id: contribution.id, answers },
    };
  }

  function contributionNeedsAdaptive(stepId, captured) {
    const contribution = state.constructionContributions[stepId];
    if (!contribution) return false;
    const answers = captured.value.answers;
    return contribution.questions.some((question) => {
      const condition = question.condition;
      const active = !condition || answers[condition.question_id] === condition.equals;
      return active && question.adaptive && Object.hasOwn(answers, question.id);
    });
  }

  function updateContributionConditions(stepId) {
    const contribution = state.constructionContributions[stepId];
    if (!contribution) return;
    const answers = contributionAnswer(stepId).value.answers;
    const section = document.querySelector(`[data-step-id="${stepId}"]`);
    contribution.questions.forEach((question) => {
      const wrapper = section.querySelector(
        `[data-contribution-question="${question.id}"]`
      );
      const condition = question.condition;
      wrapper.hidden = Boolean(condition) && answers[condition.question_id] !== condition.equals;
    });
  }

  function renderContribution(stepId, contribution) {
    state.constructionContributions[stepId] = contribution;
    const section = document.querySelector(`[data-step-id="${stepId}"]`);
    section.querySelector('[data-contribution-title]').textContent = contribution.title;
    const container = section.querySelector('[data-contribution-questions]');
    container.innerHTML = contribution.questions.map((question) => {
      const required = question.required ? ' required' : '';
      const control = question.type === 'choice'
        ? `<select data-contribution-step-id="${escapeHtml(stepId)}" ` +
          `data-contribution-question-id="${escapeHtml(question.id)}"${required}>` +
          `<option value="">Choose…</option>` + question.options.map((option) =>
            `<option value="${escapeHtml(option.id)}">${escapeHtml(option.label)}</option>`
          ).join('') + '</select>'
        : `<textarea rows="3" maxlength="2000" autocomplete="off" ` +
          `data-contribution-step-id="${escapeHtml(stepId)}" ` +
          `data-contribution-question-id="${escapeHtml(question.id)}"${required}></textarea>`;
      return `<label data-contribution-question="${escapeHtml(question.id)}">` +
        `${escapeHtml(question.prompt)}${control}</label>`;
    }).join('');
    container.querySelectorAll('select, textarea').forEach((input) => {
      input.addEventListener('change', () => updateContributionConditions(stepId));
      input.addEventListener('input', () => updateContributionConditions(stepId));
    });
    updateContributionConditions(stepId);
  }

  async function loadContribution(stepId) {
    if (!stepId.endsWith('_contribution') ||
        !state.bridge?.guided_campaign_contribution || !state.activeSessionId) return null;
    const sessionId = state.activeSessionId;
    const revision = state.constructionRevision;
    const generation = state.constructionPanelGeneration;
    const contribution = await state.bridge.guided_campaign_contribution(
      sessionId,
      stepId,
    );
    if (state.activeSessionId !== sessionId || state.constructionRevision !== revision ||
        state.constructionPanelGeneration !== generation) return null;
    if (contribution) renderContribution(stepId, contribution);
    return contribution;
  }

  function constructionStepAnswer() {
    const stepId = currentConstructionStepId();
    if (stepId === 'ruleset') {
      const selected = $('construction-package').selectedOptions[0];
      return {
        visible: selected?.textContent || 'No Ruleset — narrative play',
        value: { candidate_id: $('construction-package').value || null },
      };
    }
    if (stepId === 'setting') {
      const selected = $('construction-setting').selectedOptions[0];
      return {
        visible: selected?.textContent || 'No Campaign Setting',
        value: {
          setting_id: $('construction-setting').value || null,
          candidate_id: selectedCandidateSettingBindings().find(
            (item) => item.id === $('construction-setting').value
          )?.candidate_id || null,
        },
      };
    }
    if (stepId === 'plan') {
      const selected = $('construction-template').selectedOptions[0];
      return {
        visible: selected?.textContent || $('construction-template').value,
        value: { plan_id: $('construction-template').value,
          ...(selectedPartySourceIdentity() ? { target_identity: selectedPartySourceIdentity() } : {}),
        },
      };
    }
    if (stepId.endsWith('_contribution')) return contributionAnswer(stepId);
    if (stepId === 'name') {
      const name = $('construction-name').value.trim();
      return { visible: name, value: { display_name: name } };
    }
    if (stepId === 'participants') {
      const companionIds = [...$('rift-companions').querySelectorAll(
        'input[type="checkbox"]:checked'
      )].map((input) => input.value);
      const playerName = selectedRiftPlayerName();
      return {
        visible: `${playerName}; ${companionIds.length} companion${companionIds.length === 1 ? '' : 's'}`,
        value: {
          archetype_id: $('rift-archetype').value,
          companion_ids: companionIds,
          ...(selectedPartySourceIdentity() ? { target_identity: selectedPartySourceIdentity() } : {}),
        },
      };
    }
    if (stepId === 'boundaries_preferences') {
      const boundaries = $('construction-boundaries').value.trim();
      const preferences = $('construction-preferences').value.trim();
      return {
        visible: `Boundaries: ${boundaries || 'None'}; Preferences: ${preferences || 'None'}`,
        value: { boundaries, preferences },
      };
    }
    const noRequests = $('construction-no-requests').checked;
    const requests = noRequests ? '' : $('construction-premise').value.trim();
    return {
      visible: noRequests ? 'No additional requests' : requests,
      value: { no_additional_requests: noRequests, requests },
    };
  }

  async function advanceConstructionInterview() {
    if ($('construction-next-btn').disabled) return;
    if (!constructionStepIsValid()) return;
    const currentStep = currentConstructionStepId();
    const captured = constructionStepAnswer();
    if (currentStep === 'specific_requests' && $('construction-package').value &&
        !captured.value.no_additional_requests) {
      await startCreationSubflow('campaign_customization', captured.visible);
      return;
    }
    const previous = state.constructionAnswers[currentStep];
    if (previous && JSON.stringify(previous.value) === JSON.stringify(captured.value)) {
      const activeSteps = activeConstructionStepIds();
      const nextKnown = activeSteps[activeSteps.indexOf(currentStep) + 1];
      await navigateConstructionInterview(nextKnown || 'review');
      return;
    }
    const currentStepIndex = constructionStepIds.indexOf(currentStep);
    const affectedSteps = constructionStepIds.slice(currentStepIndex + 1).filter(
      (stepId) => state.constructionAnswers[stepId]
    );
    if (previous && affectedSteps.length && !window.confirm(
      `Changing this answer will detach saved downstream work for: ${affectedSteps.join(', ')}. ` +
      'Builder chats and reusable artifacts will be kept. Continue?'
    )) return;
    if (!state.bridge || !state.bridge.advance_guided_campaign_draft ||
        !state.constructionDraftId || !state.activeSessionId) return;
    const campaignId = state.constructionDraftId;
    const sessionId = state.activeSessionId;
    const generation = state.constructionPanelGeneration;
    const stillSelected = () => state.activeCampaignId === campaignId &&
      state.activeSessionId === sessionId && state.constructionPanelGeneration === generation;
    $('construction-next-btn').disabled = true;
    setInlineStatus('Saving answer…', 'construction');
    $('construction-back-btn').disabled = true;
    try {
      const result = await state.bridge.advance_guided_campaign_draft({
        operation_id: `guided-answer-${crypto.randomUUID()}`,
        campaign_id: campaignId,
        builder_session_id: sessionId,
        expected_construction_revision: state.constructionRevision,
        current_step: currentStep,
        visible_answer: captured.visible,
        answer: captured.value,
      });
      if (!stillSelected()) return;
      state.constructionRevision = result.construction_revision;
      constructionStepIds.slice(currentStepIndex + 1).forEach((stepId) => {
        delete state.constructionAnswers[stepId];
        if (stepId.endsWith('_contribution')) delete state.constructionContributions[stepId];
      });
      state.constructionAnswers[currentStep] = captured;
      if (currentStep === 'participants') {
        clearPendingPartySelection(localStorage, campaignId, sessionId);
      }
      await loadContribution(result.current_step);
      if (!stillSelected()) return;
      if (currentStep.endsWith('_contribution') &&
          contributionNeedsAdaptive(currentStep, captured)) {
        await startCreationSubflow(
          'adaptive_interpretation',
          captured.visible,
          result.answer_message_id,
        );
        return;
      }
      showConstructionStepId(result.current_step);
      setInlineStatus('Campaign Draft saved', 'construction');
      if (currentStep === 'name') {
        await refreshCampaignIndex();
        if (!stillSelected()) return;
        const renamed = selectedIndexEntry();
        if (renamed) {
          $('session-title').textContent = renamed.display_name || 'Campaign';
          $('ctx-campaign').textContent = renamed.display_name || '—';
        }
      }
    } catch (e) {
      if (!stillSelected()) return;
      setInlineStatus(friendlyCommandError('Answer could not be saved', e), 'construction');
    } finally {
      if (stillSelected()) {
        $('construction-back-btn').disabled = false;
        $('construction-next-btn').disabled = false;
        updateCampaignPartyActionAvailability();
      }
    }
  }

  async function startCreationSubflow(purpose, visibleAnswer, triggerMessageId = null, profile = null) {
    if (!state.bridge || !state.bridge.start_guided_creation_subflow ||
        !state.constructionDraftId || !state.activeSessionId) return;
    const parentSessionId = state.activeSessionId;
    if (state.builderLaunchPending.has(parentSessionId)) return;
    state.builderLaunchPending.add(parentSessionId);
    updateCharacterProfileControls();
    setInlineStatus('Opening Builder chat…', 'construction');
    try {
      const result = await state.bridge.start_guided_creation_subflow({
        operation_id: `guided-subflow-${crypto.randomUUID()}`,
        campaign_id: state.constructionDraftId,
        parent_session_id: parentSessionId,
        expected_construction_revision: state.constructionRevision,
        purpose,
        visible_answer: visibleAnswer,
        trigger_message_id: triggerMessageId,
        ...(profile ? { character_profile_id: profile.id,
          character_profile_sha256: profile.content_sha256 } : {}),
      });
      if (state.activeSessionId === parentSessionId) state.constructionRevision = result.construction_revision;
      if (result.status === 'independent') {
        await refreshBuilderChats();
        if (state.activeSessionId === parentSessionId) {
          state.activeCreationSubflow = null;
          await openBuilderChat(result.child_session_id);
          if (state.activeSessionId === result.child_session_id) {
            setInlineStatus('Builder ready · Campaign Interview saved', 'construction');
          }
        }
        void submitInput('', { builderStart: true, builderSessionId: result.child_session_id });
        return;
      }
      state.activeCreationSubflow = result;
      await refreshCampaignIndex();
      await openCampaign(result.campaign_id, result.child_session_id);
      setInlineStatus('Linked Builder chat · Campaign Interview suspended', 'construction');
    } catch (e) {
      if (state.activeSessionId !== parentSessionId) return;
      setInlineStatus(friendlyCommandError(
        'Creation Subflow could not open', e
      ), 'construction');
    } finally {
      state.builderLaunchPending.delete(parentSessionId);
      updateCharacterProfileControls();
    }
  }

  async function returnCreationSubflow(useResult) {
    const subflow = state.activeCreationSubflow;
    if (!subflow || !state.bridge || !state.bridge.return_guided_creation_subflow) return;
    setInlineStatus(useResult
      ? 'Validating and using Builder result…'
      : 'Returning to Campaign Interview…', 'construction');
    try {
      const result = await state.bridge.return_guided_creation_subflow({
        operation_id: `guided-subflow-return-${crypto.randomUUID()}`,
        campaign_id: subflow.campaign_id,
        parent_session_id: subflow.parent_session_id,
        child_session_id: subflow.child_session_id,
        expected_construction_revision: state.constructionRevision,
        use_result: useResult,
      });
      state.constructionRevision = result.construction_revision;
      if (useResult && ['plan', 'campaign_customization'].includes(subflow.purpose)) {
        state.hasDerivedCampaignPlan = true;
      }
      state.activeCreationSubflow = null;
      await refreshCampaignIndex();
      await openCampaign(result.campaign_id, result.parent_session_id);
      setInlineStatus(useResult
        ? 'Builder result selected · Campaign Draft saved'
        : 'Campaign Interview resumed without using Builder work', 'construction');
    } catch (e) {
      setInlineStatus(friendlyCommandError(
        useResult ? 'Builder result is not ready to use' : 'Could not return', e
      ), 'construction');
    }
  }

  async function saveReusableCampaignPlan() {
    if (!state.hasDerivedCampaignPlan || !state.bridge?.save_reusable_campaign_plan ||
        !state.constructionDraftId || !state.activeSessionId) return;
    const suggested = `${$('construction-name').value.trim() || 'Campaign'} Plan`;
    const title = window.prompt('Name the reusable Campaign Plan', suggested);
    if (title == null || !title.trim()) return;
    if (!window.confirm(
      'Save a reusable sibling? Sylvia will remove the current party and known private ' +
      'Interview overlays while preserving reusable GM-only story secrets. The current ' +
      'campaign will not be changed.'
    )) return;
    $('save-reusable-plan-btn').disabled = true;
    setInlineStatus('Reviewing and saving reusable Campaign Plan…', 'construction');
    try {
      const saved = await state.bridge.save_reusable_campaign_plan({
        operation_id: `save-reusable-plan-${crypto.randomUUID()}`,
        campaign_id: state.constructionDraftId,
        builder_session_id: state.activeSessionId,
        expected_construction_revision: state.constructionRevision,
        title: title.trim(),
      });
      const catalog = await state.bridge.campaign_creation_catalog();
      state.campaignCreationCatalog = catalog || [];
      state.reusableCampaignPlans = (catalog || []).filter(
        (item) => item.kind === 'campaign_plan' && item.reusable === true
      );
      setInlineStatus(`Saved ${saved.title} as a reusable Campaign Plan · current Draft unchanged`, 'construction');
    } catch (e) {
      setInlineStatus(friendlyCommandError(
        'Reusable Campaign Plan could not be saved', e
      ), 'construction');
    } finally {
      $('save-reusable-plan-btn').disabled = false;
    }
  }

  async function retreatConstructionInterview() {
    if (state.constructionStep > 0) {
      await navigateConstructionInterview(activeConstructionStepIds()[state.constructionStep - 1]);
    }
  }

  // Cursor movement preserves answers; saving a changed answer owns downstream invalidation.
  async function navigateConstructionInterview(targetStep) {
    if (!state.bridge?.navigate_guided_campaign_draft || !state.constructionDraftId ||
        !state.activeSessionId ||
        $('construction-back-btn').disabled) return;
    const campaignId = state.constructionDraftId;
    const sessionId = state.activeSessionId;
    const generation = state.constructionPanelGeneration;
    const stillSelected = () => state.activeSessionId === sessionId &&
      state.constructionDraftId === campaignId && state.constructionPanelGeneration === generation;
    $('construction-back-btn').disabled = true;
    $('construction-next-btn').disabled = true;
    setInlineStatus('Saving Interview position…', 'construction');
    try {
      const result = await state.bridge.navigate_guided_campaign_draft({
        operation_id: `guided-navigation-${crypto.randomUUID()}`,
        campaign_id: campaignId,
        builder_session_id: sessionId,
        expected_construction_revision: state.constructionRevision,
        target_step: targetStep,
      });
      if (!stillSelected()) return;
      state.constructionRevision = result.construction_revision;
      showConstructionStepId(result.current_step);
      setInlineStatus('Campaign Interview saved', 'construction');
    } catch (e) {
      if (stillSelected()) {
        setInlineStatus(friendlyCommandError('Could not change Interview step', e), 'construction');
      }
    } finally {
      if (stillSelected()) {
        $('construction-back-btn').disabled = false;
        $('construction-next-btn').disabled = false;
        updateCampaignPartyActionAvailability();
      }
    }
  }

  function renderRiftTemplate(template) {
    state.riftTemplate = template;
  }

  function reusableCharacterTargetLabel(item) {
    if (item?.reusable !== true) return '';
    const binding = (label, value, formatName = (id) => id) => value && typeof value.id === 'string' &&
      typeof value.version === 'string' && value.id && value.version
      ? `${label}: ${formatName(value.id)} ${value.version}` : '';
    return [binding('Saved Ruleset', item.ruleset, rulesetPackageName), binding('Setting', item.campaign_setting)]
      .filter(Boolean).join(' · ');
  }

  function renderCampaignPartyOptions(template, selection = null, fallbackDescription = '') {
    state.campaignPartyOptions = template;
    $('complete-required-characters-btn').hidden = ![
      ...(template?.player_archetypes || []), ...(template?.companions || []),
    ].some((item) => item.campaign_ready === false && item.source_ref);
    $('rift-description').textContent = template?.description || fallbackDescription;
    $('rift-archetype').innerHTML = '<option value="">Choose a Player Character</option>' +
      (template?.player_archetypes || []).map((item) => {
      const profileRequired = item.campaign_ready === false;
      const status = profileRequired
        ? ` — ${escapeHtml(item.unavailable_reason || 'Complete Character File first')}` : '';
      return `<option value="${escapeHtml(item.id)}"${profileRequired ? ' disabled' : ''}>` +
        `${escapeHtml(item.label || item.name)} - ${escapeHtml(item.summary)}${status}` +
        `${reusableCharacterTargetLabel(item) ? ` · ${escapeHtml(reusableCharacterTargetLabel(item))}` : ''}</option>`;
    }).join('');
    $('rift-companions').innerHTML = (template?.companions || []).map((item, index) => {
      const profileRequired = item.campaign_ready === false;
      const status = profileRequired
        ? ` · ${escapeHtml(item.unavailable_reason || 'Complete Character File first')}` : '';
      const deleteButton = item.reusable && item.content_sha256
        ? `<button type="button" class="btn btn-secondary btn-sm" ` +
          `data-delete-reusable-companion="${escapeHtml(item.id)}">Delete Saved Companion</button>`
        : '';
      return `<label class="companion-choice" for="rift-companion-${index}">` +
        `<input id="rift-companion-${index}" type="checkbox" value="${escapeHtml(item.id)}" ` +
        `data-profile-required="${profileRequired ? 'true' : 'false'}"` +
        ` data-reusable="${item.reusable ? 'true' : 'false'}"` +
        `${profileRequired ? ' disabled' : ''}>` +
        `<strong>${escapeHtml(item.name)}</strong>` +
        `<small>${escapeHtml(item.summary)}${status}` +
        `${item.source_profile ? ` · Adapted from ${escapeHtml(item.source_profile.name)}` : ''}` +
        `${reusableCharacterTargetLabel(item) ? `<br><span class="saved-character-target">${escapeHtml(reusableCharacterTargetLabel(item))}</span>` : ''}` +
        `</small>${deleteButton}</label>`;
    }).join('');
    $('rift-companions').querySelectorAll('input[type="checkbox"]').forEach((input) => {
      input.addEventListener('change', () => {
        enforceCompanionMaximum();
        persistSelectedPendingParty();
      });
    });
    $('rift-archetype').value = '';
    if (selection?.archetype_id && (template?.player_archetypes || []).some(
      (item) => item.id === selection.archetype_id && item.campaign_ready !== false
    )) {
      $('rift-archetype').value = selection.archetype_id;
    }
    const companionIds = new Set(
      Array.isArray(selection?.companion_ids) ? selection.companion_ids : []
    );
    $('rift-companions').querySelectorAll('input[type="checkbox"]').forEach((input) => {
      input.checked = companionIds.has(input.value) && input.dataset.profileRequired !== 'true';
    });
    applyStartingPartyPolicy();
    updateReusableLibraryDeleteControls();
  }

  function selectedRiftPlayerName() {
    const archetypeId = $('rift-archetype').value;
    const selected = (state.campaignPartyOptions?.player_archetypes || []).find(
      (item) => item.id === archetypeId
    );
    return selected?.name || selected?.label || '';
  }

  async function showConstructionPanel(mode = null) {
    const generation = ++state.constructionPanelGeneration;
    const campaignId = state.activeCampaignId;
    const sessionId = state.activeSessionId;
    const stillSelected = () => generation === state.constructionPanelGeneration &&
      campaignId === state.activeCampaignId && sessionId === state.activeSessionId;
    const panel = $('construction-panel');
    if (!state.constructionPanelHome) {
      state.constructionPanelHome = panel.parentElement;
      state.constructionPanelNext = panel.nextSibling;
    }
    const chronology = $('chronology');
    chronology.innerHTML = '';
    chronology.appendChild(panel);
    panel.classList.add('guided-construction');
    // Previous choices must stay unavailable until this Interview's library and answers are ready.
    panel.hidden = true;
    $('player-input').disabled = true;
    $('send-btn').disabled = true;
    $('builder-input-options').hidden = true;
    updateConstructionMode();
    showConstructionStep(0);
    $('construction-library-warnings').hidden = true;
    $('construction-library-warning-items').innerHTML = '';
    if (!state.bridge) return;
    if (state.bridge.campaign_creation_catalog) {
      try {
        const catalog = await state.bridge.campaign_creation_catalog() || [];
        if (!stillSelected()) return;
        state.campaignCreationCatalog = catalog;
        renderCharacterProfiles();
        state.reusableCampaignPlans = catalog.filter(
          (item) => item.kind === 'campaign_plan' && item.reusable === true
        );
        const unavailable = catalog.filter((item) => item.kind === 'unavailable_content');
        $('construction-library-warnings').hidden = unavailable.length === 0;
        $('construction-library-warning-items').innerHTML = unavailable.map((item) => {
          const details = (item.validation_errors || []).map((error) =>
            [error.file, error.path, error.message].filter(Boolean).join(' · ')
          ).join('; ');
          return `<li>${escapeHtml(item.title)} (${escapeHtml(String(item.id).slice(-12))}): ` +
            `${escapeHtml(item.summary)}${details ? ` ${escapeHtml(details)}` : ''}</li>`;
        }).join('');
      } catch (e) {
        if (!stillSelected()) return;
        setInlineStatus('Campaign Plan library unavailable', 'construction');
        $('construction-library-warnings').hidden = false;
        $('construction-library-warning-items').textContent = friendlyCommandError(
          'The content library could not be loaded. Reopen this Interview to try again', e
        );
      }
    }
    if (state.bridge.rift_template) {
      try {
        const template = await state.bridge.rift_template();
        if (!stillSelected()) return;
        renderRiftTemplate(template);
      } catch (e) {
        if (!stillSelected()) return;
        setInlineStatus('The Rift setup is unavailable', 'construction');
      }
    }
    try {
      const select = $('construction-package');
      const rulesets = state.campaignCreationCatalog.filter(
        (item) => item.kind === 'ruleset' && item.selection_id
      );
      const requestedPlan = state.campaignCreationCatalog.find(
        (item) => item.kind === 'campaign_plan' && item.id === mode
      );
      if (requestedPlan) updateConstructionNameSuggestion(requestedPlan);
      const requestedRuleset = rulesets.find((item) => item.compatible_settings?.some(
        (setting) => setting.ruleset?.id === requestedPlan?.ruleset?.id &&
          setting.id === requestedPlan?.campaign_setting?.id
      ) || exactBindingMatches({
        package_id: item.id,
        version: item.version,
        sha256: item.content_sha256,
      }, requestedPlan?.ruleset));
      const requestedSetting = requestedRuleset?.compatible_settings?.find((item) =>
        (item.ruleset && item.id === requestedPlan?.campaign_setting?.id) || exactBindingMatches({
          package_id: item.id,
          version: item.version,
          sha256: item.content_sha256,
        }, requestedPlan?.campaign_setting)
      );
      select.innerHTML = '<option value="">No Ruleset — narrative play</option>' +
        rulesets.map((item) =>
          `<option value="${escapeHtml(item.selection_id)}">${escapeHtml(rulesetPackageName(item.id, item.title))}</option>`
        ).join('');
      if (requestedRuleset) select.value = requestedRuleset.selection_id;
      updateConstructionCompatibility();
      if (requestedSetting) {
        $('construction-setting').value = requestedSetting.id;
        updateConstructionPlans();
      }
      if (requestedPlan) {
        $('construction-template').value = requestedPlan.id;
        updateConstructionMode();
      }
    } catch (e) {
      setInlineStatus('Package library unavailable', 'construction');
    }
    if (stillSelected() && !state.constructionRestoreInProgress) {
      panel.hidden = false;
      if ([$('session-status-text').textContent, $('construction-status').textContent]
          .includes('Loading campaign Interview…')) {
        setInlineStatus('Campaign Interview ready', 'construction');
      }
    }
  }

  function restoreConstructionAnswer(stepId, content) {
    const value = content && content.interview_value || {};
    state.constructionAnswers[stepId] = {
      visible: String(content && content.text || ''),
      value,
    };
    if (stepId === 'ruleset') {
      const choice = resolveCampaignCreationOptions(
        state.campaignCreationCatalog, value.candidate_id
      ).rulesetChoice;
      $('construction-package').value = choice?.selection_id || value.candidate_id || '';
      updateConstructionCompatibility();
    }
    if (stepId === 'setting') {
      $('construction-setting').value = value.setting_id || '';
      updateConstructionPlans();
    }
    if (stepId === 'plan' && value.plan_id) {
      $('construction-template').value = value.plan_id;
      updateConstructionMode();
    }
    if (stepId.endsWith('_contribution')) {
      Object.entries(value.answers || {}).forEach(([questionId, answer]) => {
        const input = document.querySelector(
          `[data-contribution-step-id="${stepId}"]` +
          `[data-contribution-question-id="${questionId}"]`
        );
        if (input) input.value = String(answer);
      });
      updateContributionConditions(stepId);
    }
    if (stepId === 'name') {
      state.constructionNameSuggested = false;
      $('construction-name').value = value.display_name || '';
    }
    if (stepId === 'participants') {
      if (value.archetype_id) $('rift-archetype').value = value.archetype_id;
      const selected = new Set(Array.isArray(value.companion_ids) ? value.companion_ids : []);
      $('rift-companions').querySelectorAll('input[type="checkbox"]').forEach((input) => {
        input.checked = selected.has(input.value);
      });
      applyStartingPartyPolicy();
    }
    if (stepId === 'boundaries_preferences') {
      $('construction-boundaries').value = value.boundaries || '';
      $('construction-preferences').value = value.preferences || '';
    }
    if (stepId === 'specific_requests') {
      $('construction-no-requests').checked = Boolean(value.no_additional_requests);
      $('construction-premise').value = value.requests || '';
      $('construction-premise').disabled = Boolean(value.no_additional_requests);
    }
  }

  function latestCampaignInterviewState(messages) {
    const interviewStates = messages.filter(
      (message) => message.kind === 'evidence' &&
        message.content && message.content.kind === 'campaign_interview_state'
    );
    const latest = interviewStates.reduce((selected, candidate) => {
      if (!selected) return candidate;
      const selectedRevision = Number(selected.content.construction_revision || 0);
      const candidateRevision = Number(candidate.content.construction_revision || 0);
      if (candidateRevision !== selectedRevision) {
        return candidateRevision > selectedRevision ? candidate : selected;
      }
      return Number(candidate.sequence || 0) > Number(selected.sequence || 0)
        ? candidate
        : selected;
    }, null);
    return latest ? latest.content : null;
  }

  async function resumeGuidedCampaignDraft(campaignId, sessionId) {
    if (!state.bridge || !state.bridge.session_context || !sessionId) return;
    setInlineStatus('Loading campaign Interview…', 'construction');
    const stillSelected = () => state.view === 'session' && state.activeCampaignId === campaignId &&
      state.activeSessionId === sessionId;
    const context = await state.bridge.session_context(sessionId);
    if (!stillSelected()) return;
    const messages = context && Array.isArray(context.messages) ? context.messages : [];
    const interviewState = latestCampaignInterviewState(messages);
    if (!interviewState) return;
    state.constructionNameSuggested = true;
    state.constructionDraftId = campaignId;
    state.constructionRevision = Number(interviewState.construction_revision || 0);
    if (interviewState.active_subflow &&
        interviewState.active_subflow.child_session_id !== sessionId) {
      await refreshCampaignIndex();
      if (!stillSelected()) return;
      await openCampaign(campaignId, interviewState.active_subflow.child_session_id);
      return;
    }
    const byId = new Map(messages.map((message) => [message.message_id, message]));
    state.constructionAnswers = {};
    state.campaignPartyRepair = null;
    const restoration = {};
    state.constructionRestoreInProgress = restoration;
    try {
      await showConstructionPanel();
      if (!stillSelected()) return;
      // The shared form must start empty before restoring this Interview's own saved answers.
      $('construction-name').value = '';
      $('construction-boundaries').value = '';
      $('construction-preferences').value = '';
      $('construction-no-requests').checked = true;
      $('construction-premise').value = '';
      $('construction-premise').disabled = true;
      state.hasDerivedCampaignPlan = Array.isArray(interviewState.subflows) &&
        interviewState.subflows.some((subflow) => subflow && subflow.used &&
          ['plan', 'campaign_customization'].includes(subflow.purpose));
      for (const stepId of constructionStepIds.filter((id) => id.endsWith('_contribution'))) {
        if ((interviewState.answers || {})[stepId] || interviewState.current_step === stepId) {
          await loadContribution(stepId);
          if (!stillSelected()) return;
        }
      }
      // Upstream answers rebuild downstream choices; saved JSON key order is not step order.
      constructionStepIds.forEach((stepId) => {
        const answer = byId.get((interviewState.answers || {})[stepId]);
        if (answer && answer.kind === 'user') restoreConstructionAnswer(stepId, answer.content || {});
      });
      // A refreshed library is not an answer to an unfinished Setting question.
      if (interviewState.current_step === 'setting' && !state.constructionAnswers.setting &&
          selectedCandidateSettingBindings().length) {
        const setting = $('construction-setting');
        setting.innerHTML = '<option value="">Choose a Campaign Setting</option>' + setting.innerHTML;
        setting.value = '';
      }
    } finally {
      if (state.constructionRestoreInProgress === restoration) {
        state.constructionRestoreInProgress = false;
      }
    }
    if (!stillSelected()) return;
    const participantAnswer = byId.get((interviewState.answers || {}).participants);
    const participantValue = participantAnswer?.kind === 'user'
      ? participantAnswer.content?.interview_value || null
      : null;
    const pendingParty = participantValue ? null : loadPendingPartySelection(
      localStorage, campaignId, sessionId, selectedPartySourceIdentity(),
    );
    const pendingRecord = participantValue ? null :
      pendingPartySelections(localStorage)[pendingPartyStorageId(campaignId, sessionId)];
    const savedTarget = participantValue?.target_identity || pendingRecord?.source ||
      state.constructionAnswers.plan?.value?.target_identity || null;
    await refreshCampaignPartyOptions({
      savedTarget,
      preserveSelection: participantValue ? {
        archetype_id: participantValue.archetype_id || '',
        companion_ids: Array.isArray(participantValue.companion_ids)
          ? participantValue.companion_ids : [],
      } : pendingParty,
    });
    if (!stillSelected()) return;
    showConstructionStepId(interviewState.current_step);
    $('construction-panel').hidden = false;
    setInlineStatus('Campaign Draft resumed', 'construction');
  }

  function hideConstructionPanel(returnHomeWhenIdle = true) {
    const panel = $('construction-panel');
    panel.hidden = true;
    panel.classList.remove('guided-construction');
    if (state.constructionPanelHome) {
      state.constructionPanelHome.insertBefore(panel, state.constructionPanelNext);
    }
    state.constructionStep = 0;
    if (returnHomeWhenIdle && !state.activeCampaignId && !state.activeSessionId) showHome();
  }

  async function startBuilderChat(options = {}) {
    if (!state.bridge || !state.bridge.create_builder_chat) return;
    const selected = defaultModelChoice();
    if (!selected) {
      setInlineStatus('Configure a provider before starting Builder');
      openSettings('models');
      return;
    }
    const nonce = Date.now();
    setInlineStatus('Starting Builder...');
    try {
      const chat = await state.bridge.create_builder_chat({
        operation_id: `builder-chat-${nonce}`,
        display_name: options.displayName || 'New chat',
        provider_id: null,
        model_id: null,
      });
      await refreshBuilderChats();
      await openBuilderChat(chat.session.session_id);
      if (options.initialPrompt) {
        $('player-input').value = options.initialPrompt;
        saveSelectedSessionDraft();
      }
      setInlineStatus('');
      $('player-input').focus();
    } catch (e) {
      setInlineStatus('Builder chat could not start');
    }
  }

  async function startCampaignBuilder() {
    if (!state.bridge || !state.bridge.create_guided_campaign_draft) return;
    hideConstructionPanel(false);
    const selected = defaultModelChoice();
    const operationId = `guided-draft-${crypto.randomUUID()}`;
    setInlineStatus('Creating campaign Draft…');
    try {
      const draft = await state.bridge.create_guided_campaign_draft({
        operation_id: operationId,
        display_name: 'New campaign interview',
        provider_id: selected ? selected.provider_id : 'unconfigured.local',
        model_id: selected ? selected.model_id : 'unconfigured.model',
      });
      state.constructionDraftId = draft.campaign_id;
      state.constructionRevision = draft.construction_revision;
      state.constructionAnswers = {};
      state.constructionContributions = {};
      state.constructionNameSuggested = true;
      state.hasDerivedCampaignPlan = false;
      await refreshCampaignIndex();
      await openCampaign(draft.campaign_id, draft.builder_session_id);
      $('session-title').textContent = 'New campaign interview';
      setInlineStatus('Choose a Ruleset to start your campaign.', 'construction');
      return draft;
    } catch (e) {
      const message = friendlyCommandError(
        'Campaign Draft could not be created',
        e,
      );
      setInlineStatus(message);
    }
  }

  async function beginConstruction() {
    if (!state.bridge) return;
    const selected = selectedModelChoice();
    if (!selected) {
      setInlineStatus('No configured models', 'construction');
      return;
    }
    const displayName = $('construction-name').value.trim();
    if (!displayName) {
      setInlineStatus('Campaign name required', 'construction');
      return;
    }
    const hasRuleset = Boolean($('construction-package').value);
    const partyProblem = hasRuleset ? campaignPartySelectionProblem() : null;
    if (partyProblem) {
      setInlineStatus(partyProblem, 'construction');
      return;
    }
    if (state.campaignConfirmationPending) return;
    state.campaignConfirmationPending = true;
    const confirmationControls = [
      $('begin-construction-btn'),
      $('cancel-construction-btn'),
      $('construction-next-btn'),
      $('construction-back-btn'),
    ].filter(Boolean);
    const confirmationControlStates = confirmationControls.map((control) => control.disabled);
    confirmationControls.forEach((control) => { control.disabled = true; });
    const nonce = Date.now();
    const boundaries = $('construction-boundaries').value.trim();
    const preferences = $('construction-preferences').value.trim();
    const specificRequests = $('construction-no-requests').checked
      ? ''
      : $('construction-premise').value.trim();
    const customizationParts = [];
    if (boundaries) customizationParts.push(`Hard boundaries: ${boundaries}`);
    if (preferences) customizationParts.push(`Preferences: ${preferences}`);
    if (specificRequests) customizationParts.push(`Specific requests: ${specificRequests}`);
    const premise = customizationParts.join('\n');
    const lane = premise ? 'customize' : 'quick_start';
    const playerCharacterId = hasRuleset ? $('rift-archetype').value || null : null;
    const companionIds = hasRuleset ? [...$('rift-companions').querySelectorAll(
      'input[type="checkbox"]:checked'
    )].map((input) => input.value) : [];
    const constructionIntent = {
      boundaries,
      preferences,
      specific_requests: specificRequests,
      contributions: hasRuleset ? Object.fromEntries(
        Object.entries(state.constructionAnswers)
          .filter(([stepId]) => stepId.endsWith('_contribution'))
          .map(([stepId, captured]) => [stepId, captured.value])
      ) : {},
    };
    setInlineStatus('Confirming campaign…', 'construction');
    try {
      let begun;
      if (hasRuleset && usesBundledCampaignSetup()) {
        if (!state.bridge.start_rift_campaign) return;
        if (!playerCharacterId) {
          setInlineStatus('Player Character setup is required', 'construction');
          return;
        }
        begun = await state.bridge.start_rift_campaign({
          operation_id: `start-rift-${nonce}`,
          campaign_id: state.constructionDraftId,
          builder_session_id: state.activeSessionId,
          ruleset_selection: $('construction-package').value,
          setting_id: $('construction-setting').value,
          plan_id: $('construction-template').value,
          display_name: displayName,
          lane,
          archetype_id: playerCharacterId,
          companion_ids: companionIds,
          customization_notes: lane === 'customize' ? premise : '',
          provider_id: selected.provider_id,
          model_id: selected.model_id,
          construction_intent: constructionIntent,
        });
      } else {
        if (!state.bridge.start_guided_campaign) return;
        begun = await state.bridge.start_guided_campaign({
          operation_id: `start-guided-${nonce}`,
          campaign_id: state.constructionDraftId,
          builder_session_id: state.activeSessionId,
          display_name: displayName,
          candidate_id: $('construction-package').value || null,
          premise,
          setting_id: hasRuleset ? $('construction-setting').value || null : null,
          plan_id: hasRuleset ? selectedCampaignPlan()?.id || null : null,
          player_character_id: playerCharacterId,
          companion_ids: companionIds,
          construction_intent: constructionIntent,
        });
      }
      hideConstructionPanel();
      await refreshCampaignIndex();
      await openCampaign(begun.campaign_id, begun.builder_session_id || null);
      setInlineStatus('Guided campaign construction started · Builder active', 'construction');
      await finalizeSelectedConstruction();
    } catch (e) {
      setInlineStatus(friendlyCommandError(
        'Construction could not start',
        e,
      ), 'construction');
    } finally {
      state.campaignConfirmationPending = false;
      confirmationControls.forEach((control, index) => {
        control.disabled = confirmationControlStates[index];
      });
      updateCampaignPartyActionAvailability();
    }
  }

  async function finalizeSelectedConstruction(modelGuidanceOverride = false) {
    if (!state.activeCampaignId || !state.bridge ||
        !state.bridge.finalize_campaign_construction) return;
    const openingCampaignId = state.activeCampaignId;
    const interviewSessionId = state.activeSessionId;
    let openingActivity = null;
    const stillInInterview = () => state.activeCampaignId === openingCampaignId &&
      state.activeSessionId === interviewSessionId;
    const selected = selectedModelChoice();
    if (!selected) {
      setInlineStatus('No configured DM model', 'construction');
      return;
    }
    if (state.campaignHandoffPending) return;
    state.campaignHandoffPending = true;
    const handoffControls = [
      $('finalize-construction-btn'),
      $('continue-model-guidance-btn'),
      $('dismiss-model-guidance-btn'),
    ].filter(Boolean);
    const handoffControlStates = handoffControls.map((control) => control.disabled);
    handoffControls.forEach((control) => { control.disabled = true; });
    try {
    if (modelGuidanceOverride) {
      const pending = state.pendingModelGuidance;
      if (!pending || pending.campaign_id !== state.activeCampaignId ||
          pending.provider_id !== selected.provider_id || pending.model_id !== selected.model_id) {
        clearModelGuidanceWarning();
        setInlineStatus('Model capability warning must be reviewed again', 'construction');
        return;
      }
      clearModelGuidanceWarning();
    } else {
      if (!state.bridge.model_guidance) return;
      try {
        const guidance = await state.bridge.model_guidance(
          openingCampaignId,
          selected.model_id,
          false
        );
        if (!stillInInterview()) return;
        if (guidance.warning_required) {
          showModelGuidanceWarning(guidance, selected);
          setInlineStatus('Review the model capability warning', 'construction');
          return;
        }
      } catch (e) {
        setInlineStatus('Model capability guidance is unavailable', 'construction');
        return;
      }
    }
    setInlineStatus('Admitting plan and handing off…', 'construction');
    let handoffCommitted = false;
    try {
      const result = await state.bridge.finalize_campaign_construction(
        openingCampaignId,
        selected.provider_id,
        selected.model_id,
        modelGuidanceOverride
      );
      handoffCommitted = true;
      // The Ready panel is rendered inside chronology. Restore it before polling can
      // clear chronology so later campaign refreshes never address a detached node.
      if (stillInInterview()) {
        hideConstructionPanel(false);
        saveSelectedSessionDraft();
        state.activeSessionId = result.handoff.dm_session_id;
        state.activeSessionRole = 'dm';
        restoreSelectedSessionDraft();
      }
      if (state.bridge.start_dm_session) {
        const openingSessionId = result.handoff.dm_session_id;
        const openingAttemptId = result.dm_attempt_id;
        if (!openingAttemptId) throw new Error('Campaign opening attempt is unavailable');
        openingActivity = beginSessionActivity(ensureSessionActivity({
          sessionId: openingSessionId, sessionRole: 'dm', campaignId: openingCampaignId,
        }), openingAttemptId);
        selectedStatus(openingActivity, 'Game Master is opening the campaign…');
        refreshActivityControls(openingActivity);
        renderActivityIndexes();
        startProjectionPolling(openingActivity);
        try {
          const opening = state.bridge.start_dm_session(
            openingCampaignId,
            openingSessionId,
            result.dm_turn_id,
          );
          startStreamHeartbeat(openingActivity, true);
          await opening;
          finishSessionActivity(openingActivity, 'completed', 'Completed');
        } catch (e) {
          finishSessionActivity(openingActivity, 'interrupted', 'Generation was interrupted');
          throw e;
        } finally {
          stopStreamHeartbeat(openingActivity);
          stopProjectionPolling(openingActivity);
          refreshActivityControls(openingActivity);
        }
      }
      await refreshCampaignIndex();
      if (selectedActivityMatches(openingActivity)) await refreshSelectedCampaign();
      selectedStatus(openingActivity,
        `Plan ${result.plan.plan_revision} admitted · DM started · Builder remains available · revision ` +
        result.handoff.resulting_campaign_revision);
    } catch (e) {
      // Before handoff, the Ready panel is still the player's durable recovery surface. Re-rendering
      // the session here resets the visible interview cursor even though preflight correctly left the
      // Draft untouched. Only refresh after a committed handoff, where the DM projection owns recovery.
      if (handoffCommitted && selectedActivityMatches(openingActivity)) {
        try { await refreshSelectedCampaign(); } catch (refreshError) { /* preserve start failure */ }
      }
      if (stillInInterview() || selectedActivityMatches(openingActivity)) {
        setInlineStatus(friendlyCommandError(
          handoffCommitted ? 'Opening narration was interrupted' : 'Game Master could not start',
          e,
        ), 'construction');
      }
    }
    } finally {
      state.campaignHandoffPending = false;
      // Warning controls are shared by later interviews, including after a successful
      // handoff changes the selected session. Their lock lasts only for this handoff.
      handoffControls.forEach((control, index) => {
        if (control === $('continue-model-guidance-btn') ||
            control === $('dismiss-model-guidance-btn')) {
          control.disabled = false;
        } else if (stillInInterview()) {
          control.disabled = handoffControlStates[index];
        }
      });
    }
  }

  function selectedIndexEntry() {
    return state.campaigns.find((entry) => entry.campaign_id === state.activeCampaignId) || null;
  }

  function selectedSessionEntry(entry = selectedIndexEntry()) {
    if (!entry || !Array.isArray(entry.sessions)) return null;
    return entry.sessions.find((session) => session.session_id === state.activeSessionId) || null;
  }

  // Return the selected session regardless of whether it belongs to a campaign or Builder home.
  function activeSessionEntry() {
    const campaignSession = selectedSessionEntry();
    if (campaignSession) return campaignSession;
    const chat = state.builderChats.find(
      (entry) => entry.session && entry.session.session_id === state.activeSessionId
    );
    return chat ? chat.session : null;
  }

  // Keep input admission derived from the authoritative session and turn lifecycle.
  function refreshSessionInputAdmission() {
    const session = activeSessionEntry();
    const configured = Boolean(
      state.bridge && state.bridge.submit_session_input && state.activeSessionId &&
      session && session.status === 'active' && session.provider_id &&
      session.provider_id !== 'preview.synthetic' && session.model_id
    );
    const activity = selectedActivity();
    const unresolvedDmTurn = state.activeSessionRole === 'dm' &&
      (activityIsWorking(activity) || Boolean(activity && activity.retryBasis && !activity.retryBasis.can_redirect));
    const canSubmit = configured && !unresolvedDmTurn && !campaignDisplayPending() &&
      !state.deletingBuilderChats.has(state.activeSessionId) && !state.archivingItems.has(state.activeSessionId);
    if ($('compact-context-btn')) $('compact-context-btn').disabled = !configured ||
      activityIsWorking(activity) || (state.activeSessionRole === 'builder' &&
        state.activeCampaignId && state.constructionDraftId === state.activeCampaignId &&
        !state.activeCreationSubflow);
    $('campaign-sequel-btn').disabled = !configured || activityIsWorking(activity) || Boolean(activity?.retryBasis);
    $('player-input').disabled = !canSubmit;
    $('player-input').setAttribute('aria-label', state.activeSessionRole === 'dm'
      ? 'In-character message or action' : 'Message to Builder');
    $('ooc-input').disabled = !canSubmit || state.activeSessionRole !== 'dm';
    $('ooc-note').hidden = state.activeSessionRole !== 'dm';
    $('send-btn').disabled = !canSubmit;
    $('stop-btn').disabled = !activityIsWorking(activity) || Boolean(activity.stopRequestedAttemptId);
    $('stop-btn').hidden = !activityIsWorking(activity);
    $('send-btn').hidden = activityIsWorking(activity);
    $('composer-steering-hint').hidden = !canSubmit || !activityIsWorking(activity) ||
      state.activeSessionRole !== 'builder';
    $('ooc-note').classList.toggle('has-note', Boolean($('ooc-input').value.trim()));
    dictation?.sync();
    return canSubmit;
  }

  function failureCategoryCopy(category) {
    const messages = {
      provider_empty_completion: 'The provider returned an empty completion. Retry this exact turn.',
      provider_unavailable: 'The provider was unavailable. Retry this exact turn when it is ready.',
      transient: 'The provider failed temporarily. Retry this exact turn.',
      rate_limit: 'The provider rate limit interrupted this turn. Retry it later.',
      auth_failure: 'The provider rejected its credentials. Fix the model connection, then retry.',
      context_overflow: 'The model context was too large for this turn.',
      invalid_request: 'The provider rejected this model request.',
      user_cancelled: 'This turn was stopped.',
      local_adapter_error: 'The local model adapter interrupted this turn.',
    };
    return messages[category] || 'This turn was interrupted.';
  }

  async function refreshSelectedCampaign() {
    if (window.__sylviaPanelContext) return gamePanels().request('refresh', {});
    const campaignId = state.activeCampaignId;
    const sessionId = state.activeSessionId;
    await refreshCampaignIndex();
    if (state.activeCampaignId === campaignId && state.activeSessionId === sessionId &&
        campaignId && state.campaigns.some((entry) => entry.campaign_id === campaignId)) {
      await openCampaign(campaignId, sessionId);
    }
  }

  async function renameSelectedCampaign() {
    if (!state.activeSnapshot || !state.bridge || !state.bridge.rename_campaign) return;
    const displayName = window.prompt('Campaign name', state.activeSnapshot.display_name || '');
    if (displayName == null || !displayName.trim()) return;
    try {
      await state.bridge.rename_campaign(
        `rename-${Date.now()}`,
        state.activeCampaignId,
        displayName.trim(),
        state.activeSnapshot.revision
      );
      await refreshSelectedCampaign();
      setInlineStatus('Campaign renamed', 'campaign-management');
    } catch (e) {
      setInlineStatus('Rename rejected', 'campaign-management');
    }
  }

  async function stopSelectedSession() {
    const entry = selectedIndexEntry();
    if (!entry || !state.activeSessionId || !state.bridge || !state.bridge.stop_agent_session) return;
    const sessionId = state.activeSessionId;
    const activity = selectedActivity();
    try {
      if (activityIsWorking(activity)) await stop();
      await state.bridge.stop_agent_session(sessionId);
      if (state.activeSessionId === sessionId) await refreshSelectedCampaign();
      selectedStatus(activity, 'DM stopped · Builder remains available', 'campaign-management');
    } catch (e) {
      selectedStatus(activity, 'Stop Session rejected', 'campaign-management');
    }
  }

  async function resumeSelectedSession() {
    const entry = selectedIndexEntry();
    if (!entry || !state.activeSessionId || !state.bridge || !state.bridge.resume_agent_session) return;
    try {
      await state.bridge.resume_agent_session(state.activeSessionId);
      await refreshSelectedCampaign();
      setInlineStatus('Session resumed', 'campaign-management');
    } catch (e) {
      setInlineStatus('Resume Session rejected', 'campaign-management');
    }
  }

  async function archiveSelectedCampaign() {
    const entry = selectedIndexEntry();
    if (!entry || !state.activeSnapshot || !state.bridge || !state.bridge.archive_campaign) return;
    if (entry.turn_status === 'open') {
      setInlineStatus('Complete current work before Archive', 'campaign-management');
      return;
    }
    try {
      await state.bridge.archive_campaign(
        `archive-${Date.now()}`,
        entry.campaign_id,
        state.activeSnapshot.revision,
        state.activeSessionId
      );
      await refreshSelectedCampaign();
      setInlineStatus('Campaign archived', 'campaign-management');
    } catch (e) {
      setInlineStatus('Archive rejected', 'campaign-management');
    }
  }

  async function restoreSelectedCampaign() {
    const entry = selectedIndexEntry();
    if (!entry || !state.activeSnapshot || !state.bridge) return;
    const lifecycle = (state.activeSnapshot.lifecycle && state.activeSnapshot.lifecycle.value) ||
      state.activeSnapshot.lifecycle;
    try {
      if (lifecycle === 'archived') {
        await state.bridge.restore_archived_campaign(
          `restore-${Date.now()}`,
          entry.campaign_id,
          state.activeSnapshot.revision,
          state.activeSessionId
        );
      } else {
        await state.bridge.restore_trashed_campaign(
          `restore-${Date.now()}`,
          entry.campaign_id,
          state.activeSnapshot.revision
        );
      }
      await refreshSelectedCampaign();
      setInlineStatus('Campaign restored', 'campaign-management');
    } catch (e) {
      setInlineStatus('Restore rejected', 'campaign-management');
    }
  }

  async function trashSelectedCampaign() {
    if (!state.activeSnapshot || !state.bridge || !state.bridge.trash_campaign) return;
    try {
      await state.bridge.trash_campaign(
        `trash-${Date.now()}`,
        state.activeCampaignId,
        state.activeSnapshot.revision,
        state.activeSessionId
      );
      await refreshSelectedCampaign();
      setInlineStatus('Campaign moved to Trash', 'campaign-management');
    } catch (e) {
      setInlineStatus('Trash rejected', 'campaign-management');
    }
  }

  async function purgeSelectedCampaign() {
    if (!state.activeSnapshot || !state.bridge || !state.bridge.purge_campaign) return;
    const phrase = `permanently purge ${state.activeCampaignId}`;
    const confirmation = window.prompt(`Type exactly: ${phrase}`);
    if (confirmation !== phrase) {
      setInlineStatus('Permanent deletion cancelled', 'campaign-deletion');
      return;
    }
    try {
      await state.bridge.purge_campaign(
        `purge-${Date.now()}`,
        state.activeCampaignId,
        state.activeSnapshot.revision,
        confirmation
      );
      state.activeCampaignId = null;
      state.activeSessionId = null;
      state.activeSessionRole = null;
      state.activeSnapshot = null;
      updateModelSwitchButtons();
      await refreshCampaignIndex();
      updateLifecycleControls(null, null);
      clearCampaignFrame();
      $('chronology').innerHTML = '<div class="empty-state"><div class="empty-icon">◆</div><div>Campaign permanently deleted.</div></div>';
      setInlineStatus('Campaign permanently deleted', 'campaign-deletion');
    } catch (e) {
      setInlineStatus('Permanent deletion could not be completed', 'campaign-deletion');
    }
  }

  // ---- Start a no-rules campaign through the standard Builder construction path ----
  async function createNoRulesCampaign() {
    const draft = await startCampaignBuilder();
    if (!draft || state.activeCampaignId !== draft.campaign_id ||
        state.activeSessionId !== draft.builder_session_id) return;
    $('construction-package').value = '';
    updateConstructionCompatibility();
    state.constructionNameSuggested = true;
    $('construction-name').value =
      'No-Rules Campaign ' + new Date().toLocaleTimeString();
    showConstructionStepId('ruleset');
    setInlineStatus('Campaign Draft · narrative play', 'construction');
  }

  // Keep archive results visible in the Settings panel where the operation began.
  function setCampaignDataStatus(message) {
    $('campaign-data-status').textContent = message;

  }

  function sequelSelectionIsCurrent(dialog = state.sequelDialog) {
    return dialog && state.sequelDialog === dialog && state.view === 'session' &&
      state.activeCampaignId === dialog.campaign_id && state.activeSessionId === dialog.session_id;
  }

  function refreshSequelConfirmation() {
    const dialog = state.sequelDialog;
    $('campaign-sequel-append').disabled = !dialog?.preview || dialog.busy ||
      (!dialog.request && (!$('campaign-sequel-reviewed').checked ||
        !$('campaign-sequel-connection').value.trim()));
  }

  function resetSequelReview() {
    if (state.sequelDialog) state.sequelDialog.preview = null;
    $('campaign-sequel-review').hidden = true;
    $('campaign-sequel-confirmation').hidden = true;
    $('campaign-sequel-reviewed').checked = false;
    refreshSequelConfirmation();
  }

  async function openCampaignSequel() {
    if (selectedSessionIsWorking() || selectedActivity()?.retryBasis || state.activeSessionRole !== 'dm') return;
    const dialog = { campaign_id: state.activeCampaignId, session_id: state.activeSessionId,
      operation_id: `append-adventure-${crypto.randomUUID()}`, preview: null, request: null,
      busy: false, context: null };
    state.sequelDialog = dialog;
    resetSequelReview();
    $('campaign-sequel-connection').value = '';
    for (const id of ['campaign-sequel-source', 'campaign-sequel-connection', 'campaign-sequel-reviewed'])
      $(id).disabled = false;
    $('campaign-sequel-append').textContent = 'Append adventure';
    $('campaign-sequel-status').textContent = 'Loading your adventure library…';
    $('campaign-sequel-dialog').showModal();
    try {
      const [catalog, context] = await Promise.all([
        state.bridge.campaign_creation_catalog(),
        state.bridge.campaign_sequel_context({ campaign_id: dialog.campaign_id,
          session_id: dialog.session_id }),
      ]);
      if (!sequelSelectionIsCurrent(dialog)) return;
      dialog.context = context;
      $('campaign-sequel-source').innerHTML = '<option value="">Choose an adventure…</option>' +
        catalog.filter(item => item.kind === 'campaign_plan').map(item =>
          `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)}</option>`).join('');
      $('campaign-sequel-status').textContent =
        'Choose an adventure to check its exact world and rules. You can also author a compatible next adventure in Builder.';
    } catch (error) {
      if (sequelSelectionIsCurrent(dialog)) $('campaign-sequel-status').textContent =
        friendlyCommandError('Adventure library could not load', error);
    }
  }

  async function previewCampaignSequel() {
    const dialog = state.sequelDialog;
    const sourceId = $('campaign-sequel-source').value;
    if (!sequelSelectionIsCurrent(dialog) || !sourceId || dialog.busy || dialog.request) return;
    resetSequelReview();
    dialog.busy = true;
    $('campaign-sequel-status').textContent = 'Checking this adventure against the campaign…';
    try {
      const preview = await state.bridge.preview_campaign_sequel({ campaign_id: dialog.campaign_id,
        session_id: dialog.session_id, source_id: sourceId });
      if (!sequelSelectionIsCurrent(dialog) || sourceId !== $('campaign-sequel-source').value) return;
      dialog.preview = preview;
      const initial = preview.source_initial_state || {};
      const facts = (initial.facts || []).map(item => typeof item === 'string' ? item : item.fact);
      $('campaign-sequel-review').innerHTML =
        `<h3>${escapeHtml(preview.title)}</h3><p>${escapeHtml(preview.summary)}</p>` +
        `<p>${escapeHtml(preview.warning)}</p>` +
        `<p><strong>Retained characters:</strong> ${preview.participants.map(item => escapeHtml(item.name)).join(', ') || 'Current campaign party'}</p>` +
        (preview.ending.choice === 'finish' && preview.ending.has_after_story
          ? '<p><strong>This campaign already chose its after-story.</strong> Review the saved future in history. The next adventure must follow those events.</p>' : '') +
        '<h4>New sequence</h4><ol>' + preview.acts.map(item =>
          `<li>Act ${item.number}: ${escapeHtml(item.title)}</li>`).join('') + '</ol>' +
        '<details><summary>Retained story for review</summary>' +
        '<p>This excerpt includes up to 64 recent history entries and current facts. The complete saved history remains authoritative.</p>' +
        `<pre>${escapeHtml(JSON.stringify({ scene: preview.current_scene,
          facts: preview.retained_facts, recent_history: dialog.context?.recent_history }, null, 2))}</pre></details>` +
        '<details><summary>Review preparation and setup assumptions (contains story spoilers)</summary>' +
        '<p>Authored starting facts are shown for compatibility review. They are not applied to the campaign.</p>' +
        (facts.length ? '<ul>' + facts.map(item => `<li>${escapeHtml(item)}</li>`).join('') + '</ul>' : '') +
        `<pre>${escapeHtml(preview.source_markdown)}</pre>` +
        '<details><summary>Exact authored setup</summary>' +
        `<pre>${escapeHtml(JSON.stringify({ initial_state: initial,
          starting_location: preview.source_plan?.starting_location,
          starting_party: preview.source_plan?.starting_party }, null, 2))}</pre></details></details>` +
        '<details><summary>Review separate story identities</summary>' +
        '<p>These mappings keep new progress separate from earlier acts and objectives. World identities are retained.</p>' +
        '<table><thead><tr><th>Authored reference</th><th>Campaign reference</th></tr></thead><tbody>' +
        Object.entries(preview.identity_mapping).map(([from, to]) =>
          `<tr><td>${escapeHtml(from)}</td><td>${escapeHtml(to)}</td></tr>`).join('') +
        '</tbody></table></details>' +
        `<details><summary>Exact source</summary><pre>${escapeHtml(JSON.stringify({
          id: preview.basis.source_id, sha256: preview.basis.source_sha256,
          bindings: preview.source_bindings }, null, 2))}</pre></details>`;
      $('campaign-sequel-review').hidden = false;
      $('campaign-sequel-confirmation').hidden = false;
      $('campaign-sequel-status').textContent =
        'Review the adventure against your history. Resolve incompatible assumptions in Builder before appending.';
    } catch (error) {
      if (sequelSelectionIsCurrent(dialog)) $('campaign-sequel-status').textContent =
        friendlyCommandError('This adventure cannot be appended', error);
    } finally {
      dialog.busy = false;
      if (sequelSelectionIsCurrent(dialog)) refreshSequelConfirmation();
    }
  }

  async function appendCampaignSequel() {
    const dialog = state.sequelDialog;
    if (!sequelSelectionIsCurrent(dialog) || !dialog.preview || dialog.busy ||
        $('campaign-sequel-append').disabled) return;
    if (!dialog.request) dialog.request = {
      campaign_id: dialog.campaign_id, session_id: dialog.session_id,
      operation_id: dialog.operation_id, source_id: dialog.preview.basis.source_id,
      expected_revision: dialog.preview.basis.expected_revision,
      preview_sha256: dialog.preview.preview_sha256,
      review: { connection: $('campaign-sequel-connection').value.trim(), timeline: 'after', reviewed: true },
    };
    dialog.busy = true;
    for (const id of ['campaign-sequel-source', 'campaign-sequel-connection', 'campaign-sequel-reviewed'])
      $(id).disabled = true;
    refreshSequelConfirmation();
    $('campaign-sequel-status').textContent = 'Appending the reviewed adventure…';
    try {
      await state.bridge.append_campaign_sequel(dialog.request);
      if (!sequelSelectionIsCurrent(dialog)) return;
      $('campaign-sequel-dialog').close();
      await openCampaign(dialog.campaign_id, dialog.session_id);
      setInlineStatus('Adventure added. Continue with your Game Master when you are ready.');
    } catch (error) {
      if (sequelSelectionIsCurrent(dialog)) {
        $('campaign-sequel-status').textContent = friendlyCommandError(
          'Append was not confirmed. Retry this exact request to recover its result; if the review is stale, cancel and review again', error);
        $('campaign-sequel-append').textContent = 'Retry append';
      }
    } finally {
      dialog.busy = false;
      if (sequelSelectionIsCurrent(dialog)) refreshSequelConfirmation();
    }
  }

  async function authorCampaignSequel() {
    const dialog = state.sequelDialog;
    if (!sequelSelectionIsCurrent(dialog) || dialog.busy || dialog.request || !dialog.context) return;
    const context = dialog.context;
    const preparation = {
      source_plan_id: context.accepted_plan?.id, source_references: context.source_references,
      bindings: context.accepted_plan?.bindings,
      boundaries: context.accepted_plan?.boundaries, current_scene: context.current_scene,
      participants: context.participants.map(item => ({ id: item.id, name: item.data.name })),
      recent_facts: context.facts.slice(-64), recent_history: context.recent_history,
      ending: context.ending,
    };
    const fullContext = JSON.stringify(preparation, null, 2);
    const contextExcerpt = fullContext.length <= 60000 ? fullContext :
      fullContext.slice(0, 60000) + '\n\n[Context excerpt truncated at 60,000 characters. ' +
      'It may end during a field. Complete saved history and boundaries remain authoritative. ' +
      'Ask me for missing material rather than treating it as absent.]';
    $('campaign-sequel-dialog').close();
    await startBuilderChat({ displayName: `Next adventure: ${context.campaign_name}`,
      initialPrompt: 'Help me author or adapt a compatible next Campaign Plan for this existing campaign. ' +
        'Keep its exact Setting and Ruleset snapshots, characters, boundaries, played events, ' +
        'unresolved consequences and already narrated future. This independent Builder may publish ' +
        'a reusable Plan; it cannot change the live campaign. Its starting setup will not reset ' +
        'the campaign. Ask about genuine narrative conflicts. I will review and append it separately. ' +
        'This context includes up to 64 recent facts and history excerpts (up to 4000 characters each); ' +
        'my complete saved history remains authoritative.\n\n' +
        contextExcerpt });
  }

  async function exportSelectedCampaign() {
    if (!state.activeCampaignId || !state.bridge || !state.bridge.export_campaign_file) return;
    const includeThinking = $('export-include-thinking')?.checked === true;
    if ($('export-include-thinking')) $('export-include-thinking').checked = false;
    const nonce = Date.now();
    setCampaignDataStatus('Choose export destination…');
    try {
      const result = await state.bridge.export_campaign_file(
        `export-file-${nonce}`,
        `export-file-operation-${nonce}`,
        state.activeCampaignId,
        includeThinking
      );
      setCampaignDataStatus(result.status === 'cancelled'
        ? 'Export cancelled'
        : `Exported ${result.file_name || 'campaign archive'}`);
    } catch (e) {
      setCampaignDataStatus(friendlyCommandError('Export failed', e));
    }
  }

  async function recoverCampaignFile() {
    if (!state.bridge || !state.bridge.recover_campaign_file) return;
    setCampaignDataStatus('Choose campaign archive…');
    try {
      const result = await state.bridge.recover_campaign_file(`recover-file-${Date.now()}`);
      if (result.status === 'cancelled') {
        setCampaignDataStatus('Recovery cancelled');
        return;
      }
      await refreshCampaignIndex();
      const campaignId = result.recovery && result.recovery.campaign_id;
      if (campaignId) await openCampaign(campaignId);
      setCampaignDataStatus(`Recovered ${result.file_name || 'campaign archive'}`);
    } catch (e) {
      const detail = friendlyCommandError('Recovery failed', e);
      setCampaignDataStatus(detail.includes('portable_recovery_campaign_collision')
        ? 'This campaign already exists here. Your current campaign is unchanged.'
        : detail);
    }
  }

  async function attachBuilderSources() {
    if (state.activeSessionRole !== 'builder' || selectedSessionIsWorking() ||
        !state.activeSessionId) return;
    if (!state.bridge || !state.bridge.ingest_builder_sources) return;
    const activity = selectedActivity();
    const sessionId = state.activeSessionId;
    setInlineStatus('Choose text or PDF sources…');
    try {
      const result = await state.bridge.ingest_builder_sources(
        sessionId,
        'source-ingestion-' + Date.now()
      );
      if (!selectedActivityMatches(activity)) return;
      if (result.status === 'cancelled') {
        setInlineStatus('Source attachment cancelled');
        return;
      }
      setInlineStatus(`Attached ${result.source_count} local source${result.source_count === 1 ? '' : 's'}`);
      await renderBuilderSession(sessionId);
    } catch (e) {
      selectedStatus(activity, friendlyCommandError('Source attachment failed', e));
    }
  }

  // ---- Submit player input through the configured canonical session loop ----
  function saveSelectedSessionDraft() {
    const input = $('player-input');
    if (!input || !state.activeSessionId) return;
    const oocText = state.activeSessionRole === 'dm' ? $('ooc-input').value : '';
    if (input.value || oocText || $('ooc-note').open) {
      state.sessionDrafts.set(state.activeSessionId, {
        text: input.value, oocText, oocOpen: $('ooc-note').open,
      });
    }
    else state.sessionDrafts.delete(state.activeSessionId);
  }

  function restoreSelectedSessionDraft() {
    dictationRevision += 1;
    dictation?.sync();
    const input = $('player-input');
    if (!input) return;
    const draft = state.sessionDrafts.get(state.activeSessionId) || {};
    input.value = draft.text || '';
    $('ooc-input').value = draft.oocText || '';
    $('ooc-note').open = Boolean(draft.oocOpen);
  }

  // Clear only the accepted session's draft; navigating to another chat must not erase its text.
  function clearAcceptedInput(activity) {
    const submitted = activity && activity.pendingInput;
    if (!submitted || !currentActivity(activity)) return;
    const saved = state.sessionDrafts.get(submitted.sessionId);
    if (saved && saved.text === submitted.text && saved.oocText === submitted.oocText) {
      state.sessionDrafts.delete(submitted.sessionId);
    }
    if (state.activeSessionId === submitted.sessionId &&
        $('player-input').value === submitted.text && $('ooc-input').value === submitted.oocText) {
      $('player-input').value = '';
      $('ooc-input').value = '';
      $('ooc-note').open = false;
    }
    activity.pendingInput = null;
  }

  // Render only owner-projected block kinds and labels; model HTML and image paths are not UI.
  function renderResponseBlocks(blocks, fallbackText) {
    if (!Array.isArray(blocks) || !blocks.length) {
      return `<div class="story-text">${renderNarrationMarkdown(fallbackText)}</div>`;
    }
    return blocks.map((block) => {
      if (block.kind === 'npc_dialogue') {
        return `<section class="response-block npc-dialogue" aria-label="NPC dialogue">` +
          `<div class="response-label">${escapeHtml(block.speaker_label || 'Unknown speaker')}</div>` +
          `<div class="story-text">${renderNarrationMarkdown(block.text || '')}</div></section>`;
      }
      if (block.kind === 'ooc') {
        return `<section class="response-block ooc-message" aria-label="Out of character">` +
          `<div class="response-label">OOC · Sylvia</div>` +
          `<div class="story-text">${escapeHtml(block.text || '')}</div></section>`;
      }
      return `<div class="response-block story-text">${renderNarrationMarkdown(block.text || '')}</div>`;
    }).join('');
  }

  function modelChoiceFromReference(reference) {
    const folded = reference.toLowerCase();
    const matches = state.modelChoices.map((choice, index) => {
      const prefix = String(choice.provider_id || '').replaceAll('_', '-') + '/';
      const upstream = String(choice.model_id || '').startsWith(prefix)
        ? String(choice.model_id).slice(prefix.length)
        : String(choice.model_id || '');
      const names = [choice.model_id, choice.model_name, upstream]
        .filter(Boolean).map((value) => String(value).toLowerCase());
      return names.includes(folded) ? { choice, index } : null;
    }).filter(Boolean);
    return matches.length === 1 ? matches[0] : null;
  }

  async function handleModelSlashCommand(text) {
    const parts = text.split(/\s+/).filter(Boolean);
    if (!parts.length || parts[0].toLowerCase() !== '/model') return false;
    if (selectedSessionIsWorking()) {
      setInlineStatus('Stop or finish the current model turn first');
      return true;
    }
    const flags = parts.slice(1).filter((part) => part.startsWith('--'));
    const references = parts.slice(1).filter((part) => !part.startsWith('--'));
    if (flags.length > 1 || references.length > 1 || parts.length > 3) {
      setInlineStatus('Use /model [model] [--once|--global]');
      return true;
    }
    let mode = 'current';
    if (flags[0] === '--once') mode = 'once';
    else if (flags[0] === '--global') mode = 'global';
    else if (flags.length) {
      setInlineStatus('Supported model modes are --once and --global');
      return true;
    }
    if (!references.length) {
      state.pendingModelMode = mode;
      openSettings('models');
      $('model-status').textContent = mode === 'once'
        ? 'Choose a model, then select Use next turn only.'
        : mode === 'global'
          ? 'Choose a model, then select Switch chat & default.'
          : 'Choose a model, then select Switch this chat.';
      return true;
    }
    const match = modelChoiceFromReference(references[0]);
    if (!match) {
      state.pendingModelMode = mode;
      openSettings('models');
      $('model-status').textContent = 'That model is unavailable or ambiguous. Choose it here.';
      return true;
    }
    $('chat-model-choice').value = String(match.index);
    await switchSelectedSessionModel(mode);
    setInlineStatus(mode === 'once'
      ? 'Next model turn override staged'
      : 'Session model switched');
    return true;
  }

  async function respondToPlayerAction(campaignId, sessionId, actionId) {
    // Main already schedules the response after a forwarded action commits.
    if (window.__sylviaPanelContext) return;
    if (!sessionId || !state.bridge.campaign_panels) return;
    try {
      const projection = await state.bridge.campaign_panels(campaignId);
      const offer = projection?.response_action;
      if (!offer?.automatic || !offer.available || offer.action_id !== actionId ||
          (offer.session_id && offer.session_id !== sessionId)) return;
      await submitInput('', { playerAction: { campaignId, sessionId, actionId, automatic: true } });
    } catch (error) {
      selectedStatus(state.sessionActivities.get(sessionId),
        friendlyCommandError('Action saved; Sylvia’s response could not start', error));
    }
  }

  async function submitInput(text, {
    builderStart = false, builderSessionId = null, playerAction = null, campaignEndingChoice = null,
    builderQuestionAnswers = null,
  } = {}) {
    if (builderSessionId && !builderStart) return;
    const sessionId = playerAction?.sessionId || builderSessionId || state.activeSessionId;
    if (state.deletingBuilderChats.has(sessionId)) return;
    const sessionRole = playerAction ? 'dm' : builderSessionId ? 'builder' : state.activeSessionRole;
    const campaignId = playerAction?.campaignId || (builderSessionId ? null : state.activeCampaignId);
    const queueLane = sequenceLanes.get(JSON.stringify([campaignId, sessionId]));
    if (!playerAction && sessionRole === 'dm' && queueLane?.pending) {
      setInlineStatus('Wait for the queued actions to finish saving or executing, then send your message.');
      return;
    }
    const compact = text.trim() === '/compact' && !playerAction && !builderStart;
    const oocText = !compact && !playerAction && !campaignEndingChoice && sessionRole === 'dm' ? $('ooc-input').value : '';
    if ((!playerAction && !builderStart && !campaignEndingChoice && !text.trim() && !oocText.trim()) || !sessionId) return;
    if (!state.bridge || !state.bridge.submit_session_input) {
      setInlineStatus('Model session configuration required');
      return;
    }
    if (!oocText.trim() && /^\/model(?:\s|$)/i.test(text.trim())) {
      if (await handleModelSlashCommand(text.trim()) && state.activeSessionId === sessionId &&
          $('player-input').value === text) {
        $('player-input').value = '';
        saveSelectedSessionDraft();
      }
      return;
    }
    let activity = ensureSessionActivity({ sessionId, sessionRole, campaignId });
    if (compact && activityIsWorking(activity)) {
      selectedStatus(activity, 'Finish or stop the current model turn before compacting');
      return;
    }
    if (activityIsWorking(activity) && sessionRole === 'builder') {
      if (builderQuestionAnswers) return;
      if (!state.bridge.steer_session || activity.stopRequestedAttemptId || !text.trim()) return;
      if (selectedActivityMatches(activity)) {
        dictation?.cancel('', { announce: false });
      }
      saveSelectedSessionDraft();
      try {
        await state.bridge.steer_session(sessionId, 'steering-' + crypto.randomUUID(), text.trim());
        if (!currentActivity(activity)) return;
        const saved = state.sessionDrafts.get(sessionId);
        if (saved && saved.text === text) state.sessionDrafts.delete(sessionId);
        if (selectedActivityMatches(activity) && $('player-input').value === text) {
          $('player-input').value = '';
        }
        selectedStatus(activity, 'Steering accepted…');
        if (selectedActivityMatches(activity)) await renderBuilderSession(sessionId);
      } catch (e) {
        selectedStatus(activity, 'Steering rejected');
      }
      return;
    }
    if (activityIsWorking(activity)) {
      selectedStatus(activity, 'Stop or finish the current model turn first');
      return;
    }
    const campaign = state.campaigns.find((entry) => entry.campaign_id === campaignId);
    if (!compact && sessionRole !== 'builder' && (!campaign || !campaign.turn_id ||
        (activity.retryBasis && (!activity.retryBasis.can_redirect || playerAction || campaignEndingChoice)))) {
      selectedStatus(activity, activity.retryBasis ? 'Retry or resolve the interrupted turn first' : 'No active turn');
      return;
    }
    const command = {
      session_id: sessionId,
      turn_id: campaign ? campaign.turn_id : null,
      attempt_id: 'attempt-' + crypto.randomUUID(),
      message_id: 'message-' + crypto.randomUUID(),
      user_text: text,
      ooc_text: oocText.trim() ? oocText : null,
      allow_public_web: !builderStart && sessionRole === 'builder' && webResearchPermission(),
      builder_start: builderStart,
      player_action_id: playerAction?.actionId || null,
      automatic_response: Boolean(playerAction?.automatic),
      campaign_ending_choice: campaignEndingChoice,
      ...(builderQuestionAnswers ? { builder_question_answers: builderQuestionAnswers } : {}),
      redirect_interruption_id: !compact && !playerAction && !campaignEndingChoice && sessionRole === 'dm'
        ? activity.retryBasis?.interruption_id || null : null,
    };
    if (!playerAction && !builderStart && !campaignEndingChoice && !compact && state.activeSessionId === sessionId) {
      dictation?.cancel('', { announce: false });
    }
    if (state.activeSessionId === sessionId) {
      setWebResearchPermission(false);
      saveSelectedSessionDraft();
    }
    activity = beginSessionActivity(activity, command.attempt_id);
    activity.contextCompaction = compact;
    activity.ownerId = compact || sessionRole === 'builder' ? sessionId : campaignId;
    activity.pendingInput = playerAction || compact || campaignEndingChoice || builderQuestionAnswers
      ? null : { sessionId, messageId: command.message_id, text, oocText };
    refreshActivityControls(activity);
    selectedStatus(activity, compact ? 'Compacting context · saved conversation is kept' : 'Streaming…');
    renderActivityIndexes();
    startProjectionPolling(activity);
    try {
      const submission = state.bridge.submit_session_input(command);
      activity.submissionSettled = submission.catch(() => {});
      startStreamHeartbeat(activity, true);
      const result = await submission;
      if (!currentActivity(activity)) return;
      clearAcceptedInput(activity);
      const stopped = activity.stopRequestedAttemptId === command.attempt_id;
      const interrupted = result && (result.interruption || result.status === 'interrupted');
      finishSessionActivity(activity, stopped ? 'stopped' : interrupted ? 'interrupted' : 'completed',
        stopped ? 'Stopped' : interrupted ? 'Generation was interrupted' :
          compact ? 'Context compacted · saved conversation is kept' : 'Completed');
    } catch (e) {
      if (currentActivity(activity)) {
        const stopped = activity.stopRequestedAttemptId === command.attempt_id;
        finishSessionActivity(activity, stopped ? 'stopped' : 'interrupted',
          stopped ? 'Stopped' : friendlyCommandError('Session request rejected', e));
      }
    } finally {
      stopStreamHeartbeat(activity);
      stopProjectionPolling(activity);
      if (currentActivity(activity)) {
        activity.pendingInput = null;
        try {
          if (selectedActivityMatches(activity)) await renderPlayerProjection(campaignId, sessionId);
          if (sessionRole === 'builder' && !campaignId) await refreshBuilderChats();
          else await refreshCampaignIndex();
        } catch (e) { /* A failed final paint does not change the request result. */ }
        refreshActivityControls(activity);
        renderActivityIndexes();
      }
    }
  }

  function renderActivityIndexes() {
    renderBuilderChatRail();
    renderSidebarState();
    renderHome();
  }

  function refreshActivityControls(activity) {
    if (!selectedActivityMatches(activity)) return;
    if (panelHost && !panelHost.isChild) void panelHost.publish().catch(() => {});
    const canSubmit = refreshSessionInputAdmission();
    updateModelSwitchButtons();
    updateCampaignSetupControl();
    const builderAvailable = canSubmit && activity.sessionRole === 'builder' &&
      !activityIsWorking(activity);
    $('attach-builder-sources-btn').disabled = !builderAvailable;
    $('allow-public-web').disabled = !builderAvailable;
  }

  function finishSessionActivity(activity, phase, label) {
    if (!currentActivity(activity)) return;
    if (state.displayHistory?.sessionId === activity.sessionId) state.displayHistory.generation++;
    activity.phase = phase;
    activity.terminalLabel = label;
    activity.restored = false;
    activity.attachmentGeneration = null;
    stopStreamHeartbeat(activity);
    stopProjectionPolling(activity);
    activity.contextCompaction = false;
    activity.ownerId = activity.sessionRole === 'builder' ? activity.sessionId : activity.campaignId;
    persistDisconnectedActivities();
    selectedStatus(activity, phase === 'completed' ? '' : label);
    refreshActivityControls(activity);
    renderActivityIndexes();
  }

  // ---- Live streaming projection: read-only owner polling while pending ----
  function startProjectionPolling(activity) {
    if (!activity) return;
    stopProjectionPolling(activity);
    const poll = async () => {
      if (!activityIsWorking(activity) || !currentActivity(activity) || activity.projectionPollBusy) return;
      activity.projectionPollBusy = true;
      try {
        if (activity.restored && activity.attemptId) {
          const statusMethod = activity.sessionRole === 'builder' || activity.contextCompaction
            ? state.bridge.provider_stream_status : state.bridge.narration_attempt_status;
          if (statusMethod) {
            const attempt = await statusMethod(activity.ownerId, activity.attemptId);
            if (!currentActivity(activity) || !activityIsWorking(activity)) return;
            const terminal = {
              completed: ['completed', 'Completed'],
              cancelled: ['stopped', 'Stopped'],
              stopped: ['stopped', 'Stopped'],
              interrupted: ['interrupted', 'Generation was interrupted'],
              failed: ['interrupted', 'Generation was interrupted'],
              unavailable: ['interrupted', 'Previous generation could not be restored · Saved work retained'],
            }[attempt.status];
            if (terminal) finishSessionActivity(activity, ...terminal);
            else if (attempt.status === 'disconnected') {
              await reconnect(activity.attemptId, activity.acknowledgedSequence,
                activity.attachmentGeneration, activity);
            }
          }
        }
        if (selectedActivityMatches(activity)) {
          await renderPlayerProjection(activity.campaignId, activity.sessionId);
          refreshActivityControls(activity);
        }
      } catch (e) {
        // Failed observation leaves the attempt attached; the next poll can recover.
      } finally {
        activity.projectionPollBusy = false;
      }
    };
    activity.projectionTimer = setInterval(() => { void poll(); }, 500);
    void poll();
  }

  function stopProjectionPolling(activity = selectedActivity()) {
    if (!activity) return;
    if (activity.projectionTimer != null) clearInterval(activity.projectionTimer);
    activity.projectionTimer = null;
    activity.projectionPollBusy = false;
  }

  function startStreamHeartbeat(activity, immediate = false) {
    if (!activity) return;
    stopStreamHeartbeat(activity);
    if (!state.bridge || !activity.attemptId || !activity.ownerId) return;
    const heartbeatMethod = activity.sessionRole === 'builder' || activity.contextCompaction
      ? state.bridge.provider_stream_heartbeat
      : state.bridge.stream_heartbeat;
    if (!heartbeatMethod) return;
    const heartbeat = async () => {
      if (!activityIsWorking(activity) || activity.heartbeatBusy ||
          state.sessionActivities.get(activity.sessionId) !== activity) return;
      activity.heartbeatBusy = true;
      try {
        const lease = await heartbeatMethod(
          activity.ownerId,
          activity.attemptId,
          activity.attachmentGeneration
        );
        if (state.sessionActivities.get(activity.sessionId) === activity &&
            lease && Number.isSafeInteger(lease.attachment_generation)) {
          activity.attachmentGeneration = lease.attachment_generation;
          persistDisconnectedActivities();
        }
      } catch (e) {
        // Initial attachment may still be pending. Reclaim an established connection
        // during its disconnect grace so a delayed heartbeat does not stop live work.
        if (currentActivity(activity) && activityIsWorking(activity) &&
            Number.isSafeInteger(activity.attachmentGeneration)) {
          await reconnect(activity.attemptId, activity.acknowledgedSequence,
            activity.attachmentGeneration, activity);
        }
      } finally {
        activity.heartbeatBusy = false;
      }
    };
    activity.heartbeatTimer = setInterval(() => { void heartbeat(); }, 2000);
    if (immediate) void heartbeat();
  }

  function stopStreamHeartbeat(activity = selectedActivity()) {
    if (!activity) return;
    if (activity.heartbeatTimer != null) clearInterval(activity.heartbeatTimer);
    activity.heartbeatTimer = null;
    activity.heartbeatBusy = false;
  }

  // ---- Stop: interrupt the narration through authority command ----
  async function stop() {
    const activity = selectedActivity();
    if (!state.bridge || !activityIsWorking(activity) || !activity.ownerId ||
        !activity.attemptId || activity.stopRequestedAttemptId) return;
    const interrupt = activity.sessionRole === 'builder' || activity.contextCompaction
      ? state.bridge.interrupt_provider_stream : state.bridge.interrupt_stream;
    if (!interrupt) return;
    activity.stopRequestedAttemptId = activity.attemptId;
    refreshActivityControls(activity);
    selectedStatus(activity, 'Stopping…');
    try {
      await interrupt(activity.ownerId, activity.attemptId);
      // Admission reopens only after the cancelled request releases its owner session.
      if (activity.submissionSettled) await activity.submissionSettled;
      finishSessionActivity(activity, 'stopped', 'Stopped');
      if (selectedActivityMatches(activity)) {
        await renderPlayerProjection(activity.campaignId, activity.sessionId);
        refreshActivityControls(activity);
      }
    } catch (e) {
      activity.stopRequestedAttemptId = null;
      selectedStatus(activity, 'Stop failed');
      refreshActivityControls(activity);
    }
  }

  // ---- Manual Retry: configured provider, narration only, no automatic fallback ----
  async function retry() {
    if (!state.bridge || !state.bridge.manual_retry_session || state.activeSessionRole !== 'dm') return;
    const campaignId = state.activeCampaignId;
    const sessionId = state.activeSessionId;
    if (!campaignId || !sessionId) return;
    let activity = ensureSessionActivity({ sessionId, sessionRole: 'dm', campaignId });
    if (activityIsWorking(activity)) {
      selectedStatus(activity, 'Stop or finish the current model turn first');
      return;
    }
    let interruption = activity.retryBasis;
    if ((!interruption || !interruption.interruption_id) && activity.interruptedAttemptId) {
      try {
        interruption = await state.bridge.retryable_interruption(campaignId, activity.interruptedAttemptId);
      } catch (e) { interruption = null; }
      if (!currentActivity(activity) || activityIsWorking(activity)) return;
    }
    if (!interruption || !interruption.interruption_id) {
      selectedStatus(activity, 'No retry basis');
      return;
    }
    const retryId = interruption.resolving_retry_id || ('retry-' + crypto.randomUUID());
    activity = beginSessionActivity(activity, retryId);
    refreshActivityControls(activity);
    selectedStatus(activity, 'Retry streaming…');
    renderActivityIndexes();
    startProjectionPolling(activity);
    try {
      const submission = state.bridge.manual_retry_session(sessionId, interruption.interruption_id, retryId);
      activity.submissionSettled = submission.catch(() => {});
      startStreamHeartbeat(activity, true);
      await submission;
      if (!currentActivity(activity)) return;
      activity.retryBasis = null;
      activity.interruptedAttemptId = null;
      finishSessionActivity(activity, 'completed', 'Completed');
    } catch (e) {
      let nextBasis = null;
      try { nextBasis = await state.bridge.retryable_interruption(campaignId, retryId); }
      catch (basisError) { /* Keep the last known basis until the next projection. */ }
      if (!currentActivity(activity)) return;
      activity.retryBasis = nextBasis || activity.retryBasis;
      activity.interruptedAttemptId = nextBasis ? retryId : activity.interruptedAttemptId;
      const stopped = activity.stopRequestedAttemptId === retryId;
      finishSessionActivity(activity, stopped ? 'stopped' : 'interrupted',
        nextBasis ? 'Retry interrupted · Retry available' : stopped ? 'Stopped' : 'Retry rejected');
    } finally {
      stopStreamHeartbeat(activity);
      stopProjectionPolling(activity);
      if (selectedActivityMatches(activity)) {
        await renderPlayerProjection(campaignId, sessionId);
        refreshActivityControls(activity);
      }
    }
  }

  // ---- Reconnect/replay (G-RECOVER) through authority command ----
  // Transport hints contain identities only. The owner decides whether an attempt is still running.
  function persistDisconnectedActivities() {
    const streams = [...state.sessionActivities.values()].filter(activityIsWorking).map((activity) => ({
      campaign_id: activity.campaignId,
      session_id: activity.sessionId,
      session_role: activity.sessionRole,
      context_compaction: Boolean(activity.contextCompaction),
      stream_owner_id: activity.ownerId,
      attempt_id: activity.attemptId,
      acknowledged_sequence: activity.acknowledgedSequence || 0,
      attachment_generation: activity.attachmentGeneration,
    }));
    try {
      if (streams.length) sessionStorage.setItem(DISCONNECTED_STREAM_KEY, JSON.stringify({ version: 2, streams }));
      else sessionStorage.removeItem(DISCONNECTED_STREAM_KEY);
    } catch (e) { /* Best-effort transport metadata; saved conversations belong to the owner. */ }
  }

  function signalActiveStreamDisconnect() {
    persistDisconnectedActivities();
    for (const activity of state.sessionActivities.values()) {
      if (!activityIsWorking(activity)) continue;
      stopStreamHeartbeat(activity);
      stopProjectionPolling(activity);
      const disconnect = activity.sessionRole === 'builder' || activity.contextCompaction
        ? state.bridge && state.bridge.disconnect_provider_stream
        : state.bridge && state.bridge.disconnect_stream;
      if (disconnect && (activity.sessionRole === 'builder' || activity.contextCompaction ||
          Number.isSafeInteger(activity.attachmentGeneration))) {
        void disconnect(activity.ownerId, activity.attemptId, activity.attachmentGeneration).catch(() => {});
      }
    }
  }

  async function restoreDisconnectedStream() {
    let saved;
    try { saved = JSON.parse(sessionStorage.getItem(DISCONNECTED_STREAM_KEY) || 'null'); }
    catch (e) { sessionStorage.removeItem(DISCONNECTED_STREAM_KEY); return; }
    const streams = saved && saved.version === 2 ? saved.streams : saved ? [saved] : [];
    if (!Array.isArray(streams)) { sessionStorage.removeItem(DISCONNECTED_STREAM_KEY); return; }
    const restored = [];
    for (const hint of streams) {
      if (!hint || typeof hint.session_id !== 'string' || typeof hint.attempt_id !== 'string' ||
          (hint.attachment_generation != null && !Number.isSafeInteger(hint.attachment_generation))) continue;
      const campaign = state.campaigns.find((entry) => entry.campaign_id === hint.campaign_id);
      const session = campaign
        ? (campaign.sessions || []).find((entry) => entry.session_id === hint.session_id)
        : !hint.campaign_id && state.builderChats.find(
          (entry) => entry.session && entry.session.session_id === hint.session_id)?.session;
      if (!session || !['builder', 'dm'].includes(session.role)) continue;
      const previous = ensureSessionActivity({
        sessionId: session.session_id, sessionRole: session.role, campaignId: campaign?.campaign_id || null,
      });
      if (activityIsWorking(previous)) continue;
      // Restore all identities before saving the combined set or awaiting a single reconnect.
      const activity = {
        ...previous, attemptId: hint.attempt_id, phase: 'working', restored: true,
        contextCompaction: Boolean(hint.context_compaction),
        acknowledgedSequence: Number(hint.acknowledged_sequence) || 0,
        attachmentGeneration: hint.attachment_generation ?? null,
      };
      state.sessionActivities.set(activity.sessionId, activity);
      restored.push(activity);
    }
    persistDisconnectedActivities();
    await Promise.all(restored.map(async (activity) => {
      await reconnect(activity.attemptId, activity.acknowledgedSequence, activity.attachmentGeneration, activity);
      if (!currentActivity(activity) || !activityIsWorking(activity)) return;
      startStreamHeartbeat(activity, true);
      startProjectionPolling(activity);
    }));
    renderActivityIndexes();
  }

  async function reconnect(attemptId, acknowledgedSequence, attachmentGeneration, activity = selectedActivity()) {
    if (!state.bridge || !currentActivity(activity) || activity.attemptId !== attemptId) return null;
    try {
      const replay = activity.sessionRole === 'builder' || activity.contextCompaction
        ? await state.bridge.reconnect_provider_stream(activity.ownerId, attemptId, attachmentGeneration)
        : await state.bridge.reconnect_stream(activity.ownerId, attemptId, acknowledgedSequence || 0, attachmentGeneration);
      if (!currentActivity(activity) || !activityIsWorking(activity)) return null;
      activity.attachmentGeneration = replay.attachment_generation;
      persistDisconnectedActivities();
      selectedStatus(activity, 'Reconnected');
      return replay;
    } catch (e) {
      // A transient reconnect failure is not evidence that generation ended.
      if (!currentActivity(activity) || !activityIsWorking(activity)) return null;
      selectedStatus(activity, 'Reconnecting…');
      return null;
    }
  }

  // ---- Sidebar resize (host-extensible, user-draggable) ----
  // Navigation controls are host presentation; hiding them must not change any saved session.
  function renderSidebarState() {
    const campaign = state.view === 'session' && Boolean(state.activeCampaignId);
    $('campaign-sequel-btn').hidden = !campaign || state.activeSessionRole !== 'dm';
    $('builder-launcher').hidden = campaign && state.activeSessionRole === 'dm';
    $('session-context').hidden = !campaign;
    for (const side of ['left', 'right']) {
      const collapsed = state.sidebarCollapsed[side];
      const frame = $('session-frame');
      if (collapsed) frame.classList.add(`${side}-sidebar-collapsed`);
      else frame.classList.remove(`${side}-sidebar-collapsed`);
      const button = $(`toggle-${side}-sidebar-btn`);
      const label = `${collapsed ? 'Expand' : 'Collapse'} ${side} sidebar`;
      button.setAttribute('aria-expanded', String(!collapsed));
      button.setAttribute('aria-label', label);
      button.title = label;
    }
  }

  function setSidebarCollapsed(side, collapsed) {
    if (!['left', 'right'].includes(side)) return;
    state.sidebarCollapsed[side] = collapsed === true;
    try { localStorage.setItem('sylvia-gm-sidebar-visibility', JSON.stringify(state.sidebarCollapsed)); }
    catch (_) { /* Keep the controls usable when local preferences cannot be saved. */ }
    renderSidebarState();
  }

  function sidebarMaximumWidth() {
    return Math.max(260, Math.min(Math.floor(window.innerWidth * 0.65), window.innerWidth - 560));
  }

  // The separator and chat-side toggle track the actual sidebar without occupying layout space.
  function setSidebarWidth(width) {
    const pixels = Math.max(220, Math.min(sidebarMaximumWidth(), width));
    $('right-sidebar').style.width = `${pixels}px`;
    $('session-frame').style.setProperty('--sidebar-width', `${pixels}px`);
    $('sidebar-resize-handle').setAttribute('aria-valuenow', String(Math.round(pixels)));
    $('sidebar-resize-handle').setAttribute('aria-valuemax', String(sidebarMaximumWidth()));
  }

  function applySidebarWidth() {
    const sidebar = $('right-sidebar');
    if (!sidebar) return;
    const saved = panelLayout().sidebar_width;
    const defaultWidth = state.panelProjection?.surfaces.some((surface) => ['tactical_grid', 'coordinate_map'].includes(surface.renderer)) ? Math.max(480, Math.round(window.innerWidth * 0.5)) : 280;
    const width = saved == null ? defaultWidth : Number(saved);
    setSidebarWidth(Number.isFinite(width) ? width : defaultWidth);
  }

  // Keep the footer anchored while the message box grows upward, without touching its draft.
  function initComposerResize() {
    const handle = $('composer-resize-handle');
    const input = $('player-input');
    if (!handle || !input) return;
    let drag = null;
    const maximum = () => {
      const column = input.closest('.chronology-col');
      const footer = input.closest('.input-composer').querySelector('.composer-footer');
      const feedback = $('session-status-text');
      const available = (column?.clientHeight || window.innerHeight) -
        (column?.querySelector('.session-topbar')?.offsetHeight || 48) -
        footer.offsetHeight - feedback.offsetHeight - ($('dictation-feedback')?.offsetHeight || 0) - 160;
      return Math.max(58, Math.min(200, available));
    };
    const resize = (requested) => {
      const limit = maximum();
      const height = Math.round(Math.max(58, Math.min(limit, requested)));
      input.style.height = `${height}px`;
      handle.setAttribute('aria-valuemax', String(Math.floor(limit)));
      handle.setAttribute('aria-valuenow', String(height));
      handle.setAttribute('aria-valuetext', `${height} pixels high`);
    };
    const finish = (event) => {
      if (!drag || (event?.pointerId != null && event.pointerId !== drag.pointerId)) return;
      const previous = drag;
      drag = null;
      handle.classList.remove('active');
      document.body.style.userSelect = previous.userSelect;
      if (handle.hasPointerCapture(previous.pointerId)) handle.releasePointerCapture(previous.pointerId);
    };
    handle.addEventListener('pointerdown', (event) => {
      if (event.button !== 0 || drag) return;
      event.preventDefault();
      drag = { pointerId: event.pointerId, y: event.clientY, height: input.getBoundingClientRect().height,
        userSelect: document.body.style.userSelect };
      document.body.style.userSelect = 'none';
      handle.classList.add('active');
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener('pointermove', (event) => {
      if (drag?.pointerId === event.pointerId) resize(drag.height + drag.y - event.clientY);
    });
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
    handle.addEventListener('lostpointercapture', finish);
    window.addEventListener('blur', () => finish());
    handle.addEventListener('keydown', (event) => {
      if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const height = input.getBoundingClientRect().height;
      resize(event.key === 'Home' ? 58 : event.key === 'End' ? maximum() :
        height + (event.key === 'ArrowUp' ? 16 : -16));
    });
    window.addEventListener('resize', () => resize(input.getBoundingClientRect().height));
    resize(input.getBoundingClientRect().height || 58);
  }

  function initSidebarResize() {
    try {
      const saved = JSON.parse(localStorage.getItem('sylvia-gm-sidebar-visibility') || '{}');
      state.sidebarCollapsed = { left: saved?.left === true, right: saved?.right === true };
    } catch (_) { state.sidebarCollapsed = { left: false, right: false }; }
    renderSidebarState();
    let dragInfo = null;
    const handle = $('sidebar-resize-handle');
    const sidebar = $('right-sidebar');
    if (!handle || !sidebar) return;
    applySidebarWidth();
    window.addEventListener('resize', applySidebarWidth);
    handle.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      setSidebarWidth(sidebar.getBoundingClientRect().width + (event.key === 'ArrowLeft' ? 16 : -16));
      savePanelLayout({ sidebar_width: Math.round(sidebar.getBoundingClientRect().width) });
    });
    document.addEventListener('mousedown', (e) => {
      if (e.target !== handle || state.sidebarCollapsed.right) return;
      e.preventDefault();
      document.body.style.userSelect = 'none';
      handle.classList.add('active');
      dragInfo = { startX: e.clientX, startW: sidebar.getBoundingClientRect().width };
    });
    document.addEventListener('mousemove', (e) => {
      if (!dragInfo) return;
      const delta = e.clientX - dragInfo.startX;
      const newW = Math.max(220, Math.min(sidebarMaximumWidth(), dragInfo.startW - delta));
      setSidebarWidth(newW);
    });
    document.addEventListener('mouseup', () => {
      if (!dragInfo) return;
      savePanelLayout({ sidebar_width: Math.round(sidebar.getBoundingClientRect().width) });
      handle.classList.remove('active');
      document.body.style.userSelect = '';
      dragInfo = null;
    });
  }

  // ---- HTML escape (prevent injection from projection text) ----
  function escapeHtml(text) {
    if (text == null) return '';
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Split table cells while preserving escaped pipes and inline-code content.
  function splitNarrationTableRow(value) {
    const cells = [];
    let cell = '';
    let delimiterCount = 0;
    let inCode = false;
    const line = String(value || '');
    for (let index = 0; index < line.length; index += 1) {
      const character = line[index];
      if (character === '\\' && line[index + 1] === '|') {
        cell += '|';
        index += 1;
      } else if (character === '`') {
        inCode = !inCode;
        cell += character;
      } else if (character === '|' && !inCode) {
        cells.push(cell.trim());
        cell = '';
        delimiterCount += 1;
      } else {
        cell += character;
      }
    }
    cells.push(cell.trim());
    if (!delimiterCount) return null;
    if (cells[0] === '') cells.shift();
    if (cells[cells.length - 1] === '') cells.pop();
    return cells.length >= 2 ? cells : null;
  }

  function isNarrationTableSeparator(cells) {
    return Array.isArray(cells) && cells.length >= 2 &&
      cells.every((cell) => /^:?-{3,}:?$/.test(cell));
  }

  // Render the bounded, player-facing Markdown subset used by Builder and GM TALK.
  // Raw HTML is escaped and links are admitted only for explicitly safe schemes.
  function renderNarrationMarkdown(value) {
    const lines = String(value || '').replace(/\r\n?/g, '\n').split('\n');
    const rendered = [];
    let paragraph = [];
    let listKind = null;
    let listItems = [];
    let quoteLines = [];
    let codeFence = null;
    let codeLines = [];

    const flushParagraph = () => {
      if (!paragraph.length) return;
      rendered.push(`<p>${paragraph.map(renderNarrationInline).join('<br>')}</p>`);
      paragraph = [];
    };
    const flushList = () => {
      if (!listKind) return;
      rendered.push(`<${listKind}>${listItems.map((item) =>
        `<li>${renderNarrationInline(item)}</li>`
      ).join('')}</${listKind}>`);
      listKind = null;
      listItems = [];
    };
    const flushQuote = () => {
      if (!quoteLines.length) return;
      rendered.push(`<blockquote>${quoteLines.map(renderNarrationInline).join('<br>')}</blockquote>`);
      quoteLines = [];
    };
    const flushProse = () => {
      flushParagraph();
      flushList();
      flushQuote();
    };
    const flushCode = () => {
      const language = String(codeFence || '').match(/^[A-Za-z0-9_-]+$/)
        ? ` class="language-${escapeHtml(codeFence)}"`
        : '';
      rendered.push(`<pre><code${language}>${escapeHtml(codeLines.join('\n'))}</code></pre>`);
      codeFence = null;
      codeLines = [];
    };

    for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
      const line = lines[lineIndex];
      const fence = line.match(/^\s*```\s*([A-Za-z0-9_-]*)\s*$/);
      if (codeFence !== null) {
        if (fence) flushCode();
        else codeLines.push(line);
        continue;
      }
      if (fence) {
        flushProse();
        codeFence = fence[1] || '';
        continue;
      }
      if (!line.trim()) {
        flushProse();
        continue;
      }
      const headerCells = splitNarrationTableRow(line);
      const separatorCells = splitNarrationTableRow(lines[lineIndex + 1]);
      if (headerCells && separatorCells && headerCells.length === separatorCells.length &&
          isNarrationTableSeparator(separatorCells)) {
        flushProse();
        const rows = [];
        lineIndex += 2;
        while (lineIndex < lines.length) {
          const cells = splitNarrationTableRow(lines[lineIndex]);
          if (!cells || cells.length !== headerCells.length) break;
          rows.push(cells);
          lineIndex += 1;
        }
        lineIndex -= 1;
        const head = headerCells.map((cell) =>
          `<th scope="col">${renderNarrationInline(cell)}</th>`
        ).join('');
        const body = rows.length
          ? `<tbody>${rows.map((cells) => `<tr>${cells.map((cell) =>
            `<td>${renderNarrationInline(cell)}</td>`
          ).join('')}</tr>`).join('')}</tbody>`
          : '';
        rendered.push(`<div class="markdown-table-wrap"><table><thead><tr>${head}</tr></thead>${body}</table></div>`);
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        flushProse();
        const level = heading[1].length;
        rendered.push(`<h${level}>${renderNarrationInline(heading[2])}</h${level}>`);
        continue;
      }
      if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        flushProse();
        rendered.push('<hr>');
        continue;
      }
      const quote = line.match(/^\s*>\s?(.*)$/);
      if (quote) {
        flushParagraph();
        flushList();
        quoteLines.push(quote[1]);
        continue;
      }
      const unordered = line.match(/^\s*[-+*]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (unordered || ordered) {
        flushParagraph();
        flushQuote();
        const nextKind = unordered ? 'ul' : 'ol';
        if (listKind && listKind !== nextKind) flushList();
        listKind = nextKind;
        listItems.push((unordered || ordered)[1]);
        continue;
      }
      flushList();
      flushQuote();
      paragraph.push(line);
    }
    if (codeFence !== null) flushCode();
    flushProse();
    return rendered.join('');
  }

  function renderNarrationInline(value) {
    const protectedValues = [];
    const protect = (html) => {
      const marker = `\u0000${protectedValues.length}\u0000`;
      protectedValues.push(html);
      return marker;
    };
    let text = String(value || '').replace(/\u0000/g, '');
    text = text.replace(/`([^`\n]+)`/g, (_match, code) =>
      protect(`<code>${escapeHtml(code)}</code>`)
    );
    text = text.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_match, label, target) => {
      const safeTarget = safeNarrationUrl(target);
      if (!safeTarget) return protect(escapeHtml(label));
      return protect(`<a href="${escapeHtml(safeTarget)}" target="_blank" ` +
        `rel="noopener noreferrer">${escapeHtml(label)}</a>`);
    });
    text = text
      .replace(/\$\\rightarrow\$/g, '→')
      .replace(/\$\\leftarrow\$/g, '←')
      .replace(/\$\\leftrightarrow\$/g, '↔')
      .replace(/\\rightarrow\b/g, '→')
      .replace(/\\leftarrow\b/g, '←')
      .replace(/\\leftrightarrow\b/g, '↔');
    text = escapeHtml(text)
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/__([^_\n]+)__/g, '<strong>$1</strong>')
      .replace(/\*([^*\n]+)\*/g, '<em>$1</em>')
      .replace(/(^|[^\w])_([^_\n]+)_($|[^\w])/g, '$1<em>$2</em>$3');
    return text.replace(/\u0000(\d+)\u0000/g, (_match, index) =>
      protectedValues[Number(index)] || ''
    );
  }

  function safeNarrationUrl(value) {
    const target = String(value || '').trim();
    if (/^#[A-Za-z0-9_-]+$/.test(target)) return target;
    try {
      const parsed = new URL(target);
      return ['http:', 'https:', 'mailto:'].includes(parsed.protocol) ? target : '';
    } catch (_error) {
      return '';
    }
  }

  function narrationProjectionIdentity(projection) {
    const kind = String(projection && projection.kind || '');
    const attemptId = projection && projection.attempt_id;
    if (attemptId && ['talk', 'think'].includes(kind)) {
      return `narration-${attemptId}-${kind}`;
    }
    if (projection && projection.sequence != null) return `sequence-${projection.sequence}`;
    const payload = projection && projection.safe_payload || {};
    return `${kind}-${payload.action_id || projection && projection.action_id ||
      payload.interruption_id || ''}`;
  }

  function friendlyCommandError(fallback, error) {
    const detail = String(error || '').replace(/^Error:\s*/, '').trim();
    if (!detail || detail === '[object Object]') return fallback;
    return `${fallback}: ${detail}`;
  }

  // ---- Public API ----
  return {
    init,
    setTheme,
    openCampaign,
    createNoRulesCampaign,
    submitInput,
    stop,
    retry,
    reconnect,
    startProjectionPolling,
    stopProjectionPolling,
    renderPlayerProjection,
    refreshCampaignIndex,
    resolveCampaignCreationOptions,
    latestCampaignInterviewState,
    toolResultMarkup,
    groupedAgentActivity,
    withActMarkers,
    withCampaignEnding,
    renderNarrationMarkdown,
    renderResponseBlocks,
    narrationProjectionIdentity,
  };
})();

// Expose for inline onclick handlers in app.html
window.SylviaGMFrontend = SylviaGMFrontend;

document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });
