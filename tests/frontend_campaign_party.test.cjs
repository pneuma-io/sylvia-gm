const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve;
  const promise = new Promise((yes) => { resolve = yes; });
  return { promise, resolve };
}

function basicElement() {
  const attributes = new Map();
  const listeners = new Map();
  return {
    value: '', textContent: '', innerHTML: '', outerHTML: '', className: '',
    dataset: {}, style: { setProperty(name, value) { this[name] = value; } }, children: [],
    disabled: false, hidden: false, open: false,
    options: [], selectedOptions: [],
    appendChild(child) {
      if (child.parentElement) {
        child.parentElement.children = child.parentElement.children.filter((item) => item !== child);
      }
      this.children.push(child);
      child.parentElement = this;
    },
    insertBefore(child, next) {
      if (child.parentElement) {
        child.parentElement.children = child.parentElement.children.filter((item) => item !== child);
      }
      const index = next ? this.children.indexOf(next) : -1;
      if (index >= 0) this.children.splice(index, 0, child);
      else this.children.push(child);
      child.parentElement = this;
    },
    replaceWith(next) {
      const parent = this.parentElement;
      parent.children[parent.children.indexOf(this)] = next;
      next.parentElement = parent;
      this.parentElement = null;
    },
    remove() {
      this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
      this.parentElement = null;
    },
    get lastElementChild() { return this.children.at(-1); },
    setAttribute: (name, value) => attributes.set(name, value),
    getAttribute: (name) => attributes.get(name),
    removeAttribute: (name) => attributes.delete(name),
    hasAttribute: (name) => attributes.has(name),
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    dispatchEvent(event) {
      event.target ||= this;
      for (const handler of listeners.get(event.type) || []) handler(event);
      return true;
    },
    focus() {}, classList: { add() {}, remove() {}, toggle() {} },
    querySelectorAll: () => [], querySelector: () => null,
  };
}

function selectElement() {
  const element = basicElement();
  let value = '';
  let html = '';
  Object.defineProperty(element, 'innerHTML', {
    get: () => html,
    set(next) {
      html = next;
      element.options = [...next.matchAll(/<option value="([^"]*)"([^>]*)>(.*?)<\/option>/g)]
        .map((match) => ({
          value: match[1], disabled: match[2].includes('disabled'), hidden: false,
          selected: /\bselected\b/.test(match[2]),
          textContent: match[3].replace(/<[^>]*>/g, '').replace(/&lt;/g, '<')
            .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&'),
        }));
      value = element.options.findLast((option) => option.selected)?.value ??
        element.options.find((option) => !option.disabled)?.value ?? '';
    },
  });
  Object.defineProperty(element, 'value', {
    get: () => value,
    set(next) {
      value = element.options.length && !element.options.some((option) => option.value === next)
        ? '' : next;
    },
  });
  Object.defineProperty(element, 'selectedOptions', {
    get: () => element.options.filter((option) => option.value === value),
  });
  return element;
}

function companionElement() {
  const element = basicElement();
  let html = '';
  let inputs = [];
  Object.defineProperty(element, 'innerHTML', {
    get: () => html,
    set(next) {
      html = next;
      inputs = [...next.matchAll(/<input[^>]*value="([^"]*)"([^>]*)>/g)].map((match) => {
        const label = { hidden: false };
        return {
          value: match[1], checked: false, disabled: match[2].includes('disabled'),
          dataset: { profileRequired: /data-profile-required="true"/.test(match[2]) ? 'true' : 'false' },
          addEventListener() {}, closest: () => label,
        };
      });
    },
  });
  element.querySelectorAll = (selector) => selector.includes(':checked')
    ? inputs.filter((input) => input.checked)
    : inputs;
  return element;
}

function partyOptions(id, players, companions = []) {
  return {
    template_id: id,
    title: id,
    description: `Options for ${id}`,
    starting_party: {
      player_character: { mode: 'compatible' },
      required_companions: [],
      custom_companions: { minimum: 0, maximum: 2 },
    },
    player_archetypes: players.map((playerId) => ({
      id: playerId, label: playerId, name: playerId, summary: `${playerId} summary`,
      profile_status: 'authored', campaign_ready: true,
    })),
    companions: companions.map((companionId) => ({
      id: companionId, label: companionId, name: companionId,
      summary: `${companionId} summary`, profile_status: 'authored', campaign_ready: true,
    })),
  };
}

function harness(stepIds = ['participants'], storage = null) {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, basicElement());
    return nodes.get(id);
  };
  nodes.set('rift-archetype', selectElement());
  nodes.set('character-profile-select', selectElement());
  nodes.set('rift-companions', companionElement());
  for (const id of ['construction-package', 'construction-setting', 'construction-template']) {
    nodes.set(id, selectElement());
  }
  // Interview tests wire the complete shell; give its composer the real layout relationships.
  const composer = basicElement();
  composer.querySelector = (selector) => selector === '.composer-footer' ? { offsetHeight: 32 } : null;
  const chronology = { clientHeight: 800, querySelector: () => ({ offsetHeight: 48 }) };
  node('session-status-text').offsetHeight = 0;
  node('player-input').closest = (selector) => selector === '.input-composer' ? composer :
    selector === '.chronology-col' ? chronology : null;
  node('player-input').getBoundingClientRect = () => ({
    width: 600, height: parseFloat(node('player-input').style.height) || 58,
  });
  const sections = stepIds.map((stepId) => {
    const section = basicElement();
    section.dataset.stepId = stepId;
    if (stepId.endsWith('_contribution')) {
      section.setAttribute('data-contribution-step', '');
    }
    return section;
  });
  const panel = basicElement();
  panel.querySelectorAll = (selector) => selector.includes('data-construction-step') ? sections : [];
  nodes.set('construction-panel', panel);
  const context = {
    window: { addEventListener() {} },
    document: {
      getElementById: node, querySelectorAll: () => [], querySelector: () => null,
      addEventListener() {}, createElement: (tag) => {
        const result = basicElement();
        if (tag === 'template') {
          result.content = { children: [] };
          Object.defineProperty(result, 'innerHTML', { set(html) {
            const child = basicElement();
            child.innerHTML = child.outerHTML = html;
            result.content.children = html ? [child] : [];
          } });
        }
        return result;
      }, body: basicElement(),
    },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    localStorage: storage || { getItem: () => null, setItem() {}, removeItem() {} },
    crypto: require('node:crypto').webcrypto,
    console, setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    requestAnimationFrame: () => 1,
  };
  let source = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.js'), 'utf8');
  const insertion = source.lastIndexOf('  return {');
  source = source.slice(0, insertion) + `  window.partyTest = {
    state, createHostBridge, refreshCampaignPartyOptions, campaignPartyOptionsReady,
    campaignPartySelectionProblem, updateCampaignPartyActionAvailability,
    showConstructionPanel, toolPresentation, toolResultMarkup, wireBuilderResultActions,
    advanceConstructionInterview, loadContribution, exportSelectedCampaignPlan,
    constructionStepAnswer, showConstructionStepId, currentConstructionStepId,
    activeConstructionStepIds, wireControls, refreshRulesetBrowser,
    resumeGuidedCampaignDraft, updateConstructionPlans, renderBuilderSession,
    finalizeSelectedConstruction,
    restoreConstructionAnswer, hideConstructionPanel,
    startCampaignBuilder, refreshCampaignIndex, showHome,
    renderCharacterProfiles, updateCharacterProfileControls, adaptSelectedCharacterProfile,
  };\n` + source.slice(insertion);
  source = ['panel-workspace.js', 'shot-playback.js', 'panel-host.js'].map((name) =>
    fs.readFileSync(path.join(__dirname, '../src-tauri/frontend', name), 'utf8')).join('\n') + '\n' + source;
  context.setTimeout ||= setTimeout;
  context.clearTimeout ||= clearTimeout;
  context.window.innerWidth = 1400;
  vm.runInNewContext(source, context, { filename: 'app.js' });
  const api = context.window.partyTest;
  api.state.view = 'session';
  api.state.activeCampaignId = 'campaign-a';
  api.state.activeSessionId = 'interview-a';
  api.state.campaignCreationCatalog = ['plan-a', 'plan-b'].map((id) => ({
    kind: 'campaign_plan', id, title: id, starting_party: partyOptions(id, []).starting_party,
  }));
  node('construction-package').value = 'rules';
  node('construction-setting').value = 'setting';
  node('construction-template').value = 'plan-a';
  return {
    ...api, node, window: context.window, document: context.document,
    flush: async () => { for (let index = 0; index < 200; index += 1) await Promise.resolve(); },
  };
}

test('saved companion missing from refreshed target requires explicit reselection', async () => {
  const h = harness();
  const saved = { archetype_id: 'pc', companion_ids: ['old-vela'] };
  h.state.constructionAnswers = { participants: { visible: 'Vela', value: saved } };
  h.state.bridge = {
    campaign_party_options: async () => partyOptions('plan-a', ['pc'], ['new-vela']),
  };
  await h.refreshCampaignPartyOptions({ preserveSelection: saved });
  assert.deepEqual(saved.companion_ids, ['old-vela'], 'saved answer must stay intact');
  assert.match(h.campaignPartySelectionProblem() || '', /saved.*companion|content.*changed/i,
    'an unavailable saved companion must not become an accepted empty party');
  assert.equal(h.node('construction-next-btn').disabled, true);
  assert.equal(h.node('construction-party-repair').hidden, false);
  h.wireControls();
  h.node('construction-reselect-party-btn').dispatchEvent({ type: 'click' });
  assert.equal(h.node('construction-party-repair').hidden, true);
  assert.deepEqual(saved.companion_ids, ['old-vela'], 'reselection is not an implicit saved answer');
  assert.equal(h.node('rift-archetype').value, '', 'reselection requires a deliberate player choice');
  assert.equal(h.node('rift-companions').querySelectorAll('input:checked').length, 0);
  h.node('rift-archetype').value = 'pc';
  h.node('rift-archetype').dispatchEvent({ type: 'change' });
  assert.equal(h.campaignPartySelectionProblem(), null);
});

for (const changed of [false, true]) {
  test(`Interview restore detects exact saved target changes (${changed})`, async () => {
    const h = harness(['ruleset', 'setting', 'plan', 'participants']);
    const ruleset = { id: 'rules', version: '1', content_sha256: 'r'.repeat(64) };
    const setting = { id: 'setting', version: '1', content_sha256: 's'.repeat(64) };
    // SQLite canonical JSON can reorder object fields without changing their identity.
    const target = { setting: { content_sha256: setting.content_sha256,
      version: setting.version, id: setting.id }, ruleset,
      plan_sha256: 'p'.repeat(64), plan_id: 'plan-a' };
    const messages = [
      ['rules-answer', { candidate_id: 'rules' }],
      ['setting-answer', { setting_id: 'setting' }],
      ['plan-answer', { plan_id: 'plan-a', target_identity: target }],
      ['party-answer', { archetype_id: 'pc', companion_ids: ['vela'], target_identity: target }],
    ].map(([message_id, interview_value]) => ({
      kind: 'user', message_id, content: { text: 'Saved answer', interview_value },
    }));
    messages.push({ kind: 'evidence', content: {
      kind: 'campaign_interview_state', construction_revision: 4, current_step: 'participants',
      answers: { ruleset: 'rules-answer', setting: 'setting-answer', plan: 'plan-answer',
        participants: 'party-answer' }, subflows: [],
    } });
    const before = JSON.stringify(messages);
    h.state.bridge = {
      session_context: async () => ({ messages }),
      campaign_creation_catalog: async () => [
        { kind: 'ruleset', ...ruleset, selection_id: 'rules', title: 'Rules',
          compatible_settings: [setting] },
        { kind: 'campaign_plan', id: 'plan-a', title: 'Plan', ruleset,
          campaign_setting: setting, content_sha256: (changed ? 'q' : 'p').repeat(64) },
      ],
      campaign_party_options: async () => partyOptions('plan-a', ['pc'], ['vela']),
    };
    await h.resumeGuidedCampaignDraft('campaign-a', 'interview-a');
    assert.equal(h.node('construction-party-repair').hidden, !changed);
    assert.equal(h.node('construction-next-btn').disabled, changed);
    assert.equal(JSON.stringify(messages), before, 'restore must not rewrite saved content');
    if (changed) {
      assert.match(h.campaignPartySelectionProblem(), /content has changed/);
      h.wireControls();
      h.node('construction-reselect-party-btn').dispatchEvent({ type: 'click' });
      h.node('rift-archetype').value = 'pc';
      h.node('rift-archetype').dispatchEvent({ type: 'change' });
      assert.equal(h.campaignPartySelectionProblem(), null);
    }
    const answer = h.constructionStepAnswer();
    assert.equal(answer.value.target_identity.plan_sha256, (changed ? 'q' : 'p').repeat(64));
    assert.deepEqual(JSON.parse(JSON.stringify(answer.value.target_identity.ruleset)), ruleset);
    h.showConstructionStepId('plan');
    assert.equal(h.constructionStepAnswer().value.target_identity.plan_sha256,
      (changed ? 'q' : 'p').repeat(64));
  });
}

