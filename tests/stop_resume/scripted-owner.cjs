// A scripted Sylvia owner: the desktop host's command contract for Stop, Retry and reopen, in JS.
//
// invoke(command, args) takes the command names and camelCase arguments that createHostBridge()
// in app.js sends, and resolves with the owner's result shapes or rejects with the desktop host's
// "code: message" string. The shapes were recorded from the real owner through the WebUI, and
// contract.cjs checks the same promises against both this owner and the real one.
//
// Its GM follows the markers of JourneyGM in tests/stop_resume_owner_fixture.py: a message
// containing [hold] waits until Stop, [fail] fails once with a provider error, [late] waits until
// the test calls release(); any other message is answered at once. gate() delays one chosen read
// so a test can make a response arrive late. It has no timers, so tests wait on state, not time.
'use strict';

const HOLD = '[hold]', FAIL = '[fail]', LATE = '[late]';
const PROVIDER = 'test.transport', MODEL = 'test.transport/model0';
const FAILURE = 'provider_error: Your message was saved. Sylvia could not connect to the provider. ' +
  'Check the connection address, network, and whether your local model server is running, then ' +
  'retry. Saved work is kept.';

const reply = text => 'The Game Master answers: ' + text.replace(/\[(hold|fail|late)\]/g, '').trim();
const slug = text => text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

class Rejection extends Error {}

// Native desktop-host commands the main window calls for detached panel windows. The journeys open
// none, so the host has nothing to report; owner commands are below in #handlers.
const HOST = {
  sync_panel_windows: () => ({ failed_window_ids: [] }),
  publish_panel_workspace: () => null,
  focus_panel_window: () => null,
  panel_window_message: () => null,
  panel_window_reply: () => null,
};
const reject = (code, message) => { throw new Rejection(`${code}: ${message}`); };

function modelState() {
  const common = { provider_id: PROVIDER, model_id: MODEL, supported: null, capability_source: 'unknown',
    valid: true, requested: null, effective_request: null, native_options: {}, provider_reported_effective: null };
  return {
    reasoning_state: { current: null, once: null, ...common, scope: 'mode_default' },
    speed_state: { current: null, ...common, scope: 'provider_default' },
    model_state: { current: { provider_id: PROVIDER, model_id: MODEL }, once: null, default_model: null,
      chat_defaults: { reasoning_effort: null, speed: null } },
  };
}

class ScriptedOwner {
  // names: the campaigns to start with, each a No Ruleset campaign with one GM session.
  constructor(names) {
    this.campaigns = names.map((name, index) => this.#campaign(name, index));
    this.calls = [];
    this.unsupported = [];
    this.drafts = { format_version: 1, sessions: [] };
    this.gates = [];
    this.waiters = [];
    this.ordinal = 100;
  }

  #campaign(name, index) {
    const id = slug(name);
    return {
      name, campaign_id: `campaign-${id}`, session_id: `session-dm-${id}`, turn_id: `turn-dm-${id}`,
      revision: 4, ordinal: 10 + index, messages: [{ kind: 'evidence', message_id: `ready-reference-${id}`,
        content: { kind: 'ready_to_begin_reference', source_message_id: `guided-ready-${id}`,
          source_session_id: `session-guided-${id}` } }],
      canonical: [], interruption: null, attempt: null, retries: new Map(), failed: new Set(),
    };
  }

  campaign(name) {
    const found = this.campaigns.find(entry => entry.name === name || entry.campaign_id === name);
    if (!found) throw new Error(`No scripted campaign ${name}`);
    return found;
  }

  // Resolve when the named campaign's GM is working on a reply (or at once if it already is).
  working(name) { return this.#until(() => this.campaign(name).attempt?.status === 'working'); }

  // Resolve once the named campaign's GM is no longer working on a reply.
  settled(name) { return this.#until(() => this.campaign(name).attempt?.status !== 'working'); }

  // Finish a [late] reply for the named campaign.
  release(name) {
    const attempt = this.campaign(name).attempt;
    if (!attempt?.late) throw new Error(`${name} has no late reply waiting`);
    attempt.late();
  }

  // Delay the next call of command whose arguments include match until release(). `arrived`
  // resolves when the frontend makes that call, `answered` once its result has been returned.
  gate(command, match = {}) {
    const signal = () => { let resolve; const promise = new Promise(done => { resolve = done; });
      return { promise, resolve }; };
    const opened = signal(), arrived = signal(), answered = signal();
    this.gates.push({ command, match, opened: opened.promise, reached: arrived.resolve,
      answered: answered.resolve });
    return { arrived: arrived.promise, answered: answered.promise, release: () => opened.resolve() };
  }

  #until(condition) {
    if (condition()) return Promise.resolve();
    return new Promise(resolve => this.waiters.push({ condition, resolve }));
  }

  #changed() {
    this.waiters = this.waiters.filter(waiter => {
      if (!waiter.condition()) return true;
      waiter.resolve();
      return false;
    });
  }

