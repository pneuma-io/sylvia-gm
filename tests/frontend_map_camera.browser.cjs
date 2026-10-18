const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

// Exercise real layout and delayed preview rendering; a DOM stub cannot model scroll clamping.
for (const [pageName, width, height] of [['app.html', 1402, 932], ['panel.html', 500, 480]]) {
test(`${pageName}: tactical camera survives pending and settled action previews`, { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'], channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const page = await browser.newPage({ viewport: { width, height } });
    await page.route('**/*', async route => {
      const name = new URL(route.request().url()).pathname.slice(1);
      if (!['app.html', 'panel.html', 'app.js', 'app.css', 'panel-workspace.js', 'panel-host.js',
        'shot-playback.js', 'campaign-plan-browser.js', 'assets/sylvia-portrait.png'].includes(name)) return route.abort();
      let body = fs.readFileSync(path.join(frontend, name));
      if (name === 'app.js') body = body.toString()
        .replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
        .replace('  // ---- Public API ----', `window.cameraTest = { state, renderRulesetPanelProjection,
          updateTacticalSelection, tacticalTargetSelection }; // ---- Public API ----`);
      await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
        name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
    });
    await page.goto(`https://sylvia-camera.test/${pageName}`);
    await page.evaluate(() => {
      document.getElementById('connecting-overlay')?.classList.add('hidden');
      const frame = document.getElementById('session-frame');
      if (frame) frame.style.display = 'flex';
      const api = window.cameraTest;
      const map = { id: 'map', title: 'Tactical map', renderer: 'tactical_grid', minimum_size: 320,
        default_size: 620, data: { map_revision: 1,
          map: { id: 'floor', bounds: { min: [0, 0, 0], max: [20, 18, 3] },
            grid: { cell_size: 1, unit: 'm', origin: [0, 0] } },
          selection: {}, terrain: [], zones: [], objectives: [], geometry: [],
          entities: [{ id: 'hero', position: [2.5, 14.5, 0], height: 1.8, display: { label: 'Hero' } },
            { id: 'target', position: [9.5, 14.5, 0], height: 1.8, display: { label: 'Target' } }] },
        actions: [{ id: 'fire', label: 'Fire', available: true, checks_required: true,
          map_preview_enabled: true, bindings: { attacker: 'actor', target: 'target', aim_point: 'aim_point' } }] };
      const projection = { campaign_id: 'camera-test', ruleset_id: 'camera-test', revision: 1,
        shot_effects: [], surfaces: [map], player_control: { available: true, actors: [{ id: 'hero', name: 'Hero' }] } };
      Object.assign(api.state, { activeCampaignId: 'camera-test', activeSessionId: 'camera-session',
        activeSessionRole: 'dm', activeSnapshot: { revision: 1 }, view: 'session' });
      window.previewReplies = [];
      api.state.bridge = { player_action_availability: command => new Promise(resolve => {
        window.previewReplies.push(() => resolve({ campaign_id: command.campaign_id,
          revision: command.expected_revision, results: command.requests.map(() => ({ available: true,
            preview: { version: 1, title: 'Confirm Fire', details: [], actions: [], summary: [
              { label: 'Actor', value: 'Hero' }, { label: 'Target', value: 'Target' },
              { label: 'Aim point', value: '9.5, 14.5, 0.9' }] },
            map_preview: { map_id: 'floor', map_revision: command.expected_revision,
              ray: { origin: [2.91, 14.5, 1.4], destination: [9.5, 14.5, .9], contacts: [] },
              exposure: { fraction: 1, cover_ids: [] }, notice: 'The action rechecks current game state.' },
          })) }));
      }) };
      api.renderRulesetPanelProjection(projection);
      api.updateTacticalSelection(map, { actor: 'hero', ...api.tacticalTargetSelection(map, 'target') });
      api.state.tacticalViews.map.zoom = .75;
      api.renderRulesetPanelProjection(projection);
      window.previewReplies.splice(0).forEach(reply => reply());
    });
    await page.getByText('Exposed silhouette: 100%', { exact: false }).waitFor();
    const mapScroll = page.locator('.tactical-scroll');
    await mapScroll.evaluate(node => { node.scrollTop = node.scrollHeight; });
    // Initial focus is already positive. Wait for this input's scroll event,
    // so the preview baseline includes the camera the fixture actually chose.
    await page.waitForFunction(() => {
      const node = document.querySelector('.tactical-scroll');
      return node && node.scrollTop > 0 &&
        window.cameraTest.state.tacticalViews.map.scrollTop === node.scrollTop;
    });
    const before = await mapScroll.evaluate(node => ({ top: node.scrollTop, height: node.clientHeight }));
    await page.evaluate(() => {
      const api = window.cameraTest;
      const next = structuredClone(api.state.panelProjection);
      next.revision += 1;
      next.surfaces[0].data.map_revision += 1;
      next.surfaces[0].data.entities[0].height = 1.15;
      api.renderRulesetPanelProjection(next);
    });
    await page.getByText('Checking Fire preview…', { exact: true }).waitFor();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const pending = await mapScroll.evaluate(node => ({ top: node.scrollTop, height: node.clientHeight }));
    assert.deepEqual(pending, before, 'The pending preview must not resize or scroll the map');
    await page.evaluate(() => window.previewReplies.splice(0).forEach(reply => reply()));
    await page.getByText('Exposed silhouette: 100%', { exact: false }).waitFor();
    const after = await mapScroll.evaluate(node => ({ top: node.scrollTop, height: node.clientHeight }));
    assert.equal(after.height, before.height);
    assert.ok(Math.abs(after.top - before.top) < 1,
      `Camera moved from ${before.top} to ${after.top} after pending/settled preview`);
    assert.equal(await page.locator('[data-tactical-preview]').evaluate(node => node.style.minHeight), '',
      'The completed preview must release its temporary height');
  } finally { await browser.close(); }
});
}
