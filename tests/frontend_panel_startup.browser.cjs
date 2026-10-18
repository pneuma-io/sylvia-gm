// Real child HTML/JS startup and projection rendering with a synthetic native event transport.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

for (const failStartup of [false, true]) test(`detached panel startup ${failStartup ? 'shows a recovery error' : 'renders the first owner projection'}`, { timeout: 15000 }, async () => {
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
      window.__TAURI__ = {
        event: { listen: async (name, callback) => { listeners.set(name, callback); return () => listeners.delete(name); } },
        core: { invoke: async (command, args) => {
          window.startupCalls.push(command);
          if (command === 'panel_window_message' && args.payload.type === 'ready') {
            if (failStartup) throw new Error('Synthetic transport unavailable');
            listeners.get('panel-workspace')({ payload: { context, window_id: windowId, payload: {
              presentation_sequence: 1, busy: false, theme: 'slate',
              projection: { campaign_id: 'synthetic', revision: 1, shot_effects: [], surfaces: [
                { id: 'party', title: 'Party', renderer: 'party', actions: [], data: {
                  participants: [{ id: 'asha', kind: 'character', data: { name: 'Asha' } }],
                  entities: { asha: { hp: 8, ap: 4, res: 7, stance: 'STANDING' } },
                } },
              ] },
              layout: { version: 1, sidebar: null, hidden: [], known: ['party'], views: {}, windows: [
                { id: windowId, title: 'Party', tree: { kind: 'tabs', id: 'party-tabs', panels: ['party'], active: 'party' } },
              ] },
            } } });
          }
          return {};
        } },
      };
    }, failStartup);
    await page.goto('https://sylvia-panel.test/panel.html');
    await page.waitForFunction(() => document.getElementById('session-status-text').textContent !== 'Loading campaign panels…');
    const status = await page.locator('#session-status-text').textContent();
    const content = await page.locator('#ruleset-panels').textContent();
    if (failStartup) {
      assert.match(status, /Could not load campaign panels: .*Synthetic transport unavailable/);
      assert.match(status, /Close this window to return the panels to the sidebar/);
    } else {
      assert.ok(content.includes('Asha'), `Panel did not render: status=${status}; errors=${JSON.stringify(errors)}`);
      assert.equal(status, '');
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
