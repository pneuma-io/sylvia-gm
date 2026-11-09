---
name: BUILD CAMPAIGN PLAN
description: Author and revise runnable Campaign Plans, from single encounters to long campaigns, using the selected Setting and Ruleset; validate and save their complete preparation.
---

# BUILD CAMPAIGN PLAN

Use this skill naturally when a general Builder conversation becomes campaign-planning work. The
user does not need to select a workflow first. Ask only questions whose answers materially change
the campaign: desired experience, tone and boundaries, length, preparation detail, and how firm or
open the planned structure should be.

## Author a runnable adventure

Assume Sylvia's file format is unfamiliar. Retrieve `get_content_definition` with
`name: campaign_plan` before authoring; its installed schema owns the accepted fields and types.
Use this skill for the workflow and the worked examples for how complete preparation fits those
fields. A scaffold provides valid containers; finish their campaign content before saving it as a
completed adventure.

For a first Plan or an unfamiliar format, read
[the worked-example walkthrough](references/worked-examples.md). In Builder, retrieve it using
`load_builder_skill` with `name: BUILD CAMPAIGN PLAN` and
`reference: references/worked-examples.md`. The same tool returns the available reference paths.
Read the paired JSON and Markdown example for the closest length. These are actual authored
Severed Lands examples; reuse their structure and reasoning, then resolve the current selected
sources and their exact bindings. Their D&D mechanics and story facts belong to their own Setting.
Repository authors can read the same files directly and use the repository validators instead of
pretending Builder-only tools are present.

For repository validation, `domain.campaign_plan.validate_campaign_plan` checks the structured
Plan against exact package bindings and existing Setting paths;
`application.plan_snapshot.validate_plan_snapshot` checks a Plan with its retained package archive. Required characters also
need `application.reusable_participants.parse_setting_character` against the admitted participant
declaration. Read the live signatures and the selected package manifest for schema identities.
The Severed Lands collection script demonstrates those calls and export/import without using a
personal library. Source inspection helpers may expose fewer fields than Builder's identity tool;
retrieve missing declarations from the admitted manifest instead of guessing them.

Build the preparation around decisions the player can make:

1. **Entry and scope.** Establish who can play, why the situation can involve them, the immediate
   location and pressure, and what concludes this adventure. Accept compatible characters without
   assigning them unchosen personal history. Put an essential authored past in an explicit fixed
   Character File. A recommended level is guidance; party compatibility does not enforce level or
   guarantee encounter balance.
2. **Truth and people.** Decide what actually happened, who knows, what each important person wants,
   what they will offer or conceal, and what changes their mind. Author original local people inside
   the Plan's situations/facts, or add Setting files when reusable world entries are needed. A
   typed `setting_references` entry always names a real file, never an invented one.
3. **Playable situations.** Give the GM concrete places, observable information, motives, obstacles,
   useful approaches and consequences. For a mystery, prepare the answer and ways to discover it;
   a failed check should change cost or exposure while leaving a believable route forward. For a
   fight, prepare opposition, terrain, objectives, tactics, withdrawal and outcomes. Exact attacks,
   damage and resource changes go through the selected Ruleset's tools.
4. **Acts and continuity.** Each act should change the situation, not repeat a premise at greater
   length. Carry specific promises, evidence, losses and alliances into later acts. Branches describe
   meaningful consequences of choices; allow early discoveries and alternate routes. Acts are
   private preparation, not a timetable forcing player actions or deciding their success.
5. **Resolution.** Write success, compromise, withdrawal and failure outcomes appropriate to the
   premise, including the immediate human aftermath. A one-shot or combat scenario ends there;
   continued play is offered only after resolution and requires the player's interest. A long
   campaign supports breathing room and optional side stories without requiring every prepared lead.

Use a short scenario's small scope to make it quick. One combat needs an orientation, the encounter
and its aftermath; two combats need a connection and consequences carried from the first. A short
one-shot concentrates on one problem. Two or three acts warrant changing relationships and stakes.
A six-act campaign warrants sustained development, regional variety and consequences that survive
detours. These are authoring choices, not fixed word counts, session lengths or token budgets.

### Define the main story and exact act endings

