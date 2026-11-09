// Real child HTML/JS startup and projection rendering with a synthetic native event transport.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

async function withPanel(failStartup, check, renderer = 'party') {
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.stack));
    await page.route('https://sylvia-panel.test/**', async route => {
      const name = new URL(route.request().url()).pathname.slice(1);
      if (!['panel.html', 'app.css', 'app.js', 'panel-workspace.js', 'shot-playback.js', 'panel-host.js', 'piece-table.js'].includes(name)) return route.abort();
      await route.fulfill({ body: fs.readFileSync(path.join(frontend, name)),
        contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html' });
    });
    await page.addInitScript(({failStartup, renderer}) => {
      const context = { campaign_id: 'synthetic', session_id: 'synthetic-gm', generation: 'generation-one' };
      const windowId = 'party-window';
      window.__sylviaPanelContext = { context, window_id: windowId };
      const listeners = new Map();
      window.startupCalls = [];
      window.selectionRequests = [];
      window.panelRequests = [];
      let presentation;
      window.presentPanel = patch => {
        Object.assign(presentation, patch);
        presentation.presentation_sequence += 1;
        window.fixtureProjection = presentation.projection;
        listeners.get('panel-workspace')({ payload: { context, window_id: windowId, payload: presentation } });
      };
      window.__TAURI__ = {
        event: { listen: async (name, callback) => { listeners.set(name, callback); return () => listeners.delete(name); } },
        core: { invoke: async (command, args) => {
          window.startupCalls.push(command);
          if (command === 'panel_window_message' && args.payload.type === 'rpc') {
            const request = args.payload;
            window.panelRequests.push(request);
            if (request.method === 'selection') window.selectionRequests.push(request);
            const result = request.method === 'player_action_availability' ? {
              campaign_id: 'synthetic', revision: 1,
              results: request.args.requests.map(item => ({available: item.selection?.piece === 'ghost'})),
            } : request.method === 'campaign_panels' ? window.fixtureProjection
              : request.method === 'snapshot' ? {campaign_id: 'synthetic', revision: 1} : {};
            listeners.get('panel-window-reply')({payload: {
              context, request_id: request.request_id, result, error: null,
            }});
          }
          if (command === 'panel_window_message' && args.payload.type === 'ready') {
            if (failStartup) throw new Error('Synthetic transport unavailable');
            presentation = {
              presentation_sequence: 1, busy: false, theme: 'slate',
              characterInspection: { campaignId: 'synthetic', rulesetId: 'fixture', id: 'asha', expanded: [] },
              projection: { campaign_id: 'synthetic', ruleset_id: 'fixture', revision: 1, shot_effects: [], surfaces: [
                { id: 'party', title: 'Party', renderer, actions: renderer === 'piece_table' ? [{
                  id: 'run', label: 'Run program', intent: 'activate', params: {},
                  bindings: {selected_piece: 'piece'}, available: true, checks_required: true,
                }] : [], data: renderer === 'piece_table' ? {
                  format: 1, title: 'Signal console', status: [{label: 'Cycle', value: '3'}], areas: [
                    {id: 'operator', label: 'Operator', position: 'bottom', groups: [
                      {id: 'programs', label: 'Programs', pieces: [
                        {id: 'ghost', label: 'Ghost route', action_id: 'run'},
                        {id: 'spent', label: 'Spent route', action_id: 'run'},
                      ]},
                    ]},
                    {id: 'remote', label: 'Remote operator', position: 'top', groups: [
                      {id: 'concealed', label: 'Programs', concealed: true, count: 13},
                    ]},
                  ],
                } : {
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
          }
          return {};
        } },
      };
    }, {failStartup, renderer});
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

test('detached panel startup renders piece_table with per-piece availability', { timeout: 15000 }, async () => {
  await withPanel(false, async (page, status, content) => {
    assert.ok(content.includes('Ghost route'));
        assert.match(content, /13 concealed pieces/);
        await page.waitForFunction(() => {
          const button = document.querySelector('[data-panel-piece-id="ghost"]');
          return button && !button.disabled && window.panelRequests.some(item =>
            item.method === 'player_action_availability');
        });
        assert.equal(await page.locator('[data-panel-piece-id="spent"]').isDisabled(), true);
        const checks = await page.evaluate(() => window.panelRequests.find(item =>
          item.method === 'player_action_availability').args.requests);
        assert.deepEqual(checks.map(item => item.selection), [{piece: 'ghost'}, {piece: 'spent'}]);
        await page.locator('[data-panel-piece-id="ghost"]').click();
        await page.waitForFunction(() => window.panelRequests.some(item => item.method === 'queue_panel_action'));
        const queued = await page.evaluate(() => window.panelRequests.find(item => item.method === 'queue_panel_action').args);
        assert.deepEqual(queued.selection, {piece: 'ghost'});
        await page.setViewportSize({width: 400, height: 700});
        const overflow = await page.locator('.piece-table').evaluate(element => element.scrollWidth > element.clientWidth);
        assert.equal(overflow, false, 'Narrow table should wrap without horizontal overflow');
    assert.equal(status, '');
  }, 'piece_table');
});
