const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const sandbox = { window: {}, document: { addEventListener() {} } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8'), sandbox);
const { withActMarkers, groupedAgentActivity } = sandbox.window.SylviaGMFrontend;
const marker = (act_number, status, after_message_id) => ({ act_number, status, after_message_id });

test('chapter card follows saved narration outside GM activity and combines same-response transitions', () => {
  const messages = [
    { message_id: 'player', kind: 'user', content: { text: 'Return the supplies.' } },
    { message_id: 'activity', kind: 'evidence', content: {
      kind: 'gm_turn_activity', attempt_id: 'a', basis_message_id: 'player',
    } },
    { message_id: 'reply', kind: 'canonical_reference', content: { event_type: 'talk' } },
    { message_id: 'next-player', kind: 'user', content: { text: 'Continue.' } },
  ];
  const cards = groupedAgentActivity(messages, ['PLAYER', '', 'NARRATION', 'NEXT PLAYER'], {}, 'dm');
  const markers = [marker(2, 'begins', 'reply'), marker(1, 'complete', 'reply')];
  const html = withActMarkers(messages, cards, [...markers, ...markers]).join('');
  assert.equal((html.match(/class="act-transition"/g) || []).length, 1);
  assert.match(html, /aria-label="Act 1 complete\. Act 2 begins"/);
  assert(html.indexOf('</details>') < html.indexOf('NARRATION'));
  assert(html.indexOf('NARRATION') < html.indexOf('<section'));
  assert(html.indexOf('</section>') < html.indexOf('NEXT PLAYER'));
  assert.equal(withActMarkers(messages, cards, markers).join(''), html);
});

test('older chapter cards appear only when their narration page is loaded; no future details render', () => {
  const markers = [
    { ...marker(1, 'complete', 'older'), title: 'PRIVATE', summary: '<script>PRIVATE</script>' },
    marker(2, 'begins', 'newer'), marker(7, 'introduced', 'newer'),
    marker('<img>', 'complete', 'newer'), marker(0, 'complete', 'newer'),
  ];
  const latest = withActMarkers([{ message_id: 'newer' }], ['NEW'], markers).join('');
  assert.match(latest, /Act 2 begins/);
  assert.doesNotMatch(latest, /Act 1|PRIVATE|img|Act 7/);
  const loaded = withActMarkers([{ message_id: 'older' }, { message_id: 'newer' }],
    ['OLD', 'NEW'], markers).join('');
  assert.equal((loaded.match(/class="act-transition"/g) || []).length, 2);
  assert.doesNotMatch(loaded, /PRIVATE|script|Act 7/);
  assert.match(withActMarkers([{ message_id: 'final' }], ['END'],
    [marker(6, 'complete', 'final')]).join(''), /Act 6 complete/);
});

test('separate act beginning introduces its saved narration while completion follows the closing reply', () => {
  const messages = [
    { message_id: 'closing', kind: 'canonical_reference', content: { event_type: 'talk' } },
    { message_id: 'player', kind: 'user', content: { text: 'Ask about Door research.' } },
    { message_id: 'activity', kind: 'evidence', content: {
      kind: 'gm_turn_activity', attempt_id: 'opening-attempt', basis_message_id: 'player',
    } },
    { message_id: 'opening', kind: 'canonical_reference', content: { event_type: 'talk' } },
  ];
  const cards = groupedAgentActivity(messages, ['CLOSING', 'PLAYER', '', 'OPENING'], {}, 'dm');
  const markers = [marker(1, 'complete', 'closing'), marker(2, 'begins', 'opening')];
  const html = withActMarkers(messages, cards, markers).join('');
  assert(html.indexOf('CLOSING') < html.indexOf('Act 1 complete'));
  assert(html.indexOf('Act 1 complete') < html.indexOf('PLAYER'));
  assert(html.indexOf('PLAYER') < html.indexOf('Act 2 begins'));
  assert(html.indexOf('</details>') < html.indexOf('Act 2 begins'));
  assert(html.indexOf('Act 2 begins') < html.indexOf('OPENING'));
  assert.equal((html.match(/class="act-transition"/g) || []).length, 2);
  assert.equal(withActMarkers(messages, cards, [...markers, ...markers]).join(''), html);
});
