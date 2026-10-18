// Source-browser proof only: real input/rendering with a synthetic Interview selection owner.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

test('Plan browsing keeps selection across filters and separates exact same-title copies',
  { timeout: 30000 }, async () => {
    const browser = await chromium.launch({ headless: true,
      channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
    try {
      const page = await browser.newPage({ viewport: { width: 1000, height: 850 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.setContent('<main style="width:100%;padding:20px"><div id="plans"></div><output id="selection"></output></main>');
      await page.addStyleTag({ path: path.resolve(__dirname, '../src-tauri/frontend/app.css') });
      await page.addScriptTag({ path: path.resolve(__dirname, '../src-tauri/frontend/campaign-plan-browser.js') });
      const catalogue = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../campaign-plans/catalogue.json'), 'utf8'));
      await page.evaluate(catalogue => {
        const plans = catalogue.map(row => ({ id: row.id, title: row.title, summary: row.pitch,
          browsing: row, starting_party: { player_character: { mode: row.character_mode } } }));
        plans.push({ id: 'imported', title: '<img src=x onerror="window.injected=true">',
          summary: 'Imported Plan without optional length metadata.' });
        plans.push({ id: 'copy-a', title: 'Same title', reusable: true, content_sha256: 'a'.repeat(64) });
        plans.push({ id: 'copy-b', title: 'Same title', reusable: true, content_sha256: 'b'.repeat(64) });
        const state = { plans, selectedId: '', contextKey: 'interview-one' };
        const widget = window.SylviaPlanBrowser.create(document.getElementById('plans'), id => {
          state.selectedId = id; widget.update(state);
          document.getElementById('selection').textContent = id;
        });
        window.fixture = { state, widget }; widget.update(state);
      }, catalogue);
      assert.equal(await page.locator('[data-plan-choice]').count(), 19);
      const riftPreview = await page.locator('[data-plan-choice="rift-awakening"]').innerText();
      assert.match(riftPreview, /woodland clearing/);
      assert.match(riftPreview, /Thornwall/);
      assert.doesNotMatch(riftPreview, /deciding whether to stay|seek a way home|city of Nexus/i);
      assert.doesNotMatch(await page.locator('#plans').innerText(), /raider-held wayhouse|hostile Rift echoes|flooded records depot|contested Warden vault|guarded causeway/i);
      assert.equal(await page.locator('[data-plan-choice][aria-pressed="true"]').count(), 0);
      await page.locator('[data-plan-choice="a-debt-in-iron"]').click();
      assert.match(await page.locator('[data-plan-detail]').innerText(), /Play as Tavin Bell/);
      await page.locator('[data-plan-format]').selectOption('combat');
      assert.equal(await page.locator('[data-plan-choice]').count(), 6);
      assert.equal(await page.locator('#selection').innerText(), 'a-debt-in-iron');
      await page.getByRole('button', { name: 'Show selected plan' }).click();
      assert.equal(await page.locator('[data-plan-format]').inputValue(), '');
      await page.locator('[data-plan-search]').fill('Same title');
      assert.equal(await page.locator('[data-plan-choice]').count(), 2);
      await page.getByRole('button', { name: /Saved copy bbbbbbbbbbbb/ }).press('Enter');
      assert.equal(await page.locator('#selection').innerText(), 'copy-b');
      await page.locator('[data-plan-search]').fill('no such adventure');
      assert.match(await page.locator('[data-plan-list]').innerText(), /No matching plans/);
      assert.equal(await page.locator('#selection').innerText(), 'copy-b');
      await page.locator('[data-plan-search]').fill('Imported Plan');
      assert.equal(await page.locator('[data-plan-choice]').count(), 1);
      assert.equal(await page.locator('#plans img').count(), 0);
      await page.evaluate(() => {
        window.fixture.state.contextKey = 'interview-two';
        window.fixture.state.selectedId = 'the-map-of-tomorrow';
        window.fixture.widget.update(window.fixture.state);
      });
      assert.equal(await page.locator('[data-plan-search]').inputValue(), '');
      assert.equal(await page.locator('[data-plan-choice][aria-pressed="true"]').getAttribute('data-plan-choice'), 'the-map-of-tomorrow');
      // Sidebars can leave a narrow Interview even when the desktop viewport is wide.
      await page.locator('main').evaluate(main => { main.style.width = '430px'; });
      const narrowLayout = await page.evaluate(() => {
        const list = document.querySelector('[data-plan-list]').getBoundingClientRect();
        const detail = document.querySelector('[data-plan-detail]').getBoundingClientRect();
        return { stacked: detail.top >= list.bottom && Math.abs(detail.left - list.left) < 1,
          overflow: document.getElementById('plans').scrollWidth > document.getElementById('plans').clientWidth };
      });
      assert.equal(narrowLayout.stacked, true, 'narrow Interview content must stack at a wide viewport');
      assert.equal(narrowLayout.overflow, false);
      await page.locator('main').evaluate(main => { main.style.width = '100%'; });
      for (const theme of ['slate', 'sylvia', 'sylvia-dark']) {
        await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
        await page.setViewportSize({ width: 720, height: 850 });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 720);
        assert.equal(await page.locator('[data-plan-search]').isVisible(), true);
      }
      await page.evaluate(() => {
        window.fixture.state.plans = []; window.fixture.widget.update(window.fixture.state);
      });
      assert.match(await page.locator('[data-plan-list]').innerText(), /No compatible plans/);
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  });

