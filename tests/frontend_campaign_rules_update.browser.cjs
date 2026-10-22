// Browser component + real Python owner. Native windows/provider inference are not substituted proof.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const { chromium } = require('playwright');
const repo = path.resolve(__dirname, '..');

test('rendered review/cancel/conflict/adopt controls preserve real saved campaigns and workspace',
  async () => {
    const evidence = path.join(repo, '.scratch', `rules-update-ui-${Date.now()}`);
    fs.mkdirSync(evidence, { recursive: true });
    const python = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python';
    const server = spawn(path.join(repo, python), ['-m', 'tests.rules_update_ui_owner', evidence], {
      cwd: repo, env: { ...process.env, PYTHONUTF8: '1', TEMP: evidence, TMP: evidence,
        SYLVIA_GM_TEST_FULL_AUDIT: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = '';
    server.stderr.on('data', chunk => { stderr += chunk; });
    let browser;
    try {
      const url = await new Promise((resolve, reject) => {
        const lines = readline.createInterface({ input: server.stdout });
        lines.once('line', line => { lines.close(); resolve(JSON.parse(line).url); });
        server.once('error', reject);
        server.once('exit', code => reject(new Error(`Test owner exited ${code}: ${stderr}`)));
      });
      browser = await chromium.launch({ headless: true,
        channel: process.platform === 'win32' ? 'msedge' : undefined });
      const page = await browser.newPage({ viewport: { width: 1180, height: 1050 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/app.js', async route => {
        let source = fs.readFileSync(path.join(repo, 'src-tauri/frontend/app.js'), 'utf8');
        source = source.replace("document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '');
        await route.fulfill({ body: source, contentType: 'text/javascript' });
      });
      await page.goto(`${url}/app.html`);
      await page.evaluate(async () => {
        const rpc = async (method, params = {}) => {
          const response = await fetch('/command', { method: 'POST',
            body: JSON.stringify({ method, params }) });
          const result = await response.json();
          if (result.error) throw new Error(result.error);
          return result.result;
        };
        const bridge = {
          campaign_rules_update_options: id => rpc('campaign_rules_update_options', { campaign_id: id }),
          preview_campaign_rules_update: (id, selected) => rpc('preview_campaign_rules_update', { campaign_id: id, candidate_id: selected }),
          adopt_campaign_rules_update: command => rpc('adopt_campaign_rules_update', command),
        };
        window.rulesFixture = { rpc, campaign: 'running', bridge };
        const root = document.getElementById('campaign-rules-update');
        // Use the production card, styles and component. The rest of the desktop shell stays inactive.
        const heading = document.createElement('h2'); heading.textContent = 'Synthetic campaign · Rules update';
        document.body.replaceChildren(heading, root);
        document.body.style.cssText = 'display:block;height:auto;padding:28px;max-width:1040px;margin:auto;overflow:auto';
        const before = await rpc('fixture_snapshot');
        const selectPanels = async () => {
          const value = await rpc('fixture_snapshot');
          const projection = value.panels;
          rulesFixture.layout = rulesFixture.store.select({ campaignId: 'running',
            rulesetId: projection.ruleset_id, surfaces: projection.surfaces, original: projection.layout });
          return value;
        };
        rulesFixture.store = SylviaPanelWorkspace.createStore(localStorage);
        await selectPanels();
        rulesFixture.store.change({ type: 'move', panelId: 'clockwork.panel', destination: { windowId: 'new' } });
        rulesFixture.store.change({ type: 'move', panelId: 'status', destination: { windowId: 'new' } });
        rulesFixture.store.snapshot().windows.forEach((item, index) => {
          rulesFixture.store.change({ type: 'placement', windowId: item.id,
            placement: { x: 120 + index * 200, y: 80, width: 860, height: 640 } });
        });
        rulesFixture.store.change({ type: 'size', panelId: 'clockwork.panel', height: 321 });
        rulesFixture.store.change({ type: 'sidebar-width', width: 340 });
        rulesFixture.store.saveView('synthetic-scene', { zoom: 1.4, scrollLeft: 20, scrollTop: 30 });
        rulesFixture.before = before;
        rulesFixture.beforeLayout = rulesFixture.store.snapshot();
        rulesFixture.controller = SylviaCampaignRulesUpdate.mount({ root,
          bridge: () => bridge, campaign: () => rulesFixture.campaign,
          adopted: async () => {
            // The main campaign refresh clears the review while keeping this component mounted.
            rulesFixture.controller.reset(); rulesFixture.after = await selectPanels();
          },
        });
      });
      const button = name => page.locator(`[data-rules="${name}"]`);
      await button('load').click();
      await page.waitForFunction(() => document.querySelector('[data-rules="versions"]').options.length === 2);
      await button('versions').selectOption('candidate-new');
      await button('preview').click();
      await button('adopt').waitFor({ state: 'visible' });
      assert.match(await button('review').innerText(), /New sidebar panels: Journal/);
      await page.screenshot({ path: path.join(evidence, '01-compatible-preview.png'), fullPage: true });
      await button('cancel').click();
      assert.deepEqual(await page.evaluate(async () => (await rulesFixture.rpc('fixture_snapshot')).campaign),
        await page.evaluate(() => rulesFixture.before.campaign));
      await button('versions').selectOption('candidate-conflict');
      await button('preview').click();
      await page.waitForFunction(() => document.querySelector('[data-rules="review"]').textContent.includes('Update blocked'));
      assert.equal(await button('adopt').isVisible(), false);
      await page.screenshot({ path: path.join(evidence, '02-conflict-blocked.png'), fullPage: true });
      await button('versions').selectOption('candidate-new');
      await button('preview').click();
      await button('adopt').waitFor({ state: 'visible' });
      await button('adopt').click();
      await page.waitForFunction(() => Boolean(rulesFixture.after));
      assert.match(await button('status').innerText(), /Rules updated/);
      assert.equal(await button('load').isEnabled(), true);
      assert.equal(await button('versions').isEnabled(), true);
      const result = await page.evaluate(() => ({ before: rulesFixture.before,
        after: rulesFixture.after, layout: rulesFixture.layout, oldLayout: rulesFixture.beforeLayout }));
      assert.deepEqual(result.after.campaign.state, result.before.campaign.state);
      assert.deepEqual(result.after.other, result.before.other);
      assert.deepEqual(result.layout.windows, result.oldLayout.windows);
      assert.deepEqual(result.layout.views, result.oldLayout.views);
      assert.deepEqual(result.layout.panel_sizes, result.oldLayout.panel_sizes);
      assert.equal(result.layout.sidebar_width, 340);
      const ids = tree => !tree ? [] : tree.kind === 'tabs' ? tree.panels : [...ids(tree.first), ...ids(tree.second)];
      assert.equal(ids(result.layout.sidebar).filter(id => id === 'journal').length, 1);
      assert.equal(result.after.lineage.length, 2);
      await page.screenshot({ path: path.join(evidence, '03-adopted.png'), fullPage: true });
      await page.evaluate(async () => { await rulesFixture.rpc('fixture_reopen'); });
      const reopened = await page.evaluate(async () => {
        const value = await rulesFixture.rpc('fixture_snapshot');
        const store = SylviaPanelWorkspace.createStore(localStorage);
        return { value, layout: store.select({ campaignId: 'running', rulesetId: value.panels.ruleset_id,
          surfaces: value.panels.surfaces, original: value.panels.layout }) };
      });
      assert.deepEqual(reopened.value.campaign.state, result.before.campaign.state);
      assert.deepEqual(reopened.layout, result.layout);
      assert.deepEqual(errors, []);
      console.log(`Synthetic screenshots: ${evidence}`);
    } finally {
      if (browser) await browser.close();
      server.kill();
    }
  });