For every finite act, author its named main problem, required discoveries/events and exact closing event.
`structure[].objectives` lists the objective IDs required to finish that act. Every linked objective
is required, and every entry in its `completion_conditions` must hold. Express an alternative
explicitly within the condition it satisfies. Put optional activities in `side_content` or the
act's situations; optional work does not substitute for or extend the required ending.

For example, a rescue objective can require all three: "Ira is out of the winch room, alive or
with her death established by play"; "the survivors reach the far bank or the prepared lower
refuge"; "closing event: Keeper Sol receives the survivors and the rescue report, and responds to the
actual outcome." The named places, people and routes must exist in the preparation. Accepting
the job, repairing another rope or assigning the rescue to somebody else does not meet those
conditions. A diplomatic or engineering climax can be equally exact; a boss is not required.

Author concrete failure endings to the same problem. A refusal alone leaves the main objective
unfinished; if leaving ends this adventure, define the actual departure/report consequence and
label that outcome honestly. Keep methods and alliances open while retaining the named story
requirements. Record debts and optional follow-up as carried consequences, not new act gates.
For each ending, compare one unfinished state and one finished state: the required facts must
explain the difference without "enough progress", an inferred scale of undertaking, or a turn count.
Distinguish a promised decision or report from its performed outcome. If disposition is required,
state what is decided and recorded; paying an already recorded debt can remain later follow-up.
For required evidence, name where it is available, who can interpret it, and how any permitted
copy or witness route is reached when direct access is blocked. An alternative mentioned only in
the checklist still needs playable preparation. Keep summaries and reference prose consistent
with the same conditions, including which alternatives satisfy each one.

`create_campaign_plan_draft` requires `completion_conditions` separately from `primary_objective`.
It preserves authored text, not a judgment of its quality. Put the explicit closing event in those
conditions, prepare accessible leads to the main problem, and align the act's prose and branches
with the same ending. Remove contradictory generic handover/withdrawal shortcuts. When expanding
a draft, give every act its own requirements and make the campaign ending match the last act.

### Separate the immediate ending from the after-story

Every newly authored or revised Plan declares `ending`. Choose one form:

- **Finite:** `mode: finite` and one or more `outcomes`. Each has an `id`, concrete `conditions`,
  a concise intended `outcome`, named `immediate_aftermath` resolutions, an `open_world` boolean,
  and `after_story` (a brief longer-future premise, or null). A final in-character decision can
  select an outcome; reaching an authored final boss result can also suffice. Keep the conditions
  consistent with the required final-act objective and actual prepared people/places.
- **Ongoing:** `mode: ongoing` and a `note` stating that the final/only act has no conclusion.
  That act has an empty required-objectives list; earlier finite acts retain their exact endings.
  A one-act ongoing Plan may have no objectives. The note is that act's no-ending footnote.

The immediate ending covers the finale's consequences, reactions, celebrations and the specific
loose ends the Plan names. Give each required loose end an observable resolution; optional
emergent threads may remain for open-world play. Resolve this immediate ending before Sylvia
offers Continue adventuring (when `open_world` is true) and Finish the story. The after-story is
only narrated if the player finishes, and never becomes a required act objective. Author a premise
such as a peaceful life in the adopted home; the active GM develops it into flowing narration
using the friendships, romances and choices that actually occurred. Do not prewrite those details
or replace a story decision with an invitation to choose/decline an epilogue. OOC can override
the intended direction. Continuing play does not automatically append new authored acts.

Check the whole Plan for agreement: objective descriptions, conditions, situations, branches,
Setting prose and the ending must describe the same reachable outcome. Test one unfinished state
and each intended ending. Older accepted snapshots can omit `ending`; new authoring must include it.

### Make the chosen Ruleset matter

Match the adventure's main activities to the requested kind of play. A Setting binding and a few
optional checks do not make a conversation story a satisfying D&D dungeon or combat adventure.
For a combat-forward D&D brief, prepare connected, explorable locations; purposeful opposition
with usable statistics and tactics; obstacles that reward skills, tools and actual character
features; and consequences for expending HP, spells, supplies and recovery resources. Give traps
and hazards warning signs, DCs, success/failure effects and practical alternate approaches. State
where rest is possible, what delay changes, what treasure is recoverable and who owns it.

