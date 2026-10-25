// Actual shipped update markup/module, with an inert pending-install fixture and no native owner.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

test('pending restart preparation cannot be dismissed as Not now', async () => {
  const markup = fs.readFileSync(path.join(frontend, 'app.html'), 'utf8')
    .match(/<section\b[^>]*\bid="desktop-updates"[^>]*>[\s\S]*?<\/section>/)?.[0];
  assert.ok(markup, 'Use the complete shipped update section');
  const browser = await chromium.launch({ channel: 'msedge', headless: true, chromiumSandbox: true });
  try {
    const page = await browser.newPage();
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'updates.test') return route.abort();
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body:
        `<html><head><meta charset="UTF-8"><link rel="stylesheet" href="app.css"></head><body data-theme="slate">${markup}` +
        '<script src="desktop-updates.js"></script></body></html>' });
      const name = url.pathname.slice(1);
      if (!['app.css', 'desktop-updates.js'].includes(name)) return route.abort();
      await route.fulfill({ body: fs.readFileSync(path.join(frontend, name)),
        contentType: name.endsWith('.js') ? 'text/javascript' : 'text/css' });
    });
    await page.goto('https://updates.test/');
    await page.evaluate(() => {
      window.fixtureCalls = [];
      const ui = window.SylviaDesktopUpdates.mount(document.getElementById('desktop-updates'), {
        desktop_update_state: async () => ({ phase: 'verified', busy: false, can_apply: true,
          downloaded: true, current_version: '0.3.0-alpha.1', operation_id: 'a'.repeat(32) }),
      }, async identity => {
        window.fixtureCalls.push(identity);
        await new Promise(resolve => { window.releaseFixture = resolve; });
      });
      ui.setVisible(true);
    });
    await page.getByRole('button', { name: 'Update and restart…', exact: true }).click();
    await page.getByRole('button', { name: 'Update and restart', exact: true }).click();
    const confirmation = page.getByRole('group', { name: 'Confirm update and restart', exact: true });
    assert.equal(await page.getByRole('button', { name: 'Not now', exact: true }).isDisabled(), true);
    assert.match(await page.locator('[data-update-role="status"]').textContent(), /Saving drafts and preparing a safe restart/);
    await page.getByRole('button', { name: 'Not now', exact: true }).dispatchEvent('click');
    assert.equal(await confirmation.isVisible(), true);
    assert.deepEqual(await page.evaluate(() => window.fixtureCalls), ['a'.repeat(32)]);
    await page.evaluate(() => window.releaseFixture());
    await confirmation.waitFor({ state: 'hidden' });
  } finally { await browser.close(); }
});
