# Sylvia GM Alpha Launch Contract

> **Status:** Active product authority
>
> **Purpose:** Define the complete promise and product boundary for Sylvia GM's first public Alpha.

This contract is authoritative as written. Decision provenance is retained separately in
[`../evidence/decision-ledger-provenance.md`](../evidence/decision-ledger-provenance.md) and
does not make individual sentences here conditionally authoritative.

## 1. Product and release identity

Sylvia GM is an AI game-running platform.

On 16 October 2026, Daniel Young, operating under the Pneuma IO brand, adopted standard
PolyForm Noncommercial 1.0.0 with Sylvia Additional Permissions revision 3 for material he is
authorised to license, with the stated scope and exclusions. See [licensing status](../../LICENSING-STATUS.md).
Prior grants and third-party terms remain preserved. Official publication and release acceptance
remain separate project decisions.
Optional monetization may be explored after launch but is not required for Alpha readiness.

The current scope is AI-driven roleplaying games: the player roleplays a Player Character and
Sylvia acts as Game Master, interpreting open-ended player instructions and deciding how play
continues. A Ruleset may let that player control an entire squad or team during combat, sport,
or another mode; this does not remove the roleplaying relationship or Player Character requirement.
General wargames without a roleplaying component are outside the current scope. Builder must
explain the Player Character requirement when a requested campaign omits one, before claiming
that the requested campaign is ready.

**Sylvia GM** is the software/product name. **Sylvia** is the GM persona presented in the
play experience. Pneuma IO is recorded only where ordinary publisher or company metadata is
needed; it is not a required tagline, splash treatment, or emphasized co-brand.

The first promoted release is honestly identified as an **Alpha**. The label permits bounded
scope, not a broken advertised experience. Every advertised Alpha journey must work reliably
end to end.

The public Alpha supports the Windows 11 desktop application through an unsigned NSIS installer
and an unsigned portable EXE ZIP. The ZIP uses adjacent local data and does not require installation.
Both formats need exact-candidate qualification before publication. Windows 10 qualification,
installer formats other than NSIS, native macOS/Linux clients and cloud-hosted play are post-Alpha.

Sylvia GM never displays estimated monetary cost. Any future in-house subscription usage belongs
only in a dedicated Usage Settings surface, never Builder or DM conversation windows. Local or
BYOK operation never fabricates subscription consumption.

The desktop Models settings surface manages multiple saved provider configurations without manual
file editing or restart. Alpha includes convenient presets for OpenAI API keys, OpenCode Go and
Zen, OpenRouter, Featherless, LM Studio, Ollama, llama.cpp server, vLLM, and LocalAI. A first-class
Custom OpenAI-Compatible option accepts hosted, LAN, or localhost endpoints, an optional API key,
transport, model discovery or manual model ID, context override, and optional advanced headers.
Named presets are conveniences rather than a provider allowlist. PES-150 extends that convenience
to Groq, Together AI and DeepSeek through existing supported transports. Normal setup is choose
service, connect, select model and save. Prefer service/runtime metadata, then documented exact
service/model defaults for missing facts; never borrow another route's limits or imply account
availability from a static catalogue. Keep optional manual caps, a return to automatic values,
and clear inline requests for genuinely missing facts. Model maximum output and an optional
reply budget are distinct; lower reported runtime/plan limits still apply. Finding or saving a
model does not run inference, select it for an existing chat or change the global default.

Sylvia connects to already-running local inference servers. Alpha does not download models,
install inference runtimes, or start and manage their processes. Local detection checks only a
bounded set of known loopback endpoints; manual URLs remain available and Sylvia never performs a
broad LAN scan. Provider credentials and private headers remain owner-local and outside campaign
data, canonical history, backups, logs, prompts, exports, and renderer projections.

Saved API keys and private request headers use Windows protection tied to the current user.
Existing local plaintext settings migrate without losing their usable configuration on failure;
unavailable protected values require clear re-entry/recovery instead of silent unauthenticated
requests. This protects stored settings within Windows' user protection boundary, not against
malicious software already running as that user. LRD-07 owns the implementation and acceptance;
this is the selected Alpha requirement, not a claim that migration has already shipped.

One global default provider/model applies to new Builder and GM sessions. Existing sessions retain
their exact selection. The configured-model picker and `/model` command may switch the current
idle session while preserving history; `--once` applies to exactly the next provider turn and is
consumed after success, failure, or interruption, while `--global` also updates the default. A
model cannot change during active generation.

Reasoning effort is selectable beside the model in GM and Builder. The mode defaults are GPT High
for GM and X-High for Builder, with documented closest supported native equivalents for other
models/providers. Offer all supported levels, show the actual resolved setting, and clearly label
provider-managed behavior where no control is available or support is unknown. A saved per-chat
preference overrides the mode default; a next-reply-only override follows the existing one-turn
selection lifecycle. Effort is separate from output/context limits and does not authorize changing
the model, subscription/service tier, or token allowance. Chats without an explicit preference use
the mode default on future replies, including existing chats; do not rewrite or replay history.
Preserve valid explicit preferences and make unsupported selections actionable rather than
silently ignoring them. This is AR-20's selected requirement, not an implemented-capability claim.

The ChatGPT account connection is implemented through the owner-side Codex app-server route.
The earlier post-Alpha/planning-only description is historical: PES-82 selected implementation
while preserving Sylvia's tool and content authority. Capture the subscription-runtime audit
data that is available and label downstream details the runtime does not expose. Account access,
current provider compatibility and exact-candidate acceptance remain separate from implementation.
OpenAI API-key authentication remains supported. Additional provider sign-ins need their own
supported route; undocumented subscription-token extraction is not selected.

### 1.1 Release gates

The durable roadmap is `alpha-03` through `alpha-10b` in the active plan:

- `alpha-03` through `alpha-07` and `alpha-09` implement and qualify the product;
  `alpha-08` is retained only as the retired original-ruleset slot.
- **`alpha-10a-release-ready`** is the autonomous terminal gate: locally committed,
  independently reviewed, provenance-complete, and ready to publish.
- **`alpha-10b-publication`** is the actual public Alpha release through GitHub Releases.
  It runs only after direct publication authorization.

A playable preview, local executable, or release-ready candidate is not the released Alpha.
The autonomous task may complete `alpha-10a`; it must not claim `alpha-10b` or public Alpha
completion before publication succeeds.

## 2. Alpha play promises

Alpha supports these distinct play proofs:

1. **SRD Lite with playable authoritative mechanics** — the launch Ruleset uses the SRD 5.2.1
   core with a reduced spell list of 117 supported spells, without consumables. Consumables belong
   to future SRD Full. Exact source bytes, licences, attribution, catalogue scope and package
   identities remain explicit. Fixed rules for the offered Lite roster remain pre-release scope;
   the separately preserved inactive Full suite is not a launch gate. Structured spell and actor
   data drive reusable
   package checks, saves, attacks, damage, healing, resources, conditions, timing and state effects,
   including later consequences. The GM chooses intent and supplies situational facts; the engine
   resolves fixed mechanics. This does not require a bespoke resolver or test journey per spell.
   Inherited Sylvia GM/DSV4 content is comparison evidence until traced.
2. **No Ruleset** — a playable model-plus-database baseline with no invented TTRPG, combat,
   character-class, HP, ability, or fantasy assumptions.
3. **Severed Lands** — the bundled D&D Campaign Setting and selected Campaign Plans, including
   Rift Awakening, use the supported construction, play, progression and recovery journeys below.

Trigger Discipline and Siren Protocol are later work and are not Alpha blockers. This supersedes
their 2026-09-23 pre-release component selection. Their retained design below does not require
Alpha implementation or acceptance, and unsettled rules/defaults remain explicit decisions.
Cairn 2e may remain experimental development material but is not advertised Alpha scope.
An original bundled non-D&D ruleset is not required for Alpha. Earlier Dating Sim and gunplay-focused
concepts remain possible post-Alpha design candidates and do not direct implementation until a
dedicated PRD is approved and promoted.

