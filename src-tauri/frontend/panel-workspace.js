(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.SylviaPanelWorkspace = api;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this), function () {
  'use strict';

  const VERSION = 1;
  const LEGACY_KEY = 'sylvia-gm-panel-layout-v1';
  const CAMPAIGN_PREFIX = 'sylvia-gm-panel-workspace-v1:campaign:';
  const RULESET_PREFIX = 'sylvia-gm-panel-workspace-v1:ruleset:';

  function copy(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function read(storage, key) {
    try {
      const raw = storage && storage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function write(storage, key, value) {
    if (!storage || typeof storage.setItem !== 'function') return;
    storage.setItem(key, JSON.stringify(value));
  }

  function identifier(prefix, value) {
    return `${prefix}-${encodeURIComponent(String(value))}`;
  }

  function idsIn(tree, result) {
    if (!tree) return result;
    if (tree.kind === 'tabs') tree.panels.forEach((id) => result.push(id));
    else if (tree.kind === 'split') {
      idsIn(tree.first, result);
      idsIn(tree.second, result);
    }
    return result;
  }

  function uniqueId(value, prefix, identity) {
    const wanted = typeof value === 'string' && value ? value : prefix;
    if (!identity.used.has(wanted)) {
      identity.used.add(wanted);
      return wanted;
    }
    let candidate;
    do { candidate = `${wanted}-${++identity.next}`; } while (identity.used.has(candidate));
    identity.used.add(candidate);
    return candidate;
  }

  function cleanTree(tree, allowed, seen, identity) {
    if (!tree || typeof tree !== 'object') return null;
    if (tree.kind === 'tabs' && Array.isArray(tree.panels)) {
      const panels = tree.panels.filter((id) =>
        typeof id === 'string' && allowed.has(id) && !seen.has(id) && seen.add(id)
      );
      if (!panels.length) return null;
      return { kind: 'tabs', id: uniqueId(tree.id, 'tabs', identity), panels, active: panels.includes(tree.active) ? tree.active : panels[0] };
    }
    if (tree.kind === 'split' && (tree.axis === 'row' || tree.axis === 'column')) {
      const nodeId = uniqueId(tree.id, 'split', identity);
      const first = cleanTree(tree.first, allowed, seen, identity);
      const second = cleanTree(tree.second, allowed, seen, identity);
      if (!first) return second;
      if (!second) return first;
      const ratio = Number(tree.ratio);
      return {
        kind: 'split', id: nodeId, axis: tree.axis,
        ratio: Number.isFinite(ratio) ? Math.max(0.1, Math.min(0.9, ratio)) : 0.5,
        first, second,
      };
    }
    return null;
  }

  function tabs(id, panels) {
    return { kind: 'tabs', id, panels: panels.slice(), active: panels[0] };
  }

  function join(first, second, axis, id) {
    if (!first) return second;
    if (!second) return first;
    return { kind: 'split', id, axis: axis || 'column', ratio: 0.5, first, second };
  }

  function defaultLayout(surfaces) {
    const ordered = surfaces.slice().sort((a, b) =>
      (Number(a.order) || 0) - (Number(b.order) || 0) || String(a.id).localeCompare(String(b.id))
    );
    // New game workspaces show the three declared primary surfaces independently.
    // Saved layouts still reconcile through the ordinary reducer without replacement.
    const map = ordered.find(item => ['coordinate_map', 'tactical_grid'].includes(item.renderer));
    const party = ordered.find(item => item.renderer === 'party');
    const character = ordered.find(item => item.renderer === 'unit_list');
    if (map && party && character && [map, party, character].every(item => item.default_placement !== 'window')) {
      const queue = ordered.find(item => item.renderer === 'action_queue');
      const utility = ordered.filter(item => ![map.id, party.id, character.id, queue?.id].includes(item.id));
      const loadout = utility.filter(item => item.renderer === 'inventory').map(item => item.id);
      const status = utility.filter(item => !loadout.includes(item.id) && item.default_placement !== 'window').map(item => item.id);
      return { version: VERSION,
        sidebar: { kind: 'split', id: 'game-columns', axis: 'row', ratio: 0.34,
          first: { kind: 'split', id: 'game-roster-character', axis: 'column', ratio: 0.4,
            first: tabs('game-party', [party.id, ...status]), second: queue
              ? {kind:'split',id:'game-character-queue',axis:'column',ratio:0.65,
                first:tabs('game-character',[character.id,...loadout]),second:tabs('game-action-queue',[queue.id])}
              : tabs('game-character', [character.id, ...loadout]) },
          second: tabs('game-map', [map.id]) },
        windows: utility.filter(item => item.default_placement === 'window').map(item =>
          ({ id: identifier('window', item.id), title: item.title || item.id, tree: tabs(identifier('tabs',item.id),[item.id]) })),
        hidden: [], known: ordered.map(item => item.id), panel_sizes: {}, sidebar_width: null, views: {} };
    }
    const groups = [];
    const byGroup = new Map();
    ordered.forEach((surface) => {
      if (!surface || typeof surface.id !== 'string') return;
      const placement = surface.default_placement === 'window' ? 'window' : 'docked';
      const key = `${placement}:` + (surface.group ? `group:${surface.group}` : `panel:${surface.id}`);
      if (!byGroup.has(key)) {
        const group = [];
        byGroup.set(key, group);
        groups.push([key, group]);
      }
      byGroup.get(key).push(surface.id);
    });
    let sidebar = null;
    const windows = [];
    groups.forEach(([key, panels], index) => {
      const tree = tabs(identifier('tabs', key), panels);
      if (key.startsWith('window:')) {
        windows.push({ id: `window-default-${index}`, title: ordered.find((item) => item.id === panels[0]).title || 'Panels', tree });
      } else sidebar = join(sidebar, tree, 'column', `split-default-${index}`);
    });
    return {
      version: VERSION, sidebar, windows, hidden: [],
      known: ordered.map((surface) => surface.id), panel_sizes: {}, sidebar_width: null, views: {},
    };
  }

  function validLayout(value) {
    return value && typeof value === 'object' && value.version === VERSION &&
      Array.isArray(value.windows) && Array.isArray(value.hidden) && Array.isArray(value.known);
  }

  function baseLayout(original, surfaces) {
    if (!validLayout(original)) return defaultLayout(surfaces);
    return copy(original);
  }

  function reconcile(layout, surfaces) {
    const surfaceIds = surfaces.filter((surface) => surface && typeof surface.id === 'string')
      .map((surface) => surface.id);
    const known = Array.from(new Set([
      ...(layout.known || []).filter((id) => typeof id === 'string' && id), ...surfaceIds
    ]));
    const allowed = new Set(known);
    const seen = new Set();
    const identity = { used: new Set(), next: 0 };
    const sidebar = cleanTree(layout.sidebar, allowed, seen, identity);
    const windowIds = { used: new Set(), next: 0 };
    const windows = (layout.windows || []).map((window) => {
      if (!window || typeof window !== 'object') return null;
      const tree = cleanTree(window.tree, allowed, seen, identity);
      if (!tree) return null;
      const result = { id: uniqueId(window.id, 'window', windowIds), title: String(window.title || 'Panels'), tree };
      if (window.placement && typeof window.placement === 'object') result.placement = copy(window.placement);
      return result;
    }).filter(Boolean);
    const hidden = [];
    (layout.hidden || []).forEach((id) => {
      if (allowed.has(id) && !seen.has(id)) {
        seen.add(id);
        hidden.push(id);
      }
    });
    let nextSidebar = sidebar;
    surfaceIds.forEach((id) => {
      if (seen.has(id)) return;
      seen.add(id);
      nextSidebar = join(nextSidebar, tabs(identifier('tabs', id), [id]), 'column', identifier('split-new', id));
    });
    return {
      version: VERSION, sidebar: nextSidebar, windows, hidden, known,
      panel_sizes: layout.panel_sizes && typeof layout.panel_sizes === 'object' ? copy(layout.panel_sizes) : {},
      sidebar_width: layout.sidebar_width != null && Number.isFinite(Number(layout.sidebar_width))
        ? Number(layout.sidebar_width) : null,
      views: cleanViews(layout.views),
      inspection: cleanInspection(layout.inspection),
    };
  }

  function cleanView(view) {
    if (!view || typeof view !== 'object' || Array.isArray(view)) return {};
    const safe = {};
    const zoom = view.zoom;
    if (typeof zoom === 'number' && Number.isFinite(zoom))
      safe.zoom = Math.max(0.5, Math.min(2, zoom));
    ['scrollLeft', 'scrollTop'].forEach((field) => {
      const value = view[field];
      if (typeof value === 'number' && Number.isFinite(value))
        safe[field] = Math.max(0, Math.min(10000000, value));
    });
    if (typeof view.coverInfo === 'boolean') safe.coverInfo = view.coverInfo;
    if (view.selectedGeometry === null || typeof view.selectedGeometry === 'string') {
      safe.selectedGeometry = typeof view.selectedGeometry === 'string'
        ? view.selectedGeometry.slice(0, 512) : null;
    }
    return safe;
  }

  // Saved inspection is presentation only and remains isolated to its campaign/Ruleset.
  function cleanInspection(value) {
    const validId = (id) => typeof id === 'string' && id.length > 0 && id.length <= 512;
    if (!value || !validId(value.campaignId) || !(value.rulesetId === null || validId(value.rulesetId))) return null;
    return { campaignId: value.campaignId, rulesetId: value.rulesetId,
      id: validId(value.id) ? value.id : null,
      unavailableId: validId(value.unavailableId) ? value.unavailableId : null,
      expanded: Array.isArray(value.expanded) ? [...new Set(value.expanded.filter(validId))].slice(0, 512) : [] };
  }

  function cleanViews(views) {
    if (!views || typeof views !== 'object' || Array.isArray(views)) return {};
    const safe = {};
    Object.keys(views).forEach((mapId) => {
      if (mapId) safe[mapId] = cleanView(views[mapId]);
    });
    return safe;
  }

  function applyLegacyTabs(layout, legacyTabs, surfaces) {
    if (!legacyTabs || typeof legacyTabs !== 'object') return layout;
    const desired = new Map();
    surfaces.forEach((surface) => {
      if (surface && surface.group && legacyTabs[surface.group] === surface.id)
        desired.set(`tabs-${encodeURIComponent(`docked:group:${surface.group}`)}`, surface.id);
    });
    function apply(tree) {
      if (!tree) return tree;
      if (tree.kind === 'tabs' && desired.has(tree.id) && tree.panels.includes(desired.get(tree.id)))
        return { ...tree, active: desired.get(tree.id) };
      if (tree.kind === 'split') return { ...tree, first: apply(tree.first), second: apply(tree.second) };
      return tree;
    }
    return { ...layout, sidebar: apply(layout.sidebar) };
  }

  function removePanel(tree, panelId) {
    if (!tree) return null;
    if (tree.kind === 'tabs') {
      const panels = tree.panels.filter((id) => id !== panelId);
      if (!panels.length) return null;
      return { ...tree, panels, active: panels.includes(tree.active) ? tree.active : panels[0] };
    }
    const collapsed = join(
      removePanel(tree.first, panelId), removePanel(tree.second, panelId), tree.axis, tree.id
    );
    return collapsed && collapsed.kind === 'split' ? { ...collapsed, ratio: tree.ratio } : collapsed;
  }

  function updateNode(tree, id, update) {
    if (!tree) return tree;
    if (tree.id === id) return update(tree);
    if (tree.kind !== 'split') return tree;
    return { ...tree, first: updateNode(tree.first, id, update), second: updateNode(tree.second, id, update) };
  }

  function addAt(tree, panelId, destination, makeId) {
    if (!tree) return tabs(makeId('tabs'), [panelId]);
    if (destination.position === 'tab' && destination.targetPanelId) {
      let found = false;
      const changed = (function add(node) {
        if (node.kind === 'tabs' && node.panels.includes(destination.targetPanelId)) {
          found = true;
          return { ...node, panels: [...node.panels, panelId] };
        }
        if (node.kind === 'split') return { ...node, first: add(node.first), second: add(node.second) };
        return node;
      })(tree);
      if (found) return changed;
    }
    if (destination.targetPanelId) {
      let found = false;
      const position = destination.position;
      const changed = (function add(node) {
        if (node.kind === 'tabs' && node.panels.includes(destination.targetPanelId)) {
          found = true;
          if (position === 'before' || position === 'after') {
            const panels = node.panels.slice();
            const offset = position === 'after' ? 1 : 0;
            panels.splice(panels.indexOf(destination.targetPanelId) + offset, 0, panelId);
            return { ...node, panels };
          }
          const incoming = tabs(makeId('tabs'), [panelId]);
          const before = position === 'left' || position === 'above';
          const axis = position === 'left' || position === 'right' ? 'row' : 'column';
          return join(before ? incoming : node, before ? node : incoming, axis, makeId('split'));
        }
        if (node.kind === 'split') return { ...node, first: add(node.first), second: add(node.second) };
        return node;
      })(tree);
      if (found) return changed;
    }
    return join(tree, tabs(makeId('tabs'), [panelId]), 'column', makeId('split'));
  }

  function createStore(storage, options) {
    const randomUUID = options && options.randomUUID ||
      (typeof crypto !== 'undefined' && crypto.randomUUID && (() => crypto.randomUUID())) ||
      (() => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`);
    let sequence = 0;
    const makeId = (prefix) => `${prefix}-${randomUUID()}-${sequence++}`;
    let context = null;
    let current = null;
    let originalLayout = null;

    function campaignKey() { return `${CAMPAIGN_PREFIX}${context.campaignId}`; }
    function rulesetKey() { return `${RULESET_PREFIX}${context.rulesetId}`; }
    function persist() { if (context && current) write(storage, campaignKey(), current); }

    function select(selection) {
      if (!selection || !selection.campaignId || !selection.rulesetId) return current ? copy(current) : null;
      const surfaces = Array.isArray(selection.surfaces) ? selection.surfaces : [];
      context = { campaignId: selection.campaignId, rulesetId: selection.rulesetId, surfaces };
      originalLayout = reconcile(baseLayout(selection.original, surfaces), surfaces);
      const savedCampaign = read(storage, campaignKey());
      const savedPreference = read(storage, rulesetKey());
      let selected = savedCampaign;
      if (!validLayout(selected)) selected = savedPreference;
      const usingOriginal = !validLayout(selected);
      if (usingOriginal) selected = originalLayout;
      selected = copy(selected);
      if (usingOriginal) {
        const legacy = read(storage, LEGACY_KEY);
        if (legacy && typeof legacy === 'object') {
          if (legacy.sidebar_width != null && Number.isFinite(Number(legacy.sidebar_width)))
            selected.sidebar_width = Number(legacy.sidebar_width);
          if (legacy.panel_sizes && typeof legacy.panel_sizes === 'object') selected.panel_sizes = copy(legacy.panel_sizes);
          selected = applyLegacyTabs(selected, legacy.tabs, surfaces);
        }
      }
      current = reconcile(selected, surfaces);
      persist();
      return copy(current);
    }

    // Restore saved geometry before surface data arrives, without rewriting the saved arrangement.
    function restore(campaignId) {
      context = null;
      current = null;
      originalLayout = null;
      const value = read(storage, `${CAMPAIGN_PREFIX}${campaignId}`);
      if (!validLayout(value)) return null;
      const surfaces = value.known.filter((id) => typeof id === 'string').map((id) => ({ id }));
      context = { campaignId, rulesetId: null, surfaces };
      current = reconcile(value, surfaces);
      return copy(current);
    }

    function change(command) {
      if (!current || !command || typeof command !== 'object') return snapshot();
      if (command.type === 'move' && current.known.includes(command.panelId) && command.destination) {
        const destination = command.destination;
        if (destination.targetPanelId === command.panelId) return snapshot();
        const sourceWindow = current.windows.find((window) => idsIn(window.tree, []).includes(command.panelId));
        if (destination.windowId !== 'sidebar' && destination.windowId !== 'new' &&
            !current.windows.some((window) => window.id === destination.windowId)) return snapshot();
        if (sourceWindow && sourceWindow.id === destination.windowId &&
            idsIn(sourceWindow.tree, []).length === 1 && !destination.targetPanelId) return snapshot();
        current.sidebar = removePanel(current.sidebar, command.panelId);
        current.windows = current.windows.map((window) => ({ ...window, tree: removePanel(window.tree, command.panelId) }))
          .filter((window) => window.tree);
        current.hidden = current.hidden.filter((id) => id !== command.panelId);
        if (destination.windowId === 'new') {
          const surface = context.surfaces.find((item) => item.id === command.panelId);
          current.windows.push({ id: makeId('window'), title: surface?.title || 'Panels',
            tree: tabs(makeId('tabs'), [command.panelId]),
            ...(destination.placement ? { placement: copy(destination.placement) } : {}) });
        } else if (destination.windowId === 'sidebar') {
          current.sidebar = addAt(current.sidebar, command.panelId, destination, makeId);
        } else {
          const window = current.windows.find((item) => item.id === destination.windowId);
          if (window) window.tree = addAt(window.tree, command.panelId, destination, makeId);
          else current.sidebar = addAt(current.sidebar, command.panelId, {}, makeId);
        }
      } else if (command.type === 'hide' && current.known.includes(command.panelId)) {
        current.sidebar = removePanel(current.sidebar, command.panelId);
        current.windows = current.windows.map((window) => ({ ...window, tree: removePanel(window.tree, command.panelId) }))
          .filter((window) => window.tree);
        if (!current.hidden.includes(command.panelId)) current.hidden.push(command.panelId);
      } else if (command.type === 'show' && current.known.includes(command.panelId)) {
        current.hidden = current.hidden.filter((id) => id !== command.panelId);
        if (![...idsIn(current.sidebar, []), ...current.windows.flatMap((window) => idsIn(window.tree, []))].includes(command.panelId))
          current.sidebar = addAt(current.sidebar, command.panelId, {}, makeId);
      } else if (command.type === 'close-window') {
        const window = current.windows.find((item) => item.id === command.windowId);
        if (window) {
          current.windows = current.windows.filter((item) => item.id !== command.windowId);
          idsIn(window.tree, []).forEach((id) => { current.sidebar = addAt(current.sidebar, id, {}, makeId); });
        }
      } else if (command.type === 'tab') {
        current.sidebar = updateNode(current.sidebar, command.groupId, (node) =>
          node.kind === 'tabs' && node.panels.includes(command.panelId) ? { ...node, active: command.panelId } : node
        );
        current.windows = current.windows.map((window) => ({ ...window, tree: updateNode(window.tree, command.groupId, (node) =>
          node.kind === 'tabs' && node.panels.includes(command.panelId) ? { ...node, active: command.panelId } : node
        ) }));
      } else if (command.type === 'ratio') {
        const ratio = Math.max(0.1, Math.min(0.9, Number(command.ratio)));
        if (Number.isFinite(ratio)) {
          current.sidebar = updateNode(current.sidebar, command.nodeId, (node) => node.kind === 'split' ? { ...node, ratio } : node);
          current.windows = current.windows.map((window) => ({ ...window, tree: updateNode(window.tree, command.nodeId, (node) => node.kind === 'split' ? { ...node, ratio } : node) }));
        }
      } else if (command.type === 'size' && current.known.includes(command.panelId) && Number.isFinite(Number(command.height))) {
        current.panel_sizes[command.panelId] = Math.max(48, Math.round(Number(command.height)));
      } else if (command.type === 'sidebar-width' && Number.isFinite(Number(command.width))) {
        current.sidebar_width = Math.max(180, Math.round(Number(command.width)));
      } else if (command.type === 'inspection') {
        current.inspection = cleanInspection(command.value);
      } else if (command.type === 'placement') {
        const window = current.windows.find((item) => item.id === command.windowId);
        if (window && command.placement && typeof command.placement === 'object') window.placement = copy(command.placement);
      }
      current = reconcile(current, context.surfaces);
      persist();
      return copy(current);
    }

    function preference(action) {
      if (!current) return null;
      if (action === 'save') {
        const preferred = copy(current);
        preferred.views = {};
        preferred.inspection = null;
        write(storage, rulesetKey(), preferred);
        return copy(current);
      }
      if (action === 'mine' || action === 'original') {
        const views = copy(current.views);
        const inspection = copy(current.inspection);
        const source = action === 'mine' ? read(storage, rulesetKey()) : originalLayout;
        current = reconcile(validLayout(source) ? source : originalLayout, context.surfaces);
        current.views = views;
        current.inspection = inspection;
        persist();
      }
      return copy(current);
    }

    function saveView(mapId, view) {
      if (!current || !mapId || !view || typeof view !== 'object') return snapshot();
      const safe = cleanView(view);
      current.views[mapId] = safe;
      persist();
      return copy(safe);
    }

    function view(mapId) {
      return current && current.views[mapId] ? copy(current.views[mapId]) : null;
    }

    function snapshot() { return current ? copy(current) : null; }

    return { select, restore, snapshot, change, preference, saveView, view };
  }

  return { createStore };
});
