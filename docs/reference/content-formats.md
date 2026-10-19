# Builder content formats

The installed catalog is owned by `domain/content_definitions.py`. Call
`get_content_definition` without a name to list it, then request the applicable
name. Each definition returns its versioned schema, file role, valid example,
invalid example with a correction, and procedural admission requirements.
`section` accepts a JSON Pointer such as `/schema/properties`, `/example`, or
`/requirements`. A section response includes the complete definition's hash.
Read the relevant section instead of truncating a large definition.

## Current inventory

| Definition | Authored content and owning admission boundary |
| --- | --- |
| `ruleset_source` | Compact `ruleset-source.yaml`; Ruleset compiler and draft checker. |
| `package_runtime` | Expanded declarative runtime; shared runtime admission checks its supported effects, references and expressions. |
| `schema_admission_subset` | Admitted schemas for package tools, characters and world information; the supported subset is intentionally bounded. |
| `package_archive` | Package manifest and exact declared files; safe archive admission and package validation. |
| `package_draft_record` | Editable package record; package materialization supplies file identities and validates the result. |
| `package_content_declarations` | Typed content and source boundaries; package reference and ownership checks. |
| `ruleset_tests` | Saved Ruleset test scenarios; their format and actual game-loop test execution remain separate checks. |
| `campaign_setting_knowledge` | Setting facts, secrets and reveal declarations; package and knowledge-reference admission. |
| `setting_character_requirements` | Setting-owned world fields, combined with its contained Ruleset's character requirements. |
| `gameplay_support` | Declared gameplay support and its limitations; package validation. |
| `campaign_plan` | `plan.json`; Plan, contained snapshot, starting-state, network and spatial admission. |
| `portable_plan_bundle_record` | Existing portable Plan record; safe bundle import and exact contained Setting admission. |
| `character_file` | Packaged playable Character File; exact Setting and contained Ruleset declarations, calculations and character rules also apply. |
| `character_profile` | Legacy unbound profile v1; profile check, immutable save and retained-source load. |
| `character_profile_v2` | Rich unbound profile v2; the same profile boundaries plus unique section IDs and safe inert asset references. |
| `inventory_holding` | Authored holdings; quantity, identity, ownership and the selected Ruleset's inventory contract also apply. |
| `panel_declaration` | Package UI surface declaration; package admission checks state/tool references. |
| `panel_layout` | Initial panel layout; layout admission checks placement and surface references. |
| `result_card` | Result presentation metadata; package admission checks tool names and public paths. |
| `spatial_scene` | Structured scene; spatial admission and later party-placement checks. |
| `tactical_grid_source` | Map source declaration; package and Plan spatial admission. |
| `interview_contribution` | Authored questions; owner-kind, question identity and reference checks. |
| `portable_campaign_export` | Existing campaign archive manifest; export/recovery codec, exact closure, excluded private content and integrity checks. |
| `portable_campaign_export_v2` | Existing format-2 campaign archive manifest when saved thinking is explicitly included; requires `provider-private.jsonl` and the existing private-record codec. |

`character_creation` is contextual. In a character or Plan chat, retrieve it from
the saved exact Setting context. It returns participant schemas, complete initial
state schemas, ordered calculations, rule-validation requirements and Setting
world fields. Standalone source Ruleset identity alone cannot establish character
compatibility. A Setting contains its Ruleset snapshot; a Plan contains its Setting.

Setting lore bodies remain ordinary prose. A Setting's structured files and
package records use the definitions above. General workspace files and internal
database rows are not additional product authoring formats.

## Check, publish and repair

Use `check_ruleset_draft`, `check_campaign_setting_draft`,
`check_campaign_plan_draft`, `check_participant_draft`, or
`check_character_profile_draft` for the current artifact. These call the applicable
admission owners, preserve unfinished files, and do not publish or change a live
campaign. Structural success still requires the contextual checks listed in each
definition. Missing inputs or formulas need correction; no character choices or
mechanical defaults are invented.

Publication uses those same owners and exact bindings. A new saved copy does not
update an earlier source, adaptation or campaign. Compatible character reuse is
explicit: validate unchanged character data against the destination Setting and
save a distinct bound copy. Changed data requires explicit adaptation.