Player-visible resolved actions may use an open, Ruleset-declared result-card system at their
chronological point in play. The Ruleset defines each card's purpose, labels, initial information,
expanded information, visibility, and any action buttons; cards do not require dice or a fixed set
of summary fields. Sylvia supplies the common layout and renders committed engine results without
rerolling or calculating a new outcome. A reopened card retains its original result and presentation
even when the current Ruleset or character later changes. An action on an old card is revalidated
against current state through the shared Ruleset action path. Missing or invalid presentation falls
back to a basic completed-or-rejected operation display without inferring gameplay success. D&D SRD
does not adopt card buttons in this scope. Outcomes remain readable without relying on color.

Rulesets select an inline result or compact card, its information, and its visual emphasis. Sylvia
supplies consistent typography, spacing, and interaction. Inline results occupy a quiet text line;
compact cards have a bounded width and expandable detail rather than a stretched label/value table.
The approved D&D ability-check presentation shows the check name/type, actual die result in its die
shape, all modifiers combined into one signed value, final total, outcome, and public difficulty.
For example, a D20 showing `15`, combined modifier `+0`, and total `15` remain visible together.
Calculation expands to recorded dice and contributing modifiers; individual zero modifiers are
omitted while a combined `+0` remains meaningful. Character identification uses names when useful,
and redundant internal labels such as `Resolution: Rolled` are omitted. Dice animation is a later
enhancement, not part of this presentation correction. These D&D choices do not impose dice or
fixed fields on other Rulesets.

### 2.1 Guided New Campaign construction

Choosing **NEW CAMPAIGN** immediately creates one visible, resumable Draft Campaign and opens its
Guided Builder Chat. The Draft is saved before the interface advances and begins without an
invented player message, provider inference, or content binding. There is one guided journey rather
than separate Quick Start and Customize lanes.

The Campaign Interview chooses content through one inheritance chain: Ruleset, including explicit
**No Ruleset**; a Campaign Setting descended from that Ruleset; and a Campaign Plan descended from
that Setting. Choosing an existing Setting or Plan uses the complete inherited content saved inside
it and does not require the parent sources to be installed separately. The Interview also chooses an
editable suggested name; the participants permitted or required by that Plan's Starting Party Policy;
global Boundaries and Preferences; and Specific Requests, including **No additional requests**.
Each content selector remains visible: zero compatible required choices offers Create New and Back,
one is suggested but still requires confirmation, and several require a choice. Optional
participants may be skipped.

The launch app includes all 16 selected Severed Lands D&D Campaign Plans in the Plan chooser by
default, without a separate download or manual import: six combat scenarios, four one-shots,
three two-act adventures, two three-act adventures and the six-act Rift Awakening. The shorter
adventures use D&D exploration, combat and character resources, while Rift retains the flagship
isekai scope. Their authored Starting Party Policies remain authoritative, including Tavin Bell
as the fixed protagonist of A Debt in Iron. The Interview supports
browsing this collection alongside compatible imported or locally authored Plans using public
premises, scope and character requirements; private GM preparation is never browsing copy.

Home also offers **Ready campaigns** for four existing adventures with complete prepared parties:
A Debt in Iron uses Tavin Bell (Fighter 1) and Oren Reed (Cleric 1); Last Cart Through uses
Bram Alder (Fighter 2) and Oren Reed (Cleric 2); The Cartographer's Wake uses Pip Venn
(Rogue 2), Bram Alder (Fighter 2) and Oren Reed (Cleric 2); The Rift Awakening uses Neri Vale
(Bard 1), Bram Alder (Fighter 1) and Oren Reed (Cleric 1). The first named character is the
Player Character. Preview explains the scenario and party. Start admits an independent editable
copy through the exact retained content contracts, without model-driven character creation,
advancement, party assembly or a campaign-authoring interview. Bundled templates, individual
starters, existing saves and progress remain unchanged. Customize saves independent party files
for the ordinary Campaign Interview and Builder; the Plan's fixed-character policy still applies.
Provider configuration and the first live narrative response are separate from loading the local
prepared state. No campaign is persisted merely by browsing its preview.

Optional Ruleset, Setting and Plan questions follow the three content selections. They come from
the exact inherited copies the selected Plan will use, or from the selected Setting and its Ruleset
when no Plan is chosen yet. Reselecting content removes downstream answers from the active path;
saved chats and reusable content remain available.

Creating a Ruleset, Campaign Setting, Campaign Plan, Player Character, or Companion from the
Campaign Interview opens an independent ordinary saved Builder chat. The Interview remains visible
in the sidebar with its answers intact. Sylvia supplies the creation purpose, the content selections
and relevant Interview information that existed at launch, and an instruction to load the needed
skills and references. This launch brief remains fixed if the Interview later changes. Returning to
the Interview refreshes compatible reusable content, but the player still chooses what to use.
Compatibility is checked against the campaign's current selections at that time.

A Ruleset defines mechanics and does not declare Campaign Settings. A Campaign Setting starts with
an exact saved copy of its Ruleset and may customize that contained copy without changing the source
Ruleset. A Campaign Plan likewise contains an exact saved copy of its Campaign Setting, including
the Setting's contained Ruleset, and may customize the inherited world and rules without changing
either source. The Plan supplies the authored starting world and map; starting a campaign copies
that material into the new campaign together with the chosen party.

Saving a descendant fixes its inherited content at that revision. Later source edits do not flow
into existing Settings, Plans, or campaigns. Adopting source changes is explicit Builder work. It
preserves descendant customizations where they remain compatible and asks the user to resolve a
conflict in intent. Alpha selects neither automatic propagation nor a generic merge framework or
updater interface for this work.

These Builder chats can continue generating while the player works in another chat. They survive
restart, campaign completion, archive, and permanent deletion of the originating campaign, and
their lifecycle never depends on a timely return or completion handshake. Permanently deleting the
campaign removes its Interview, not these independent chats or reusable library content. Finished,
validated content is saved to the local reusable library; unfinished work remains in Builder.
Final campaign customization and meaning-changing adaptive interpretation still require visible
human confirmation and explicit application to the Interview. Upstream reselection warns about and
removes incompatible downstream choices from the active path without deleting Builder chats or
reusable artifacts.

The sidebar identifies working and completed chats, and saved chats have descriptive, user-renamable
titles. Interrupted work remains identifiable and resumable after restart. Existing linked content
chats and their files are preserved when moving to this session model. A shared-file save rejects
outdated reads so Builder can reread and reconcile compatible edits, asking about conflicting intent.

Existing compatible content, complete required answers, and No additional requests can reach the
spoiler-free review without a model call. Adaptive work additionally requires Builder semantic
completion and deterministic structural validation. A Boundary is a hard limit; an unresolved
conflict blocks readiness, while a Preference remains softer guidance. Meaning-changing Builder
interpretations require a visible confirm-or-edit step; exact choices and lossless normalization do
not require redundant confirmation.

The Ruleset supplies the outer participant and mechanical compatibility envelope. Within it, the
Campaign Plan owns whether the Player Character is one fixed Character File or any compatible
character, which exact Companions are required, and the minimum and maximum number of additional
Custom Companions. Required Companions do not count toward the custom limit. Upstream reselection
re-evaluates this policy before preserving downstream participant choices.

The review offers **Ready to Begin** and **Keep Building**. Ready is genuine player input. Starting
either completes the durable handoff and opens the GM on the same Campaign Aggregate, or leaves the
Draft coherently resumable with no partial GM authority. The bundled D&D path admits one Player
Character and at most five total Companions. The Rift Awakening Plan allows any compatible Player
Character, requires no Companion, and allows zero to five Custom Companions. This is not a universal
core cardinality.

### 2.2 Play panels and fallback inventory

