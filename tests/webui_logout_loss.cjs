// The real backend handles logout; Playwright drops only the response reaching the page.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const backend = process.env.SYLVIA_TEST_WEBUI_BACKEND;
const origin = 'https://sylvia.example.ts.net';
const key = 'synthetic-private-route-key-for-tests-only-0123456789';
assert.ok(backend, 'Run this helper through pytest tests/test_webui_private_proxy.py');

test('lost logout responses lock private drafts with honest revocation uncertainty', { timeout: 100000 }, async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'chrome' : undefined) });
  try {
    for (const delivered of [true, false]) {
      const mode = delivered ? 'after-revocation' : 'before-delivery';
      const context = await browser.newContext({ viewport: delivered
        ? { width: 1280, height: 900 } : { width: 390, height: 844 } });
      let releaseLogout;
      try {
        const page = await context.newPage();
        page.setDefaultTimeout(10000);
        const commands = [], loginBodies = [], errors = [];
        page.on('pageerror', error => errors.push(error.message));
        let backendLogoutStatus, previousCookie, logoutRequests = 0, observeLogout;
        const logoutObserved = new Promise(resolve => { observeLogout = resolve; });
        await page.route(`${origin}/**`, async route => {
          const request = route.request(), url = new URL(request.url());
          const headers = { ...await request.allHeaders(), host: url.host };
          if (url.pathname === '/api/command') commands.push(request.postDataJSON());
          if (url.pathname === '/auth/login') loginBodies.push(request.postData());
          if (url.pathname === '/auth/logout') {
            logoutRequests += 1; previousCookie = headers.cookie;
            if (delivered) backendLogoutStatus = (await route.fetch({ url: backend + url.pathname,
              headers, maxRetries: 0, maxRedirects: 0 })).status();
            const released = new Promise(resolve => { releaseLogout = resolve; });
            observeLogout();
            await released;
            return route.abort('failed'); // The page never receives a logout response.
          }
          const response = await route.fetch({ url: backend + url.pathname + url.search,
            headers, maxRetries: 0, maxRedirects: 0 });
          await route.fulfill({ response });
        });
        const signIn = async () => {
          await page.getByLabel('Access key').fill(key);
          await page.getByRole('button', { name: 'Sign in', exact: true }).click();
          await page.locator('.webui-auth-recovery').waitFor({ state: 'detached' });
          await page.locator('.webui-session-bar output').filter({ hasText: 'Signed in' }).waitFor();
        };
        const screenshot = async name => {
          if (!process.env.SYLVIA_WEBUI_QA_OUTPUT) return;
          fs.mkdirSync(process.env.SYLVIA_WEBUI_QA_OUTPUT, { recursive: true });
          await page.screenshot({ path: path.join(process.env.SYLVIA_WEBUI_QA_OUTPUT, name) });
        };
        await page.goto(origin + '/app.html');
        await signIn();
        await page.locator('#home-tutorial').waitFor({ state: 'visible' });
        await page.keyboard.press('Escape');
        const sessionId = await page.evaluate(async mode => (await window.SylviaWebUI.rpc('create_builder_chat', {
          operationId: 'logout-loss-' + mode, providerId: 'test.logout', modelId: 'test.logout/model',
          displayName: 'Private draft recovery',
        })).session.session_id, mode);
        await page.reload();
        await page.locator('#connecting-overlay').waitFor({ state: 'hidden' });
        // The overlay can close before the independent Builder index arrives.
        // Exercise logout from the saved chat, after that chat is actually navigable.
        await page.locator(`[data-builder-session-id="${sessionId}"]`).first().waitFor({ state: 'attached' });
        if (!delivered) await page.getByRole('button', { name: 'Sessions', exact: true }).click();
        await page.locator('#rail-builder-btn').click();
        await page.locator('#home-view').waitFor({ state: 'hidden' });
        await page.locator('#player-input').waitFor({ state: 'visible' });
        await page.getByText('The Builder is ready.', { exact: true }).waitFor();
        await page.waitForFunction(() => !document.getElementById('player-input').disabled);
        assert.equal(await page.locator('#connecting-overlay').isVisible(), false);
        if (delivered) await screenshot('desktop-workspace-loaded.png');
        const privateText = 'Synthetic private draft must stay hidden';
        await page.locator('#player-input').fill(privateText);
        const navigations = [];
        page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigations.push(frame.url()); });
        await page.getByRole('button', { name: 'Sign out', exact: true }).click();
        await page.getByRole('heading', { name: 'Tab locked · Signing out', exact: true }).waitFor();
        assert.equal(await page.locator('#player-input').isVisible(), false, 'Lock before waiting for the network');
        assert.equal(await page.getByLabel('Access key').isDisabled(), true);
        await logoutObserved;
        if (delivered) assert.equal(backendLogoutStatus, 200);
        const probeCookie = () => page.request.post(backend + '/api/command', {
          headers: { Host: new URL(origin).host, Origin: origin, 'X-Sylvia-WebUI': '1', Cookie: previousCookie },
          data: { id: 'logout-loss-probe', method: 'application_info' },
        });
        assert.equal((await probeCookie()).status(), delivered ? 401 : 200,
          'Probe the actual backend using the original cookie');
        releaseLogout();
        await page.getByRole('heading', { name: 'Tab locked · Sign-out unconfirmed', exact: true }).waitFor();
        const dialog = page.locator('.webui-auth-recovery');
        assert.match(await dialog.innerText(), /session may still be active/i);
        assert.equal((await dialog.innerText()).includes(privateText), false);
        assert.equal(await page.locator('#player-input').isVisible(), false);
        assert.equal(await page.locator('#player-input').inputValue(), privateText);
        assert.equal(await page.locator('#session-frame').evaluate(element => element.inert), true);
        await page.keyboard.press('Escape');
        assert.equal(await dialog.isVisible(), true);
        assert.equal(await page.evaluate(value => JSON.stringify({ local: { ...localStorage },
          session: { ...sessionStorage } }).includes(value), privateText), false);
        await screenshot(`logout-unconfirmed-${mode}.png`);
        await signIn();
        await page.locator('#player-input').waitFor({ state: 'visible' });
        assert.equal(await page.locator('#player-input').inputValue(), privateText);
        const saved = await page.evaluate(() => window.SylviaWebUI.rpc('load_desktop_drafts'));
        assert.equal(saved.sessions.find(row => row.session_id === sessionId).text, privateText);
        assert.equal((await probeCookie()).status(), 401, 'Fresh login must retire any old session');
        assert.equal(logoutRequests, 1, 'Never automatically retry logout');
        assert.equal(commands.filter(item => item.method === 'create_builder_chat').length, 1);
        assert.equal(commands.filter(item => item.method === 'submit_session_input').length, 0);
        assert.equal(navigations.length, 0);
        assert.ok(loginBodies.every(body => !body.includes(privateText)));
        assert.deepEqual(errors, []);
        console.log(`Verified lost logout ${mode}, local privacy lock and draft recovery`);
      } finally { releaseLogout?.(); await context.close(); }
    }
  } finally { await browser.close(); }
});