  async invoke(command, args = {}) {
    this.calls.push({ command, args });
    const gateIndex = this.gates.findIndex(gate => gate.command === command &&
      Object.entries(gate.match).every(([key, value]) => args[key] === value));
    let gate = null;
    if (gateIndex >= 0) {
      [gate] = this.gates.splice(gateIndex, 1);
      gate.reached();
      await gate.opened;
    }
    const handler = this.#handlers[command] || HOST[command];
    if (!handler) {
      this.unsupported.push(command);
      reject('unsupported', `The scripted owner does not implement ${command}.`);
    }
    try {
      return structuredClone(await handler.call(this, args));
    } finally {
      this.#changed();
      gate?.answered();
    }
  }

  #session(id) {
    const campaign = this.campaigns.find(entry => entry.session_id === id);
    if (!campaign) reject('not_found', 'Session not found');
    return campaign;
  }

  #byCampaign(id) {
    const campaign = this.campaigns.find(entry => entry.campaign_id === id);
    if (!campaign) reject('not_found', 'Campaign not found');
    return campaign;
  }

  #indexRow(c) {
    const session = { session_id: c.session_id, role: 'dm', status: 'active', provider_id: PROVIDER, model_id: MODEL,
      selected_skill: null, model_guidance: { registry_version: 'sylvia.capability.registry.v1', model_id: MODEL,
        minimum_tier_id: null, placement_tier_id: null, placement_evidence_sha256: null, status: 'not_applicable',
        warning_code: null, override_accepted: false } };
    return { campaign_id: c.campaign_id, display_name: c.name, lifecycle: 'active', writer: 'dm',
      revision: c.revision, ruleset_summary: 'No rules', package_summary: 'No packages', package_count: 0,
      playable: true, availability: 'available', ruleset_id: null, ruleset_label: 'No rules',
      campaign_setting_id: null, campaign_setting_label: 'No campaign setting',
      session_activity_ordinal: c.ordinal, turn_id: c.turn_id, turn_status: 'open', session_id: c.session_id,
      session_status: 'active', provider_id: PROVIDER, model_id: MODEL, sessions: [session] };
  }

  #append(c, message) {
    c.messages.push(message);
    c.ordinal = ++this.ordinal;
  }

  #retryProjection(c) {
    const i = c.interruption;
    return { kind: 'retry', sequence: null, text: 'Narration interrupted—Retry',
      safe_payload: { interruption_id: i.interruption_id, failed_attempt_id: i.failed_attempt_id,
        resolving_retry_id: i.resolving_retry_id, can_redirect: i.can_redirect, saved_changes: false },
      collapsed_by_default: false, provisional: false, action_id: null, attempt_id: i.failed_attempt_id };
  }

  #interruptionResult(c) {
    const i = c.interruption;
    return { campaign_id: c.campaign_id, interruption_id: i.interruption_id, turn_id: c.turn_id,
      last_action_id: null, failed_attempt_id: i.failed_attempt_id, retryable: true,
      resolving_retry_id: i.resolving_retry_id, resolved_sequence: null };
  }

  // Run the scripted GM for one attempt; resolves with its talk event or rejects as the owner does.
  #generate(c, attempt, text) {
    attempt.status = 'working';
    return new Promise((resolve, rejectReply) => {
      attempt.stop = () => { attempt.status = 'stopped'; resolve(null); };
      const answer = () => {
        const sequence = c.canonical.length + 1;
        const event = { sequence, event_type: 'talk', turn_id: c.turn_id, action_id: null,
          safe_payload: { response_blocks: [{ kind: 'narration', text: reply(text) }], text: reply(text) },
          visibility: 'player_safe', attempt_id: attempt.attempt_id, attempt_ordinal: 0 };
        c.canonical.push(event);
        this.#append(c, { kind: 'evidence', message_id: `gm-activity-${attempt.attempt_id}`, content: {
          kind: 'gm_turn_activity', activity: 'turn_completed', attempt_id: attempt.attempt_id,
          basis_message_id: attempt.message_id, turn_id: c.turn_id,
          activity_timing: { phase: 'completed', elapsed_ms: 0, quiet_ms: 0, operation: null,
            last_operation: 'action', revision: 5 } } });
        this.#append(c, { kind: 'canonical_reference', message_id: `canonical-${sequence}`,
          canonical_sequence: sequence, content: { event_type: 'talk', safe_payload: event.safe_payload,
            visibility: 'player_safe' } });
        attempt.status = 'completed';
        resolve(event);
        this.#changed();
      };
      if (text.includes(HOLD)) return;
      if (text.includes(FAIL) && !c.failed.has(text)) {
        c.failed.add(text);
        attempt.status = 'failed';
        rejectReply(new Rejection(FAILURE));
        return;
      }
      if (text.includes(LATE)) { attempt.late = answer; return; }
      answer();
    });
  }

  #terminal(c, attempt, phase, category) {
    this.#append(c, { kind: 'evidence', message_id: `terminal-${attempt.attempt_id}`, content: {
      kind: 'session_attempt_terminal', attempt_id: attempt.attempt_id, input_id: attempt.input_id,
      message_id: attempt.message_id, committed_action_id: null, failure_category: category,
      recovery_kind: 'uncommitted_player_action', retryable: true, status: 'interrupted', turn_id: c.turn_id,
      activity_timing: { phase, elapsed_ms: 0, quiet_ms: 0, operation: null, last_operation: null, revision: 4 } } });
  }

  #interrupt(c, attempt, canRedirect) {
    c.interruption = { interruption_id: c.interruption?.interruption_id ||
        `interruption-${attempt.attempt_id}`, failed_attempt_id: attempt.input_id,
      can_redirect: canRedirect, resolving_retry_id: null, message_id: attempt.message_id, text: attempt.text };
  }

  #handlers = {
    desktop_update_startup_state: () => ({ pending: false }),
    desktop_update_state: () => ({ phase: 'idle', current_version: '0.0.0-scripted' }),
    load_desktop_drafts: function () { return this.drafts; },
    save_desktop_drafts: function ({ drafts }) { this.drafts = drafts; return { status: 'saved' }; },
    record_local_fault: () => ({ status: 'recorded' }),
    application_info: () => ({ product_name: 'Sylvia GM', version: '0.0.0-scripted',
      release_channel: 'scripted owner', signing_status: 'unsigned', platform: 'windows' }),
    provider_settings: () => ({ presets: [], configured: [], default_model: null,
      chat_defaults: { reasoning_effort: null, speed: null }, model_defaults: [] }),
    model_choices: () => [{ provider_id: PROVIDER, provider_name: 'Test Transport', model_id: MODEL,
      model_name: 'Model 0', context_window: 8192, capability_registry_version: 'sylvia.capability.registry.v1',
      capability_tier_id: null, capability_evidence_sha256: null }],
    campaign_index: function () { return this.campaigns.map(c => this.#indexRow(c)); },
    builder_chat_index: () => [],
    display_snapshot: function ({ campaignId }) {
      const c = this.#byCampaign(campaignId);
      return { campaign_id: c.campaign_id, display_name: c.name, lifecycle: 'active', writer: 'dm',
        revision: c.revision, package_lock: { entries: [] } };
    },
    cancel_display_load: () => ({ status: 'cancelled' }),
    campaign_panels: function ({ campaignId }) {
      const c = this.#byCampaign(campaignId);
      return { campaign_id: c.campaign_id, revision: c.revision, direct_actions_available: true, ruleset_id: null,
        layout: null, player_control: null, response_action: null, shot_effects: [], surfaces: [{
          id: 'sylvia-inventory', title: 'Inventory', icon: '▣', renderer: 'generic_inventory', group: null,
          order: 30, default_size: 220, minimum_size: 120, default_placement: 'docked',
          allowed_placements: ['docked', 'floating', 'window'], actions: [], data: { characters: [] } }] };
    },
    action_sequence: () => null,
    session_history_page: function ({ sessionId }) {
      const c = this.#session(sessionId);
      const messages = c.messages.map((message, index) => ({ session_id: c.session_id, sequence: index + 1,
        canonical_sequence: null, ...message }));
      const working = c.attempt?.status === 'working' ? c.attempt : null;
      const users = messages.filter(message => message.kind === 'user');
      return { session: { session_id: c.session_id, campaign_id: c.campaign_id, role: 'dm', provider_id: PROVIDER,
        model_id: MODEL, status: 'active', parent_session_id: null, selected_skill: null, scope: [],
        reasoning_effort: null, reasoning_effort_once: null }, messages, thinking: [], next_before_sequence: null,
      latest_sequence: messages.length, latest_user_sequence: users.at(-1)?.sequence ?? null,
      launch_messages: [], latest_canonical_sequence: c.canonical.at(-1)?.sequence ?? null, act_markers: [],
      campaign_ending: null, campaign_endings: [], builder_question_messages: [], provisional: [], activity: null,
      activity_progress: working ? { attempt_id: working.attempt_id, basis_message_id: working.message_id,
        phase: 'working', operation: null, last_operation: null, elapsed_ms: 0, quiet_ms: 0, revision: 3 } : null,
      context_status_hint: null, ...modelState() };
    },
    player_projection_page: function ({ campaignId, afterSequence }) {
      const c = this.#byCampaign(campaignId);
      // With no afterSequence the owner sends live controls only, no narration.
      const shown = afterSequence == null ? [] : c.canonical.filter(event => event.sequence > afterSequence);
      const talk = shown.map(event => ({
        kind: 'talk', sequence: event.sequence, text: event.safe_payload.text, safe_payload: event.safe_payload,
        collapsed_by_default: false, provisional: false, action_id: null, attempt_id: event.attempt_id }));
      // The owner withdraws the Retry offer while a reply for the interrupted turn is running.
      const offered = c.interruption && c.attempt?.status !== 'working';
      return offered ? [...talk, this.#retryProjection(c)] : talk;
    },
    submit_session_input: async function (args) {
      const c = this.#session(args.sessionId);
      if (c.attempt?.status === 'working') reject('session_busy', 'The Game Master is still working on a reply.');
      if (c.interruption && args.redirectInterruptionId !== c.interruption.interruption_id) {
        reject('command_rejected', 'Retry or resolve the interrupted turn first');
      }
      if (c.interruption && !c.interruption.can_redirect) {
        reject('command_rejected', 'Only stopped narration accepts replacement input');
      }
      const attempt = { attempt_id: args.attemptId, input_id: args.attemptId, message_id: args.messageId,
        text: args.userText };
      const redirected = c.interruption;
      this.#append(c, { kind: 'user', message_id: args.messageId, content: { attempt_id: args.attemptId,
        canonical_start_sequence: c.canonical.length, input_id: args.attemptId, input_kind: 'user',
        text: args.userText, turn_id: c.turn_id, ...(redirected ? { redirect_interruption_id:
          redirected.interruption_id, redirected_from_message_id: redirected.message_id } : {}) } });
      c.attempt = attempt;
      try {
        const event = await this.#generate(c, attempt, args.userText);
        if (!event) return { canonical: [], receipts: [], interruption: this.#interruptionResult(c) };
        c.interruption = null;
        return { canonical: [event], receipts: [] };
      } catch (error) {
        this.#terminal(c, attempt, 'failed', 'provider_unavailable');
        this.#interrupt(c, attempt, false);
        throw error;
      }
    },
    interrupt_stream: function ({ campaignId, attemptId }) {
      const c = this.#byCampaign(campaignId);
      const attempt = c.attempt;
      if (!attempt || attempt.attempt_id !== attemptId || attempt.status !== 'working') {
        reject('command_rejected', 'No running narration matches this Stop.');
      }
      if (attempt.retry_id) reject('unsupported', 'The scripted owner does not stop a Retry.');
      this.#terminal(c, attempt, 'stopped', 'user_cancelled');
      this.#interrupt(c, attempt, true);
      attempt.stop();
      return this.#interruptionResult(c);
    },
    stream_heartbeat: function ({ campaignId, attemptId }) {
      const attempt = this.#byCampaign(campaignId).attempt;
      if (!attempt || attempt.attempt_id !== attemptId || attempt.status !== 'working') {
        reject('command_rejected', 'Narration attachment is unavailable');
      }
      return { attachment_generation: 1 };
    },
    retryable_interruption: function ({ campaignId, failedAttemptId }) {
      const c = this.#byCampaign(campaignId);
      if (!c.interruption || c.interruption.failed_attempt_id !== failedAttemptId) {
        reject('not_found', 'No retryable interruption matches this attempt.');
      }
      return this.#interruptionResult(c);
    },
    manual_retry_session: async function ({ sessionId, interruptionId, retryId }) {
      const c = this.#session(sessionId);
      if (c.retries.has(retryId)) return { ...c.retries.get(retryId), replayed: true };
      const basis = c.interruption;
      if (!basis || basis.interruption_id !== interruptionId) {
        reject('command_rejected', 'This Retry no longer matches the interrupted turn.');
      }
      const attempt = { attempt_id: retryId, retry_id: retryId, input_id: basis.failed_attempt_id,
        message_id: basis.message_id, text: basis.text };
      c.attempt = attempt;
      basis.resolving_retry_id = retryId;
      const event = await this.#generate(c, attempt, basis.text);
      c.interruption = null;
      const result = { input: { campaign_id: c.campaign_id, interruption_id: interruptionId, retry_id: retryId,
        failed_attempt_id: basis.failed_attempt_id, canonical_high_water: event.sequence - 1, turn_id: c.turn_id,
        action_receipt: null }, canonical: [event] };
      c.retries.set(retryId, result);
      return { ...result, replayed: false };
    },
  };
}

module.exports = { ScriptedOwner, HOLD, FAIL, LATE, reply };
