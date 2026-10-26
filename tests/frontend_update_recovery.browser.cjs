// Render the shipped recovery UI/CSS against explicit synthetic state; no native owner or network.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

for (const retry of [false, true]) {
  test(`recovery renders only valid data choices: ${retry ? 'ambiguous retry' : 'initial restore'}`, async () => {
    const browser = await chromium.launch({ channel: 'msedge', headless: true, chromiumSandbox: true });
    try {
      const page = await browser.newPage();
      const calls = [];
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        const name = url.pathname.slice(1) || 'update-recovery.html';
        if (url.hostname !== 'recovery.test' ||
            !['update-recovery.html', 'update-recovery.js', 'app.css', 'update-recovery.css'].includes(name)) {
          return route.abort();
        }
        await route.fulfill({ body: fs.readFileSync(path.join(frontend, name)),
          contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html' });
      });
      await page.exposeFunction('recordRecoveryCall', command => calls.push(command));
      await page.addInitScript(({ retry }) => {
        window.__TAURI_INTERNALS__ = { invoke: async command => {
          await window.recordRecoveryCall(command);
          if (command !== 'desktop_update_recovery_state') throw new Error('Unexpected mutation');
          return { operation_id: 'a'.repeat(32), can_keep_data: true, can_recover: true,
            previous_version: '0.3.0-alpha.1101', restart_nonce: retry ? 'b'.repeat(32) : null,
            restore_warning: 'Restoring the pre-update checkpoint loses newer saved progress.',
            message: retry ? 'Review a retry. Current data will be kept.' : 'Choose a data option.' };
        } };
      }, { retry });
      await page.goto('https://recovery.test/');
      await page.locator('#recovery-choices').waitFor({ state: 'visible' });
      assert.equal(await page.locator('#recovery-restore').isVisible(), !retry);
      const warning = await page.locator('#recovery-warning').textContent();
      if (retry) {
        assert.match(warning, /keeps current saved data/);
        assert.doesNotMatch(warning, /loses newer saved progress/);
      } else assert.match(warning, /loses newer saved progress/);
      assert.equal(await page.locator('#recovery-confirm').isVisible(), false);
      await page.getByRole('radio').first().focus();
      await page.keyboard.press('Space');
      await page.keyboard.press('Tab');
      assert.equal(await page.getByRole('button', { name: 'Check recovery status', exact: true }).evaluate(node => node === document.activeElement), true);
      await page.keyboard.press('Tab');
      assert.equal(await page.getByRole('button', { name: 'Review recovery and restart', exact: true }).evaluate(node => node === document.activeElement), true);
      await page.keyboard.press('Enter');
      assert.equal(await page.locator('#recovery-confirm').isVisible(), true);
      assert.equal(await page.getByRole('button', { name: 'Recover and restart', exact: true }).evaluate(node => node === document.activeElement), true);
      if (retry) assert.match(await page.locator('#recovery-confirm-text').textContent(), /No data will be restored again/);
      await page.keyboard.press('Tab');
      assert.equal(await page.getByRole('button', { name: 'Go back', exact: true }).evaluate(node => node === document.activeElement), true);
      await page.keyboard.press('Enter');
      assert.equal(await page.locator('#recovery-confirm').isVisible(), false);
      assert.equal(await page.getByRole('button', { name: 'Review recovery and restart', exact: true }).evaluate(node => node === document.activeElement), true);
      assert.deepEqual(calls, ['desktop_update_recovery_state']);
      await page.close();
    } finally { await browser.close(); }
  });
}