The left sidebar follows the current activity. Home shows Sylvia's portrait/branding and saved
Builder sessions without empty Campaign, Status or Writer fields. An active GM campaign shows
its campaign context in place of the Builder list. Builder and Campaign Interview navigation
remain available through Home and the construction flows. A small house button at the top left
provides explicit Home navigation. Both sidebars have independent, reachable collapse/expand
controls. Home owns campaign browsing; the campaign right sidebar does not repeat the campaign
list or expose Stop Session. Settings and relevant setup controls remain accessible; stopping an
in-progress response is a separate composer action. These are presentation changes, not changes
to saved-session lifecycle or background Builder work.

The desktop provides one reusable panel host whose contents come from the active Ruleset rather
than from campaign-specific frontend code. A Ruleset may declare Party, Encounter, Inventory,
reference, or other safe built-in presentations over owner-projected authoritative player-visible
state. Raw Campaign Plan data is private GM preparation and is never a legal player-facing panel
source. A no-rules campaign receives no D&D-shaped UI; the built-in rules-neutral Inventory panel
described below is available without a Ruleset declaration.

People and objects remain persistently trackable with or without a Ruleset. When a Ruleset defines
inventory, use that system. When there is no Ruleset or it defines no inventory system, Sylvia
provides Fallback Inventory through the same GM-facing query/update tools. This fallback is not a
second inventory to synchronise with a Ruleset inventory.

Fallback Inventory distinguishes an item's owner from its current holder or storage location.
Borrowing, storing, dropping or returning an item does not itself transfer ownership; a gift or
sale can. Identical items may stack, partial transfers split quantities, and distinct items retain
separate records. Money is tracked as named quantities with no assumed exchange rates; conversions
require a rate established by the campaign or Ruleset.

The generic Inventory panel shows the selected character's player-visible holdings: item names,
quantities, named currencies, carrying/storage location, and ownership where relevant (for example,
borrowed from Waystation). It reads committed Campaign State, not separately maintained display
data. A Ruleset may supply a specialised Inventory panel; otherwise the generic presentation is
used over the active inventory system. Hidden NPC holdings and private GM facts are not exposed.

The bundled D&D Ruleset provides the panels needed to play The Rift Awakening: Party displays the
selected Player Character first, followed by Companions in stable order, with HP, conditions, and
important resources. Display order does not change actor selection or combat initiative. Ordinary
Party and Inventory labels use authored display names or readable fallbacks rather than internal
identifiers, preserving stored identities and values. Encounter appears while authoritative combat
is active and displays the current round, actor, combatants,
HP, and conditions; Inventory is shown only when authoritative inventory data exists. Alpha does
not show an Objectives panel because it does not yet own a distinct canonical player-visible
objective record. Campaign navigation and Settings remain host controls rather than pretending to
be Ruleset panels.

Alpha extends the existing resizable right-sidebar panel host with rearrangement, hide/show and tab
group controls, plus multiple extra game-panel windows. A panel appears in one place and may move
between the sidebar and a window through drag-and-drop or explicit menu commands. Dragging a panel
out of its source game window provides visible panel feedback and detaches it near the release
position, without requiring a pause over a textual destination. Cancellation preserves the layout.
During rearrangement or return from an extra window, a highlighted preview shows the prospective
placement before release, distinguishing vertical stacking, side-by-side splitting and tab grouping
where supported. Releasing commits the previewed arrangement; cancelling clears the preview.
Extra windows may contain tab groups and resizable rows or columns. This customization applies to Game Panels,
including the generic Inventory panel. Campaign navigation and Settings remain host-owned and
follow the contextual shell described above.

Game-panel frames and headers stay compact and visually subordinate to their contents. Arrangement
commands remain available through unobtrusive, accessible controls; permanent Return to sidebar and
Move to a new window blocks do not occupy ordinary play. Preserve the accepted character-name/HP
treatment and GM activity appearance while refining the surrounding Party and Inventory frames.

Rulesets supply starting layouts. Sylvia saves player preferences per Ruleset and campaign-specific
overrides, with explicit controls to apply either the player's Ruleset layout or the Ruleset's
original layout. Layouts and view details follow the selected campaign or Builder session. Switching
away closes the inactive campaign's extra windows and returning restores them against current game
state; app closure preserves the arrangement. Manually closing an extra window returns its panels
to the sidebar. Missing monitors recover onto an available display while retaining the preferred
placement for later. Relevant panels share actor and target selection without moving the camera or
executing an action. A newly adopted Ruleset version adds new panels to the sidebar without
rearranging the existing layout.

### 2.3 Tactical actions, map geometry, and shot presentation

Chat, map, Party, and result-card controls perform the same Ruleset actions and produce the same
authoritative outcomes. Sylvia receives exact committed direct-action outcomes as context without
pretending it called those tools itself. Rulesets decide which actors the player controls, which
actions require preview/confirmation, and when direct actions or turn completion trigger a GM
response. D&D SRD controls one character; the later TD design controls a squad. All committed
outcomes remain in context even when they do not request narration. The player can explicitly request a response.
Direct actions are unavailable while Sylvia responds and until current Ruleset state permits them;
they do not queue or interrupt. Required enemy responses still gate further play. A failed GM
response offers retry of that response while preserving the already committed mechanics.

**Retained later design:** the TD/Siren coordinate-map, panel/network and shot-presentation
requirements below are deferred with Trigger Discipline and Siren Protocol. They are not Alpha
blockers; shared host behavior required by supported Alpha journeys retains its existing scope.
This does not defer the SRD coordinate combat and map selected in section 3.2.

Maps distinguish cover along a cell edge from objects occupying cells, including objects with both.
Movement respects sealed corners and actual clearance. Open doors use their map-defined footprint,
so an open hinged panel can still obstruct space and a retracted door can clear it. Cover has numeric
physical height with Ruleset-defined named presets, compact visual indicators, and exact inspection
details. Relevant cover appears during movement/fire previews and selection, with optional broader
overlays, without exposing hidden information. TD currently uses one-meter cells and same-level
play; height and stance matter, but rooftop or multi-floor traversal is outside this scope.

Wan's accepted TD/Siren Protocol design, 2026-09-23: ordinary one-cell props align to one whole cell
and retain one object identity/destruction state. Thin fences may use physical edge barriers;
uncovered endpoint portions remain open and actual clearance/rays determine traversal and cover.
Do not resize saved geometry automatically or forbid intentionally larger/offset objects.
TD presents cover height primarily as Full / Half / Quarter, with editable 2.4 / 1.2 / 0.6 meter
defaults and exact inspection details. These are height labels, not fixed protection fractions.
Lower barriers use lighter fills plus height marks/labels; cosmetic opacity does not change
physical visibility or material. At ordinary same-level human dimensions, standing can shoot over
an adjacent Half barrier while crouching behind it blocks that direct line; source/target posture,
angle and real geometry govern the actual result. Full cover requires another valid physical
firing solution rather than an invented automatic lean or stance change.

TD/Siren Protocol uses genuine independently arranged game panels in Sylvia's existing host. The
accepted starting composition is Tactical Map, Squad and a Character/Loadout tab group in a
widened right Game panels area beside the GM conversation. Navigation collapse and width remain
user choices; existing saved layouts are preserved. The panels inherit Sylvia's active Slate,
Sylvia purple or Dark colors, including map chrome, controls, selections and extra windows.
There is no fixed green campaign palette. Panel selection, actions, inventory and previews use
their existing authoritative paths. Mockup cast, resource values and scene text are illustrative,
not approved balance or a Campaign Plan. Later visual changes should remain reviewable in compact
previews. TD-specific mechanics and authored Siren Protocol content remain package-owned.

Wan's subsequent networking approval, 2026-09-23, adds a Network game panel initially tabbed
beside Tactical Map, with the same independent arrangement/detachment and inherited theme. It
shows known connected devices, the selected actor's available route and access, usable functions
and explicit map location. Discovering a device does not itself make it reachable or controlled.
Real entry, discovery, defended access and authorized operation connect hacking to physical
encounter effects. Open traversal through controlled nodes and already-authorized functions do
not require repeated intrusion checks; operations still spend their applicable time/resources.

