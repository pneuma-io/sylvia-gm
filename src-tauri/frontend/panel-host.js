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
    let stack = [];
    let frameDrag = null;
    let frameObserver = null;
    let viewFocusWired = false;
    const EFFECTS_READY_TIMEOUT_MS = 2000;
    const FLOATING_BASE_Z = 400;
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
    // The browser WebUI manages detached panels as in-page windows; the native host keeps its own path.
    const browserWindows = () => !child && !window.__TAURI__ && Boolean(window.__sylviaBrowserInvoke);
    const floatingShown = () => browserWindows() && !window.matchMedia?.('(max-width: 760px)').matches;
    const frameOf = (windowId) => layout?.windows.find((item) => item.id === windowId)?.frame || null;
    const minimized = (windowId) => Boolean(frameOf(windowId)?.minimized);

    // The visible game area below the WebUI bar and above the window dock.
    function workArea() {
      const frame = document.getElementById('session-frame')?.getBoundingClientRect();
      const dock = document.querySelector('.floating-panel-dock')?.getBoundingClientRect();
      const top = frame?.height ? Math.max(0, frame.top) : 0;
      const bottom = Math.min(frame?.height ? frame.bottom : window.innerHeight, dock?.height ? dock.top : window.innerHeight);
      return { top, bottom, width: window.innerWidth, height: Math.max(160, bottom - top) };
    }

    // New windows open over the game view, cascading so two windows never exactly coincide.
    function defaultFrame(index = layout?.windows.length || 0) {
      const area = workArea();
      const offset = (index % 6) * 28;
      return { left: Math.round(Math.max(16, area.width * 0.14) + offset),
        top: Math.round(area.top + Math.max(16, area.height * 0.08) + offset),
        width: Math.round(Math.min(760, Math.max(320, area.width * 0.48))),
        height: Math.round(Math.min(720, Math.max(240, area.height * 0.8))), minimized: false };
    }

    // Keep the title bar reachable: on screen vertically and at least partly on screen horizontally.
    function clampFrame(frame) {
      const area = workArea();
      const width = Math.min(frame.width, Math.max(240, area.width - 16));
      const height = Math.min(frame.height, Math.max(160, area.height));
      return { ...frame, width, height,
        left: Math.round(Math.max(96 - width, Math.min(area.width - 96, frame.left))),
        top: Math.round(Math.max(area.top, Math.min(area.bottom - 36, frame.top))) };
    }

    // Window order, back to front. 'main' is the game view; windows behind it are fully covered.
    function syncStack() {
      const ids = (layout?.windows || []).map((item) => item.id);
      stack = stack.filter((id) => id === 'main' || ids.includes(id));
      if (!stack.includes('main')) stack.unshift('main');
      ids.forEach((id) => { if (!stack.includes(id)) stack.push(id); });
    }

    function frontView() {
      for (let index = stack.length - 1; index >= 0; index -= 1)
        if (stack[index] === 'main' || !minimized(stack[index])) return stack[index];
      return 'main';
    }

    function windowState(windowId) {
      if (windowId === 'main') return frontView() === 'main' ? 'front' : 'open';
      if (minimized(windowId)) return 'minimized';
      if (frontView() === windowId) return 'front';
      return stack.indexOf(windowId) < stack.indexOf('main') ? 'covered' : 'open';
    }

    const stateLabels = { front: 'in front', open: 'open', covered: 'behind the game view', minimized: 'minimized' };

    // Restack the rendered windows and dock without re-rendering, so the click that raised a
    // window still reaches the control under the pointer.
    function applyStack() {
      syncStack();
      document.querySelectorAll('[data-floating-window]').forEach((element) => {
        const id = element.dataset.floatingWindow, current = windowState(id);
        element.style.zIndex = String(FLOATING_BASE_Z + Math.max(0, stack.indexOf(id)));
        element.dataset.windowState = current;
        element.inert = current === 'covered';
      });
      document.querySelectorAll('[data-floating-dock-view]').forEach((button) => {
        const current = windowState(button.dataset.floatingDockView);
        button.dataset.windowState = current;
        button.setAttribute('aria-pressed', String(current === 'front'));
        button.setAttribute('aria-label', `${button.dataset.floatingDockTitle} · ${stateLabels[current]}`);
      });
    }

    function setFrame(windowId, patch, rerender = true) {
      const index = layout?.windows.findIndex((item) => item.id === windowId);
      if (!context || loadingLayout || index == null || index < 0) return;
      layout = store.change({ type: 'frame', windowId, frame: { ...(frameOf(windowId) || defaultFrame(index)), ...patch } });
      if (rerender) render();
    }

    // Bring a window (restoring it if minimized) or the game view to the front.
    function raise(view) {
      if (!floatingShown() || !context) return;
      syncStack();
      if (!stack.includes(view)) return;
      stack = [...stack.filter((id) => id !== view), view];
      if (view !== 'main' && minimized(view)) setFrame(view, { minimized: false });
      else applyStack();
    }

    // Taskbar behaviour: the front window minimizes; any other window comes forward.
    function dockSelect(view) {
      if (view !== 'main' && windowState(view) === 'front') setFrame(view, { minimized: true });
      else raise(view);
    }

    function detach(panelId) {
      void changed({ type: 'move', panelId, destination: { windowId: 'new', frame: defaultFrame() } });
    }

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
      // In-page windows draw in this document, so no separate window has to report readiness.
      if (browserWindows()) return new Set();
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
      stack = [];
      endFrameDrag();
      frameObserver?.disconnect();
      frameObserver = null;
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

    // Phase one returns legacy window panels inline before any loading publication.
    function returnWindowsInline() {
      for (const item of [...(layout?.windows || [])])
        layout = store.change({ type: 'close-window', windowId: item.id });
    }

    // Loading carries saved layout identifiers, never stale game data or old effects.
    function loading(campaignId, sessionId, phase = 'loading') {
      if (child) return null;
      if (context?.campaign_id !== campaignId || context?.session_id !== sessionId) {
        leave();
        context = { campaign_id: campaignId, session_id: sessionId, generation: crypto.randomUUID() };
        layout = store.restore(campaignId);
        if (!browserWindows()) returnWindowsInline();
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
      if (!browserWindows()) returnWindowsInline();
      else layout.windows.forEach((item, index) => {
        if (!item.frame) layout = store.change({ type: 'frame', windowId: item.id, frame: defaultFrame(index) });
      });
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
      const destination = command?.type === 'move' ? command.destination?.windowId : 'sidebar';
      if (destination !== 'sidebar' && !(destination === 'new' ? floatingShown() : browserWindows())) {
        status('This candidate keeps panels in the main workspace. Arrange panels here instead.');
        return;
      }
      if (loadingLayout && !['placement', 'close-window'].includes(command?.type)) return;
      if (destination === 'new' && !command.destination.frame)
        command = { ...command, destination: { ...command.destination, frame: defaultFrame() } };
      layout = store.change(command);
      syncStack();
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
      if (windowId !== 'sidebar') raise(windowId);
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

    function treeMarkup(tree, surfaces, floatable = false) {
      if (!tree) return '';
      if (tree.kind === 'split') {
        const first = treeMarkup(tree.first, surfaces, floatable), second = treeMarkup(tree.second, surfaces, floatable);
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
        `role="tab" aria-controls="panel-${escape(surface.id)}" tabindex="${surface.id === selected.id ? '0' : '-1'}" ` +
        `draggable="true" data-workspace-panel="${escape(surface.id)}" data-workspace-tab="${escape(tree.id)}" ` +
        `title="Drag ${escape(surface.title || surface.id)} to move it" ` +
        `aria-selected="${surface.id === selected.id}">${surface.icon ? `<span class="panel-glyph" aria-hidden="true">${escape(surface.icon)}</span>` : ''}` +
        `${escape(surface.title || surface.id)}</button>`).join('');
      return `<section class="panel-tab-group" data-renderer="${escape(selected.renderer)}" data-panel-drop="${escape(selected.id)}">` +
        `<div class="ruleset-panel-tabs" role="tablist" aria-label="Panel tabs">${tabs}` +
        (floatable && floatingShown() ? `<button type="button" class="panel-menu-button panel-float-button" ` +
          `data-panel-float="${escape(selected.id)}" title="Float ${escape(selected.title || selected.id)} in its own window" ` +
          `aria-label="Float ${escape(selected.title || selected.id)} in its own window">⧉</button>` : '') +
        `<button type="button" class="panel-menu-button" ` +
        `data-panel-menu="${escape(selected.id)}" title="Arrange ${escape(selected.title || selected.id)}" ` +
        `aria-label="Arrange ${escape(selected.title || selected.id)}">⋯</button>` +
        `</div>${renderPanel(selected, false)}</section>`;
    }

    // One chip per declared panel with its real placement; choosing a chip reveals that panel.
    function stripMarkup(current, surfaces) {
      const shown = new Set();
      (function collect(tree) {
        if (!tree) return;
        if (tree.kind === 'split') { collect(tree.first); collect(tree.second); return; }
        const available = tree.panels.map((id) => surfaces.find((item) => item.id === id))
          .filter((surface) => surface && visible(surface));
        const selected = available.find((item) => item.id === tree.active) || available[0];
        if (selected) shown.add(selected.id);
      })(ownerTree('sidebar'));
      const labels = { shown: 'showing', tab: 'behind a tab', hidden: 'hidden',
        window: floatingShown() ? 'in a floating window' : 'in its own window' };
      return '<div class="panel-strip-chips">' + surfaces.filter((surface) =>
        current.known.includes(surface.id) && (visible(surface) || current.hidden.includes(surface.id))).map((surface) => {
        const placement = current.hidden.includes(surface.id) ? 'hidden'
          : current.windows.some((item) => owns(item.id, surface.id)) ? 'window'
            : shown.has(surface.id) ? 'shown' : 'tab';
        const title = surface.title || surface.id;
        return `<button type="button" class="panel-chip" data-panel-chip="${escape(surface.id)}" ` +
          `data-panel-placement="${placement}" aria-label="${escape(title)} · ${labels[placement]}" ` +
          `title="${escape(title)} · ${labels[placement]}">${surface.icon ? `<span class="panel-glyph" aria-hidden="true">${escape(surface.icon)}</span>` : ''}${escape(title)}</button>`;
      }).join('') + '</div>';
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
      const controls = child ? '' : `<div class="panel-strip" role="group" aria-label="Game panels">` +
        `<span class="panel-strip-label" aria-hidden="true">Panels</span>${stripMarkup(current, projection.surfaces)}` +
        `<details class="panel-layout-controls"><summary>Arrange panels</summary>` +
        `<div class="panel-layout-options"><button type="button" data-panel-preference="save">Save as my Ruleset default</button>` +
        `<button type="button" data-panel-preference="mine">Use my Ruleset layout</button>` +
        `<button type="button" data-panel-preference="original">Use Ruleset's original layout</button>` +
        current.windows.map((item) => `<button type="button" data-panel-close-window="${escape(item.id)}">` +
          `${escape(item.title)}: return panels to sidebar</button>`).join('') +
        current.hidden.map((id) => {
          const surface = projection.surfaces.find((item) => item.id === id);
          return surface ? `<button type="button" data-panel-show="${escape(id)}">Show ${escape(surface.title || id)}</button>` : '';
        }).join('') + '</div></details></div>';
      // Narrow browser screens show saved floating windows inline until there is room to float them.
      const inline = browserWindows() && !floatingShown() ? current.windows.map((item) =>
        `<div class="floating-panel-inline" data-floating-inline="${escape(item.id)}">` +
        `${treeMarkup(item.tree, projection.surfaces)}</div>`).join('') : '';
      const contents = treeMarkup(tree, projection.surfaces, !child) + inline;
      const dropDestinations = `<div class="panel-workspace-drop-destinations" aria-label="Panel drop destinations">` +
        `<div class="panel-workspace-drop-destination" data-panel-workspace-destination="sidebar">Return to sidebar</div>` +
          `</div>`;
      return controls + dropDestinations + `<div class="panel-workspace-tree">${contents || (child
        ? '<p class="panel-empty">No panels are available in the current game state.</p>' : '')}</div>` +
        (floatingShown() ? floatingMarkup(current, projection.surfaces) : '');
    }

    function windowTitle(item, surfaces) {
      const shown = panelIds(item.tree).map((id) => surfaces.find((surface) => surface.id === id))
        .filter((surface) => surface && visible(surface));
      return shown.length === 1 ? shown[0].title || shown[0].id : item.title;
    }

    // In-page windows plus the dock that keeps every one of them reachable over the game view.
    function floatingMarkup(current, surfaces) {
      if (!current.windows.length) return '';
      syncStack();
      const dock = [`<button type="button" class="floating-dock-item" data-floating-dock-view="main" ` +
        `data-floating-dock-title="Game view" data-window-state="${windowState('main')}" ` +
        `aria-pressed="${windowState('main') === 'front'}" aria-label="Game view · ${stateLabels[windowState('main')]}" ` +
        `title="Bring the game view to the front">Game view</button>`];
      const windows = current.windows.map((item, index) => {
        const title = windowTitle(item, surfaces), id = escape(item.id), state = windowState(item.id);
        const only = panelIds(item.tree).length === 1 && surfaces.find((surface) => surface.id === panelIds(item.tree)[0]);
        dock.push(`<button type="button" class="floating-dock-item" data-floating-dock-view="${id}" ` +
          `data-floating-dock-title="${escape(title)}" data-window-state="${state}" aria-pressed="${state === 'front'}" ` +
          `aria-label="${escape(title)} · ${stateLabels[state]}" title="${escape(title)}: bring to front, or minimize when in front">` +
          `${only?.icon ? `<span class="panel-glyph" aria-hidden="true">${escape(only.icon)}</span>` : ''}` +
          `<span class="floating-dock-title">${escape(title)}</span></button>`);
        if (state === 'minimized') return '';
        const frame = clampFrame(frameDrag?.windowId === item.id ? frameDrag.frame : item.frame || defaultFrame(index));
        const contents = treeMarkup(item.tree, surfaces, panelIds(item.tree).length > 1) ||
          '<p class="panel-empty">No panels are available in the current game state.</p>';
        return `<section class="floating-panel-window" data-floating-window="${id}" data-window-state="${state}" ` +
          `role="group" aria-label="${escape(title)} window" data-frame-width="${frame.width}" data-frame-height="${frame.height}" ` +
          `style="left:${frame.left}px;top:${frame.top}px;width:${frame.width}px;height:${frame.height}px;` +
          `z-index:${FLOATING_BASE_Z + stack.indexOf(item.id)}"${state === 'covered' ? ' inert' : ''}>` +
          `<header class="floating-panel-titlebar" data-floating-drag="${id}" title="Drag to move ${escape(title)}">` +
          `<span class="floating-panel-title">${escape(title)}</span>` +
          `<button type="button" class="floating-panel-control" data-floating-minimize="${id}" ` +
          `aria-label="Minimize ${escape(title)} to the window dock" title="Minimize to the window dock">–</button>` +
          `<button type="button" class="floating-panel-control" data-floating-return="${id}" ` +
          `aria-label="Return ${escape(title)} to the panel column" title="Return to the panel column">⇲</button></header>` +
          `<div class="floating-panel-body panel-window-host"><div class="panel-workspace-tree">${contents}</div></div></section>`;
      }).join('');
      return windows + `<nav class="floating-panel-dock" aria-label="Window dock">` +
        `<span class="floating-dock-label" aria-hidden="true">Windows</span>${dock.join('')}</nav>`;
    }

    function menu(panelId) {
      const surfaces = state.panelProjection.surfaces;
      const surface = surfaces.find((item) => item.id === panelId);
      if (!surface) return;
      const menuContext = { ...context };
      const destinations = [
        ['Return to sidebar', { windowId: 'sidebar' }],
        ...(floatingShown() ? [['Float in its own window', { windowId: 'new' }]] : []),
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
        (floatingShown() ? '<p>Floating windows stay reachable from the window dock at the bottom of the page.</p>'
          : '<p>This candidate keeps panels in the main workspace.</p>') +
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

    // Finish an in-workspace panel drag without starting a native window operation.
    async function finishDrag(drag, cancelled) {
      if (drag.ended) return;
      drag.ended = true;
      if (activeDrag === drag) activeDrag = null;
      clearDropPreview();
      drag.container.classList.remove('panel-workspace-dragging');
      drag.preview?.remove();
    }

    // Title-bar moves follow window-level pointer events, so a re-render mid-drag cannot strand them.
    function startFrameDrag(event, bar) {
      const element = bar.closest('[data-floating-window]');
      if (event.button !== 0 || event.target.closest('button') || !element || !context) return;
      event.preventDefault();
      endFrameDrag();
      const rect = element.getBoundingClientRect();
      // Moving keeps the saved size; the rendered size may only be clamped to a small viewport.
      frameDrag = { windowId: bar.dataset.floatingDrag, pointerId: event.pointerId, context: { ...context },
        dx: event.clientX - rect.left, dy: event.clientY - rect.top,
        frame: { width: rect.width, height: rect.height, ...(frameOf(bar.dataset.floatingDrag) || {}),
          left: rect.left, top: rect.top } };
      element.classList.add('floating-panel-moving');
      window.addEventListener('pointermove', moveFrameDrag);
      window.addEventListener('pointerup', endFrameDrag);
      window.addEventListener('pointercancel', endFrameDrag);
    }

    function moveFrameDrag(event) {
      if (!frameDrag || event.pointerId !== frameDrag.pointerId) return;
      const next = clampFrame({ ...frameDrag.frame, left: event.clientX - frameDrag.dx, top: event.clientY - frameDrag.dy });
      frameDrag.frame = { ...frameDrag.frame, left: next.left, top: next.top };
      const element = document.querySelector(`[data-floating-window="${CSS.escape(frameDrag.windowId)}"]`);
      if (element) Object.assign(element.style, { left: `${next.left}px`, top: `${next.top}px` });
    }

    function endFrameDrag(event) {
      if (!frameDrag || (event && event.pointerId !== frameDrag.pointerId)) return;
      const drag = frameDrag;
      frameDrag = null;
      window.removeEventListener('pointermove', moveFrameDrag);
      window.removeEventListener('pointerup', endFrameDrag);
      window.removeEventListener('pointercancel', endFrameDrag);
      document.querySelector(`[data-floating-window="${CSS.escape(drag.windowId)}"]`)?.classList.remove('floating-panel-moving');
      if (event && sameContext(drag.context)) setFrame(drag.windowId, { left: drag.frame.left, top: drag.frame.top }, false);
    }

    // A pointer press raises the window under it; a plain click elsewhere in the game view raises the view.
    // Click (not press) keeps a panel drag that starts in the view from hiding its destination windows.
    function wireViewFocus() {
      if (viewFocusWired) return;
      viewFocusWired = true;
      document.addEventListener('pointerdown', (event) => {
        const element = event.target.closest?.('[data-floating-window]');
        if (element && floatingShown() && windowState(element.dataset.floatingWindow) !== 'front')
          raise(element.dataset.floatingWindow);
      }, true);
      document.addEventListener('click', (event) => {
        const target = event.target;
        if (!floatingShown() || !layout?.windows.length || frontView() === 'main' ||
            !target.closest?.('#session-frame') ||
            target.closest('[data-floating-window], .floating-panel-dock, dialog')) return;
        raise('main');
      }, true);
      // Keyboard focus entering a window behind another brings it forward, like a press does.
      document.addEventListener('focusin', (event) => {
        const element = event.target.closest?.('[data-floating-window]');
        if (element && floatingShown() && windowState(element.dataset.floatingWindow) === 'open')
          raise(element.dataset.floatingWindow);
      });
      let resizeFrame = null;
      window.addEventListener('resize', () => {
        if (resizeFrame !== null) return;
        resizeFrame = window.requestAnimationFrame(() => {
          resizeFrame = null;
          fitViewport();
        });
      });
    }

    // Crossing the narrow breakpoint switches between floating and inline windows; otherwise
    // rendered windows are re-clamped in place so title bars stay reachable. Nothing is saved.
    function fitViewport() {
      if (!context || !layout?.windows.length || state.panelProjection?.loading) return;
      if (Boolean(document.querySelector('.floating-panel-dock')) !== floatingShown()) {
        render();
        return;
      }
      if (frameDrag) return;
      document.querySelectorAll('[data-floating-window]').forEach((element) => {
        const index = layout.windows.findIndex((item) => item.id === element.dataset.floatingWindow);
        if (index < 0) return;
        const frame = clampFrame(layout.windows[index].frame || defaultFrame(index));
        element.dataset.frameWidth = frame.width;
        element.dataset.frameHeight = frame.height;
        Object.assign(element.style, { left: `${frame.left}px`, top: `${frame.top}px`,
          width: `${frame.width}px`, height: `${frame.height}px` });
      });
    }

    // Window controls re-render; keep keyboard focus on the control that now represents the window.
    function refocus(selector) {
      document.querySelector(selector)?.focus({ preventScroll: true });
    }

    function wireFloating(container) {
      frameObserver?.disconnect();
      frameObserver = null;
      if (browserWindows()) wireViewFocus();
      if (!floatingShown()) return;
      const css = (value) => CSS.escape(value);
      container.querySelectorAll('[data-panel-float]').forEach((button) =>
        button.addEventListener('click', () => {
          const panelId = button.dataset.panelFloat;
          detach(panelId);
          refocus(`[data-floating-window] [data-workspace-panel="${css(panelId)}"]`);
        }));
      container.querySelectorAll('[data-floating-minimize]').forEach((button) =>
        button.addEventListener('click', () => {
          const windowId = button.dataset.floatingMinimize;
          setFrame(windowId, { minimized: true });
          refocus(`[data-floating-dock-view="${css(windowId)}"]`);
        }));
      container.querySelectorAll('[data-floating-return]').forEach((button) =>
        button.addEventListener('click', () => {
          const tree = ownerTree(button.dataset.floatingReturn);
          const panelId = tree?.kind === 'tabs' ? tree.active : panelIds(tree)[0];
          changed({ type: 'close-window', windowId: button.dataset.floatingReturn });
          if (panelId) refocus(`#ruleset-panels > .panel-workspace-tree [data-workspace-panel="${css(panelId)}"]`);
        }));
      container.querySelectorAll('[data-floating-dock-view]').forEach((button) =>
        button.addEventListener('click', () => {
          const view = button.dataset.floatingDockView;
          dockSelect(view);
          if (!button.isConnected) refocus(`[data-floating-dock-view="${css(view)}"]`);
        }));
      container.querySelectorAll('[data-floating-drag]').forEach((bar) =>
        bar.addEventListener('pointerdown', (event) => startFrameDrag(event, bar)));
      // Save a user resize (the native corner grip); renders that merely clamp to the viewport are not saved.
      if (!window.ResizeObserver) return;
      frameObserver = new ResizeObserver((entries) => entries.forEach(({ target }) => {
        const width = Math.round(target.offsetWidth), height = Math.round(target.offsetHeight);
        if (frameDrag || !target.isConnected || (Math.abs(width - Number(target.dataset.frameWidth)) < 2 &&
            Math.abs(height - Number(target.dataset.frameHeight)) < 2)) return;
        target.dataset.frameWidth = width;
        target.dataset.frameHeight = height;
        setFrame(target.dataset.floatingWindow, { width, height }, false);
      }));
      container.querySelectorAll('[data-floating-window]').forEach((element) => frameObserver.observe(element));
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
        button.addEventListener('keydown', (event) => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          const tabs = Array.from(button.parentElement.querySelectorAll('[data-workspace-tab]'));
          const index = tabs.indexOf(button);
          const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs.at(-1)
            : tabs[(index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
          if (!next || !context) return;
          event.preventDefault();
          const captured = { ...context };
          const groupId = next.dataset.workspaceTab, panelId = next.dataset.workspacePanel;
          void Promise.resolve(changed({type:'tab',groupId,panelId})).then(() => {
            if (sameContext(captured)) container.querySelector(
              `[data-workspace-tab="${CSS.escape(groupId)}"][data-workspace-panel="${CSS.escape(panelId)}"]`)?.focus();
          }).catch(error => status(`Panel tab could not be selected: ${error}`));
        });
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
          const owner = target.closest('[data-floating-window]')?.dataset.floatingWindow ||
            target.closest('[data-floating-inline]')?.dataset.floatingInline;
          void changed({ type: 'move', panelId: value.panelId, destination: {
            windowId: child?.window_id || owner || 'sidebar', targetPanelId: target.dataset.panelDrop, position } });
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
      container.querySelectorAll('[data-panel-chip]').forEach((button) =>
        button.addEventListener('click', () => reveal(button.dataset.panelChip)));
      container.querySelectorAll('[data-panel-show]').forEach((button) =>
        button.addEventListener('click', () => changed({ type: 'show', panelId: button.dataset.panelShow })));
      container.querySelectorAll('[data-panel-close-window]').forEach((button) =>
        button.addEventListener('click', () => changed({ type: 'close-window', windowId: button.dataset.panelCloseWindow })));
      wireFloating(container);
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