test('adapted companion source labels are escaped and never fabricated for legacy characters', async () => {
  const h = harness();
  const options = partyOptions('plan-a', ['pc'], ['adapted', 'legacy']);
  options.companions[0].source_profile = {
    artifact_id: 'source', content_sha256: 'a'.repeat(64), name: '<Vela>',
  };
  h.state.bridge = {campaign_party_options: async () => options};
  await h.refreshCampaignPartyOptions();
  const html = h.node('rift-companions').innerHTML;
  assert.match(html, /Adapted from &lt;Vela&gt;/);
  assert.doesNotMatch(html, /<Vela>/);
  assert.equal((html.match(/Adapted from/g) || []).length, 1);
  assert.equal(h.node('rift-companions').querySelectorAll('input:checked').length, 0);
});

test('Ready summary preserves saved package labels through materialized selection aliases and model warnings', async () => {
  const ruleset = { id: 'dnd-srd-5.2.1', version: '1', content_sha256: 'r'.repeat(64) };
  const setting = { id: 'severed-lands', version: '1', content_sha256: 's'.repeat(64) };
  const answers = {
    ruleset: { candidate_id: 'candidate-before-materialization' },
    setting: { setting_id: setting.id }, plan: { plan_id: 'the-rift-awakening' },
    name: { display_name: 'Ready summary check' },
    participants: { archetype_id: 'pc', companion_ids: ['chloe', 'daniel'] },
    specific_requests: { no_additional_requests: true },
  };
  const messages = Object.entries(answers).map(([step, value]) => ({
    message_id: `answer-${step}`, kind: 'user', content: { text: step, interview_value: value },
  }));
  messages.push({ message_id: 'ready', kind: 'evidence', content: {
    kind: 'campaign_interview_state', construction_revision: 6, current_step: 'review',
    answers: Object.fromEntries(Object.keys(answers).map((step) => [step, `answer-${step}`])),
  } });
  const bridge = {
    session_context: async () => ({ messages }),
    campaign_creation_catalog: async () => [
      { kind: 'ruleset', ...ruleset, selection_id: ruleset.id, title: 'D&D SRD 5.2.1',
        selection_aliases: ['candidate-before-materialization'], compatible_settings: [setting] },
      { kind: 'campaign_setting', ...setting, title: 'Severed Lands' },
      { kind: 'campaign_plan', id: 'the-rift-awakening', title: 'The Rift Awakening',
        ruleset, campaign_setting: setting },
    ],
    campaign_party_options: async () => partyOptions('the-rift-awakening', ['pc'], ['chloe', 'daniel']),
    model_guidance: async () => ({ warning_required: true, status: 'unrated' }),
    finalize_campaign_construction: async () => { assert.fail('Warning inspection must not start a campaign'); },
  };
  const assertLabels = (h) => {
    const html = h.node('construction-review').innerHTML;
    assert.match(html, /Ruleset: D&amp;D SRD 5.2.1/);
    assert.match(html, /Setting: Severed Lands/);
    assert.match(html, /Plan: The Rift Awakening/);
    assert.match(html, /Companions: 2/);
    assert.doesNotMatch(html, /No Ruleset|Create a new Campaign Plan/);
  };
  for (const phase of ['materialized', 'reopened']) {
    const h = harness(['ruleset', 'setting', 'plan', 'name', 'participants', 'specific_requests', 'review']);
    h.state.bridge = bridge;
    h.state.activeSessionRole = 'builder';
    h.state.modelChoices = [{ provider_id: 'test', model_id: 'unrated' }];
    h.node('model-choice').value = '0';
    await h.resumeGuidedCampaignDraft('campaign-a', 'interview-a');
    assertLabels(h);
    await h.finalizeSelectedConstruction();
    assert.equal(h.node('model-guidance-warning').hidden, false, phase);
    assertLabels(h);
    h.wireControls();
    h.node('dismiss-model-guidance-btn').dispatchEvent({ type: 'click' });
    assert.equal(h.node('model-guidance-warning').hidden, true);
    assertLabels(h);
  }
});

test('Interview restore isolates free-text answers and restores only the selected draft', async () => {
  const h = harness(['ruleset', 'boundaries_preferences', 'specific_requests', 'review']);
  const answers = {
    boundaries_preferences: { boundaries: 'No spiders', preferences: 'A peaceful station morning' },
    specific_requests: { no_additional_requests: false, requests: 'Begin beside the ticket office' },
  };
  const savedMessages = Object.entries(answers).map(([step, value]) => ({
    message_id: `answer-${step}`, kind: 'user', content: { text: step, interview_value: value },
  }));
  savedMessages.push({ kind: 'evidence', content: {
    kind: 'campaign_interview_state', construction_revision: 2, current_step: 'review',
    answers: Object.fromEntries(Object.keys(answers).map(step => [step, `answer-${step}`])),
  } });
  const emptyMessages = [{ kind: 'evidence', content: {
    kind: 'campaign_interview_state', construction_revision: 0, current_step: 'ruleset', answers: {},
  } }];
  h.state.bridge = {
    session_context: async id => ({ messages: id === 'interview-a' ? savedMessages : emptyMessages }),
    campaign_creation_catalog: async () => [],
  };
  const savedBefore = JSON.stringify(savedMessages);
  await h.resumeGuidedCampaignDraft('campaign-a', 'interview-a');
  assert.equal(h.node('construction-preferences').value, answers.boundaries_preferences.preferences);
  h.state.activeCampaignId = 'campaign-b';
  h.state.activeSessionId = 'interview-b';
  await h.resumeGuidedCampaignDraft('campaign-b', 'interview-b');
  for (const id of ['construction-boundaries', 'construction-preferences', 'construction-premise']) {
    assert.equal(h.node(id).value, '', `${id} must not leak from another Interview`);
  }
  assert.equal(h.node('construction-no-requests').checked, true);
  assert.equal(h.node('construction-premise').disabled, true);
  h.state.activeCampaignId = 'campaign-a';
  h.state.activeSessionId = 'interview-a';
  await h.resumeGuidedCampaignDraft('campaign-a', 'interview-a');
  assert.equal(h.node('construction-boundaries').value, answers.boundaries_preferences.boundaries);
  assert.equal(h.node('construction-preferences').value, answers.boundaries_preferences.preferences);
  assert.equal(h.node('construction-premise').value, answers.specific_requests.requests);
  assert.equal(h.node('construction-no-requests').checked, false);
  assert.equal(h.node('construction-premise').disabled, false);
  assert.equal(JSON.stringify(savedMessages), savedBefore, 'restoration must not alter saved answers');
});

test('general profiles require explicit selection and respect Companion capacity and busy state', async () => {
  const h = harness();
  h.state.bridge = { campaign_party_options: async () => partyOptions('plan-a', ['pc']) };
  await h.refreshCampaignPartyOptions();
  h.renderCharacterProfiles();
  assert.equal(h.node('adapt-character-profile-btn').disabled, true);
  assert.match(h.node('character-profile-summary').textContent, /Builder chat first/);
  h.state.campaignCreationCatalog.push({kind: 'character_profile', id: 'profile-12345678',
    name: '<Vela>', summary: '<A traveller>', content_sha256: 'a'.repeat(64)});
  h.renderCharacterProfiles();
  assert.equal(h.node('character-profile-select').value, '');
  assert.match(h.node('character-profile-select').innerHTML, /&lt;Vela&gt;/);
  assert.match(h.node('character-profile-select').innerHTML, /Needs adaptation/);
  h.node('character-profile-select').value = 'profile-12345678';
  h.updateCharacterProfileControls();
  assert.equal(h.node('adapt-character-profile-btn').disabled, false);
  h.state.builderLaunchPending.add('interview-a');
  h.updateCharacterProfileControls();
  assert.equal(h.node('adapt-character-profile-btn').disabled, true);
  h.state.builderLaunchPending.clear();
  h.state.campaignPartyOptions.starting_party.custom_companions.maximum = 0;
  h.updateCharacterProfileControls();
  assert.equal(h.node('character-profile-picker').hidden, true);
  assert.equal(h.node('adapt-character-profile-btn').disabled, true);
});

test('adapt forwards the exact source once and cannot disturb a newly selected Interview', async () => {
  const h = harness();
  const pending = deferred();
  const requests = [];
  h.state.constructionDraftId = 'draft-a';
  h.state.bridge = {
    campaign_party_options: async () => partyOptions('plan-a', ['pc']),
    start_guided_creation_subflow: (args) => { requests.push(args); return pending.promise; },
  };
  await h.refreshCampaignPartyOptions();
  h.state.campaignCreationCatalog.push({kind: 'character_profile', id: 'profile-12345678',
    name: 'Vela', summary: 'A traveller', content_sha256: 'a'.repeat(64)});
  h.renderCharacterProfiles();
  h.node('character-profile-select').value = 'profile-12345678';
  const launch = h.adaptSelectedCharacterProfile();
  await h.adaptSelectedCharacterProfile();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].character_profile_id, 'profile-12345678');
  assert.equal(requests[0].character_profile_sha256, 'a'.repeat(64));
  assert.equal(requests[0].purpose, 'companion');
  h.state.activeSessionId = 'interview-b';
  h.state.constructionRevision = 99;
  pending.resolve({status: 'independent', child_session_id: 'builder-a', construction_revision: 4});
  await launch;
  assert.equal(h.state.activeSessionId, 'interview-b');
  assert.equal(h.state.constructionRevision, 99);
  assert.equal(h.state.builderLaunchPending.size, 0);
});

test('the package browser loads installed rulesets using elements present in the shipped page', async () => {
  const html = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.html'), 'utf8');
  const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]));
  const h = harness();
  const candidateSelect = selectElement();
  const compareSelect = selectElement();
  h.document.getElementById = (id) => {
    if (!ids.has(id)) return null;
    if (id === 'ruleset-candidate-select') return candidateSelect;
    if (id === 'ruleset-compare-select') return compareSelect;
    return h.node(id);
  };
  h.node('ruleset-support-counts').parentElement = basicElement();
  const inspected = [];
  h.state.bridge = {
    package_candidates: async () => [{ candidate_id: 'td-installed', package_lock: {
      entries: [{ role: 'primary', package_id: 'trigger-discipline', version: '1.0' }],
    } }],
    inspect_package_candidate: async (id) => { inspected.push(id); return { packages: [] }; },
  };

  await h.refreshRulesetBrowser();

  assert.equal(candidateSelect.disabled, false);
  assert.equal(candidateSelect.selectedOptions[0].textContent, 'trigger-discipline 1.0');
  assert.deepEqual(inspected, ['td-installed']);
  assert.equal(h.node('ruleset-inspection-status').textContent, 'No package files are available to view.');
});