Place fights in the adventure's main situation, rather than adding an unrelated optional ambush
to a social outline. Let preparation, negotiation and supported magic bypass a fight when earned;
do not respawn opposition to enforce an encounter count. Carry resources, recovered keys, route
knowledge and defeated enemies across acts. Inspect the actual party before scaling opposition;
declare any weakened creature profile explicitly rather than silently changing its full stat block.

This is a fit check against the user's brief, not a rule that every Ruleset or every D&D adventure
must contain combat. Conversation-focused and no-rules campaigns can be appropriate when requested.
When curating a collection, compare actual play loops and scale as well as themes: dungeon recovery,
heist, rescue and wilderness expedition offer different choices. Preserve the flagship's promised
scope instead of giving each shorter adventure another world-ending revelation. Player-facing
copy should name concrete activities the preparation actually supports, without revealing secrets.

### Put each kind of information in its owning field

| Preparation | Campaign Plan field |
| --- | --- |
| Spoiler-free selection pitch | `premise`, `tone`, `intended_player_experience`; the premise can appear in the player's library. |
| What is already true at the first moment | `initial_state.facts`; use fact records with `known_by` to distinguish public and private facts. |
| Private answer or concealed motive | `secrets`, with who knows and a concrete earned reveal condition. |
| Situations, people, clues and approaches | `structure[].situations`, `branches`, `consequences`, `planned_encounters`; use the existing prose fields rather than adding undeclared JSON fields. |
| What progress means | `objectives` with observable completion conditions; reference their IDs from relevant acts. |
| Desired opening presentation | `opening_direction`; establish actual people and danger in starting facts, not only here. |
| Deterministic actors, map or world state at Start | `initial_state.mechanics` and `party_placements`, using only the bound Ruleset's declarations. |
| End and aftermath | `ending` separates intended outcomes, immediate aftermath and optional after-story; `completion_conditions`, `failure_conditions`, `continuation_conditions` agree with it. Failure can conclude a short adventure. |

`plan.json` is the canonical GM preparation. Keep every essential truth, clue, encounter and
resolution in it or in an admitted referenced Setting file. Render the same preparation into
`plan.md` for humans. Extra Markdown-only plot information is not a substitute for the canonical
content. Keep short summaries short and place developed preparation in `situations`; do not copy
the whole act into both its summary and situation text.

Before validation, read the draft as a GM receiving it cold. Can they establish the opening, portray
the cast, answer the mystery, respond to a refusal, resolve its challenges and recognise the ending
without inventing the adventure's missing core? Complete those gaps. Empty optional lists are valid;
placeholder prose and generic instructions to invent the main plot are unfinished preparation.

For a focused example of fixed characters, secrets, combat state and recovery from a rejected draft,
read [authoring variations](references/variations.md) using the same reference tool.

Every played campaign requires a Player Character for Sylvia to GM, including Rulesets where the
player also controls a squad or team. If the brief requests a character-free campaign, explain this
constraint before drafting or promising readiness. Offer the supported route: select or create a
compatible Player Character during New Campaign, or use a complete fixed Setting character.
Check that the selected Ruleset declares the necessary Character schema; explain a missing schema
instead of inventing one. A saved Plan is not a ready-to-play campaign: party selection and the
remaining campaign setup must still be completed.

## Keep the three layers distinct

- The **Ruleset** defines mechanics, participants, state, tools, and ruleset-owned presentation.
- The **Campaign Setting** defines the world and the people, places, factions, creatures, items,
  lore, and other content that can exist within it. It contains its own Ruleset snapshot (saved copy).
- The **Campaign Plan** curates a specific playable campaign from those sources. It references the
  files in its own Setting snapshot, including that Setting's Ruleset copy. Publishing captures
  those exact bytes; later changes to either source leave the saved Plan unchanged.

For a plain Builder chat, explicitly select the requested saved Setting with
`select_builder_setting` before Plan work. Use its `id` as `setting_id` and the exact `candidate_id`
from its save receipt or the Ruleset's discovered `compatible_settings` entry. A bundled entry may
advertise `candidate_id: null`; pass that null to retain its exact bundled snapshot. This works immediately
after saving a Setting in the same chat and turn. An already selected Ruleset must match; an already
selected Setting cannot be replaced. Interview-launched chats keep their original sources and do
not need this selection tool.

