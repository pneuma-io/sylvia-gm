// Each Ruleset's Panels render with the look it declares (architecture contract §10.5.1, issue 109): SRD Lite the
// fantasy look of the sizzle reel, Trigger Discipline its tactical look, and an undeclared Ruleset a neutral look.
// The shared PanelSystem carries neither game's look. Real owner projections (SRD Lite combat, delivered TD map) in
// the shipped shell with a synthetic bridge; not native acceptance.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const fixture = (name) => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', name), 'utf8'));
const srd = fixture('srd-lite-combat-projection.json');
const td = fixture('td-delivered-map-controls.json');

async function open(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('https://sylvia-looks.test/**', async (route) => {
    const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
    const file = path.join(frontend, name);
    if (!file.startsWith(frontend) || !fs.existsSync(file)) return route.abort();
    let body = fs.readFileSync(file);
    if (name === 'app.js') body = body.toString()
      .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.looksQA = { state, wireControls, renderRulesetPanelProjection,
        showSessionView, ensureSessionActivity }; // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css'
      : name.endsWith('.png') ? 'image/png' : name.endsWith('.svg') ? 'image/svg+xml' : 'text/html' });
  });
  await page.goto('https://sylvia-looks.test/app.html', { timeout: 30000 });
  await page.evaluate(() => {
    document.getElementById('connecting-overlay').classList.add('hidden');
    document.getElementById('session-frame').style.display = 'flex';
    window.looksQA.wireControls();
  });
  return { page, errors };
}

// Show a projection as a fresh workspace with its focused actor inspected.
const show = (page, source) => page.evaluate((projection) => {
  projection = JSON.parse(JSON.stringify(projection));
  delete projection.layout;
  const api = window.looksQA;
  const actor = projection.player_control?.focused_actor_id || projection.player_control?.actors?.[0]?.id;
  Object.assign(api.state, { view: 'session', activeCampaignId: projection.campaign_id, activeSessionId: 'looks-gm', activeSessionRole: 'dm',
    activeSnapshot: { revision: projection.revision },
    campaigns: [{ campaign_id: projection.campaign_id, display_name: 'Looks', lifecycle: 'active', sessions: [{ session_id: 'looks-gm', role: 'dm', status: 'active' }] }],
    bridge: { snapshot: async () => ({ revision: projection.revision }), campaign_panels: async () => projection,
      player_action_availability: async (c) => ({ revision: c.expected_revision, results: c.requests.map(() => ({ available: true })) }) } });
  api.state.characterInspection = { campaignId: projection.campaign_id, rulesetId: projection.ruleset_id || null, id: actor, unavailableId: null, expanded: [] };
  api.ensureSessionActivity({ sessionId: 'looks-gm', sessionRole: 'dm', campaignId: projection.campaign_id });
  api.showSessionView();
  api.renderRulesetPanelProjection(projection);
}, source);

const variant = (source, edit) => { const copy = JSON.parse(JSON.stringify(source)); edit(copy); return copy; };
// An SRD character who casts nothing (the map declares no cast actions), out of combat so Party is the shown tab.
const nonCaster = (source) => variant(source, (projection) => {
  for (const surface of projection.surfaces) {
    if (Array.isArray(surface.actions)) surface.actions = surface.actions.filter((action) => action.intent !== 'cast_spell');
    if (surface.data?.combat) surface.data.combat.encounter_active = false;
  }
});

// What the page shows: the look, each Panel's PanelWidget style and the computed values that differ between looks.
const read = (page) => page.evaluate(() => {
  const style = (selector, property) => { const node = document.querySelector(selector); return node ? getComputedStyle(node).getPropertyValue(property) : null; };
  const widgets = Object.fromEntries(Array.from(document.querySelectorAll('[data-panel-id][data-panel-widget]'),
    (node) => [node.dataset.panelId, node.dataset.panelWidget]));
  return {
    look: document.documentElement.dataset.panelLook,
    widgets,
    tabEdge: style('.ruleset-panel-tab[aria-selected="true"]', 'box-shadow'),
    character: document.querySelector('.ruleset-character-panel .character-hero')?.className || null,
    plateClip: style('.op-art', 'clip-path'),
    barSlant: style('.suite-bar i', 'transform'),
    partyMedal: Boolean(document.querySelector('.ruleset-roster-panel .squad-card .suite-medal')),
    partyBar: Boolean(document.querySelector('.ruleset-roster-panel .squad-card .suite-bar')),
    spellBook: Boolean(document.querySelector('.grim-book')),
    firstAbility: document.querySelector('.grim-rune small')?.textContent || null,
    firstTier: document.querySelector('.grim-tier small')?.textContent || null,
    nameFont: style('.ladder-now strong', 'font-family'),
    partyFont: style('.ruleset-roster-panel .sq-name strong', 'font-family'),
  };
});

