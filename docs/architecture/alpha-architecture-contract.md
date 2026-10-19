# Sylvia GM Alpha Architecture Contract

> **Status:** Active architecture authority
>
> **Scope:** Detailed architecture for the Windows 11 public Alpha under `ARCH-002` and `ARCH-003`.
>
> **Execution boundary:** This contract defines mechanisms, not side-effect permission. Direct
> human authority in the current task governs implementation, commits, pushes, deployment,
> publication, spending, destructive operations, credentials, and personal campaign data.

## 1. Purpose and authority

This contract translates the active product contract into one coherent local architecture. It is
the sole authority for campaign truth, transaction, event, package, agent, lifecycle, and desktop
mechanisms. The active product contract governs promised behavior; the active process governs how
work is delivered; the active plan governs status and sequence.

Historical decision IDs and sources are retained in
[`../evidence/decision-ledger-provenance.md`](../evidence/decision-ledger-provenance.md). They may
explain why a mechanism exists but cannot override this contract.


## 2. Selected architecture in one paragraph

Sylvia GM Alpha is a **local modular monolith**. A desktop-supervised Sylvia GM owner process is the sole process allowed to open one application-level `sylvia-gm.db`. The Windows desktop and any future local clients communicate with that owner through authenticated local APIs and streams. Normalized SQLite records contain mutable campaign truth, plans, TURN/action outcomes, canonical chronological segments, exact package lineage and pins, and immutable validated Alpha package/dependency bytes. Generic Builder and DM sessions interact through application services; every model-facing ruleset or engine interaction crosses dynamically advertised MCP tools. The implementation keeps those responsibilities explicit and uses one production path for each authority boundary.

## 3. Non-goals

The Alpha architecture does not introduce:

- microservices or multiple campaign-authority services;
- full event sourcing or one giant campaign row;
- a distributed command ledger;
- an external package/content store before measured need;
- a bespoke durable Builder job/workflow engine;
- automatic provider fallback or background inference after disconnect;
- a secure arbitrary-code sandbox or stable third-party plugin ABI;
- multiplayer, cloud hosting, native macOS/Linux clients, or subscription infrastructure;
- D&D-shaped concepts in the ruleset-neutral core;
- compatibility shims that preserve filesystem/JSON campaign truth;
- implementation plans or cards before this contract is approved.

## 4. Module and dependency boundaries

The modular monolith has four inward-pointing layers:

1. **Domain**
   - campaign identity, lifecycle, writer role, revision, Campaign Plan, TURN, action, chronology, package-lock, ruleset capability, and character-artifact invariants;
   - pure ruleset-neutral types and deterministic transitions;
   - no SQLite, HTTP, desktop, provider, or filesystem dependencies.

2. **Application**
   - command admission, per-campaign serialization, expected-revision checks, action orchestration, lifecycle transitions, package installation/activation, Builder-to-DM handoff, backup/recovery, and query projections;
   - defines ports for persistence, agent sessions, MCP runtime, package validation, clocks, hashing, and streams;
   - never lets adapters mutate domain state directly.

3. **Infrastructure**
   - SQLite repositories and transactions, schema migrations, online backup, package-blob streaming, model/provider adapters, MCP adapter, generic session storage, local authentication, and readers only for explicitly supported import formats;
   - implements application ports without becoming a second authority.

4. **Interfaces**
   - local API and streaming protocol, Windows desktop client, file picker/import/export surfaces, and optional future CLI clients;
   - clients send commands and expected revisions and render query/stream projections;
   - no client opens the live database or implements mechanics.

Dependencies point inward. Domain code does not import infrastructure or interfaces. Rulesets depend only on the versioned public ruleset/runtime boundary, never on desktop/server internals.

### 4.1 Current implementation owners

The conceptual layers above remain the required dependency direction. In the retained launch
source, application/authority.py is the orchestration facade; interfaces/owner_process.py owns the
local protocol; infrastructure/sqlite_authority.py and its extracted SQLite helpers own durable
records; agent/_stateless_mcp.py projects the current engine tools; and runtime/ owns deterministic
interpretation. Provider adapters remain owner-side under provider/. These are current module
responsibilities, not a claim that the deferred AH01–06 refactors have landed.

Bundled package construction is owned by application/bundled_packages.py. Plan selection and
pre-read containment are owned by application/bundled_campaign_plans.py. The sidecar builder
stages only paths explicitly selected in src-tauri/sidecar-data-files.json; adding a source sibling
does not add it to the release. Legal notice delivery and source/installer evidence are separate
from rights clearance and adoption. The active plan records the candidate-specific status.

## 5. Owner process and local deployment

### 5.1 Desktop-supervised owner

The shipped Windows desktop launches and supervises one local Sylvia GM owner process for the current OS user. The owner process contains the modular monolith and owns `sylvia-gm.db`. It is not an independently persistent Windows service for Alpha.

The desktop:

- discovers the owner through a launch handshake rather than a fixed public port;
- authenticates local API/stream connections with an ephemeral per-launch secret delivered outside ordinary URL/log output;
- renders state returned by the owner;
- sends commands but never writes database files, package files, or campaign folders directly;
- requests graceful shutdown and follows the selected disconnect grace/cancel behavior.

A process-level ownership lock prevents a second owner from opening the same live database. A second desktop instance attaches to the existing owner or exits with a clear recovery action; it does not start another writer.

### 5.2 Storage location

The live database resides in Sylvia GM-managed per-user local application data. Network shares, OneDrive, Dropbox, and comparable synchronization folders are unsupported for the live database. Transfer uses validated exports/backups.

### 5.3 Local transport

The exact loopback protocol may use the existing local HTTP/stream stack if it satisfies this contract. It must provide:

- authenticated command and query endpoints;
- one ordered event stream per attached session/campaign projection;
- explicit connection/session identity;
- reconnect with replay from a durable canonical sequence;
- cancellation after the short disconnect grace period;
- no reliance on browser arrival order as canonical chronology.

Transport technology is replaceable; authority and reconnection semantics are not.

## 6. SQLite durability domain

### 6.1 Required conceptual records

Exact DDL, ORM, and names are implementation details, but the schema must represent these normalized concepts without competing authority:

- **Campaign registry:** one stable campaign ID and aggregate for construction and play, display
  metadata, availability lifecycle, orthogonal construction phase, current revision and writer,
  and current content-binding state.
- **Campaign construction:** durable Interview State and cursor, exact question/answer references,
  active selections, unresolved requirements, campaign customization work, readiness basis, and active
  or detached campaign-local work.
- **Campaign state:** current ruleset-neutral core state plus namespaced package-owned state validated against the active exact schemas.
- **Campaign Plans:** selected source identity, campaign-local working/Derived revisions, accepted
  live revision, and provenance. The admitted Plan is the selected source of campaign preparation.
  Campaign State records what is true now; prepared encounters and future events are not current
  facts merely because the Plan has been admitted.
- **Ruleset-specific artifacts:** Player Characters, Companions, and other package-declared
  participant artifacts tied to an exact Campaign Setting and its contained Ruleset package/schema
  context, including when that Setting is contained by a Campaign Plan.
- **TURN envelopes:** generic time/actor-group lifecycle status independent of already committed actions.
- **Actions:** stable action identity, expected/committed campaign revision, request/tool identity, status, deterministic stored result, and parent TURN.
- **Canonical segments:** append-only completed THINK, TALK, TOOL CALL, and TOOL RESULT segments with immutable chronological sequence, visibility class, action/TURN correlation, and safe structured payload.
- **Package library:** exact manifest/revision identities, dependency relationships, capabilities, schemas, provenance, validation state, and content hashes.
- **Package blobs:** immutable content-addressed bytes stored in SQLite for Alpha and streamed rather than loaded wholesale.
- **Campaign lock manifests:** zero or one primary Ruleset plus its exact typed dependency/plugin
  closure and lineage. No current manifest means an unbound Draft; an admitted empty manifest means
  the explicit No Ruleset choice.
- **Package candidates/activations:** non-authoritative validation results and explicit activation records.
- **Agent session references:** campaign association, Builder/DM role, provider/model metadata,
  resumable conversation identity, status, non-secret operational metadata, and explicit delegation
  relationships where selected. A content Builder launched from an Interview owns an immutable
  launch-context snapshot and has no parent-lifecycle dependency. Interview State references its
  canonical session messages rather than copying a competing transcript.
- **Migration/import evidence:** source identity/hash, deterministic mappings, audit notes, quarantined conflicts, and human resolutions.

Provider reasoning is not a canonical narrative-segment payload. The owner persists its separate
inspection records correlated to session, turn/response, attempt, and source order under §9.2.
These records participate in full backup; portable export follows the explicit selection in §9.2.
Ordinary logs and campaign-memory projections exclude them.

### 6.2 SQLite operation

The owner enables foreign-key enforcement and uses crash-safe transactional settings appropriate to local Windows operation. Schema migrations are versioned, deterministic, transactional where SQLite permits, and backed up before destructive transformation. Startup performs integrity, schema-version, and exact-package-closure checks before admitting campaign mutation.

Backups use SQLite's supported consistent-backup mechanism or an equivalently proven snapshot, not ad hoc copying of an open database. Because package bytes share the same durability domain, a consistent backup captures campaign truth and its locally required exact package closure together.

## 7. Command admission, writer authority, and revisions

### 7.1 One logical campaign writer

Every campaign has one campaign-changing authority:

- construction authority before play handoff;
- DM after play begins;
- neither while archived, tombstoned, purging, migrating, or otherwise quiescence-blocked.

The existing Builder writer may implement construction authority, but model capability or prose
never grants mutation permission. Within the sole owner process, a per-campaign serialized admission
lane orders mutating commands. SQLite expected-revision checks and uniqueness constraints provide
the durable fence. No model call, user wait, tool execution outside the deterministic engine,
plugin call, network operation, or file selection holds a SQLite transaction open.

### 7.2 Command envelope

Every mutating command carries:

- stable request/action identity assigned before effects;
- campaign ID;
- expected campaign revision;
- actor/session identity and authorized writer role;
- command/tool type and validated payload;
- parent TURN identity when applicable.

Admission rejects stale revision, wrong writer, incompatible lifecycle, unavailable exact pins, undeclared capability, or invalid payload before mutation. Where a model-issued TOOL CALL has entered chronology, rejection/failure/no-op is still committed with its TOOL RESULT card.

### 7.3 Idempotency

Action identity is unique within its campaign. Reusing an identity returns the exact stored outcome and resulting revision without re-running mechanics, plugins, or side effects. A payload mismatch under an existing identity fails closed as an identity collision.

## 8. TURN, mechanics action, and chronological commit protocol

### 8.1 TURN envelope

A TURN is a durable lifecycle envelope for generic time and actor grouping. It may span model/user waits and several short action transactions. It can continue, complete, fail, cancel, pause, or require recovery without rolling back or repeating actions that already committed.

Externally driven actors persist and pause; only explicit deterministic engine procedures auto-run. Positioning and typed resource refresh are activated only by package-declared capabilities/procedures.

### 8.2 E-A + G1 mechanics commit

Each admitted mechanics action executes as follows:

1. Read the campaign's current revision, writer, lifecycle, exact lock manifest, state, and parent TURN outside a long-lived write transaction.
2. Validate the dynamically advertised MCP tool/schema and prepare deterministic declarative-engine work.
3. Execute only bounded deterministic primitives over explicit inputs. No model/provider call,
   arbitrary network/file side effect, executable plugin, or other irreversible external operation
   is part of the Alpha mechanics commit.
4. Open one short SQLite write transaction.
5. Recheck campaign revision, writer, lifecycle, TURN status, lock-manifest identity, and action uniqueness.
6. Atomically commit:
   - TOOL CALL;
   - TOOL RESULT, including success, failure, rejection, or no-op;
   - mechanical mutation when any;
   - canonical consequence/chronology;
   - action stored result and resulting campaign revision.
7. Commit and publish the resulting durable sequence to attached clients.

The application never acknowledges an authoritative mechanics success before this commit. A failed transaction exposes no partial mechanical result. A committed result survives later narration failure.

Some deterministic package mechanics require an informed user choice after a roll or other
package result is known. The core represents this with a separate durable pending-mechanics
reservation, never by widening terminal Action status. The reservation binds the exact campaign,
unchanged basis revision, TURN, action, package lock, parameters, initiating session, and one
bounded package-authored public choice descriptor. It commits no Action, chronology, state
mutation, resource spend, or revision increment. While it exists, other campaign mutations,
TURN completion, package changes, and lifecycle transitions fail closed. Resolving the choice
reruns the exact deterministic package tool from its original basis and action seed, requires the
same descriptor, injects only the selected option identity, and atomically converts the reservation
into the ordinary terminal E-A + G1 Action at the next revision. Replay, reopen, backup, and
recovery preserve either the unresolved reservation or the one terminal result; a client never
serializes or resumes an interpreter stack, callable, AST, private context, or arbitrary continuation.

