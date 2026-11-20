// Stop, Retry, switching away and reopening, played through the rendered controls.
//
// The shipped frontend starts normally and unmodified; a scripted owner stands in for the desktop
// host (tests/stop_resume/). Browser journeys only: not native-window, installed or real-model
// acceptance. tests/stop_resume/contract.cjs checks the scripted owner against the real one.
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { ScriptedOwner } = require('./stop_resume/scripted-owner.cjs');
const path = require('node:path');
const { launchBrowser, hostContext, openSylvia, controls, contrast, focusLook, focusShown, placement } =
  require('./stop_resume/journey-page.cjs');

const CAMPAIGNS = ['Greyhook', 'Saltmarsh'];
const THEMES = ['slate', 'sylvia', 'sylvia-dark'];
// Optional folder for review screenshots; the checks never depend on it.
const EVIDENCE = process.env.SYLVIA_JOURNEY_EVIDENCE || null;
async function shot(page, name) {
  if (!EVIDENCE) return;
  // Let running entrance transitions finish so the picture shows the settled state. Review
  // pictures only: the cap keeps a stuck animation from holding up the checks.
  await page.evaluate(() => Promise.race([Promise.all(document.getAnimations()
    .filter(animation => animation.playState === 'running' &&
      animation.effect?.getComputedTiming().endTime !== Infinity)
    .map(animation => animation.finished.catch(() => {}))),
  new Promise(resolve => setTimeout(resolve, 2000))]));
  await page.screenshot({ path: path.join(EVIDENCE, `${name}.png`) });
}

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
      await shot(page, 'stop-stopped');
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
      assert.doesNotMatch(await ui.status.innerText(), /provider_error/, 'no owner code in player text (#223)');
      assert.equal(await ui.composer.isDisabled(), true, 'after a failure only Retry continues the turn');
      assert.equal(await ui.send.isDisabled(), true);
      await shot(page, 'retry-offered');
      await ui.retry.click();
      await ui.story.getByText('The Game Master answers: I open the door.').waitFor();
      await ui.retry.waitFor({ state: 'detached' });
      await enabled(page, ui.composer);
      assert.equal(await ui.story.getByText('[fail] I open the door.').count(), 1, 'the message is shown once');
      assert.equal(await ui.story.getByText('The Game Master answers: I open the door.').count(), 1);
      assert.equal(count(owner, 'submit_session_input'), 1, 'Retry did not resend the message');
      assert.equal(count(owner, 'manual_retry_session'), 1);
      await shot(page, 'retry-answered');
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
      await shot(page, 'switch-late-reply-in-its-campaign');
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
      await shot(reopened.page, 'reopen-retry-kept');
      await again.retry.click();
      await again.story.getByText('The Game Master answers: I climb the stair.').waitFor();
      await again.retry.waitFor({ state: 'detached' });
      assert.equal(count(owner, 'submit_session_input'), 1, 'nothing was resent on reopen');
      assert.deepEqual(reopened.errors, []);
    });
  });

// Press `key` until `locator` has focus; returns the number of presses, or fails after `limit`.
async function pressUntilFocused(page, key, locator, limit = 8) {
  for (let presses = 1; presses <= limit; presses++) {
    await page.keyboard.press(key);
    if (await locator.evaluate(element => element === document.activeElement).catch(() => false)) return presses;
  }
  assert.fail(`${key} did not reach the control within ${limit} presses`);
}

const runningAnimations = page => page.evaluate(() => document.getAnimations()
  .filter(animation => animation.playState === 'running' && animation.constructor.name === 'CSSAnimation')
  .map(animation => animation.animationName));

test('keyboard: Stop and Manual Retry are reached by Tab, operated by key and show their focus',
  { timeout: 60000 }, async () => {
    await journey(async ({ page, ui }) => {
      await ui.campaign('Greyhook').focus();
      await page.keyboard.press('Enter');
      await ui.title.filter({ hasText: 'Greyhook' }).waitFor();
      await enabled(page, ui.composer);
      await ui.composer.focus();
      await page.keyboard.type('[hold] I listen at the door.');
      await page.keyboard.press('Enter');
      await ui.stop.waitFor();
      // Focus currently falls to the page when the message box is disabled (issue #221); Tab
      // still continues from the message box to Stop.
      await pressUntilFocused(page, 'Tab', ui.stop, 4);
      assert.equal(await focusShown(ui.stop), true, 'Stop shows its keyboard focus');
      await page.keyboard.press('Space');
      await ui.status.filter({ hasText: 'Stopped' }).waitFor();

      await ui.composer.focus();
      await page.keyboard.type('[fail] I open the door.');
      await page.keyboard.press('Enter');
      await ui.retry.waitFor();
      await pressUntilFocused(page, 'Shift+Tab', ui.retry, 8);
      assert.equal(await focusShown(ui.retry), true, 'Manual Retry shows its keyboard focus');
      await page.keyboard.press('Enter');
      await ui.story.getByText('The Game Master answers: I open the door.').waitFor();
      await enabled(page, ui.composer);
      const unfocused = await focusLook(ui.composer);
      await pressUntilFocused(page, 'Tab', ui.composer, 12);
      assert.equal(await focusShown(ui.composer, unfocused), true, 'the message box shows its focus');
    });
  });

