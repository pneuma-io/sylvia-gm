// Exercise the captured production function without a browser or live UI.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const sourcePath = process.argv[2];
const outputPath = process.argv[3];
assert(sourcePath && outputPath, 'Owned source and result paths are required');
const source = fs.readFileSync(sourcePath, 'utf8');
const start = source.indexOf('  function withCampaignEnding(');
const end = source.indexOf('  // Separate beginnings', start);
assert(start >= 0 && end > start, 'Production ending function must be present');
const context = vm.createContext({
  chronologyKeyAttribute: (key) => ` data-key="${key}"`,
  escapeHtml: (text) => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;'),
});
vm.runInContext(source.slice(start, end), context);
const render = context.withCampaignEnding;
const messages = [{message_id: 'closing'}];
const ending = {ending_id: 'life-ended', act_number: null, after_message_id: 'closing',
  plan_title: '<Private-safe title>', open_world: false, has_after_story: true, choice: null};
const results = [];
function check(name, operation) {
  try { operation(); results.push({name, status: 'PASS'}); }
  catch (error) { results.push({name, status: 'FAIL', message: error.message}); }
}
check('Early conclusion has Finish without any act-complete label', () => {
  const [card] = render(messages, [''], ending);
  assert(card.includes('Finish the story'));
  assert(!card.includes('Act '));
  assert(!card.includes('Continue adventuring'));
  assert(card.includes('&lt;') === false); // Non-continuable summary does not include the title.
});
check('Saved early choice has a quiet escaped marker and no active choices', () => {
  const [card] = render(messages, [''], {...ending, choice: 'finish'});
  assert(card.includes('campaign-ending-marker'));
  assert(card.includes('&lt;Private-safe title&gt;'));
  assert(!card.includes('<button'));
  assert(!card.includes('Act '));
});
check('Ordinary complete ending keeps its approved act label and Continue', () => {
  const [card] = render(messages, [''], {...ending, act_number: 6, open_world: true});
  assert(card.includes('Act 6 complete'));
  assert(card.includes('Continue adventuring'));
});
check('Busy ending disables the Finish action', () => {
  const [card] = render(messages, [''], ending, true);
  assert(card.includes(' disabled'));
});
check('Missing anchors and malformed ordinal remain hidden', () => {
  for (const invalid of [undefined, 0, -1, '1']) {
    assert.equal(render(messages, ['original'], {...ending, act_number: invalid})[0], 'original');
  }
  assert.equal(render(messages, ['original'], {...ending, after_message_id: 'missing'})[0], 'original');
});
fs.writeFileSync(outputPath, JSON.stringify({
  utc: new Date().toISOString(), source: sourcePath,
  no_browser_no_UI_no_live_calls: true, results}, null, 2));
process.stdout.write(JSON.stringify(results, null, 2) + '\n');
process.exitCode = results.some(item => item.status === 'FAIL') ? 1 : 0;