Networks are authored devices and connections, not a fixed test scene. Monitoring and defender
responses follow actual rules and evidence; unknown security awareness stays unknown to the
player. Losing a connection removes access dependent on it while already completed physical
effects and legitimately retained knowledge persist according to the rules. The approved preview
selects the interaction and panel direction, not its illustrative AP values, successful outcomes
or unresolved security tuning. Actual Siren Protocol Campaign Plans remain a later task.

Rulesets may request visual and optional audio effects for committed results, with TD gunfire as the
first use. Shots show moving tracers and resolved impacts, stopping at character/cover hits or
following resolved penetration contacts. Bursts show individual rounds at the weapon's real fire
rate. Misses use a simple tracer passing just beside the target and fading before anything behind
it, without simulating stray hits. Effects have separate volume/mute, reduced-motion static cues,
and an off setting. Playback is brief, skippable, and nonblocking while normal action gates remain
in force. Switching sessions stops current effects without replaying a backlog on return. Replay
controls are deferred until demand after launch.

## 3. D&D model and engine authority

D&D has ruleset-defined authority rather than a player-facing model/engine slider:

- the model reads the Campaign Plan and directs narrative flow;
- the Ruleset declares its mechanics and the authoritative state/phase conditions under which each
  mechanic is available;
- the engine evaluates those declarations against current committed campaign state and exposes only
  the currently available mechanics through MCP;
- a Ruleset may bind non-choice operation inputs to its own authoritative state, in which case the
  engine omits those inputs from the MCP schema and supplies them at execution; the model is asked
  only for genuine GM choices, never to echo a current actor or phase identity the engine owns;
- a Ruleset may mark non-choice lifecycle operations under declared state predicates—for example,
  beginning the initiative actor's turn or advancing Initiative—and the owner executes those
  operations automatically rather than asking the model to echo engine bookkeeping;
- a Ruleset declares how the active actor maps to a Player or GM decision owner. A Player boundary
  waits for genuine player input; a GM boundary asks the selected model what the named controlled
  actor does and supplies that actor's current state and complete legal MCP surface;
- the model recognizes when an exposed mechanic is required, translates campaign or player intent
  into one or more of those advertised generic or specialized package tools, and supplies only the
  situational inputs the Ruleset leaves to the GM;
- the engine authoritatively resolves encoded checks, saves, attacks, combat, resources,
  conditions, timing, and other mechanics;
- the model narrates from the committed result.

Each finite Campaign Plan act names its main story problem, required discoveries/events and exact closing
event. Every objective linked from the act is required, and all its completion conditions must hold;
alternatives are explicit within the condition they satisfy. Player choices determine methods and
alliances. Optional work cannot replace the main objective or add an unbounded closing requirement.
An authored failure can have a concrete ending; mere acceptance, delegation, deferral or retirement
does not establish completion. Ending play early leaves unplayed acts unfinished. The generic host
checks declared references and supplies these requirements on an explicit act read; the GM assesses
played facts, without automatic advancement or automatic future-act preparation.

Wan's campaign-ending selection, 2026-09-25: every Campaign Plan declares either intended
ending(s), or an explicit note on its final or only act that it has no ending or conclusion and
supports indefinitely ongoing open-world play. The latter is an explicit exception to requiring
a closing event for that ongoing act; it does not remove concrete endings from earlier finite acts.

An intended ending follows authored story events and, where applicable, a final in-character
decision. Its authored content is a concise outcome premise, not a prewritten final narration or
a separate choice to accept/decline an epilogue. The active GM develops that premise into a long,
flowing conclusion grounded in actual campaign events, friendships and romances. The player may
override the intended direction through OOC instructions. Each intended ending declares a simple
yes/no indication of whether subsequent open-world play is viable, so the player can choose
whether to continue when it is viable. This is reusable Campaign Plan behaviour, not Rift-specific.

Wan's subsequent clarification separates the immediate ending from an optional after-story.
The immediate ending covers the finale's actual consequences, reactions and any celebrations
over the following days or weeks. The Plan specifies the loose story ends that must be resolved
there and how; these requirements must remain concrete, not an unbounded demand to tie up every
thread. Once this immediate ending is complete, the player can choose open-world play if the
ending permits it. That play proceeds from the immediate aftermath without first narrating away
the character's future. If the player chooses to finish instead, and an after-story sequence
exists, the GM narrates it: for example, the longer happy life shaped by actual friendships and
romances. An after-story is not a prerequisite for the immediate ending or continued adventures.
There is no automatic long time jump followed by a rewind merely to offer continued play.

Wan approved the chat completion-card proposal on 2026-09-25. After the immediate aftermath, show
a soft card headed Campaign complete with Act N complete above it. Offer Continue adventuring
only for a viable ending and Finish the story, with concise descriptions of each action. Once
selected, retain a quiet completion marker and the chosen continuation or after-story in history;
do not keep presenting active choice buttons. Preserve the choice through reopen. Home remains
navigation, not a finish action, and OOC chat remains available. Illustrative mockup prose does
not establish campaign canon.

After a continuable authored ending, the player may choose Add another adventure from the session
menu. They select a saved compatible Plan or open an independent Builder to author one, review its
preparation and setup assumptions, then explicitly append it. The review includes the exact source,
new act sequence and separate story identities. The player explains how it follows the retained
campaign; already narrated events and futures remain true. A contradictory premise must be adapted
before adoption. The app checks structural and exact-source compatibility, not narrative judgment.

The new adventure retains the current exact Setting and Ruleset snapshots. Its starting party,
location, facts and mechanical setup do not reinitialize the campaign. Played acts, characters,
history, state, unresolved consequences and previous source revisions remain available. Earlier
completion cards stay in history; the next adventure has its own completion gates. Publishing a
library revision alone still leaves live campaigns unchanged. Rules updates remain separate PL-01
work, and sequel append does not authorize mechanical migration or replacing the Setting.

The Campaign Plan is the GM's private story preparation: the direction the GM hopes to lead the
players toward through natural intrigue, leads, character motives, pressures, and consequences. It
is not a script or a declaration that its next prepared scene has begun. A capable selected model
decides how to interpret, pace, defer, reorder, or retire connective material in response to actual
play while preserving hard truths, boundaries, and earned reveal gates. The Plan does not grant or
remove engine operations. The provider harness does not infer tool availability from words in
player dialogue and does not override the Ruleset with generic phase restrictions. For example, an

A Campaign Plan may include an optional natural-language Opening Direction. It is private creative
guidance for the framing, pace, and dramatic purpose of the opening exchange only. It establishes no
person, event, relationship, or mechanic as current; the GM interprets it from admitted participants
and Campaign State, and any requested deterministic procedure still belongs to the Ruleset.
The opening request also places the admitted Player Character's useful surface identity, the present
Companions, and the starting location beside the lifecycle instruction, and explicitly marks prior
history and the Player Character's first action as unestablished. This is factual footing, not a
required exposition checklist or a host-selected opening style. Ordinary turns keep the player's
exact current message salient as the action boundary without classifying it, imposing a prose
length, or adding a host editor.

The compact participant roster does not copy Character File profiles into every provider request.
A Character File preserves its full authored profile separately from its short selector summary and
Ruleset data. When the current player message explicitly names a roster character, Sylvia retrieves
that exact Character File into private turn context; it does not retrieve unrelated party files.
The retrieved form labels the selector, profile, and Ruleset starting data separately so the GM does
not mistake setup shorthand or an opaque Ruleset identifier for authored biography.
An older Character File with no authored profile remains readable and is shown in setup with a clear
completion requirement, but it cannot be selected into a newly created campaign. The user may
complete or replace it through the participant Builder flow; Sylvia never generates an accepted
biography merely to pass admission.
For other character details, the same GM may read the exact admitted Character File through the
reference tool. Retrieved material is a reusable baseline, while Campaign State remains current
campaign truth. A sparse older file is identified as sparse, and missing material must not be filled
with inferred history.

