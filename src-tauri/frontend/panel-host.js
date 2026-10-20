/* Saved game-panel arrangements and native window presentation. Game actions stay with main. */
(() => {
  'use strict';

  function create({ state, renderPanel, render, dispatch, selection, busy, status, visible = () => true }) {
    const store = window.SylviaPanelWorkspace.createStore(localStorage);
    const effects = window.SylviaShotPlayback.create({ storage: localStorage });
    const child = window.__sylviaPanelContext || null;
    const pending = new Map();
    let context = child?.context || null;
    let layout = null;
    let nativeQueue = Promise.resolve();
    let registeredContext = null;
    let initialized = false;
    let restoreWindows = false;
    let pendingEffects = null;
    let presentationSequence = 0;
    let workspaceSequence = 0;
    let playbackSequence = 0;
    let presentationMarker = null;
    let readinessTimer = null;
    let childPresentationSequence = -1;
    let childPresentationMarker = null;
    let childPrepared = null;
    let childReady = null;
    let childStart = null;
    let loadingLayout = false;
    let activeDrag = null;
    let dropPreview = null;
    const EFFECTS_READY_TIMEOUT_MS = 2000;
    const escape = (value) => String(value ?? '').replace(/[&<>"']/g,
      (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
    const sameContext = (value) => context && value &&
      value.generation === context.generation && value.campaign_id === context.campaign_id &&
      value.session_id === context.session_id;
    const native = (command, args) => window.__TAURI__.core.invoke(command, args);
    const panelIds = (tree) => !tree ? [] : tree.kind === 'tabs' ? tree.panels :
      [...panelIds(tree.first), ...panelIds(tree.second)];
    const ownerTree = (windowId) => windowId === 'sidebar' ? layout?.sidebar :
      layout?.windows.find((item) => item.id === windowId)?.tree;
    const owns = (windowId, panelId) => panelIds(ownerTree(windowId)).includes(panelId);

    function clearReadiness() {
      if (readinessTimer !== null) clearTimeout(readinessTimer);
      readinessTimer = null;
    }

    function receiveEffects(packet) {
      const currentRevision = effects.snapshot().revision;
      if (!packet || packet.revision < currentRevision) return false;
      return effects.receive(packet);
    }

    const childIsReady = () => !childPrepared || (childReady &&
      childReady.presentation_sequence === childPrepared.presentation_sequence &&
      childReady.playback_sequence === childPrepared.playback_sequence);

    // Only small presentation state is compared; the projection itself advances at render boundaries.
    function presentationKey(value) {
      const preferences = value.effects?.preferences || effects.preferences();
      return JSON.stringify([value.layout, value.busy, value.projection?.presentation_key, value.theme, value.tacticalSelections,
        value.networkSelections, value.crossSurfaceFocus, value.characterInspection, preferences]);
    }

    function workspacePayload() {
      if (!child && layout && state.characterInspection && !state.panelProjection?.loading) {
        layout = store.change({ type: 'inspection', value: state.characterInspection });
      }
      return {
        projection: { ...state.panelProjection, shot_effects: [] }, layout,
        effects: effects.snapshot(), busy: busy(), theme: state.selectedTheme,
        tacticalSelections: state.tacticalSelections, networkSelections: state.networkSelections,
        crossSurfaceFocus: state.crossSurfaceFocus,
        characterInspection: state.characterInspection,
      };
    }

    function childEffectOwners(records) {
      const mapIds = new Set(records.flatMap((record) => Array.isArray(record?.shots) ? record.shots : [])
        .map((shot) => shot?.map_id).filter((id) => typeof id === 'string' && id));
      const surfaces = state.panelProjection?.surfaces || [];
      const effectPanels = new Set(surfaces.filter((surface) =>
        ['tactical_grid', 'coordinate_map'].includes(surface.renderer) && mapIds.has(surface.data?.map?.id)
      ).map((surface) => surface.id));
      const renderedPanels = (tree) => {
        if (!tree) return [];
        if (tree.kind === 'split') return [...renderedPanels(tree.first), ...renderedPanels(tree.second)];
        const available = tree.panels.filter((id) => {
          const surface = surfaces.find((item) => item.id === id);
          return surface && visible(surface);
        });
        return available.length ? [available.includes(tree.active) ? tree.active : available[0]] : [];
      };
      return new Set((layout?.windows || []).filter((item) =>
        renderedPanels(item.tree).some((panelId) => effectPanels.has(panelId))
      ).map((item) => item.id));
    }

    function startPendingEffects(timedOut = false) {
      const pendingEffect = pendingEffects;
      if (!pendingEffect || !sameContext(pendingEffect.context) ||
          (!timedOut && Array.from(pendingEffect.required).some((id) => !pendingEffect.ready.has(id)))) return;
      pendingEffects = null;
      clearReadiness();
      if (effects.ingest(pendingEffect.context.generation, pendingEffect.revision, pendingEffect.records)) {
        pendingEffect.start();
        void publish({ effectsOnly: {
          presentation_sequence: pendingEffect.workspace,
          playback_sequence: pendingEffect.playback,
        } }).catch(status);
      }
      if (timedOut) status('A panel window was not ready for shot effects.');
    }

    function armReadiness(pendingEffect) {
      if (readinessTimer !== null || pendingEffects !== pendingEffect) return;
      readinessTimer = setTimeout(() => {
        readinessTimer = null;
        if (pendingEffects === pendingEffect) startPendingEffects(true);
      }, EFFECTS_READY_TIMEOUT_MS);
    }

    async function preparePendingEffects(pendingEffect) {
      if (pendingEffects !== pendingEffect || !sameContext(pendingEffect.context)) return;
      const currentRevision = effects.snapshot().revision;
      const freshRecords = pendingEffect.records.filter((record) =>
        Number.isSafeInteger(record?.revision) && record.revision > currentRevision);
      pendingEffect.playback = ++playbackSequence;
      pendingEffect.workspace = workspaceSequence;
      pendingEffect.required = childEffectOwners(freshRecords);
      pendingEffect.ready = new Set();
      if (!pendingEffect.required.size) {
        startPendingEffects();
        return;
      }
      armReadiness(pendingEffect);
      await publish();
    }

    // Serialize navigation and window mutations so a late native reply cannot restore an old layout.
    function sync() {
      if (child || !initialized || !window.__TAURI__) return;
      const captured = context && { ...context };
      const restoreLayout = restoreWindows;
      restoreWindows = false;
      nativeQueue = nativeQueue.catch(() => {}).then(async () => {
        if (captured ? !sameContext(captured) : context !== null) return;
        const windows = layout?.windows.map(({ id, title, placement }) => ({ id, title, placement })) || [];
        const result = await native('sync_panel_windows', { context: captured, windows: captured ? windows : [],
          restoreLayout });
        registeredContext = captured && { ...captured };
        if (sameContext(captured) && result?.failed_window_ids?.length) {
          for (const windowId of result.failed_window_ids)
            layout = store.change({ type: 'close-window', windowId });
          render();
          status('A panel window could not be opened. Its panels have been returned to the sidebar.');
          return;
        }
        if (sameContext(captured)) await publishRegistered();
      }).catch((error) => status(`Panel windows could not be updated: ${error}`));
    }

    function leave() {
      if (child || !context) return;
      cancelDrag();
      document.querySelectorAll?.('dialog.player-action-dialog').forEach((dialog) => {
        if (dialog.open) dialog.close('cancel');
      });
      context = null;
      clearReadiness();
      pendingEffects = null;
      presentationSequence += 1;
      layout = null;
      loadingLayout = false;
      restoreWindows = false;
      effects.leave();
      state.tacticalSelections = {};
      state.networkSelections = {};
      state.tacticalViews = {};
      state.tacticalViewScenes = {};
      state.panelTabs = {};
      state.crossSurfaceFocus = null;
      state.characterInspection = null;
      sync();
    }

    // Loading windows carry only saved layout identifiers, never stale game data or old effects.
    function loading(campaignId, sessionId, phase = 'loading') {
      if (child) return null;
      if (context?.campaign_id !== campaignId || context?.session_id !== sessionId) {
        leave();
        context = { campaign_id: campaignId, session_id: sessionId, generation: crypto.randomUUID() };
        layout = store.restore(campaignId);
        effects.enter(context.generation, 0);
      }
      loadingLayout = true;
      restoreWindows = true;
      return { campaign_id: campaignId, loading: phase, surfaces: (layout?.known || []).map((id) =>
        ({ id, title: 'Game panel', loading: phase, actions: [] })), shot_effects: [] };
    }

    function activate(projection) {
      if (child) return layout;
      if (!projection || !state.activeSessionId || state.activeSessionRole !== 'dm') {
        leave();
        return null;
      }
      if (projection.loading) {
        sync();
        return layout;
      }
      if (context?.campaign_id !== projection.campaign_id || context?.session_id !== state.activeSessionId) {
        leave();
        context = { campaign_id: projection.campaign_id, session_id: state.activeSessionId,
          generation: crypto.randomUUID() };
        effects.enter(context.generation, projection.revision);
      }
      if (loadingLayout) {
        effects.enter(context.generation, projection.revision);
        loadingLayout = false;
      }
      layout = store.select({ campaignId: projection.campaign_id,
        rulesetId: projection.ruleset_id || 'no-rules', surfaces: projection.surfaces,
        original: projection.layout || null });
      state.characterInspection ||= layout.inspection;
      state.tacticalViewScenes ||= {};
      projection.surfaces.filter((surface) =>
        ['tactical_grid', 'coordinate_map'].includes(surface.renderer) && surface.data?.map
      ).forEach((surface) => {
        const scene = `${surface.id}:${surface.data.map.id || surface.id}`;
        if (state.tacticalViewScenes[surface.id] !== scene) {
          state.tacticalViews[surface.id] = { zoom: 1, scrollLeft: 0, scrollTop: 0,
            coverInfo: false, selectedGeometry: null, ...store.view(scene),
            initialFocus: !Object.hasOwn(store.view(scene) || {}, 'scrollLeft') };
          delete state.tacticalSelections[surface.id];
          state.tacticalViewScenes[surface.id] = scene;
        }
      });
      sync();
      return layout;
    }

    // Start fresh effects after replacement panels have had a paint opportunity.
    function presentEffects(projection, start) {
      if (projection?.loading) return false;
      if (child) {
        childStart = start;
        return false;
      }
      if (!context || !projection || state.activeSessionRole !== 'dm' ||
          context.campaign_id !== projection.campaign_id ||
          context.session_id !== state.activeSessionId) return false;
      const captured = { ...context };
      const records = Array.isArray(projection.shot_effects) ? projection.shot_effects : [];
      if (pendingEffects && sameContext(pendingEffects.context)) {
        const actions = new Map(pendingEffects.records.map((record) => [record?.action_id, record]));
        records.forEach((record) => actions.set(record?.action_id, record));
        pendingEffects = {
          context: captured,
          revision: Math.max(pendingEffects.revision, projection.revision),
          records: Array.from(actions.values()),
          start,
        };
      } else {
        pendingEffects = { context: captured, revision: projection.revision, records, start };
      }
      clearReadiness();
      workspaceSequence += 1;
      presentationMarker = presentationKey(workspacePayload());
      const sequence = ++presentationSequence;
      const afterFrame = () => {
        if (sequence !== presentationSequence || !pendingEffects || !sameContext(captured)) return;
        window.requestAnimationFrame(() => {
          if (sequence !== presentationSequence || !pendingEffects || !sameContext(captured)) return;
          const pendingEffect = pendingEffects;
          void preparePendingEffects(pendingEffect).catch(status);
        });
      };
      window.requestAnimationFrame(afterFrame);
      return true;
    }

    async function publishRegistered({ effectsOnly = null } = {}) {
      if (child || !context || !initialized || !window.__TAURI__ || !layout) return;
      if (effectsOnly) {
        await native('publish_panel_workspace', { context, payload: {
          effects_update: effectsOnly, effects: effects.snapshot(),
        } });
        return;
      }
      const payload = workspacePayload();
      const marker = presentationKey(payload);
      if (presentationMarker !== marker) {
        workspaceSequence += 1;
        presentationMarker = marker;
        if (pendingEffects?.playback) {
          clearReadiness();
          pendingEffects.playback = ++playbackSequence;
          pendingEffects.workspace = workspaceSequence;
          pendingEffects.required = childEffectOwners(pendingEffects.records);
          pendingEffects.ready = new Set();
        }
      }
      payload.presentation_sequence = workspaceSequence;
      if (pendingEffects?.playback) payload.effects_prepare = {
        presentation_sequence: pendingEffects.workspace,
        playback_sequence: pendingEffects.playback,
      };
      if (pendingEffects?.playback) armReadiness(pendingEffects);
      await native('publish_panel_workspace', { context, payload });
    }

    // A new presentation context must exist in the native registry before it can receive a publish.
    async function publish(options = {}) {
      if (child || !context || !initialized || !window.__TAURI__ || !layout) return;
      const captured = { ...context };
      if (!sameContext(registeredContext)) await nativeQueue.catch(() => {});
      if (!sameContext(captured) || !sameContext(registeredContext)) return;
      await publishRegistered(options);
    }

    function changed(command) {
      if (child) return request('layout', command);
      if (!context) return;
      if (loadingLayout && !['placement', 'close-window'].includes(command?.type)) return;
      layout = store.change(command);
      render();
    }

    function configureEffects(patch) {
      if (child) return request('effects-configure', patch);
      effects.configure(patch);
      render();
    }

    function skipEffects() {
      if (child) return request('effects-skip', {});
      presentationSequence += 1;
      clearReadiness();
      if (pendingEffects && sameContext(pendingEffects.context)) {
        effects.ingest(context.generation, pendingEffects.revision,
          pendingEffects.records.map((record) => ({ ...record, shots: [] })));
      }
      pendingEffects = null;
      effects.skip();
      void publish().catch(status);
    }

    function saveView(surface, view) {
      const mapId = `${surface.id}:${surface.data.map.id || surface.id}`;
      const safe = { zoom: view.zoom, scrollLeft: view.scrollLeft, scrollTop: view.scrollTop,
        coverInfo: view.coverInfo, selectedGeometry: view.selectedGeometry };
      if (child) {
        void request('view', { surfaceId: surface.id, mapId, view: safe }).catch(status);
      } else if (context) {
        store.saveView(mapId, safe);
        layout = store.snapshot();
      }
    }

    function choose(surfaceId, patch) {
      if (child) {
        void request('selection', { surfaceId, patch }).catch(status);
      } else {
        selection(surfaceId, patch);
        void publish().catch(status);
      }
    }

    function reveal(surfaceId) {
      if (child) return request('reveal', { surfaceId, focus: state.crossSurfaceFocus });
      if (!context || !state.panelProjection.surfaces.some((item) => item.id === surfaceId)) return;
      const find = (tree) => !tree ? null : tree.kind === 'tabs'
        ? tree.panels.includes(surfaceId) ? tree.id : null : find(tree.first) || find(tree.second);
      if (layout.hidden.includes(surfaceId)) layout = store.change({ type: 'show', panelId: surfaceId });
      const windowId = layout.windows.find((item) => owns(item.id, surfaceId))?.id || 'sidebar';
      const groupId = find(ownerTree(windowId));
      if (groupId) changed({ type: 'tab', groupId, panelId: surfaceId });
      const captured = { ...context };
      if (initialized && window.__TAURI__) void nativeQueue.then(() => {
        if (sameContext(captured)) return native('focus_panel_window', { context: captured, windowId });
      }).catch(status);
    }

    function request(method, args) {
      if (!child) throw new Error('Only panel windows forward requests.');
      const requestId = crypto.randomUUID();
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(requestId);
          reject(new Error('The main window did not reply. Check its current campaign before trying again.'));
        }, 60000);
        pending.set(requestId, { resolve, reject, timer });
        native('panel_window_message', { context,
          payload: { type: 'rpc', request_id: requestId, method, args } }).catch((error) => {
          clearTimeout(timer);
          pending.delete(requestId);
          reject(error);
        });
      });
    }

    async function handleMessage(message) {
      if (!sameContext(message.context)) return;
      const payload = message.payload;
      if (payload.type === 'ready') { await publish(); return; }
      if (payload.type === 'effects-ready') {
        const pendingEffect = pendingEffects;
        if (pendingEffect && pendingEffect.workspace === payload.presentation_sequence &&
            pendingEffect.playback === payload.playback_sequence &&
            pendingEffect.required.has(message.window_id) &&
            childEffectOwners(pendingEffect.records).has(message.window_id)) {
          pendingEffect.ready.add(message.window_id);
          startPendingEffects();
        }
        return;
      }
      if (payload.type !== 'rpc' || typeof payload.request_id !== 'string') return;
      let result = null, error = null;
      try {
        if (!layout?.windows.some((item) => item.id === message.window_id)) {
          throw new Error('This panel window is no longer active.');
        }
        const args = payload.args;
        if (payload.method === 'effects-configure') {
          configureEffects(payload.args);
        } else if (payload.method === 'effects-skip') {
          skipEffects();
        } else if (payload.method === 'layout') {
          // A drop may originate in a different window of this same active workspace.
          changed(args);
        } else if (payload.method === 'reveal') {
          const physical = state.panelProjection.surfaces.find((item) => item.id === args.focus?.surfaceId);
          if (['tactical_grid', 'coordinate_map'].includes(physical?.renderer) &&
              physical.data.entities.some((item) => item.id === args.focus.entityId)) {
            state.crossSurfaceFocus = { surfaceId: physical.id, entityId: args.focus.entityId };
          }
          reveal(args.surfaceId);
        } else if (payload.method === 'selection' || payload.method === 'view') {
          if (!owns(message.window_id, args.surfaceId)) throw new Error('This panel has moved.');
          const surface = state.panelProjection.surfaces.find((item) => item.id === args.surfaceId);
          if (!surface) throw new Error('The panel is unavailable.');
          if (payload.method === 'view') {
            if (!['tactical_grid', 'coordinate_map'].includes(surface.renderer)) throw new Error('This panel has no map view.');
            saveView(surface, args.view);
          } else {
            selection(args.surfaceId, args.patch);
            render();
          }
        } else {
          result = await dispatch(payload.method, args, {
            ...context, owns: (id) => owns(message.window_id, id),
            current: () => sameContext(message.context),
          });
        }
      } catch (failure) { error = String(failure?.message || failure); }
      if (sameContext(message.context)) {
        await native('panel_window_reply', { context, windowId: message.window_id,
          requestId: payload.request_id, result, error });
      }
    }

    async function init() {
      if (initialized || !window.__TAURI__) return;
      initialized = true;
      const listen = window.__TAURI__.event.listen;
      if (child) {
        await listen('panel-window-reply', ({ payload }) => {
          if (!sameContext(payload.context)) return;
          const waiter = pending.get(payload.request_id);
          if (!waiter) return;
          clearTimeout(waiter.timer);
          pending.delete(payload.request_id);
          if (payload.error) waiter.reject(new Error(payload.error));
          else waiter.resolve(payload.result);
        });
        await listen('panel-workspace', ({ payload }) => {
          if (!sameContext(payload.context) || payload.window_id !== child.window_id) return;
          const value = payload.payload;
          if (value.effects_update) {
            if (childReady && value.effects_update.presentation_sequence === childPresentationSequence &&
                value.effects_update.presentation_sequence === childReady.presentation_sequence &&
                value.effects_update.playback_sequence === childReady.playback_sequence && value.effects) {
              receiveEffects(value.effects);
              childStart?.();
            }
            return;
          }
          if (!Number.isSafeInteger(value.presentation_sequence) ||
              value.presentation_sequence < childPresentationSequence) return;
          const marker = presentationKey(value);
          const equivalent = value.presentation_sequence === childPresentationSequence &&
            marker === childPresentationMarker;
          const advancesEffects = value.effects && value.effects.revision > effects.snapshot().revision;
          layout = value.layout;
          if (value.effects && (!advancesEffects || (equivalent && childIsReady()))) {
            receiveEffects(value.effects);
          }
          state.activeCampaignId = context.campaign_id;
          state.activeSessionId = context.session_id;
          state.activeSessionRole = 'dm';
          state.view = 'session';
          state.activeSnapshot = value.projection.loading ? null : { revision: value.projection.revision };
          state.panelWindowBusy = value.busy;
          state.tacticalSelections = value.tacticalSelections || {};
          state.networkSelections = value.networkSelections || {};
          state.crossSurfaceFocus = value.crossSurfaceFocus;
          state.characterInspection = value.characterInspection || null;
          state.selectedTheme = value.theme;
          document.body.setAttribute('data-theme', value.theme);
          status(value.busy ? 'Sylvia is working…' : '');
          state.tacticalViewScenes ||= {};
          value.projection.surfaces.filter((surface) => ['tactical_grid', 'coordinate_map'].includes(surface.renderer)).forEach((surface) => {
            const scene = `${surface.id}:${surface.data.map.id || surface.id}`;
            if (state.tacticalViewScenes[surface.id] !== scene) {
              state.tacticalViews[surface.id] = { zoom: 1, scrollLeft: 0, scrollTop: 0,
                coverInfo: false, selectedGeometry: null, ...layout.views?.[scene],
                initialFocus: !Object.hasOwn(layout.views?.[scene] || {}, 'scrollLeft') };
              state.tacticalViewScenes[surface.id] = scene;
            }
          });
          if (equivalent) childStart?.();
          else {
            childPresentationSequence = value.presentation_sequence;
            childPresentationMarker = marker;
            render(value.projection);
            if (advancesEffects && !value.effects_prepare) {
              receiveEffects(value.effects);
              childStart?.();
            }
          }
          const preparation = value.effects_prepare;
          if (preparation?.presentation_sequence === childPresentationSequence &&
              Number.isSafeInteger(preparation.playback_sequence)) {
            const prepared = {
              presentation_sequence: childPresentationSequence,
              playback_sequence: preparation.playback_sequence,
            };
            if (childPrepared?.presentation_sequence === prepared.presentation_sequence &&
                childPrepared?.playback_sequence === prepared.playback_sequence) return;
            childPrepared = prepared;
            window.requestAnimationFrame(() => {
              window.requestAnimationFrame(() => {
                if (childPrepared !== prepared || !sameContext(payload.context)) return;
                childReady = prepared;
                void native('panel_window_message', { context, payload: {
                  type: 'effects-ready', ...prepared,
                } }).catch(status);
              });
            });
          }
        });
        await native('panel_window_message', { context, payload: { type: 'ready' } });
      } else {
        await listen('panel-window-message', ({ payload }) => { void handleMessage(payload).catch(status); });
        await listen('panel-window-closed', ({ payload }) => {
          if (sameContext(payload.context)) changed({ type: 'close-window', windowId: payload.window_id });
        });
        await listen('panel-window-placement', ({ payload }) => {
          if (sameContext(payload.context)) {
            layout = store.change({ type: 'placement', windowId: payload.window_id, placement: payload.placement });
          }
        });
        sync();
      }
    }

    function treeMarkup(tree, surfaces) {
      if (!tree) return '';
      if (tree.kind === 'split') {
        const first = treeMarkup(tree.first, surfaces), second = treeMarkup(tree.second, surfaces);
        if (!first || !second) return first || second;
        const axis = tree.axis === 'row' ? 'row' : 'column';
        return `<div class="panel-split panel-split-${axis}" data-panel-split="${escape(tree.id)}" ` +
          `data-axis="${axis}" style="--panel-ratio:${tree.ratio}">` +
          `<div class="panel-split-child">${first}</div><div class="panel-split-handle" ` +
          `role="separator" tabindex="0" aria-label="Resize panel group" ` +
          `aria-orientation="${axis === 'row' ? 'vertical' : 'horizontal'}" ` +
          `aria-valuemin="10" aria-valuemax="90" aria-valuenow="${Math.round(tree.ratio * 100)}"></div>` +
          `<div class="panel-split-child">${second}</div></div>`;
      }
      const available = tree.panels.map((id) => surfaces.find((item) => item.id === id))
        .filter((surface) => surface && visible(surface));
      if (!available.length) return '';
      const selected = available.find((item) => item.id === tree.active) || available[0];
      const tabs = available.map((surface) => `<button type="button" class="ruleset-panel-tab" ` +
        `draggable="true" data-workspace-panel="${escape(surface.id)}" data-workspace-tab="${escape(tree.id)}" ` +
        `title="Drag ${escape(surface.title || surface.id)} to move it" ` +
        `aria-selected="${surface.id === selected.id}">${escape(surface.title || surface.id)}</button>`).join('');
      return `<section class="panel-tab-group" data-panel-drop="${escape(selected.id)}">` +
        `<div class="ruleset-panel-tabs">${tabs}<button type="button" class="panel-menu-button" ` +
        `data-panel-menu="${escape(selected.id)}" title="Arrange ${escape(selected.title || selected.id)}" ` +
        `aria-label="Arrange ${escape(selected.title || selected.id)}">⋯</button>` +
        `</div>${renderPanel(selected, false)}</section>`;
    }

    function markup(projection) {
      clearDropPreview();
      const current = activate(projection);
      if (projection?.loading) {
        const contents = current && treeMarkup(ownerTree(child?.window_id || 'sidebar'), projection.surfaces);
        return contents || `<p class="panel-empty" role="status">${projection.loading === 'error'
          ? 'Game panels could not be loaded. Retry loading in the campaign chat.' : 'Loading game panels…'}</p>`;
      }
      if (!current) return '';
      if (!current.known.length) return '';
      const tree = ownerTree(child?.window_id || 'sidebar');
      const controls = child ? '' : `<details class="panel-layout-controls"><summary>Arrange panels</summary>` +
        `<div class="panel-layout-options"><button type="button" data-panel-preference="save">Save as my Ruleset default</button>` +
        `<button type="button" data-panel-preference="mine">Use my Ruleset layout</button>` +
        `<button type="button" data-panel-preference="original">Use Ruleset's original layout</button>` +
        current.windows.map((item) => `<button type="button" data-panel-close-window="${escape(item.id)}">` +
          `${escape(item.title)}: return panels to sidebar</button>`).join('') +
        current.hidden.map((id) => {
          const surface = projection.surfaces.find((item) => item.id === id);
          return surface ? `<button type="button" data-panel-show="${escape(id)}">Show ${escape(surface.title || id)}</button>` : '';
        }).join('') + '</div></details>';
      const contents = treeMarkup(tree, projection.surfaces);
      const dropDestinations = `<div class="panel-workspace-drop-destinations" aria-label="Panel drop destinations">` +
        `<div class="panel-workspace-drop-destination" data-panel-workspace-destination="sidebar">Return to sidebar</div>` +
        `<div class="panel-workspace-drop-destination" data-panel-workspace-destination="new">Move to a new window</div></div>`;
      return controls + dropDestinations + `<div class="panel-workspace-tree">${contents || (child
        ? '<p class="panel-empty">No panels are available in the current game state.</p>' : '')}</div>`;
    }

    function menu(panelId) {
      const surfaces = state.panelProjection.surfaces;
      const surface = surfaces.find((item) => item.id === panelId);
      if (!surface) return;
      const menuContext = { ...context };
      const destinations = [
        ['Return to sidebar', { windowId: 'sidebar' }],
        ['Move to a new window', { windowId: 'new' }],
        ...layout.windows.map((item) => [`Move to ${item.title}`, { windowId: item.id }]),
      ];
      for (const item of surfaces) {
        if (item.id === panelId || layout.hidden.includes(item.id)) continue;
        const windowId = layout.windows.find((entry) => owns(entry.id, item.id))?.id || 'sidebar';
        for (const [position, label] of [['tab', 'Tab with'], ['left', 'Left of'], ['right', 'Right of'],
          ['above', 'Above'], ['below', 'Below']]) {
          destinations.push([`${label} ${item.title || item.id}`, { windowId, targetPanelId: item.id, position }]);
        }
      }
      const dialog = document.createElement('dialog');
      dialog.className = 'player-action-dialog';
      dialog.innerHTML = `<h2>Arrange ${escape(surface.title || panelId)}</h2>` +
        `<label>Move panel<select data-panel-destination>${destinations.map(([label], i) =>
          `<option value="${i}">${escape(label)}</option>`).join('')}</select></label>` +
        `<form method="dialog"><button class="btn btn-secondary" value="cancel">Cancel</button>` +
        `<button class="btn btn-secondary" value="hide">Hide panel</button>` +
        `<button class="btn btn-primary" value="move">Move</button></form>`;
      document.body.appendChild(dialog);
      dialog.addEventListener('close', () => {
        const destination = destinations[Number(dialog.querySelector('[data-panel-destination]').value)]?.[1];
        const choice = dialog.returnValue;
        dialog.remove();
        if (!sameContext(menuContext)) return;
        if (choice === 'hide') void changed({ type: 'hide', panelId });
        else if (choice === 'move' && destination) void changed({ type: 'move', panelId, destination });
      }, { once: true });
      dialog.showModal();
    }

    // Render a real panel preview for the browser's native drag image. It is visible while
    // captured, then removed on the next task; no duplicate panel receives events or state.
    function dragPreview(event, button, surface) {
      const rect = button.closest('.panel-tab-group').getBoundingClientRect();
      const preview = document.createElement('div');
      preview.className = 'panel-drag-preview';
      preview.setAttribute('aria-hidden', 'true');
      preview.style.width = `${Math.min(400, Math.max(220, rect.width))}px`;
      preview.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 420))}px`;
      preview.style.top = `${Math.max(8, Math.min(rect.top, window.innerHeight - 340))}px`;
      preview.innerHTML = `<div class="panel-drag-preview-title">${escape(surface.title || surface.id)}</div>` +
        renderPanel(surface, false);
      preview.querySelectorAll('[id]').forEach((element) => element.removeAttribute('id'));
      document.body.appendChild(preview);
      event.dataTransfer.setDragImage(preview, 32, 20);
      setTimeout(() => preview.remove(), 0);
      return { preview, width: rect.width, height: rect.height };
    }

    function cancelDrag() {
      clearDropPreview();
      if (activeDrag) void finishDrag(activeDrag, true);
    }

    // Preview and commit share one destination; corners follow the closest relative edge.
    function panelDropPosition(target, event) {
      const rect = target.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width;
      const y = (event.clientY - rect.top) / rect.height;
      const edges = [['above', y], ['below', 1 - y], ['left', x], ['right', 1 - x]];
      edges.sort((a, b) => a[1] - b[1]);
      return edges[0][1] < 0.2 ? edges[0][0] : 'tab';
    }

    function clearDropPreview() {
      dropPreview?.element.remove();
      dropPreview = null;
    }

    // A fixed, non-interactive overlay leaves both the grabbed tab and drop target in place.
    function showDropPreview(target, position) {
      if (dropPreview?.target !== target) {
        clearDropPreview();
        const element = document.createElement('div');
        element.className = 'panel-drop-preview';
        element.setAttribute('aria-hidden', 'true');
        const label = document.createElement('span');
        label.className = 'panel-drop-preview-label';
        element.appendChild(label);
        document.body.appendChild(element);
        dropPreview = { element, label, target, position };
      }
      const rect = target.getBoundingClientRect();
      let { left, top, width, height } = rect;
      if (position === 'left' || position === 'right') {
        width /= 2;
        if (position === 'right') left += width;
      } else if (position === 'above' || position === 'below') {
        height /= 2;
        if (position === 'below') top += height;
      }
      dropPreview.position = position;
      dropPreview.element.dataset.position = position;
      Object.assign(dropPreview.element.style, {
        left: `${left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px`,
      });
      dropPreview.label.textContent = { above: 'Stack above', below: 'Stack below',
        left: 'Split left', right: 'Split right', tab: 'Group as tabs' }[position];
    }

    // Native observation supplies release/cancel and physical coordinates; dropEffect only
    // tells us when the existing HTML docking path already handled the gesture.
    async function finishDrag(drag, cancelled) {
      if (drag.ended) return;
      drag.ended = true;
      if (activeDrag === drag) activeDrag = null;
      clearDropPreview();
      drag.container.classList.remove('panel-workspace-dragging');
      drag.preview?.remove();
      try {
        if (!await drag.ready) {
          if (!cancelled && sameContext(drag.context))
            status('This drag could not detach a panel. Try again or use Arrange panels.');
          return;
        }
        const placement = await native('finish_panel_drag', {
          context: drag.context, dragId: drag.id, cancelled,
        });
        if (!placement || cancelled || activeDrag || !sameContext(drag.context) ||
            !owns(child?.window_id || 'sidebar', drag.panelId)) return;
        await changed({ type: 'move', panelId: drag.panelId, destination: { windowId: 'new', placement } });
      } catch (_) {
        if (!cancelled && sameContext(drag.context))
          status('The panel could not be detached. Use Arrange panels or try dragging again.');
      }
    }

    function wire(container) {
      if (state.panelProjection?.loading) return;
      // wire() runs after each render; properties replace listeners on the persistent container.
      container.ondragleave = (event) => {
        if (!container.contains(event.relatedTarget)) clearDropPreview();
      };
      container.ondragover = (event) => {
        if (!event.target.closest('[data-panel-drop]')) clearDropPreview();
      };
      container.querySelectorAll('[data-panel-menu]').forEach((button) =>
        button.addEventListener('click', () => menu(button.dataset.panelMenu)));
      container.querySelectorAll('[data-workspace-tab]').forEach((button) => {
        button.addEventListener('click', () => { void changed({ type: 'tab',
          groupId: button.dataset.workspaceTab, panelId: button.dataset.workspacePanel }); });
        button.addEventListener('dragstart', (event) => {
          const surface = state.panelProjection.surfaces.find((item) => item.id === button.dataset.workspacePanel);
          if (!context || !surface) { event.preventDefault(); return; }
          cancelDrag();
          container.classList.add('panel-workspace-dragging');
          const drag = { id: crypto.randomUUID(), context: { ...context }, panelId: surface.id, container };
          activeDrag = drag;
          const preview = dragPreview(event, button, surface);
          drag.preview = preview.preview;
          event.dataTransfer.setData('application/x-sylvia-panel', JSON.stringify({
            context, panelId: drag.panelId }));
          event.dataTransfer.effectAllowed = 'move';
          drag.ready = (child ? Promise.resolve() : nativeQueue).then(async () => {
            if (!sameContext(drag.context)) return false;
            await native('begin_panel_drag', { context: drag.context, dragId: drag.id,
              width: preview.width, height: preview.height });
            return true;
          }).catch(() => false);
        });
        button.addEventListener('dragend', (event) => {
          if (activeDrag?.panelId === button.dataset.workspacePanel)
            void finishDrag(activeDrag, Boolean(event.dataTransfer?.dropEffect && event.dataTransfer.dropEffect !== 'none'));
          else container.classList.remove('panel-workspace-dragging');
        });
      });
      container.querySelectorAll('[data-panel-drop]').forEach((target) => {
        target.addEventListener('dragover', (event) => {
          if (!event.dataTransfer.types.includes('application/x-sylvia-panel') ||
              activeDrag?.panelId === target.dataset.panelDrop) {
            clearDropPreview();
            return;
          }
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
          showDropPreview(target, panelDropPosition(target, event));
        });
        target.addEventListener('dragleave', (event) => {
          if (!target.contains(event.relatedTarget)) clearDropPreview();
        });
        target.addEventListener('drop', (event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
          const position = dropPreview?.target === target
            ? dropPreview.position : panelDropPosition(target, event);
          clearDropPreview();
          let value;
          try { value = JSON.parse(event.dataTransfer.getData('application/x-sylvia-panel')); }
          catch (_) { return; }
          if (!sameContext(value?.context) || !layout.known.includes(value?.panelId) ||
              value.panelId === target.dataset.panelDrop) return;
          void changed({ type: 'move', panelId: value.panelId, destination: {
            windowId: child?.window_id || 'sidebar', targetPanelId: target.dataset.panelDrop, position } });
        });
      });
      container.querySelectorAll('[data-panel-workspace-destination]').forEach((target) => {
        target.addEventListener('dragover', (event) => {
          if (!event.dataTransfer.types.includes('application/x-sylvia-panel')) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
        });
        target.addEventListener('drop', (event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
          clearDropPreview();
          let value;
          try { value = JSON.parse(event.dataTransfer.getData('application/x-sylvia-panel')); }
          catch (_) { return; }
          if (!sameContext(value?.context) || !layout.known.includes(value?.panelId)) return;
          void changed({ type: 'move', panelId: value.panelId,
            destination: { windowId: target.dataset.panelWorkspaceDestination } });
        });
      });
      container.querySelectorAll('[data-panel-preference]').forEach((button) => {
        button.addEventListener('click', () => {
          const action = button.dataset.panelPreference;
          layout = store.preference(action);
          restoreWindows = action === 'mine' || action === 'original';
          render();
        });
      });
      container.querySelectorAll('[data-panel-show]').forEach((button) =>
        button.addEventListener('click', () => changed({ type: 'show', panelId: button.dataset.panelShow })));
      container.querySelectorAll('[data-panel-close-window]').forEach((button) =>
        button.addEventListener('click', () => changed({ type: 'close-window', windowId: button.dataset.panelCloseWindow })));
      container.querySelectorAll('[data-panel-split]').forEach((split) => {
        const handle = split.querySelector(':scope > .panel-split-handle');
        let dragging = false, ratio = Number(split.style.getPropertyValue('--panel-ratio')) || 0.5;
        const commit = () => { void changed({ type: 'ratio', nodeId: split.dataset.panelSplit, ratio }); };
        handle.addEventListener('pointerdown', (event) => {
          if (event.button !== 0) return;
          event.preventDefault();
          dragging = true;
          handle.setPointerCapture(event.pointerId);
        });
        handle.addEventListener('pointermove', (event) => {
          if (!dragging) return;
          const rect = split.getBoundingClientRect();
          ratio = Math.max(0.1, Math.min(0.9, split.dataset.axis === 'row'
            ? (event.clientX - rect.left) / rect.width : (event.clientY - rect.top) / rect.height));
          split.style.setProperty('--panel-ratio', ratio);
          handle.setAttribute('aria-valuenow', Math.round(ratio * 100));
        });
        handle.addEventListener('pointerup', () => { if (dragging) { dragging = false; commit(); } });
        handle.addEventListener('pointercancel', () => { if (dragging) { dragging = false; commit(); } });
        handle.addEventListener('keydown', (event) => {
          if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
          event.preventDefault();
          ratio = Math.max(0.1, Math.min(0.9, ratio + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -0.05 : 0.05)));
          commit();
        });
      });
    }

    return { init, leave, loading, markup, presentEffects, wire, saveView, choose, reveal, request, publish, changed,
      effects, configureEffects, skipEffects,
      effectRevision: () => context && !loadingLayout ? effects.snapshot().revision : null,
      isChild: Boolean(child), ownsPanel: (id) => owns(child?.window_id || 'sidebar',id), snapshot: () => layout };
  }

  window.SylviaPanelHost = { create };
})();
