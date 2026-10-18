// Real browser parsing/execution probes against source assets; installed WebView proof is separate.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');
const csp = JSON.parse(fs.readFileSync(path.resolve(frontend, '../tauri.conf.json'), 'utf8')).app.security.csp;
const hostile = `"'><img src="https://unexpected.test/probe" onerror="window.executed=true"><svg onload="window.executed=true"><script>window.executed=true</script>`;

async function shell(browser, pageName, withPolicy) {
  const page = await browser.newPage();
  const requests = [], errors = [], commands = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (!request.url().startsWith('https://sylvia-security.test/')) requests.push(request.url());
  });
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://sylvia-security.test') return route.abort();
    const name = url.pathname.slice(1);
    if (!['app.html', 'panel.html', 'audit.html', 'app.js', 'audit.js', 'app.css', 'audit.css',
      'panel-host.js', 'panel-workspace.js', 'shot-playback.js', 'campaign-plan-browser.js',
      'assets/sylvia-portrait.png'].includes(name)) return route.abort();
    let body = fs.readFileSync(path.join(frontend, name));
    if (name === 'app.js') body = body.toString().replace(
      "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
      .replace('  // ---- Public API ----', `window.securityTest = { state, renderNarrationMarkdown,
        rulesetCardMarkup, panelMarkup, renderProviderValidation, renderAdminAuditTraces,
        renderConfiguredProviders, setInlineStatus }; // ---- Public API ----`);
    await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
      name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html',
      headers: withPolicy ? { 'Content-Security-Policy': csp } : {} });
  });
  await page.exposeFunction('recordCommand', command => commands.push(command));
  await page.addInitScript(payload => {
    window.__TAURI__ = { core: { async invoke(command) {
      await window.recordCommand(command);
      if (command === 'admin_full_audit_status') return { enabled: true, trace_count: 1 };
      if (command === 'admin_full_audit_record') return { trace: { kind: payload, input: payload } };
      return {};
    } } };
  }, hostile);
  await page.goto(`https://sylvia-security.test/${pageName}`);
  return { page, requests, errors, commands };
}

for (const withPolicy of [false, true]) {
  test(`untrusted text stays inert across main and panel rendering (CSP=${withPolicy})`, async () => {
    const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
    try {
      for (const name of ['app.html', 'panel.html']) {
        const { page, requests, errors, commands } = await shell(browser, name, withPolicy);
        await page.evaluate(payload => {
          const api = window.securityTest;
          const main = document.createElement('main'); main.id = 'synthetic-probe'; document.body.append(main);
          const schemes = ['javascript:window.executed=true', 'JaVaScRiPt:alert', 'data:text/html,hi',
            'file:///synthetic', 'java&#x73;cript:alert', 'javascript%3Aalert'];
          main.innerHTML = api.renderNarrationMarkdown(`# Safe title\n**Bold** and *emphasis*\n\n${payload}\n` +
            schemes.map(target => `[blocked](${target})`).join('\n') +
            '\n[valid](https://example.test/path)\n\n| Name | Value |\n| --- | --- |\n| Row | 4 |\n\n```js\n' + payload + '\n```');
          main.insertAdjacentHTML('beforeend', api.rulesetCardMarkup({ version: 1, title: payload,
            subtitle: payload, summary: [{ label: payload, value: payload, emphasis: false }],
            details: [{ label: 'Details', value: payload, emphasis: true }],
            actions: [{ label: payload, tool: payload, parameters: {} }] }, ''));
          main.insertAdjacentHTML('beforeend', api.panelMarkup({ id: payload, title: payload,
            icon: payload, renderer: 'key_value', minimum_size: 96, default_size: 180,
            data: { text: payload }, actions: [] }));
          if (document.getElementById('provider-validation-report')) {
            api.renderProviderValidation({ reachability: payload, authentication: payload });
            document.getElementById('provider-settings-status').textContent = payload;
            api.state.providerSettings = { configured: [{ provider_id: payload, name: payload,
              credential_error: payload, models: [{ id: payload, name: payload }] }] };
            api.renderConfiguredProviders();
          }
        }, hostile);
        const probe = page.locator('#synthetic-probe');
        assert.equal(await probe.locator('img,svg,script,iframe,object').count(), 0);
        assert.equal(await probe.locator('a').count(), 1);
        assert.equal(await probe.locator('a').getAttribute('href'), 'https://example.test/path');
        assert.equal(await probe.locator('strong').first().innerText(), 'Bold');
        assert.equal(await probe.locator('table tbody tr').count(), 1);
        assert.match(await probe.locator('pre code').innerText(), /onerror/);
        assert.equal(await probe.locator('[data-result-card-button-index]').isDisabled(), true);
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        assert.equal(await page.evaluate(() => window.executed), undefined);
        assert.deepEqual(requests, []); assert.deepEqual(commands, []); assert.deepEqual(errors, []);
        await page.close();
      }
    } finally { await browser.close(); }
  });
}

test('Audit is inert and source CSP blocks unsolicited inline script execution', async () => {
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
  try {
    const { page, requests, errors, commands } = await shell(browser, 'audit.html', true);
    await page.locator('.record summary').click();
    assert.match(await page.locator('.record pre').innerText(), /onerror/);
    assert.equal(await page.locator('.record img,.record svg,.record script').count(), 0);
    await page.evaluate(() => {
      window.policyViolations = [];
      document.addEventListener('securitypolicyviolation', e => window.policyViolations.push(e.violatedDirective));
      const script = document.createElement('script'); script.textContent = 'window.executed=true';
      document.body.append(script);
    });
    await page.waitForFunction(() => window.policyViolations.length > 0);
    assert.equal(await page.evaluate(() => window.executed), undefined);
    assert.deepEqual([...new Set(commands)].sort(), ['admin_full_audit_record', 'admin_full_audit_status']);
    assert.deepEqual(requests, []); assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