test('host bridge forwards exact party selections for lookup and generic Start', async () => {
  const h = harness();
  const calls = [];
  h.window.__TAURI__ = { core: { invoke: async (...args) => { calls.push(args); return {}; } } };
  const bridge = h.createHostBridge();
  await bridge.campaign_party_options({
    ruleset_selection: 'logical.ruleset.selection', setting_id: 'signal-setting', plan_id: 'plan-a',
  });
  await bridge.start_guided_campaign({
    operation_id: 'start', campaign_id: 'campaign-a', builder_session_id: 'interview-a',
    display_name: 'Signals', candidate_id: 'logical.ruleset.selection', premise: '',
    setting_id: 'signal-setting', plan_id: 'plan-a', player_character_id: 'player-b',
    companion_ids: ['companion-a'], construction_intent: {
      boundaries: '', preferences: 'mystery', specific_requests: '', contributions: {},
    },
  });

  assert.deepEqual(JSON.parse(JSON.stringify(calls[0])), ['campaign_party_options', {
    rulesetSelection: 'logical.ruleset.selection', settingId: 'signal-setting', planId: 'plan-a',
  }]);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1])), ['start_guided_campaign', {
    operationId: 'start', campaignId: 'campaign-a', builderSessionId: 'interview-a',
    displayName: 'Signals', candidateId: 'logical.ruleset.selection', premise: '',
    settingId: 'signal-setting', planId: 'plan-a', playerCharacterId: 'player-b',
    companionIds: ['companion-a'], constructionIntent: {
      boundaries: '', preferences: 'mystery', specific_requests: '', contributions: {},
    },
  }]);
});

test('host bridge forwards exact guided Interview navigation fields', async () => {
  const h = harness();
  const calls = [];
  h.window.__TAURI__ = { core: { invoke: async (...args) => { calls.push(args); return {}; } } };
  const bridge = h.createHostBridge();

  await bridge.navigate_guided_campaign_draft({
    operation_id: 'navigate-back',
    campaign_id: 'campaign-a',
    builder_session_id: 'interview-a',
    expected_construction_revision: 4,
    target_step: 'plan',
  });

  assert.deepEqual(JSON.parse(JSON.stringify(calls[0])), ['navigate_guided_campaign_draft', {
    operationId: 'navigate-back',
    campaignId: 'campaign-a',
    builderSessionId: 'interview-a',
    expectedConstructionRevision: 4,
    targetStep: 'plan',
  }]);
});

test('the visible Interview selects content before its questions and retains the Setting copy', async () => {
  const html = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.html'), 'utf8');
  const stepIds = [...html.matchAll(/data-step-id="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(stepIds.slice(0, 6), [
    'ruleset', 'setting', 'plan',
    'ruleset_contribution', 'setting_contribution', 'plan_contribution',
  ]);
  const h = harness(stepIds);
  h.state.constructionDraftId = 'campaign-a';
  h.state.campaignCreationCatalog.unshift({
    kind: 'ruleset', id: 'rules', selection_id: 'rules',
    compatible_settings: [{ id: 'setting', candidate_id: 'saved-setting-copy' }],
  });
  h.showConstructionStepId('setting');
  let sent;
  h.state.bridge = { advance_guided_campaign_draft: async (command) => {
    sent = command;
    return { construction_revision: 2, current_step: 'plan' };
  } };
  await h.advanceConstructionInterview();
  assert.deepEqual(JSON.parse(JSON.stringify(sent.answer)), {
    setting_id: 'setting', candidate_id: 'saved-setting-copy',
  });
  assert.equal(h.currentConstructionStepId(), 'plan');
});

test('party choices explain the actual readiness failure instead of blaming a complete profile', async () => {
  const h = harness();
  const template = partyOptions('plan-a', ['visitor'], ['companion']);
  const reason = 'This Ruleset has no declared character state location. <state>';
  for (const item of [...template.player_archetypes, ...template.companions]) {
    item.campaign_ready = false;
    item.unavailable_reason = reason;
  }
  h.state.bridge = { campaign_party_options: async () => template };
  await h.refreshCampaignPartyOptions();
  for (const id of ['rift-archetype', 'rift-companions']) {
    const html = h.node(id).innerHTML;
    assert.match(html, /no declared character state location/);
    assert.match(html, /&lt;state&gt;/);
    assert.doesNotMatch(html, /Complete Character File first|<state>/);
    assert.match(html, /disabled/);
  }
});

test('an unavailable required character offers Plan completion instead of another optional copy', async () => {
  const h = harness();
  const template = partyOptions('plan-a', ['marek']);
  template.starting_party.required_companions = [{ id: 'rola', path: 'characters/rola.json' }];
  template.starting_party.custom_companions.maximum = 0;
  template.companions = [{ id: 'rola', name: 'Rola', summary: 'Needs completion',
    campaign_ready: false, reusable: false,
    source_ref: { id: 'rola', path: 'characters/rola.json' } }];
  h.state.campaignCreationCatalog[0].starting_party = template.starting_party;
  h.node('complete-required-characters-btn').hidden = true;
  h.state.bridge = { campaign_party_options: async () => template };
  await h.refreshCampaignPartyOptions();
  assert.equal(h.node('complete-required-characters-btn').hidden, false);
  h.node('rift-archetype').value = 'marek';
  assert.equal(h.campaignPartySelectionProblem(), 'A required Companion is unavailable or incomplete');

  template.companions[0].campaign_ready = true;
  await h.refreshCampaignPartyOptions();
  assert.equal(h.node('complete-required-characters-btn').hidden, true);
  const html = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.html'), 'utf8');
  assert.match(html, /id="complete-required-characters-btn"[^>]*data-start-creation-subflow="plan"/);
});

test('a blocked pending party choice survives restart only for the exact Interview source', async () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  const ruleset = { id: 'rules', version: '1', content_sha256: 'r'.repeat(64) };
  const setting = { id: 'setting', version: '1', content_sha256: 's'.repeat(64) };
  const plan = {
    kind: 'campaign_plan', id: 'plan-a', title: 'Plan A', content_sha256: 'p'.repeat(64),
    ruleset, campaign_setting: setting,
  };
  const catalog = [
    { kind: 'ruleset', selection_id: 'rules', title: 'Rules', ...ruleset,
      compatible_settings: [{ ...setting, candidate_id: 'setting-copy' }] },
    { kind: 'campaign_setting', title: 'Setting', ...setting },
    { ...plan, id: 'plan-older', content_sha256: 'o'.repeat(64) },
    plan,
  ];
  const options = partyOptions('plan-a', ['marek']);
  const companionRef = { id: 'rola', path: 'characters/rola.json' };
  options.starting_party.required_companions = [companionRef];
  options.starting_party.custom_companions.maximum = 0;
  options.companions = [{
    id: 'rola', name: 'Rola', summary: 'Needs completion', campaign_ready: false,
    source_ref: companionRef,
  }];
  plan.starting_party = options.starting_party;
  const messages = [
    ['ruleset-answer', 'ruleset', { candidate_id: 'rules' }],
    ['setting-answer', 'setting', { setting_id: 'setting', candidate_id: 'setting-copy' }],
    ['plan-answer', 'plan', { plan_id: 'plan-a' }],
  ].map(([message_id, step, interview_value], index) => ({
    message_id, sequence: index + 1, kind: 'user', content: { text: step, interview_value },
  }));
  messages.push({
    message_id: 'interview-state', sequence: 4, kind: 'evidence',
    content: {
      kind: 'campaign_interview_state', construction_revision: 4,
      current_step: 'participants', subflows: [],
      answers: { plan: 'plan-answer', ruleset: 'ruleset-answer', setting: 'setting-answer' },
    },
  });
  const configure = (h, selectedCatalog = catalog) => {
    h.state.constructionDraftId = 'campaign-a';
    h.state.campaignCreationCatalog = selectedCatalog;
    h.state.bridge = {
      campaign_creation_catalog: async () => selectedCatalog,
      campaign_party_options: async () => options,
      session_context: async () => ({ messages }),
      advance_guided_campaign_draft: async () => ({
        construction_revision: 5, current_step: 'review',
      }),
    };
  };

  const beforeRestart = harness(['ruleset', 'setting', 'plan', 'participants'], storage);
  configure(beforeRestart);
  beforeRestart.wireControls();
  beforeRestart.showConstructionStepId('participants');
  await beforeRestart.refreshCampaignPartyOptions();
  beforeRestart.node('rift-archetype').value = 'marek';
  beforeRestart.node('rift-archetype').dispatchEvent({ type: 'change' });
  assert.equal(beforeRestart.node('construction-next-btn').disabled, true);

  const afterRestart = harness(['ruleset', 'setting', 'plan', 'participants'], storage);
  configure(afterRestart);
  await afterRestart.resumeGuidedCampaignDraft('campaign-a', 'interview-a');
  assert.equal(afterRestart.node('construction-template').value, 'plan-a');
  assert.equal(afterRestart.node('rift-archetype').value, 'marek');
  assert.equal(afterRestart.node('construction-next-btn').disabled, true);

  const otherInterview = harness(['ruleset', 'setting', 'plan', 'participants'], storage);
  configure(otherInterview);
  await otherInterview.resumeGuidedCampaignDraft('campaign-a', 'interview-other');
  assert.equal(otherInterview.node('rift-archetype').value, '');

  const changedPlan = { ...plan, content_sha256: 'x'.repeat(64) };
  const changedCatalog = catalog.map((item) => item.id === plan.id ? changedPlan : item);
  const changedSource = harness(['ruleset', 'setting', 'plan', 'participants'], storage);
  configure(changedSource, changedCatalog);
  await changedSource.resumeGuidedCampaignDraft('campaign-a', 'interview-a');
  assert.equal(changedSource.node('rift-archetype').value, '');
  assert.equal(changedSource.node('construction-party-repair').hidden, false);
  assert.match(changedSource.campaignPartySelectionProblem(), /content has changed/);

  options.companions[0].campaign_ready = true;
  await afterRestart.refreshCampaignPartyOptions();
  assert.equal(afterRestart.node('construction-next-btn').disabled, false);
  await afterRestart.advanceConstructionInterview();

  const afterAcceptedAnswer = harness(
    ['ruleset', 'setting', 'plan', 'participants'], storage,
  );
  configure(afterAcceptedAnswer);
  await afterAcceptedAnswer.resumeGuidedCampaignDraft('campaign-a', 'interview-a');
  assert.equal(afterAcceptedAnswer.node('rift-archetype').value, '');
});