Safely readable invalid package and Plan imports can open a separate repair copy
through Fix with Builder. `check_import_repair` and `publish_import_repair` retain
normal admission. Archive safety failures cannot be bypassed by repair.

## Rich unbound profiles

V1 and v2 have distinct published definitions. A v1 source keeps its original
schema, canonical identity and hashes; reading or editing it never silently adds
v2 fields. V2 requires only `format_id`, `format_version`, `name` and `summary`.
Its optional areas are:

| Area | Meaning |
| --- | --- |
| `identity` | Aliases, pronouns and the original identity. |
| `biography`, `relationships` | Description, personality, appearance, voice, original history and addressable relationships. |
| `portrayal` | Scenario, context and portrayal guidance. |
| `greetings` | Default greeting and ordered alternative/group greetings. |
| `dialogue_examples` | Addressable examples with exact text and optional context. |
| `lore` | Addressable entries, original IDs, keys, ordering, enablement and metadata. |
| `metadata` | Creator/source notes, tags, revision, languages, dates and multilingual notes. |
| `assets` | Named inert references; HTTP(S) with a hostname and no credentials, bounded relative `card://` identities, or `asset:sha256:` followed by exactly 64 lowercase hexadecimal digits. |
| `foreign_configuration` | Bounded typed JSON labelled `preserved_only`. |
| `import_source` | Optional exact source provenance, when present; external conversion is a separate workflow. |

Keep supplied text, order and identifiers. Omitted, empty and disabled values have
different meanings. Section IDs must be unique within each addressable collection.
Profiles remain unbound and cannot be placed directly in a party. Adaptation uses
the target Setting and contained Ruleset's normal character tools and checks.

Retrieve exact sections or a lore/dialogue/relationship/asset ID with
`read_character_profile_section`. Use `open_character_profile` for a complete
guarded workspace copy; ordinary version-checked file editing then publishes a
distinct source. JSON source and canonical bytes are bounded to 256 KiB and 32
nesting levels; individual sections and arrays have their declared schema limits.
A section response is bounded to 64 KiB. If larger, request an addressable entry
or open the complete workspace copy. Limits produce errors and never truncate.

Greetings, scenarios and examples are reference material. They are not played
history. Foreign prompts, macros, regex and scripts remain inert; no expansion,
fetch, execution, provider-policy change or tool authority follows from them.
Narrative abilities do not grant mechanical powers.

## Grouped piece tables

A panel can select `renderer: piece_table` with the ordinary `state`, `literal`, or
composed source declaration. Its source resolves to a closed version-1 object:

```json
{
  "format": 1,
  "title": "Relay console",
  "status": [{"label": "Cycle", "value": "3"}],
  "areas": [{
    "id": "relay", "label": "Relay", "position": "center",
    "groups": [{
      "id": "route", "label": "Route", "visibility": "public",
      "pieces": [{"id": "a", "label": "A", "detail": "Online", "emphasis": "accent"}]
    }]
  }]
}
```

Use each of `top`, `left`, `center`, `right`, and `bottom` at most once. Narrow
panels stack areas in declaration order. Each area contains ordered groups, and
each group contains ordered pieces. Status values and all labels are plain text.
Pieces may use `normal`, `accent`, or `muted` emphasis. There is no package HTML,
JavaScript, remote asset loading, or renderer-owned gameplay state.

An owner-only group sets `visibility: owner` and `owner` to an admitted participant
identity. The owner resolves viewing identities through the Ruleset's existing
`model_decision_boundary.participant_control` declaration and canonical participants,
independently of whose turn it is. Missing control declarations reveal no private
pieces. A concealed group exposes its label and, only with `show_count: true`, its
piece count. Neither the hidden pieces nor the original source are sent to the UI.
Public metadata must itself be suitable for all viewers.