Ordered player intentions are orchestrated by application/action_sequences.py through the same
owner/action boundaries. They do not create a second mechanics implementation or parallel writer.
Each action revalidates current authority; committed prefixes remain durable across interruption,
and required package choices retain their pending-mechanics reservation. The selected player
journey reviews the ordered batch and uses one Execute with one closing narration. That is an
acceptance requirement; the active plan identifies any remaining combined-candidate UI/native proof.

### 8.3 Narration after mechanics

Model narration uses committed action results as immutable input. TALK streams provisionally. A
channel transition completes the prior narrative segment; only then is its safe completed payload
appended canonically in chronological order. Failure or cancellation drops incomplete narration.
Available partial provider reasoning is retained separately as interrupted inspection data (§9.2).

Manual **Narration interrupted—Retry** reuses the committed action result and starts only the missing narration continuation. It never reruns the action.

Once explicit Stop has settled, the session owner admits a new message as a redirection of the
unfinished continuation, retaining its relationship to the interrupted input and authoritative
saved results. Preserve committed action identities and required turn/choice constraints; new
guidance neither replays completed mechanics nor skips an outstanding required response. With
no committed action, new guidance may replace the unperformed direction without rewriting saved
history. Acceptance durably supersedes the previous Retry opportunity, so stale requests and
reopening cannot revive it. Optional Retry without new input retains the existing recovery path.
UI admission follows that owner state rather than blocking solely because a retry basis exists.

## 9. Event protocol, reasoning, and streams

### 9.1 Canonical chronology

Chapter announcements are read projections of committed Campaign continuity: resolved act progress
and the first saved scene entering an act. Display history attaches numeric markers to the saved
narration for the record's response basis, with stable per-act/status identity. It excludes Plan
titles, summaries and future preparation; it creates no campaign mutation or automatic transition.

Canonical conversation is an append-only ordered sequence of completed segments. Each segment records its true type and safe visibility. THINK, TALK, TOOL CALL, and TOOL RESULT are never merged across channel transitions or reconstructed from UI timing.

A provider-emitted TOOL CALL may be streamed/rendered as a provisional pending card while deterministic resolution is prepared. It becomes canonical only in the E-A + G1 transaction that also commits its TOOL RESULT, mechanical mutation when any, consequence, and stored action result. Crash/cancellation before that transaction cannot leave a canonical orphan TOOL CALL; recovery may discard the pending projection and rerun admission under the same action identity.

TOOL outcomes retain their source ordering inside the response's activity projection. GM and Builder
share the one-line expandable group defined in product §5. Grouping uses saved response/attempt
correlation, not renderer arrival timing. Only neutral operation descriptions and basic status enter
ordinary tool activity; raw arguments, results, and private identifiers stay in the Inspector trace.
Meaningful game results and Builder outputs render separately at their chronological point. This
presentation grouping does not change canonical tool transactions, action identity, or retry rules.

Ruleset result-card declarations define their own purpose, labels, initial and expanded fields,
visibility, and optional actions. The shared renderer does not require dice or fixed actor, action,
target, or outcome fields. Persist the safe resolved values and their original presentation with
the committed result so reopening history does not reinterpret it through a newer Ruleset. Missing
or invalid presentation uses a generic completed/rejected operation card without inventing gameplay
success. Card actions resolve through current owner admission; D&D SRD does not select card buttons
in this scope.

The declaration also selects the inline or compact-card footprint and emphasis defined in product
§2. These are shared render treatments, not hardcoded lists of game purposes. Optional dice and
calculation presentation uses recorded engine values; combined modifiers come from the authoritative
result rather than a competing frontend calculation. Die graphics reflect the declared die type.
Styling changes must preserve historical values, visibility, and meaning; absent old fields are not
reconstructed from current character state or a newer package.

The package runtime resolves a tool's `metadata.result_card` against explicit parameter and public
result-context paths at execution time. Its version-1 payload has optional title, initial/expanded
rows and action requests; labels and scalar/list values are Ruleset-owned. It cannot read live state,
private spatial-query variables, provenance or source documents. Malformed display data falls back
without invalidating the committed mechanic. The saved payload is the renderer's input; button
requests do not grant action authority.

Card clicks carry the saved action identity and button index. The owner reads the original button's
tool and parameters from its committed record, checks the current tool in that same package, and
uses the shared player-action path. The UI cannot replace those parameters. Current button
availability is projected separately from the saved display, so old cards retain their original
meaning while unavailable actions disable.

Direct player controls share the existing campaign action transaction and current Ruleset admission.
The owner rejects them while campaign work is active, including the interval before provider text
arrives, and rejects new GM work while a direct action resolves. Rejected clicks are not queued.
Panel projections expose current availability; the backend rechecks it when a click arrives.

The application records a direct action's origin as its own consequence in the same transaction as
the result. A Ruleset cannot emit that reserved origin marker. Pending mechanics choices retain the
origin in their saved descriptor, so resolution after restart keeps the player-control association.
An identical request returns its existing receipt; reusing its identity for different selections
rejects. The GM receives outcomes since its last completed response as labelled application data,
with private spatial-query evidence omitted. This neither fabricates provider tool-call history nor
executes the action again. Ruleset-defined response triggers and response-only retry use this saved
mechanics result.

A tool's `metadata.player_controls` chooses `confirmation: none|required` and
`response: on_request|automatic`; absent policy defaults to immediate execution and an explicit
response request. Its optional `preview` uses the shared card declaration with selected parameters,
without executing mechanics. A required confirmation binds the operation identity, selected values,
campaign revision, tool and package bytes. Execution rechecks that basis. Invalid control policy
disables direct controls while leaving the underlying chat mechanic subject to normal admission.

Optional `checks` contain bounded `{condition, reason}` entries for direct-action prerequisites.
They use the runtime's pure expression evaluator and the same parameter defaults, state bindings,
entity context and ordinary tool preconditions used by execution. Checks never invoke effects,
roll dice, reserve an action or expose their working context. Reasons are literal player-facing
text; an invalid check fails closed with a generic explanation. The owner repeats these checks
before direct execution, independently of the mechanic's final guards. Panel checks are batched
for current selections and revision; late replies cannot enable a different selection or session.
Saved card buttons refresh their availability and reasons separately from the saved card's display.
Optional `queries` obtain physical facts from current package-owned scenes before checking those
conditions. Each entry uses the ordinary `spatial_query` body (`scene`, `operation`, `query`) and a
private `store_as` name. Optional `calculations` use local scalar `set` expressions with an optional
condition; they cannot address campaign fields. These bounded reads reuse spatial query admission
and pure expression evaluation, bypassing effect dispatch entirely. They let a Ruleset share its
route and AP formula with committed movement. Full-scene eligibility checks expose only the literal
reason, never their query results, calculated context or private scene. The availability response
may also include the separately projected player-visible preview described below.

The optional `actor_parameter` names the tool argument identifying the acting character. Panel
bindings to `actor` supply it when tool metadata omits it. Party panels can bind one such argument
to a visible member; fixed Party actions retain fixed parameters. The existing Ruleset decision
declaration supplies the player-control scope (which actors the player may act through now). Its
UI projection exposes actor identities and names, never the GM's actor state. Direct execution,
saved card buttons and panel availability use that scope. Map actor selection includes only
controlled visible actors; target selection can include other visible actors. This admission does
not replace the mechanic's action-specific costs and conditions or authoritative spatial checks.

An action-triggered response is a saved `player_action_response` application event in the originating
GM session. It contains the saved action identity, not fabricated player speech. The existing session
loop owns provider selection, streaming, cancellation and retry. It starts with the committed action
as its recovery basis and supplies a current task that does not reactivate earlier player text.
Repeated response requests do not start another generation for that action; interruptions use the
existing response retry. Required GM transitions and Campaign File updates remain in the same loop.
Navigation does not redirect the response or consume either session's unsent text.

Player turns retain separate IC and OOC fields in one USER session record, with one turn and input
identity. The provider receives both explicitly labelled parts from that record; retry preserves
their association. OOC is not an application control or fictional Player Character speech.

Ordered narration, NPC dialogue and OOC reply blocks use completed TALK events with typed safe
payloads. They pass through the same held-response and Campaign File disposition interface as prose,
and release only after successful disposition. Host-side campaign knowledge resolves speaker labels;
model-provided hidden names, image paths and arbitrary markup are not presentation authority.
Canonical ordering and readable labelled text survive reopen and export without a second store.
Current response-format instructions accompany the current tool interface even when a resumed
session retains an older GM operating contract. This does not rewrite that stored contract or history.

The durable interruption projection owns the single visible notice. Its saved-result claim comes
from actual committed actions and Campaign File records tied to the interrupted player input,
not prose completion, failed actions, earlier inputs in the same engine turn, or failure wording.
Session terminal evidence may enrich that notice but must not render another copy.

### 9.2 Reasoning sinks

The owner classifies provider output at ingress:

- **provider reasoning for inspection:** persist actual readable provider output with session,
  turn/response, attempt, source ordering, and completion/interruption identity. It is independent
  of Full Audit capture. A dedicated inspection read exposes it only when the user opens Thinking;
  the ordinary activity/history projection includes availability and status, never a text preview.
  No supplied readable reasoning means no Thinking entry; opaque protocol data is not display text.
  Existing safe THINK records keep their identity but do not justify creating substitute reasoning;
- **provider-required continuation:** preserve native reasoning fields, signatures, or opaque
  continuation data unchanged where the selected provider requires them. The provider adapter
  attaches only the data required for the exchanges actually continued, including across user turns
  or reopen where required. Inspection text is not copied into ordinary assistant prose, sent to a
  different provider as generic history, or supplied as source prose for campaign memory/compaction. Required
  continuation contributes to request accounting; there is no universal zero-reasoning-input claim;
- **saved reasoning sinks:** full backups include inspection records; normal campaign exports omit
  them unless the user explicitly includes thinking. Ordinary logs and canonical campaign memory
  exclude them. Clearing or disabling Full Audit clears its own trace store, not saved Thinking.
  Partial reasoning retained after interruption is labelled as such and is never promoted to a
  completed narrative or an authoritative game fact;
- **isolated Ruleset participants:** an explicit package opt-in creates separate actor-owned
  inference histories. The GM retains its campaign and narration history. Participant requests
  start from the declared observation/persona/action envelope, never a filtered GM transcript.
  Native fields and private compaction notes remain with the campaign, session, stable actor,
  exact package and compatible provider/model context. Revisions fence proposals, not history
  lifetime. Missing private continuation starts a fresh actor-only context. Readable participant
  reasoning uses the same private inspection sink; signatures and opaque data are not previews
  or summary prose. Only the engine's committed published result enters GM history. Cancellation
  before commit discards a proposal; recovery after commit reads its receipt without replay;
- **TALK:** provisionally streamed, canonically stored only on completion;
- **Admin Full Audit trace:** off by default during ordinary use and required during testing; it
  mirrors each complete provider request, source-event/role provenance, exact tool surface and
  options, the canonical serialization and SHA-256 of the neutral application request and
  provider-client request representation, response chunks, provider-reported usage, private
  reasoning, and tool-call deltas into a separate in-memory owner-process store. The testing request
  authorizes enabling the trace, inspecting records from the selected test, and copying or locally
  exporting them when useful for that test without a second approval. The captured layers do not
  claim SDK-internal, HTTP, compression, TLS, or network bytes.

#### 9.2.1 “What Sylvia would actually send”

For model evaluation, debugging, and support decisions, **what Sylvia would actually send** means
the complete `provider_wire_request.request` built by the selected production provider transport
for one real GM inference step, immediately before Sylvia calls that provider SDK. It is not the GM
system prompt alone, an application-level request viewed before transport conversion, an MCP tool
list viewed in isolation, a hand-written approximation, or a request whose player text or expected
answer was edited after generation.

The request must be generated as one coherent artifact from the same live session step. It includes:

- the selected provider's wire model identity;
- the complete ordered model-visible conversation after the stable Sylvia contract, optional
  package-owned Ruleset operating guide, stable/semi-static campaign explainers, complete
  chronological history while it fits, fresh authoritative state, current player-intent fencing,
  and any current decision or narration control have been assembled;
- the exact model tool surface projected from that inference step's fresh MCP discovery, including
  provider aliases, descriptions, and model-visible schemas;
- the provider options that affect the inference, including streaming and output budget; and
- transport-owned conversion. OpenAI-compatible Chat Completions preserves Sylvia's message objects
  and tool definitions, adds the wire model/stream fields and streamed-usage request, and passes the
  result to `chat.completions.create`. Other selected transports may represent the same neutral input
  differently—for example, Anthropic separates system content and converts function definitions to
  Anthropic tools—so their provider-wire request, not an OpenAI-shaped surrogate, is the comparison
  boundary.

