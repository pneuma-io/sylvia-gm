// Render the shipped WebUI shell; synthetic session state, no provider or native input.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

test('Game panels to bottom Builder exposes conversation and composer at 390x844 and preserves desktop', async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    for (const viewport of [{ width: 390, height: 844 }, { width: 1600, height: 1000 }]) {
      const page = await browser.newPage({ viewport });
      page.setDefaultTimeout(4000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://sylvia-navigation.test') return route.abort();
        if (url.pathname.startsWith('/api/')) return route.fulfill({ json: { ok: true, result: [] } });
        const name = url.pathname.slice(1) || 'app.html', file = path.join(frontend, name);
        if (!file.startsWith(frontend + path.sep) || !fs.existsSync(file)) return route.abort();
        let body = fs.readFileSync(file);
        if (name === 'app.html') body = body.toString().replace('</head>', '<link rel="stylesheet" href="webui-mobile.css"></head>')
          .replace('<script src="panel-workspace.js">', '<script src="webui-bridge.js"></script><script src="webui-mobile.js"></script><script src="panel-workspace.js">');
        if (name === 'app.js') body = body.toString()
          .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
          .replace('  // ---- Public API ----', 'window.navigationTest = { state, wireControls, initSidebarResize, showSessionView, renderBuilderChatRail, renderSidebarState };\n  // ---- Public API ----');
        await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
      });
      try {
        await page.goto('https://sylvia-navigation.test/app.html');
        await page.evaluate(() => {
          const api = window.navigationTest;
          document.getElementById('connecting-overlay').classList.add('hidden');
          document.getElementById('session-frame').style.display = 'flex';
          Object.assign(api.state, { view: 'session', activeCampaignId: 'synthetic', activeSessionId: 'synthetic-gm', activeSessionRole: 'dm', bridge: { submit_session_input() { throw new Error('Navigation must not submit a draft'); } }, campaigns: [],
            modelChoices: [{ provider_id: 'test', model_id: 'test/model', model_name: 'Synthetic offline model' }],
            builderChats: [{ display_name: 'Synthetic Builder', session: { session_id: 'synthetic-builder', role: 'builder', status: 'active', provider_id: 'test', model_id: 'test/model' } }] });
          api.wireControls(); api.initSidebarResize(); api.showSessionView(); api.renderBuilderChatRail(); api.renderSidebarState();
        });
        if (viewport.width < 720) {
          await page.getByRole('button', { name: 'Game panels', exact: true }).click();
          assert.equal(await page.locator('#session-frame').getAttribute('data-browser-pane'), 'panels');
        }
        await page.locator('#rail-builder-btn').click();
        await page.waitForFunction(() => window.navigationTest.state.activeSessionRole === 'builder');
        if (process.env.SYLVIA_WEBUI_QA_OUTPUT) await page.screenshot({ path: path.join(process.env.SYLVIA_WEBUI_QA_OUTPUT, `builder-from-panels-${viewport.width}.png`) });
        assert.equal(await page.locator('#story-column').isVisible(), true, 'opened Builder conversation must be visible');
        assert.equal(await page.locator('#player-input').isVisible(), true, 'opened Builder composer must be visible');
        if (viewport.width < 720) {
          assert.equal(await page.locator('#session-frame').getAttribute('data-browser-pane'), 'chat');
          assert.equal(await page.getByRole('button', { name: 'Chat', exact: true }).getAttribute('aria-pressed'), 'true');
          await page.getByRole('button', { name: 'Game panels', exact: true }).click();
          await page.locator('#rail-builder-btn').click();
          assert.equal(await page.locator('#player-input').isVisible(), true, 'reselecting Builder also exposes its composer');
        }
        await page.locator('#player-input').fill('Synthetic unsent Builder draft');
        assert.equal(await page.locator('#player-input').inputValue(), 'Synthetic unsent Builder draft');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }
  } finally { await browser.close(); }
});

test('rendered Stop reports an abnormal close and retains drafts; clean close disables the workspace', async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    for (const code of [0, 23]) {
      const page = await browser.newPage();
      let stops = 0;
      await page.route('https://sylvia-stop.test/**', async route => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === '/api/command') return route.fulfill({ json: { ok: true, result: [] } });
        if (pathname === '/api/stop') {
          stops++;
          return route.fulfill({ status: code ? 400 : 200, json: code
            ? { ok: false, error: { code: 'owner_close_failed', message: 'Sylvia owner did not close cleanly (exit 23). Saved work is retained; launch again to inspect it.' } }
            : { ok: true, result: { status: 'closed' } } });
        }
        if (pathname === '/webui-bridge.js') return route.fulfill({ body: fs.readFileSync(path.join(frontend, 'webui-bridge.js')), contentType: 'text/javascript' });
        return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head><script>window.flushes=0;window.SylviaGMFrontend={flushBrowserDrafts:async()=>{window.flushes++}}</script><script src="webui-bridge.js"></script></head><body><main id="session-frame"><textarea id="draft">Synthetic unsent draft</textarea></main></body></html>' });
      });
      try {
        await page.goto('https://sylvia-stop.test/');
        const status = page.locator('.webui-session-bar output');
        await status.filter({ hasText: 'This computer only' }).waitFor();
        await page.locator('[data-webui-stop]').click();
        await status.filter({ hasText: code ? 'exit 23' : 'Local WebUI stopped' }).waitFor();
        assert.equal(await page.evaluate(() => window.SylviaWebUI.isStopped()), code === 0);
        assert.equal(await page.locator('#session-frame').evaluate(node => node.inert), code === 0);
        assert.equal(await page.locator('[data-webui-stop]').isEnabled(), code !== 0);
        assert.equal(await page.locator('#draft').inputValue(), 'Synthetic unsent draft');
        assert.equal(await page.evaluate(() => window.flushes), 1);
        assert.equal(stops, 1, 'failure is not automatically replayed');
      } finally { await page.close(); }
    }
  } finally { await browser.close(); }
});
