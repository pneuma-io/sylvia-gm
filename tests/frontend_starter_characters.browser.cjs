// Shipped Interview controls, with a synthetic bridge. Optional owner-exported choices support evidence.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const authored = JSON.parse(fs.readFileSync(path.join(__dirname,
  '../src/sylvia_gm/campaign/srd-starters.json'), 'utf8'));
const options = process.env.SYLVIA_STARTER_OPTIONS
  ? JSON.parse(fs.readFileSync(process.env.SYLVIA_STARTER_OPTIONS, 'utf8'))
  : { starting_party: { format_version: 1, player_character: { mode: 'compatible' },
    required_companions: [], custom_companions: { minimum: 0, maximum: 0 } },
  player_archetypes: authored.map(row => ({ id: `fixture-${row.id}`, name: row.participant.name,
    summary: row.summary, reusable: true, builtin_starter: true, campaign_ready: true,
    content_sha256: '1'.repeat(64), starter_preview: { authored_profile: row.authored_profile,
      participant: row.participant, entity_state: { ...row.entity_state, armor_class: 12, speed: 30 } } })),
  companions: [] };

for (const width of [1280, 640]) test(`Starter chooser, preview and editable copy at ${width}px`, async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('https://sylvia-starter.test/**', async route => {
      const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
      const file = path.resolve(frontend, name);
      if (!file.startsWith(frontend + path.sep) || !fs.existsSync(file)) return route.abort();
      let body = fs.readFileSync(file);
      if (name === 'app.js') body = body.toString().replace(
        "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
        .replace('  // ---- Public API ----',
          'window.starterQA = { state, renderCampaignPartyOptions, initStarterCharacterControls, createHostBridge };\n  // ---- Public API ----');
      await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
        name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
    });
    await page.goto('https://sylvia-starter.test/');
    await page.evaluate((options) => {
      const { state, renderCampaignPartyOptions, initStarterCharacterControls, createHostBridge } = window.starterQA;
      document.getElementById('connecting-overlay').style.display = 'none';
      document.getElementById('session-frame').style.display = 'flex';
      document.getElementById('home-view').hidden = true;
      document.getElementById('ctx-campaign').textContent = 'Starter character test';
      document.getElementById('chronology').innerHTML = '';
      const panel = document.getElementById('construction-panel');
      panel.hidden = false;
      panel.classList.add('guided-construction');
      document.getElementById('chronology').appendChild(panel);
      for (const section of panel.querySelectorAll('[data-construction-step]')) {
        section.hidden = section.id !== 'rift-construction-fields';
      }
      state.activeSessionId = 'starter-interview'; state.activeCampaignId = 'starter-campaign';
      state.view = 'session'; state.activeSessionRole = 'builder';
      state.constructionDraftId = 'starter-campaign'; state.constructionRevision = 4;
      window.launches = [];
      window.__TAURI__ = { core: { invoke: (method, request) => {
        if (method === 'start_guided_creation_subflow') window.launches.push(request);
        return new Promise(() => {}); // The real owner/Builder route has separate Python proof.
      } } };
      state.bridge = createHostBridge();
      renderCampaignPartyOptions(options);
      initStarterCharacterControls();
    }, options);
    const chooser = page.locator('#rift-archetype');
    assert.equal(await chooser.inputValue(), '');
    assert.equal(await page.locator('#starter-character-preview').isVisible(), false);
    assert.equal(await page.getByRole('button', { name: 'Create New Player Character', exact: true }).isVisible(), true);
    const rows = options.player_archetypes.filter(row => row.builtin_starter);
    assert.equal(rows.length, 12);
    for (const row of rows) {
      await chooser.selectOption(row.id);
      assert.match(await page.locator('#starter-character-heading').textContent(), /Level 1/);
      assert.match(await page.locator('#starter-character-heading').textContent(), new RegExp(row.name));
      assert.equal(await page.locator('#starter-character-profile').textContent(), row.starter_preview.authored_profile);
      const details = await page.locator('#starter-character-data').textContent();
      assert.match(details, /Ability scores:/);
      assert.match(details, /Equipment:/);
      assert.match(details, /Spells:/);
      assert.doesNotMatch(details, /entity_state|weapon_sets|resource_maxima/);
      assert.equal(await page.locator('#delete-reusable-player-btn').isVisible(), false);
    }
    await chooser.selectOption(rows[0].id);
    await page.getByText('Character preview', { exact: true }).click();
    assert.equal(await page.locator('#starter-character-profile').isVisible(), true);
    assert.equal(await page.locator('body').evaluate(el => el.scrollWidth > innerWidth), false);
    if (process.env.SYLVIA_STARTER_SCREENSHOTS) {
      fs.mkdirSync(process.env.SYLVIA_STARTER_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: path.join(process.env.SYLVIA_STARTER_SCREENSHOTS,
        `starter-preview-${width}.png`), fullPage: true });
    }
    await page.getByRole('button', { name: 'Edit a copy in Builder', exact: true }).click();
    const launches = await page.evaluate(() => window.launches);
    assert.equal(launches.length, 1);
    assert.equal(launches[0].purpose, 'player_character');
    assert.equal(launches[0].characterArtifactId, rows[0].id);
    assert.equal(launches[0].characterArtifactSha256, rows[0].content_sha256);
    assert.equal(launches[0].visibleAnswer, `Edit a copy of ${rows[0].name}`);
    await page.evaluate((options) => {
      const fixed = { id: 'required-protagonist', name: 'Required protagonist', summary: 'Fixed by Plan',
        campaign_ready: true, source_ref: { id: 'required-protagonist', path: 'characters/fixed.json' } };
      window.starterQA.renderCampaignPartyOptions({ ...options,
        player_archetypes: [...options.player_archetypes, fixed],
        starting_party: { ...options.starting_party, player_character: {
          mode: 'fixed', character: fixed.source_ref,
        } },
      });
    }, options);
    assert.equal(await chooser.inputValue(), 'required-protagonist');
    assert.equal(await chooser.isDisabled(), true);
    assert.equal(await page.locator('#starter-character-preview').isVisible(), false);
    assert.equal(await page.locator('#rift-create-player').isVisible(), false);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