test('narrow layout: Stop and Retry fit a phone-width window through the Story pane',
  { timeout: 60000 }, async () => {
    await journey(async ({ page, ui }) => {
      await ui.campaign('Greyhook').click();
      await ui.title.filter({ hasText: 'Greyhook' }).waitFor();
      await page.locator('#view-switch').getByRole('button', { name: 'Story' }).click();
      await enabled(page, ui.composer);
      await send(ui, '[hold] I listen at the door.');
      await ui.stop.waitFor();
      for (const [name, control] of [['Stop', ui.stop], ['message box', ui.composer]]) {
        const { box, inside, sideways } = await placement(page, control);
        assert.ok(inside, `${name} is inside the window: ${JSON.stringify(box)}`);
        assert.equal(sideways, false, 'the page does not scroll sideways');
      }
      const stopBox = await ui.stop.boundingBox();
      assert.ok(stopBox.width >= 24 && stopBox.height >= 24, 'Stop is at least 24 by 24 pixels');
      await shot(page, 'narrow-working');
      await ui.stop.click();
      await ui.status.filter({ hasText: 'Stopped' }).waitFor();
      await send(ui, '[fail] I open the door.');
      await ui.retry.waitFor();
      await ui.retry.scrollIntoViewIfNeeded();
      const { box, inside, sideways } = await placement(page, ui.retry);
      assert.ok(inside, `Manual Retry is inside the window: ${JSON.stringify(box)}`);
      assert.equal(sideways, false);
      assert.ok(box.height >= 24, 'Manual Retry is at least 24 pixels tall');
      await shot(page, 'narrow-retry');
      await ui.retry.click();
      await ui.story.getByText('The Game Master answers: I open the door.').waitFor();
    }, { viewport: { width: 390, height: 844 } });
  });

test('reduced motion: the working indicator animates only when motion is allowed',
  { timeout: 60000 }, async () => {
    const working = {};
    for (const reducedMotion of ['no-preference', 'reduce']) {
      await journey(async ({ owner, page, ui }) => {
        await openCampaign(page, ui, 'Greyhook');
        await send(ui, '[hold] I listen at the door.');
        await owner.working('Greyhook');
        await ui.stop.waitFor();
        await delivered(page);
        working[reducedMotion] = await runningAnimations(page);
        await ui.stop.click();
        await ui.status.filter({ hasText: 'Stopped' }).waitFor();
      }, { reducedMotion });
    }
    assert.ok(working['no-preference'].length > 0, 'the check sees the working animation when motion is allowed');
    assert.deepEqual(working.reduce, [], 'nothing animates while the GM works under reduced motion');
  });

// Text measured below 4.5:1 and tracked by issue #222. Remove an entry once the theme is fixed;
// every other element is held to the minimum in every theme.
const CONTRAST_ISSUE_222 = {
  slate: [],
  sylvia: ['status line', 'Retry notice'],
  'sylvia-dark': ['status line', 'Retry notice', 'Manual Retry'],
};

test('themes: interruption and recovery text and controls keep readable contrast in each theme',
  { timeout: 120000 }, async () => {
    for (const theme of THEMES) {
      await journey(async ({ page, ui }) => {
        assert.equal(await page.locator('body').getAttribute('data-theme'), theme);
        const readable = async (name, element, minimum = 4.5) => {
          if (CONTRAST_ISSUE_222[theme].includes(name)) return;
          const ratio = await contrast(element);
          assert.ok(ratio >= minimum, `${theme}: ${name} contrast ${ratio.toFixed(2)} is at least ${minimum}`);
        };
        await openCampaign(page, ui, 'Greyhook');
        await send(ui, '[hold] I listen at the door.');
        await ui.stop.waitFor();
        await readable('Stop', ui.stop, 3);
        await ui.stop.click();
        await ui.status.filter({ hasText: 'Stopped' }).waitFor();
        await readable('status line', ui.status);
        await send(ui, '[fail] I open the door.');
        await ui.retry.waitFor();
        await readable('Retry notice', ui.story.locator('.retry-card > div').first());
        await readable('Manual Retry', ui.retry);
        await readable('your message', ui.story.getByText('[fail] I open the door.'));
        await shot(page, `theme-${theme}-retry`);
        await ui.retry.click();
        const answer = ui.story.getByText('The Game Master answers: I open the door.');
        await answer.waitFor();
        await readable('the reply', answer);
      }, { theme });
    }
  });
