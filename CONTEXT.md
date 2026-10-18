# Sylvia GM Domain Language

This glossary records Sylvia GM's canonical product terms, preferred names, semantic distinctions,
and avoid-list language. It does not define product promises, architecture mechanisms, delivery
status, or side-effect authority; those remain with the owning documents in `docs/INDEX.md`.

## Campaign construction

**Guided Builder Chat**:
The player-facing conversation in which Builder guides a player from campaign choices to a campaign
that is ready for the Game Master. It may contain deterministic widgets and adaptive Builder
discussion.
_Avoid_: New Campaign form, generic Builder chat

**Campaign Interview**:
The saved guided conversation that gathers compatible content, participants and player requests
for a new campaign. It keeps its own place while the player uses independent Builder Sessions.
_Avoid_: setup form, prompt questionnaire

**Deterministic Happy Path**:
The Campaign Interview path in which the player chooses existing compatible content and
participants, completes every active required question, chooses no specific requests, and triggers
no adaptive interpretation or creation. It reaches construction readiness without an LLM
inference.
_Avoid_: mandatory final review inference, Quick Start lane

**Interview Contribution**:
A declarative group of questions that a Ruleset, Campaign Setting, or Campaign Plan adds to the
Campaign Interview. It may describe answer types, choices, conditions, requiredness, safe
explanatory text, and whether an answer needs adaptive Builder interpretation. It is interview data,
not arbitrary system instructions or executable code; ordinary artifact prose is not an Interview
Contribution.
_Avoid_: injected artifact prompt

**Interview Widget**:
A deterministic interactive question presented inside a Guided Builder Chat. It may use choices or
a bounded free-text answer. The player's interaction is genuine input even though it is not
fabricated natural-language dialogue.
_Avoid_: synthetic User message

**Compatible Content Selector**:
The visible choice point for a Ruleset, Campaign Setting, or Campaign Plan stage, including the cases
where no compatible item exists, one is suggested for confirmation, or several are available.
_Avoid_: hidden auto-selection, empty dead end

**Specific Requests**:
The final global Campaign Interview question for requests not already captured, including an
explicit answer that the player has no additional requests.
_Avoid_: mandatory final model review, customization notes box

**Boundary**:
A hard player limit that Campaign content, Builder interpretation, and later requests cannot
silently override. A conflict must be resolved before the campaign is ready.
_Avoid_: preference, GM-discretion safety note

**Preference**:
Player guidance that should shape the campaign but is softer than a Boundary. It never silently
overrides a Boundary.
_Avoid_: hard limit

**Starting Party Policy**:
The Campaign Plan's declaration of whether the Player Character is fixed or compatible, which
Companions are required, and how many Custom Companions the player may add within content compatibility.
_Avoid_: free-text party requirements, Ruleset party composition

**Fixed Player Character**:
The exact Character File a Campaign Plan requires as its Player Character.
_Avoid_: suggested character, default archetype

**Required Companion**:
An exact Companion a Campaign Plan requires in the starting party independently of player-selected
Custom Companions.
_Avoid_: optional Companion, planned NPC

**Custom Companion**:
An owner-local reusable Companion selected or created by the player for a campaign whose Starting
Party Policy permits it and whose Campaign Setting and Ruleset admit it.
_Avoid_: Required Companion, bundled Plan Companion, ordinary NPC

## Conversation and authority

**Player Input Event**:
A player's genuine typed message or structured interaction, including an exact Interview Widget
answer, user-origin steering, or Ready to Begin action. It preserves what the player actually did
without manufacturing dialogue for them.
_Avoid_: synthetic User message, application control

**Application Control Event**:
A lifecycle, state, application-created steering, or invocation event created by Sylvia GM rather
than the player or model. It is not a Player Input Event and must never be represented as speech by
the player.
_Avoid_: hidden User message, prefixed fake dialogue

**Interview State**:
The saved position, accepted answers, unresolved requirements and content selections of a Campaign
Interview. It belongs to the Draft Campaign and is distinct from the visible Interview Transcript.
_Avoid_: conversation history

**Interview Transcript**:
The complete ordered conversational projection of Campaign Interview questions and the player's
exact answers. Deterministic questions are Builder output; exact typed, chosen, or clicked answers
are Player Input Events; real tool interaction remains tool interaction. Interview State remains
the resumable source of truth.
_Avoid_: active-node-only context, fabricated dialogue, summary replacement

**Builder Capability Catalog**:
The compact inventory of Builder skills and tools that keeps the general Builder aware of what it
can do. Awareness of a capability does not grant authority to use it against a Draft Campaign or
artifact.
_Avoid_: loading every full skill every turn, workflow-specific Builder