Provider tool decoding accepts the exact advertised alias. It also accepts an exact underlying
operation name only when that name identifies one and only one tool on the current advertised
surface, then normalizes it to that alias. Ambiguous duplicates, stale aliases, fuzzy names, and
operations absent from the current surface remain rejected; this is wire compatibility, not host
selection of a mechanic.

For a genuine current player action, the first inference step retains the exact player text as a
`user` message and places the application-owned `SYLVIA_GM_ACTION_SCOPE` beside it. Sylvia
does not stem, classify, alias-match, or otherwise assign mechanical meaning to that prose before
the GM model sees it. The Ruleset and current authoritative state determine the optional MCP
surface; the model decides whether the present fiction and intent call for one of those operations.
After a commit, the owner executes Ruleset-declared non-choice lifecycle transitions itself,
rediscovering MCP after every commit, until it reaches a Player or GM decision boundary. A Player
boundary waits for genuine player input. A GM boundary sends `SYLVIA_GM_DECISION_REQUEST` with the
controlled actor and current state, and the model chooses among the currently legal actions.

The Campaign File update is the GM's final state-changing tool action for a decided response.
The same GM retains one continuous conversation containing the exact current player action,
labelled source context, its selected reference and Ruleset calls, their real results and fresh
Campaign State. It may use available tools for as many useful rounds as needed, with no total-round
cutoff. Cancellation is checked before discovery and automatic transitions as well as during model
streaming. Audit round numbers count model requests, not engine bookkeeping. The selected model's
output-token setting reaches the provider unchanged and is reserved in context-fit admission; the
GM adapter must not clamp it to a separate response allowance. Bounded correction of invalid calls
and consecutive empty completions still apply. There is no forced first read, host-selected mechanic,
mandatory preparation form, preparation identity or separate composition-only inference.

When ready, the GM uses the existing final Campaign File interface to submit durable note changes,
or an unchanged disposition, together with one complete player-facing response. The adapter
validates and separates provider-only response data from the durable payload. The existing
authority owner checks structure, identities, writer, revision and other campaign invariants and
commits the update through its single writer. The adapter releases the exact response only after
successful disposition. Ruleset results already committed remain immutable input; this note
update cannot replace an engine-owned outcome.

The same Campaign File interface accepts structured character bookkeeping separately from narrative
attributes. A Ruleset may declare one internal mechanic using the input-schema annotation
`x-sylvia-gm-character-update` (its owned entity-state namespace/key) and an `updates` array of
participant identities and explicitly permitted `fields`. This mechanic is not a separate
model-facing tool. The shared character reference exposes current values, editable schema and
revision. The owner routes the update through normal Ruleset admission, execution, mutation
ownership, action identity and replay checks. Related continuity notes join that action's database
transaction; neither side commits on rejection. Reusable Character Files remain starting data.
This is ordinary bookkeeping, not a route around engine-owned combat or protected fields.

Inventory has one active representation in Campaign State: a declared Ruleset inventory when
present, otherwise rules-neutral Fallback Inventory. Inventory-system selection is explicit, not
inferred from a panel's label or a prose attribute named inventory. The shared Campaign File
query/update interface exposes the active representation and supported schema. Fallback writes
use the existing owner, writer/revision checks, idempotency and atomic continuity transaction;
they do not manufacture a Ruleset mechanic or bypass Ruleset-owned fields. They preserve people
and object identity, owner separately from holder/location, stack quantities, distinct items and
named currency quantities. A partial transfer conserves quantity and changes ownership only when
explicitly requested. Do not guess exchange rates or migrate legacy prose into authoritative
holdings. Existing Ruleset inventory is not mirrored into fallback records.

A Ruleset whose inventory changes through ordinary mechanics may explicitly bind inventory to
its owned state, existing presentation surface and supported GM tools. This selection uses the
same package admission and action executor; it grants no generic bookkeeping access to protected
equipment state. The shared inventory reference exposes the binding and tool names, and rejects
fallback ledger writes while that inventory is active.

The owner supplies a player-safe inventory projection for the generic Inventory panel, using
the active inventory representation. Ruleset inventory bindings provide that projection when
there is no specialised panel. The frontend does not maintain balances or inspect private raw
entity attributes. The generic panel uses the existing Game Panel host and campaign layout;
absence of a Ruleset does not remove this rules-neutral host capability. Specialised inventory
presentation replaces the generic one without creating a second inventory authority.

The implementation must not replace the retired preparation/composition flow with an ordered
reasoning checklist inside the final object. Field order, model self-attestation and JSON validity
are not semantic proof. The model still owns GM adjudication and useful prose; the application
validates the real tool and persistence interface. A final unchanged disposition remains valid and
does not require a fabricated Plan Progress or Campaign State change.

For an otherwise complete response whose Campaign File fields require structural correction,
Sylvia keeps the exact decoded response and asks the same GM to repair the rejected state fields.
The correction surface keeps the complete-response field optional: omission reuses the held response,
and an explicit complete replacement updates it. Bookkeeping does not require another prose copy.
Report privacy-safe paths, problem kinds, expected and actual JSON types,
direct parents and relevant schema constraints without echoing private submitted values. Do not
carry preparation-only exact-copy or sentinel constraints into the simplified interface.

If a round explicitly requires a tool call and the provider returns only prose, that prose is not
released, persisted as canonical narration or adopted as the decided outcome. The correction
retains the exact unresolved player action and every still-legal reference, Ruleset and Campaign
File option; it must not narrow the next round to Campaign File completion merely because prose
was received. Report the ignored required call precisely. A direct final Campaign File call remains
a valid no-mechanic choice. Required-call compliance does not prove that the GM selected the right
mechanic; the Ruleset guides that judgment and real-play evidence evaluates it.

Malformed or incomplete provider calls receive bounded, explicit correction. An empty completion
uses the existing consecutive-empty allowance: substantive narration or tool assembly, including
an assembly requiring structural correction, ends that empty sequence. Successful tool work does
not consume a correction allowance. Application correction reports the actual failure to the same GM and does
not score, rewrite or automatically select prose. Rejected drafts are not current campaign truth.

Interruption and retry retain committed mechanics and their authoritative results; the GM does
not roll or spend them again merely because prose or note completion failed. Existing recovery
may complete missing narration from committed outcomes, but it does not create a mandatory second
narration pass for ordinary turns. Keep canonical history, private reasoning channels, Full Audit,
single-writer ownership and exact-response release at their established interfaces.

A suitability result may be attributed to a model only when the counted request passes all of these
coherence checks:

1. it was generated through the production session, MCP, provider-adapter, and selected transport
   path for the exact tested player text—not repaired by post-generation text substitution;
2. its correlation identifies the fresh MCP discovery used for that inference;
3. a mechanic-decision request contains the current action identity and text plus the authoritative
   actor/target/state needed for the decision; a narration-only correction instead contains its exact
   correction marker and immutable committed facts, while neither form substitutes a stale action as
   current;
4. its tool surface, final application-owned control, schemas, model identity, and sampling options
   agree with that same inference step; and
5. the recorded response is the response to that exact request serialization.

If one of those checks fails, the observation is a harness or test-fixture failure until corrected;
it is not evidence that the model cannot perform the task. Passing the input checks does not certify
the model: the model must still choose a legal call, allow the engine to commit it, follow every
fresh lifecycle continuation, and narrate only the authoritative result.

PES-82 exception selected by Wan on 2026-09-11: for the managed ChatGPT subscription
connection, capture all available Sylvia-to-Codex requests, returned events, tool exchanges,
model identity and reported usage. Label any unavailable downstream model-request details;
do not present runtime-boundary input as a complete OpenAI wire capture. This does not relax
credential privacy, saved-history ownership or the existing direct-HTTP audit contract.

Admin Full Audit is the runtime inspection surface for this boundary. Its
`application_provider_request` record preserves the neutral `messages`, `tools`, and options received
by the provider wrapper. Its correlated `provider_specific_wire_request` preserves the exact request
object handed to the selected provider SDK plus canonical serialization bytes and SHA-256. These
records deliberately do not claim to capture SDK-internal serialization, HTTP framing, compression,
TLS, server-side chat-template expansion, or network bytes.

Testing enables Full Audit before the first tested action and keeps it enabled for the run. While
Full Audit is enabled, the desktop may open a separate read-only live inspector.
It reads one exact memory-only record at a time, preserves chronology, labels private reasoning,
supports search, collapsible exact JSON, pause, and automatic follow, and cannot replay or mutate a
campaign. Closing the window does not change capture. Disabling Full Audit immediately clears both
the owner store and the inspector projection.

Disabling Full Audit or closing/restarting the owner destroys its traces. An explicit local Audit
file export and explicit clipboard copying of visible records remain separate from campaign export
and backup. The Audit surface shows a passive spoilers notice; enabling, viewing, copying and
exporting require no extra warning acknowledgement. File export retains its normal save chooser.
Credentials, authentication/private headers, URL-embedded secrets, and environment
values are never captured. Token values identify provider-reported, authoritative-tokenizer,
estimated, or unavailable provenance. Canonical recovery and ordinary rendering never depend on
this store. Providers without reasoning output remain valid.

### 9.3 Reconnect and cancellation

Canonical stream messages carry durable sequence identifiers so reconnect can replay completed segments. Provisional tokens carry ephemeral stream/segment identity and are never replayed as canonical after failure.

On desktop disconnect, the owner allows a short configurable implementation-level grace interval. If no authorized client reconnects, it cancels unfinished inference, discards incomplete prose, preserves committed mechanics, and records/serves the manual Retry state. It does not continue inference invisibly.

## 10. Ruleset-neutral runtime and MCP

### 10.1 Neutral core

The core knows only generic concepts such as identity, namespaced validated state, procedures, effects, capabilities, TURN timing/actor grouping, resources, modes, events, and package lineage. It has no universal HP, armor class, ability, class, Companion, combat, distance, relationship, school-day, or TTRPG requirement.

A campaign may declare no ruleset. That is represented directly by the absence of a primary ruleset and an empty ruleset/package closure—not by a synthetic `none@version` package. It remains playable through model conversation plus campaign persistence, exposes no mechanics capabilities or tools, and must not inherit D&D or generic-fantasy prompts or surfaces.

A synthetic non-D&D fixture must prove that package-sensitive prompts, prose authority, MCP tools, state, consequences, and global UI remain free of undeclared D&D/TTRPG assumptions—not merely that schemas parse.

Executable package admission must also accept a Ruleset with no ability/stat layer, no inherited fixed feature-toggle object, and independent skills with no ability relationship. Ability/stat declarations, when present, do not receive implicit generation, modifier, save, or usage mechanics from the shared schema. Package-owned data and declared capabilities define those concepts.

The declarative runtime distinguishes **neutral primitives**—arbitrary namespaced state reads/writes, set/increment/decrement, dice, branching, procedures, tables, conditions, and timing—from **compatibility conveniences** such as HP damage/healing, ability damage, D20 saving throws, hunger dice, and inherited D&D/Cairn feature metadata. Compatibility conveniences may remain callable for packages that select them, but they are not mandatory, globally advertised as Sylvia GM's game model, or injected into Builder designs. No replacement universal RPG/harm ontology is introduced without demonstrated need.

A **mechanic family** is compact package-authoring reuse over those primitives, not a new universal
host ontology. One family may expand into several configured public actions and private mechanics.
Each parameter declares its authority as a model choice, bounded GM judgment, or an exact
Ruleset-owned binding in Campaign State. The compiler omits state-owned inputs from the model schema, retains choice/judgment inputs,
and emits the ordinary admitted runtime artifacts. A trigger-bound reaction first commits one
immutable pending trigger containing the exact reacting/provoking identities and package-selected
facts; later resolution derives those identities from state and cannot substitute another target.

### 10.1.1 Ruleset-neutral spatial engine

Sylvia core owns one built-in, deterministic spatial engine for Rulesets that select coordinate play.
It provides validated coordinate-map state, configurable grid scale, path and zone queries, LINE OF
SIGHT and exposure queries, and ordered ray intersections against the complete authoritative scene.
SRD selects full coordinate combat and map display for launch under Wan's 2026-10-11 selection.
Precise positioning matters in combat; exploration and ordinary roleplay do not require map movement.
Combat movement, range, targeting and spell areas use the same owner-authoritative scene, positions and
body geometry. The map projects that scene; UI previews and model assertions cannot establish a
different authoritative position or affected-target set. Preserve existing SRD rules and approved
spell adaptations. Coordinate positions govern targeting and map display. Trigger Discipline retains its grid and
consumes this shared capability. TD and Siren Protocol remain later work and not Alpha blockers;
their 2026-09-23 pre-release selection is superseded. These selections do not change the shared
engine's ownership.

