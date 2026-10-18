# Sylvia GM competitor watch — 2026-08-21

> Status: research note, not product or architecture authority. This updates the saved Hermes
> competitor snapshot taken at `2026-07-28T12:08:33Z`. It uses public primary sources only:
> official repositories, histories, releases, documentation, stores, and company announcements.
> Proprietary-product capabilities below are first-party claims, not hands-on verification.

## Executive summary

- **Loreweaver is now the most urgent open-source direct competitor.** In 24 days it went from 27
  to 39 GitHub stars and from one to three forks, while landing 276 commits. More importantly, it
  now has downloadable desktop clients, local-model support, portable rulepacks, a mechanics DSL,
  and a large test suite. The earlier desktop, local-model, and multi-ruleset differentiation has
  narrowed substantially. Its public description still does not show a complete tactical combat
  loop comparable to Sylvia's intended rules-engine boundary.
- **Friends & Fables has effectively transferred its competitive future to Craft.** Friends &
  Fables is in maintenance mode; Craft entered open beta on 5 August. Craft combines an AI GM,
  explicit reusable **Systems** and **Worlds**, a portable folder format and CLI, discovery, and
  creator earnings. It is the closest current commercial analogue to Sylvia's GM plus Builder
  concept, although the runtime remains proprietary and cloud/credit-based.
- **Latitude's Voyage is a material omission from the old list.** It launched before the baseline,
  so it is new to this watch rather than newly launched. Its proprietary World Engine claims
  deterministic state, rules, inventory, geography, relationships, and autonomous NPCs over long
  play. Latitude also brings AI Dungeon's distribution and creator base.
- **Multihog and NarrativeEngine-P both moved quickly.** Multihog added persistent evolving maps,
  world causality, NPC/PC libraries, and prompt-generated game systems; its current licence is
  GPL-3.0 rather than the MIT licence recorded in the baseline. NarrativeEngine-P deepened its
  world simulation and GM tooling, but its “Initiate Combat” feature remains a signal rather than a
  deterministic combat executor.
- **Galdr's public evidence disappeared.** Its repository now returns 404 and its owner's account
  exposes zero public repositories. That lowers its observable open-source threat but does not prove
  deletion or abandonment; it may have been made private, moved, or renamed.
- **AI Dungeon itself has not crossed the watched boundary.** Its official positioning remains
  freeform—“no rules, no objective”—and the reviewed official surfaces show no new open-source,
  self-hosting, or public-API move. Voyage should be tracked separately.

## Movement in the saved six

GitHub counts are snapshots, not adoption or active-user counts. Commit totals below were derived
from GitHub's official REST pagination for commits after the baseline timestamp.

