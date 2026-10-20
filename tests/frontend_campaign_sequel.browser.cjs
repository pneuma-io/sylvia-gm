// Browser component proof. The owner and native desktop connection are substituted explicitly.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

test('review, cancellation, source conflicts and uncertain append retries use the selected campaign',
  { timeout: 30000 }, async () => {
    const browser = await chromium.launch({ headless: true,
      channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || 'msedge' });
    try {
      const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
      page.setDefaultTimeout(5000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const root = path.resolve(__dirname, '..');
      const html = fs.readFileSync(path.join(root, 'src-tauri/frontend/app.html'), 'utf8');
      const dialog = html.match(/<dialog id="campaign-sequel-dialog"[\s\S]*?<\/dialog>/)[0];
      const markup = `<html data-theme="slate"><body><button id="campaign-sequel-btn">Add another adventure…</button>
        <output id="feedback"></output>${dialog}</body></html>`;
      await page.route('http://localhost/sequel-component', route => route.fulfill({
        contentType: 'text/html; charset=utf-8', body: markup,
      }));
      await page.goto('http://localhost/sequel-component');
      await page.addStyleTag({ path: path.join(root, 'src-tauri/frontend/app.css') });
      const script = fs.readFileSync(path.join(root, 'src-tauri/frontend/app.js'), 'utf8');
      const functions = script.slice(script.indexOf('  function sequelSelectionIsCurrent('),
        script.indexOf('  async function exportSelectedCampaign()'));
      const wiring = script.slice(script.indexOf("    $('campaign-sequel-btn').addEventListener"),
        script.indexOf("    $('home-settings-btn').addEventListener"));
      await page.addScriptTag({ content: `
        const $ = id => document.getElementById(id);
        const escapeHtml = value => String(value || '').replace(/[&<>"']/g, char => ({
          '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[char]);
        const selectedSessionIsWorking = () => false;
        const selectedActivity = () => null;
        const friendlyCommandError = (label, error) => label + ': ' + error.message;
        const setInlineStatus = text => $('feedback').textContent = text;
        const openCampaign = async (campaign, session) => { window.opened = { campaign, session }; };
        const startBuilderChat = async options => { window.authored = options; };
        window.calls = [];
        const state = { view: 'session', activeCampaignId: 'relay', activeSessionId: 'gm-relay',
          activeSessionRole: 'dm', bridge: {
            campaign_creation_catalog: async () => [
              {kind:'campaign_plan', id:'second', title:'Echoes Beyond the Relay'},
              {kind:'campaign_plan', id:'incompatible', title:'Different rules'}],
            campaign_sequel_context: async () => ({ campaign_name:'Relay crew',
              accepted_plan:{id:'first',bindings:{ruleset:{id:'signal'}},boundaries:[]},
              participants:[{id:'iona', data:{name:'Iona'}}], facts:[], current_scene:{}, ending:{} }),
            preview_campaign_sequel: async request => {
              if(request.source_id === 'incompatible') throw new Error('Different Ruleset snapshot');
              return { basis:{source_id:'second',source_sha256:'a'.repeat(64),expected_revision:9},
                preview_sha256:'b'.repeat(64), title:'Echoes Beyond the Relay',
                summary:'The relay crew follows a second signal after their earlier journey.',
                warning:'Your current characters, world, resources and saved history are retained.',
                participants:[{id:'iona',name:'Iona'}], source_initial_state:{facts:[]},
                ending:{choice:'finish',has_after_story:true},
                acts:[{id:'adventure-2:act-1',number:2,title:'The distant reply'}],
                identity_mapping:{'act-1':'adventure-2:act-1'},
                source_markdown:'# Echoes Beyond the Relay' + String.fromCharCode(10) + 'The recorder remains safe.',
                source_bindings:{ruleset:{id:'signal',version:'1.0'}} };
            },
            append_campaign_sequel: async request => {
              window.calls.push(JSON.parse(JSON.stringify(request)));
              if(window.calls.length === 1) throw new Error('Acknowledgement lost');
              return {plan_id:'saved-second',resulting_revision:10};
            }
          }};
        ${functions}
        ${wiring}
      ` });
      assert.deepEqual(errors, [], 'fixture scripts must load before interaction');
      await page.getByRole('button', { name: 'Add another adventure…', exact: true }).click();
      await page.locator('#campaign-sequel-source option[value="second"]').waitFor({ state: 'attached' });
      await page.locator('#campaign-sequel-source').selectOption('incompatible');
      await page.getByRole('button', { name: 'Review adventure', exact: true }).click();
      await page.getByRole('status').filter({ hasText: 'Different Ruleset snapshot' }).waitFor();
      assert.equal(await page.locator('#campaign-sequel-append').isDisabled(), true);
      await page.getByRole('button', { name: 'Cancel', exact: true }).click();
      assert.equal(await page.evaluate(() => window.calls.length), 0);

      await page.getByRole('button', { name: 'Add another adventure…', exact: true }).click();
      await page.locator('#campaign-sequel-source').selectOption('second');
      await page.getByRole('button', { name: 'Review adventure', exact: true }).click();
      await page.locator('#campaign-sequel-review:not([hidden])').waitFor();
      assert.equal(await page.locator('#campaign-sequel-append').isDisabled(), true);
      assert.match(await page.locator('#campaign-sequel-review').innerText(), /already chose its after-story/);
      await page.locator('#campaign-sequel-connection').fill(
        'After Iona’s established years on the relay moon, a new signal calls the crew together.');
      await page.locator('#campaign-sequel-reviewed').check();
      assert.equal(await page.locator('#campaign-sequel-append').isEnabled(), true);
      const evidence = process.env.SYLVIA_SEQUEL_COMPONENT_SCREENSHOT;
      if (evidence) {
        await page.locator('#campaign-sequel-dialog').evaluate(element => { element.scrollTop = 0; });
        await page.screenshot({ path: path.resolve(evidence), fullPage: true, animations: 'disabled' });
        await page.locator('#campaign-sequel-append').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.resolve(evidence.replace(/\.png$/, '-confirmation.png')),
          fullPage: true, animations: 'disabled' });
      }
      await page.getByRole('button', { name: 'Append adventure', exact: true }).click();
      await page.getByRole('button', { name: 'Retry append', exact: true }).waitFor();
      assert.equal(await page.locator('#campaign-sequel-source').isDisabled(), true);
      await page.getByRole('button', { name: 'Retry append', exact: true }).click();
      await page.locator('#campaign-sequel-dialog').waitFor({ state: 'hidden' });
      const observed = await page.evaluate(() => ({ calls: window.calls, opened: window.opened }));
      assert.deepEqual(observed.calls[0], observed.calls[1]);
      assert.deepEqual(observed.opened, { campaign: 'relay', session: 'gm-relay' });
      assert.equal(observed.calls[0].review.timeline, 'after');
      assert.equal(observed.calls[0].review.reviewed, true);
      await page.evaluate(() => {
        const original = state.bridge.campaign_sequel_context;
        state.bridge.campaign_sequel_context = async () => ({ ...await original(),
          recent_history: Array.from({ length: 64 }, () => ({ kind: 'talk', excerpt: 'x'.repeat(4000) })) });
      });
      await page.locator('#campaign-sequel-btn').click();
      await page.locator('#campaign-sequel-source option[value="second"]').waitFor({ state: 'attached' });
      await page.getByRole('button', { name: 'Author with Builder', exact: true }).click();
      const authored = await page.evaluate(() => window.authored);
      assert.ok(authored.initialPrompt.length < 64000, 'composed draft fits ordinary Builder input');
      assert.match(authored.initialPrompt, /Context excerpt truncated/);
      assert.match(authored.initialPrompt, /Ask me for missing material/);
      assert.equal(await page.evaluate(() => window.calls.length), 2, 'authoring never adopts a Plan');
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  });