For example, an SRD Ruleset may
expose encounter start and applicable D20 tests while combat is inactive, then expose only the
operations permitted by the current encounter, actor turn, and action state after combat begins.
Another Ruleset may declare entirely different modes and transitions without a core change.
The GM chooses whether to call an available optional operation. In D&D, encounter start remains
available while combat is inactive but is never forced by player wording. Ordinary player prose is
interpreted only by the selected GM model: Sylvia does not implement a hidden keyword, stem, alias,
enum, or semantic command router. A genuine attack may lead the model to call encounter start; a
story told to a bartender about an old fight sees the same legal surface and may remain ordinary
conversation.

### 3.1 GM knowledge, character knowledge, and reveals

The GM has authoritative access to the complete accepted Campaign Plan and Campaign Setting,
including private facts and spoilers, so it can foreshadow and run the authored campaign rather than
inventing a substitute. Access does not make those facts player knowledge. Every turn distinguishes:

- **player-known facts**, which may be treated as established in player-facing narration;
- **GM-private facts**, which may guide preparation, foreshadowing, and eligible reveals but must not
  be disclosed merely because they were supplied to the model; and
- **character knowledge**, where a fact's explicit `known_by` identities determine which NPCs or
  participants may act on it. An empty list means that no character knows the private fact.

A Campaign Setting may declare compact fact-level private knowledge with source provenance and may
also mark whole admitted references as GM-private. A Campaign Plan may select a Setting fact and add
the campaign-specific reveal condition rather than copying the Setting's truth. Plan-local secrets
remain valid for campaign-specific material. Initial player knowledge comes from admitted character
and participant facts, accepted plan initial state, and canonical campaign events—not simply from a
fact appearing in the Setting.

Campaign State is the active campaign's durable GM screen: the current, campaign-specific truth
about people, places, relationships, knowledge, Plan Progress, and Ruleset-owned mechanical values.
It is one part of the Campaign Aggregate, not a second store beside the engine. A campaign-specific
fact overrides the reusable Setting baseline for that campaign without changing the Setting source.
Relevant private material is included in the bounded turn context and additional admitted Campaign
State, Plan, Setting, Ruleset, or participant material remains retrievable through MCP. A reveal
becomes durable player or character knowledge only through an owner-authoritative Campaign State
commit. The same boundary records durable decisions, relationships, discoveries, Campaign Entity
changes, current-scene progression, and Plan Progress. Improvised connective detail is allowed, but
a new fact that later turns must treat as canon must be committed. No string filter is represented
as a complete spoiler-enforcement mechanism; prompt discipline, explicit knowledge projections,
authoritative reveal state, and high-value output checks work together.

When the Campaign File disposition is available, the same GM runs the whole exchange as one
continuous tool conversation. It may query references and invoke Ruleset mechanics as needed,
retaining the exact player action and real tool results as context. There is no mandatory
preparation form or separate prose-composition stage. When ready, the GM submits the decided
durable Campaign File changes, or an unchanged disposition, together with its player-facing
response. Sylvia validates and commits through the existing authoritative writer before releasing
that response. The Ruleset still owns mechanical outcomes; tool availability does not force a
particular read or mechanic.

Fresh Campaign State is the endpoint immediately before the current player message. Continued
activity proceeds from that point without replaying an earlier discovery or elapsed period as
new. Ordinary exploration, survival or elapsed time is not Plan Progress unless prepared material
actually changes status. The GM may determine that a declared action takes an hour; the Campaign
File preserves that chronology. The GM adjudicates the time and ordinary execution details needed
to resolve the chosen action. It returns control before a consequential omitted player choice or
an undeclared subsequent activity, rather than requiring routine micromanagement.

The response describes what the Player Character can observe and leaves their voluntary choices,
speech, feelings, memories and conclusions to the player unless already established or supplied
by the current message. NPCs and Companions may act independently. When their proposed action
requires the Player Character's cooperation, the GM does not invent that cooperation. Ordinary
descriptive detail remains GM judgment; lasting truth must be recorded in the Campaign File.

The GM is not required to certify its reasoning or repeat the same decision across preparation,
composition and state-writing forms. Structured tool validation protects the real campaign and
mechanical interfaces; it does not prove narrative coherence. If a complete response is already
recoverable and only Campaign File fields are rejected, Sylvia retains that response and asks
the same GM to correct the reported state fields without generating another prose copy. Invalid
or incomplete provider calls still require honest bounded recovery. No separate memory agent,
prose editor, host-authored rewrite, automatic prose score or response-length ceiling is introduced.

GM exchanges have no Sylvia-imposed total tool-call or engine-transition count limit. Useful work
continues until the response is complete, the player stops it, or an actual error interrupts it.
Provider output-token settings cover reasoning and tool arguments as well as narration. Use the
selected provider's supported output capacity, or an explicitly chosen lower setting; do not add
a hidden smaller GM allowance. Provider context/output capacity and subscription limits still apply.

#### Player messages and reply presentation

Structured Campaign Plans show prominent chapter cards in saved chat history: an explicitly
resolved act is complete, and a saved scene entering another act begins that act. Announcements
use act numbers without private preparation, appear once at their actual played response, and
survive reopening and history paging. Offering a future lead does not begin its act. A chapter
card does not advance the story, force a player decision or invent another act after the finale.

One player turn may contain IC/action text, an OOC note, or both. The normal composer has an
optional "Add OOC note" field underneath and one Send button. Both exact parts remain associated
with the same turn through persistence, provider input and retry. Successful submission clears
both fields and collapses the OOC field. OOC is explicit player guidance, never inferred from
punctuation or attributed to the Player Character.

The same GM resolves the IC action and briefly acknowledges OOC in a separately labelled block
within its response. Durable preferences and continuity corrections use the existing Campaign File
commit. The acknowledgement must reflect what was actually saved or needs clarification. Corrections
update current facts and record what was corrected afterward; old transcript messages stay intact.

A reply may interleave narration, NPC speech and OOC acknowledgement in order. NPC speech appears
in inline boxes with player-known speaker names and an existing safe portrait when available.
Narration and action descriptions stay outside speech boxes. Concealed identities never leak through
speaker metadata; missing portraits do not block speech. Labels remain readable in plain text,
accessible presentation and reopened history. Old prose need not be retroactively split into speakers.
This adds no model call, portrait generation, or model-authored markup.

An interrupted turn shows one notice. Authoritative saved results determine its wording: when game
changes saved, say "Game changes were saved, but Sylvia couldn't finish the narration." When nothing
saved, say so. Rendering or reopening a notice never repeats an action, retries automatically or
changes saved rolls, HP or inventory.

### 3.2 Ruleset-specific resolution breadth

SRD Lite is the active launch target: a reduced spell list of 117 supported spells, without
consumables. The current roster preserves the 96-spell Lite base and 21 selected Full-only
implementations. Consumables belong to future SRD Full. Full-specific work remains separately
preserved and inactive; the multi-ruleset architecture and existing saved pins remain intact.
Earlier 96/210/339 populations are dated checkpoints, not additional launch requirements.
FC-14's fixed-rule completeness requirement applies to every spell offered in the Lite roster.
Structured spell and actor data supply fixed dice, scaling, saves, costs, limits, timing and
persistent consequences through reusable package operations. The GM chooses intent, supplies
situational facts and narrates committed results; the engine resolves the fixed mechanics.
Missing fixed effects remain implementation gaps rather than GM rulings.

Genuinely open-ended fictional adjudication, including illusions, divination and unusual wishes,
remains with the GM while the engine enforces their fixed rules and persists mechanical effects.
This does not promise deterministic interpretation of every fictional possibility or require a
bespoke resolver or end-to-end test per spell. Shared mechanic proofs, declarative catalogue checks
and representative composition must substantiate the complete offered behavior, including later
consequences; source text or a support label is insufficient.

