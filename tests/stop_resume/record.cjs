// Record a short, paced video of interruption and recovery for human review.
//
//   node tests/stop_resume/record.cjs <output folder>
//
// It plays the same steps as the browser journeys against the scripted owner, with pauses so a
// person can follow them and a caption naming each step. The caption is drawn over the page for
// the recording only; nothing else on the page is changed. Not a test: the checks are in
// tests/frontend_stop_resume_journeys.browser.cjs.
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { ScriptedOwner } = require('./scripted-owner.cjs');
const { launchBrowser, hostContext, openSylvia, controls } = require('./journey-page.cjs');

const out = path.resolve(process.argv[2] || 'stop-resume-recording');
const PAUSE = 1600;

async function caption(page, text) {
  await page.evaluate(value => {
    let bar = document.getElementById('review-caption');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'review-caption';
      bar.style.cssText = 'position:fixed;left:50%;top:10px;transform:translateX(-50%);z-index:99999;' +
        'pointer-events:none;padding:6px 14px;border-radius:6px;background:#111c;color:#fff;' +
        'font:600 15px system-ui,sans-serif;box-shadow:0 2px 8px #0008';
      document.body.appendChild(bar);
    }
    bar.textContent = value;
  }, text);
  await page.waitForTimeout(PAUSE);
}

async function type(ui, text) {
  await ui.composer.click();
  await ui.composer.pressSequentially(text, { delay: 35 });
  await ui.composer.press('Enter');
}

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const owner = new ScriptedOwner(['Greyhook', 'Saltmarsh']);
  const browser = await launchBrowser();
  const size = { width: 1280, height: 800 };
  const context = await hostContext(browser, owner, { viewport: size, recordVideo: { dir: out, size } });
  try {
    let { page } = await openSylvia(context);
    let ui = controls(page);
    await caption(page, 'Home: two saved campaigns (scripted owner, no real model)');
    await ui.campaign('Greyhook').click();
    await ui.composer.and(page.locator(':enabled')).waitFor();
    await caption(page, '1. Send a message; the Game Master starts working');
    await type(ui, '[hold] I listen at the door.');
    await ui.stop.waitFor();
    await caption(page, 'Stop replaces Send while the reply is running');
    await ui.stop.click();
    await ui.status.filter({ hasText: 'Stopped' }).waitFor();
    await caption(page, '2. Stopped: the message is kept, no reply was saved');
    await caption(page, 'A new message redirects the stopped turn');
    await type(ui, 'I knock instead.');
    await ui.story.getByText('The Game Master answers: I knock instead.').waitFor();
    await caption(page, 'The redirected reply arrives');
    await caption(page, '3. Now the provider fails during a reply');
    await type(ui, '[fail] I open the door.');
    await ui.retry.waitFor();
    await caption(page, 'Retry is offered; new messages wait until the turn is retried');
    await caption(page, '4. Close the window and open Sylvia again');
    await page.close();
    ({ page } = await openSylvia(context));
    ui = controls(page);
    await caption(page, 'Reopened: Home');
    await ui.campaign('Greyhook').click();
    await ui.retry.waitFor();
    await caption(page, 'The interrupted turn and its Retry are still there');
    await ui.retry.click();
    await ui.story.getByText('The Game Master answers: I open the door.').waitFor();
    await caption(page, '5. Manual Retry answers once; the message was not sent again');
    await caption(page, '6. Start a slow reply, then switch to the other campaign');
    await type(ui, '[late] I wait for the tide.');
    await ui.stop.waitFor();
    await ui.home.click();
    await ui.campaign('Saltmarsh').click();
    await ui.composer.and(page.locator(':enabled')).waitFor();
    await caption(page, 'Saltmarsh: Greyhook is still working, but nothing of it shows here');
    owner.release('Greyhook');
    await owner.settled('Greyhook');
    await caption(page, "Greyhook's reply arrived late; Saltmarsh is unchanged");
    await ui.home.click();
    await ui.campaign('Greyhook').click();
    await ui.story.getByText('The Game Master answers: I wait for the tide.').waitFor();
    await caption(page, 'Back in Greyhook: the late reply is shown once, in its own campaign');
    await page.close();
  } finally {
    await context.close();
    await browser.close();
  }
  const videos = fs.readdirSync(out).filter(name => name.endsWith('.webm'));
  console.log(videos.map(name => path.join(out, name)).join('\n'));
})().catch(error => { console.error(error); process.exit(1); });
