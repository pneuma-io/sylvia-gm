# Severed Lands campaign collection

Sixteen authored D&D Campaign Plans: six short combat adventures, four one-shots, three two-act
campaigns, two three-act campaigns and The Rift Awakening, the six-act flagship. This page is safe
to browse for players; the linked Plan files contain GM spoilers.

These adventures target SRD Lite: a reduced spell list of 117 supported spells, without
consumables. Consumables belong to future SRD Full. The application and included content are
still evolving. New Plan revisions retain exact package snapshots; existing campaign saves and
pins are not rewritten.

The [interactive player guide](player-guide.html) gives every Plan a spoiler-free introduction,
a reason to play and concrete D&D activities. Filter by scope, theme or character and save a shortlist.
Open the self-contained HTML file in a browser; it works offline.

Fifteen Plans accept a compatible Player Character. A Debt in Iron includes Tavin Bell, a complete
level-1 Fighter with an authored history and equipment. The other rebuilt one-shots and multi-act
adventures use a solo level-2 baseline with level-1 adjustments; inspect the actual party before
scaling encounters. Compatibility alone does not guarantee encounter balance.

The shorter adventures focus on dangerous places, combat objectives, skills, tools, spells, treasure
and recovery. The Rift Awakening keeps the world-spanning isekai journey and central Door mystery.
Acts organise preparation, not compulsory scenes. Prepared fights allow clever avoidance, surrender
and retreat; duration and model-token use depend on the actual game.

## Flagship

| Plan | Scope / character | Premise |
| --- | --- | --- |
| [The Rift Awakening](the-rift-awakening/plan.md) | 6 acts / compatible character | Find your place in the Severed Lands, a strange world of ancient Doors, dangerous frontiers and people who dream of home. Build friendships, explore its regions and decide whose hopes deserve your trust in an expansive six-act campaign. |

## Combat scenario

| Plan | Scope / character | Premise |
| --- | --- | --- |
| [Last Cart Through](last-cart-through/plan.md) | 1 prepared combat / compatible character | A broken Guild wagon blocks a forest crossing. Keep its driver safe while hungry Rifthounds close in. |
| [The Quarry Turns](the-quarry-turns/plan.md) | 1 prepared combat / compatible character | A Rift-touched boar has made a supply trail impassable. Choose your ground before the hunt becomes a charge. |
| [Hold the Rope](hold-the-rope/plan.md) | 2 prepared combats / compatible character | A surveyor hangs below a Cairnfields crossing. Clear the approach, then keep the rescue line intact. |
| [Dead Inventory](dead-inventory/plan.md) | 1 prepared combat / compatible character | A recovered suit of Warden armour wakes during an Athenaeum stocktake. Get the porters out before the storeroom becomes wreckage. |
| [The Sparring Debt](the-sparring-debt/plan.md) | 2 prepared combats / compatible character | A Lodge trainee owes the town an exhibition. Stand in for two friendly bouts and give the audience something worth remembering. |
| [No One Left Below](no-one-left-below/plan.md) | 2 prepared combats / compatible character | A salvage crew is trapped in a Hollows cellar. Open a way in, then bring them out before the district shifts. |

## One-shot

| Plan | Scope / character | Premise |
| --- | --- | --- |
| [The Cartographer’s Wake](the-cartographers-wake/plan.md) | 1 act / compatible character | A stolen chart at a Nexus wake points to a sealed survey vault. Track the thieves, brave the old works and recover the mapmaker’s last expedition record. |
| [A Light on Breathing Street](a-light-on-breathing-street/plan.md) | 1 act / compatible character | A lost townhouse returns to the Hollows with a lamp burning upstairs. Explore its trapped rooms, confront hostile Rift echoes and get a family keepsake out before the street shifts. |
| [The Honest Thief](the-honest-thief/plan.md) | 1 act / compatible character | Recover stolen wages from a guarded Thornwall counting-house. Scout the rooms, beat its locks and traps, and get the money and a witness through a dangerous exit. |
| [The Bell Below](the-bell-below/plan.md) | 1 act / compatible character | A scavengers’ warning bell has vanished into the tunnels beneath the Hollows. Descend through a flooded Warden workhouse, fight its hungry inhabitants and haul the bell back to daylight. |

## Two-act campaign

| Plan | Scope / character | Premise |
| --- | --- | --- |
| [The Long Way Round](the-long-way-round/plan.md) | 2 acts / compatible character | Track a missing caravan into the Verdant Margin, break it out of a raider-held wayhouse and escort the survivors through a forest that has swallowed the road. |
| [A Debt in Iron](a-debt-in-iron/plan.md) | 2 acts / Tavin Bell | As Lodge trainee Tavin Bell, retrieve a pledged Warden relic from a dangerous salvage vault, then defend the crew carrying it out while deciding how to face your mentor’s debt. |
| [The Scavengers’ Accord](the-scavengers-accord/plan.md) | 2 acts / compatible character | Two crews need the same route into a newly returned Hollows storehouse. Scout a monster-held district, open its dangerous vault and bring the salvage out before their rivalry turns violent. |

## Three-act campaign

| Plan | Scope / character | Premise |
| --- | --- | --- |
| [The Company of the Lost](the-company-of-the-lost/plan.md) | 3 acts / compatible character | Build a salvage company through three dangerous Hollows jobs: a beast-haunted storehouse, a flooded records depot and a contested Warden vault. Your crew’s future depends on what you bring back together. |
| [The Map of Tomorrow](the-map-of-tomorrow/plan.md) | 3 acts / compatible character | Survey a broken route across the Cairnfields. Climb ruined marker stations, survive Riftbeasts and reclaim a guarded causeway so the next caravan has a road worth trusting. |

## Authoring and export

`plan.json` in each directory is the canonical authored preparation. `plan.md` renders the same content. The catalogue is an index, not a new Sylvia runtime format. Character Files under a Plan’s `setting-files/` are included in that Plan’s retained Setting snapshot; they do not modify the base Setting or require manual character creation.

The [BUILD CAMPAIGN PLAN skill](../src/sylvia_gm/agent/skills/build_campaign_plan/SKILL.md) explains the workflow and includes complete short-combat and multi-act JSON/Markdown examples. Builder can retrieve its packaged references with `load_builder_skill`. Read the skill and selected source contracts before editing. Refresh derived Markdown and exact package bindings after changing the authored Plan or its source packages:

```powershell
.venv/Scripts/python.exe scripts/build_severed_lands_campaigns.py --write
```

Check all authored Plans against their retained Ruleset and Setting, required Character Files and readable copies:

```powershell
.venv/Scripts/python.exe scripts/build_severed_lands_campaigns.py
```

Export individual files accepted by Sylvia’s Plan import:

```powershell
.venv/Scripts/python.exe scripts/build_severed_lands_campaigns.py --output-dir D:/Temp/severed-lands-plans
```

The export command validates, saves, exports and reimports every Plan in temporary libraries, checking that content identities survive the round trip. Each `.sylvia-gm-plan` includes its exact Setting/Ruleset snapshot and any fixed character. It does not read or modify the user’s installed library. Import a selected file into Sylvia, then complete New Campaign party selection and setup. The existing Rift helper now refreshes its authored Plan instead of rebuilding a less detailed outline from the Setting’s six act summaries.

Validation proves format, references, character compatibility and importability. These authored adventures have not been live-playtested or automatically balanced for every party. No new creature, faction, economy or engineering engine is installed; each Plan identifies narrative procedures requiring GM adjudication.
