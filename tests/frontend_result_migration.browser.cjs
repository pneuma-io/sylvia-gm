// Synthetic browser composition using the shipped markup/styles, not installed acceptance.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

test('migrated results, historical checks and Builder controls fit normal and narrow chat', { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(3000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('https://sylvia-results.test/**', async route => {
      const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
      if (!['app.html', 'app.css', 'app.js', 'panel-workspace.js', 'shot-playback.js',
        'panel-host.js', 'assets/sylvia-portrait.png'].includes(name)) return route.abort();
      let body = fs.readFileSync(path.join(frontend, name));
      if (name === 'app.js') body = body.toString().replace(
        "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
        .replace('  // ---- Public API ----', `window.resultTest = { wireBuilderResultActions, renderProjection, initSidebarResize, wireControls };
          // ---- Public API ----`);
      await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
        name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
    });
    await page.goto('https://sylvia-results.test/');
    await page.evaluate(() => {
      document.getElementById('connecting-overlay').style.display = 'none';
      document.getElementById('session-frame').style.display = 'flex';
      document.getElementById('session-title').textContent = 'FC-16 synthetic presentation checks';
      const row = (label, value) => ({ label, value, emphasis: false });
      const contents = [
        { tool_name: 'attack', result: { status: 'committed', result_card: { version: 1,
          footprint: 'compact', title: 'Spear', subtitle: 'Attack roll',
          equation: { die_sides: 20, roll: 15, modifier: 3, total: 18 },
          summary: [row('Outcome', 'Hit'), row('Damage', 6), row('Target', 'Training guard')],
          details: [row('D20 results', [15]), row('Ability', 1), row('Proficiency', 2)],
          details_label: 'Calculation', actions: [] } } },
        { tool_name: 'resource', result: { status: 'committed', result_card: { version: 1,
          footprint: 'inline', title: 'Power restored', summary: [row('Charge', 2)],
          details: [row('Recorded effect', 'Relay online')], actions: [] } } },
        { tool_name: 'd20_test', parameters: { skill: 'survival', target_number: 14 },
          result: { context: { _outcome: 'failure', _adjusted_total: 8,
            _d20_test: { results: [5], total: 5, modifier: 3 }, actor: { data: { name: 'Mira' } } } } },
        { tool_name: 'resource', result: { status: 'rejected', result_card: null } },
        { tool_name: 'publish_campaign_plan', result: { success: true, title: 'The relay',
          readable_preview: '<script>unsafe</script> ' + 'A long saved Plan preview. '.repeat(80),
          library_artifact: { id: 'saved-plan' } } },
        { tool_name: 'test_ruleset_draft', result: { status: 'failed', scenarios: [
          { id: 'cost', status: 'failed', steps: [{ step: 0, failures: [{ subject: 'state',
            path: ['charge'], expected: 3, actual: 0 }] }] } ] } },
      ];
      const container = document.getElementById('chronology');
      container.innerHTML = contents.map(content => window.SylviaGMFrontend.toolResultMarkup(content, '')).join('') +
        window.resultTest.renderProjection({ kind: 'think', sequence: 1,
          text: 'Saved <private> reasoning', collapsed_by_default: false });
      window.resultTest.wireBuilderResultActions(container);
      window.resultTest.wireControls();
      window.resultTest.initSidebarResize();
    });
    assert.equal(await page.locator('#chronology > .result-card').count(), 5);
    assert.equal(await page.locator('#chronology > .inline-feedback').count(), 1);
    assert.doesNotMatch(await page.locator('#chronology').innerText(), /Operation completed|Operation pending/);
    const tile = await page.locator('.result-card-compact').first().evaluate(card => {
      const style = getComputedStyle(card);
      return { border: style.borderTopWidth, radius: style.borderTopLeftRadius,
        padding: style.paddingLeft, background: style.backgroundColor };
    });
    assert.equal(tile.border, '1px');
    assert.equal(tile.radius, '8px');
    assert.equal(tile.padding, '15px');
    assert.notEqual(tile.background, 'rgba(0, 0, 0, 0)');
    assert.equal(await page.locator('.tool-card, [class*="skill-check-"], #chronology .story-collapse').count(), 0);
    const thinking = page.locator('.agent-thinking');
    assert.equal(await thinking.locator('.thinking-body').isVisible(), false);
    assert.equal(await thinking.locator('summary').innerText(), 'Thinking May contain spoilers');
    await thinking.locator('summary').click();
    assert.equal(await thinking.locator('.thinking-body').innerText(), 'Saved <private> reasoning');
    assert.equal(await thinking.locator('private').count(), 0);
    const preview = page.locator('.campaign-plan-preview');
    assert.equal(await preview.isVisible(), false);
    await page.getByRole('button', { name: 'Preview', exact: true }).click();
    assert.equal(await preview.isVisible(), true);
    assert.match(await preview.innerText(), /<script>unsafe<\/script>/);
    assert.equal(await preview.locator('script').count(), 0);
    await page.getByRole('button', { name: 'Hide preview', exact: true }).click();
    assert.equal(await preview.isVisible(), false);
    for (const width of [1400, 962, 390]) {
      await page.setViewportSize({ width, height: 1100 });
      if (width === 390) {
        // Use existing ordinary controls to give the narrow chat its full width.
        await page.locator('#toggle-left-sidebar-btn').click();
        await page.locator('#toggle-right-sidebar-btn').click();
        assert.equal(await page.locator('.portrait-col').isVisible(), false);
        assert.equal(await page.locator('.right-sidebar').isVisible(), false);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      }
      for (const expanded of [false, true]) {
        await page.locator('#chronology details').evaluateAll((nodes, open) => {
          for (const node of nodes) node.open = open;
        }, expanded);
        const bounds = await page.locator('#chronology').evaluate(container => {
          const parent = container.getBoundingClientRect();
          return [...container.querySelectorAll('.result-card')].map(card => {
            const rect = card.getBoundingClientRect();
            return { left: rect.left, right: rect.right, parentLeft: parent.left,
              parentRight: parent.right, scroll: card.scrollWidth, client: card.clientWidth };
          });
        });
        for (const bound of bounds) {
          assert(bound.left >= bound.parentLeft && bound.right <= bound.parentRight + 1);
          assert(bound.scroll <= bound.client + 1, `overflow at ${width}px: ${JSON.stringify(bound)}`);
        }
        await page.locator('#chronology').evaluate(async container => {
          await Promise.all(container.getAnimations({subtree: true}).filter(animation =>
            animation.effect?.getTiming().iterations !== Infinity).map(animation => animation.finished));
        });
        if (process.env.SYLVIA_FC16_CAPTURE_DIR) {
          fs.mkdirSync(process.env.SYLVIA_FC16_CAPTURE_DIR, { recursive: true });
          await page.locator('#chronology').screenshot({ path: path.join(process.env.SYLVIA_FC16_CAPTURE_DIR,
            `source-${width}-${expanded ? 'expanded' : 'collapsed'}.png`) });
        }
      }
    }
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