const launch = () => chromium.launch({ headless: true, args: ['--disable-gpu'],
  channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });

test('SRD Lite panels use the fantasy look for casters and non-casters alike', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await open(browser);
    await show(page, srd);
    const caster = await read(page);
    assert.equal(caster.look, 'fantasy');
    assert.deepEqual(caster.widgets, { 'dnd-encounter': 'initiative-ladder', 'srd-coordinate-map': 'battlefield', 'dnd-character': 'character-sheet' },
      'every shown Panel is tagged with its declared PanelWidget style');
    assert.match(caster.tabEdge, /rgb\(226, 180, 92\)/, 'the active tab carries the declared gold accent');
    assert.match(caster.character, /\bgrimoire\b/);
    assert.equal(caster.spellBook, true);
    assert.equal(caster.firstAbility, 'STR', 'abilities follow the declared order');
    assert.equal(caster.firstTier, 'I', 'spell slot tiers read as Roman numerals');
    assert.match(caster.nameFont, /Georgia/, 'names are set in the serif heading type');

    await show(page, nonCaster(srd));
    const fighter = await read(page);
    assert.match(fighter.character, /\bgrimoire\b/, 'a non-caster gets the same fantasy sheet');
    assert.doesNotMatch(fighter.character, /\boperator\b/, 'never Trigger Discipline\'s Operator plate');
    assert.equal(fighter.plateClip, null);
    assert.equal(fighter.spellBook, false, 'no spell book without declared casts');
    assert.equal(fighter.widgets['dnd-party'], 'adventurer-cards');
    assert.equal(fighter.partyMedal, true);
    assert.match(fighter.partyFont, /Georgia/);
    assert.equal(fighter.partyBar, false, 'no slanted tactical HP bar on an SRD party card');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

// On an SRD adventurer card the party role shares the Details row with the Details control. With a wider font
// (Linux desktops usually give DejaVu Sans) the two ran together as "DetailsPlayer Character". They need clear
// space between them, and the role stays whole inside the card.
test('SRD party card keeps Details and the party role apart', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await open(browser);
    await show(page, nonCaster(srd));
    // In the installed app's default arrangement the Party panel is short, so its cards form the compact strip
    // (the roster content is a size container; the strip applies below 229px). Size it so here, at the app's
    // own panel width.
    await page.evaluate(() => {
      const content = document.querySelector('[data-panel-id="dnd-party"] .ruleset-panel-content');
      Object.assign(content.style, { height: '200px', maxHeight: '200px', width: '225px' });
    });
    const card = page.locator('[data-panel-id="dnd-party"] .squad-card').first();
    await card.waitFor();
    await page.evaluate(() => new Promise(requestAnimationFrame));
    const geometry = await card.evaluate((node) => {
      const text = (element) => { const range = document.createRange(); range.selectNodeContents(element);
        const r = range.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; };
      const role = node.querySelector('.panel-unit-role');
      const box = node.getBoundingClientRect();
      return { card: { left: box.left, right: box.right }, role: text(role), roleText: role.textContent,
        details: text(node.querySelector('.roster-inspection > summary')),
        roleCut: role.scrollWidth > role.clientWidth + 0.5, roleEllipsis: getComputedStyle(role).textOverflow,
        roleFontSize: parseFloat(getComputedStyle(role).fontSize),
        strip: Boolean(node.closest('.squad-list')) };
    });
    assert.equal(geometry.roleText, 'Player Character');
    const sameRow = Math.min(geometry.role.bottom, geometry.details.bottom) - Math.max(geometry.role.top, geometry.details.top) > 0.5;
    if (sameRow) {
      const gap = geometry.role.left - geometry.details.right;
      // At least one character's height apart, so the gap holds when a desktop draws the labels larger.
      assert.ok(gap >= geometry.roleFontSize,
        `Details and the role need space between them (gap ${gap.toFixed(1)}px): ${JSON.stringify(geometry)}`);
    }
    assert.ok(geometry.role.left >= geometry.card.left - 0.5 && geometry.role.right <= geometry.card.right + 0.5,
      `the role stays inside the card: ${JSON.stringify(geometry)}`);
    assert.equal(geometry.roleCut, false, `the role is shown whole on the card: ${JSON.stringify(geometry)}`);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

// An SRD Fighter: no casts, no spell slots, Hit Die and Second Wind as other resources, no weapon set in hand.
const fighter = (source) => variant(nonCaster(source), (projection) => {
  for (const surface of projection.surfaces) {
    const hero = surface.data?.entities?.hero;
    if (hero) Object.assign(hero, { name: 'Tavin Bell', hp: 12, max_hp: 12, resources: { hit_die: 1, second_wind: 2 }, weapon_sets: {} });
    for (const participant of surface.data?.participants || []) if (participant.id === 'hero')
      Object.assign(participant.data, { name: 'Tavin Bell', background: 'soldier', classes: [{ id: 'fighter', level: 1 }], level: 1 });
  }
});

// The fantasy character sheet was wider than its panel for a Fighter: the HP, AC and Speed row does not wrap, and the
// sheet's single grid column grew to that row's width instead of the panel's, so the right edge was cut off.
test('SRD character sheet stays inside its panel at every game-panel width', { timeout: 90000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await open(browser);
    for (const [who, source] of [['fighter', fighter(srd)], ['caster', srd]]) {
      await show(page, source);
      // The game panels' column, from the divider's minimum (220px) through the default to a wide setting.
      for (const width of [220, 250, 280, 360]) {
        const outside = await page.evaluate((pixels) => {
          document.getElementById('right-sidebar').style.width = `${pixels}px`;
          const content = document.querySelector('[data-panel-id="dnd-character"] .ruleset-panel-content');
          const sheet = content?.querySelector('.character-hero.grimoire');
          if (!sheet) return ['no fantasy sheet shown'];
          const box = content.getBoundingClientRect(), style = getComputedStyle(content);
          const left = box.left + parseFloat(style.borderLeftWidth) + parseFloat(style.paddingLeft);
          const right = box.right - parseFloat(style.borderRightWidth) - parseFloat(style.paddingRight);
          const found = [];
          if (content.scrollWidth > content.clientWidth + 0.5) found.push(`panel scrolls sideways (${content.scrollWidth} > ${content.clientWidth})`);
          for (const node of [sheet, ...sheet.querySelectorAll('*')]) {
            const r = node.getBoundingClientRect();
            if (r.width <= 1 || r.height <= 1) continue; // nothing to see (a label kept for screen readers only)
            if (r.left < left - 0.5 || r.right > right + 0.5) found.push(`${node.tagName.toLowerCase()}.${String(node.className?.baseVal ?? node.className).trim().replace(/\s+/g, '.')}` +
              ` "${(node.textContent || '').trim().slice(0, 24)}" ${r.left.toFixed(1)}–${r.right.toFixed(1)} outside ${left.toFixed(1)}–${right.toFixed(1)}`);
            if (node.scrollWidth > node.clientWidth + 0.5 && getComputedStyle(node).overflowX !== 'visible') found.push(`${node.tagName.toLowerCase()} cuts its own text`);
          }
          return found;
        }, width);
        assert.deepEqual(outside, [], `${who} at a ${width}px game-panel column: no part of the sheet is outside the panel`);
      }
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('Trigger Discipline panels keep the tactical look', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await open(browser);
    await show(page, td);
    const tactical = await read(page);
    assert.equal(tactical.look, 'tactical');
    assert.equal(tactical.widgets['operations.party'], 'squad-strip');
    assert.equal(tactical.widgets['operations.character'], 'operator');
    assert.equal(tactical.widgets['operations.map'], 'tactical-grid');
    assert.match(tactical.tabEdge, /rgb\(179, 157, 255\)/);
    assert.match(tactical.character, /\boperator\b/);
    assert.match(tactical.plateClip, /^polygon/, 'the cut-corner portrait plate');
    assert.notEqual(tactical.barSlant, 'none', 'the slanted squad HP bar');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('a Ruleset with no declared look gets the neutral look, and switching Rulesets leaves nothing behind', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await open(browser);
    await show(page, srd);
    assert.equal((await read(page)).look, 'fantasy');
    for (const rulesetId of ['example.pathfinder', null]) {
      await show(page, variant(nonCaster(srd), (projection) => { projection.ruleset_id = rulesetId; }));
      const neutral = await read(page);
      assert.equal(neutral.look, 'neutral', String(rulesetId));
      assert.equal(neutral.widgets['dnd-party'], 'roster');
      assert.equal(neutral.widgets['dnd-character'], 'sheet');
      assert.match(neutral.tabEdge, /rgb\(157, 181, 212\)/, 'the neutral accent, not either game\'s');
      assert.equal(neutral.plateClip, 'none', 'no tactical cut corner');
      assert.ok(['none', 'matrix(1, 0, 0, 1, 0, 0)'].includes(neutral.barSlant), `no tactical slant (${neutral.barSlant})`);
      assert.doesNotMatch(neutral.partyFont, /Georgia/, 'no fantasy serif');
    }
    await show(page, td);
    assert.equal((await read(page)).look, 'tactical');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
