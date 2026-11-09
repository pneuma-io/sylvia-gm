// Rendered source UI + real owner. Playwright simulates HTTPS forwarding, not Tailscale or TLS.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const backend = process.env.SYLVIA_TEST_WEBUI_BACKEND;
const origin = 'https://sylvia.example.ts.net';
const key = 'synthetic-private-route-key-for-tests-only-0123456789';
assert.ok(backend, 'Run this helper through pytest tests/test_webui_private_proxy.py');

test('private login, owner workflows and sign-out at desktop and phone sizes',
  { timeout: 100000 }, async () => {
    const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
      channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'chrome' : undefined) });
    try {
      for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
        const context = await browser.newContext({ viewport });
        try {
          const page = await context.newPage();
          page.setDefaultTimeout(10000);
          const errors = [], commands = [];
          let delayReloadDocument = false;
          page.on('pageerror', error => errors.push(error.message));
          await page.route(`${origin}/**`, async route => {
            const request = route.request();
            const url = new URL(request.url());
            if (delayReloadDocument && request.isNavigationRequest() && url.pathname === '/app.html') {
              delayReloadDocument = false;
              // Keep the old signed-in document alive while the replacement response is pending.
              await new Promise(resolve => setTimeout(resolve, 1000));
            }
            const headers = { ...await request.allHeaders(), host: url.host };
            if (url.pathname === '/api/command') commands.push(request.postDataJSON());
            const response = await route.fetch({ url: backend + url.pathname + url.search, headers,
              maxRetries: 0, maxRedirects: 0 });
            await route.fulfill({ response });
          });
          await page.goto(origin + '/app.html');
          await page.getByLabel('Access key').waitFor();
          if (process.env.SYLVIA_WEBUI_QA_OUTPUT) {
            fs.mkdirSync(process.env.SYLVIA_WEBUI_QA_OUTPUT, { recursive: true });
            await page.screenshot({ path: path.join(process.env.SYLVIA_WEBUI_QA_OUTPUT,
              `login-${viewport.width}.png`) });
          }
          assert.equal((await context.cookies(origin)).length, 0);
          await page.getByLabel('Access key').fill('incorrect');
          await page.getByRole('button', { name: 'Sign in', exact: true }).click();
          await page.getByRole('status').filter({ hasText: 'Sign-in failed' }).waitFor();
          assert.equal(await page.getByLabel('Access key').inputValue(), '');
          await page.getByLabel('Access key').fill(key);
          await page.getByRole('button', { name: 'Sign in', exact: true }).click();
          await page.locator('.webui-session-bar output').filter({ hasText: 'Signed in' }).waitFor();
          const cookie = (await context.cookies(origin)).find(value => value.name === '__Host-sylvia_webui');
          assert.ok(cookie?.secure && cookie.httpOnly && cookie.sameSite === 'Strict');
          assert.equal(await page.evaluate(() => document.cookie.includes('__Host-sylvia_webui')), false);
          assert.equal(await page.evaluate(() => JSON.stringify(localStorage).includes('synthetic-private-route-key')), false);
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), viewport.width);
          await page.locator('#home-tutorial').waitFor({ state: 'visible' });
          await page.keyboard.press('Escape'); // Dismiss the ordinary first-visit Home tutorial.
          await page.locator('#home-settings-btn').click();
          await page.locator('#settings-overlay').waitFor({ state: 'visible' });
          const bar = await page.locator('.webui-session-bar').boundingBox();
          const settings = await page.locator('#settings-overlay').boundingBox();
          assert.ok(settings.y >= bar.y + bar.height - 1, 'Settings must leave transport controls clear');
          await page.locator('#close-settings-btn').click();
          await page.locator('#home-new-campaign-btn').click();
          await page.locator('#session-title').filter({ hasText: 'New campaign interview' }).waitFor();
          await page.locator('#construction-package').waitFor({ state: 'visible', timeout: 30000 });
          assert.ok((await page.locator('#construction-package option').allTextContents())
            .some(label => /Lite/i.test(label)), 'Interview must offer the actual bundled Lite ruleset');
          assert.equal(commands.filter(item => item.method === 'create_guided_campaign_draft').length, 1);
          const before = commands.filter(item => item.method === 'create_guided_campaign_draft').length;
          if (viewport.width < 720) {
            for (const [name, pane] of [['Game panels', 'panels'], ['Chat', 'chat'], ['Sessions', 'sessions']]) {
              await page.getByRole('button', { name, exact: true }).click();
              assert.equal(await page.locator('#session-frame').getAttribute('data-browser-pane'), pane);
            }
          }
          await page.evaluate(() => { window.__sylviaProxyReloadWitness = 'old-document'; });
          delayReloadDocument = true;
          await Promise.all([
            page.waitForEvent('domcontentloaded'),
            page.getByRole('button', { name: 'Reload / reconnect', exact: true }).click(),
          ]);
          assert.equal(await page.evaluate(() => window.__sylviaProxyReloadWitness), undefined,
            'Reload readiness must belong to the replacement document');
          await page.locator('.webui-session-bar output').filter({ hasText: 'Signed in' }).waitFor();
          await page.locator('#connecting-overlay').waitFor({ state: 'hidden' });
          await page.locator('#home-view').waitFor({ state: 'visible' });
          await page.locator('#home-recent').getByRole('button', { name: 'Continue', exact: true }).click();
          await page.locator('#home-view').waitFor({ state: 'hidden' });
          await page.locator('#session-title').filter({ hasText: 'New campaign interview' }).waitFor();
          await page.locator('#construction-package').waitFor({ state: 'visible', timeout: 30000 });
          assert.equal(commands.filter(item => item.method === 'create_guided_campaign_draft').length, before,
            'Reload must not replay creation');
          const logout = page.getByRole('button', { name: 'Sign out', exact: true });
          const box = await logout.boundingBox();
          assert.ok(box.x >= 0 && box.x + box.width <= viewport.width);
          if (viewport.width < 720) assert.ok(box.height >= 44);
          if (process.env.SYLVIA_WEBUI_QA_OUTPUT) await page.screenshot({
            path: path.join(process.env.SYLVIA_WEBUI_QA_OUTPUT, `workspace-${viewport.width}.png`) });
          await logout.click();
          await page.getByLabel('Access key').waitFor();
          await page.getByRole('heading', { name: 'Signed out', exact: true }).waitFor();
          assert.equal((await context.cookies(origin)).filter(value => value.name === '__Host-sylvia_webui').length, 0);
          if (viewport.width < 720) {
            await page.getByLabel('Access key').fill(key);
            await page.getByRole('button', { name: 'Sign in', exact: true }).click();
            await page.locator('.webui-session-bar output').filter({ hasText: 'Signed in' }).waitFor();
            await page.getByRole('button', { name: 'Stop WebUI for all devices', exact: true }).click();
            await page.locator('.webui-session-bar output').filter({ hasText: 'stopped' }).waitFor();
            assert.equal(await page.locator('#session-frame').evaluate(element => element.inert), true);
          }
          assert.deepEqual(errors, []);
          console.log(`Verified simulated proxy viewport ${viewport.width}x${viewport.height}`);
        } finally { await context.close(); }
      }
    } finally { await browser.close(); }
  });
