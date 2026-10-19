const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

async function withComposer(check) {
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
        'panel-host.js', 'shot-playback.js', 'piece-table.js', 'campaign-plan-browser.js', 'assets/sylvia-portrait.png'];
      if (!allowed.includes(name)) return route.abort();
      let body = fs.readFileSync(path.join(frontend, name));
      if (name === 'app.js') body = body.toString().replace(
        "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
        .replace('  // ---- Public API ----', `window.dictationTest = { state, wireControls,
          refreshSessionInputAdmission, ensureSessionActivity, restoreSelectedSessionDraft,
          renderRulesetPanelProjection, applyPanelSelection };
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
        dictation_capabilities: async () => ({ engines: [{ id: 'test', language: 'en-GB', label: 'English (United Kingdom)' }] }),
        dictation_start: async (id, engine) => { window.captureId = id; window.selectedSpeechEngine = engine; },
        dictation_stop: () => new Promise(resolve => { window.finishDictation = resolve; }),
        dictation_cancel: async id => { window.cancelled.push(id); },
        submit_session_input: command => {
          window.cancellationsAtSubmit = window.cancelled.length;
          window.submissions.push(command); return new Promise(() => {});
        },
      };
      ensureSessionActivity({ sessionId: 'fixture-gm', sessionRole: 'dm', campaignId: 'fixture' });
      wireControls(); refreshSessionInputAdmission();
    });
    await check(page);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

test('keyboard dictation reviews text and ordinary Send submits exactly once', { timeout: 30000 }, async () => {
  await withComposer(async page => {
    const input = page.locator('#player-input'), mic = page.getByRole('button', { name: 'Start dictation', exact: true });
    await input.fill('I wait.');
    await input.press('Alt+m');
    await page.getByRole('button', { name: 'Stop dictation', exact: true }).waitFor();
    assert.equal(await page.locator('#dictation-toggle').getAttribute('aria-pressed'), 'true');
    assert.match(await page.locator('#dictation-status').innerText(), /Recording/);
    assert.equal(await page.locator('#dictation-status').getAttribute('aria-live'), 'polite');
    assert.equal(await input.isEditable(), true);
    assert.equal(await page.getByLabel('Speech language').inputValue(), 'test');
    assert.equal(await page.evaluate(() => window.selectedSpeechEngine), 'test');
    await page.locator('#dictation-options summary').click();
    assert.match(await page.locator('#dictation-privacy').innerText(), /does not save recordings/);
    await page.locator('#dictation-options summary').click();
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
    await page.evaluate(() => {
      const api = window.dictationTest;
      api.state.activeCampaignId = null; api.state.activeSessionId = 'builder-fixture';
      api.state.activeSessionRole = 'builder';
      api.state.builderChats = [{ session: { session_id: 'builder-fixture', role: 'builder',
        status: 'active', provider_id: 'test', model_id: 'fixture' } }];
      const activity = api.ensureSessionActivity(); activity.phase = 'working'; activity.attemptId = 'builder-running';
      window.steering = [];
      api.state.bridge.steer_session = async (...args) => { window.steering.push(args); };
      api.refreshSessionInputAdmission();
    });
    await input.fill('Use a desert.');
    await mic.click();
    await page.getByRole('button', { name: 'Stop dictation', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: 'Send message', exact: true }).isVisible(), false);
    await input.press('Enter');
    await page.waitForFunction(() => window.steering.length === 1 && document.getElementById('player-input').value === '');
    await page.evaluate(() => window.finishDictation('late Builder words'));
    assert.equal(await input.inputValue(), '');
    assert.equal(await page.locator('#dictation-review-panel').isVisible(), false);
    assert.equal(await page.evaluate(() => window.submissions.length), 1);
  });
});

test('dictation during piece confirmation stays in the draft and ordinary Send cancels capture before owner work',
  { timeout: 30000 }, async () => {
  await withComposer(async page => {
    await page.evaluate(() => {
      const api = window.dictationTest;
      api.state.activeSnapshot = { revision: 4 };
      Object.assign(api.state.bridge, {
        snapshot: async () => ({ revision: 4 }),
        preview_panel_action: async command => {
          window.piecePreview = command;
          return { confirmation_required: true, confirmation: 'ghost-only', label: 'Run program' };
        },
        execute_panel_action: async command => { window.pieceExecution = command; },
      });
      const projection = { campaign_id: 'fixture', revision: 4,
        player_control: { available: true, actors: [{ id: 'alpha' }, { id: 'bravo' }] }, surfaces: [
          { id: 'relay', title: 'Relay', renderer: 'piece_table', minimum_size: 96, default_size: 180,
            actions: [{ id: 'run', label: 'Run program', available: true, bindings: { selected: 'piece' } }],
            data: { format: 1, areas: [{ position: 'bottom', groups: [{ label: 'Programs', pieces: [
              { id: 'ghost', label: 'Ghost route', action_id: 'run' },
            ] }] }] } },
          { id: 'party', title: 'Party', renderer: 'party', minimum_size: 96, default_size: 180,
            actions: [], data: { participants: [
              { id: 'alpha', kind: 'character', data: { name: 'Asha' } },
              { id: 'bravo', kind: 'character', data: { name: 'Bram' } },
            ], entities: {} } },
        ] };
      api.state.bridge.campaign_panels = async () => projection;
      api.renderRulesetPanelProjection(projection);
    });
    const input = page.locator('#player-input');
    await input.fill('Keep the program ready.');
    await page.getByRole('button', { name: 'Start dictation', exact: true }).click();
    await page.getByRole('button', { name: 'Stop dictation', exact: true }).click();
    await page.getByRole('button', { name: 'Run program: Ghost route', exact: true }).click();
    const dialog = page.locator('dialog.player-action-dialog');
    await dialog.waitFor();
    await page.evaluate(() => {
      window.dictationTest.applyPanelSelection('party', { character: 'bravo' });
      window.finishDictation('and inspect Bram');
    });
    await page.waitForFunction(() => document.getElementById('player-input').value.includes('inspect Bram'));
    assert.equal(await page.evaluate(() => window.submissions.length), 0);
    assert.equal(await page.evaluate(() => window.pieceExecution), undefined);
    assert.deepEqual(await page.evaluate(() => window.piecePreview.selection), { piece: 'ghost' });
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    assert.equal(await input.inputValue(), 'Keep the program ready. and inspect Bram');
    assert.equal(await page.evaluate(() => window.dictationTest.state.characterInspection.id), 'bravo');
    assert.equal(await page.evaluate(() => window.pieceExecution), undefined);

    // The ordinary Send path must cancel an unfinished transcription and send
    // only the editable draft, once, without committing the rejected piece.
    await page.getByRole('button', { name: 'Start dictation', exact: true }).click();
    await page.getByRole('button', { name: 'Stop dictation', exact: true }).click();
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await page.evaluate(() => window.finishDictation('late words must be discarded'));
    await input.press('Enter');
    assert.equal(await page.evaluate(() => window.submissions.length), 1);
    assert.equal(await page.evaluate(() => window.submissions[0].user_text),
      'Keep the program ready. and inspect Bram');
    assert.ok(await page.evaluate(() => window.cancellationsAtSubmit) > 0);
    assert.equal(await page.locator('#dictation-review-panel').isVisible(), false);
    assert.equal(await page.evaluate(() => window.pieceExecution), undefined);
    assert.doesNotMatch(await input.inputValue(), /late words/);
  });
});