**Builder Authority Boundary**:
The distinction between Builder's creative capabilities and Sylvia GM's authority over artifacts,
Draft Campaign membership, lifecycle, and writer ownership. Builder capability awareness never
grants campaign mutation authority.
_Avoid_: prompt-only authorization, hiding general capabilities

**Builder Session**:
An independent saved chat for creating or editing reusable game content. A session opened from a
Campaign Interview retains its launch context without depending on that Interview's later progress.
_Avoid_: attached subflow, child campaign

**Creation Subflow**:
The former campaign-attached creation flow with a special return step. It is a historical term;
new creation work uses an independent Builder Session.
_Avoid_: using this term for current Builder Sessions

## Ruleset support

**Ruleset**:
The campaign's rulebook: it defines how play is resolved, including which judgments belong to the
GM and which mechanics the engine resolves deterministically. It does not keep a separate copy of
Campaign State.
_Avoid_: combat engine, Ruleset State, mechanics database

**Ruleset Engine**:
The deterministic referee that applies procedures declared by the active Ruleset to its owned facts
in Campaign State. It does not decide narrative direction or keep a separate state store.
_Avoid_: combat database, Ruleset State, second campaign authority

**Ruleset Snapshot**:
The copy of a Ruleset contained by a Campaign Setting, including its rules and reference material.
It can be customized locally; changes to the source Ruleset enter that copy only through explicit
adoption.
_Avoid_: live dependency, Ruleset-declared Setting

**Result Card**:
A player-facing presentation of a Ruleset outcome or available action, using Sylvia's common card
layout and the Ruleset's own labels and information.
_Avoid_: D&D-only skill widget

**Game Panel**:
A Ruleset-provided or built-in rules-neutral view of campaign play, such as Map, Party or Inventory,
placed in the sidebar or an extra window within the active campaign's saved layout.
_Avoid_: second campaign owner

**Reference Availability**:
Whether a Ruleset includes or excludes the exact rules content for a record, independently of how
that content is resolved during play.
_Avoid_: implemented, mechanics coverage

**Gameplay Resolution**:
How a Ruleset honestly handles applicable content during play: by mechanic family, specialized
engine behavior, a named engine/GM split, deliberate GM adjudication, or explicit non-support.
_Avoid_: implemented, pending, complete coverage

## Campaign artifacts and state

**Character File**:
The reusable authored baseline for a Player Character or Companion, addressed by stable identity.
It contains a full authored profile and Ruleset-compatible starting data; its short catalog summary
is only a selector. Campaign State governs any campaign-specific fact that later differs from the
baseline.
Older files without an authored profile remain readable, but must be completed before they can be
selected into a newly created campaign.
_Avoid_: participant prompt, character memory, Campaign State

**Campaign Setting**:
The reusable world reference together with its contained Ruleset Snapshot. Campaign State governs
any campaign-specific fact that later differs from this baseline.
_Avoid_: current world state, Campaign State

**Campaign Plan**:
The GM's private story preparation, with a contained copy of its Campaign Setting and that Setting's
Ruleset. Its intended direction, truths, reveals and situations guide play without automatically
becoming current fiction.
_Avoid_: next-scene script, screenplay, current state

**Opening Direction**:
Optional private Campaign Plan guidance for the framing, pace, or dramatic purpose of the first
playable exchange. The GM interprets how to realise it from current truth; it does not establish
fiction or override the Ruleset, and it is not repeated into later turns.
_Avoid_: opening script, current scene, mandatory encounter

**Campaign Entity**:
A person, place, group, object, or other subject tracked in a campaign. It has a stable identity so
Campaign State can record campaign-specific facts without changing its source Campaign Setting
entry.
_Avoid_: copied Setting file, name-only reference

**Campaign State**:
The mutable GM-screen record of what is true in one campaign now, including each Campaign Entity's
current facts, relationships, knowledge, and Ruleset-owned mechanical values. It is durable memory
inside the Campaign Aggregate, not a transcript or a second state store.
_Avoid_: World State, Ruleset State, session memory, conversation history

**Campaign Chronology**:
The ordered record of what happened in play and what changed Campaign State. Campaign State answers
what is true now; Campaign Chronology explains how it became true.
_Avoid_: current state, conversation history

**Plan Progress**:
The private bookmark between actual play and the Campaign Plan. It records which prepared material
has been introduced, deferred, resolved, retired, or remains available without making that material
current fiction.
_Avoid_: Plan State, forced scene order

**Inventory Holding**:
A quantity of an item or named currency tracked in a campaign, with ownership distinct from its
current holder or storage location. Identical items may share a holding; distinct items retain
separate identities.
_Avoid_: ownership inferred from possession, narrative-only inventory

