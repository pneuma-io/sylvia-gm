---
name: BUILD RULESET
description: Create or revise an installable Sylvia GM Ruleset from the user's game rules, validate the package, and report actual behavior-test coverage.
---

# BUILD RULESET

Use this workflow when the user wants to create, convert, or revise the mechanical Ruleset at the
base of a Sylvia campaign. Begin from their natural request. Do not require a form or ask them to
choose a workflow they have already entered.

A Ruleset package is the executable mechanical contract beneath Campaign Setting and Campaign
Plan. It declares state, legal actions, deterministic procedures, participant artifacts, content,
and presentation surfaces. It is not a prose summary of the game. The user's existing rules source
remains canonical when they have one; package files are an implementation of that source, not a
second design authority.

## 1. Establish authority before translating

Identify the sources the user supplied or explicitly authorized. Record their exact identities,
versions or revisions, and stated reading order. Follow repository instructions and source-local
authority rules. Prefer canonical atomic sources over summaries, navigation files, examples, or
prior model knowledge.

Ask only when a missing answer would materially change play or the package boundary. Do not ask a
fixed questionnaire. Do not silently resolve contradictions, invent an absent mechanic, replace an
explicitly unresolved choice, or redesign a rule merely because the runtime cannot yet express it.
Permitted research may locate public technical documentation; it may not substitute a convenient
third-party rules account for the user's stated authority.

## 2. Build a mechanic-family and support inventory

Inventory only what is needed to implement and demonstrate the Ruleset. Group ordinary content under
reusable mechanic families instead of turning each spell, item, move, or ability into a bespoke
operation. For every family, track its source locator, required state, genuine choices, GM judgments,
procedure, outputs, timing, persistence, UI needs, and representative conformance case. Cover, when
present:

- actors, groups, ownership, decision scopes, and turn or phase structure;
- attributes, resources, conditions, inventories, relationships, and other persistent state;
- checks, randomizers, tables, modifiers, branching, and success or failure procedures;
- actions, reactions, interrupts, costs, targets, prerequisites, and effect ordering;
- maps, positions, zones, visibility, paths, or other spatial requirements;
- content records and references supplied by Campaign Settings or campaign state;
- participant-owned artifacts and their schemas;
- player-visible surfaces, dynamic action intents, private projections, and GM-only information;
- save, reopen, replay, migration, and provenance requirements.

For every applicable content row, keep Reference Availability (`included` or `excluded`) separate
from Gameplay Resolution (`not_applicable`, `engine_family`, `engine_specialized`, `hybrid`,
`gm_adjudicated`, or `unsupported`). `gm_adjudicated` is an honest playable result, not a euphemism
for unfinished work. Do not put `pending`, test identities, or development milestones into installed
support claims. Keep the inventory compact and source-linked; do not copy the entire source corpus
into it.

## 3. Map rules to neutral Sylvia primitives

Compose the package from existing declarative primitives: namespaced state, bounded reads and
writes, arithmetic, deterministic dice and tables, closed branches, procedures, resources,
conditions, timing, participant artifacts, content declarations, package tools, and UI surfaces.
Use a shared engine primitive only for the neutral fact it owns; the package still owns the rule's
meaning and consequences. Keep public parameters and state shapes closed enough to reject malformed
calls. Preserve stable IDs and explicit ordering wherever replay could otherwise differ.

Do not turn a familiar game's vocabulary or data shape into a universal engine assumption. Do not
embed executable plugins, arbitrary code, network access, or filesystem authority in package data.
Do not encode a model-supplied answer as deterministic truth when owner-authoritative state or an
engine query must decide it.

When an honest mapping is impossible, stop only that mechanic slice and report the smallest missing
generic capability with its required inputs, physical or logical facts, deterministic result,
validation bounds, and replay needs. Ask whether the user wants to defer that slice, use explicit GM
judgment, change the design, or schedule core work. Never approximate it silently and never publish
the package as complete while calling the slice implemented. Report this outcome with the stable
status code `missing_engine_capability`.

Before reporting a missing capability, inspect the current package schemas and built-in adapters.
In particular, Sylvia provides neutral coordinate maps and deterministic spatial queries, a
package-driven `tactical_grid` surface, package-owned actor or side decision scopes, and a
privacy-filtered `network_graph` surface. A durable network topology belongs in package state;
discovery changes its visible projection rather than creating nodes. Bind a visible graph node to a
visible tactical entity with the renderer's physical-identity reference. The host supplies display,
selection, cross-surface navigation, validation, and the ordinary owner commit path; the Ruleset
supplies every game-specific movement, firearm, intrusion, privilege, detection, and remote-function
rule.

## 4. Author readable compact source

Use this ordinary workspace layout:

```text
ruleset-draft/
  ruleset-source.yaml
```

Author identity, participant shapes, game-specific definitions, mechanic families, public actions,
private mechanics, genuine decisions, honest support claims, and an optional compact operating guide.
For new character creation, each participant declaration needs both `entity_state_schema` and
`initial_state_calculations`. Define the complete starting data, then distinguish authored choices
from calculated outputs. Calculations use ordered `set` records with a declared field and
an expression, plus an optional condition; expressions read only `participant`, `state`, and
`setting_data` (world-specific character choices declared by the current Setting). Mechanical
effects of those choices stay in the contained Ruleset's formulas.
Results must match the target's declared JSON type. Parent and child targets cannot overlap.
An empty calculation array is valid when the Ruleset deliberately makes every starting field
authored. Missing required inputs are errors, never defaults. A Setting owns its characters and
uses this declaration from its contained Ruleset. Retrieve `get_content_definition` with name
`ruleset_source` for the exact declaration shape, and use `check_ruleset_draft` before publication.
For each parameter, declare whether its authority is `choice`, `gm_judgment`, or `state`. Use
`from_state` for state-owned facts so they are resolved from fresh authority and omitted from the
model interface. Put spatial queries inside family effects; Builder derives the corresponding engine
feature requirement. Do not ask the user to supply package hashes, runtime contracts, archive fields,
manifest tool declarations, content roots, or generated content declarations.

