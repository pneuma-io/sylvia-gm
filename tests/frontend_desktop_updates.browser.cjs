// Shipped About UI with synthetic release/session projections; no installer or real account.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

async function openShell(browser) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 950 } });
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://sylvia-updates.test/**', async route => {
    const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
    if (!['app.html', 'app.css', 'app.js', 'panel-workspace.js', 'shot-playback.js',
      'panel-host.js', 'assets/sylvia-portrait.png'].includes(name)) return route.abort();
    let body = fs.readFileSync(path.join(frontend, name));
    if (name === 'app.js') body = body.toString().replace(
      "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.updateTest = { state, wireControls,
        openSettings, refreshApplicationInfo, ensureSessionActivity };
        // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
      name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
  });
  await page.goto('https://sylvia-updates.test/');
  await page.evaluate(async () => {
    const { state, wireControls, openSettings, refreshApplicationInfo, ensureSessionActivity } = window.updateTest;
    window.fixture = { calls: [], current_version: '0.3.0-alpha.1', channel: 'alpha and stable',
      release: { version: '0.3.0-alpha.2', tag: 'v0.3.0-alpha.2',
        url: 'https://github.com/pneuma-io/sylvia-gm/releases/tag/v0.3.0-alpha.2',
        notes: 'A synthetic release\n<script>window.injected = true</script>\n<img src=x onerror=alert(1)>',
        packages: ['Installer', 'Portable ZIP', 'Checksums'], notes_truncated: false } };
    const fixture = window.fixture;
    state.bridge = {
      application_info: async () => ({ version: fixture.current_version, release_channel: 'alpha', signing_status: 'unsigned' }),
      check_desktop_update: async () => {
        fixture.calls.push('check');
        if (fixture.delay) await new Promise(resolve => { fixture.finish = resolve; });
        if (fixture.fail) throw new Error('Synthetic release service unavailable. Retry later.');
        return structuredClone({ current_version: fixture.current_version, channel: fixture.channel, release: fixture.release });
      },
      open_desktop_update: async tag => {
        fixture.calls.push({ open: tag });
        if (fixture.openError) throw new Error('Synthetic handoff interrupted.');
        return { opened: !fixture.noBrowser, url: fixture.release.url };
      },
    };
    state.activeSessionId = 'synthetic-gm'; state.activeSessionRole = 'dm';
    state.activeCampaignId = 'synthetic-campaign';
    state.sessionDrafts.set('synthetic-gm', { text: 'Unsent player action', ooc: 'Unsent correction' });
    state.sessionDrafts.set('synthetic-builder', { text: 'Unsent Builder request' });
    for (const [sessionId, sessionRole] of [['synthetic-gm', 'dm'], ['synthetic-builder', 'builder']]) {
      const activity = ensureSessionActivity({ sessionId, sessionRole, campaignId: 'synthetic-campaign' });
      activity.phase = 'working'; activity.attemptId = 'synthetic-in-progress-' + sessionId;
    }
    document.getElementById('player-input').value = 'Unsent visible action';
    document.getElementById('ooc-input').value = 'Unsent visible correction';
    document.getElementById('connecting-overlay').classList.add('hidden');
    document.getElementById('session-frame').style.display = 'flex';
    window.sessionSnapshot = () => JSON.stringify({
      session: state.activeSessionId, campaign: state.activeCampaignId,
      drafts: [...state.sessionDrafts], activities: [...state.sessionActivities],
      text: document.getElementById('player-input').value, ooc: document.getElementById('ooc-input').value,
    });
    fixture.before = window.sessionSnapshot();
    wireControls(); await refreshApplicationInfo(); openSettings('about');
  });
  return { page, errors };
}

async function screenshot(page, name, target = '#desktop-update-version') {
  if (!process.env.SYLVIA_UPDATE_SCREENSHOTS) return;
  fs.mkdirSync(process.env.SYLVIA_UPDATE_SCREENSHOTS, { recursive: true });
  await page.locator(target).scrollIntoViewIfNeeded().catch(() => {});
  await page.screenshot({ path: path.join(process.env.SYLVIA_UPDATE_SCREENSHOTS, name), fullPage: true });
}

