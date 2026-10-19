const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

test('keyboard dictation reviews text and ordinary Send submits exactly once', { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
    page.setDefaultTimeout(4000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('https://dictation.test/**', async route => {
      const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
      const allowed = ['app.html', 'app.css', 'app.js', 'dictation.js', 'panel-workspace.js',
        'panel-host.js', 'shot-playback.js', 'campaign-plan-browser.js', 'assets/sylvia-portrait.png'];
      if (!allowed.includes(name)) return route.abort();
      let body = fs.readFileSync(path.join(frontend, name));
      if (name === 'app.js') body = body.toString().replace(
        "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
        .replace('  // ---- Public API ----', `window.dictationTest = { state, wireControls,
          refreshSessionInputAdmission, ensureSessionActivity, restoreSelectedSessionDraft };
          // ---- Public API ----`);
      await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
        name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
    });
    await page.goto('https://dictation.test/');
    await page.evaluate(() => {
      const { state, wireControls, ensureSessionActivity, refreshSessionInputAdmission } = window.dictationTest;
      document.getElementById('connecting-overlay').style.display = 'none';
      document.getElementById('session-frame').style.display = 'flex';
      state.view = 'session'; state.activeSessionId = 'fixture-gm'; state.activeSessionRole = 'dm';
      state.activeCampaignId = 'fixture';
      state.campaigns = [{ campaign_id: 'fixture', lifecycle: 'active', turn_id: 'turn-fixture',
        sessions: [{ session_id: 'fixture-gm', role: 'dm', status: 'active', provider_id: 'test', model_id: 'fixture' }] }];
      window.submissions = []; window.cancelled = []; window.nextTranscript = 'I walk north.';
      state.bridge = {
        dictation_start: async id => { window.captureId = id; },
        dictation_stop: () => new Promise(resolve => { window.finishDictation = resolve; }),
        dictation_cancel: async id => { window.cancelled.push(id); },
        submit_session_input: command => { window.submissions.push(command); return new Promise(() => {}); },
      };
      ensureSessionActivity({ sessionId: 'fixture-gm', sessionRole: 'dm', campaignId: 'fixture' });
      wireControls(); refreshSessionInputAdmission();
    });
    const input = page.locator('#player-input'), mic = page.getByRole('button', { name: 'Start dictation', exact: true });
    await input.fill('I wait.');
    await input.press('Alt+m');
    await page.getByRole('button', { name: 'Stop dictation', exact: true }).waitFor();
    assert.equal(await page.locator('#dictation-toggle').getAttribute('aria-pressed'), 'true');
    assert.match(await page.locator('#dictation-status').innerText(), /Recording/);
    assert.equal(await page.locator('#dictation-status').getAttribute('aria-live'), 'polite');
    assert.equal(await input.isEditable(), true);
    assert.equal(await page.evaluate(() => window.submissions.length), 0);
    await page.locator('#dictation-toggle').focus(); await page.keyboard.press('Space');
    assert.match(await page.locator('#dictation-status').innerText(), /Transcribing/);
    await input.fill('I wait outside.');
    await page.evaluate(() => window.finishDictation('and listen'));
    await page.locator('#dictation-review-panel').waitFor();
    assert.equal(await input.inputValue(), 'I wait outside.');
    await page.getByLabel('Review transcript').fill('and watch the window');
    await page.getByRole('button', { name: 'Insert transcript at cursor' }).focus();
    await page.keyboard.press('Enter');
    assert.equal(await input.inputValue(), 'I wait outside. and watch the window');
    assert.equal(await page.evaluate(() => window.submissions.length), 0);
    await page.getByRole('button', { name: 'Undo dictation' }).click();
    assert.equal(await input.inputValue(), 'I wait outside.');

    await mic.focus(); await page.keyboard.press('Enter');
    await page.getByRole('button', { name: 'Stop dictation', exact: true }).waitFor();
    await page.keyboard.press('Escape');
    assert.equal(await input.inputValue(), 'I wait outside.');
    assert.equal(await mic.getAttribute('aria-pressed'), 'false');

    await page.evaluate(() => {
      const bridge = window.dictationTest.state.bridge;
      window.validDictationStart = bridge.dictation_start;
      bridge.dictation_start = async () => { throw 'permission'; };
    });
    await mic.press('Enter');
    await page.waitForFunction(() => document.getElementById('dictation-status').textContent.includes('permission was denied'));
    assert.equal(await input.inputValue(), 'I wait outside.');
    assert.equal(await input.isEditable(), true);
    await page.evaluate(() => { window.dictationTest.state.bridge.dictation_start = window.validDictationStart; });

    await mic.press('Enter');
    await page.getByRole('button', { name: 'Stop dictation', exact: true }).click();
    await page.evaluate(() => {
      const api = window.dictationTest;
      api.state.sessionDrafts.set('other-gm', { text: 'The other campaign draft.' });
      api.state.activeCampaignId = 'other'; api.state.activeSessionId = 'other-gm';
      api.restoreSelectedSessionDraft(); window.finishDictation('Old campaign text');
    });
    assert.equal(await input.inputValue(), 'The other campaign draft.');
    assert.equal(await page.locator('#dictation-review-panel').isVisible(), false);
    await page.evaluate(() => {
      const api = window.dictationTest;
      api.state.activeCampaignId = 'fixture'; api.state.activeSessionId = 'fixture-gm';
      api.restoreSelectedSessionDraft(); api.refreshSessionInputAdmission();
    });
    assert.equal(await input.inputValue(), 'I wait outside.');

    await mic.press('Enter');
    await page.getByRole('button', { name: 'Stop dictation', exact: true }).click();
    await page.evaluate(() => window.finishDictation('I knock.'));
    await page.waitForFunction(() => document.getElementById('dictation-status').textContent.startsWith('Dictation ready'));
    assert.equal(await input.inputValue(), 'I wait outside. I knock.');
    await input.fill('I wait outside and knock softly.');
    if (process.env.SYLVIA_DICTATION_SCREENSHOT) await page.screenshot({ path: process.env.SYLVIA_DICTATION_SCREENSHOT });
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await input.press('Enter');
    assert.equal(await page.evaluate(() => window.submissions.length), 1);
    assert.equal(await page.evaluate(() => window.submissions[0].user_text), 'I wait outside and knock softly.');
    assert.equal(await page.locator('#dictation-review-panel').isVisible(), false);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