The spatial engine is a neutral primitive, not a firearm or Trigger Discipline implementation. A
Ruleset declares the map, entity volumes or silhouettes, stance/height inputs, blockers, cover and
other intersectable geometry, material metadata, dynamic geometry state, and the package mechanics
that interpret spatial results. Core returns deterministic physical facts such as an ordered list of
intersections, exposed geometry, traversable path, crossed zone, or immutable geometry snapshot. It
does not decide ATTACK ROLL, COVER INTERCEPT, MATERIAL PENETRATION, DAMAGE, RES, or any other
Ruleset-owned consequence.

Ray queries use stable sub-cell origins and destinations, a versioned deterministic grid traversal,
and explicit tie-breaking where a ray crosses a corner or meets simultaneous surfaces. Bodies,
doors, barriers, shields, furniture, and other package-declared physical objects participate through
the same intersectable-geometry contract. Queries read the owner-authoritative scene rather than a
model-supplied list of alleged obstructions. Dynamic geometry changes affect the next query
immediately; a Ruleset may explicitly request an immutable geometry snapshot for one simultaneous
resolution such as an explosion.

A path query may name one `moving_entity_id`. Admission requires one active entity with that exact
identity on the queried map and excludes only its current footprint from collision; every other
entity and ordinary blocker remains in force. The identity is part of canonical query and replay
evidence. Omitting it preserves ordinary entity collision.

The declarative `spatial_mutation` effect changes one exact geometry or entity object through a
closed, kind-specific patch. It requires the map identity and expected revision, rejects stale,
missing, duplicate, cross-map, or open-field targets before mutation, and atomically writes the new
scene revision with before-and-after spatial evidence. A later live snapshot sees that revision;
an earlier immutable snapshot remains unchanged. AP, damage, penetration, hacking, and other
consequences remain Ruleset-owned.

The host tactical-grid renderer owns selection, zoom, and bounded viewport panning. Panning is
presentation state: it dispatches no package action, changes no campaign state, and enters no
replay history. Coordinate hit-testing and accessible selection use the transformed viewport.
Declared `zone` and `device` selections bind only identities present in the current public map
projection, at its current revision. A device selection does not grant control or permission;
the package tool checks the acting character, ownership and capability through ordinary admission.

Packages consume the spatial engine only through admitted declarative runtime effects and validated
UI intents. They cannot include an executable private raycaster. A committed spatial resolution
retains enough replay provenance to reproduce the result, including the spatial algorithm version,
map and geometry revision or snapshot identity, query inputs, ordered intersections, and resulting
state mutations.

Map geometry distinguishes a barrier along a grid edge from an occupied cell footprint. An object
may declare both. Traversal checks crossed edges and actual clearance, including diagonals through
sealed corners. Door state selects map-declared geometry: a hinged open panel may still occupy
space, while a retracted door may leave no barrier. Numeric physical heights remain authoritative;
Rulesets may supply named presets. Current TD play uses one-meter cells and same-level traversal.
Character height and stance still affect queries; traversable floors, stairs, and rooftops are
outside this selection.

Scene format 1 geometry uses `occupies_cells` to distinguish those two movement treatments.
Omission or `true` reserves every grid cell overlapped by the active blocking object; `false`
keeps only its authored physical box as a barrier. Both retain the same physical volume for sight,
exposure and projectile queries. A path naming a moving entity checks its entire box or circular
footprint throughout each translation, including clearance at corners and map boundaries. Exact
contact without overlap is permitted. The spatial algorithm identity is
`sylvia.spatial.supercover.v2`; older saved results retain their original provenance.
Visible geometry may include Ruleset-authored `metadata.player.height_label` and `material_label`
strings under the existing bounded public display rules. Material labels describe public facts;
the host never derives them from private material metadata or interprets Ruleset resistance values.
The inspector shows physical height and footprint dimensions alongside that label (or explicitly
unspecified material). The renderer separately shades occupied cells; retracted/inactive objects leave no
physical shape or occupied-cell shading. This does not define metric defaults for TD's named cover
categories.

Movement and fire previews query the same authoritative geometry used for execution. Their visible
projection respects player knowledge, explains relevant cover and height, and is revalidated when
committed. Selection and previews never spend resources or authorize stale actions.

A Ruleset's `player_controls.map_preview` selects its declared path, ray, exposure or crossed-zone
query results by private variable name. The preview runs those same query declarations and scalar
calculations against a knowledge-limited view of the current scene: exactly the objects admitted
by the map renderer's public display boundary, with private metadata removed. The complete scene
still governs eligibility and commitment. A hidden obstacle must not create a visible detour,
contact marker, cover identifier or changed exposure estimate in advance. Every map preview carries
the same visible-map estimate notice, regardless of whether hidden objects exist. Preview cards
may read the declared scalar calculations; raw spatial results and snapshot identifiers remain
private. Owner-derived path parameters are rebound to the known scene for display only.

Map preview drawings include only physical route points, ray endpoints and contacts, fractional
silhouette exposure, and known zone crossings. Exposure is not a hit probability. The Ruleset owns
AP and other mechanical interpretations. Both selection checks and confirmation previews are
read-only; a preview may remain visible alongside an unavailable-action explanation.

Resolved audiovisual effects consume a separate player-safe projection of committed action results.
The Ruleset requests supported tracer, impact, penetration, and optional sound cues; private spatial
evidence is not sent directly to the renderer. Stable action and cue identities prevent duplicate
playback. TD burst cues represent individual rounds at the weapon's declared cyclic cadence. A hit
ends at its resolved character or cover contact; penetration uses ordered resolved contacts. A miss
is a cosmetic tracer passing just beside its intended target and fading before scenery behind it,
without a secondary collision or new mechanical result. Playback is skippable and nonblocking,
subject to ordinary action gates, with separate effects volume/mute, reduced-motion static cues,
and effects-off controls. Leaving the campaign stops cues and audio without a backlog on return.
Prelaunch replay controls are not selected.

The declarative `shot_effect` effect captures a source, visible entity or zone target, origin,
destination, timing, ordered contact cues and bounded style against an in-action spatial snapshot.
It does not roll dice or change state. Invalid or unavailable presentation cannot undo the action.
The saved projection omits private identities and metadata; copied spatial query objects and contact
lists also remain excluded from ordinary result context. Individual scalar results can still feed
Ruleset-defined cards. Copies made before the first scene query retain the same exclusion when the
action finishes. Zone-directed fire can fade without asserting a character hit or impact.

The main window owns playback and audio. Extra panel windows receive the active timeline and draw
its current position, preserving start times through layout moves. A newly opened campaign view
starts at the current revision; later panel requests fetch effects since that revision. Supported
sound declarations use bounded noise or tone synthesis with Ruleset-selected frequency, envelope
and volume. They contain no executable code, remote URL or arbitrary filesystem path.

### 10.2 MCP authority

For DM/LLM interactions, every ruleset or engine action goes through the MCP boundary. Tool
availability has one ownership chain:

1. The exact locked Ruleset package declares each mechanic, its schema, and its availability
   predicates over generic authoritative state, TURN status, writer/lifecycle, and package-defined
   modes or phases.
2. The owner-side engine evaluates those declarations against a fresh canonical campaign snapshot.
3. MCP `list_tools` exposes exactly that engine-calculated surface.
4. When discovery contains one package-declared non-choice engine transition with no model inputs,
   the owner commits it through the normal action/MCP authority path without invoking the provider,
   then rediscovers from fresh state. Otherwise the adapter faithfully projects the discovered
   optional surface to the model without guessed companion tools or prompt-authored phase policy.
5. At a Player decision boundary, continuation stops for genuine player input. At a GM decision
   boundary, the GM chooses among currently exposed mechanics using the named controlled actor,
   Campaign Plan, current scene, and permitted situational judgment. Every model-selected call is
   revalidated against declaration, schema, and canonical state before execution.

The application derives the engine-calculated surface from:

- active exact primary/dependency/plugin lock manifest when a ruleset is present;
- declared namespaced capabilities;
- each tool's Ruleset-declared availability predicates;
- current campaign state and package-defined TURN/mode/phase;
- writer/lifecycle permissions.

An application **TURN** remains the durable envelope defined in §8. A **GM inference step** is one
provider request within that envelope, including a continuation after a TOOL RESULT. MCP discovery
runs from fresh authority before every GM inference step. After a committed mechanic, the next step
must rediscover tools because encounter, active actor, action budget, or another Ruleset-defined
mode may have changed. Rejected, awaiting-choice, cancelled, and non-mutating results follow their
explicit state semantics and do not masquerade as a successful transition.

Tool discovery is independent of the wording of player dialogue. Campaign Plan prose and scene
intent determine whether the GM should call an available tool; they do not make an unavailable
mechanic legal. A package may represent a fictionally meaningful prerequisite as authoritative
namespaced state when its Ruleset needs that prerequisite to affect availability.

Non-choice transition metadata is part of the Ruleset declaration and engine-calculated MCP
discovery, not a provider name list. It may be static for an operation or activated by
package-authored state predicates. The owner accepts exactly one parameterless transition at a time,
commits it through ordinary authority, and rediscovers after the commit. It never asks the model to
echo that bookkeeping through native required-tool settings. Generic core code does not know that a
named tool is combat, initiative, damage, or any other D&D concept.

Rulesets may also mark individual runtime parameters as sourced from a field in their own
authoritative `state` or `combat` root. Admission validates the source declaration. The engine omits
such parameters from the declared MCP input schema, reads them from the same current snapshot used
for action validation, and overwrites no value supplied by the model because the model cannot supply
one. This is for non-choice state such as the exact current initiative actor; situational choices
remain ordinary schema inputs. The binding mechanism is package-neutral and generic core code does
not name SRD operations or fields.

The model cannot use a second mechanics path. Always-on non-mechanical file reading may remain outside MCP but cannot mutate Campaign State or Ruleset-owned mechanical values.

UI commands may call the same application command handlers directly through the local API; they do not create an alternative mechanics implementation. Any UI-triggered mechanics command is validated against the same active capability/schema and committed through the same action protocol.

By the `alpha-10a` closeout gate, this boundary implements the final **MCP 2026-07-28 stateless protocol core** through one unified model-facing admission and dispatch path. The implementation explicitly pins a compatible Tier-1 SDK and final protocol revision; request-bound version negotiation rejects unsupported revisions clearly. Existing long-lived wrappers, a currently installed pre-2026 SDK, or a renamed session object are not compliance evidence.

Each list/call request carries only stable lookup and concurrency identities needed to resolve authority, such as campaign/session identity, expected revision, TURN/action/idempotency identity, and exact package-lock identity where applicable. These values locate canonical data but never assert mechanics truth. The owner/application boundary re-resolves and validates the canonical campaign snapshot, active lock, capabilities, each declaration's availability predicates, TURN/mode/phase, lifecycle, writer permissions, and idempotency state for every request.

No MCP transport object or sticky session identifier may own campaign state, conversational state, TURN state, tool availability, permissions, retries, idempotency, or mechanics results. Fresh handlers and independent processes are behaviorally equivalent for the same canonical snapshot. They may alternate requests or restart between tool discovery and invocation without affinity; if canonical state changes, the later request deterministically revalidates rather than using cached registry/mode state.

Transport statelessness does not make Sylvia GM's campaign or conversation ephemeral. Durable state remains owned by the sole SQLite writer and projected to models from canonical records. Success, failure, rejection, and no-op continue through the same E-A+G1 action protocol. Replay after disconnect returns the already committed result or a clear stale/idempotency-collision rejection and cannot create a second mechanics transaction.

Acceptance includes fresh-instance tool discovery without prior MCP session state; alternating-instance list/call behavior; restart and canonical-change revalidation; absence of required `MCP-Session-Id`-style affinity; concurrency/replay preservation of owner-only writes, STOP/interruption, expected revision, idempotency, and chronology; exact zero-tool no-rules behavior; state-varying package-derived D&D and non-D&D surfaces; rediscovery after every mechanics result before model continuation; provider requests that exactly match MCP discovery regardless of player wording; and a packaged Windows/Tauri smoke from player intent through LLM discovery/call, owner commit, visible chronological result, and save/reopen/resume.

### 10.3 D&D and declarative-neutrality sequencing

The selected launch content path for the neutral runtime is SRD Lite, based on the SRD 5.2.1
core and 2024 rules: a reduced spell list of 117 supported spells, without consumables. Fixed
mechanics for that offered roster remain required under FC-14. Consumables and the wider spell
suite belong to future SRD Full, preserved separately and inactive. Structured spell and actor
data drive reusable
package checks, saves, attacks, damage, healing, resources, conditions, timing and selected
distance mechanics, including later effects and saved consequences. The GM selects intent and
supplies situational facts; fixed resolution and committed state remain engine authority.