Wan's direct selection on 2026-10-11 supersedes the former post-Alpha deferral of SRD positioning
and map display. SRD launch combat uses full coordinate positioning with a map, retaining the existing SRD rules and approved spell adaptations.
Precise positioning matters in combat; exploration and ordinary roleplay do not require map movement.
In combat, the approved 117-spell Lite roster must resolve movement, range, targeting and area effects against the
same authoritative positions, including the selected three-dimensional spell geometry. The map
displays those committed positions and effects; it is not a separate source of combat state.
GM-supplied distance or affected-target assertions do not replace coordinate qualification.

Reuse the implemented ruleset-neutral spatial and map capabilities where practical. Trigger
Discipline retains its grid positioning. TD and Siren Protocol remain later work and not Alpha
blockers, superseding their 2026-09-23 pre-release selection. Each Ruleset declares its spatial
data and interprets shared engine facts through package mechanics; it must not ship a private or
executable package-local raycaster.

### 3.3 Stateless MCP boundary

By `alpha-10a`, all model-facing mechanics use the selected final MCP 2026-07-28 stateless
protocol core. Each request carries stable identifiers and revalidates against fresh canonical
owner queries. Transport sessions never become campaign, TURN, permission, tool-availability,
idempotency, conversation, or mechanics authority.

MCP tool discovery is the engine's current answer, not a package inventory. The engine evaluates
the exact locked Ruleset declarations against current state for every GM model inference, including
every continuation after a tool result. A committed mechanic can therefore change the next MCP tool
surface inside one player-facing exchange. The provider adapter projects that surface faithfully;
it neither adds tools nor removes them through keyword ranking. Invocation revalidates the same
Ruleset and canonical state so a stale discovery cannot authorize a stale action.

Campaigns, sessions, TURNs, package locks, actions, chronology, and bounded model context remain
owner-managed durable records. Retries return the existing committed result or a deterministic
stale/collision rejection rather than duplicating mechanics. Acceptance requires one unified
model-facing path, compatible pinned SDK, clear version rejection, exact state/package-derived
tool discovery, restart/handler equivalence, and the packaged Windows player-intent-to-save-and-
reopen journey.

## 4. Builder promise

Builder is a capable generic model/tool agent session advertising exactly three construction skills:

- **BUILD RULESET**
- **BUILD CAMPAIGN SETTING**
- **BUILD CAMPAIGN PLAN**

Builder remains model-led for adaptive research, questions, tool use, drafting, and validation.
It has its own provider/model, resumable history, skills, tools, streaming, usage visibility,
user steering, Stop, and approved generic delegation. It remains available after a valid
campaign artifact starts a DM session; DM becomes the sole gameplay writer, while Builder is
not a competing campaign writer.

The player may start a first-class standalone Builder conversation directly from the desktop's
left sidebar without creating or selecting a campaign. A new chat starts as a general agent
conversation: the player says what they want to build, and Builder applies the relevant tracked
skill from that conversation rather than requiring an up-front mode or skill choice. Builder writes
ordinary persistent workspace files. Finished files may later be selected for the applicable
package, setting, Campaign Plan, character, or campaign flow; Alpha does not invent a separate
artifact-lifecycle abstraction around them.

The general Builder receives a compact capability catalog and loads full tracked-skill instructions
only when that workflow is needed. The Guided Builder Chat is dedicated Campaign Interview
orchestration over the same session substrate. Independent Player Character and Companion work uses
ordinary Builder artifact capabilities rather than inventing additional tracked skills.

Deterministic Sylvia GM code owns schema validation, package identities/hashes/pins, permissions,
admission, and DM handoff. Alpha does not require a general-purpose Builder workflow engine beyond
the bounded Campaign Interview state. A generated
artifact is successful only after representative lifecycle/runtime validation, honest reporting
of unsupported mechanics, and appropriate provenance.

### 4.1 Content definitions and draft checks

Every supported authoring and portable content format has a complete, versioned definition:
file structure, field meanings and types, required/optional/conditional fields, allowed and
forbidden content, references, ownership, and any declared extension areas. This covers Ruleset
source and runtime packages, Campaign Settings, Campaign Plans, Character Files, their structured
maps, starting mechanical data, panels/layouts, result cards and Interview Contributions, and the
existing Portable Campaign Export. Internal database tables are not public authoring formats.
Ordinary lore remains freely written prose; no universal world-topic checklist is required.
Undeclared structured fields are rejected with a precise explanation.

Builder and external authoring agents can obtain the applicable definitions, reference
documentation and small starter examples. Agent authoring is the expected workflow; a dedicated
schema browser or editor is outside this delivery. Builder can check an unfinished draft using
the applicable checks that accept finished content. Errors identify the file, field, missing
information, type, reference or declared calculation that needs attention. A check preserves the
draft and does not publish it or change a live campaign. Format validation does not establish
narrative quality or correctness of every mechanics sequence.

PES-08 now selects Builder-accessible testing of mechanics and the points at which play requires
GM narration or a GM decision. Preserve reusable test cases with the Ruleset for later reruns.
Testing must exercise the relevant real campaign game-loop behaviour against isolated disposable
state, so the same tested setup and actions do not behave differently when played. Generated-prose
quality evaluation is outside this selection. Tests supply scripted GM choices and simple narration
completion through the ordinary game loop, checking when those inputs are requested or allowed.
Builder writes and runs relevant tests as part of ordinary Ruleset creation and editing, including
related behaviour affected by changes. Failing saved tests prevent declaring the Ruleset ready to
use; unfinished drafts remain saved with an explanation. Builder must fix the implementation or
discuss an intended rule change, never silently alter expectations merely to get a pass.

Scenarios support controlled dice outcomes and sequences across multiple actions/turns. Tests
cover meaningful supported actions, important rejections and key interactions; missing applicable
tests do not count as a pass, and an exhaustive combination matrix is not required. Builder chat
shows a concise summary and expandable failure details with the step, expected result and actual
result. Existing Rulesets and campaigns without tests remain usable and are not labelled test-passed.
Apply the testing requirement to new or revised Rulesets created by Builder without retroactively
blocking live campaigns. This selection does not claim an implemented testing facility.

Playable Character Files belong to a Campaign Setting. The Setting may require world-specific information;
mechanical requirements and effects belong to its contained Ruleset Snapshot. A Ruleset offering
custom character creation supplies the applicable creation procedure, including starting choices
and equipment; a premade-only Ruleset need not offer custom creation. Builder supplies authored
choices and facts, and Sylvia calculates derived values from the contained Ruleset's declared
formulas. Missing required choices or formulas are reported without invented defaults. Authored
inputs need no formula. Deliberate exceptions use Ruleset-declared customization; unsupported
customization requires an explicit edit to the contained Ruleset, without silently editing its
source. This authoring work does not recalculate existing live campaigns.

After explicitly adopting a revised Setting, the user can request reuse of an existing character.
If its unchanged data satisfies the revised Setting and contained Ruleset, Sylvia saves a
compatible copy with the new exact binding. The original character and existing campaign copies
remain intact. Changed choices or data require explicit Builder adaptation; no automatic mass
rebinding occurs.

Alpha also requires a general character library: reusable authored character profiles saved through
Builder without a target Setting or Ruleset binding. When creating a Custom Companion, the user can
select a general profile and have Builder adapt it to the chosen Setting and contained Ruleset.
Preserve the intended character identity while adapting world-specific details and starting data,
settling material choices through the Builder conversation. Save the validated result as a separate
target-bound Character File, subject to ordinary character admission and the Plan's party policy.
Keep the general source and other adaptations intact; an unbound profile is not directly playable,
and later edits do not automatically propagate into adaptations or live campaigns.

For safely readable imported content with format errors, show the errors and offer **Fix with
Builder**. Selecting it opens an independent saved Builder chat with a repair copy and the
diagnostics. Preserve the original and validate the repaired copy before it becomes usable.
Archive and file safety requirements continue to apply.