A piece may reference `action_id`, the identity of one of this panel's declared
actions. This renders an ordinary action button. An action can declare
`bindings: {selected_piece: piece}` to bind the clicked piece's opaque identity
to its `selected_piece` tool parameter. The owner resolves the identity from the
current permitted table and checks that the piece advertises that action. Hidden,
missing, or differently bound pieces and extra selection fields are rejected.
The selection cannot replace fixed parameters or bypass existing revision,
capability, player-control, or command admission. Availability checks are specific
to the selected piece; the Ruleset still decides whether that move is legal.
Avoid private values in action declarations: declarations are public presentation.
The Ruleset still computes legal actions, scoring and state transitions. Invalid
tables show an unavailable message with no table controls.

Limits are five areas, 16 status values, 64 groups total, 256 pieces per group,
1,024 pieces total, and 256 UTF-8 bytes per label. Piece identities are unique
within their group; area identities and positions are unique within the table.
The synthetic `tests/fixtures/piece-table/signal-console.json` demonstrates a
non-Mahjong composition with private programs and a public route.

## Private collections and public results

`state_schemas` defaults to `visibility: public`. An engine-owned root declares
`visibility: engine` and requires `private_state.v1`. Canonical mechanics and saves
retain it; desktop snapshots, mutation history on the desktop wire, general GM context
and the ordinary current-state reference exclude it. A Ruleset may declare a bounded
panel projection and the existing active-actor decision scope for permitted views.
Fresh GM context includes that permitted decision scope, taken with public state
from the same revision, including when a session resumes before its first action.
Rulesets without private roots retain their existing GM context payload.
The optional `actor_participant_field` binds a seat entity to a canonical participant;
it does not accept a viewer identity from the client.

An explicit `model_decision_boundary.isolated_participant` declaration opts into
`isolated_participants.v1`. It declares `instructions`, actor-relative
`observation_path`, optional `persona_path`, `actions_path`, and a
`public_observation: {namespace, key}` state reference. Paths contain at most eight
identifier segments. Instructions and persona are bounded to 8 KiB each; the
serialized observation envelope is bounded to 128 KiB. The action list contains
at most 256 closed offers `{id, label, operation, arguments}`. Operations and
arguments must match this exact primary package's admitted catalog and schema.

The configured provider receives only that actor's envelope and compatible own
history. Its sole proposal tool selects an offered `action_id`; the host supplies
the actor, turn, package, revision, operation and arguments. Ordinary engine
admission still decides legality. Every public action in an opted-in Ruleset must
explicitly `publish` its public result. GM context receives the declared public
observation and committed published results while retaining campaign dialogue.
Raw offer arguments, private state and participant native continuations stay out
of GM history. Save/reopen retains valid actor-owned history; unavailable private
history starts fresh without falling back to GM messages. Packages without this
opt-in retain ordinary GM and side-scope behavior.

Engine-private action arguments remain canonical in storage for ledger integrity.
Desktop/player snapshots and public replay projections omit those arguments;
this does not alter the saved action, its receipt or its idempotency identity.

`collection_mechanics.v1` provides bounded pure value operations (`indices`, `portion`,
`joined`, `replaced`, `without`, `occurrences`, `one`, `ordered`, `text`, `total`),
exact multiset `partitions` and `completions`, and a `shuffle` effect. Searches consume
the shared execution budget. A shuffle samples a private order independent of public
action IDs, commits that order, and cannot precede a pending `request_choice` in the
same action. Retry and reopen use committed state rather than sampling again.

`public_results.v1` supplies `publish: {fields: {...}}`. It selects the action's public
result context while nested mechanics may retain private calculations. Hidden shuffles
require explicit publication; raw temporary variables must not become results.

## Plan completion

The Plan definition and `BUILD CAMPAIGN PLAN` reference preserve FC-15 semantics.
Every objective linked to a finite act is required, and every condition within
each objective must be met by played facts. An authored alternative belongs inside
the condition it can satisfy. Include an observable closing event, such as the
named keeper receiving the survivors and responding to the rescue report.

Keep optional activities in side content or situations. They cannot replace the
main objective or add indefinite ending gates. Author concrete failure outcomes
and bounded immediate aftermath. The optional after-story follows the player's
Finish choice. Structurally valid text can still be vague; schema checks do not
replace the GM judgement and progression journeys.
