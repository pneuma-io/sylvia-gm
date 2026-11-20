// Open the shipped frontend, unmodified, with a scripted owner standing in for the desktop host.
//
// Every frontend file is served byte for byte from src-tauri/frontend. The only thing supplied is
// the host transport that app.js's hostTransport() reads, window.__TAURI__.core.invoke, which
// forwards each command to an owner object in this Node process (scripted-owner.cjs, or anything
// with the same invoke contract). The owner lives outside the page, so a reload or a second
// window reopens the same saved state, as reopening the desktop app does.
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const FRONTEND = path.resolve(__dirname, '../../src-tauri/frontend');
const ORIGIN = 'https://sylvia-journey.test';
const TYPES = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png',
  '.svg': 'image/svg+xml', '.webp': 'image/webp', '.json': 'application/json', '.woff2': 'font/woff2' };

function launchBrowser() {
  return chromium.launch({ headless: true, args: ['--disable-gpu'],
    channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
}

// A browser context whose pages reach `owner` as their desktop host. Options are Playwright's
// context options (viewport, reducedMotion, colorScheme, recordVideo) plus `theme`, the saved
// appearance preference a returning player would have.
async function hostContext(browser, owner, { theme = null, ...options } = {}) {
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, ...options });
  await context.exposeFunction('__sylviaJourneyOwner', async (command, args) => {
    try { return { ok: true, result: await owner.invoke(command, args) }; }
    catch (error) { return { ok: false, error: String(error?.message || error) }; }
  });
  await context.addInitScript(({ theme }) => {
    // The desktop host's transport: resolve with the result, reject with "code: message".
    window.__TAURI__ = {
      core: { invoke: async (command, args) => {
        const reply = await window.__sylviaJourneyOwner(command, args ?? {});
        if (!reply.ok) throw reply.error;
        return reply.result;
      } },
      // Host events come only from detached panel windows, which these journeys do not open.
      event: { listen: async () => () => {} },
    };
    // A returning player: the welcome tour is done and the appearance choice is saved.
    try {
      if (!localStorage.getItem('sylvia-gm-tutorials-v1')) localStorage.setItem('sylvia-gm-tutorials-v1',
        JSON.stringify({ welcomeSeen: true, journey: null, resumeId: null,
          lessons: { home: { status: 'completed', index: 0 } } }));
      if (theme) localStorage.setItem('sylvia-gm-theme', theme);
    } catch (_) { /* Storage is a preference only. */ }
  }, { theme });
  await context.route(`${ORIGIN}/**`, async route => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.slice(1)) || 'app.html';
    const file = path.resolve(FRONTEND, name);
    if (!file.startsWith(FRONTEND + path.sep) || !fs.existsSync(file)) return route.abort();
    await route.fulfill({ body: fs.readFileSync(file),
      contentType: TYPES[path.extname(file)] || 'application/octet-stream' });
  });
  return context;
}

// Start Sylvia normally in a new window of `context` and wait until Home is ready.
async function openSylvia(context) {
  const page = await context.newPage();
  page.setDefaultTimeout(Number(process.env.SYLVIA_JOURNEY_TIMEOUT_MS) || 10000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${ORIGIN}/app.html`);
  await page.locator('#home-view').waitFor({ state: 'visible' });
  return { page, errors };
}

// Player-facing controls, by the roles and names a player (or screen reader) meets.
function controls(page) {
  return {
    home: page.getByRole('button', { name: 'Open Home' }),
    campaign: name => page.locator('#home-view').getByRole('button', { name: new RegExp(`^${name}\\b`) }),
    composer: page.getByRole('textbox', { name: 'In-character message or action' }),
    send: page.getByRole('button', { name: 'Send message' }),
    stop: page.getByRole('button', { name: 'Stop generation' }),
    retry: page.getByRole('button', { name: 'Manual Retry' }),
    story: page.locator('#chronology'),
    status: page.locator('#session-status-text'),
    title: page.locator('#session-title'),
  };
}

module.exports = { launchBrowser, hostContext, openSylvia, controls, FRONTEND };