test('package import reports its outcome inside Settings', async () => {
  const h = harness();
  const draft = { campaign_id: 'package-draft', builder_session_id: 'package-builder' };
  const created = {
    campaign_id: draft.campaign_id, display_name: 'New campaign interview',
    availability: 'draft', lifecycle: 'active', revision: 0, writer: 'builder',
    session_id: draft.builder_session_id,
    sessions: [{ session_id: draft.builder_session_id, role: 'builder', status: 'active' }],
  };
  h.state.bridge = {
    install_package_file: async () => ({
      status: 'installed', file_name: 'Training Yard.sylvia-gm-package', candidate: {},
    }),
    create_guided_campaign_draft: async () => draft,
    campaign_index: async () => [created],
    player_projection: async () => [],
    snapshot: async () => ({
      campaign_id: draft.campaign_id, display_name: created.display_name,
      lifecycle: 'active', writer: 'builder', revision: 0,
    }),
    campaign_panels: async () => ({ campaign_id: draft.campaign_id, revision: 0, surfaces: [] }),
    session_context: async () => ({
      session: { ...created.sessions[0], campaign_id: draft.campaign_id }, messages: [],
    }),
    campaign_creation_catalog: async () => [],
  };
  h.wireControls();
  h.node('install-package-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.match(h.node('package-install-status').textContent, /Installed Training Yard/);

  h.state.bridge.install_package_file = async () => { throw new Error('Invalid archive format'); };
  h.node('install-package-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.match(h.node('package-install-status').textContent, /Invalid archive format/);

  h.state.bridge.install_package_file = async () => ({ status: 'cancelled' });
  h.node('install-package-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(h.node('package-install-status').textContent, 'Package installation cancelled');
  const html = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.html'), 'utf8');
  assert.match(html, /id="package-install-status"[^>]*role="status"/);
});

test('Campaign Plan import opens a new Interview instead of reusing the active DM session', async () => {
  const h = harness(['ruleset', 'setting', 'plan']);
  const previous = {
    campaign_id: 'greyhaven', display_name: 'Greyhaven', availability: 'available',
    lifecycle: 'active', revision: 31, writer: 'dm', session_id: 'greyhaven-gm',
    sessions: [{ session_id: 'greyhaven-gm', role: 'dm', status: 'active' }],
  };
  const draft = {
    campaign_id: 'import-interview', builder_session_id: 'import-builder',
    construction_revision: 0,
  };
  const created = {
    campaign_id: draft.campaign_id, display_name: 'New campaign interview',
    availability: 'draft', lifecycle: 'active', revision: 0, writer: 'builder',
    session_id: draft.builder_session_id,
    sessions: [{ session_id: draft.builder_session_id, role: 'builder', status: 'active' }],
  };
  const importedPlan = {
    kind: 'campaign_plan', id: 'tactical-range', title: 'Tactical Range',
    ruleset: { id: 'rules', version: '1', content_sha256: 'rules-sha' },
    campaign_setting: { id: 'setting', version: '1', content_sha256: 'setting-sha' },
  };
  const catalog = [{
    kind: 'ruleset', id: 'rules', selection_id: 'rules', title: 'Rules',
    content_sha256: 'rules-sha', compatible_settings: [{
      id: 'setting', version: '1', content_sha256: 'setting-sha',
      ruleset: importedPlan.ruleset,
    }],
  }, importedPlan];
  const advances = [];
  h.state.campaigns = [previous];
  h.state.activeCampaignId = previous.campaign_id;
  h.state.activeSessionId = previous.session_id;
  h.state.activeSessionRole = 'dm';
  h.state.activeSnapshot = { campaign_id: previous.campaign_id, revision: 31 };
  h.state.panelProjection = { campaign_id: previous.campaign_id, revision: 31, surfaces: [] };
  h.state.bridge = {
    install_package_file: async () => ({
      status: 'installed', file_name: 'Tactical Range.sylvia-gm-plan', candidate: importedPlan,
    }),
    create_guided_campaign_draft: async () => draft,
    campaign_index: async () => [previous, created],
    player_projection: async () => [],
    snapshot: async () => ({
      campaign_id: draft.campaign_id, display_name: created.display_name,
      lifecycle: 'active', writer: 'builder', revision: 0,
    }),
    campaign_panels: async () => ({ campaign_id: draft.campaign_id, revision: 0, surfaces: [] }),
    session_context: async () => ({
      session: { ...created.sessions[0], campaign_id: draft.campaign_id },
      messages: [{ kind: 'evidence', message_id: 'interview-state', sequence: 1, content: {
        kind: 'campaign_interview_state', construction_revision: 0,
        current_step: 'ruleset', answers: {}, subflows: [],
      } }],
    }),
    campaign_creation_catalog: async () => catalog,
    advance_guided_campaign_draft: async (command) => {
      advances.push(command);
      return { construction_revision: 1, current_step: 'setting', answer_message_id: 'answer-1' };
    },
  };
  h.wireControls();

  h.node('install-package-btn').dispatchEvent({ type: 'click' });
  await h.flush();

  assert.equal(h.state.activeCampaignId, draft.campaign_id);
  assert.equal(h.state.activeSessionId, draft.builder_session_id);
  assert.equal(h.state.activeSessionRole, 'builder');
  assert.equal(h.state.constructionDraftId, draft.campaign_id);
  assert.equal(h.state.activeSnapshot.campaign_id, draft.campaign_id);
  assert.equal(h.node('construction-template').value, importedPlan.id);
  assert.equal(h.state.panelProjection, null, 'the Builder Interview has no game panels');
  assert.ok(h.state.campaigns.includes(previous), 'the previous saved Campaign remains available');

  h.node('construction-next-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(advances.length, 1);
  assert.equal(advances[0].campaign_id, draft.campaign_id);
  assert.equal(advances[0].builder_session_id, draft.builder_session_id);
  assert.equal(h.currentConstructionStepId(), 'setting');
});

test('a repairable import shows errors and waits for an explicit Builder request', async () => {
  const h = harness();
  let starts = 0;
  h.state.bridge = {
    install_package_file: async () => ({
      status: 'repairable', repair_id: 'safe-receipt', file_name: 'Relay.zip',
      message: 'Package manifest fields are invalid',
      validation_errors: [{ file: 'manifest.json', path: '$.packages[0].invented',
        message: 'Remove this undeclared field.' }],
    }),
    start_import_repair: async () => { starts += 1; },
  };
  h.wireControls();
  h.node('install-package-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(starts, 0);
  assert.equal(h.node('repair-import-btn').hidden, false);
  assert.match(h.node('package-install-status').textContent, /packages\[0\]\.invented/);
  assert.doesNotMatch(h.node('package-install-status').textContent, /Installed/);
  assert.equal(h.state.pendingImportRepair.repair_id, 'safe-receipt');
  h.state.bridge.install_package_file = async () => ({ status: 'cancelled' });
  h.node('install-package-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(h.node('repair-import-btn').hidden, true);
  assert.equal(h.state.pendingImportRepair, null);
});

test('campaign recovery explains a collision inside Settings without replacing the campaign', async () => {
  const h = harness();
  const collision = 'portable_recovery_campaign_collision: campaign already exists';
  h.state.bridge = {
    recover_campaign_file: async () => { throw new Error(collision); },
    campaign_index: async () => { throw new Error('must not refresh after rejected recovery'); },
  };
  h.wireControls();
  h.node('recover-campaign-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.match(h.node('campaign-data-status').textContent, /already exists/);
  assert.match(h.node('campaign-data-status').textContent, /unchanged/i);
  assert.equal(h.state.activeCampaignId, 'campaign-a');
  assert.equal(h.state.activeSessionId, 'interview-a');
  const html = fs.readFileSync(path.join(__dirname, '../src-tauri/frontend/app.html'), 'utf8');
  assert.match(html, /id="campaign-data-status"[^>]*role="status"/);
});

test('an Interview save and contribution reply cannot repaint a different session', async () => {
  const h = harness();
  h.state.constructionDraftId = 'campaign-a';
  h.node('construction-template').value = '';
  h.node('construction-player').value = 'Vela';
  const answer = deferred();
  let request;
  h.state.bridge = { advance_guided_campaign_draft: (command) => {
    request = command;
    return answer.promise;
  } };
  const saving = h.advanceConstructionInterview();
  assert.equal(request.builder_session_id, 'interview-a');
  h.state.activeCampaignId = 'campaign-b';
  h.state.activeSessionId = 'interview-b';
  h.state.constructionRevision = 7;
  h.node('session-status-text').textContent = 'Other Interview';
  h.node('construction-next-btn').disabled = true;
  answer.resolve({ construction_revision: 1, current_step: 'boundaries_preferences' });
  await saving;
  assert.equal(h.state.constructionRevision, 7);
  assert.equal(h.node('session-status-text').textContent, 'Other Interview');
  assert.equal(h.node('construction-next-btn').disabled, true);

  const contribution = deferred();
  h.state.bridge = { guided_campaign_contribution: () => contribution.promise };
  const loading = h.loadContribution('ruleset_contribution');
  h.state.activeSessionId = 'builder-c';
  contribution.resolve({ title: 'Old Ruleset questions', questions: [] });
  assert.equal(await loading, null);
  assert.equal(h.state.constructionContributions.ruleset_contribution, undefined);
});

test('accepting the campaign name refreshes the saved Interview title in the sidebar', async () => {
  const h = harness(['name', 'participants']);
  h.state.constructionDraftId = 'campaign-a';
  h.node('construction-name').value = 'The silent relay';
  let saved = false;
  h.state.bridge = {
    advance_guided_campaign_draft: async (command) => {
      assert.equal(command.current_step, 'name');
      assert.equal(command.answer.display_name, 'The silent relay');
      saved = true;
      return { construction_revision: 4, current_step: 'participants' };
    },
    campaign_index: async () => {
      assert.equal(saved, true);
      return [{
        campaign_id: 'campaign-a', display_name: 'The silent relay · Interview',
        lifecycle: 'active', availability: 'draft', revision: 0,
        sessions: [{ session_id: 'interview-a', role: 'builder', status: 'active' }],
      }];
    },
  };

  await h.advanceConstructionInterview();

  assert.match(h.node('builder-chat-rail').innerHTML, /The silent relay · Interview/);
  assert.equal(h.node('session-title').textContent, 'The silent relay · Interview');
  assert.equal(h.node('ctx-campaign').textContent, 'The silent relay · Interview');
  assert.equal(h.state.constructionRevision, 4);
});

test('rejected Builder file work cannot display a successful review or save', () => {
  const h = harness();
  for (const tool_name of ['read_file', 'write_file', 'search_files', 'publish_ruleset_candidate']) {
    const card = h.toolResultMarkup({
      tool_name, result: { success: false, error: 'File not found: [workspace]/reference.md' },
    }, '');
    assert.match(card, /Builder could not use this request/);
    assert.doesNotMatch(card, /Operation |result-card|<summary>Details/);
    assert.doesNotMatch(card, /File reviewed|File saved|Files searched|Ruleset package ready/);
    assert.doesNotMatch(card, /data-builder-result-action/);
  }
  assert.match(h.toolResultMarkup({
    tool_name: 'read_file', result: { success: true, path: 'reference.md' },
  }, ''), /File reviewed/);
});

test('Ruleset test cards expose escaped expected and actual failures without a save claim', () => {
  const h = harness();
  const report = {
    status: 'failed', success: false, scenarios: [{ id: 'cost', status: 'failed', steps: [{
      step: 1, failures: [{ subject: 'state', path: ['charge'], expected: 3,
        actual: '<script>unsafe</script>', missing: false }],
    }] }],
  };
  for (const content of [
    { tool_name: 'test_ruleset_draft', result: report },
    { tool_name: 'publish_ruleset_candidate', result: { status: 'failed', success: false, tests: report } },
  ]) {
    const card = h.toolResultMarkup(content, '');
    assert.match(card, /Ruleset tests failed/);
    assert.match(card, /0\/1 scenarios passed/);
    assert.match(card, /<details[^>]*><summary>Test details/);
    assert.match(card, /Step 2/);
    assert.match(card, /Expected: 3/);
    assert.match(card, /Actual:.*&lt;script&gt;/);
    assert.doesNotMatch(card, /<script>|saved to the library/);
  }
});

test('passing scenarios with incomplete coverage never display a test pass', () => {
  const h = harness();
  const card = h.toolResultMarkup({ tool_name: 'publish_ruleset_candidate', result: {
    success: false, tests: { status: 'incomplete_coverage',
      scenarios: [{ status: 'passed', steps: [] }],
      coverage: { missing_actions: ['heal'], missing_rejections: ['attack'], missing_interaction: true },
    },
  } }, '');
  assert.match(card, /More Ruleset tests needed/);
  assert.match(card, /Actions needing outcome checks: heal/);
  assert.match(card, /Actions needing rejection checks: attack/);
  assert.doesNotMatch(card, /Ruleset tests passed|saved to the library/);
});

test('campaign content cards route the exact generic or Rift sibling Plan', () => {
  const h = harness();
  for (const plan of [
    { id: 'generic-plan', title: 'Signal Relay' },
    { id: 'rift-sibling', title: 'Another Rift', setup_id: 'the-rift-awakening' },
  ]) {
    const presentation = h.toolPresentation({
      tool_name: 'discover_campaign_content', result: { items: [{ kind: 'campaign_plan', ...plan }] },
    });
    assert.deepEqual(Array.from(presentation.actions), ['configure_campaign']);
    assert.equal(presentation.campaignPlanId, plan.id);
    assert.equal(presentation.setupId, plan.setup_id || '');
    const card = h.toolResultMarkup({
      tool_name: 'prepare_campaign_setup', result: {
        success: true, campaign_plan_id: plan.id, setup_id: plan.setup_id,
      },
    }, '');
    assert.match(card, new RegExp(`data-campaign-plan-id="${plan.id}"`));
    assert.match(card, new RegExp(`data-campaign-setup-id="${plan.setup_id || ''}"`));
  }
});

for (const cardAction of ['configure_campaign', 'use_campaign_plan']) {
test(`${cardAction} creates an Interview and saves the card Plan binding through Continue`, async () => {
  const h = harness(['ruleset', 'setting', 'plan', 'participants']);
  const independent = {
    display_name: 'Import repair',
    session: { session_id: 'repair-session', role: 'builder', status: 'active' },
  };
  const draft = {
    campaign_id: 'campaign-from-card', builder_session_id: 'interview-from-card',
    construction_revision: 0,
  };
  const draftIndex = {
    campaign_id: draft.campaign_id, display_name: 'New campaign interview',
    availability: 'draft', lifecycle: 'active', revision: 0, writer: 'builder',
    session_id: draft.builder_session_id,
    sessions: [{ session_id: draft.builder_session_id, role: 'builder', status: 'active' }],
  };
  const ruleset = {
    kind: 'ruleset', id: 'trigger-rules', version: '1', content_sha256: 'rules-hash',
    selection_id: 'trigger-candidate', title: 'Trigger Discipline',
    compatible_settings: [{ id: 'greyhaven', version: '2', content_sha256: 'setting-hash',
      candidate_id: 'greyhaven-snapshot' }],
  };
  const plan = {
    kind: 'campaign_plan', id: 'repaired-plan', title: 'Greyhaven Gate Alert',
    ruleset: { id: 'trigger-rules', version: '1', content_sha256: 'rules-hash' },
    campaign_setting: { id: 'greyhaven', version: '2', content_sha256: 'setting-hash' },
  };
  const catalog = [ruleset, { kind: 'campaign_setting', id: 'greyhaven', title: 'Greyhaven' }, plan];
  const created = [];
  const answers = [];
  const nextStep = { ruleset: 'setting', setting: 'plan', plan: 'participants' };
  h.state.builderChats = [independent];
  h.state.activeCampaignId = null;
  h.state.activeSessionId = independent.session.session_id;
  h.state.activeSessionRole = 'builder';
  h.state.constructionDraftId = null;
  h.state.modelChoices = [{ provider_id: 'provider-a', model_id: 'model-a' }];
  h.state.providerSettings = {
    default_model: { provider_id: 'provider-a', model_id: 'model-a' },
  };
  h.node('model-choice').value = '0';
  h.state.bridge = {
    create_guided_campaign_draft: async (command) => { created.push(command); return draft; },
    campaign_index: async () => [draftIndex],
    campaign_creation_catalog: async () => catalog,
    session_context: async () => ({
      session: { ...draftIndex.sessions[0], campaign_id: draft.campaign_id },
      messages: [{ kind: 'evidence', message_id: 'interview-state', sequence: 1,
        content: { kind: 'campaign_interview_state', construction_revision: 0,
          current_step: 'ruleset', answers: {}, subflows: [] } }],
    }),
    snapshot: async () => ({ campaign_id: draft.campaign_id,
      display_name: draftIndex.display_name, lifecycle: 'active', writer: 'builder', revision: 0 }),
    advance_guided_campaign_draft: async (command) => {
      answers.push(command);
      return { construction_revision: answers.length, current_step: nextStep[command.current_step] };
    },
  };
  const button = basicElement();
  const markup = h.toolResultMarkup(cardAction === 'use_campaign_plan' ? {
    tool_name: 'publish_campaign_plan', result: {
      success: true, artifact_id: 'internal-draft-id', library_artifact: { id: plan.id },
    },
  } : {
    tool_name: 'prepare_campaign_setup', result: { success: true, campaign_plan_id: plan.id },
  }, '');
  assert.match(markup, new RegExp(`data-campaign-plan-id="${plan.id}"`));
  button.dataset.campaignPlanId = markup.match(/data-campaign-plan-id="([^"]+)"/)[1];
  const card = basicElement();
  card.querySelectorAll = (selector) =>
    selector === `[data-builder-result-action="${cardAction}"]` ? [button] : [];
  h.wireBuilderResultActions(card);
  h.wireControls();

  button.dispatchEvent({ type: 'click' });
  button.dispatchEvent({ type: 'click' });
  await h.flush();

  assert.equal(created.length, 1);
  assert.equal(created[0].display_name, 'New campaign interview');
  assert.equal(h.state.activeCampaignId, draft.campaign_id);
  assert.equal(h.state.activeSessionId, draft.builder_session_id);
  assert.equal(h.state.constructionDraftId, draft.campaign_id);
  assert.equal(h.state.builderChats[0].session.session_id, independent.session.session_id);
  assert.equal(h.node('construction-panel').hidden, false);
  assert.equal(h.node('construction-status').textContent, 'Campaign Interview ready');
  assert.equal(h.node('construction-package').value, ruleset.selection_id);
  assert.equal(h.node('construction-setting').value, 'greyhaven');
  assert.equal(h.node('construction-template').value, plan.id);

  for (const step of ['ruleset', 'setting', 'plan']) {
    assert.equal(h.currentConstructionStepId(), step);
    h.node('construction-next-btn').dispatchEvent({ type: 'click' });
    await h.flush();
  }
  assert.deepEqual(answers.map((command) => JSON.parse(JSON.stringify(command.answer))), [
    { candidate_id: 'trigger-candidate' },
    { setting_id: 'greyhaven', candidate_id: 'greyhaven-snapshot' },
    { plan_id: 'repaired-plan' },
  ]);
});
}

test('opening a generic or Rift sibling card retains the exact selected Plan', async () => {
  for (const requestedId of ['generic-plan', 'rift-sibling']) {
    const h = harness();
    const ruleset = {
      kind: 'ruleset', id: 'rules', version: '1', content_sha256: 'rules-hash',
      selection_id: 'rules-selection', title: 'Rules', compatible_settings: [
        { id: 'setting', version: '1', content_sha256: 'setting-hash' },
      ],
    };
    const binding = {
      ruleset: { id: 'rules', version: '1', content_sha256: 'rules-hash' },
      campaign_setting: { id: 'setting', version: '1', content_sha256: 'setting-hash' },
    };
    const catalog = [ruleset,
      { kind: 'campaign_setting', id: 'setting', version: '1', title: 'Setting' },
      { kind: 'campaign_plan', id: 'other-plan', title: 'Other', ...binding },
      { kind: 'campaign_plan', id: 'generic-plan', title: 'Generic', ...binding },
      { kind: 'campaign_plan', id: 'rift-sibling', title: 'Rift sibling',
        setup_id: 'the-rift-awakening', ...binding },
    ];
    h.state.bridge = { campaign_creation_catalog: async () => catalog };

    await h.showConstructionPanel(requestedId);

    assert.equal(h.node('construction-template').value, requestedId);
  }
});

test('content changes refresh a suggested campaign name but preserve entered and restored names', async () => {
  const catalog = [
    { kind: 'ruleset', id: 'default-rules', selection_id: 'default-candidate', title: 'Default',
      version: '1', content_sha256: 'default-hash',
      compatible_settings: [{ id: 'default-setting', version: '1', content_sha256: 'default-setting-hash' }] },
    { kind: 'ruleset', id: 'trigger-rules', selection_id: 'trigger-candidate', title: 'Trigger Discipline',
      version: '1', content_sha256: 'trigger-hash',
      compatible_settings: [{ id: 'greyhaven', version: '1', content_sha256: 'greyhaven-hash' }] },
    { kind: 'campaign_setting', id: 'default-setting', version: '1', title: 'Default Setting' },
    { kind: 'campaign_setting', id: 'greyhaven', version: '1', title: 'Greyhaven Yard' },
    { kind: 'campaign_plan', id: 'default-plan', title: 'The Rift Awakening',
      ruleset: { id: 'default-rules', version: '1', content_sha256: 'default-hash' },
      campaign_setting: { id: 'default-setting', version: '1', content_sha256: 'default-setting-hash' } },
    { kind: 'campaign_plan', id: 'repaired-plan', title: 'Greyhaven Gate Alert',
      ruleset: { id: 'trigger-rules', version: '1', content_sha256: 'trigger-hash' },
      campaign_setting: { id: 'greyhaven', version: '1', content_sha256: 'greyhaven-hash' } },
  ];
  const setup = async () => {
    const h = harness(['ruleset', 'setting', 'plan', 'name']);
    h.state.bridge = { campaign_creation_catalog: async () => catalog };
    h.wireControls();
    await h.showConstructionPanel('default-plan');
    return h;
  };

  const suggested = await setup();
  assert.equal(suggested.node('construction-name').value, 'The Rift Awakening');
  suggested.node('construction-package').value = 'trigger-candidate';
  suggested.node('construction-package').dispatchEvent({ type: 'change' });
  assert.equal(suggested.node('construction-template').value, '', 'new content needs an explicit Plan choice');
  suggested.node('construction-template').value = 'repaired-plan';
  suggested.node('construction-template').dispatchEvent({ type: 'change' });
  assert.equal(suggested.node('construction-template').value, 'repaired-plan');
  assert.equal(suggested.node('construction-name').value, 'Greyhaven Gate Alert');

  suggested.node('construction-name').value = 'My gate campaign';
  suggested.node('construction-name').dispatchEvent({ type: 'input' });
  suggested.node('construction-package').value = 'default-candidate';
  suggested.node('construction-package').dispatchEvent({ type: 'change' });
  assert.equal(suggested.node('construction-name').value, 'My gate campaign');

  const restored = await setup();
  restored.restoreConstructionAnswer('name', {
    text: 'Saved campaign', interview_value: { display_name: 'Saved campaign' },
  });
  restored.node('construction-package').value = 'trigger-candidate';
  restored.node('construction-package').dispatchEvent({ type: 'change' });
  assert.equal(restored.node('construction-name').value, 'Saved campaign');

  restored.state.activeCampaignId = 'campaign-without-name';
  restored.state.activeSessionId = 'interview-without-name';
  restored.state.bridge.session_context = async () => ({ messages: [{
    kind: 'evidence', message_id: 'empty-interview', sequence: 1,
    content: { kind: 'campaign_interview_state', construction_revision: 0,
      current_step: 'ruleset', answers: {}, subflows: [] },
  }] });
  await restored.resumeGuidedCampaignDraft('campaign-without-name', 'interview-without-name');
  assert.equal(restored.node('construction-name').value, '');
});

test('same-title saved Plans have copy labels matching their Builder receipts', async () => {
  const h = harness();
  const binding = {
    ruleset: { id: 'rules', version: '1', content_sha256: 'rules-hash' },
    campaign_setting: { id: 'setting', version: '1', content_sha256: 'setting-hash' },
  };
  const plans = ['a', 'b'].map((letter) => ({
    kind: 'campaign_plan', id: `plan-${letter}`, title: 'Greyhaven Gate Alert',
    reusable: true, content_sha256: letter.repeat(64), ...binding,
  }));
  h.state.bridge = { campaign_creation_catalog: async () => [
    { kind: 'ruleset', id: 'rules', version: '1', content_sha256: 'rules-hash',
      selection_id: 'rules', title: 'Rules', compatible_settings: [binding.campaign_setting] },
    { kind: 'campaign_setting', id: 'setting', version: '1', title: 'Setting' },
    ...plans,
  ] };
  await h.showConstructionPanel('plan-a');
  const choices = h.node('construction-template').innerHTML;
  for (const plan of plans) {
    const label = `Saved copy ${plan.content_sha256.slice(0, 12)}`;
    assert.ok(choices.includes(label));
    const receipt = h.toolPresentation({ tool_name: 'publish_campaign_plan', result: {
      success: true, status: 'ready', title: plan.title, library_artifact: plan,
    } });
    assert.ok(receipt.summary.includes(label));
  }
  assert.equal(h.node('construction-template').value, 'plan-a');
});

test('unavailable saved content keeps healthy Rulesets selectable with persistent repair details', async () => {
  const h = harness(['ruleset', 'setting', 'plan', 'name', 'participants']);
  const badId = 'campaign-plan-reusable-000000000000000000000000-1234567890123456';
  h.state.bridge = { campaign_creation_catalog: async () => [
    { kind: 'ruleset', id: 'rules', selection_id: 'rules', title: 'Healthy Ruleset',
      version: '1', content_sha256: 'rules-hash', compatible_settings: [] },
    { kind: 'unavailable_content', content_kind: 'campaign_plan', id: badId,
      title: 'Saved Campaign Plan unavailable', summary: 'Repair a copy in Builder.',
      validation_errors: [{ file: 'plan.json', path: '$.initial_state.map',
        message: 'Invalid scene <test>' }] },
  ] };
  await h.showConstructionPanel();
  h.node('session-status-text').textContent = 'Campaign Draft · choose a Ruleset';
  assert.ok(h.node('construction-package').options.some((option) =>
    option.value === 'rules' && !option.disabled));
  assert.ok(!h.node('construction-template').options.some((option) => option.value === badId));
  assert.equal(h.node('construction-library-warnings').hidden, false);
  const details = h.node('construction-library-warning-items').innerHTML;
  assert.match(details, /Saved Campaign Plan unavailable/);
  assert.match(details, /567890123456/);
  assert.match(details, /plan\.json/);
  assert.match(details, /Invalid scene &lt;test&gt;/);
  assert.doesNotMatch(details, /<test>/);
});

test('a failed catalog request keeps its explanation visible after status updates', async () => {
  const h = harness();
  h.state.bridge = { campaign_creation_catalog: async () => { throw new Error('Unavailable'); } };
  await h.showConstructionPanel();
  h.node('session-status-text').textContent = 'Campaign Draft';
  assert.equal(h.node('construction-library-warnings').hidden, false);
  assert.match(h.node('construction-library-warning-items').textContent, /could not be loaded/);
});

test('late party options cannot repaint a changed selection or session', async () => {
  const h = harness();
  const latePlan = deferred();
  const lateSession = deferred();
  h.state.bridge = {
    campaign_party_options: ({ plan_id: planId }) => {
      if (planId === 'plan-a') return latePlan.promise;
      return Promise.resolve(partyOptions('plan-b', ['player-b']));
    },
  };

  const first = h.refreshCampaignPartyOptions();
  h.node('construction-template').value = 'plan-b';
  await h.refreshCampaignPartyOptions();
  latePlan.resolve(partyOptions('plan-a', ['late-player-a']));
  await first;
  assert.equal(h.state.campaignPartyOptions.template_id, 'plan-b');
  assert.equal(h.node('rift-archetype').value, '');

  h.state.bridge.campaign_party_options = () => lateSession.promise;
  const fromOldSession = h.refreshCampaignPartyOptions();
  h.state.activeSessionId = 'interview-b';
  lateSession.resolve(partyOptions('plan-b', ['late-session-player']));
  await fromOldSession;
  assert.equal(h.state.campaignPartyOptions, null);
  assert.equal(h.node('rift-archetype').value, '');
});

test('refresh preserves valid saved choices instead of selecting a newly published result', async () => {
  const h = harness();
  let options = partyOptions('plan-a', ['player-a', 'player-b'], ['companion-a']);
  h.state.bridge = { campaign_party_options: async () => options };
  await h.refreshCampaignPartyOptions();
  h.node('rift-archetype').value = 'player-b';
  h.node('rift-companions').querySelectorAll('input[type="checkbox"]')[0].checked = true;

  options = partyOptions(
    'plan-a',
    ['new-player', 'player-a', 'player-b'],
    ['new-companion', 'companion-a'],
  );
  await h.refreshCampaignPartyOptions();

  assert.equal(h.campaignPartyOptionsReady(), true);
  assert.equal(h.node('rift-archetype').value, 'player-b');
  assert.deepEqual(
    h.node('rift-companions').querySelectorAll('input[type="checkbox"]:checked')
      .map((input) => input.value),
    ['companion-a'],
  );
});

test('a new Character appearing after Builder return remains explicitly unselected', async () => {
  const h = harness();
  let players = [];
  h.state.bridge = { campaign_party_options: async () => partyOptions('plan-a', players) };
  await h.refreshCampaignPartyOptions();
  players = ['newly-saved-character'];

  await h.refreshCampaignPartyOptions();

  assert.equal(h.node('rift-archetype').value, '');
  assert.equal(h.node('construction-next-btn').disabled, true);
  h.node('rift-archetype').value = 'newly-saved-character';
  h.updateCampaignPartyActionAvailability();
  assert.equal(h.node('construction-next-btn').disabled, false);
});

test('an old interview catalog cannot repaint a newly selected session', async () => {
  const h = harness();
  const catalog = deferred();
  const originalCatalog = h.state.campaignCreationCatalog;
  h.state.bridge = { campaign_creation_catalog: () => catalog.promise };
  h.state.constructionRestoreInProgress = {};
  const opening = h.showConstructionPanel();
  h.state.activeSessionId = 'another-interview';
  catalog.resolve([{ kind: 'ruleset', id: 'late-rules', selection_id: 'late-rules' }]);

  await opening;

  assert.equal(h.state.campaignCreationCatalog, originalCatalog);
});

test('an unavailable fixed Player Character clears the choice and disables Continue', async () => {
  const h = harness();
  const options = partyOptions('plan-a', ['missing-fixed', 'other-player']);
  options.starting_party.player_character = {
    mode: 'fixed', character: { id: 'setting-character', path: 'characters/fixed.md' },
  };
  options.player_archetypes[0].source_ref = {
    id: 'setting-character', path: 'characters/fixed.md',
  };
  options.player_archetypes[0].campaign_ready = false;
  options.player_archetypes[0].summary = 'Complete the required mechanical character data first.';
  h.state.bridge = { campaign_party_options: async () => options };

  await h.refreshCampaignPartyOptions();

  assert.equal(h.node('rift-archetype').value, '');
  assert.equal(h.node('rift-archetype').disabled, true);
  assert.equal(h.node('construction-next-btn').disabled, true);
  assert.match(h.node('rift-archetype').innerHTML, /Complete the required mechanical character data/);
});

test('complete fixed and required Setting files are selected without using custom slots', async () => {
  const h = harness();
  const options = partyOptions('plan-a', ['captain', 'other-player'], ['guide', 'custom']);
  const playerRef = { id: 'captain', path: 'characters/captain.json' };
  const companionRef = { id: 'guide', path: 'characters/guide.json' };
  options.starting_party = {
    player_character: { mode: 'fixed', character: playerRef },
    required_companions: [companionRef],
    custom_companions: { minimum: 0, maximum: 0 },
  };
  options.player_archetypes[0].source_ref = playerRef;
  options.companions[0].source_ref = companionRef;
  h.state.bridge = { campaign_party_options: async () => options };

  await h.refreshCampaignPartyOptions();

  assert.equal(h.node('rift-archetype').value, 'captain');
  assert.equal(h.node('rift-archetype').disabled, true);
  const [required, custom] = h.node('rift-companions').querySelectorAll('input[type="checkbox"]');
  assert.equal(required.checked, true);
  assert.equal(required.disabled, true);
  assert.equal(custom.checked, false);
  assert.equal(custom.disabled, true);
  assert.equal(h.campaignPartySelectionProblem(), null);
  assert.equal(h.node('construction-next-btn').disabled, false);
});

test('Back from an incomplete party restores Continue on earlier interview steps', async () => {
  const h = harness(['ruleset', 'setting', 'plan', 'name', 'participants', 'review']);
  const options = partyOptions('plan-a', []);
  h.state.constructionDraftId = 'campaign-a';
  h.state.constructionRevision = 4;
  h.state.bridge = {
    campaign_party_options: async () => options,
    navigate_guided_campaign_draft: async (command) => ({
      construction_revision: command.expected_construction_revision + 1,
      current_step: command.target_step,
    }),
  };
  h.wireControls();
  h.showConstructionStepId('participants');
  await h.refreshCampaignPartyOptions();
  assert.equal(h.node('construction-next-btn').disabled, true);

  h.node('construction-back-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(h.currentConstructionStepId(), 'name');
  assert.equal(h.node('construction-next-btn').disabled, false);
  h.node('construction-back-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(h.currentConstructionStepId(), 'plan');
  assert.equal(h.node('construction-next-btn').disabled, false);

  h.showConstructionStepId('participants');
  assert.equal(h.node('construction-next-btn').disabled, true);
  h.showConstructionStepId('review');
  assert.equal(h.node('begin-construction-btn').disabled, true);
});

test('legacy construction without a saved Campaign Plan remains available', () => {
  const h = harness();
  h.node('construction-template').value = 'custom';
  h.state.campaignPartyOptionsStatus = 'error';

  h.updateCampaignPartyActionAvailability();

  assert.equal(h.campaignPartySelectionProblem(), null);
  assert.equal(h.node('construction-next-btn').disabled, false);
});

test('New No-Rules creates an isolated usable interview without stale Ruleset choices', async () => {
  const h = harness([
    'ruleset', 'setting', 'plan',
    'ruleset_contribution', 'setting_contribution', 'plan_contribution',
    'name', 'participants', 'boundaries_preferences', 'specific_requests', 'review',
  ]);
  const savedBuilder = {
    display_name: 'Character Builder',
    session: {
      session_id: 'character-builder', role: 'builder', status: 'active',
      provider_id: 'provider-a', model_id: 'model-a',
    },
  };
  const draft = {
    campaign_id: 'no-rules-draft', builder_session_id: 'no-rules-interview',
    construction_revision: 0,
  };
  const draftIndex = {
    campaign_id: draft.campaign_id, display_name: 'New campaign interview',
    availability: 'draft', lifecycle: 'active', revision: 0, writer: 'builder',
    session_id: draft.builder_session_id,
    sessions: [{
      session_id: draft.builder_session_id, role: 'builder', status: 'active',
      provider_id: 'provider-a', model_id: 'model-a',
    }],
  };
  const created = [];
  const answers = [];
  const navigations = [];
  let startCommand = null;
  const startPending = deferred();
  const nextStep = {
    ruleset: 'name',
    name: 'boundaries_preferences',
    boundaries_preferences: 'specific_requests',
    specific_requests: 'review',
  };

  h.state.builderChats = [savedBuilder];
  h.state.activeCampaignId = null;
  h.state.activeSessionId = savedBuilder.session.session_id;
  h.state.activeSessionRole = 'builder';
  h.state.modelChoices = [{ provider_id: 'provider-a', model_id: 'model-a' }];
  h.state.providerSettings = {
    default_model: { provider_id: 'provider-a', model_id: 'model-a' },
  };
  h.node('model-choice').value = '0';
  h.node('session-title').textContent = savedBuilder.display_name;
  h.node('player-input').value = 'unfinished character notes';
  h.node('ruleset-panel-host').hidden = false;
  h.node('ruleset-panels').innerHTML = '<section>Old Ruleset panel</section>';
  h.state.panelProjection = { campaign_id: 'old-rules-campaign', surfaces: [{ id: 'old' }] };
  h.state.bridge = {
    create_guided_campaign_draft: async (command) => { created.push(command); return draft; },
    campaign_index: async () => [draftIndex],
    campaign_creation_catalog: async () => [{
      kind: 'ruleset', id: 'rules-a', selection_id: 'rules-a', title: 'Rules A',
      compatible_settings: [{ id: 'setting-a', version: '1' }],
    }],
    session_context: async () => ({
      session: { ...draftIndex.sessions[0], campaign_id: draft.campaign_id },
      // The actual owner projects the selected chat model separately from its default.
      model_state: { current: { provider_id: 'provider-a', model_id: 'model-a' } },
      messages: [{
        kind: 'evidence', message_id: 'interview-state', sequence: 1,
        content: {
          kind: 'campaign_interview_state', construction_revision: 0,
          current_step: 'ruleset', answers: {}, subflows: [],
        },
      }],
    }),
    snapshot: async () => ({
      campaign_id: draft.campaign_id, display_name: draftIndex.display_name,
      lifecycle: 'active', writer: 'builder', revision: 0,
    }),
    advance_guided_campaign_draft: async (command) => {
      answers.push(command);
      return {
        construction_revision: answers.length,
        current_step: nextStep[command.current_step],
      };
    },
    navigate_guided_campaign_draft: async (command) => {
      navigations.push(command);
      return {
        construction_revision: command.expected_construction_revision + 1,
        current_step: command.target_step,
      };
    },
    start_guided_campaign: (command) => {
      startCommand = command;
      return startPending.promise;
    },
  };
  h.wireControls();

  h.node('new-no-rules-btn').dispatchEvent({ type: 'click' });
  await h.flush();

  assert.equal(created.length, 1);
  assert.equal(h.state.activeCampaignId, draft.campaign_id);
  assert.equal(h.state.activeSessionId, draft.builder_session_id);
  assert.equal(h.node('chat-model-choice').value, '0');
  assert.equal(h.state.constructionDraftId, draft.campaign_id);
  assert.equal(h.node('construction-status').textContent, 'Campaign Draft · narrative play');
  assert.equal(h.node('construction-package').value, '');
  const noRulesName = h.node('construction-name').value;
  h.updateConstructionPlans();
  assert.equal(h.node('construction-name').value, noRulesName);
  assert.equal(h.node('construction-panel').hidden, false);
  assert.equal(h.currentConstructionStepId(), 'ruleset');
  assert.deepEqual(Array.from(h.activeConstructionStepIds()), [
    'ruleset', 'name', 'boundaries_preferences', 'specific_requests', 'review',
  ]);
  assert.equal(h.state.builderChats.length, 1);
  assert.equal(h.state.builderChats[0].display_name, 'Character Builder');
  assert.deepEqual(JSON.parse(JSON.stringify(h.state.sessionDrafts.get('character-builder'))), {
    text: 'unfinished character notes', oocText: '', oocOpen: false,
  });
  assert.equal(h.node('ruleset-panel-host').hidden, true);
  assert.equal(h.node('ruleset-panels').innerHTML, '');

  h.node('construction-next-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(h.currentConstructionStepId(), 'name');
  assert.equal(answers.length, 1);
  assert.equal(answers[0].campaign_id, draft.campaign_id);
  assert.equal(answers[0].builder_session_id, draft.builder_session_id);
  assert.deepEqual(JSON.parse(JSON.stringify(answers[0].answer)), { candidate_id: null });

  h.node('construction-back-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(h.currentConstructionStepId(), 'ruleset');
  assert.equal(navigations.length, 1);
  assert.equal(navigations[0].target_step, 'ruleset');
  h.node('construction-next-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(h.currentConstructionStepId(), 'name');
  assert.equal(answers.length, 1);

  h.node('construction-name').value = 'Quiet Roads';
  h.node('construction-next-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(h.currentConstructionStepId(), 'boundaries_preferences');
  h.node('construction-next-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(h.currentConstructionStepId(), 'specific_requests');
  h.node('construction-no-requests').checked = true;
  h.node('construction-next-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(h.currentConstructionStepId(), 'review');

  h.node('construction-template').value = 'stale-plan';
  h.state.riftTemplate = { template_id: 'stale-setup' };
  h.state.campaignCreationCatalog.push({
    kind: 'campaign_plan', id: 'stale-plan', setup_id: 'stale-setup',
  });
  h.state.constructionAnswers.ruleset_contribution = { value: { old_choice: true } };
  h.node('rift-archetype').value = 'stale-player';
  h.node('rift-companions').querySelectorAll = (selector) =>
    selector.includes(':checked') ? [{ value: 'stale-companion' }] : [];
  h.node('begin-construction-btn').dispatchEvent({ type: 'click' });
  await h.flush();

  assert.ok(startCommand);
  assert.equal(startCommand.campaign_id, draft.campaign_id);
  assert.equal(startCommand.builder_session_id, draft.builder_session_id);
  assert.equal(startCommand.candidate_id, null);
  assert.equal(startCommand.setting_id, null);
  assert.equal(startCommand.plan_id, null);
  assert.equal(startCommand.player_character_id, null);
  assert.deepEqual(Array.from(startCommand.companion_ids), []);
  assert.deepEqual(JSON.parse(JSON.stringify(startCommand.construction_intent.contributions)), {});
});

test('Plan export captures the selected version and does not repaint another chat', async () => {
  const h = harness();
  h.state.campaignCreationCatalog[0].content_sha256 = 'a'.repeat(64);
  const pending = deferred();
  let request;
  h.state.bridge = { export_campaign_plan_file: (...args) => {
    request = args;
    return pending.promise;
  } };
  const exporting = h.exportSelectedCampaignPlan();
  assert.equal(request[1], 'plan-a');
  assert.equal(request[2], 'a'.repeat(64));
  assert.equal(h.node('export-campaign-plan-btn').disabled, true);
  h.state.activeSessionId = 'builder-b';
  h.node('session-status-text').textContent = 'Building a different world';
  pending.resolve({ status: 'exported', file_name: 'adventure.sylvia-gm-plan' });
  await exporting;
  assert.equal(h.node('session-status-text').textContent, 'Building a different world');
  assert.equal(h.node('export-campaign-plan-btn').disabled, false);
});

test('a Builder refresh cannot detach an Interview form while its answers are loading', async () => {
  const h = harness(['ruleset', 'setting']);
  const originalPanelHome = basicElement();
  originalPanelHome.appendChild(h.node('construction-panel'));
  h.state.activeSessionRole = 'builder';
  h.state.constructionDraftId = 'campaign-a';
  const catalog = deferred();
  const interviewContext = {
    session: { session_id: 'interview-a', role: 'builder', campaign_id: 'campaign-a' },
    messages: [
      { kind: 'user', message_id: 'answer-rules', content: {
        text: 'Rules', interview_value: { candidate_id: 'rules' },
      } },
      { kind: 'evidence', message_id: 'state-1', content: {
        kind: 'campaign_interview_state', construction_revision: 1,
        current_step: 'setting', answers: { ruleset: 'answer-rules' }, subflows: [],
      } },
    ],
  };
  h.state.bridge = {
    campaign_creation_catalog: () => catalog.promise,
    session_context: async () => interviewContext,
  };

  const restoring = h.resumeGuidedCampaignDraft('campaign-a', 'interview-a');
  await h.flush();
  assert.equal(h.node('construction-panel').hidden, true);
  assert.equal(h.node('construction-panel').parentElement, h.node('chronology'));
  assert.equal(h.node('session-status-text').textContent, 'Loading campaign Interview…');

  await h.renderBuilderSession('interview-a');

  assert.equal(h.node('construction-panel').parentElement, h.node('chronology'),
    'a session refresh must not replace the attached loading Interview');

  catalog.resolve([{
    kind: 'ruleset', id: 'rules', selection_id: 'rules', title: 'Rules',
    compatible_settings: [{ id: 'setting', version: '1' }],
  }]);
  await restoring;

  assert.equal(h.node('construction-panel').parentElement, h.node('chronology'));
  assert.equal(h.node('construction-panel').hidden, false);
  assert.equal(h.currentConstructionStepId(), 'setting');

  h.hideConstructionPanel(false);
  assert.equal(h.node('construction-panel').parentElement, originalPanelHome);
  await h.renderBuilderSession('interview-a');
  assert.equal(h.node('construction-panel').parentElement, originalPanelHome,
    'normal Builder rendering resumes after the Interview explicitly exits');
  assert.match(h.node('chronology').dataset.chronologyHtml, /Rules/);
});

for (const savedSetting of [null, 'severed-lands']) {
  test(`returning Interview refreshes settings without choosing a Builder result (${savedSetting})`, async () => {
    const h = harness(['ruleset', 'setting']);
    basicElement().appendChild(h.node('construction-panel'));
    const messages = [
      { kind: 'user', message_id: 'rules-answer', content: {
        interview_value: { candidate_id: 'rules' },
      } },
      ...(savedSetting ? [{ kind: 'user', message_id: 'setting-answer', content: {
        interview_value: { setting_id: savedSetting },
      } }] : []),
      { kind: 'evidence', content: {
        kind: 'campaign_interview_state', construction_revision: 2,
        current_step: 'setting', answers: {
          ruleset: 'rules-answer', ...(savedSetting ? { setting: 'setting-answer' } : {}),
        }, subflows: [],
      } },
    ];
    let submissions = 0;
    h.state.bridge = {
      session_context: async () => ({ messages }),
      campaign_creation_catalog: async () => [{
        kind: 'ruleset', id: 'rules', selection_id: 'rules', title: 'Rules',
        compatible_settings: [
          { id: 'lantern-harbor', version: '1' }, { id: 'severed-lands', version: '1' },
        ],
      }],
      submit_session_input: async () => { submissions++; },
    };
    await h.resumeGuidedCampaignDraft('campaign-a', 'interview-a');
    assert.equal(h.node('construction-setting').value, savedSetting || '');
    assert.match(h.node('construction-setting').innerHTML, /lantern-harbor/);
    assert.equal(submissions, 0, 'returning only reads the saved Interview');
    if (!savedSetting) {
      await h.advanceConstructionInterview();
      assert.equal(submissions, 0, 'Continue requires a deliberate Setting choice');
      assert.match(h.node('construction-status').textContent, /Choose a Campaign Setting/);
    }
  });
}

test('Interview Cancel opens Home and the saved question can be resumed', async () => {
  const h = harness(['ruleset', 'setting']);
  const panelHome = basicElement();
  panelHome.appendChild(h.node('construction-panel'));
  const campaign = {
    campaign_id: 'campaign-a', display_name: 'Saved Interview', availability: 'draft',
    lifecycle: 'active', revision: 0, sessions: [
      { session_id: 'interview-a', role: 'builder', status: 'active' },
    ],
  };
  const context = {
    session: { session_id: 'interview-a', campaign_id: 'campaign-a', role: 'builder' },
    messages: [
      { kind: 'user', message_id: 'answer-rules', content: {
        text: 'Rules', interview_value: { candidate_id: 'rules' },
      } },
      { kind: 'evidence', content: {
        kind: 'campaign_interview_state', construction_revision: 1,
        current_step: 'setting', answers: { ruleset: 'answer-rules' }, subflows: [],
      } },
    ],
  };
  h.state.activeSessionRole = 'builder';
  h.state.campaigns = [campaign];
  h.state.campaignIndexStatus = 'ready';
  h.state.bridge = {
    session_context: async () => context,
    campaign_creation_catalog: async () => [{
      kind: 'ruleset', id: 'rules', selection_id: 'rules', title: 'Rules',
      compatible_settings: [{ id: 'setting', version: '1' }],
    }],
  };
  h.wireControls();
  await h.resumeGuidedCampaignDraft('campaign-a', 'interview-a');
  h.node('cancel-construction-btn').dispatchEvent({ type: 'click' });

  assert.equal(h.state.view, 'home');
  assert.equal(h.node('home-view').hidden, false);
  assert.equal(h.node('construction-panel').hidden, true);
  assert.equal(h.state.activeCampaignId, null);
  assert.equal(h.state.activeSessionId, null);
  assert.equal(h.state.campaigns[0], campaign);
  assert.match(h.node('campaign-shelves').innerHTML, /Saved Interview/);

  h.state.view = 'session';
  h.state.activeCampaignId = campaign.campaign_id;
  h.state.activeSessionId = 'interview-a';
  h.state.activeSessionRole = 'builder';
  await h.resumeGuidedCampaignDraft('campaign-a', 'interview-a');
  assert.equal(h.node('construction-panel').hidden, false);
  assert.equal(h.currentConstructionStepId(), 'setting');
  assert.equal(h.node('construction-package').value, 'rules');
  assert.equal(h.state.constructionRevision, 1);
});

test('Back saves the earlier Interview cursor before Create New Campaign Plan starts', async () => {
  const h = harness(['plan', 'name']);
  const createPlanButton = basicElement();
  createPlanButton.dataset.startCreationSubflow = 'plan';
  createPlanButton.textContent = 'Create New Campaign Plan';
  h.document.querySelectorAll = (selector) => selector === '[data-start-creation-subflow]'
    ? [createPlanButton]
    : [];
  h.state.constructionDraftId = 'campaign-a';
  h.state.constructionRevision = 4;
  const navigationCommands = [];
  let subflowCommand = null;
  const subflowPending = deferred();
  h.state.bridge = {
    navigate_guided_campaign_draft: async (command) => {
      navigationCommands.push(command);
      return { construction_revision: 5, current_step: 'plan' };
    },
    start_guided_creation_subflow: (command) => {
      subflowCommand = command;
      return subflowPending.promise;
    },
  };
  h.wireControls();
  h.showConstructionStepId('name');

  h.node('construction-back-btn').dispatchEvent({ type: 'click' });
  await h.flush();

  assert.equal(h.currentConstructionStepId(), 'plan');
  assert.equal(navigationCommands.length, 1);
  assert.equal(navigationCommands[0].campaign_id, 'campaign-a');
  assert.equal(navigationCommands[0].builder_session_id, 'interview-a');
  assert.equal(navigationCommands[0].expected_construction_revision, 4);
  assert.equal(navigationCommands[0].target_step, 'plan');
  assert.equal(h.state.constructionRevision, 5);

  createPlanButton.dispatchEvent({ type: 'click' });
  await h.flush();

  assert.ok(subflowCommand);
  assert.equal(subflowCommand.purpose, 'plan');
  assert.equal(subflowCommand.expected_construction_revision, 5);
});

test('Continue revisits an unchanged saved question through the persisted Interview cursor', async () => {
  const h = harness(['plan', 'name']);
  const navigation = deferred();
  const navigationCommands = [];
  const advanceCommands = [];
  h.state.constructionDraftId = 'campaign-a';
  h.state.constructionRevision = 4;
  h.state.constructionAnswers.plan = {
    visible: 'plan-a',
    value: { plan_id: 'plan-a' },
  };
  const savedAnswers = JSON.stringify(h.state.constructionAnswers);
  h.state.bridge = {
    navigate_guided_campaign_draft: (command) => {
      navigationCommands.push(command);
      if (navigationCommands.length === 1) {
        return Promise.resolve({ construction_revision: 5, current_step: 'plan' });
      }
      return navigation.promise;
    },
    advance_guided_campaign_draft: async (command) => {
      advanceCommands.push(command);
      throw new Error('an unchanged saved answer must not be written again');
    },
  };
  h.wireControls();
  h.showConstructionStepId('name');

  h.node('construction-back-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(h.currentConstructionStepId(), 'plan');
  assert.equal(h.state.constructionRevision, 5);

  h.node('construction-next-btn').dispatchEvent({ type: 'click' });
  h.node('construction-next-btn').dispatchEvent({ type: 'click' });

  assert.equal(navigationCommands.length, 2,
    'the disabled Continue button must suppress a duplicate pending navigation');
  assert.equal(navigationCommands[1].campaign_id, 'campaign-a');
  assert.equal(navigationCommands[1].builder_session_id, 'interview-a');
  assert.equal(navigationCommands[1].expected_construction_revision, 5);
  assert.equal(navigationCommands[1].target_step, 'name');
  assert.equal(advanceCommands.length, 0);
  assert.equal(JSON.stringify(h.state.constructionAnswers), savedAnswers);

  navigation.resolve({ construction_revision: 6, current_step: 'name' });
  await h.flush();

  assert.equal(h.currentConstructionStepId(), 'name');
  assert.equal(h.state.constructionRevision, 6);
  assert.equal(JSON.stringify(h.state.constructionAnswers), savedAnswers);
});

test('rejected Back navigation leaves the current Interview step visible', async () => {
  const h = harness(['plan', 'name']);
  h.state.constructionDraftId = 'campaign-a';
  h.state.constructionRevision = 4;
  h.state.bridge = {
    navigate_guided_campaign_draft: async () => {
      throw new Error('Stale construction revision');
    },
  };
  h.wireControls();
  h.showConstructionStepId('name');

  h.node('construction-back-btn').dispatchEvent({ type: 'click' });
  await h.flush();

  assert.equal(h.currentConstructionStepId(), 'name');
  assert.equal(h.state.constructionRevision, 4);
  assert.match(h.node('construction-status').textContent, /Stale construction revision/);
});

test('late Back navigation cannot repaint a newly selected Interview', async () => {
  const h = harness(['plan', 'name']);
  const navigation = deferred();
  h.state.constructionDraftId = 'campaign-a';
  h.state.constructionRevision = 4;
  h.state.bridge = { navigate_guided_campaign_draft: () => navigation.promise };
  h.wireControls();
  h.showConstructionStepId('name');

  h.node('construction-back-btn').dispatchEvent({ type: 'click' });
  h.state.activeCampaignId = 'campaign-b';
  h.state.activeSessionId = 'interview-b';
  h.state.constructionDraftId = 'campaign-b';
  h.state.constructionRevision = 11;
  h.showConstructionStepId('name');
  h.node('session-status-text').textContent = 'Other Interview';
  assert.equal(h.node('construction-back-btn').disabled, false);
  navigation.resolve({ construction_revision: 5, current_step: 'plan' });
  await h.flush();

  assert.equal(h.currentConstructionStepId(), 'name');
  assert.equal(h.state.constructionRevision, 11);
  assert.equal(h.node('session-status-text').textContent, 'Other Interview');
});

test('New No-Rules never exposes the previous Interview Ruleset while owner projections load', async () => {
  const h = harness([
    'ruleset', 'setting', 'plan',
    'ruleset_contribution', 'setting_contribution', 'plan_contribution',
    'name', 'participants', 'boundaries_preferences', 'specific_requests', 'review',
  ]);
  const existing = {
    campaign_id: 'saved-interview', display_name: 'Saved Interview',
    availability: 'draft', lifecycle: 'active', revision: 2, writer: 'builder',
    session_id: 'saved-builder',
    sessions: [{ session_id: 'saved-builder', role: 'builder', status: 'active' }],
  };
  const draft = {
    campaign_id: 'no-rules-draft', builder_session_id: 'no-rules-builder',
    construction_revision: 0,
  };
  const created = {
    campaign_id: draft.campaign_id, display_name: 'New campaign interview',
    availability: 'draft', lifecycle: 'active', revision: 0, writer: 'builder',
    session_id: draft.builder_session_id,
    sessions: [{ session_id: draft.builder_session_id, role: 'builder', status: 'active' }],
  };
  const campaignIndex = deferred();
  const snapshot = deferred();
  const campaignPanels = deferred();
  const catalog = deferred();
  const creationCommands = [];
  h.state.campaigns = [existing];
  h.state.activeCampaignId = existing.campaign_id;
  h.state.activeSessionId = existing.session_id;
  h.state.activeSessionRole = 'builder';
  h.state.constructionDraftId = existing.campaign_id;
  h.state.constructionRevision = 2;
  h.state.modelChoices = [{ provider_id: 'provider-a', model_id: 'model-a' }];
  h.state.providerSettings = {
    default_model: { provider_id: 'provider-a', model_id: 'model-a' },
  };
  h.node('model-choice').value = '0';
  h.node('construction-package').innerHTML =
    '<option value="dnd-srd" selected>D&amp;D SRD</option>';
  h.node('construction-panel').hidden = false;
  h.node('construction-package').disabled = false;
  const assertPreviousRulesetIsUnavailable = (phase) => {
    assert.ok(
      h.node('construction-panel').hidden ||
        h.node('construction-package').disabled ||
        h.node('construction-package').value !== 'dnd-srd',
      `the previous Interview Ruleset remained enabled while ${phase}`,
    );
  };
  h.state.bridge = {
    create_guided_campaign_draft: async (command) => {
      creationCommands.push(command);
      return draft;
    },
    campaign_index: () => campaignIndex.promise,
    snapshot: () => snapshot.promise,
    campaign_panels: () => campaignPanels.promise,
    session_context: async () => ({
      session: { ...created.sessions[0], campaign_id: created.campaign_id },
      messages: [{
        kind: 'evidence', message_id: 'new-interview-state', sequence: 1,
        content: {
          kind: 'campaign_interview_state', construction_revision: 0,
          current_step: 'ruleset', answers: {}, subflows: [],
        },
      }],
    }),
    campaign_creation_catalog: () => catalog.promise,
  };
  h.wireControls();

  h.node('new-no-rules-btn').dispatchEvent({ type: 'click' });
  await h.flush();
  assert.equal(creationCommands.length, 1);
  assertPreviousRulesetIsUnavailable('the campaign list loaded');

  campaignIndex.resolve([existing, created]);
  await h.flush();
  assertPreviousRulesetIsUnavailable('the new Draft snapshot loaded');

  snapshot.resolve({
    campaign_id: draft.campaign_id, display_name: 'New campaign interview',
    lifecycle: 'active', writer: 'builder', revision: 0,
  });
  await h.flush();
  assertPreviousRulesetIsUnavailable('the new Draft panel projection loaded');

  campaignPanels.resolve({ campaign_id: draft.campaign_id, revision: 0, surfaces: [] });
  await h.flush();
  assertPreviousRulesetIsUnavailable('the campaign creation catalog loaded');

  catalog.resolve([{
    kind: 'ruleset', id: 'dnd-srd', selection_id: 'dnd-srd', title: 'D&D SRD',
    compatible_settings: [{ id: 'forgotten-realms', version: '1' }],
  }]);
  await h.flush();

  assert.equal(h.state.activeCampaignId, draft.campaign_id);
  assert.equal(h.state.activeSessionId, draft.builder_session_id);
  assert.equal(h.state.constructionDraftId, draft.campaign_id);
  assert.equal(h.node('construction-package').value, '');
  assert.deepEqual(Array.from(h.activeConstructionStepIds()), [
    'ruleset', 'name', 'boundaries_preferences', 'specific_requests', 'review',
  ]);
  assert.equal(h.currentConstructionStepId(), 'ruleset');
  assert.equal(h.node('construction-status').textContent, 'Campaign Draft · narrative play');
  assert.equal(h.node('construction-panel').hidden, false);
});

for (const newestFirst of [false, true]) {
test(`New Campaign opens its saved Interview when the newest list finishes ${newestFirst ? 'first' : 'last'}`, async () => {
  const h = harness(['ruleset', 'name', 'boundaries_preferences', 'specific_requests', 'review']);
  const draft = { campaign_id: 'new-draft', builder_session_id: 'new-interview', construction_revision: 0 };
  const created = {
    campaign_id: draft.campaign_id, display_name: 'New campaign interview', availability: 'draft',
    lifecycle: 'active', revision: 0, writer: 'builder', session_id: draft.builder_session_id,
    sessions: [{ session_id: draft.builder_session_id, role: 'builder', status: 'active' }],
  };
  const requestedList = deferred(), backgroundList = deferred();
  let listReads = 0, creations = 0;
  h.state.campaigns = [];
  h.state.bridge = {
    create_guided_campaign_draft: async () => { creations += 1; return draft; },
    campaign_index: () => ++listReads === 1 ? requestedList.promise : backgroundList.promise,
    snapshot: async () => ({ campaign_id: draft.campaign_id, display_name: created.display_name,
      lifecycle: 'active', writer: 'builder', revision: 0 }),
    session_context: async () => ({ session: created.sessions[0], messages: [{ kind: 'evidence',
      message_id: 'initial-interview', sequence: 1, content: { kind: 'campaign_interview_state',
        construction_revision: 0, current_step: 'ruleset', answers: {}, subflows: [] } }] }),
    campaign_creation_catalog: async () => [],
    player_projection: async () => [],
  };
  h.showHome();
  const opening = h.startCampaignBuilder();
  await h.flush();
  const background = h.refreshCampaignIndex();
  if (newestFirst) {
    backgroundList.resolve([created]);
    await background;
  }
  requestedList.resolve([created]);
  await h.flush();
  const prematureError = h.node('session-status-text').textContent;
  backgroundList.resolve([created]);
  await Promise.all([opening, background]);

  assert.doesNotMatch(prematureError, /could not be created|TypeError/);
  assert.equal(creations, 1);
  assert.equal(h.state.activeCampaignId, draft.campaign_id);
  assert.equal(h.state.activeSessionId, draft.builder_session_id);
  assert.equal(h.node('construction-panel').hidden, false);
  assert.equal(h.currentConstructionStepId(), 'ruleset');
});
}