Missing fixed clauses are implementation gaps, not an automatic GM fallback. Genuinely open-ended
fictional adjudication remains with the GM while fixed rules and mechanical consequences remain
engine-enforced. This does not promise deterministic interpretation of every fictional possibility.
Package mechanics must match the pinned corpus; shared families and declarative composition avoid
requiring a bespoke resolver or end-to-end certification journey per spell.

Alpha does not require a bundled original ruleset. Neutrality is instead protected by the genuine
no-ruleset path and the immutable ability-free declarative conformance package, which proves
non-D20 mechanics without adding genre, stats, resources, timing, combat, relationship, or content
assumptions to the core. Any future bundled original ruleset remains package-owned and requires a
directly approved, promoted PRD before implementation.

Every admitted gameplay package carries structured support records separate from progress labels.
The accepted resolution values distinguish reusable engine families, specialized engine resolvers,
hybrid deterministic/GM boundaries, bounded GM adjudication, unsupported behavior, and material for
which gameplay resolution is not applicable. Hybrid records name both deterministic and GM-owned
parts. Admission rejects contradictory claims such as excluded reference material represented as
GM-adjudicated gameplay. A GM or hybrid label does not excuse an absent fixed SRD spell effect;
the product contract section 3.2 owns that requirement for the offered 117-spell Lite roster.
The inactive Full archive does not expand launch scope. Full Audit logging is independent of
the Full Ruleset.

### 10.4 Model capability guidance

Every ruleset manifest declares one minimum recommended Sylvia GM capability tier. The owner compares that requirement with the selected model's demonstrated registry placement and returns a warning when the model is below the recommendation; the player may override and continue. It is never a hard provider/model gate. The registry supports at least ten ordered tiers and does not derive placement from provider, price, parameter count, or marketing labels. Engineering may define tier labels, benchmarks, and model placement through empirical work. Production models may remain unrated for Alpha; unrated produces a clear warning and uses the same explicit override. The quoted provisional “Gemma E4B” anchor has no authority until its exact identity is verified.

### 10.5 Declarative player panel host

Ruleset UI is package data. Each UI surface is hash-bound into the exact package manifest and
declares a stable identity, title, icon, bounded built-in renderer, owner-projection query, order,
optional tab group, default and minimum size, allowed placements, and optional engine intents. The
Alpha package format admits only closed declarative sources over canonical state, participant
artifacts, or bounded compositions of those sources. Player-facing surfaces cannot query the raw
Campaign Plan. A future objective surface must read a separate canonical player-visible objective
projection whose reveal state is owned by the application. Package UI does not execute package HTML
or JavaScript.

The application owner resolves each source against the current exact package lock and canonical
campaign records. The renderer receives that safe projection and never queries the model, derives
mechanics truth, or persists competing gameplay state. Any declared panel action must enter the
same local application command and package-tool authority used by other UI mechanics; it cannot
bypass capability, writer, TURN, revision, or idempotency admission.

Application navigation, sidebar visibility and Settings are host-owned and follow the contextual
shell in the product contract. Collapsing a sidebar changes presentation, not session lifecycle or
panel ownership. The existing resizable Ruleset panel host supports reorder, hide/show, tab groups,
and multiple extra native windows, each supporting tab groups and resizable rows or columns. A panel has one
placement and moves through drag-and-drop or explicit menu commands. All windows use the same
owner projections and action gates.

The layout starts with Ruleset defaults, then a user-saved Ruleset layout, then campaign overrides.
An optional primary-package artifact, `ui/panel-layout.json`, declares the starting arrangement
using closed version-1 data: a sidebar tree, named window trees, and hidden panel identities. Trees
contain tab groups or split rows/columns with bounded ratios. Admission requires
`panel_workspaces.v1`, validates exact declared panel identities and allowed placements, and rejects
duplicate panels, unknown fields, and executable content. Packages without this artifact retain
their existing per-panel grouping and default-placement declarations. Compact Builder source can
declare `ui_surfaces` and `panel_layout`; compilation supplies presentation defaults and emits the
ordinary manifest and layout artifact. The campaign projection reads the exact installed artifact.
Edits save to the current campaign; saving a Ruleset default is explicit. Resetting to the user's
Ruleset layout or the Ruleset's original affects only that campaign. Persist map camera and active
tabs per campaign/map scene, restoring current authoritative state without unfinished commands.
Explicit adoption of a new Ruleset version places newly introduced panels in the sidebar while
preserving existing placements.

Extra windows follow the selected session. Switching to another campaign or Builder closes the old
campaign's windows and restores the new session's layout; returning restores the old arrangement.
Session switching and main-window closure preserve layouts. Manual closure of an extra window
redocks its panels and saves that change. Missing monitors use accessible fallback placement while
retaining preferred coordinates. Relevant panels share actor/target selection without dispatching
an action or automatically moving the camera.

TD/Siren Protocol's retained later design (not an Alpha blocker) uses these declarative surfaces,
layout trees, selection and owner-command paths. It does not introduce package-executable UI or a second game
state. Panel chrome and tactical presentation inherit the host's current theme tokens in the main
window and extra windows, including after theme changes; no package-owned fixed green palette or
independent theme setting is introduced. Numeric geometry remains canonical; Full/Half/Quarter
labels and cosmetic fill opacity are presentation, not replacement collision/visibility semantics.

The selected Network panel uses the existing bounded network projection and command paths.
Package-owned authored topology/device bindings, discovered knowledge, routes, sessions,
privileges and defender state remain distinct canonical concerns; their exact TD mechanics are
not core assumptions. Player projections include only permitted discoveries and observable
security facts. Remote functions resolve authored physical bindings and mutate the same state
consumed by map, movement and fire; renderers neither infer privileges nor keep a second device
state. Stable authored identities replace test-scene constants. Node selection is presentation;
explicit Locate on map may focus its bound object. Actions revalidate current route, actor,
privilege and resources through the owner, including after disconnection or defender action.

Chat, map, Party, and result-card actions enter the same owner command and Ruleset mechanics path.
Direct UI commands carry their initiating session and commit an exact outcome for subsequent GM
context, identified as player UI activity rather than a fabricated model tool call. Rulesets define
controlled actors, preview/confirmation policy, and whether an action or turn boundary requests a GM
response. Every committed outcome remains available to the GM regardless of that response policy;
an explicit player request can request a response. Direct actions are unavailable while the GM is
responding, and become available afterward only if current Ruleset state permits them. There is no
queued action or interruption path. Required enemy responses gate further play. A narration failure
after a committed action offers response-only retry and cannot repeat the mechanics.

The bundled D&D package declares Party, Encounter, and Inventory through this
mechanism. Panel buttons send only the declared surface/action identities back to the owner; the
owner re-resolves the exact hash-bound action, parameters, and package tool before committing it.
A
separate ability-free conformance package declares a different generic status panel, proving that
the core and frontend do not require D&D panel identities or state shapes. Dormant predecessor
`PanelDef`/`UIConfig` models are not a second runtime contract and must be removed or reduced to an
adapter onto the package manifest declaration.

## 11. Package library, pins, and activation

### 11.1 Exact immutable identity

A package identity includes canonical ID, exact version/revision, content hash, role, schemas/capabilities, compatibility, and provenance/publisher where applicable. A campaign has at most one primary ruleset; a no-ruleset campaign has none. Dependencies are typed, namespaced, exact, and ordered by explicit compatibility/precedence—not unordered peer merging.

Reusable content forms one immutable inheritance chain: Ruleset to Campaign Setting to Campaign
Plan. A Ruleset declares mechanics and has no Setting children in its package contract. Saving a
Setting captures an exact Ruleset copy. Saving a Plan captures an exact Setting copy, including that
Setting's Ruleset copy. Each descendant may customize the content it owns without mutating its
source, and source updates never rewrite an existing descendant. Import and admission of a Setting
or Plan must have all inherited bytes needed to use it without separately installed source packages.

Package archive format 4 implements the Setting boundary with a Setting-owned `ruleset_snapshot`
reference and includes every byte in that snapshot's exact dependency closure in the same archive.
Admission verifies those bytes and publishes them through the existing content-addressed package
library. Runtime activation keeps the existing roles: the inherited Ruleset is primary and its
dependencies remain dependencies; the Setting does not become a peer mechanics authority. Archive
formats 1 through 3 remain readable as legacy inputs.

A `.sylvia-gm-plan` archive contains the immutable reusable Plan record, structured Plan, readable
Plan, optional Interview Contribution and complete Setting package snapshot. It reuses the existing
record and package formats. Admission validates the closed, bounded file set, hashes and exact Plan
references before publishing the package copy and Plan entry. Reimporting identical content retains
one Plan entry. Native export uses a save dialog and checks the selected Plan revision before writing
the requested file.

Independent Plan publication applies explicit file replacements, additions and removals to its
retained Setting and Ruleset copies. The editable Plan keeps its original source bindings; the owner
derives effective hashes after package admission and validates references against the edited copy.
Runtime validation precedes saving a selectable Plan. Publication never rewrites source packages.

Optional Plan party placements apply at Start in player-first selection order. Each slot supplies
Ruleset-authored character-state fields and additions to existing Ruleset state lists, such as map
tokens or actor rosters. Exact participant-reference values bind only the assigned ID or display
name. This lets a reusable Plan place a party whose campaign identities do not exist at authoring
time. Missing slots or lists, duplicate identities and foreign namespaces reject before campaign
state is committed. Unused slots are omitted, and source Plans and Character Files remain fixed.

Independent Setting and Plan Builders may replace package declarations within those owned copies.
Records retain their package ID, role and content kind; publication derives file and package hashes
and binds dependencies to the supplied local versions. An explicit source selection identifies one
exact installed candidate and closure hash. Its complete dependency closure is copied before local
edits are applied. Missing dependencies and conflicting shared support copies reject. Builder reads
source records and files through the same bounded, compatible-library interface; reading alone
does not adopt them.

The Campaign Interview resolves optional question groups after Ruleset, Setting and Plan selection.
An accepted Setting answer retains its exact package candidate (the saved content version), and a
selected Plan takes precedence through its contained package closure. Accepted question answers
carry an owner-generated hash of their question group. Reselection detaches downstream answers;
legacy answers are re-asked when they cannot be matched to the effective group.

Adopting later source changes is an explicit Builder operation over a new descendant revision. The
operation retains compatible descendant customization and stops for user resolution when source and
customization conflict. There is no automatic propagation, generic merge framework, or dedicated
updater interface selected for Alpha.

Current bundled construction reads admitted identity/version from package-record.json and the
reference package record. A retained package-identity.json containing an older source version is
not the identity of the admitted launch archive. Reconcile any generator still using stale identity
input at its owner before regenerating a package; source labels cannot rewrite saved exact pins.

Each authored package has one canonical identity source from which generators obtain its ID,
version, label, publisher/source provenance, dependencies, minimum model tier, and compatibility.
Compatibility has three explicit dimensions: the host-contract revision, runtime-format revision,
and a sorted set of required engine features. Admission compares each dimension to host-owned
supported constants and names the first unsupported engine feature; labels, prerelease strings, or
generator-local dictionaries are not compatibility authority.

The Alpha spatial feature identities are `tactical_grid.v1` for shared coordinate-scene queries and
rendering, `spatial_mutation.v1` for exact revision-guarded scene mutation, and
`spatial_path_moving_entity.v1` for excluding one named mover footprint during pathfinding, and
`spatial_clearance.v1` for physical barrier and moving-body clearance. `shot_effects.v1` selects the
committed shot-presentation effect. Builder compilation infers clearance support from path queries
or explicit `occupies_cells` data, and shot support from `shot_effect`, and sorts the requirements
from the emitted runtime so an older host rejects a
package before activation rather than failing during play.

When a ruleset is present, the active campaign lock manifest references one complete exact dependency closure and missing or corrupt required bytes block load/play. A no-ruleset campaign has an empty closure and no ruleset package bytes to resolve.

### 11.2 SQLite-contained immutable bytes

For Alpha, validated package and dependency bytes are immutable content-addressed SQLite blobs. Manifests reference blob hashes; blob hash verification occurs on admission and recovery. Large blobs use streaming SQLite APIs or bounded chunking.

This selection avoids a cross-store publication split. An external content-addressed store is considered only after measured package/media volume materially harms backup, integrity, update, or runtime behavior.

### 11.3 Install and candidate states

**Install Package** performs:

1. file selection outside campaign authority;
2. bounded parsing and archive/path safety checks;
3. manifest, schema, dependency, capability, compatibility, provenance, and hash validation;
4. rejection of executable-plugin payloads from the Alpha-supported declarative path;
5. durable publication of the entire exact closure and validation report into the package library;
6. creation of a non-authoritative installed candidate.

