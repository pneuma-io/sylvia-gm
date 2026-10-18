// Explicit browser composition check using the shipped shell and synthetic session data.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

test('composer and sidebar controls remain usable in the shipped shell', { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 850 } });
    page.setDefaultTimeout(3000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('https://sylvia-shell.test/**', async route => {
      const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
      if (!['app.html', 'app.css', 'app.js', 'panel-workspace.js', 'shot-playback.js',
        'panel-host.js', 'assets/sylvia-portrait.png'].includes(name)) return route.abort();
      let body = fs.readFileSync(path.join(frontend, name));
      if (name === 'app.js') body = body.toString().replace(
        "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
        .replace('  // ---- Public API ----', `window.shellTest = { state, wireControls, initSidebarResize,
          renderSessionModelState, renderSidebarState, refreshSessionInputAdmission, ensureSessionActivity,
          showModelGuidanceWarning, renderBuilderChatRail, renderBuilderHistory, attachBuilderSources };
          // ---- Public API ----`);
      await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
        name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
    });
    await page.goto('https://sylvia-shell.test/');
    await page.evaluate(() => {
      const { state, wireControls, initSidebarResize, renderSessionModelState,
        refreshSessionInputAdmission, ensureSessionActivity } = window.shellTest;
      document.getElementById('connecting-overlay').style.display = 'none';
      document.getElementById('session-frame').style.display = 'flex';
      state.view = 'session'; state.activeSessionId = 'synthetic-gm'; state.activeSessionRole = 'dm';
      state.activeCampaignId = 'synthetic';
      state.campaigns = [{ campaign_id: 'synthetic', lifecycle: 'active', sessions: [
        { session_id: 'synthetic-gm', role: 'dm', status: 'active', provider_id: 'test', model_id: 'sol' }] }];
      state.bridge = { submit_session_input() {} };
      state.modelChoices = [{ provider_id: 'test', provider_name: 'Synthetic model connection',
        model_id: 'sol', model_name: 'GPT-5.6 Sol', supported_reasoning_efforts: ['low', 'high'],
        reasoning_mode_defaults: { dm: 'high' } }];
      document.getElementById('chat-model-choice').innerHTML = '<option value="0">Sol</option>';
      ensureSessionActivity({ sessionId: 'synthetic-gm', sessionRole: 'dm', campaignId: 'synthetic' });
      wireControls(); initSidebarResize();
      renderSessionModelState({ model_state: { current: { provider_id: 'test', model_id: 'sol' } },
        reasoning_state: { current: null, once: null, scope: 'mode_default', effective_request: 'high',
          provider_id: 'test', model_id: 'sol', valid: true, supported: true } });
      refreshSessionInputAdmission();
      document.getElementById('session-title').textContent = 'The Lantern Road — a synthetic campaign with a long name';
      document.getElementById('session-status-text').textContent = 'DM session';
      document.getElementById('chronology').innerHTML = '<div class="story-card talk"><p>The lanterns glow along the road.</p></div>';
    });
    await page.evaluate(() => {
      const report = { status: 'failed', success: false, scenarios: [{ id: 'cost', steps: [{
        step: 0, failures: [{ subject: 'state', path: ['charge'], expected: 3,
          actual: '<script>unsafe</script>' }],
      }] }] };
      document.getElementById('chronology').insertAdjacentHTML('beforeend',
        window.SylviaGMFrontend.toolResultMarkup({ tool_name: 'test_ruleset_draft', result: report }, ''));
    });
    const failedTests = page.locator('#chronology .result-card');
    assert.match(await failedTests.innerText(), /Ruleset tests failed/i);
    const failureDetails = failedTests.locator('details pre');
    assert.equal(await failureDetails.isVisible(), false);
    await failedTests.getByText('Test details', { exact: true }).click();
    assert.equal(await failureDetails.isVisible(), true);
    assert.match(await failureDetails.innerText(), /Expected: 3\nActual: "<script>unsafe<\/script>"/);
    assert.equal(await failedTests.locator('script').count(), 0);
    await failedTests.evaluate(el => el.remove());
    assert.equal(await page.locator('#right-sidebar #new-campaign-btn, #right-sidebar #new-no-rules-btn').count(), 0);
    assert.equal(await page.locator('#home-new-campaign-btn').count(), 1);
    assert.equal(await page.locator('#construction-package option[value=""]').count(), 1,
      'Home campaign setup retains its existing narrative-play choice');
    const box = selector => page.locator(selector).boundingBox();
    assert.equal((await box('.session-topbar')).height, 48);
    const chat = await box('.chronology-col'), right = await box('#right-sidebar');
    assert.equal(chat.x + chat.width, right.x, 'no gap before the game panels');
    assert.ok((await box('#toggle-left-sidebar-btn')).x >= (await box('#left-sidebar')).width);
    assert.ok((await box('#toggle-right-sidebar-btn')).x < right.x);
    assert.equal(await page.locator('#stop-btn').isVisible(), false);
    const modelGroup = await box('.composer-model-controls'), send = await box('#send-btn');
    assert.ok(Math.abs(send.x - modelGroup.x - modelGroup.width - 6) <= 1,
      'model and effort remain immediately beside Send');
    const divider = await box('#sidebar-resize-handle');
    assert.equal(divider.y, right.y, 'resize highlight starts alongside the sidebar header');
    assert.equal(await page.evaluate(({ x, y }) => document.elementFromPoint(x, y).id,
      { x: divider.x + 4, y: divider.y + 8 }), 'sidebar-resize-handle');
    await page.locator('#player-input').fill('Preserve this draft while resizing.');
    const beforeResize = await box('#player-input'), composerBottom = await box('.input-composer');
    const grip = await box('#composer-resize-handle');
    await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
    await page.mouse.down();
    await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2 - 60, { steps: 5 });
    await page.mouse.up();
    const expanded = await box('#player-input'), afterComposer = await box('.input-composer');
    assert.ok(Math.abs(expanded.height - beforeResize.height - 60) <= 1);
    assert.ok(Math.abs(expanded.y - beforeResize.y + 60) <= 1, 'the dragged top edge moves upward');
    assert.ok(Math.abs(afterComposer.y + afterComposer.height - composerBottom.y - composerBottom.height) <= 1);
    assert.equal(await page.locator('#player-input').inputValue(), 'Preserve this draft while resizing.');
    assert.equal(await page.evaluate(() => document.body.style.userSelect), '');
    await page.locator('#composer-resize-handle').focus();
    await page.keyboard.press('Home');
    assert.equal((await box('#player-input')).height, 58);
    await page.keyboard.press('ArrowUp');
    assert.equal((await box('#player-input')).height, 74);
    await page.keyboard.press('End');
    assert.equal((await box('#player-input')).height, 200);
    await page.keyboard.press('Home');
    await page.locator('#chat-model-picker-btn').click();
    assert.ok((await box('#chat-model-popover')).y < (await box('#player-input')).y);
    await page.keyboard.press('Escape');
    await page.locator('#chat-reasoning-btn').click();
    assert.equal(await page.locator('#chat-reasoning-popover').isVisible(), true);
    await page.keyboard.press('Escape');
    await page.locator('#ooc-note summary').click();
    await page.locator('#ooc-input').fill('Synthetic OOC note');
    await page.keyboard.press('Escape');
    await page.locator('#player-input').click();
    await page.locator('#player-input').fill('First line');
    await page.keyboard.press('Shift+Enter');
    await page.keyboard.type('Second line');
    assert.equal(await page.locator('#player-input').inputValue(), 'First line\nSecond line');
    await page.locator('#session-menu summary').click();
    assert.equal(await page.locator('#compact-context-btn').isVisible(), true);
    await page.keyboard.press('Escape');
    await page.locator('#toggle-left-sidebar-btn').click();
    await page.locator('#toggle-right-sidebar-btn').click();
    assert.equal((await box('.chronology-col')).width, 1280, 'collapsed sidebars leave no rails');
    assert.equal(await page.locator('#home-btn').isVisible(), true);
    await page.evaluate(() => window.shellTest.showModelGuidanceWarning(
      { status: 'unrated', minimum_tier_id: 'synthetic-minimum' },
      { provider_id: 'test', model_id: 'sol' }));
    assert.equal(await page.locator('#model-guidance-warning').isVisible(), true,
      'model guidance must remain visible independently of hidden lifecycle controls and collapsed sidebars');
    assert.equal(await page.locator('#continue-model-guidance-btn').isEnabled(), true);
    await page.locator('#dismiss-model-guidance-btn').click();
    assert.equal(await page.locator('#model-guidance-warning').isVisible(), false);
    assert.equal(await page.evaluate(() => window.shellTest.state.pendingModelGuidance), null);
    await page.setViewportSize({ width: 720, height: 750 });
    assert.equal(await page.locator('#right-sidebar #new-campaign-btn, #right-sidebar #new-no-rules-btn').count(), 0);
    await page.evaluate(() => {
      document.getElementById('builder-input-options').hidden = false;
      document.getElementById('chat-model-picker-btn').textContent = 'An unusually long configured model label for the synthetic Builder';
      document.getElementById('chat-reasoning-btn').textContent = 'Reasoning: Extra high effort';
    });
    await page.setViewportSize({ width: 420, height: 430 });
    await page.locator('#composer-resize-handle').focus();
    await page.keyboard.press('End');
    assert.ok((await box('#player-input')).height < 200, 'short windows retain conversation space');
    assert.ok((await box('#chronology')).height >= 96);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 420);
    const narrowSend = await box('#send-btn'), narrowModels = await box('.composer-model-controls');
    assert.ok(Math.abs(narrowSend.x - narrowModels.x - narrowModels.width - 6) <= 1);
    assert.ok(narrowSend.x + narrowSend.width <= 420);
    await page.locator('#chat-model-picker-btn').click();
    assert.equal(await page.locator('#chat-model-popover').isVisible(), true);
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 720, height: 750 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 720);
    await page.locator('#chat-model-picker-btn').click();
    const popover = await box('#chat-model-popover');
    assert.ok(popover.x >= 0 && popover.x + popover.width <= 720);
    await page.keyboard.press('Escape');
    await page.evaluate(() => {
      window.shellTest.state.sessionActivities.get('synthetic-gm').phase = 'working';
      window.shellTest.refreshSessionInputAdmission();
    });
    assert.equal(await page.locator('#send-btn').isVisible(), false);
    assert.equal(await page.locator('#stop-btn').isVisible(), true);
    assert.deepEqual(errors, []);
    if (process.env.SYLVIA_SHELL_SCREENSHOT) {
      await page.setViewportSize({ width: 1280, height: 850 });
      await page.locator('#toggle-left-sidebar-btn').click();
      await page.locator('#toggle-right-sidebar-btn').click();
      await page.screenshot({ path: process.env.SYLVIA_SHELL_SCREENSHOT });
    }
    await page.evaluate(() => {
      const state = window.shellTest.state;
      state.builderChats = [{ display_name: 'Synthetic saved Builder', session: {
        session_id: 'builder-one', role: 'builder', status: 'active', provider_id: 'test', model_id: 'sol' } }];
      state.bridge.archive_builder_chat = async () => ({ ...state.builderChats[0],
        session: { ...state.builderChats[0].session, status: 'stopped' } });
      state.bridge.restore_builder_chat = async () => ({ ...state.builderChats[0],
        session: { ...state.builderChats[0].session, status: 'active' } });
    });
    await page.locator('#home-btn').click();
    await page.locator('#home-tutorial').waitFor({ state: 'visible' });
    await page.keyboard.press('Escape'); // Dismiss the normal first-visit tutorial in this fresh profile.
    if (!await page.locator('#builder-launcher').isVisible()) await page.locator('#toggle-left-sidebar-btn').click();
    await page.locator('.builder-chat-controls summary').click();
    await page.locator('[data-archive-builder="builder-one"]').click();
    assert.equal(await page.locator('[data-builder-session-id="builder-one"]').count(), 0);
    await page.locator('#open-archived-items-btn').click();
    await page.locator('[data-restore-builder="builder-one"]').click();
    assert.equal(await page.locator('[data-builder-session-id="builder-one"]').isVisible(), true);
    assert.equal(await page.locator('#session-context').isVisible(), false);
    await page.evaluate(() => {
      const { state, renderBuilderChatRail, ensureSessionActivity } = window.shellTest;
      state.builderChats = Array.from({ length: 16 }, (_, index) => ({
        display_name: index ? `Harbour adventure ${index}` :
          'A very long manually named lighthouse campaign chat that must remain exactly one line',
        session: { session_id: `compact-${index}`, role: 'builder', status: 'active' },
      }));
      state.activeSessionId = 'compact-0';
      ensureSessionActivity({ sessionId: 'compact-0', sessionRole: 'builder' }).phase = 'working';
      renderBuilderChatRail();
    });
    for (const width of [1280, 420]) {
      await page.setViewportSize({ width, height: 850 });
      const rows = await page.locator('.builder-chat-row').evaluateAll(rows => rows.map(row => {
        const name = row.querySelector('.builder-chat-name');
        return { height: row.getBoundingClientRect().height, nameHeight: name.getBoundingClientRect().height };
      }));
      assert.equal(rows.length, 16);
      assert.ok(rows.every(row => row.height >= 28 && row.height <= 32 && row.nameHeight <= 18));
      const long = page.locator('[data-builder-session-id="compact-0"]');
      assert.match(await long.getAttribute('aria-label'), /one line.*Independent Builder/);
      assert.equal(await long.getAttribute('aria-current'), 'true');
      assert.equal(await long.locator('.builder-stream-spinner').count(), 1);
      assert.equal(await long.locator('.builder-chat-name').evaluate(node => getComputedStyle(node).textOverflow), 'ellipsis');
    }
    await page.locator('.builder-chat-controls summary').first().focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('[data-rename-builder="compact-0"]').isVisible(), true);
    assert.equal(await page.locator('[data-delete-builder="compact-0"]').isDisabled(), true);
    await page.keyboard.press('Enter');
    if (process.env.SYLVIA_BUILDER_RAIL_SCREENSHOT) {
      await page.setViewportSize({ width: 1280, height: 850 });
      await page.screenshot({ path: process.env.SYLVIA_BUILDER_RAIL_SCREENSHOT });
    }
    await page.evaluate(() => {
      const { state, ensureSessionActivity, renderBuilderChatRail, renderBuilderHistory } = window.shellTest;
      state.activeSessionId = 'opening-title'; state.activeSessionRole = 'builder';
      state.activeCampaignId = null; state.view = 'session';
      state.builderChats = [{ display_name: 'New chat', session: {
        session_id: 'opening-title', role: 'builder', status: 'active',
      } }];
      const activity = ensureSessionActivity({ sessionId: 'opening-title', sessionRole: 'builder' });
      activity.phase = 'working'; activity.attemptId = 'opening-attempt';
      activity.pendingInput = { sessionId: 'opening-title', messageId: 'opening-message',
        text: 'A long first message about lanterns at a flooded ferry crossing.', oocText: '' };
      window.titleIndexReads = 0;
      state.bridge.builder_chat_index = async () => {
        window.titleIndexReads++;
        return [{ ...state.builderChats[0], display_name: 'A long first message about lanterns at a flooded ferry…' }];
      };
      renderBuilderChatRail();
      window.acceptedOpeningContext = { messages: [{ message_id: 'opening-message', kind: 'user',
        content: { text: activity.pendingInput.text } }] };
      renderBuilderHistory(activity, window.acceptedOpeningContext);
    });
    await page.waitForFunction(() => document.querySelector(
      '[data-builder-session-id="opening-title"] .builder-chat-name')?.textContent ===
      'A long first message about lanterns at a flooded ferry…');
    assert.equal(await page.evaluate(() => window.titleIndexReads), 1,
      'read the owner-saved fallback as soon as the opening is accepted, without waiting for inference');
    await page.evaluate(() => window.shellTest.renderBuilderHistory(
      window.shellTest.state.sessionActivities.get('opening-title'), window.acceptedOpeningContext));
    assert.equal(await page.evaluate(() => window.titleIndexReads), 1,
      'ongoing projection polling does not repeatedly query the navigation index');
    await page.setViewportSize({ width: 720, height: 750 });
    await page.evaluate(() => {
      const state = window.shellTest.state;
      state.sessionActivities.get('synthetic-gm').phase = 'idle';
      state.campaigns = Array.from({ length: 60 }, (_, index) => ({
        campaign_id: `campaign-${index}`, display_name: `Synthetic campaign ${index}`,
        lifecycle: 'active', revision: 0, sessions: [] }));
      state.bridge.archive_campaign = async () => { throw new Error('Synthetic archive rejection'); };
    });
    await page.locator('#home-btn').click();
    await page.locator('[data-archive-campaign="campaign-0"]').click();
    await page.waitForFunction(() => document.getElementById('archive-action-status').textContent.startsWith('Archive failed'));
    const archiveFeedback = await box('#archive-action-status');
    assert.ok(archiveFeedback.y >= 0 && archiveFeedback.y + archiveFeedback.height <= page.viewportSize().height,
      'campaign archive failures stay visible even with a long collection');
    await page.evaluate(async () => {
      const { state, ensureSessionActivity, attachBuilderSources } = window.shellTest;
      state.view = 'session'; state.activeSessionId = 'source-builder';
      state.activeSessionRole = 'builder'; state.activeCampaignId = null;
      ensureSessionActivity({ sessionId: 'source-builder', sessionRole: 'builder' });
      state.bridge.ingest_builder_sources = async () => {
        throw new Error('command_rejected: Text Builder source is not valid UTF-8');
      };
      await attachBuilderSources();
    });
    assert.match(await page.locator('#session-status-text').textContent(),
      /Source attachment failed: command_rejected: Text Builder source is not valid UTF-8/,
      'Builder displays the bounded admission reason so the selected source can be corrected');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