test('manual release check, safe notes, defer and download handoff preserve GM/Builder work', async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const { page, errors } = await openShell(browser);
    assert.deepEqual(await page.evaluate(() => fixture.calls), [], 'opening About makes no release request');
    await page.getByRole('button', { name: 'Check for updates', exact: true }).click();
    await page.getByText('Sylvia GM 0.3.0-alpha.2 is available', { exact: true }).waitFor();
    assert.match(await page.locator('#desktop-update-notes').innerText(), /<script>/);
    assert.equal(await page.locator('#desktop-update-notes script, #desktop-update-notes img').count(), 0);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.match(await page.locator('#desktop-update-release').innerText(), /preserve isolated-data/);
    assert.match(await page.locator('#desktop-update-release').innerText(), /save or copy unsent drafts/);
    await screenshot(page, 'update-available.png');
    await screenshot(page, 'update-handoff.png', '#open-desktop-update-btn');
    await page.getByRole('button', { name: 'Later', exact: true }).click();
    assert.equal(await page.locator('#desktop-update-release').isVisible(), false);
    assert.match(await page.locator('#desktop-update-status').innerText(), /deferred/);
    await page.getByRole('button', { name: 'Check again', exact: true }).click();
    await page.getByRole('button', { name: 'Open official GitHub download', exact: true }).click();
    await page.getByText(/Opened GitHub in your browser/).waitFor();
    assert.deepEqual(await page.evaluate(() => fixture.calls), ['check', 'check', { open: 'v0.3.0-alpha.2' }]);
    assert.equal(await page.evaluate(() => window.sessionSnapshot() === fixture.before), true);
    await page.locator('#close-settings-btn').click();
    assert.equal(await page.evaluate(() => window.sessionSnapshot() === fixture.before), true);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('check interruption, retry, no update and unavailable browser remain recoverable', async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const { page, errors } = await openShell(browser);
    await page.evaluate(() => { fixture.fail = true; fixture.delay = true; });
    await page.locator('#check-desktop-update-btn').click();
    assert.equal(await page.locator('#check-desktop-update-btn').isDisabled(), true);
    await page.locator('#close-settings-btn').click();
    await page.evaluate(() => { fixture.finish(); });
    await page.waitForFunction(() => !window.updateTest.state.desktopUpdate.busy);
    await page.evaluate(() => window.updateTest.openSettings('about'));
    assert.match(await page.locator('#desktop-update-status').innerText(), /Update check failed/);
    assert.equal(await page.locator('#desktop-update-release').isVisible(), false);
    await screenshot(page, 'update-check-failed.png');
    await page.evaluate(() => { fixture.fail = false; fixture.delay = false; });
    await page.locator('#check-desktop-update-btn').click();
    await page.locator('#desktop-update-release').waitFor();
    await page.evaluate(() => { fixture.noBrowser = true; });
    await page.locator('#open-desktop-update-btn').click();
    await page.getByText(/Could not open your browser/).waitFor();
    assert.match(await page.locator('#desktop-update-status').innerText(), /https:\/\/github.com\/pneuma-io\/sylvia-gm\/releases\/tag\//);
    await page.evaluate(() => { fixture.noBrowser = false; fixture.openError = true; });
    await page.locator('#open-desktop-update-btn').click();
    await page.getByText(/Could not open the download page/).waitFor();
    await page.evaluate(() => { fixture.openError = false; });
    await page.locator('#open-desktop-update-btn').click();
    await page.getByText(/Opened GitHub in your browser/).waitFor();
    await page.evaluate(() => { fixture.release = null; });
    await page.locator('#check-desktop-update-btn').click();
    await page.getByText(/No newer alpha and stable release found/).waitFor();
    assert.equal(await page.locator('#desktop-update-release').isVisible(), false);
    assert.equal(await page.evaluate(() => window.sessionSnapshot() === fixture.before), true);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