For closed or potentially restricted sources, the user supplies the material or explicitly
directs online research. Official Sylvia GM content uses open redistributable sources. Human review
is required for contradictory sources and missing engine capabilities.

## 5. Agent event and reasoning contract

Chat output preserves the immutable chronology of completed narrative and tool segments. Channel
transitions retain their identities and order. Grouping activity in the interface does not merge
or rewrite the underlying events. Provider reasoning has the separate inspection record below;
it is not ordinary narrative or campaign memory.

GM and Builder each show one expandable activity group per response, with entries ordered inside.
The collapsed group is no taller than one line of text. It stays compact while running, updates a
neutral current-activity description, and expands on demand; a manually expanded group stays open
until the user closes it. Routine entries use basic descriptions such as `Read campaign data`,
`Write to database`, or `Write to file`, with basic status. Raw arguments/results and private file
or database details remain in Full Audit. This replaces separate routine completion receipts such
as `Turn resolved`. Meaningful game results and Builder outputs, including publication results and
actionable validation problems, remain visible outside the group at their chronological point.

Only genuine player text or structured interaction occupies provider User role. Deterministic
Interview questions are Builder/assistant history, real tool results remain typed tool results, and
application context, lifecycle, evidence, and invocation control never become player speech.
Provider adapters fail instead of silently relabelling unknown input or adding conversational
prefills. The exact Ready event is the opening User input; `campaign_start` remains a separate
application-owned event.

Saved construction conversations and authored files survive context compaction. Sylvia may
automatically shorten the model's working context using a saved continuation note and recent exact
exchanges, with a visible progress notice and retained completion marker. Earlier conversation and
compacted source material remain available through bounded reads. Structured selections, source
snapshots, file versions, current campaign state and completed operations retain their existing
authority; a continuation note cannot replace them. Compaction runs at completed exchange boundaries,
preserves unresolved player intent and steering, and never repeats an already committed operation.
Failure preserves the previous working context and saved work with a recoverable explanation.

Model capacity, an enforced response limit and estimated planning headroom are distinct. Builder
defaults to a 32,000-token response budget where the transport supports an output limit, bounded by
the model's configured maximum and separately adjustable. Explicit GM output settings are preserved.
Subscription output is labelled managed by Codex. Codex decides final subscription request
acceptance; local estimates guide compaction and diagnostics rather than claiming a byte bound proves
overflow. Other transports retain pre-send fit checks with authoritative counting when available.
Exact counts are claimed only from authoritative usage or a supported authoritative counter; estimates
and unavailable runtime controls remain visibly distinguished. Runtime rejection or a request for
compaction preserves work and uses the explicit working-context recovery path, with no model fallback.

- readable reasoning supplied by the provider is saved with its turn and available only through a
  separately collapsed Thinking entry inside the activity group. Opening activity does not reveal
  thinking; no reasoning text appears in its collapsed label or preview. When the provider supplies
  no readable reasoning, there is no Thinking control. Sylvia does not manufacture a player-safe
  substitute or relabel ordinary narration as thinking;
- saved thinking is for inspection and stays outside ordinary model-history assembly and campaign
  memory/compaction. It is sent back as input only when the selected provider requires it for the
  conversation being continued; the resulting input usage is still counted. Saving an inspection
  archive does not authorize resending the entire archive;
- full backups retain saved thinking. Normal campaign exports exclude it by default and offer an
  explicit include-thinking option. Ordinary logs continue to exclude it;
- interrupted responses retain available partial thinking and completed activity with a clear
  Interrupted status. Thinking stays closed by default, committed results remain visible, and any
  required Retry action is available outside the collapsed activity group;
- TALK streams provisionally and becomes canonical only on completion;
- Admin Full Audit is off by default during ordinary use and mandatory during testing. A request to
  test Sylvia authorizes enabling it, inspecting the selected test's records, and copying or locally
  exporting those records when useful for the test without another approval. It is enabled before
  the first tested action and remains enabled throughout the test. The Audit surface carries only
  a short, passive `May contain spoilers` notice; enabling, viewing, copying and exporting do not
  require a separate warning acknowledgement. This replaces the earlier privacy-confirmation flow.
  While enabled, it exposes the complete model request, source roles, tool surface, provider-client
  request representation, response stream, and honestly sourced token information in a separate
  current-process trace view and an optional read-only live inspector window. The inspector
  preserves exact chronology with collapsible records, search, pause, and automatic follow; it
  cannot replay or mutate play. Disabling Full Audit clears the owner store and live projection
  immediately, while closing the window does not change capture. Explicit copying of visible
  records and a separate local Audit file export are supported; file export uses the normal save
  chooser without a preceding confirmation dialog. Audit never enters campaign context, logs,
  Campaign Export, or backup. Credentials and private transport headers are never captured;
- TOOL outcomes retain their true order through neutral activity entries and, where meaningful,
  separate user-facing result presentations. An activity group is not a replacement for those
  results or an additional technical readout;

Alpha does not automatically retry inference or switch providers. Incomplete prose is discarded;
committed mechanics survive; the player sees **Narration interrupted—Retry**. After a short
disconnect grace period, unfinished inference is cancelled rather than spending invisibly.
Manual Retry never repeats a committed action.

After the player presses Stop and generation has stopped, the composer accepts a new message;
Retry is optional. The new message redirects the unfinished continuation from the current saved
state. If no action has committed, it may replace the unperformed direction. Already committed
actions, Campaign File changes and Builder workspace edits remain saved. Required consequences,
including an outstanding enemy response, still resolve before another incompatible player action.
Retry without new input continues the interrupted response using saved results. These choices
survive reopening; accepting a redirected continuation cannot leave a stale Retry able to revive
the replaced direction. Stop is not undo and does not grant an extra action.

## 6. Rulesets, packages, and extensions

Bundled and third-party rulesets use the same public package format. External packages use an
**Install Package** file picker and deterministic candidate validation. Installation does not
activate or repin a campaign. Campaigns retain exact pins; activation is explicit, occurs only at
an idle completed-TURN checkpoint, and leaves the old pin authoritative on failure.

Existing campaigns can explicitly preview and adopt a compatible installed snapshot with the
same Ruleset and Campaign Setting ownership. Campaign Data shows content changes and conflicts
before confirmation. Compatible adoption keeps saved values and character records unchanged,
preserves the player's layout and adds new panels once in the sidebar. Removed or renamed panels,
changed existing state/character contracts and unsafe saved values block this path. It performs
no automatic repair. Stop Session reaches the checkpoint; stale previews and active work cannot
apply. Previous exact versions remain retained. Returning to one requires another compatibility
review against current progress; it never rewinds intervening play.
Character provenance and accepted Plans retain their original exact source. Active character
bookkeeping and Ruleset/Setting reference reads follow this campaign's committed compatible
update lineage. Existing fact identities, audiences, source bindings and declared content
references must remain coherent; removal or reassignment blocks adoption.
Candidate Setting knowledge must also fit the campaign's accepted Plan and retained adventures.
Conflicting fact identities or missing references are explained before adoption; this workflow
does not rename secrets or rewrite the Plan to resolve them.

Every ruleset declares one minimum recommended model-capability tier. The selected model may be
below it after a clear warning. Sylvia GM provides at least ten ordered capability tiers.
Engineering is authorized to define tier identifiers, names, empirical rubrics, benchmarks, and
model placement, provided classification is evidence-based and never derived from price,
provider, parameter count, or marketing. The provisional “Gemma E4B” label has no authority until
its exact identity is verified. Production-model benchmark placement is not an Alpha release gate:
an unrated model displays the warning and remains usable through the same explicit override.

Alpha ruleset extensions are declarative packages using the bounded compositional runtime. Third-party
executable plugins, Unsafe Mode execution, and their consent/containment lifecycle are post-Alpha and
are not advertised or required for the first release.

