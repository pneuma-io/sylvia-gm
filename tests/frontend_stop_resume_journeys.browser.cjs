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

// Wait until the page has received every owner reply already sent and painted a frame. Host
// replies and this evaluation share one ordered channel, so this is a state wait, not a sleep.
const delivered = page => page.evaluate(() => new Promise(resolve =>
  requestAnimationFrame(() => requestAnimationFrame(resolve))));

const count = (owner, command) => owner.calls.filter(call => call.command === command).length;

test('manual Retry: a failed reply offers Retry, keeps new input closed, and answers once without resending',
  { timeout: 60000 }, async () => {
    await journey(async ({ owner, page, ui }) => {
      await openCampaign(page, ui, 'Greyhook');
      await send(ui, '[fail] I open the door.');
      await ui.retry.waitFor();
      await ui.story.getByText("No game changes were saved for this turn. Sylvia couldn't finish the response.").waitFor();
      await ui.status.filter({ hasText: 'could not connect to the provider' }).waitFor();
      assert.equal(await ui.composer.isDisabled(), true, 'after a failure only Retry continues the turn');
      assert.equal(await ui.send.isDisabled(), true);
      await ui.retry.click();
      await ui.story.getByText('The Game Master answers: I open the door.').waitFor();
      await ui.retry.waitFor({ state: 'detached' });
      await enabled(page, ui.composer);
      assert.equal(await ui.story.getByText('[fail] I open the door.').count(), 1, 'the message is shown once');
      assert.equal(await ui.story.getByText('The Game Master answers: I open the door.').count(), 1);
      assert.equal(count(owner, 'submit_session_input'), 1, 'Retry did not resend the message');
      assert.equal(count(owner, 'manual_retry_session'), 1);
    });
  });

test('switching away: a late reply and a late history read from the other campaign stay with it',
  { timeout: 60000 }, async () => {
    await journey(async ({ owner, page, ui }) => {
      await openCampaign(page, ui, 'Greyhook');
      await send(ui, '[late] I wait for the tide.');
      await owner.working('Greyhook');
      await ui.stop.waitFor();
      await ui.home.click();
      await openCampaign(page, ui, 'Saltmarsh');
      assert.equal(await ui.stop.isVisible(), false, "Greyhook's running reply does not show Stop here");
      owner.release('Greyhook');
      await owner.settled('Greyhook');
      await send(ui, 'I light a lantern.');
      await ui.story.getByText('The Game Master answers: I light a lantern.').waitFor();
      assert.equal(await ui.story.getByText('I wait for the tide').count(), 0, "Greyhook's reply never lands here");
      assert.equal(await ui.title.innerText(), 'Saltmarsh');

      // Reopen Greyhook but hold its history read, switch back, then let the read arrive late.
      await ui.home.click();
      const lateRead = owner.gate('session_history_page', { sessionId: owner.campaign('Greyhook').session_id });
      await ui.campaign('Greyhook').click();
      await lateRead.arrived;
      await ui.home.click();
      await openCampaign(page, ui, 'Saltmarsh');
      lateRead.release();
      await lateRead.answered;
      await delivered(page);
      await ui.story.getByText('The Game Master answers: I light a lantern.').waitFor();
      assert.equal(await ui.title.innerText(), 'Saltmarsh');
      assert.equal(await ui.story.getByText('I wait for the tide').count(), 0);

      await ui.home.click();
      await openCampaign(page, ui, 'Greyhook');
      await ui.story.getByText('The Game Master answers: I wait for the tide.').waitFor();
      assert.equal(await ui.story.getByText('The Game Master answers: I wait for the tide.').count(), 1);
      assert.equal(await ui.stop.isVisible(), false);
    });
  });

test('reopen: an interrupted turn keeps its message and Retry after the window is reopened',
  { timeout: 60000 }, async () => {
    await journey(async ({ owner, context, page, ui }) => {
      await openCampaign(page, ui, 'Greyhook');
      await send(ui, '[fail] I climb the stair.');
      await ui.retry.waitFor();
      await page.close();

      const reopened = await openSylvia(context);
      const again = controls(reopened.page);
      await again.campaign('Greyhook').click();
      await again.title.filter({ hasText: 'Greyhook' }).waitFor();
      await again.retry.waitFor();
      await again.story.getByText('[fail] I climb the stair.').waitFor();
      assert.equal(await again.composer.isDisabled(), true, 'reopening does not turn the failed turn into new input');
      await again.retry.click();
      await again.story.getByText('The Game Master answers: I climb the stair.').waitFor();
      await again.retry.waitFor({ state: 'detached' });
      assert.equal(count(owner, 'submit_session_input'), 1, 'nothing was resent on reopen');
      assert.deepEqual(reopened.errors, []);
    });
  });
