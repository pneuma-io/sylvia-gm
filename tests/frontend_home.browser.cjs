// Home in the shipped shell over a synthetic bridge and synthetic campaigns.
// Browser composition proof only: not native window, provider or installed acceptance.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

const LONG_NAME = 'The Exceptionally Long-Named Campaign of the Seventeen Lantern Roads Beyond the Glass Marsh';

function campaigns() {
  const session = (session_id, role = 'dm', status = 'active') => ({ session_id, role, status,
    provider_id: 'synthetic', model_id: 'synthetic-model' });
  const srd = { ruleset_id: 'srd', ruleset_label: 'D&D 5e SRD Lite Alpha-3 Package', campaign_setting_id: 'sandbox',
    campaign_setting_label: 'SRD Sandbox Campaign Setting', availability: 'ready', lifecycle: 'active' };
  const td = { ruleset_id: 'td', ruleset_label: 'Trigger Discipline 0.1.24', campaign_setting_id: 'siren',
    campaign_setting_label: 'Siren Protocol 0.2.0', availability: 'ready', lifecycle: 'active' };
  return [
    { ...srd, campaign_id: 'lantern', display_name: 'The Lantern Road', revision: 41, session_activity_ordinal: 90,
      sessions: [session('lantern-gm')] },
    { ...srd, campaign_id: 'long', display_name: LONG_NAME, revision: 7, session_activity_ordinal: 40,
      sessions: [session('long-gm')] },
    { ...td, campaign_id: 'siren', display_name: 'Siren Night Shift', revision: 12, session_activity_ordinal: 70,
      sessions: [session('siren-gm')] },
    { ...td, campaign_id: 'orphan', display_name: 'Missing Session Check', revision: 2, session_activity_ordinal: 5,
      sessions: [] },
    { campaign_id: 'draft', display_name: 'New Campaign', lifecycle: 'active', availability: 'draft', revision: 1,
      session_activity_ordinal: 60, sessions: [session('draft-builder', 'builder')] },
    { ...srd, campaign_id: 'barrow', display_name: 'Old Barrow Crawl', lifecycle: 'archived', revision: 19,
      session_activity_ordinal: 1, sessions: [session('barrow-gm')] },
  ];
}

