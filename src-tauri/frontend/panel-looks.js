// PanelSystem looks (architecture contract §10.5.1). A Ruleset's look is data: a PanelTheme (a named base plus a closed
// set of colour and type values) and, per renderer, the name of the built-in PanelWidget style that draws its Panels.
// Sylvia ships every style; nothing a Ruleset declares is run or injected. First step: the declarations live here,
// keyed by Ruleset identity, in the version-1 shape a Ruleset package will later carry as ui/panel-look.json.
(function () {
  'use strict';

  // Named bases: default PanelTheme values and default PanelWidget styles. Values a base does not let a Ruleset
  // declare (ornament such as plate shapes or map ground) are the base's CSS, keyed by data-panel-look.
  const BASES = Object.freeze({
    neutral: {
      theme: { accent: '#9db5d4', ally: '#4fd1b5', hostile: '#ff7b6e', resource: '#b39dff', heading: 'sans' },
      widgets: { party: 'roster', unit_list: 'sheet', encounter: 'turn-list', coordinate_map: 'plain', tactical_grid: 'plain' },
    },
    tactical: {
      theme: { accent: '#b39dff', ally: '#4fd1b5', hostile: '#ff7b6e', resource: '#b39dff', heading: 'sans' },
      widgets: { party: 'squad-strip', unit_list: 'operator', encounter: 'turn-list', coordinate_map: 'tactical-grid', tactical_grid: 'tactical-grid' },
    },
    fantasy: {
      theme: { accent: '#e2b45c', ally: '#4fd1b5', hostile: '#ff7b6e', resource: '#c9b6ff', heading: 'serif' },
      widgets: { party: 'adventurer-cards', unit_list: 'character-sheet', encounter: 'initiative-ladder', coordinate_map: 'battlefield', tactical_grid: 'battlefield' },
    },
  });

  // Every built-in PanelWidget style by renderer. A renderer not listed has one style, `plain`.
  const STYLES = Object.freeze({
    party: ['roster', 'squad-strip', 'adventurer-cards'],
    unit_list: ['sheet', 'operator', 'character-sheet'],
    encounter: ['turn-list', 'initiative-ladder'],
    coordinate_map: ['plain', 'tactical-grid', 'battlefield'],
    tactical_grid: ['plain', 'tactical-grid', 'battlefield'],
  });

  // Fields a style accepts, each a short list of identities (for example the order ability scores are shown in).
  const STYLE_FIELDS = Object.freeze({ 'character-sheet': ['abilities'] });

  const HEADINGS = Object.freeze({ sans: 'var(--font-sans)', serif: 'var(--font-serif)', condensed: 'var(--font-number)' });
  const COLOURS = ['accent', 'ally', 'hostile', 'resource'];

  // The declarations. Keys are Ruleset identities as projected in `ruleset_id`.
  const RULESET_LOOKS = Object.freeze({
    // SRD (D&D 5e): the published sizzle reel's fantasy look.
    'sylvia.srd-lite.bg3': {
      version: 1,
      theme: { base: 'fantasy' },
      widgets: {
        unit_list: { style: 'character-sheet', fields: { abilities: ['strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma'] } },
      },
    },
    // Trigger Discipline: the tactical look it has had since the panel suite.
    'pneuma-io.trigger-discipline': { version: 1, theme: { base: 'tactical' }, widgets: {} },
  });

  const identity = /^[a-z][a-z0-9_]{0,63}$/;

  // Relative luminance of a #rrggbb colour (WCAG 2).
  function luminance(hex) {
    const channel = (offset) => {
      const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
      return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
  }

  // A declared colour must be #rrggbb and read at 3:1 or better on the darkest panel card of any session theme.
  function readableColour(value) {
    if (typeof value !== 'string' || !/^#[0-9a-f]{6}$/i.test(value)) return false;
    const card = luminance('#252038');
    return (luminance(value.toLowerCase()) + 0.05) / (card + 0.05) >= 3;
  }

  // The complete look for a Ruleset: its declaration validated over its base, or the neutral look.
  function resolve(rulesetId) {
    const declared = typeof rulesetId === 'string' && Object.prototype.hasOwnProperty.call(RULESET_LOOKS, rulesetId)
      ? RULESET_LOOKS[rulesetId] : null;
    return { rulesetId: declared ? rulesetId : null, ...fromDeclaration(declared) };
  }

  // Validate one version-1 declaration over its base. Anything unknown or unreadable falls back to the base, and a
  // declaration that is not version 1 is the neutral look.
  function fromDeclaration(declared) {
    const valid = Boolean(declared && typeof declared === 'object' && declared.version === 1 && declared.theme && typeof declared.theme === 'object');
    const baseName = valid && Object.prototype.hasOwnProperty.call(BASES, declared.theme.base) ? declared.theme.base : 'neutral';
    const base = BASES[baseName];
    const theme = { ...base.theme };
    if (valid && baseName === declared.theme.base) {
      for (const key of COLOURS) if (readableColour(declared.theme[key])) theme[key] = declared.theme[key].toLowerCase();
      if (Object.prototype.hasOwnProperty.call(HEADINGS, declared.theme.heading)) theme.heading = declared.theme.heading;
    }
    const widgets = {};
    for (const [renderer, styles] of Object.entries(STYLES)) {
      const entry = valid && declared.widgets && declared.widgets[renderer];
      const style = typeof entry === 'string' ? entry : entry && typeof entry === 'object' ? entry.style : null;
      const chosen = styles.includes(style) ? style : base.widgets[renderer];
      const fields = {};
      for (const field of STYLE_FIELDS[chosen] || []) {
        const value = entry && typeof entry === 'object' && entry.fields ? entry.fields[field] : null;
        if (Array.isArray(value) && value.length <= 16 && value.every((item) => typeof item === 'string' && identity.test(item))) fields[field] = value.slice();
      }
      widgets[renderer] = { style: chosen, fields };
    }
    return { base: baseName, theme, widgets };
  }

  // The style a renderer draws with in a resolved look (`plain` for renderers with one style).
  function widget(look, renderer) {
    return (look && look.widgets[renderer]) || { style: 'plain', fields: {} };
  }

  // Apply a resolved look to a document root: the base name for its CSS and the PanelTheme values as custom properties.
  function apply(root, look) {
    root.dataset.panelLook = look.base;
    for (const key of COLOURS) root.style.setProperty(`--look-${key}`, look.theme[key]);
    root.style.setProperty('--look-heading-font', HEADINGS[look.theme.heading]);
  }

  globalThis.SylviaPanelLooks = Object.freeze({ resolve, fromDeclaration, widget, apply, BASES, STYLES, RULESET_LOOKS });
})();