Installation never activates or repins a campaign.

Installed candidates remain immutable package-library and recovery records; Alpha does not expose
individual candidate removal. Guided campaign creation projects one candidate per logical Ruleset:
the highest semantic version wins, and the most recently completed successful installation wins
within the same package ID and version. Installation recency is durable publication order, never
the lexical order of an opaque candidate ID. Failed or invalid staging attempts are not campaign
choices. Exact-candidate activation and retained campaign locks continue to use their recorded
identities rather than this convenience projection.

Bundled, Builder-created, and third-party content use the same compact portable package format. Alpha may import that format through the file picker or a controlled developer discovery surface, but discovery is candidate preparation—not activation. Sylvia GM hosts no Alpha marketplace/catalog and validates technical compatibility rather than rights ownership. Source/provenance metadata supports reproducibility; the user remains responsible for material shared through third-party channels. Exhaustive document forensics is an optional certified-source conversion and release-qualification path, not a requirement for ordinary ruleset authors or installed runtime packages. A certified package carries the useful semantic source map and compact hash-bound receipt; detailed extraction and review evidence remains build/release evidence unless a measured runtime or audit requirement selects it.

Trusted bundled drafts and guarded Builder drafts converge on one deterministic materializer. It
computes derived schema/file/dependency hashes, writes the canonical archive order and metadata,
resolves the exact closure, and immediately re-admits the completed bytes before returning them.
Checked-in generators may prepare normalized drafts but cannot maintain a second archive or hashing
contract. On first owner initialization, the bundled D&D SRD 5.2.1 closure is materialized and
installed through this same seam when its exact candidate is absent; no campaign activation occurs.

### 11.4 Activation

Activation requires:

- no active DM/Builder work;
- a completed TURN checkpoint;
- explicit user action;
- full exact dependency closure durably present and hash-valid;
- successful deterministic migration preview/application;

One short transaction revalidates those conditions, writes migrated state if any, switches the campaign lock manifest, records lineage, and increments the campaign revision. Failure leaves the old lock authoritative. Candidate bytes cannot imply activation.

Package reclamation may remove bytes only when no active campaign, tombstoned campaign, retained campaign history/lineage, installed candidate, or pending recovery reference requires them. Portable backup/export includes the exact required closure, so a later post-purge recovery does not depend on local garbage-collection history.

### 11.5 Read-only installed candidate inspection

The owner projects an installed candidate's exact closure identity, compatibility, publisher and
source provenance, public manifest actions, private runtime mechanics, gameplay-support counts,
and complete portable file tree without opening campaign data. A bounded safe-text query previews
declared text/YAML files with their byte count and hash. Comparison operates on two exact candidate
closures and reports package/version/hash and changed portable paths. Export rematerializes the
same admitted bytes through the shared materializer, preserves legacy archive-format identity where
needed, and verifies the native handoff's exact byte count and SHA-256. These operations are
read-only with respect to packages and campaigns and never imply activation.

## 12. Declarative extensions

Alpha supports only bounded declarative ruleset extensions through Sylvia GM-owned deterministic
primitives. Package admission rejects or leaves unavailable third-party executable-plugin payloads;
the first release has no advertised Unsafe Mode execution or executable-plugin consent journey.
Executable extensions require a separately selected post-Alpha architecture for isolation,
permissions, side effects, revocation, and recovery.

## 13. Generic Builder and DM sessions

### 13.1 Shared session substrate

Builder and DM use one generic conversational session substrate with model/provider selection, context/history, skills, tools, streaming, usage diagnostics, steering, Stop, cancellation, and resume. They are not special finite jobs.

Desktop provider settings are owner commands over the owner-local configuration file. Multiple
named configurations may coexist. Presets provide defaults for known hosted and local services,
while a generic OpenAI-compatible record owns its exact base URL, transport, model catalog,
context/output limits, and optional private headers. Presets never become an allowlist. Discovery
is bounded, searchable, paginated, and supports manual model IDs; it never silently selects the
only or first returned model. Local detection probes only an explicit bounded loopback list and
does not scan the LAN or manage inference processes.

The renderer may submit a newly entered credential or private header but never receives a saved
value back; secrets are not campaign truth, canonical history, browser storage, prompt context,
ordinary logs, export, or backup content. Reachability, authentication, discovery, request
transport, streaming, and tool support are separately reported so an offline or partially
compatible configuration may be saved with an honest warning rather than misrepresented as fully
agent-capable. Saving or removing a provider atomically rebuilds the in-memory provider resolver so
model choices update without restarting the application.

Saved API keys and private request headers use current-user Windows DPAPI at the owner-local
configuration boundary. A versioned protected envelope reuses atomic settings replacement;
decryption is confined to owner memory for provider operations. Legacy plaintext migration builds
and protects the replacement before publication, retains recoverable source settings on failure,
and introduces no plaintext fallback or migration backup. Protection/decryption/version failures
are explicit: preserve non-secret configuration and saved chats, block affected secret-dependent
requests, and allow re-entry or provider removal while unaffected providers remain usable. Protected
envelopes remain excluded from campaign data, logs, Audit, exports and backups. LRD-07 records the
selected implementation brief and its Windows verification; implementation is not claimed here.

The owner keeps one default model for newly created sessions. A session's current provider/model is
durable session metadata. An owner-private `--once` selection is consumed by exactly the next
provider turn even on failure or interruption without changing that durable current model;
`--global` changes both the idle session and the default. All switch scopes preserve conversation
history and reject while provider work is pending or active. Provider-specific transports,
including supported Responses models, remain explicit rather than being coerced to Chat
Completions.

Reasoning-effort preferences use this same owner/session selection boundary. Persist explicit
chat preferences, resolve missing preferences from the product's mode defaults without rewriting
old history, and keep one-turn overrides separate. Validate against the selected model/transport's
advertised or documented capabilities; preserve the capability facts for the UI. Snapshot the
resolved native options for the whole provider turn, including tool continuations. Consume a
one-turn override on success, failure or interruption and reject setting changes while work is
pending/active. Adapters translate to supported native parameters without silently changing token
budgets or service tiers. Audit distinguishes requested options from provider-confirmed values;
absence of confirmation is not proof of application. Effort never changes canonical tool outcomes
or the private-reasoning storage/inspection rules in §9.2.

The standalone left-sidebar Builder entry creates an ordinary top-level Builder session and
workspace without creating or selecting a campaign. It uses the same owner, SQLite authority,
stream lifecycle, and generic session substrate as other conversations; it does not create a
second persistence authority.

Sharing a session substrate does not mean sharing one tool set. Builder is a full generic agent with
its ordinary research, source/document/file, validation, testing, and approved-delegation tools. DM
receives all model-callable tools through the stateless MCP projection: the engine-calculated current
Ruleset mechanics plus always-available read-only campaign-reference retrieval and, for a structured
campaign, owner-validated narrative-state commits. DM has no direct filesystem or second
MCP-external mechanics path.

The owner persists safe resumable session conversation/metadata through canonical session records.
Campaign-associated sessions retain their campaign reference; standalone Builder sessions do not
acquire a fabricated campaign reference. Raw reasoning follows §9 and is never required for resume.

Model context is a bounded query projection, never a second campaign authority. The owner assembles
current Campaign State, private Campaign Plan preparation, relevant package-defined context, and
bounded recent play. It does not replay the complete transcript or force the complete campaign and
package corpus into every prompt. Builder and DM may retrieve additional canonical material on
demand through read-only application-query or MCP surfaces. Derived summaries, caches, and prompt
projections are disposable and must be rebuilt or invalidated from canonical records rather than
accepted as competing truth.

DM retrieval uses one campaign-knowledge interface with two deliberate steps. A bounded discovery
query returns only a small ranked set of exact references, source identity, visibility, and
provenance; it does not return substitute facts or become authoritative memory. The same GM then
reads the selected exact Campaign State record, Campaign Plan item, Character File, or admitted
Setting/Ruleset section. Campaign State takes precedence over conflicting reusable baselines.
Markdown package material is addressable by heading as well as file path, so an ordinary location
overview does not silently carry adjacent hooks, encounters, or secrets. Those preparation sections
remain available through an explicit exact reference. The search strategy may change without
changing this contract or adding a second authority.

For an admitted Player Character or Companion, the automatic projection is a compact roster entry
with the exact participant identity and useful surface identity. A complete Character File stores a
full authored profile separately from its short selector summary and structured Ruleset baseline.
The existing read-only participant-reference operation returns all three with source provenance and
marks an older file with no profile body as sparse. Campaign materialization preserves that exact
source rather than discarding the authored body.

The model-facing participant reference keeps those authorities visibly separate: `selector` is a
short setup-catalog aid, `profile` contains the authored biography or an explicit sparse status, and
`ruleset` contains structured starting data. Opaque Ruleset identifiers establish their exact stored
values; they are not expanded into unwritten biography.

Legacy sparse artifacts remain loadable so existing data and active-campaign provenance are not
destroyed. New-campaign materialization rejects a selected reusable artifact whose profile is still
sparse. The public setup projection marks that choice as not campaign-ready and directs the user to
the existing participant Builder flow; package-authored starting characters are unaffected.

On an ordinary player turn, exact roster names in the current player message select only those
Character Files for deterministic private retrieval beside that action. Matching is literal and
bounded to the roster; it is not intent classification, semantic authority, or a second GM. The same
GM may use discovery and exact reads for every other relevant character or source. Retrieved
material is turn context, not Campaign State. Only a Campaign File commit makes a campaign-specific
fact durable, and Campaign State overrides the reusable baseline whenever the active campaign
differs.

Every provider item retains source provenance. Only a genuine Player Input Event maps to semantic
User. Deterministic Campaign Interview questions map to Builder/assistant history; real tool calls
and results remain a correlated typed pair; application context, lifecycle, evidence, and invocation
control use trusted non-User input. Adapters reject unknown or unrepresentable items instead of
dropping them, defaulting them to User, or adding silent conversational prefills. A provider-required
User envelope around a typed tool result remains tool-result transport, not player speech.
Messages transports also require a conversation turn for an application-started Builder. Its
`SYLVIA_GM_BUILDER_START` envelope explicitly identifies the application-owned start signal; the
launch context remains application evidence and the envelope never becomes a saved player message.

Campaign Interview inference retains its complete ordered Builder-question/player-answer Transcript.
Saved free-form Builder sessions use a separate working-context projection: retained source material,
versioned continuation notes, recent exact exchanges and current application-owned context. This
projection never deletes or rewrites saved conversation, package snapshots or authored files.
The owner persists a checkpoint only after successful generation and a consistency check; cancellation,
new steering and source changes cannot be overwritten by an older checkpoint. Prior conversation and
compacted source material remain available through bounded session-scoped retrieval. Private model
reasoning is excluded from checkpoints. GM projections share the mechanism where needed while keeping
engine state, committed Campaign File notes and private-knowledge boundaries authoritative.

Checkpoints are internal session evidence in the existing SQLite history, containing a bounded
continuation note, identities of the covered exchanges and an archive of their original protocol
messages without inspection reasoning. Provider-required continuation remains separately governed
by §9.2; it is not incorporated as source prose in the compaction note. The owner acknowledges the
atomic append before the provider uses the new note. Public projections expose a completion marker
only; GM history-tool results also
stay behind the owner boundary. No additional database or external summarization service is needed.
The same mechanism serves automatic compaction and the manual Compact context action (`/compact`).

Before each provider call, including tool-loop continuations, Sylvia assembles input, tools, controls
and the selected output budget. Compaction starts with room for its own bounded request and the next
response; it operates only on completed exchanges and cannot execute or replay game/authoring tools.
The previous context remains usable if compaction fails. A saved checkpoint can resume after restart
or model switching; mismatched source identities invalidate its projection rather than inventing
continuity. Builder message-count clipping is replaced by this explicit mechanism.

An enforced output limit is distinct from estimated planning headroom. Builder's separately
adjustable default is 32,000 tokens, capped by the configured model maximum; explicit GM output
settings are unchanged. Subscription output remains runtime-managed. Codex accepts or rejects its
own requests; local counting is diagnostic and schedules compaction, with exact usage distinguished
from estimates. Other transports retain conservative pre-send admission where no authoritative
counter exists. The offline planning estimate uses serialized UTF-8 size divided by three with
headroom; it is neither an exact token count nor an admission authority. Initial trigger and target
fractions are 70% and 42% of capacity, further limited by enforced output and headroom. Bounded summary
fragments also fit conservative admission. These are tuning defaults for the deferred live batch.
Provider errors distinguish actual overflow, requested compaction and explicitly reported output
exhaustion; unreported subscription limits remain unknown. Recovery resumes unresolved work without
automatic provider fallback or replay after visible output.

