// Real child HTML/JS startup and projection rendering with a synthetic native event transport.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

for (const scenario of [
  {failStartup: false, renderer: 'party'},
  {failStartup: true, renderer: 'party'},
  {failStartup: false, renderer: 'piece_table'},
]) test(`detached panel startup ${scenario.failStartup ? 'shows a recovery error' : `renders ${scenario.renderer}`}`, { timeout: 15000 }, async () => {
  const {failStartup, renderer} = scenario;
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
      window.panelRequests = [];
      window.__TAURI__ = {
        event: { listen: async (name, callback) => { listeners.set(name, callback); return () => listeners.delete(name); } },
        core: { invoke: async (command, args) => {
          window.startupCalls.push(command);
          if (command === 'panel_window_message' && args.payload.type === 'rpc') {
            const request = args.payload;
            window.panelRequests.push(request);
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
            const workspace = { context, window_id: windowId, payload: {
              presentation_sequence: 1, busy: false, theme: 'slate',
              projection: { campaign_id: 'synthetic', revision: 1, shot_effects: [], surfaces: [
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
                  participants: [{ id: 'asha', kind: 'character', data: { name: 'Asha' } }],
                  entities: { asha: { hp: 8, ap: 4, res: 7, stance: 'STANDING' } },
                } },
              ] },
              layout: { version: 1, sidebar: null, hidden: [], known: ['party'], views: {}, windows: [
                { id: windowId, title: 'Party', tree: { kind: 'tabs', id: 'party-tabs', panels: ['party'], active: 'party' } },
              ] },
            } };
            window.fixtureProjection = workspace.payload.projection;
            listeners.get('panel-workspace')({payload: workspace});
          }
          return {};
        } },
      };
    }, scenario);
    await page.goto('https://sylvia-panel.test/panel.html');
    await page.waitForFunction(() => document.getElementById('session-status-text').textContent !== 'Loading campaign panels…');
    const status = await page.locator('#session-status-text').textContent();
    const content = await page.locator('#ruleset-panels').textContent();
    if (failStartup) {
      assert.match(status, /Could not load campaign panels: .*Synthetic transport unavailable/);
      assert.match(status, /Close this window to return the panels to the sidebar/);
    } else {
      assert.ok(content.includes(renderer === 'piece_table' ? 'Ghost route' : 'Asha'), `Panel did not render: status=${status}; errors=${JSON.stringify(errors)}`);
      if (renderer === 'piece_table') {
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
      }
      assert.equal(status, '');
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