async function openHome(browser, viewport, { empty = false } = {}) {
  const page = await browser.newPage({ viewport });
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('https://sylvia-home.test/**', async (route) => {
    const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
    const file = path.join(frontend, name);
    if (!file.startsWith(frontend) || !fs.existsSync(file)) return route.abort();
    let body = fs.readFileSync(file);
    if (name === 'app.js') body = body.toString()
      .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.homeTest = { state, wireControls, initSidebarResize,
        showHome, renderHome, ensureSessionActivity, refreshModelChoices };
        // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css')
      ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-home.test/app.html');
  await page.evaluate(({ list, empty }) => {
    const api = window.homeTest;
    localStorage.setItem('sylvia-gm-home-tutorial-v1', JSON.stringify({ status: 'completed', step: 0 }));
    document.getElementById('connecting-overlay').classList.add('hidden');
    document.getElementById('session-frame').style.display = 'flex';
    window.calls = [];
    const call = (name, value = () => null) => async (...args) => { window.calls.push(name); return value(...args); };
    const campaign = (id) => api.state.campaigns.find((entry) => entry.campaign_id === id);
    const chats = [{ display_name: 'Goblin stat blocks', session: { session_id: 'chat-a', role: 'builder', status: 'active' } }];
    Object.assign(api.state, {
      campaigns: empty ? [] : list, builderChats: empty ? [] : chats, campaignIndexStatus: 'ready',
      modelChoices: [{ provider_id: 'synthetic', provider_name: 'Synthetic', model_id: 'synthetic-model',
        model_name: 'Synthetic model', supported_reasoning_efforts: [], reasoning_mode_defaults: {} }],
      bridge: {
        campaign_index: call('campaign_index', () => api.state.campaigns),
        builder_chat_index: call('builder_chat_index', () => api.state.builderChats),
        snapshot: call('snapshot', (id) => ({ campaign_id: id, revision: campaign(id)?.revision || 1,
          lifecycle: 'active', display_name: campaign(id)?.display_name })),
        archive_campaign: call('archive_campaign', (_op, id) => { campaign(id).lifecycle = 'archived'; }),
        restore_archived_campaign: call('restore_archived_campaign', (_op, id) => { campaign(id).lifecycle = 'active'; }),
        submit_session_input: call('submit_session_input'),
        session_context: call('session_context', () => ({ messages: [] })),
        ready_campaign_catalog: call('ready_campaign_catalog', () => []),
        start_ready_campaign: call('start_ready_campaign'),
        create_builder_chat: call('create_builder_chat', () => {
          const chat = { display_name: 'New chat', session: { session_id: 'chat-new', role: 'builder', status: 'active' } };
          api.state.builderChats.push(chat);
          return chat;
        }),
        create_guided_campaign_draft: call('create_guided_campaign_draft', () => {
          if (window.failDraft) throw new Error('synthetic draft failure');
          api.state.campaigns.push({ campaign_id: 'draft-2', display_name: 'New campaign interview',
            lifecycle: 'active', availability: 'draft', revision: 1, session_activity_ordinal: 95,
            sessions: [{ session_id: 'draft-2-builder', role: 'builder', status: 'active' }] });
          return { campaign_id: 'draft-2', builder_session_id: 'draft-2-builder', construction_revision: 1 };
        }),
      },
    });
    api.wireControls(); api.initSidebarResize(); api.showHome();
  }, { list: campaigns(), empty });
  return { page, errors };
}

const launch = () => chromium.launch({ headless: true, args: ['--disable-gpu'],
  channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
const view = (page) => page.evaluate(() => {
  const { state } = window.homeTest;
  return { view: state.view, campaign: state.activeCampaignId, session: state.activeSessionId };
});
const goHome = async (page) => { await page.locator('#home-btn').click(); assert.equal((await view(page)).view, 'home'); };

test('Home Builder stays available to explain model setup after real model refresh', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await openHome(browser, { width: 1400, height: 900 }, { empty: true });
    await page.evaluate(async () => {
      window.homeTest.state.bridge.model_choices = async () => [];
      await window.homeTest.refreshModelChoices();
    });
    assert.equal(await page.locator('#home-new-campaign-btn').isEnabled(), true);
    assert.equal(await page.locator('#home-build-btn').isEnabled(), true,
      'Home can explain model setup instead of leaving Build Something disabled');
    await page.locator('#home-build-btn').click();
    assert.equal(await page.locator('#settings-overlay').isVisible(), true);
    assert.match(await page.locator('#models-action-status').innerText(), /Configure a provider before starting Builder/);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#home-view').isVisible(), true);
    assert.equal(await page.locator('.campaign-shelf-item').count(), 0);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('Home groups campaigns by Ruleset and Setting and explains each state', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await openHome(browser, { width: 1400, height: 900 });
    await page.evaluate(() => {
      window.homeTest.ensureSessionActivity({ sessionId: 'siren-gm', sessionRole: 'dm', campaignId: 'siren' }).phase = 'working';
      window.homeTest.renderHome();
    });
    assert.equal(await page.locator('#home-recent-title').innerText(), 'The Lantern Road', 'latest activity first');
    assert.equal(await page.locator('.home-continue').getAttribute('data-home-session-id'), 'lantern-gm');
    assert.match(await page.locator('.home-recent-path').innerText(), /D&D 5e SRD Lite\s*·\s*SRD Sandbox/);
    assert.equal(await page.locator('#home-campaign-count').innerText(), '5 ongoing campaigns');
    assert.deepEqual(await page.locator('.ruleset-shelf-heading h3').allInnerTexts(),
      ['D&D 5e SRD Lite', 'Trigger Discipline 0.1.24', 'Campaign Drafts'], 'index order of Rulesets is kept');
    assert.deepEqual(await page.locator('.ruleset-shelf').first().locator('.campaign-shelf-name').allInnerTexts(),
      [LONG_NAME, 'The Lantern Road'], 'names sort within a Setting');
    const orphan = page.locator('.campaign-shelf-item[data-home-campaign-id="orphan"]');
    assert.equal(await orphan.locator('.campaign-shelf-meta').innerText(), 'No saved session');
    assert.notEqual(await orphan.locator('.campaign-shelf-meta').evaluate((node) => getComputedStyle(node).color),
      await page.locator('.campaign-shelf-item[data-home-campaign-id="siren"] .campaign-shelf-meta').evaluate((node) => getComputedStyle(node).color),
      'a missing session is shown as a warning');
    assert.match(await page.locator('.campaign-shelf-item[data-home-campaign-id="siren"]').innerText(), /Working…/);
    assert.match(await page.locator('.campaign-shelf-item[data-home-campaign-id="draft"]').innerText(), /Builder session/);
    assert.equal(await page.locator('#archived-items-count').innerText(), '(1)');

    // Long names wrap to two lines inside their card instead of widening the page.
    const long = await page.locator('.campaign-shelf-item[data-home-campaign-id="long"] .campaign-shelf-name').evaluate((node) => ({
      lines: Math.round(node.getBoundingClientRect().height / parseFloat(getComputedStyle(node).lineHeight)),
      inside: node.getBoundingClientRect().right <= node.closest('.campaign-shelf-item').getBoundingClientRect().right }));
    assert.deepEqual(long, { lines: 2, inside: true });
    assert.equal(await page.locator('.campaign-shelf-item[data-home-campaign-id="long"]').getAttribute('title'), LONG_NAME);

    // A recent campaign without a session cannot be continued and says why.
    await page.evaluate(() => {
      window.homeTest.state.campaigns.find((c) => c.campaign_id === 'orphan').session_activity_ordinal = 99;
      window.homeTest.renderHome();
    });
    assert.equal(await page.locator('.home-continue').isDisabled(), true);
    assert.match(await page.locator('#home-recent .settings-note-warn').innerText(), /no saved session/);

    // An index failure must not read as "no campaigns".
    await page.evaluate(() => {
      Object.assign(window.homeTest.state, { campaigns: [], campaignIndexStatus: 'error' });
      window.homeTest.renderHome();
    });
    assert.equal(await page.locator('#home-recent').isHidden(), true);
    assert.match(await page.locator('#campaign-shelves').innerText(), /couldn’t load your campaign list/);
    assert.doesNotMatch(await page.locator('#campaign-shelves').innerText(), /No ongoing campaigns/);
    await page.evaluate(() => { window.homeTest.state.campaignIndexStatus = 'ready'; window.homeTest.renderHome(); });
    assert.match(await page.locator('#campaign-shelves').innerText(), /No ongoing campaigns yet/);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('Home navigation keeps session selection and drafts, and dialogs cancel cleanly', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await openHome(browser, { width: 1400, height: 900 });
    await page.locator('.home-continue').click();
    assert.deepEqual(await view(page), { view: 'session', campaign: 'lantern', session: 'lantern-gm' });
    assert.equal(await page.locator('#rail-play-btn').getAttribute('aria-current'), 'page');
    await page.waitForFunction(() => !document.getElementById('player-input').disabled);
    await page.locator('#player-input').fill('I raise the lantern.');
    await page.evaluate(() => { document.getElementById('ooc-note').open = true; });
    await page.locator('#ooc-input').fill('Out of character: short session today.');

    await goHome(page);
    assert.equal(await page.locator('#home-view').isVisible(), true);
    assert.equal(await page.locator('#home-btn').getAttribute('aria-current'), 'page');
    await page.locator('.campaign-shelf-item[data-home-campaign-id="siren"]').click();
    assert.deepEqual(await view(page), { view: 'session', campaign: 'siren', session: 'siren-gm' });
    assert.equal(await page.locator('#player-input').inputValue(), '', 'drafts stay with their own session');
    await goHome(page);
    await page.locator('.campaign-shelf-item[data-home-campaign-id="lantern"]').click();
    assert.equal(await page.locator('#player-input').inputValue(), 'I raise the lantern.');
    assert.equal(await page.locator('#ooc-input').inputValue(), 'Out of character: short session today.');
    await goHome(page);
    await page.locator('.home-continue').click();
    assert.equal(await page.locator('#player-input').inputValue(), 'I raise the lantern.', 'repeat navigation keeps it');

    // Settings opens over Home and returns focus to its button.
    await goHome(page);
    await page.locator('#home-settings-btn').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#settings-overlay').isVisible(), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#settings-overlay').isHidden(), true);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'home-settings-btn');

    // Ready campaigns closes without starting anything.
    await page.locator('#home-ready-campaign-btn').click();
    assert.equal(await page.locator('#ready-campaign-dialog').isVisible(), true);
    await page.locator('#ready-campaign-close').click();
    assert.equal(await page.locator('#ready-campaign-dialog').isVisible(), false);
    assert.equal((await view(page)).view, 'home');
    assert.equal(await page.evaluate(() => window.calls.includes('start_ready_campaign')), false);

    // A failed New Campaign stays on Home and says so; a successful one opens its Interview.
    await page.evaluate(() => { window.failDraft = true; });
    await page.locator('#home-new-campaign-btn').click();
    await page.locator('#home-action-status').waitFor();
    assert.match(await page.locator('#home-action-status').innerText(), /Campaign Draft could not be created/);
    assert.equal((await view(page)).view, 'home');
    await page.evaluate(() => { window.failDraft = false; });
    await page.locator('#home-new-campaign-btn').click();
    await page.waitForFunction(() => window.homeTest.state.activeCampaignId === 'draft-2');
    assert.deepEqual(await view(page), { view: 'session', campaign: 'draft-2', session: 'draft-2-builder' });
    await goHome(page);
    assert.match(await page.locator('.ruleset-shelf').last().innerText(), /New campaign interview/,
      'leaving the Interview keeps the draft on Home');
    assert.equal(await page.evaluate(() => window.calls.filter((c) => c === 'create_guided_campaign_draft').length), 2);

    // Without a default model Build Something explains itself in Settings; with one it opens a Builder session.
    await page.locator('#home-build-btn').click();
    assert.equal(await page.locator('#settings-overlay').isVisible(), true);
    assert.match(await page.locator('#models-action-status').innerText(), /Configure a provider before starting Builder/);
    await page.keyboard.press('Escape');
    assert.equal((await view(page)).view, 'home');
    await page.evaluate(() => { window.homeTest.state.providerSettings = {
      default_model: { provider_id: 'synthetic', model_id: 'synthetic-model' } }; });
    await page.locator('#home-build-btn').click();
    await page.waitForFunction(() => window.homeTest.state.activeSessionId === 'chat-new');
    assert.equal(await page.locator('#rail-builder-btn').getAttribute('aria-current'), 'page');
    await goHome(page);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('Home archive, restore, keyboard focus and narrow layout', { timeout: 60000 }, async () => {
  const browser = await launch();
  try {
    const { page, errors } = await openHome(browser, { width: 1400, height: 900 });
    // Keyboard reaches the header actions first (including the tutorial entry points added by the
    // tutorial suite), then the Continue card.
    await page.locator('#home-new-campaign-btn').focus();
    const order = [];
    for (let i = 0; i < 7; i += 1) {
      if (i) await page.keyboard.press('Tab');
      order.push(await page.evaluate(() => document.activeElement.id || document.activeElement.className));
    }
    assert.deepEqual(order, ['home-new-campaign-btn', 'home-ready-campaign-btn', 'home-build-btn',
      'home-settings-btn', 'home-tutorials-btn', 'home-get-started-btn', 'btn btn-primary home-continue']);
    assert.notEqual(await page.locator('.home-continue').evaluate((node) => getComputedStyle(node).outlineStyle), 'none',
      'focused controls show a visible outline');
    await page.evaluate(() => window.homeTest.renderHome());
    assert.equal(await page.locator('.home-continue').evaluate((node) => document.activeElement === node), true,
      'navigation index refresh preserves keyboard focus on Continue');
    const campaignFocus = page.locator('.campaign-shelf-item[data-home-campaign-id="long"]');
    await campaignFocus.focus();
    await page.evaluate(() => window.homeTest.renderHome());
    assert.equal(await campaignFocus.evaluate((node) => document.activeElement === node), true,
      'navigation index refresh preserves keyboard focus on the same campaign');

    const archive = page.locator('[data-archive-campaign="siren"]');
    assert.equal(await archive.getAttribute('aria-label'), 'Archive campaign Siren Night Shift');
    await archive.focus();
    await page.keyboard.press('Enter');
    await page.locator('#archive-action-status').waitFor();
    assert.match(await page.locator('#archive-action-status').innerText(), /“Siren Night Shift” archived/);
    assert.equal(await page.locator('.campaign-shelf-item[data-home-campaign-id="siren"]').count(), 0);
    assert.equal(await page.locator('#home-campaign-count').innerText(), '4 ongoing campaigns');

    await page.locator('#archived-items summary').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#archived-items').evaluate((node) => node.open), true);
    const restore = page.getByRole('button', { name: 'Restore Siren Night Shift' });
    await restore.focus();
    await page.keyboard.press('Enter');
    await page.locator('.campaign-shelf-item[data-home-campaign-id="siren"]').waitFor();
    assert.match(await page.locator('#archive-action-status').innerText(), /“Siren Night Shift” restored/);
    assert.equal(await page.locator('#home-campaign-count').innerText(), '5 ongoing campaigns');
    assert.deepEqual(await page.evaluate(() => window.calls.filter((c) => /archive/.test(c))),
      ['archive_campaign', 'restore_archived_campaign']);

    // Narrow: two-column actions, full-width Continue, one card per row, no sideways scroll, and Home scrolls.
    await page.setViewportSize({ width: 390, height: 844 });
    const layout = await page.evaluate(() => {
      const box = (selector) => document.querySelector(selector).getBoundingClientRect();
      const home = document.getElementById('home-view');
      const cards = [...document.querySelectorAll('.ruleset-shelf:first-child .campaign-shelf-item')].map((n) => n.getBoundingClientRect());
      return {
        actionsSideBySide: Math.abs(box('#home-new-campaign-btn').top - box('#home-ready-campaign-btn').top) < 1,
        continueFullWidth: Math.abs(box('.home-continue').width - box('.home-recent-copy').width) < 2,
        oneCardPerRow: cards[1].top > cards[0].bottom,
        overflow: document.documentElement.scrollWidth > innerWidth || home.scrollWidth > home.clientWidth,
        scrolls: home.scrollHeight > home.clientHeight,
      };
    });
    assert.deepEqual(layout, { actionsSideBySide: true, continueFullWidth: true, oneCardPerRow: true,
      overflow: false, scrolls: true });
    await page.locator('#archived-items summary').scrollIntoViewIfNeeded();
    assert.equal(await page.locator('#archived-items summary').isVisible(), true);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