**Fallback Inventory**:
Rules-neutral Inventory Holdings used when a campaign has no Ruleset-defined inventory system.
It is not a backup copy of an active Ruleset inventory.
_Avoid_: mirrored inventory, implicit D&D inventory

**Campaign-local Derived Plan**:
A Campaign Plan revision created for one Draft Campaign when player requests require changing a
selected Campaign Plan. The selected installed Plan remains unchanged. If the player later likes the
result, they may explicitly save a reusable copy as a separate Campaign Plan.
_Avoid_: mutating installed content, customization notes

**Reusable Plan Sibling**:
A reviewed, generalized Campaign Plan saved from a Campaign-local Derived Plan under its own exact
identity. Saving it does not mutate the installed source Plan or change the campaign that produced
it.
_Avoid_: overwriting the selected Plan, silent campaign rebind

**Detached Construction Work**:
Campaign-local work that no longer belongs to the Draft's active construction path after a content
choice changes. It remains available but cannot affect readiness or handoff unless made compatible
and admitted again.
_Avoid_: destructive invalidation, silently active stale work

**Draft Campaign**:
A durable, resumable campaign under construction, created when the player begins New Campaign before
content selection. It owns a stable campaign identity, Guided Builder Chat, Interview State,
selections, and linked Creation Subflows before play begins.
_Avoid_: anonymous setup chat, temporary form state

**Construction Phase**:
Whether a Campaign Aggregate is under construction as a Draft or has entered play. It is independent
from whether the campaign is active, archived, or in Trash.
_Avoid_: inferred writer state, temporary pre-campaign object

**Construction Content Binding**:
The Draft Campaign's exact selected Ruleset and Campaign Setting package closure. A Draft begins
unbound, may replace its binding only during construction, and cannot carry incompatible downstream
work into handoff. Handoff freezes the admitted binding.
_Avoid_: creation-time placeholder binding, silent binding replacement

**No Ruleset**:
An explicit Ruleset-stage choice for a campaign that uses no rules engine. Compatible Campaign
Settings and Campaign Plans may still be selected; no generic TTRPG mechanics are invented on its
behalf.
_Avoid_: hidden freeform mode, implicit ruleset

**Campaign Aggregate**:
The sole authoritative live record of a campaign: its stable identity, lifecycle, construction
phase, exact content bindings, participants, state, accepted Campaign Plan, chronology, sessions,
and Builder-to-GM handoff. Builder creates creative artifacts; deterministic admission makes them
part of the Campaign Aggregate.
_Avoid_: campaign folder as authority, Builder-written prose as authority

**Campaign Workspace**:
The persistent working directory where Builder drafts and validates campaign-local artifacts such
as `campaign-plan.json` and `campaign-plan.md`. Workspace files are not campaign truth merely because
Builder wrote them.
_Avoid_: canonical campaign folder

**Portable Campaign Export**:
The self-contained `.sylvia-gm-campaign` archive used to export and recover either a Draft or a
campaign in play. It captures the Campaign Aggregate and required immutable content; it is not the
mutable live campaign store or a workspace copy.
_Avoid_: live campaign file, Draft-only export format

## Readiness, handoff, and audit

**Semantic Completeness**:
The condition that construction has all genuinely necessary player intent and campaign information
to finish. Adaptive work requires Builder to determine it; the Deterministic Happy Path does not
invoke a model merely to bless readiness. It is distinct from Structural Validation.
_Avoid_: schema validity, mandatory final inference

**Structural Validation**:
The deterministic proof that constructed campaign artifacts are well-formed, mutually compatible,
and technically startable. It can reject an invalid result but does not judge whether Builder
understood enough of the player's intent.
_Avoid_: semantic completeness, player verification

**Start Confirmation**:
The player's spoiler-free decision to begin play after construction is semantically complete and
structurally valid. It is made from a summary of selected content, participants, player-facing
Boundaries, Preferences and Specific Requests, and derived-Plan status. The player may begin, Keep
Building, and, when applicable, save a derived Plan separately for reuse. It confirms readiness to
play rather than the correctness of hidden campaign material.
_Avoid_: campaign verification, plan approval

**Builder-to-GM Handoff**:
The transition from a ready Draft Campaign to a campaign run by the Game Master. It freezes the
admitted content binding and starts the GM with admitted play material and an application-owned
campaign-start event. The Interview Transcript and Builder conversations remain provenance rather
than default GM context. Builder may remain available afterward without competing for live GM
authority.
_Avoid_: closing Builder, transcript transfer

**Provider Input Audit**:
The inspection record of what Sylvia GM actually supplied to a model, with source roles and honestly
identified request layers, byte and hash measurements, token information, provider attempts, and
responses. It is distinct from campaign history and Portable Campaign Export.
_Avoid_: reconstructed prompt claims, unlabeled estimates, campaign backup
