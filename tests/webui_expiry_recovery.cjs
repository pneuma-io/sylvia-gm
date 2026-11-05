// Real source UI/owner; a fixture advances session expiry, and Playwright simulates HTTPS.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const backend = process.env.SYLVIA_TEST_WEBUI_BACKEND;
const origin = 'https://sylvia.example.ts.net';
const key = 'synthetic-private-route-key-for-tests-only-0123456789';
assert.ok(backend, 'Run this helper through pytest tests/test_webui_private_proxy.py');

test('expired sessions recover active interview and Builder drafts without replay or disclosure', { timeout: 100000 }, async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'chrome' : undefined) });
  try {
    for (const kind of ['interview', 'builder']) {
      const context = await browser.newContext({ viewport: kind === 'interview'
        ? { width: 1280, height: 900 } : { width: 390, height: 844 } });
      try {
        const page = await context.newPage();
        page.setDefaultTimeout(10000);
        const commands = [], requests = [], errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.route(`${origin}/**`, async route => {
          const request = route.request(), url = new URL(request.url());
          requests.push({ url: request.url(), body: request.postData() || '' });
          if (url.pathname === '/api/command') commands.push(request.postDataJSON());
          const response = await route.fetch({ url: backend + url.pathname + url.search,
            headers: { ...await request.allHeaders(), host: url.host }, maxRetries: 0, maxRedirects: 0 });
          await route.fulfill({ response });
        });
        const signIn = async () => {
          await page.getByLabel('Access key').fill(key);
          await page.getByRole('button', { name: 'Sign in', exact: true }).click();
          await page.locator('.webui-auth-recovery').waitFor({ state: 'detached' });
          await page.locator('.webui-session-bar output').filter({ hasText: 'Signed in' }).waitFor();
        };
        await page.goto(origin + '/app.html');
        await signIn();
        await page.locator('#home-tutorial').waitFor({ state: 'visible' });
        await page.keyboard.press('Escape');
        let textField, sessionId;
        if (kind === 'interview') {
          await page.locator('#home-new-campaign-btn').click();
          await page.locator('#construction-package').waitFor({ state: 'visible', timeout: 30000 });
          await page.locator('#construction-next-btn').click(); // Commit No Ruleset exactly once.
          textField = page.locator('#construction-name');
        } else {
          sessionId = await page.evaluate(async () => {
            const result = await window.SylviaWebUI.rpc('create_builder_chat', {
              operationId: 'expiry-builder', providerId: 'test.expiry', modelId: 'test.expiry/model',
              displayName: 'Expiry draft',
            });
            return result.session.session_id;
          });
          await page.reload();
          await page.getByRole('button', { name: 'Sessions', exact: true }).click();
          await page.locator(`[data-builder-session-id="${sessionId}"]`).first().click();
          textField = page.locator('#player-input');
        }
        await textField.waitFor({ state: 'visible' });
        // Advance only the HTTP session clock; the current real-owner UI remains mounted.
        const expired = await page.evaluate(async () => (await fetch('/__test__/expire-session', {
          method: 'POST', headers: { 'X-Sylvia-WebUI': '1' }, credentials: 'same-origin',
        })).status);
        assert.equal(expired, 200);
        const privateText = `Synthetic unsaved ${kind} text <private> & intact`;
        await textField.fill(privateText);
        const committed = () => commands.filter(item => [
          'create_guided_campaign_draft', 'advance_guided_campaign_draft', 'create_builder_chat',
          'submit_session_input', 'execute_panel_action',
        ].includes(item.method)).length;
        const committedBefore = committed();
        const navigation = [];
        page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigation.push(frame.url()); });
        await page.getByRole('button', { name: kind === 'interview' ? 'Reload / reconnect' : 'Sign out', exact: true }).click();
        const dialog = page.locator('.webui-auth-recovery');
        await page.getByLabel('Access key').waitFor();
        assert.equal(await textField.inputValue(), privateText);
        const mask = await textField.evaluate(element => ({
          bodyClass: document.body.className, visibility: getComputedStyle(element).visibility,
          matchesMask: element.matches('body.webui-auth-locked > :not(.webui-auth-recovery) *'),
          sheets: [...document.styleSheets].map(sheet => ({ href: sheet.href,
            hasMask: [...sheet.cssRules].some(rule => rule.selectorText?.includes('webui-auth-locked')) })),
        }));
        assert.equal(await textField.isVisible(), false,
          'Private input must be hidden behind sign-in: ' + JSON.stringify(mask));
        assert.equal(await page.locator('#session-frame').evaluate(element => element.inert), true);
        assert.equal((await dialog.innerText()).includes(privateText), false);
        if (kind === 'builder') await dialog.getByRole('heading', { name: 'Signed out', exact: true }).waitFor();
        await page.keyboard.press('Escape');
        assert.equal(await dialog.isVisible(), true, 'Escape must not expose the signed-out UI');
        assert.equal(await page.evaluate(value => JSON.stringify({ local: { ...localStorage },
          session: { ...sessionStorage } }).includes(value), privateText), false);
        if (process.env.SYLVIA_WEBUI_QA_OUTPUT) {
          fs.mkdirSync(process.env.SYLVIA_WEBUI_QA_OUTPUT, { recursive: true });
          await page.screenshot({ path: path.join(process.env.SYLVIA_WEBUI_QA_OUTPUT,
            `expired-${kind}-locked.png`) });
        }
        await signIn();
        assert.equal(await textField.inputValue(), privateText);
        await textField.waitFor({ state: 'visible' });
        assert.equal(committed(), committedBefore, 'Recovery must not replay a committed command');
        assert.equal(navigation.length, 0, 'The draft must stay in its original document');
        assert.ok(requests.every(request => !request.url.includes(encodeURIComponent(privateText))));
        assert.ok(requests.filter(request => request.url.endsWith('/auth/login'))
          .every(request => !request.body.includes(privateText)), 'Login must never carry draft text');
        if (kind === 'interview') {
          await page.locator('#construction-next-btn').click(); // Only this explicit click commits the name.
          await page.locator('#construction-boundaries').waitFor({ state: 'visible' });
          assert.equal(committed(), committedBefore + 1);
          await page.getByRole('button', { name: 'Sign out', exact: true }).click();
          await page.getByRole('heading', { name: 'Signed out', exact: true }).waitFor();
          assert.equal((await context.cookies(origin)).filter(cookie => cookie.name === '__Host-sylvia_webui').length, 0);
          await signIn();
          assert.equal(committed(), committedBefore + 1);
        } else {
          const saved = await page.evaluate(() => window.SylviaWebUI.rpc('load_desktop_drafts'));
          assert.equal(saved.sessions.find(row => row.session_id === sessionId).text, privateText);
          assert.equal(commands.filter(item => item.method === 'submit_session_input').length, 0);
        }
        assert.deepEqual(errors, []);
        console.log(`Verified expired ${kind} recovery, private drafts and no command replay`);
      } finally { await context.close(); }
    }
  } finally { await browser.close(); }
});
