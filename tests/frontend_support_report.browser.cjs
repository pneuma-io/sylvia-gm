// Headless source UI proof with a synthetic owner; native save-dialog acceptance is separate.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

test('Settings report previews exact text, cancels, saves, and keeps Full Audit separate', async () => {
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1365, height: 1100 } });
    const errors = [], external = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'https://sylvia-report.test') {
        external.push(url.href);
        return route.abort();
      }
      const name = url.pathname.slice(1) || 'app.html';
      if (!['app.html', 'app.css', 'app.js', 'support-report.js', 'local-diagnostics.js', 'panel-workspace.js',
        'shot-playback.js', 'panel-host.js', 'tile-faces.js', 'tile-table.js', 'piece-table.js',
        'campaign-plan-browser.js', 'dictation.js', 'tile-table.css'].includes(name)) return route.abort();
      let body = fs.readFileSync(path.join(frontend, name));
      if (name === 'app.js') body = body.toString().replace(
        "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
        .replace('  // ---- Public API ----', `window.reportTest = { state, wireControls, openSettings,
          connectReport() { localSupportReport = window.SylviaSupportReport.init(state.bridge); } };
          // ---- Public API ----`);
      await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
        name.endsWith('.css') ? 'text/css' : 'text/html' });
    });
    await page.goto('https://sylvia-report.test/');
    await page.evaluate(() => {
      window.calls = [];
      window.pending = null;
      const bridge = {
        async preview_local_report(fields) {
          window.calls.push('preview');
          const content = JSON.stringify({ format: 'synthetic-ui-report', player_entered: fields,
            diagnostics: [{ event: 'command_completed', operation: 'execute_action',
              outcome: 'error', error_code: 'command_rejected', count: 3 }] }, null, 2) + '\n';
          window.pending = { preview_id: 'synthetic-preview', content, bytes: new TextEncoder().encode(content).length };
          return window.pending;
        },
        async cancel_local_report() { window.calls.push('cancel'); window.pending = null; },
        async save_local_report(id) {
          window.calls.push('save');
          if (!window.pending || id !== window.pending.preview_id) throw Error('expired');
          if (window.delaySave) {
            window.chooserReturned = true;
            await new Promise(resolve => { window.completeSave = resolve; });
          }
          window.saved = window.pending.content;
          return { status: 'saved' };
        },
      };
      const api = window.reportTest;
      api.state.bridge = bridge;
      api.wireControls();
      api.connectReport();
      document.getElementById('connecting-overlay').style.display = 'none';
      document.getElementById('session-frame').style.display = 'flex';
      api.openSettings('about');
    });
    await page.getByLabel('Report type').selectOption('model_feedback');
    const steps = 'Open a saved campaign. Choose Continue. The action stops.';
    const hostile = '<img src="https://unexpected.test/upload" onerror="window.injected=true">';
    await page.getByLabel('Steps to reproduce').fill(steps);
    await page.getByLabel('Your feedback').fill(hostile);
    await page.getByRole('button', { name: 'Preview local report' }).click();
    await page.locator('#local-report-preview').waitFor({ state: 'visible' });
    const preview = await page.locator('#local-report-content').textContent();
    assert.equal(JSON.parse(preview).player_entered.feedback, hostile);
    assert.equal(await page.locator('#local-report-content img').count(), 0);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.equal(await page.evaluate(() => window.calls.includes('save')), false);
    assert.match(await page.locator('#toggle-admin-audit-btn').textContent(), /Enable Full Audit/);

    // Use human-readable sample feedback for the evidence screenshot.
    await page.getByLabel('Your feedback').fill('The GM repeated a choice I had already made.');
    assert.equal(await page.locator('#local-report-preview').isVisible(), false);
    await page.getByRole('button', { name: 'Preview local report' }).click();
    await page.locator('#local-report-preview').waitFor({ state: 'visible' });
    const evidence = process.env.SYLVIA_DIAGNOSTICS_EVIDENCE;
    if (evidence) {
      fs.mkdirSync(evidence, { recursive: true });
      await page.locator('#preview-local-report-btn').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(evidence, 'report-preview-component.png') });
    }
    const exact = await page.locator('#local-report-content').textContent();
    await page.evaluate(() => { window.delaySave = true; });
    await page.getByRole('button', { name: 'Save report…', exact: true }).click();
    await page.waitForFunction(() => window.chooserReturned === true);
    assert.equal(await page.locator('#close-settings-btn').isDisabled(), true);
    assert.equal(await page.locator('#cancel-local-report-btn').isDisabled(), true);
    assert.equal(await page.locator('#settings-tab-models').isDisabled(), true);
    const cancelsDuringSave = await page.evaluate(() => window.calls.filter(value => value === 'cancel').length);
    await page.keyboard.press('Escape');
    await page.locator('#settings-overlay').click({ position: { x: 5, y: 5 } });
    assert.equal(await page.locator('#settings-overlay').isVisible(), true);
    assert.equal(await page.locator('#local-report-content').textContent(), exact);
    assert.match(await page.locator('#local-report-status').textContent(), /Saving report/);
    assert.equal(await page.evaluate(() => window.calls.filter(value => value === 'cancel').length), cancelsDuringSave);
    if (evidence) await page.screenshot({ path: path.join(evidence, 'report-saving-component.png') });
    await page.evaluate(() => { window.completeSave(); });
    await page.getByText('Report saved to your chosen file.', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.saved), exact);
    assert.equal(await page.getByLabel('Steps to reproduce').inputValue(), '');
    await page.locator('#close-settings-btn').click();
    assert.equal(await page.locator('#settings-overlay').isVisible(), false);
    await page.evaluate(() => window.reportTest.openSettings('about'));
    assert.equal(await page.locator('#local-report-status').textContent(), 'Report saved to your chosen file.');
    await page.getByRole('button', { name: 'Preview local report' }).click();
    await page.locator('#local-report-preview').waitFor({ state: 'visible' });
    const savesBeforeCancel = await page.evaluate(() => window.calls.filter(value => value === 'save').length);
    await page.locator('#cancel-local-report-btn').click();
    await page.getByText('Cancelled. No report was saved.', { exact: true }).waitFor();
    assert.equal(await page.locator('#local-report-preview').isVisible(), false);
    assert.equal(await page.evaluate(() => window.calls.filter(value => value === 'save').length), savesBeforeCancel);
    if (evidence) await page.screenshot({ path: path.join(evidence, 'report-cancelled-component.png') });
    assert.deepEqual(external, [], 'report actions must not make network requests');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
