// Real rendered phase-one UI, actual Lite data, real owner; no provider request is submitted.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');
const backend = process.env.SYLVIA_TEST_WEBUI_BACKEND;
const origin = process.env.SYLVIA_TEST_WEBUI_ORIGIN;
const privateMode = process.env.SYLVIA_TEST_WEBUI_PRIVATE === '1';

test('actual Lite panels, Settings, separate drafts and reopen at desktop and phone sizes', async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || 'msedge' });
  try {
    for (const viewport of [{ width: 1600, height: 1000 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      const errors = [], commands = [], external = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.origin !== origin) { external.push(url.origin); return route.abort(); }
        if (url.pathname === '/api/command') commands.push(request.postDataJSON());
        if (!privateMode) return route.continue();
        const response = await route.fetch({ url: backend + url.pathname + url.search,
          headers: { ...await request.allHeaders(), host: url.host }, maxRetries: 0, maxRedirects: 0 });
        await route.fulfill({ response });
      });
      try {
        await page.goto(origin + '/app.html');
        if (privateMode) {
          await page.getByLabel('Access key').fill('synthetic-private-route-key-for-tests-only-0123456789');
          await page.getByRole('button', { name: 'Sign in', exact: true }).click();
        }
        await page.locator('#home-view').waitFor({ state: 'visible' });
        await page.locator('#home-tutorial').waitFor({ state: 'visible' });
        await page.keyboard.press('Escape');
        // Cold Lite projection materialization is backend work; keep UI readiness bounded after its reply.
        const initialPanels = page.waitForResponse(response => {
          if (new URL(response.url()).pathname !== '/api/command') return false;
          return response.request().postDataJSON()?.method === 'campaign_panels';
        }, { timeout: 60000 });
        await page.locator('#home-recent [data-home-campaign-id="lite"]').click();
        const panelsResponse = await initialPanels;
        assert.equal(panelsResponse.status(), 200);
        assert.equal((await panelsResponse.json()).ok, true);
        await page.locator('#home-view').waitFor({ state: 'hidden' });
        await page.locator('#player-input').waitFor({ state: 'visible' });
        await page.waitForFunction(() => !document.getElementById('player-input').disabled);
        const playDraft = `WEEKEND ${privateMode} ${viewport.width} PLAY unsent`;
        const oocDraft = `WEEKEND ${privateMode} ${viewport.width} OOC unsent`;
        const builderDraft = `WEEKEND ${privateMode} ${viewport.width} BUILDER unsent`;
        await page.locator('#player-input').fill(playDraft);
        await page.locator('#ooc-note summary').click();
        await page.locator('#ooc-input').fill(oocDraft);
        await page.locator('#rail-settings-btn').click();
        await page.locator('#settings-overlay').waitFor({ state: 'visible' });
        await page.locator('#settings-tab-models').click();
        assert.match(await page.locator('#settings-panel-models').innerText(), /Synthetic offline test/);
        await page.locator('#close-settings-btn').click();
        assert.equal(await page.locator('#player-input').inputValue(), playDraft);
        assert.equal(await page.locator('#ooc-input').inputValue(), oocDraft);
        if (viewport.width < 720) await page.getByRole('button', { name: 'Game panels', exact: true }).click();
        await page.locator('#ruleset-panels .ruleset-panel').first().waitFor({ state: 'visible' });
        assert.ok(await page.locator('#ruleset-panels .ruleset-panel').count() >= 2);
        assert.equal(await page.locator('[data-panel-workspace-destination="new"]').count(), 0);
        const menu = page.locator('[data-panel-menu]').first();
        if (await menu.isVisible()) {
          await menu.click();
          assert.doesNotMatch(await page.locator('dialog[open]').innerText(), /Move to a new window/);
          await page.getByRole('button', { name: 'Cancel', exact: true }).click();
        }
        assert.equal(await page.locator('#dictation-toggle').isEnabled(), false);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), viewport.width);
        await page.screenshot({ path: path.join(process.env.SYLVIA_WEBUI_QA_OUTPUT, `lite-${privateMode ? 'private' : 'local'}-${viewport.width}-panels.png`) });
        if (viewport.width < 720) {
          assert.equal(await page.locator('#session-frame').getAttribute('data-browser-pane'), 'panels');
          assert.equal(await page.locator('#player-input').isVisible(), false);
        }
        await page.locator('#rail-builder-btn').click();
        await page.locator('#rail-builder-btn[aria-current="page"]').waitFor();
        await page.screenshot({ path: path.join(process.env.SYLVIA_WEBUI_QA_OUTPUT, `builder-from-panels-${privateMode ? 'private' : 'local'}-${viewport.width}.png`) });
        assert.equal(await page.locator('#story-column').isVisible(), true, 'Builder conversation is exposed directly from Game panels');
        assert.equal(await page.locator('#player-input').isVisible(), true, 'Builder composer is exposed directly from Game panels');
        if (viewport.width < 720) {
          assert.equal(await page.locator('#session-frame').getAttribute('data-browser-pane'), 'chat');
          assert.equal(await page.getByRole('button', { name: 'Chat', exact: true }).getAttribute('aria-pressed'), 'true');
          assert.equal(await page.locator('#right-sidebar').isVisible(), false);
        }
        await page.waitForFunction(() => !document.getElementById('player-input').disabled);
        await page.locator('#player-input').fill(builderDraft);
        await page.locator('#rail-play-btn').click();
        assert.equal(await page.locator('#player-input').inputValue(), playDraft);
        assert.equal(await page.locator('#ooc-input').inputValue(), oocDraft);
        await page.getByRole('button', { name: 'Reload / reconnect', exact: true }).click();
        await page.locator('#home-view').waitFor({ state: 'visible' });
        await page.locator('#home-recent [data-home-campaign-id="lite"]').click();
        await page.waitForFunction(() => !document.getElementById('player-input').disabled);
        assert.equal(await page.locator('#player-input').inputValue(), playDraft);
        assert.equal(await page.locator('#ooc-input').inputValue(), oocDraft);
        assert.equal(commands.filter(command => ['submit_session_input', 'execute_panel_action', 'execute_action_sequence'].includes(command.method)).length, 0);
        assert.deepEqual(external, []);
        assert.deepEqual(errors, []);
      } catch (error) {
        await page.screenshot({ path: path.join(process.env.SYLVIA_WEBUI_QA_OUTPUT, `failure-${privateMode ? 'private' : 'local'}-${viewport.width}.png`) });
        console.error(JSON.stringify({ viewport, commands: commands.map(item => item.method), errors,
          status: await page.locator('#session-status-text').textContent(),
          loading: await page.locator('#campaign-loading-status').textContent() }));
        throw error;
      } finally {
        await context.route('**/*', route => route.abort());
        await page.unrouteAll({ behavior: 'wait' });
        await context.close();
      }
    }
  } finally { await browser.close(); }
});