For a structured DM session, the owner builds one knowledge catalog from the accepted Campaign
Plan and its exact contained Campaign Setting. The optional fixed Setting artifact
`setting-knowledge.json` contains bounded, canonically ordered facts with stable identity, source
path, and explicit `known_by` identities, plus an index of whole-file GM-private references. Package
admission verifies that every referenced path belongs to that exact package. A Plan secret is either
campaign-local fact data or a stable reference to a Setting knowledge fact, with the Plan owning its
campaign-specific reveal condition. Invalid or missing Setting fact identities reject the Plan.

The per-inference projection separates `PLAYER_KNOWN` from `GM_PRIVATE`, derives per-character
knowledge from `known_by`, labels Plan situations, encounters, branches, and consequences as private
preparation rather than current fiction, and includes only relevant source bodies. The automatic
projection carries compact campaign intent, explicit GM guidance, boundaries, and the current Plan
pointer. Detailed objectives, future situations, and unrevealed secrets remain behind exact Plan
retrieval until the GM needs them. An explicit `structured/structure/<act-id>` read returns that
act with its linked required objectives and completion conditions; it does not include another
act's preparation. This is a bounded read projection of the admitted Plan, not another stored
authority. An act requires all its linked objectives, each with all its authored conditions and
closing event; explicitly authored alternatives and failure endings apply to that same main
problem. Side work cannot replace or extend those requirements. Receiving a private fact never
mutates player knowledge. Scene,
fact, Campaign Entity, reveal, and Plan Progress changes are revision-fenced Campaign State commits
reached through MCP. They retain provenance and are independent of the DM session transcript.
Protected secrets still require a valid catalog identity before their audience can change, but the
core does not turn a Plan act order into a scene script. Recent conversation is bounded independently
from these durable projections, so restart and history truncation reconstruct the same current truth
without promoting a private fact. When more Campaign State records exist than fit in the automatic
projection, ordinary discovery by name, subject, relationship, or location still resolves to the
exact durable record; the GM does not need to guess its record ID.

Authored campaign endings extend Plan v2 with finite intended outcomes or an explicitly ongoing
final-act note. A final-act reference read includes that ending preparation on demand. Existing
Plan Progress records the reached finite outcome as `ending:<id>` after all required acts and
linked objectives resolve; admission checks this structural dependency and a single reached
outcome. The GM still judges the authored story conditions and immediate aftermath. An ongoing
final act cannot be resolved. Retained Plans without this extension remain readable unchanged.

The completion card is a projection of earned ending progress anchored after its canonical TALK.
It exposes only public title/act, continuation viability and whether an after-story exists.
Continue/Finish enters the ordinary DM input path: the validated ending identity and choice are
saved atomically with explicit OOC text in that user message. Saved history reconstructs the quiet
completion marker after selection, including reopen and context compaction. It does not add a
campaign lifecycle state, archive the campaign, lock chat, or append authored acts. Home navigation
does not make a choice. Further OOC instructions can change direction.

An optional Campaign Plan `opening_direction` is included in that private preparation only during
the opening exchange and any of its tool-correction rounds. It is labelled as creative framing, not
factual or mechanical authority, and is omitted from later turns. Plans without it use the same
opening path. The one-time lifecycle control places a compact factual `opening_footing` beside that
instruction: the selected Player Character's useful surface identity, present Companions, and
starting location, together with explicit markers that pre-opening history and the Player
Character's opening action are unestablished. These values are projected from admitted
participants and the accepted Plan. The opening also includes the bounded `Overview` section from
the exact starting-location reference when that conventional section exists, otherwise a bounded
leading excerpt; the full record remains available through MCP. This gives the GM local setting
texture without scripting a scene or mining an entire location file for optional material. If the
Opening Direction calls for authored character background, the GM uses each roster identity to
retrieve the relevant Character File before it settles the opening. Current-message exact-name
retrieval does not inject every party profile into an opening that did not name them.

Immediately before ordinary current player input, the owner supplies one compact action-boundary
control. It does not classify the action, set a word limit, score prose, or rewrite the result. It
keeps the player's exact words salient after the larger Campaign File and preparation projections,
and reminds the GM to leave follow-up observation or investigation for a later player choice.

Operational provider/model identity, reported token counts, call counts, and timing are projections rather than campaign mechanics. Builder may expose them through its inspection surface. DM keeps equivalent information out of the persistent immersive conversation and makes it available only through an unobtrusive tooltip or comparable secondary surface. Sylvia GM never displays estimated monetary cost. Future in-house subscription consumption belongs only in a post-launch Usage Settings surface; Alpha local/BYOK operation does not fabricate it.

### 13.2 Builder

Standalone Builder starts as a general conversation. A compact capability catalog advertises BUILD
RULESET, BUILD CAMPAIGN SETTING, BUILD CAMPAIGN PLAN, and ordinary tools; full tracked-skill
instructions load only when the model selects that workflow. The model coordinates adaptive
research, source ingestion, questioning, drafting, tool use, and validation. Its normal agent tools
remain available after a DM session starts. Deterministic code owns schemas, package identity,
permissions, candidate admission, and campaign start.

The Campaign Interview uses the same session substrate with dedicated owner-controlled Interview
State. Deterministic questions and exact player answers require no inference. New Ruleset, Setting,
Plan, Player Character, and Companion buttons launch ordinary independent saved Builder sessions.
The owner saves the button/purpose, original selected content identities, relevant Interview
context, and source references as launch context, separate from genuine player messages. Builder
loads the relevant skills and references on demand. Later changes to the Interview do not retarget
the chat. Character and Companion work uses ordinary artifact capabilities without new tracked
skills. Explicit campaign customization and meaning-changing interpretation retain their confirmed
application to the Interview.

The Interview stays saved and selectable while other chats work. Returning refreshes compatible
library choices without auto-selection, highlighting a presumed result, or a completion handshake.
Streams, input, steering, Stop, and working/completed indicators belong to their session; navigating
does not redirect or cancel another chat's output. Descriptive titles can refine automatically and
remain user-renamable. Interrupted work is identified and resumable after restart. Existing linked
content chats and their files migrate without losing their history or unfinished work.

Builder writes ordinary persistent workspace files. Completed, validated content is saved to the
local reusable library; the player selects whether to use it in a campaign. Unfinished drafts stay
resumable. Shared-file saves reject an edit based on an outdated read; Builder rereads and reconciles
compatible changes and asks about conflicting intent. Independent chats, their original context,
files, and reusable content survive completion, archive, or permanent deletion of the originating
campaign and have their own deletion controls. A file is not
live campaign truth merely because Builder wrote it; deterministic validation and explicit
application commands still own any admission or campaign mutation. The bounded Campaign Interview
is not a general artifact workflow or publication state machine.

BUILD RULESET begins from the user's natural request without requiring a predetermined interview form. Its default path produces a compact declarative package through bounded deterministic schema, cross-reference, dependency, capability, identity, and representative mechanical-behavior validation; ordinary authors do not run exhaustive PDF forensics. When explicitly selected for a permitted source, Builder may instead coordinate certified source conversion with exact source identity, semantic records and locators, coverage and ambiguity decisions, deterministic regeneration, source-to-mechanic mapping, and a compact certification receipt. Builder may use user-supplied notes/documents, open public documentation, and user-directed online research for potentially restricted material. It does not independently hunt for unauthorized source copies. Contradictory sources and missing engine capabilities stop at human review; Builder must not silently approximate an unsupported mechanic and claim completeness. Provenance is preserved for technical reproducibility, not copyright adjudication.

The normal authoring source is one readable `ruleset-source.yaml`: identity, mechanic-family
templates, configured public actions, private mechanics, definitions, participant schemas, and an
optional operating guide. Builder owns converting product-language intent into that source and
invoking deterministic compile/materialize/install commands; the author is not expected to hand-edit
generated manifests, file hashes, or archives. Validation failures return structured diagnostics
with a stable code, phase, message, portable file, data path, optional package identity, and useful
guidance. They do not collapse to a generic invalid-package string when the owning boundary can name
the defect.

Approved delegated Builder child sessions use ordinary generic session relationships and artifact/evidence return. The parent Builder evaluates, integrates, validates, and presents the result. No separate durable workflow engine or special run-budget substrate is introduced.

### 13.2.1 Shared content definitions and repair

Supported content formats have versioned authoring definitions and matching validation at their
owning boundaries. The coverage is Ruleset source/runtime packages, Settings, Plans, Character
Files and their structured subdocuments, and the Portable Campaign Export already defined in
section 15. Reuse current formats where they satisfy the product contract. Derive machine-readable
definitions and author documentation from the same owning declarations where practical; document
procedural reference and consistency checks beside those definitions. Unknown structured fields
reject except in explicitly declared open areas. Ordinary lore body text remains free-form.

Builder retrieves applicable definitions and examples for its saved content context. Draft checking
calls the same compiler, schema, reference and declared-calculation validators used by final
admission, without invoking publication, selection, installation, or live campaign mutation.
Incomplete drafts stay in their workspace. Diagnostics carry the portable file and data path plus
actionable correction facts. A successful check states what was checked; it does not confer
publication or prove every possible gameplay sequence. This does not introduce a general code
execution sandbox or a second mechanics engine.

Builder mechanics tests selected under PES-08 must share the real campaign paths for action
admission, mechanical execution, committed results and GM/player control handoff. Tests operate
on isolated disposable state; a separate approximation of the game loop is insufficient.
Saved scenarios supply scripted GM decisions and simple narration completion through the normal
session/adapter path. The script supplies inputs when requested; it must not reproduce the logic
that decides when those inputs are required or allowed. Scenarios can control dice results and
span several actions/turns; test randomness must stay isolated from live campaign randomness.
The local test worker composes CampaignSessionLoop, ProviderSessionAdapter and AuthorityOwner;
direct player steps use AuthorityOwner.player_action and its ordinary response path.
Reusable Ruleset test cases cover mechanics and
narration/decision timing; generated-prose quality is outside this selected testing boundary.

Character validation composes Setting-owned world requirements with the contained Ruleset's
participant and starting-state requirements. Authored inputs, calculated outputs and allowed
customization are explicit in the applicable definitions. Creation calculates derived values with
the established deterministic Ruleset runtime and validates the resulting complete starting data.
It reports missing inputs or unsupported/missing calculations before accepting playable content.
Import and campaign admission verify the applicable requirements again; they cannot accept
unvalidated statistics merely because JSON types survived transport. Existing admitted live
campaign state is not automatically recalculated by this authoring change.

Explicit character reuse validates unchanged character data against the newly selected Setting
and its contained Ruleset, then saves a separate immutable character bound to those exact
identities. Schema compatibility does not weaken package hashes or rewrite the original character
or any campaign. If data must change, return the required adaptation to Builder for explicit
authoring and user choices.

General character profiles (unbound authoring sources selected in AR-07) use a separate versioned
`sylvia-gm-character-profile` v1 envelope: name, short summary and full authored profile, with no
Setting, Ruleset or mechanical binding. The owner validates and saves immutable local library
entries; Builder edits guarded workspace copies. The existing content-definition interface exposes
their schema and the existing discovery catalog distinguishes them as requiring adaptation, never
as playable participants. Keep the current playable Character File format and validation intact.

The Companion creation selector may open an independent Builder Session from one exact profile.
The owner captures its validated source snapshot and digest alongside the original target references
in the existing durable launch record. Ordinary reference read/copy tools expose the full snapshot;
provider-history projection carries metadata and a retrieval path rather than repeating its body.
Launch replay uses the retained source and rejects conflicting identity. Source edits or removal,
Interview reselection and restart do not retarget that saved session. Builder publishes adaptations
through ordinary target checks, and the player explicitly selects the saved result.

Owner-local reusable-participant record v5 retains nullable source-profile identity, digest and name
as hashed provenance. Earlier record versions retain their exact read/hash behavior without a mass
migration. Source provenance is not a dependency needed to load or play an adaptation; compatible
copies and repairs retain it without propagating base edits. This addition does not change packaged
Character File v2, create a live inheritance relationship or alter Campaign State. The AR-07 ticket
specifies the concrete tool, picker, storage and compatibility work; this is a selected design, not
an implementation-complete claim.

An import that passes the existing safe-file/archive boundary but fails content validation can
supply a repair copy and bounded diagnostics to a new independent Builder session when the user
selects Fix with Builder. Preserve the source file, retain the ordinary saved-session lifecycle,
and require repaired content to pass normal admission before use. This path grants no access to
unrelated files and cannot convert a rejected archive-safety check into accepted content.

### 13.3 Start DM from a completed Draft Campaign

Ready to Begin is one genuine Player Input Event and one idempotent external start operation that
requires:

- a selected, resolved Campaign Plan, including its inherited Setting and Ruleset copies, and
  campaign state that pass structural validation;