Before drafting, read `ruleset/identity.json`, then the Setting entries relevant to the campaign.
Reuse exact source contents already read for this chat's saved selection. To discover missing
paths, call `inspect_campaign_sources` with `action: list` and a focused `query`. Its `total` is
the matching count; follow `next_offset` with the same query when more paths are needed. A null
`next_offset` ends that filtered listing. Start at offset 0 whenever the filter changes.
Read the needed sources before dependent draft changes. For a new plan, prefer
`create_campaign_plan_draft`: give it a concise campaign concept plus the exact `setting/...` player
and starting-location paths returned by source inspection. Sylvia creates the complete closed
artifact and readable draft. Package sources are not workspace files; never use `read_file` or
`search_files` to locate them. Use the larger package-bound example only when directly revising the
structured JSON.

Never invent a package hash, Ruleset capability, Setting path, validation result, or publication
receipt. If requested material is absent, tell the user exactly what is missing. Mark mechanics the
Ruleset does not encode as explicit GM adjudications instead of pretending they are engine features.

## Fixed draft layout

Keep these persistent workspace files:

```text
campaign-plan-draft/
  plan.json
  plan.md
```

`plan.json` is the canonical `sylvia-gm-campaign-plan` version 2 artifact. Follow the example's
closed structure. It must include exact Ruleset, Campaign Setting, and package-closure bindings;
starting-party policy and state; typed Setting references; acts, situations, objectives, branches,
consequences, secrets, side content, continuity guidance, completion/continuation/failure
conditions, and explicit unsupported mechanics. Use stable IDs. Every Setting `path` must exist in
the saved sources or in this Plan's explicit Setting-file additions described below.

### Edit this Plan's inherited copies

In an independent Campaign Plan Builder chat, use `inspect_builder_references` to read the saved
`setting/...` and `ruleset/...` files. Use `copy_builder_reference` to copy a complete source into
the editable draft before changing it. Follow `next_offset` when reading pages; a partial read is
not the whole document. Write only intended replacements or additions under
`campaign-plan-draft/setting-files/<package-relative path>` or
`campaign-plan-draft/ruleset-files/<package-relative path>`. For example,
`setting-files/locations/relay.md` replaces that world entry inside this Plan. Files absent from
these directories retain their saved bytes. Keep declarations consistent with added or removed
artifacts; canonical JSON declaration files require sorted keys, compact separators and no trailing
newline, as in their saved sources.

To remove inherited files, write `campaign-plan-draft/removed-files.json` as an array such as
`["setting/locations/unused.md"]`. Each entry starts with `setting/` or `ruleset/`, followed by an
existing package-relative path. Remove its references too. A file cannot be replaced and removed
in the same draft.

Keep the draft's original three `bindings` values. Publication derives new content hashes after
validating the edited copy, writes the effective bindings to `campaign-plan.json`, and saves all
inherited bytes with the Plan. The editable draft remains relative to its original saved sources;
leave the replacement files available for subsequent revisions. Publishing identical content again
returns the same library Plan. Invalid rules or unresolved references prevent publication.

Use this path for reusable Plan work. Final campaign customization inside a campaign's own Builder
does not replace its already-bound packages; create an independent reusable Plan for inherited-file
changes.

### Adopt a requested source change

When the user asks to bring in source changes, call `inspect_library_sources` with
`action: candidates`, then inspect `files` and `read` for a concrete returned candidate ID. Results
identify exact saved versions; list order does not mean newest. The tool reads installed sources
without changing this chat's launch references or any draft.

Compare the chosen source with the original `inspect_builder_references` content and the current
draft replacements. Apply compatible requested changes while retaining customizations. Ask the user
which intent to keep when source and custom rules conflict. Use ordinary guarded writes, then
publish the edited Plan. To adopt the complete chosen source, write
`campaign-plan-draft/ruleset-source.json` or `campaign-plan-draft/setting-source.json` with exactly
`{"candidate_id": "<returned ID>", "expected_closure_sha256": "<returned closure hash>"}`.
The logical Ruleset and Setting IDs must stay the same. Publication copies the selected source and
all its dependencies, then applies this draft's local edits. If both source files exist, the Setting
copy is selected first, followed by the Ruleset copy. Reconcile existing replacement files with the
incoming source before publishing: a complete local file replacement would otherwise hide changes
to that file in the source.

