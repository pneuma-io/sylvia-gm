// Real child HTML/JS startup and projection rendering with a synthetic native event transport.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

async function withPanel(failStartup, check) {
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.stack));
    await page.route('https://sylvia-panel.test/**', async route => {
      const name = new URL(route.request().url()).pathname.slice(1);
      if (!['panel.html', 'app.css', 'app.js', 'panel-workspace.js', 'shot-playback.js', 'panel-host.js'].includes(name)) return route.abort();
      await route.fulfill({ body: fs.readFileSync(path.join(frontend, name)),
        contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html' });
    });
    await page.addInitScript(failStartup => {
      const context = { campaign_id: 'synthetic', session_id: 'synthetic-gm', generation: 'generation-one' };
      const windowId = 'party-window';
      window.__sylviaPanelContext = { context, window_id: windowId };
      const listeners = new Map();
      window.startupCalls = [];
      window.selectionRequests = [];
      let presentation;
      window.presentPanel = patch => {
        Object.assign(presentation, patch);
        presentation.presentation_sequence += 1;
        listeners.get('panel-workspace')({ payload: { context, window_id: windowId, payload: presentation } });
      };
      window.__TAURI__ = {
        event: { listen: async (name, callback) => { listeners.set(name, callback); return () => listeners.delete(name); } },
        core: { invoke: async (command, args) => {
          window.startupCalls.push(command);
          if (command === 'panel_window_message' && args.payload.type === 'ready') {
            if (failStartup) throw new Error('Synthetic transport unavailable');
            presentation = {
              presentation_sequence: 1, busy: false, theme: 'slate',
              characterInspection: { campaignId: 'synthetic', rulesetId: 'fixture', id: 'asha', expanded: [] },
              projection: { campaign_id: 'synthetic', ruleset_id: 'fixture', revision: 1, shot_effects: [], surfaces: [
                { id: 'party', title: 'Party', renderer: 'party', actions: [], data: {
                  participants: [{ id: 'asha', kind: 'character', data: { name: 'Asha' } },
                    { id: 'bram', kind: 'character', data: { name: 'Bram' } }],
                  entities: { asha: { hp: 8, ap: 4, res: 7, stance: 'STANDING' }, bram: { hp: 6, skills: { lore: 4 } } },
                } },
              ] },
              layout: { version: 1, sidebar: null, hidden: [], known: ['party'], views: {}, windows: [
                { id: windowId, title: 'Party', tree: { kind: 'tabs', id: 'party-tabs', panels: ['party'], active: 'party' } },
              ] },
            };
            window.presentPanel({});
          } else if (command === 'panel_window_message' && args.payload.method === 'selection') {
            window.selectionRequests.push(args.payload);
            listeners.get('panel-window-reply')({ payload: { context, request_id: args.payload.request_id, result: {} } });
          }
          return {};
        } },
      };
    }, failStartup);
    await page.goto('https://sylvia-panel.test/panel.html');
    await page.waitForFunction(() => document.getElementById('session-status-text').textContent !== 'Loading campaign panels…');
    const status = await page.locator('#session-status-text').textContent();
    const content = await page.locator('#ruleset-panels').textContent();
    await check(page, status, content, errors);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

for (const failStartup of [false, true]) test(`detached panel startup ${failStartup ? 'shows a recovery error' : 'renders the first owner projection'}`, { timeout: 15000 }, async () => {
  await withPanel(failStartup, async (page, status, content, errors) => {
    if (failStartup) {
      assert.match(status, /Could not load campaign panels: .*Synthetic transport unavailable/);
      assert.match(status, /Close this window to return the panels to the sidebar/);
    } else {
      assert.ok(content.includes('Asha'), `Panel did not render: status=${status}; errors=${JSON.stringify(errors)}`);
      assert.equal(status, '');
    }
  });
});

test('detached inspection retains keyboard focus after the asynchronous owner presentation', { timeout: 15000 }, async () => {
  await withPanel(false, async page => {
    await page.getByRole('button', { name: 'Inspect Bram', exact: true }).focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), 'Inspect Bram');
    assert.deepEqual(await page.evaluate(() => window.selectionRequests.map(request => request.args)),
      [{ surfaceId: 'party', patch: { character: 'bram' } }]);
    await page.evaluate(() => window.presentPanel({ characterInspection: {
      campaignId: 'synthetic', rulesetId: 'fixture', id: 'bram', expanded: [],
    } }));
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), 'Inspecting Bram');
    await page.getByLabel('Details for Bram', { exact: true }).focus();
    await page.keyboard.press('Enter');
    await page.evaluate(() => window.presentPanel({ characterInspection: {
      campaignId: 'synthetic', rulesetId: 'fixture', id: 'bram', expanded: ['bram'],
    } }));
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), 'Details for Bram');
    assert.equal(await page.locator('[data-character-expansion="bram"]').evaluate(details => details.open), true);
    await page.getByRole('button', { name: 'Inspecting Bram', exact: true }).focus();
    await page.evaluate(() => window.presentPanel({ characterInspection: {
      campaignId: 'synthetic', rulesetId: 'other-rules', id: 'bram', expanded: [],
    }, projection: { campaign_id: 'synthetic', ruleset_id: 'other-rules', revision: 2, shot_effects: [], surfaces: [
      { id: 'party', title: 'Party', renderer: 'party', actions: [], data: {
        participants: [{ id: 'bram', kind: 'character', data: { name: 'Bram' } }], entities: { bram: { hp: 10 } },
      } },
    ] } }));
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), null,
      'A new Ruleset must not inherit focus by reusing character identifiers');
    assert.equal(await page.locator('[data-character-expansion="bram"]').evaluate(details => details.open), false);
  });
});