A Ruleset, Campaign Setting, or Campaign Plan may contribute bounded Campaign Interview questions
through one restricted versioned declarative schema. Contributions may define stable question
identities, supported answer types and options, requiredness, safe text, deterministic conditions,
and an adaptive flag. They cannot supply executable code, tools, provider policy, or arbitrary
system instructions; invalid or unsupported contributions fail closed.

### 6.1 Ability-neutral runtime

A ruleset may omit abilities/stats and inherited feature-toggle objects. Skills may be independent.
When stats exist, their generation, modifiers, saves, relationships, and uses are explicit package
mechanics rather than implicit Sylvia GM defaults.

D&D/Cairn-oriented helpers may remain clearly labelled compatibility conveniences but are not the
universal contract. Alpha acceptance includes an immutable non-D&D conformance package with no
abilities, race/species, HP, AC, or D20; independent skills; non-D20 resolution; Stress/Wounds;
a resource, condition, advancement mutation, and opposed or noncombat check; ordinary admission,
activation, execution, persistence, reopen, and idempotent replay.

### 6.2 Ruleset creation, support, and inspection

Sylvia is a practical framework for creating Rulesets, not a requirement to hand-code or certify
every content entry. Builder's normal BUILD RULESET path owns the technical work: it turns the
author's game intent into compact family-based YAML, exact runtime and manifest artifacts, validates
them, and publishes one portable candidate for explicit installation. Authors choose whether an
input is a player/GM choice, a bounded GM judgment, or engine-owned state. Reactions and similar
interruptions bind the exact triggering entities and facts before resolution. Only public actions
declared by the package manifest can enter the model tool surface; private family mechanics remain
internal composition.

Every installed Ruleset reports gameplay support honestly. A mechanic or catalog entry identifies
whether it is resolved by a reusable engine family, a specialized deterministic resolver, a bounded
hybrid, bounded GM adjudication, or is unsupported; reference-only material is distinguished from
gameplay resolution. Gameplay package identity, source provenance, and optional certification
evidence are separate facts. A provenance or certification record never implies deterministic
mechanical coverage. These reporting categories do not waive section 3.2's fixed-rule requirement
for the offered 117-spell Lite roster; missing offered fixed effects remain gaps.

Fresh Sylvia installations already contain one admitted, selectable D&D SRD 5.2.1 candidate; the
player performs no import step for the bundled Ruleset. Settings provides read-only installed
Ruleset browsing: exact identity and compatibility, public actions and private mechanics, support
claims, complete file tree, safe YAML/text preview, exact archive export, and comparison between
installed versions. Browsing or export never activates or repins a campaign.

Ruleset verification remains proportional: test shared primitives deeply once, each new mechanic
family at its owning boundary, catalogs structurally, representative compositions, and one installed
smoke for the supported journey. Useful specialized mechanics remain valid, but ordinary spells,
content rows, and family compositions do not each require a bespoke resolver or end-to-end test.

## 7. Reliability boundary

An advertised journey is Alpha-ready only when it:

- works through the shipped desktop rather than a test-only bypass;
- has one coherent authoritative ownership model;
- fails closed instead of reporting fake success;
- preserves coherent retry, cancellation, reload, backup, and recovery;
- cannot silently diverge conversation from committed mechanics;
- has active automated protection and human-readable probes;
- reports unsupported content and missing mechanics honestly.

Archive is unavailable while work able to mutate that campaign is active; independent content
Builder chats do not block it. An idle turn waiting for the player is saved state, not active work;
Archive and Restore retain that turn without completing or discarding it. Delete first creates an indefinite
recoverable tombstone. Permanent purge is a separate explicit irreversible local-store action;
independent exports/backups remain untouched, and recovery under a colliding local ID fails closed.

Home campaign entries and independent Builder chats offer reversible Archive actions and a shared
archived-items collection with explicit Restore. Archived items leave normal navigation and recent
choices. Independent Builder archiving retains its conversation, unfinished files and saved choices,
rejects live or queued work, and requires Restore before further work. Campaign-owned Interviews
and Builders follow their campaign's archive state; a chat action must not silently archive a campaign.

Draft and started campaigns use the same `.sylvia-gm-campaign` portable export. A Draft export
contains the exact Campaign Interview/session records, active selections, campaign customization,
and campaign-local material required to resume, while excluding unrelated workspace scratch and
Admin Full Audit. The export warns that construction answers and campaign material may be private.

### 7.1 Campaign and supported-journey acceptance

Wan cancelled the marathon six-act AR-01 live run after P396 because of progression gaps.
Its earlier execution requirement is superseded by a comprehensive automated testing framework plus
short targeted real-model and installed acceptance checks. The cancelled run is
partial evidence, not a pass. AR-01's original procedure is retained local historical evidence,
outside this source distribution; its status and findings are preserved in the
[historical plan records](../plans/alpha-rebuild-readiness-plan.md#historical-work-and-acceptance-records).

Preserve evidence of continuity, carried consequences after detours, meaningful act progression
and endings, player agency, compaction, save/reopen and recovery, and the supported construction
and play journeys. Scripted-provider checks establish deterministic behavior; real-model checks
establish the selected judgment behavior, and installed checks verify the exact desktop artifact.
Serious failures, unperformed checks, hidden state repair or evaluator coaching cannot yield a pass.

FC-14 owns the framework work; the development process owns verification method and the active
plan records verified coverage and remaining gaps. Current development qualification has no
whole-run wall-time cutoff: let healthy progressing tests finish. The 1,800-second allowance
applies only to future post-launch CI before pull requests, selected explicitly with --ci.
It is separate from the removed package-generation timeout and does not establish final framework
coverage or completed native, provider or installed acceptance.

## 8. Windows release decisions

Engineering may choose and verify a semantic prerelease version and reversible installer/release
layout for Alpha. A paid signing certificate and automatic updater are not Alpha requirements.
Unsigned-artifact limitations must be disclosed honestly. Spending, signing procurement,
deployment, and public GitHub publication remain separately authorized.

`alpha-10a` validates the local Windows 11 NSIS installer and portable EXE ZIP, their exact
source/payload manifests, provenance, integrity, version display and launch/reopen/recovery.
Installer checks cover installation and removal. Portable checks cover adjacent data, movement,
replacement without overwriting saves, installed/portable coexistence, same-dataset protection
and credential re-entry on another Windows account. `alpha-10b` publishes only those exact
qualified artifacts through GitHub Releases after direct approval.

## 9. Explicitly post-Alpha

- incompatible rules-update migrations and player-resolved conflict handling beyond the
  compatible-only adoption path;
- Trigger Discipline and Siren Protocol implementation and acceptance; they are not Alpha blockers;
- Cairn 2e as an advertised supported ruleset;
- multiplayer, mobile, native macOS/Linux, or hosted cloud play;
- Windows 10 qualification, installer formats other than NSIS, and elevated MSI qualification;
- third-party executable plugins and Unsafe Mode execution;
- importing unreleased predecessor-prototype campaign state;
- campaign cloning, marketplace, visual map editor, and image generation;
- a large bundled campaign library;
- Sylvia-hosted monetization/subscription infrastructure, billing, quotas, and telemetry;
- general-purpose software-development promises for Builder;
- a bundled original non-D&D launch Ruleset;
- automatic updater and paid code-signing procurement unless later selected.
- additional general browser/device-code provider sign-in flows beyond the implemented ChatGPT
  account route; each needs supported integration and its own compatibility qualification.

## 10. Open product boundary

No unresolved product-design choice blocks `alpha-10a`. A future bundled original ruleset requires
its own approved PRD and promotion into the active product contract; its mechanics, content, UX, and
acceptance journey must not be invented by implementation.

Detailed executable-plugin sandbox/ABI policy and material marketplace-style package-management UX
remain deferred unless implementation reaches a genuine need.

## 11. Change control

New Alpha promises require a direct product selection and a semantic update to this contract.
A defect becomes a release blocker when it prevents an advertised journey or violates the
reliability boundary, not merely because additional polish would be useful.
