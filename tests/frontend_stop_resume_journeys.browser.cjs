// Stop, Retry, switching away and reopening, played through the rendered controls.
//
// The shipped frontend starts normally and unmodified; a scripted owner stands in for the desktop
// host (tests/stop_resume/). Browser journeys only: not native-window, installed or real-model
// acceptance. tests/stop_resume/contract.cjs checks the scripted owner against the real one.
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { ScriptedOwner } = require('./stop_resume/scripted-owner.cjs');
const { launchBrowser, hostContext, openSylvia, controls } = require('./stop_resume/journey-page.cjs');

const CAMPAIGNS = ['Greyhook', 'Saltmarsh'];

async function journey(run, options = {}) {
  const owner = new ScriptedOwner(CAMPAIGNS);
  const browser = await launchBrowser();
  try {
    const context = await hostContext(browser, owner, options);
    const { page, errors } = await openSylvia(context);
    await run({ owner, context, page, ui: controls(page) });
    assert.deepEqual(errors, [], 'no page errors');
    assert.deepEqual(owner.unsupported, [], 'the frontend used only commands the scripted owner implements');
  } finally { await browser.close(); }
}

// Wait until a control is enabled (a state wait, never a sleep).
const enabled = (page, locator) => locator.and(page.locator(':enabled')).waitFor();

async function openCampaign(page, ui, name) {
  await ui.campaign(name).click();
  await ui.title.filter({ hasText: name }).waitFor();
  await enabled(page, ui.composer);
}

async function send(ui, text) {
  await ui.composer.fill(text);
  await ui.composer.press('Enter');
}

test('Stop: a held reply stops, keeps the message without a reply, and the next message redirects it',
  { timeout: 60000 }, async () => {
    await journey(async ({ owner, page, ui }) => {
      await openCampaign(page, ui, 'Greyhook');
      await send(ui, '[hold] I listen at the door.');
      await owner.working('Greyhook');
      await ui.stop.waitFor();
      assert.equal(await ui.send.isVisible(), false, 'Send gives way to Stop while the GM works');
      await ui.stop.click();
      await ui.status.filter({ hasText: 'Stopped' }).waitFor();
      await ui.send.waitFor();
      assert.equal(await ui.stop.isVisible(), false);
      await ui.story.getByText('[hold] I listen at the door.').waitFor();
      assert.equal(await ui.story.getByText('The Game Master answers').count(), 0, 'the stopped message has no reply');
      assert.equal(await ui.composer.isEnabled(), true, 'a stopped reply can be redirected by a new message');
      await send(ui, 'I knock instead.');
      await ui.story.getByText('The Game Master answers: I knock instead.').waitFor();
      assert.equal(await ui.retry.count(), 0, 'the redirected message replaced the Retry offer');
    });
  });