For tool, character-schema, presentation or dependency changes, call `inspect_library_sources` with
`action: record`, an exact `candidate_id`, and `path: ruleset`, `setting`, or
`dependencies/<package ID>` when you need a bounded preview. Use `copy_library_source` with
`source_type: editable_record` to copy that package prefix directly to
`campaign-plan-draft/package-records/<package ID>.json`. Supply the exact candidate and closure hash.
For runtime or other file changes, use `source_type: file` with an exact path from `files` and the
appropriate draft replacement path. Search the owned copy with `search_files`, read the relevant
section, then edit with its current file version. Existing destinations require reconciliation and
their observed version before replacement. Use the original candidate for a local
edit or the explicitly selected source for an update. Keep `id`, `role` and `content_kind` fixed;
versions and declarations may change. Do not supply computed file or package hashes. When editing
a participant schema, remove its old `schema_sha256` so publication computes the new value.

Every declared dependency must be present in the selected complete copy. Unchanged dependency
references follow an edited package's new version; explicit mismatched references reject. Unused
support packages are omitted. Keep runtime files, content declarations and records consistent;
admission checks their agreement before the Plan becomes selectable.

### Starting state, party and opening

For an authored map or other starting game state, add `initial_state.mechanics`: a list of
`{"namespace": "<exact Ruleset ID or its subnamespace>", "key": "<state root>", "value": ...}`
entries. Read the selected Ruleset's state and map declarations through `inspect_builder_references`
before choosing these keys or values. The `entities` and `facts` fields describe narrative starting
conditions; `mechanics` supplies the actual game state. Campaign Start copies these entries and adds
the chosen party at the Ruleset's declared character location, retaining world actors and adjacent
state. Keep party identities distinct from authored world actors. Omit `mechanics` when no mechanical
starting world is needed; prose does not create a playable map.

Each `initial_state.entities` entry has exactly `id`, `kind`, `source_ref`, and `state`.
`source_ref` is an object with `id` and `path`, for example
`{"id": "operator", "path": "characters/operator.json"}`. Use an actual Setting file and its
package-relative path: the `setting/` prefix belongs to source inspection, not the saved reference.
`state` is an object containing that entity's narrative starting data. The entities list may be
empty. Authored mechanical actors still need the selected Ruleset's state entries in `mechanics`.

For a selectable party on that map, add `initial_state.party_placements` with one slot per possible
party member, ordered Player Character first, then Companions in selection order. Start uses only the
selected slots; too few slots rejects. Each slot has exactly `entity_state` (complete top-level field
replacements in that character's copied game state) and `append` (a list of additions to already
authored state lists). An append has `namespace`, `key` (a dot path beginning at a state root), and
`value`. For example, a Ruleset may place its map token in `state.spatial_scene.entities` and add its
ID to `state.eligible_actor_ids`; create those lists in `initial_state.mechanics` first.

Use `{"$participant": "id"}` or `{"$participant": "name"}` as an entire value wherever a slot needs
the campaign-assigned identity or selected character's display name. Do not invent that ID. Supply
complete map tokens, positions and other state according to the chosen Ruleset. Sylvia substitutes
those two references, applies the fields, and appends the entries; it does not infer tactical
placement or translate rules. Existing world entries remain. Duplicate identities, missing target
lists and foreign state namespaces reject before Start commits.

The starting-party policy says whether the one player character is `compatible` with the Ruleset
and Setting or is a `fixed` Setting character, lists any exact required companions, and gives the
minimum and maximum number of additional custom companions. Required companions do not count
toward those custom limits. Do not encode these choices as prose requirements.

For a fixed Player Character or required Companion, inspect the exact Setting file before selecting
its reference. Generic campaign setup needs a complete `sylvia-gm-character-file` version 2 JSON
artifact with its authored profile, Ruleset-compatible participant data, and mechanical state.
A prose-only biography or an unfinished file remains unavailable. The bundled Rift setup keeps its
own admitted character format. Publishing a Plan validates its references but does not make an
incomplete required character playable; report that missing work before calling the campaign ready.