- a selected party that satisfies the Plan's Starting Party Policy and the Ruleset's outer
  participant envelope;
- the Plan's exact inherited Ruleset closure or the explicit admitted empty No Ruleset closure;
- every required Ruleset-declared participant artifact validated and compatible;
- every required Interview answer and meaning-changing interpretation confirmed;
- semantic completion for adaptive work, no unresolved Boundary conflict, and no active work able
  to change the admitted construction; and
- one current construction writer and expected revision.

The durable handoff transaction rechecks the current construction basis, copies the Plan's authored
starting world and map into the campaign with the chosen party, accepts Plan/state, freezes the exact
binding, ends construction mutation authority, sets DM as sole gameplay writer, creates
the DM session and initial TURN, and records handoff chronology. Failure exposes no DM authority and
leaves the Draft coherently retryable. No provider request occurs inside that transaction.

Structured Plans may include `initial_state.mechanics`: bounded namespace/key/value entries owned
by the inherited Ruleset. Start copies these entries into campaign state and adds the chosen party
at the Ruleset's declared character location. It preserves authored world actors and sibling state,
and rejects identity collisions or an incompatible character-state shape. Narrative `entities` and
`facts` remain distinct from these mechanical values. The source Plan stays immutable.

A Ruleset may declare data-only `state_schemas` in its package record, with the explicit
`starting_state_validation.v1` host requirement. Each declaration selects an exact state key in
that Ruleset's own namespace, applies the admitted JSON-schema subset, and optionally checks
references to declared collections in the same namespace. Absent selected keys remain absent;
present values, including null, must satisfy the schema. Collection selectors may bind a source
record's key for a corresponding target lookup; they cannot execute expressions or package code.
Ordinary Plan draft, import and start validation use the exact retained Ruleset declaration.
Rejections report field paths and corrective schema/reference messages without copying private
values, granting access, repairing state or changing the Plan. This authoring check does not
replace runtime action eligibility or enforce Ruleset rules that its declaration does not encode.

The first GM inference receives the exact Ready event once as User and `campaign_start` as separate
application-owned non-User control. It receives Campaign State initialized from the Plan's admitted
initial facts and starting location, plus separately labelled private Plan preparation. Future Plan
situations and encounters are not current merely because they appear first. It receives no copy of
the construction Transcript. Failure after committed handoff is an ordinary retryable interrupted
GM TURN and never creates another Ready event.

This transition does **not** close, stop, delete, replace, or reduce the ordinary availability of
persistent Builder chats. Their construction relationship becomes read-only and they cannot compete
with DM authority over the live Campaign Aggregate.

## 14. Campaign construction and lifecycle

### 14.1 Identity and construction

NEW CAMPAIGN creates one stable Campaign Aggregate and deterministic first Builder question before
content selection or inference. Draft/play construction phase is orthogonal to active/archive/Trash
availability lifecycle. A new Draft has no placeholder lock or gameplay TURN; explicit No Ruleset is
represented by an admitted empty closure and is distinct from unbound.

Rename, archive, restore and Trash apply to an unbound Draft through the same lifecycle commands.
They record campaign revisions without inventing a package lock or gameplay TURN. When the player
later completes the restored Interview, its first real content basis is admitted at the current
revision. Earlier revisions must contain only the Draft's lifecycle operations. Campaign deletion
continues to remove its own Interview while retaining independent Builder chats and library content.

The owner advances the fixed guided stages and composes only restricted, versioned declarative
Interview Contributions from the exact content selected so far and its contained copies. Contributions cannot
inject executable code, tools, free-form system policy, or writes into another artifact's domain.
Interview State is resume authority; exact questions and answers live once in canonical session
messages. Every transition is revision-fenced and durable before the UI advances.

Reselection is independent of ordinary content Builder activity. It removes incompatible downstream
answers and selections from the active path, marks incompatible campaign-local work detached, and
preserves chats and reusable artifacts. Raw player answers remain immutable; any derived
meaning-changing interpretation remains separate and requires player confirmation.

Player Character and Companion concepts are package-declared. The Ruleset supplies their outer
mechanical envelope and compatibility schema. The Campaign Plan narrows that envelope with one
versioned Starting Party Policy: a fixed or compatible Player Character, exact Required Companions,
and minimum/maximum additional Custom Companions. Required Companions do not count toward the custom
limits. The Interview enforces the policy, re-evaluates it after upstream selection changes, and the
handoff validates it again against the final artifacts. The bundled D&D/Rift adapter admits one PC
plus at most five total Companions; the Rift Plan currently requires none and allows zero to five
Custom Companions. The neutral core does not impose that cardinality on other Rulesets.

Generic campaign setup resolves fixed and required characters from the exact selected Setting's
admitted package files. A playable `sylvia-gm-character-file` version 1 JSON object has exactly
`format_id`, `format_version`, `id`, `schema`, `summary`, `authored_profile`, `participant`, and
`entity_state`. Its `id` matches the Plan reference; `schema` contains the selected Ruleset's declared
schema `id` and `content_sha256`. The full authored profile and short selector summary remain
separate, and `participant` must pass that schema. `entity_state` is an explicit Ruleset-owned JSON
object copied into the declared actor-state location. Setup does not derive statistics from prose.
The shared deterministic schema subset supports typed `allOf`, paired `if`/`then` and array
`contains`/`minContains` constraints. Rulesets may declare build prerequisites using these data
constraints; draft checks, publication and saved-file admission enforce the same schema. The host
does not interpret class or specialty identities, infer allocations or execute package code.
The Builder publication tool receives this opaque state as JSON object text so model tool-argument
encoding preserves its scalar and collection types. The adapter rejects malformed, duplicate-key,
nonfinite, oversized or excessively nested JSON, then passes an ordinary object to the existing
library API. The library and saved Character File still store a JSON object. This transport check
does not establish that an authored statistic is correct under its Ruleset.
The caller binds the file to the exact Setting and its contained Ruleset package identities.
Those package bindings remain outside the Character File envelope; the schema identity inside
the file identifies its mechanical declaration. Rulesets do not depend on or declare Settings.
Files are bounded to 256 KiB, with unique object keys, closed metadata, and at most 32 nested levels.
Invalid or incomplete files appear unavailable. Start resolves them again, enforces the exact fixed
Player Character and required Companion identities, and copies their profiles and state into the
campaign. The bundled Rift adapter retains its existing admitted source format.

### 14.2 Lifecycle commands

- **Rename:** updates display metadata and revision; identity is unchanged.
- **Archive:** available only when no campaign-mutating Builder/DM work is active; reversible under the same ID; blocks new campaign mutation. Independent content Builders do not block it.
- **Restore from Archive:** reactivates the same ID after closure/integrity validation.
- **Delete:** creates an indefinite recoverable tombstone in Trash; does not purge shared package bytes.
- **Restore from Trash:** restores the same ID after validation.
- **Permanent purge:** separate explicit irreversible local-store action; removes the campaign and its Interview, retaining independent Builder chats, their work, and reusable library content. Remove only package bytes no remaining reference protects.
- **Backup recovery after purge:** restores the original campaign ID from a validated portable backup. Collision with an existing local ID blocks automatic merge/replacement.

No lifecycle command silently queues behind active work or cancels it. The user waits or explicitly Stops first.

An idle open TURN is retained across Archive and Restore under the same identity. The runtime
campaign-work reservation excludes active execution; incomplete action transactions still block
Archive. A saved TURN awaiting player input is not itself a running campaign mutation.

### 14.3 Schema evolution and explicitly supported import

The first public Alpha has no predecessor campaign compatibility requirement. The preceding
prototype was unreleased and has no user campaign population to import. Inputs from an
external format are supported only when Sylvia GM explicitly advertises that format.

Unreleased development state stored under the retired prototype identity is deliberately unsupported
and disposable. The rebrand does not inspect, move, or mutate that private state and introduces no
legacy compatibility subsystem. New Sylvia GM launches and installs create only Sylvia GM-named
paths, identifiers, exports, and package formats.

- routine schema/storage changes migrate automatically;
- campaigns retain their exact ruleset/package versions rather than being casually rewritten by application updates;
- a recoverable source is preserved before materially changing migration, and failure never destroys the only campaign copy;
- safe normalized migration provenance travels with portable export, backup, and recovery;
- portable provenance excludes original filesystem paths, raw source bytes or excerpts, credentials, machine identifiers, and private parser/filesystem diagnostics;
- genuinely ambiguous supported inputs fail clearly rather than being guessed;
- a campaign declaring no ruleset simply has no ruleset;
- an LLM never invents, blends, or silently resolves campaign truth.

The compatibility design is proportionate: current-schema evolution, exact package retention,
ordinary backup/recovery, and clear failure—not certification machinery for a nonexistent prior
public release.

## 15. Backup, export, recovery, and startup

A Draft and a campaign in play use the same `.sylvia-gm-campaign` archive. It contains:

- stable campaign identity, availability lifecycle, construction phase, and revision;
- exact active selections, lock/dependency lineage, and required immutable package bytes;
- Draft Interview State, canonical transcript/session messages, campaign customization, retained
  detached-work references, participants, and campaign-local artifacts required for resume;
- current state, accepted Campaign Plan, TURN/action records, and chronology once play has begun;
- safe schema-evolution/import provenance, schema/product version, and checksums; and
- no Admin Full Audit, credentials, independent content Builder chats, or unrelated workspace scratch.

Saved thinking is an optional, explicitly selected export component under §9.2; full database
backups retain it. An export that omits reasoning must not pretend missing provider-native
continuation is present or fabricate a Thinking record on import.

Startup/recovery validates database integrity, schema version, Campaign/Interview revision
relationships, session-message references, incomplete TURN/action status, canonical ordering, and
exact package closure. It preserves committed action outcomes, discards incomplete provisional
prose, and presents explicit recovery/quarantine when coherence cannot be proved. UI caches and
browser state are never recovery authority.

## 16. Architecture acceptance

Architecture is accepted through the supported journeys that exercise it, not through historical
test counts or a separate evidence programme. The active plan selects the current journeys and
release candidate.

A changed architecture boundary needs:

- a focused invariant test at the owner of that boundary;
- representative composition through its direct consumer;
- persistence, restart, privacy, or installed-artifact proof when the boundary affects them; and
- plain reporting of unsupported or deferred behavior.

The Windows release journey must exercise the actual desktop, supervised owner, configured provider,
Builder or GM session, package/runtime path where applicable, save/reopen behavior, and clean process
shutdown. Lower-layer mocks do not replace that proof.

New Campaign acceptance includes a zero-inference Draft path, independent Player Character and
Companion creation followed by returning to the saved Interview, confirmed adaptive customization,
warned reselection with retained work, Draft export/recovery, fail-closed provider capacity admission,
role/input Audit, and an installed same-identity Ready-to-GM handoff.

## 17. Rejection gates

An implementation is outside this contract if it:

1. creates mutable campaign truth outside the SQLite Campaign Aggregate;
2. permits overlapping Builder/DM writers or client database mutation;
3. acknowledges mechanics before the tool result and authoritative mutation commit coherently;
4. holds a SQLite transaction across model, user, network, plugin, or file-picker waits;
5. reconstructs canonical chronology from renderer arrival order;
6. retries narration by rerunning committed mechanics;
7. gives a model a ruleset/engine path that bypasses the selected stateless MCP boundary;
8. introduces undeclared D&D/TTRPG concepts into no-rules play or the neutral core;
9. falls back from missing exact package pins or auto-activates candidates;
10. exposes credentials, places private reasoning in canonical campaign memory or ordinary logs,
    or exposes/exports/resends reasoning outside the explicit inspection, backup, export, and
    provider-required continuation boundaries in §9.2;
11. reports success from prose or UI state when the authoritative transaction failed;
12. lets a package execute arbitrary code through the Alpha declarative extension path;
13. lets a client, sync replica, or second process co-own the database;
14. represents application control, evidence, or lifecycle state as player speech;
15. silently compacts required Campaign Interview history or claims an estimate is exact;
16. allows unresolved campaign customization or a Boundary conflict into handoff; or
17. deletes detached Builder work merely because an upstream selection changed.

## 18. Change control

A material product or architecture change requires a direct human selection and an update to the
applicable active contract. Routine implementation and documentation maintenance do not require a
new architecture packet, verifier chain, phase plan, or handover archive.

Project Desk owns current planning decisions. The active plan synchronizes repository status and
sequencing; Git history plus its current-work section preserve repository evidence across environments.
Historical documents, generated handoffs, test output and agent summaries remain evidence only.

The following remain post-Alpha unless directly selected: executable plugins and their sandbox,
cloud/subscription infrastructure, native macOS/Linux clients, multiplayer, automatic updates,
paid signing, a bundled original ruleset, and a generalized package marketplace.
