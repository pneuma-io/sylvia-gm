const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Run the actual app callers over the browser bridge, including their catch wrappers.
function harness(mode) {
  const statuses = [], calls = [];
  const context = {window: {}, console, TypeError,
    localStorage: {getItem: () => null},
    crypto: {randomUUID: () => 'request'},
    FormData: class {append() {}},
    document: {addEventListener() {}, body: {appendChild() {}},
      getElementById: () => ({hidden: false}),
      createElement: () => {const events = {}; return {files: [{name: 'synthetic.txt'}],
        addEventListener(name, fn) {events[name] = fn;},
        click() {events.change();}, remove() {}};}},
    async fetch(url) {
      calls.push(url);
      if (mode === 'lost') throw new TypeError('Failed to fetch');
      return {ok: false, async json() {
        if (mode === 'unreadable') throw new SyntaxError('Truncated response');
        return {ok: false, error: {message: 'Selected input rejected.'}};
      }};
    }};
  vm.runInNewContext(fs.readFileSync(require.resolve('../src-tauri/frontend/webui-bridge.js'), 'utf8'), context);
  let app = fs.readFileSync(require.resolve('../src-tauri/frontend/app.js'), 'utf8');
  app = app.replace('  // ---- Public API ----', `
    window.uploadQA = {state, attachBuilderSources, installSelectedPackage, recoverCampaignFile};
    selectedSessionIsWorking = () => false;
    selectedActivity = () => ({}); selectedActivityMatches = () => true;
    setInlineStatus = selectedStatus = (...args) => window.statuses.push(args.at(-1));
    setPackageInstallStatus = setCampaignDataStatus = value => window.statuses.push(value);
    // ---- Public API ----`);
  context.window.statuses = statuses;
  vm.runInNewContext(app, context);
  const api = context.window.uploadQA;
  Object.assign(api.state, {activeSessionRole: 'builder', activeSessionId: 'synthetic', bridge: {
    ingest_builder_sources: () => context.window.__sylviaBrowserInvoke('ingest_builder_sources'),
    install_package_file: () => context.window.__sylviaBrowserInvoke('install_package_file'),
    recover_campaign_file: () => context.window.__sylviaBrowserInvoke('recover_campaign_file'),
  }});
  return {api, statuses, calls};
}

for (const caller of ['attachBuilderSources', 'installSelectedPackage', 'recoverCampaignFile']) {
  for (const mode of ['lost', 'unreadable']) {
    test(`${caller}: ${mode} response stays unconfirmed at the actual caller`, async () => {
      const h = harness(mode);
      await h.api[caller]();
      assert.match(h.statuses.at(-1), /status unconfirmed/i);
      assert.doesNotMatch(h.statuses.at(-1), /failed|cancelled|rejected|recovered|installed|attached/i);
      assert.equal(h.calls.length, 1);
    });
  }
  test(`${caller}: definitive rejection remains a failure without retry`, async () => {
    const h = harness('rejected');
    await h.api[caller]();
    assert.match(h.statuses.at(-1), /failed: Selected input rejected/);
    assert.equal(h.calls.length, 1);
  });
}