When completing required characters from party selection, revise the saved Plan instead of creating
a new story. Use `copy_builder_reference` to copy `campaign-plan/plan.json` and
`campaign-plan/plan.md` to `campaign-plan-draft/plan.json` and `campaign-plan-draft/plan.md`.
Read an existing draft and reconcile its edits before replacing it with an observed version.
Keep unchanged Plan content intact with focused `edit_file` changes; do not recreate its structure
from a paged reference or call the new-Plan scaffold. Replace each incomplete character under
`campaign-plan-draft/setting-files/<its existing path>`. Its JSON has exactly `format_id`
(`sylvia-gm-character-file`), `format_version` (2), `id` (matching the Plan reference), `schema`
(`id` and `content_sha256` from the Ruleset's participant declaration), `summary`, `authored_profile`
(a non-empty text string containing the readable character profile; Markdown is allowed),
`participant` (Ruleset-schema data), `entity_state` (complete starting data, including the Ruleset's
calculated outputs), and `setting_data` (an object satisfying the Setting's world requirements).
Read `setting/character-requirements.json` if present; otherwise use empty `setting_data`.
Older version-1 files remain readable and have no `setting_data`; they must still satisfy the exact
Setting and contained Ruleset before selection.
Retrieve `get_content_definition` with `name: character_file` for the envelope and
`name: character_creation` for this chat's saved Setting and Ruleset requirements. Use
`check_participant_draft` with `role: player_character` or `role: companion` to check authored inputs
and obtain calculated starting values without saving a separate library character. Write the
returned complete state into the required Character File. These checks use the saved source
declarations; if the Plan draft changes its contained Ruleset or Setting requirements, reconcile the
character files with those edited declarations. `check_campaign_plan_draft` checks the actual edited
copies and reports whether required characters satisfy them before publishing the Plan.
Read `inspect_campaign_sources` with `action: read` and `path: ruleset/identity.json` for the
current authoring source's `participant_artifacts`, including each exact schema and `schema_sha256`.
Use that hash as the Character File's `schema.content_sha256`; do not guess or omit it. Its top-level
`id` must match the Plan's required reference even when the Ruleset's internal actor ID differs.
Completion launched from party selection rejects publication until those required files pass the
same format and participant checks as the Interview. Ordinary Plan authoring may still save a Plan
with unfinished required characters. Keep the character identity consistent across references and
avoid placing a required party member twice as a separate world actor. Publish the revised Plan
and tell the player to select it on return. A standalone optional character save does not update
these required files. Changed Setting or Ruleset copies can make previously saved custom characters
incompatible; report that need for explicit adaptation rather than claiming all party choices remain ready.

`opening_direction` is optional private creative direction for the opening exchange only. It does
not establish facts, force a scene, or override the Ruleset. Use the user's own preference when one
was given; otherwise omission is valid. Useful forms include a quiet character-led introduction,
in-media-res danger, a shared job or duty, witnessing a catastrophe, a chain introduction, a social
occasion, a mystery or discovery, an open-world arrival, or a cinematic establishing scene. These
forms can be mixed. Describe the desired effect and pacing, leaving the GM freedom to invent how it
happens from the admitted party, initial state, and Setting.

`plan.md` is the readable GM rendering. It may be detailed and pleasant to read, but it is not a
validation bypass and cannot replace `plan.json`.

## Validate and publish

After writing both files, call `check_campaign_plan_draft` with no arguments. It checks the Plan,
its edited Setting and Ruleset copies, references and runtime without publishing anything. Read
`required_characters_ready` separately: a valid ordinary Plan draft can still contain unfinished
required characters. A chat launched to complete those characters must finish them before its check
and publication succeed. Correct reported file/field errors while retaining the draft.

When the user wants the finished Plan saved to the library, call `publish_campaign_plan` with no
arguments. If it rejects the draft,
inspect the files and source catalog, correct the problem, and call it again. A successful result
creates a selectable Builder artifact; it does not start a campaign, activate packages, or mutate
gameplay. The saved Plan includes its Setting and Ruleset snapshots. Players can export it as a
`.sylvia-gm-plan` file and import that file into another library without installing those sources
separately. Keep both draft files available for later revision.

When reporting success, name the saved Campaign Plan and state any remaining character or setup
requirements. Describe it as ready to select, not proof that its campaign is ready to play.
Keep hashes, package-lock internals and raw validation payloads out of ordinary chat.