| Competitor | 28 July baseline | 21 August evidence | Assessment |
| --- | --- | --- | --- |
| **galdr** | 0 stars, 0 forks; public repository | The [repository URL](https://github.com/Doichinstein/galdr) now returns 404, while [Doichinstein](https://github.com/Doichinstein) exposes zero public repositories. | Move from **critical observable open-source threat** to **unverifiable/dormant watch**. Check for a return, rename, or public product; do not infer why it disappeared. |
| **Loreweaver** | 27 stars, 1 fork, 1 open issue; pushed 25 July | [39 stars, 3 forks, 1 open issue](https://api.github.com/repos/1A7432/loreweaver); pushed 20 August; [276 post-baseline commits](https://api.github.com/repos/1A7432/loreweaver/commits?since=2026-07-28T12:08:33Z&per_page=1). | **Critical direct/open-source.** The most consequential movement in the saved list. |
| **NarrativeEngine-P** | 70 stars, 13 forks, 14 open issues; pushed 26 July | [86 stars, 20 forks, 9 open issues](https://api.github.com/repos/Sagesheep/NarrativeEngine-P); pushed 16 August; [145 post-baseline commits](https://api.github.com/repos/Sagesheep/NarrativeEngine-P/commits?since=2026-07-28T12:08:33Z&per_page=1). | Raise from **low** to **medium / emerging direct**. It overlaps increasingly with campaign operation, but not yet with deterministic rules execution. |
| **SillyTavern Multihog D&D Framework** | 58 stars, 10 forks, 0 open issues; MIT recorded | [73 stars, 14 forks, 0 open issues](https://api.github.com/repos/MultihogAurelius/SillyTavern-MultihogDnDFramework); pushed 20 August; [271 post-baseline commits](https://api.github.com/repos/MultihogAurelius/SillyTavern-MultihogDnDFramework/commits?since=2026-07-28T12:08:33Z&per_page=1). Its [current licence is GPL-3.0](https://github.com/MultihogAurelius/SillyTavern-MultihogDnDFramework/blob/main/LICENSE). | Raise from **low direct / rising adjacent** to **high adjacent / emerging direct**. Still constrained by SillyTavern coupling. |
| **Friends & Fables** | Proprietary web competitor; watch for OSS/API/self-hosting | On [5 August Craft entered open beta](https://fables.gg/patch-notes/craft-open-beta-and-credit-transfer), with F&F credit transfers and prorated annual refunds. The company says F&F is not closing but is now in [maintenance mode with a slower roadmap and likely no new systems](https://www.craftrpgs.com/blog/craft-a-new-approach-to-ai-rpgs). | Reduce the old product to **maintenance watch**; replace it with **Craft as a critical direct competitor**. No OSS/self-host/public API move found. |
| **AI Dungeon** | Low open-source adjacency; watch for OSS/self-host/API | The official product still presents itself as unconstrained narrative play with [“No rules, no objective”](https://aidungeon.com/). No qualifying OSS, self-hosting, or public-API movement appeared on reviewed official surfaces. | **Low adjacent, unchanged.** Track sibling product Voyage independently. |

### Loreweaver: the sharpest open-source movement

Loreweaver's visible interest increased 44% in stars (27→39) and 200% in forks (1→3), but the
capability movement matters more. Its current [repository and documentation](https://github.com/1A7432/loreweaver)
describe:

- a portable file-based engine and open content standard, with CoC 7e, D&D 5e, and World of
  Darkness rulepacks;
- declarative resolution rules, dice-pool variants, sandboxed scripted mechanics, and `.lwpack`
  bundles for rules, skills, cards, lore, interface panels, and media;
- local and remote model providers plus peer-to-peer multiplayer; and
- a Tauri desktop client with Windows, macOS, and Linux builds, alongside a terminal client and more
  than 2,200 Python tests plus roughly 370 client tests.

The saved baseline's main gap remains visible: the public README does not describe a complete
initiative/action/targeting combat loop. Loreweaver is nevertheless no longer just a promising
rules abstraction. It has neutralised three easy Sylvia positioning claims—desktop delivery, local
models, and multi-system portability—so comparison should focus on the depth and trustworthiness of
deterministic play, campaign planning, and the GM/Builder workflow.

### NarrativeEngine-P: deeper simulation, not yet a game engine

NarrativeEngine-P gained 16 stars (23%) and seven forks (54%). There was no tagged release after the
baseline—the latest remains [v1.0.4, released 28 July](https://github.com/Sagesheep/NarrativeEngine-P/releases)—but
main-branch work continued. Its [current repository](https://github.com/Sagesheep/NarrativeEngine-P)
advertises NPC goals and relationship pressure, world arcs and time-skips, knowledge boundaries,
inventory proposals, a scene notebook, lore retrieval, rollback, procedural overworld maps, and
local Ollama/embedding support.

Its combat overlap should not be overstated. The public “Initiate Combat” tool identifies hostiles
and hands off a combat state; it does not show the deterministic turn, action, targeting, effect,
and resolution ownership that Sylvia intends. Treat it as a rapidly improving AI-GM orchestration
competitor, not proof that the engine boundary has been matched.

### Multihog: sustained velocity and a more ambitious simulation

Multihog gained 15 stars (26%) and four forks (40%). Its official
[changelog](https://github.com/MultihogAurelius/SillyTavern-MultihogDnDFramework/blob/main/CHANGELOG.md)
records Map Evolution on 14 August, a causality/thread ledger on 16 August, an NPC/PC library on
17 August, and v8.1.3 on 18 August. The current
[README](https://github.com/MultihogAurelius/SillyTavern-MultihogDnDFramework) describes persistent
maps and settlements, autonomous off-screen world evolution, undo/redo and map inspection, hybrid
committed-DC resolution, lore and progression agents, and AI generation of custom game systems from
prompts.

This is meaningful architectural convergence. Its remaining boundary is product shape: it is still
a SillyTavern extension rather than a standalone GM/Builder application, and the reviewed sources
do not show an MCP boundary or a first-class campaign-act plan. The licence shift to GPL-3.0 also
changes reuse and commercial-integration implications.

## Direct competitors newly added to the watch

“Newly added” does not mean newly launched. Craft's open beta is new; Voyage and some other products
predate this scan but were absent from the saved list.

| Product | Why it qualifies | Threat now | What to watch |
| --- | --- | --- | --- |
| **Craft** | Craft's explicit [Systems](https://www.craftrpgs.com/patch-notes/patch-notes-b0-026-systems) define file types, rules, instructions, and reusable base content; Worlds build on them. [CDF v2 and the Craft CLI](https://www.craftrpgs.com/patch-notes/patch-notes-b0-24-craft-cli-cdf-v2) make projects portable folders editable with outside tools and coding agents. On 14 August it added [creator earnings](https://www.craftrpgs.com/patch-notes/patch-notes-b0-031-creator-earnings-profile-cosmetics) for both world and system creators. | **Critical direct.** Closest proprietary match to GM + Builder + reusable rules/content packages, with marketplace distribution. | Deterministic rules depth; export round-trip fidelity; whether CDF enables a non-Craft runtime; multiplayer; pricing and creator uptake. |
| **Voyage by Latitude** | Latitude's [21 April announcement](https://latitude.io/press) and [23 April expanded-beta update](https://latitude.io/news) describe a World Engine governing state, memory, rules, health, inventory, currency, geography, relationships, and autonomous NPC motivations over thousands of turns. | **Critical direct, previously missed.** It pairs serious world-state claims with AI Dungeon's parent and distribution. | Public availability beyond waitlist/beta; authoring tools; multiplayer; rules customisation; data portability; evidence of deterministic depth under extended play. |
| **Auferet** | Its official [Custom Game Modes](https://auferet.com/custom-game-modes) support stats, resources, skills, formulas, plain-language rules, and sandboxed scripted tools, with D&D 5e and PF2e templates. Modes and saves are exportable JSON; the full mode is embedded in each browser-local save. The broader [product](https://auferet.com/about) adds multiplayer, memory, lore uploads, quests, and NPC relationships. | **High direct.** Strong rules-authoring overlap and unusually good user ownership for a commercial web product. | Whether custom mechanics remain deterministic during live play; authoring usability; desktop/local inference; campaign-planning depth; adoption. |
| **Taverna** | The official site says it is [now in open beta](https://tavernadm.com/) and claims deterministic D&D 5e combat including action economy, spell slots, concentration, reactions, and death saves, plus tactical maps, persistent state, long-term memory, and multiplayer through Discord. | **Medium-high direct.** Strong execution loop, but narrow to 5e and Discord. | Open-beta retention; content/rules authoring; non-5e support; portability; whether deterministic claims hold outside combat. |
| **ArchitectRPG** | The [live product](https://www.architectrpg.com/) claims D&D 5e and PF2e support, persistent worlds, branching plots and factions, combat with cryptographic random dice, seven specialised AI directors, world-building tools, voice, and free/paid access. | **Medium direct, evidence-limited.** Broad feature overlap, but current evidence is first-party marketing and custom systems are still future-facing. | Hands-on verification; system fidelity; custom-system launch; state ownership; export/local options. |

Craft deserves the first commercial comparison page. It competes on the entire creation-to-play loop,
not just AI prose. Voyage deserves the second because its World Engine attacks Sylvia's persistent,
stateful play story while drawing on Latitude's existing scale. Latitude's official fact sheet reports
[more than eight million registered AI Dungeon players and 68 million adventures](https://latitude.io/press);
those figures are company-reported, but the distribution advantage is material even with that caveat.

## Emerging projects to watch, not yet promote as peers

| Project | Public evidence | Current judgement |
| --- | --- | --- |
| **Dungeon Ultimate** | The open-source [Rust desktop game](https://github.com/timoncool/dungeon-ultimate) has 11 stars, 142 commits, deterministic D&D 5e checks/combat/damage, quests, tools, local-GPU or OpenRouter model paths, and Windows packaging. It made 32 post-baseline commits and released [v0.11.0 on 18 August](https://github.com/timoncool/dungeon-ultimate/releases/tag/v0.11.0). | **Medium emerging direct.** Credible local desktop and deterministic loop, but early, D&D-specific, and Windows/NVIDIA-centred for local inference. |
| **MythWeaver** | The [self-hosted repository](https://github.com/xavibonell/mythweaver) has two stars and describes a deterministic D&D 5e engine, generated walkable scenes, and 264 tests. Its own status section says deeper combat, episodic memory, and several longer-horizon systems remain unbuilt. | **Medium-low emerging direct.** Strong conceptual overlap with unusually honest scope, but too early for a top-tier threat. |
| **masterless** | The [early-access product](https://www.nobirds.org/masterless-en.html) offers solo/co-op play, tactical maps, SRD 5.1/5.2 grounding, rules-PDF or homebrew uploads, and a structured world-state mutation pipeline. | **Medium emerging direct.** Worth testing, but rules uploads are not yet evidence of reusable deterministic ruleset packages. |
| **RpgAi** | The [MIT repository](https://github.com/massimobernava/RpgAi) uses a C++ engine and Lua for rules, world state, and relationships, with schema-constrained LLM output, persistent memory, multiple model providers, and a web interface. It has zero stars, one fork, and 38 commits. | **Low emerging watch.** Relevant engine/scripting architecture, no traction signal yet. |

These are additions to a watchlist, not claims of market arrival. Small repositories can be genuine
architectural competitors, but stars and feature lists do not prove play quality, maintainership, or
user demand.

## Adjacent signals

- **WorldSeed** is an important architecture signal rather than a direct TTRPG product. Its
  [open-source multi-agent world engine](https://github.com/AIScientists-Dev/WorldSeed) has 808 stars
  and 53 forks and uses rules-neutral YAML, filtered perception, replay, and deterministic effects
  where possible. Watch its world-state and agent primitives, not its product positioning.
- **AI Dungeon** remains relevant for audience expectations, mobile/cloud accessibility, and
  freeform narrative, but should not be scored as a rules-engine peer.
- **Multihog** belongs in both the emerging-direct and adjacent columns: it is increasingly a game
  simulation platform, but its host dependency still defines the experience.
- The previously noted open-source MCP project **DMCP** remains adjacent and static: its
  [repository](https://github.com/mcp-tool-shop-org/dmcp) is still at nine stars and three forks,
  with no post-baseline commit. It is an integration primitive, not a full end-user competitor.

Generic AI character-chat and story-writing apps were excluded unless an official source showed at
least three of the following: persistent campaign/world state, game-mechanics ownership, a GM play
loop, multiplayer or party play, rules/content authoring, or a product that users can actually run.

## Strategic implications for Sylvia

1. **Lead with the combined contract, not isolated checkboxes.** Desktop, local inference, multiple
   rulesets, deterministic mechanics, persistent campaign plans, and creator-owned portable data
   are each available somewhere else. The defensible claim is their coherent combination with a
   clear GM/Builder boundary.
2. **Make deterministic ownership legible.** Several competitors now say “deterministic,” “system,”
   or “rules engine.” Sylvia should show exactly which state transitions the engine owns, where the
   LLM may propose, and where it must wait. That is a stronger comparison than a feature count.
3. **Treat content portability as a product feature.** Craft's CDF/CLI, Loreweaver's file standard,
   Auferet's embedded save/mode JSON, and local projects all raise the baseline for creator trust.
4. **Prioritise focused comparison research.** The next hands-on comparisons should be Craft,
   Loreweaver, and Voyage. Galdr should remain a URL/account monitor until verifiable public evidence
   returns.
5. **Keep category discipline.** A natural-language custom rule is not automatically a deterministic
   ruleset, a combat hand-off is not a combat engine, and a local save is not local inference. Public
   positioning should preserve those distinctions.

## Recommended watch cadence

- **Every two weeks:** Loreweaver, Craft, Voyage.
- **Monthly:** Auferet, Multihog, Taverna, NarrativeEngine-P, Dungeon Ultimate.
- **Quarterly or on a trigger event:** ArchitectRPG, masterless, MythWeaver, RpgAi, WorldSeed,
  AI Dungeon, and the missing galdr URL/account.
- **Immediate trigger events:** a portable third-party Craft runtime; Loreweaver full combat loop;
  Voyage general availability or builder launch; Auferet desktop/local inference; Multihog standalone
  release; galdr public return.