For game panels, add optional `ui_surfaces` entries with `id`, `capability`, `renderer`, and `source`.
The compiler supplies a title from the ID, a neutral icon, source order, 180px default height, 96px
minimum height, no group or actions, and docked placement with both docked and window placement
allowed. Override those fields when the Ruleset needs different presentation. Existing renderer
and source schemas still apply; declaring a panel does not create its data.

An optional `panel_layout` selects the Ruleset's starting arrangement. Use exactly `version: 1`,
`sidebar`, `windows`, and `hidden`. A tree is either a tab group (`kind: tabs`, unique `id`, `panels`,
`active`) or a split (`kind: split`, unique `id`, `axis: row` or `column`, `ratio` from 0.1 to 0.9,
`first`, `second`). The sidebar may be null. Each window has a unique `id`, `title`, and nonempty
`tree`; `hidden` is a list of panel IDs. Reference only this Ruleset's declared panels, place each
at most once, and respect its allowed placements. Omitted panels appear in the sidebar. Players
can change and save the arrangement. Compilation emits the layout artifact and host requirement;
do not write generated files or package UI code.

The compact source is the normal human- and agent-maintained artifact. Sylvia deterministically
compiles it into runtime YAML, a manifest-owned public tool surface, compatibility declarations,
support declarations, hashes, and archive layout. Do not edit generated runtime or archive bytes.

For a deliberately small Ruleset or advanced low-level control, the guarded direct-runtime layout
(`package.json` plus `files/runtime/ruleset.yaml`) remains available. Treat it as the explicit
advanced path, not the entry requirement for ordinary creation.

## 5. Author, run and correct saved behavior tests

Call `check_ruleset_draft` to check the current source, package, references and runtime before
publication. It uses installation's validators and preserves the unfinished draft without saving
a candidate or changing a campaign. When validation returns structured diagnostics, use its stable code, file, and YAML/JSON
path to correct the compact source and retry. Ask the user only when the failure exposes a genuine
missing rule or design choice; do not ask them to translate generated package fields.

Retrieve the `ruleset_tests` content definition before writing tests. Save scenarios in the compact
source's `tests` field. For the advanced descriptor format, use `files/tests/scenarios.json` and
include that path in content declarations. Author initial state, scripted GM tool/narration replies,
individual die faces, and expected outcomes. Scenarios use the real campaign loop in disposable
storage; they do not call an external model or evaluate prose quality. Use request expectations to
check when narration is withheld or control returns to the player.
After a direct `player_action` step, use `player_action_response` to test the normal GM response
to its saved result. That step takes replies and expectations, without a player message.

Call `test_ruleset_draft` during ordinary authoring and after relevant edits. Check meaningful
outcomes for public actions, important rejected inputs and preconditions, and sequences where
actions affect each other. A `covers` label alone proves nothing. Assert actual state or result
values, not only that an action committed. Use `tool_results` to assert rejected operations.
Controlled dice are individual faces in consumption order, not a random seed. Unexpected requests
or unused replies/dice fail the scenario.

Read the returned status, coverage gaps and failing step's expected/actual values. Correct the
Ruleset when it differs from the agreed rule. Change an expectation only when the rule establishes
that the expectation was wrong; explain the correction. Never change expectations merely to pass.
Keep unfinished drafts and report missing tests, cancellation or execution errors as unfinished.
Existing content without saved tests remains usable, but new/revised Builder candidates need tests.

When the draft is usable, call `publish_ruleset_candidate`. Sylvia deterministically materializes
and pre-admits the archive, runs package/runtime validation and saved tests against that exact
candidate, checks coverage, then records a non-active
candidate, and writes the installable artifact at:

```text
ruleset-draft/output/candidate-<closure-sha256>.sylvia-gm-package
```

Never edit that generated archive by hand. Repeating publication for unchanged inputs must return
the same closure and artifact bytes. Publication does not activate the candidate, replace a live
campaign Ruleset, or prove a mechanic that remained blocked.

## 6. Report results and hand off installed playtesting

Report:

- candidate identity, package identity/version, closure hash, and relative artifact path;
- authoritative source identities and provenance used;
- package validation results, behavior cases actually run, and cases still not run; include dice,
  results, and repeat-action evidence only when they were produced by the testing tool;
- Reference Availability and Gameplay Resolution counts, including every `unsupported` row;
- each missing engine capability or unresolved design choice without invented behavior;
- the explicit next action required to select the candidate during campaign setup or other
  authorized activation flow.

Give the player or a separate tester a short installed smoke-test handoff (a representative campaign
journey): select the candidate explicitly, create the test participant, run representative actions,
save, close, reopen, and repeat an action with the same identity to check that its effects are not
applied twice. Ask the tester to check saved state, selected package, chronology, and visible results.
Builder does not operate that installed campaign or activate the candidate. Report installed play
as unverified until the tester provides evidence, and attribute that evidence to the tester. Missing
agent-accessible execution support remains a testing gap; do not claim the Ruleset is fully tested
because publication succeeded.
