# From an idea to complete campaign preparation

Read a complete pair through `load_builder_skill` with `name: BUILD CAMPAIGN PLAN` and the
listed `reference`. Each JSON file is a whole Campaign Plan v2, not an excerpt. Each Markdown
file renders the same preparation. The pairs mirror the repository's canonical
`campaign-plans/last-cart-through` and `campaign-plans/the-map-of-tomorrow` sources and are checked
against their retained Setting snapshots through ordinary Plan and package admission.

| Request | Read |
| --- | --- |
| A short combat adventure or first small Plan | `references/combat-plan.json` and `references/combat-plan.md`: Last Cart Through. |
| A developed story with consequences across acts | `references/multi-act-plan.json` and `references/multi-act-plan.md`: The Map of Tomorrow. |

These examples bind real Severed Lands and D&D package copies. For another Setting, inspect its
sources, retrieve its definitions and retain the bindings produced by its own draft tool. A copied
example's package identity is not a substitute for selecting that source. The tool names below
refer to Sylvia Builder; repository authors use the equivalent domain/application validators.

## Worked route: Last Cart Through

The brief is: "One short fight defending a broken wagon. Let me bring my own character."

Start with `get_content_definition({"name":"campaign_plan"})`. Discover/select the saved
Severed Lands Setting through the normal source tools. Inspect its identity and search for the
Verdant Margin, Caravaneers' Guild and Rifthound. Read the returned exact files. This determines
the starting place, what the predators are and which combat tools the selected Ruleset supports.

Call `create_campaign_plan_draft` using the returned location path and `player_character_mode:
compatible`. The result supplies exact bindings and a closed JSON scaffold. It does not decide
why this particular wagon matters, how the driver behaves, or what ends the fight. Supply those
decisions by editing the generated files with their current workspace versions.

The finished example makes the following choices:

- Its `premise` gives the player a job and an immediate problem without revealing hidden facts.
- Its starting facts locate the wagon, driver and visible danger. A Player Character can help,
  bargain, retreat or decline; no earlier heroic action is attributed to them.
- The cast and terrain in `situations` let the GM answer practical questions without inventing the
  job's essential details. The driver wants people safe first, cargo second; the food is replaceable.
- `planned_encounters` gives concrete opposition, a manageable solo baseline, tactics, retreat and
  meaningful non-kill objectives. Scaling changes are disclosed before they become committed state.
- The counter for repairing the axle is explicit GM adjudication. Attacks, checks, damage and
  resources use the Ruleset. The plan does not pretend a narrative counter is an installed engine feature.
- The act links to `objective-1`. All its completion conditions are required: the predator danger
  has ended, Nessa and Orlo are accounted for, the medicine and flour have actual dispositions,
  and Nessa responds on safe ground with the losses and payment settled. A promise to repair the
  cart is unfinished; an actual escape with lost cargo can meet the authored failure ending.
  There is no mandatory investigation after the fight.

All those facts live in the JSON preparation. The Markdown is useful to read, but the GM is not
expected to reconstruct missing JSON content from a separate author conversation.

Run `check_campaign_plan_draft({})`. If it reports a bad field or reference, correct that exact
location and check again. Separately review narrative completeness and any required characters.
When the request includes saving to the library, call `publish_campaign_plan({})` and report its
actual result. A draft, a saved Plan and a campaign that has completed party setup are different
states; describe the one the tools establish.

## Worked route: The Map of Tomorrow

The brief is: "Three acts of D&D wilderness exploration and dangerous ruins, with a route the
player can change through their expedition. Keep the stakes smaller than The Rift Awakening."

Use the same source-and-schema route. Read the Cairnfields before inventing the expedition:
the region has unstable ground, climbing routes, hostile creatures and an established sanctuary
at the Door Graveyard. The example uses a local causeway and eastern approach, rather than sending
a new solo character into the deepest raider camps or giving a shorter campaign its own apocalypse.

Read how the acts connect concrete D&D activities:

1. A raider-held marker station has a stair, an exterior ledge, a shutter trap and a recoverable key.
   Combat, stealth, climbing equipment and supported spells can change access. Surveying produces
   bearings and a mechanism diagram, not merely permission to begin the next conversation.
2. Upper ledges and lower gullies trade climbing effort against predator exposure and supplies.
   A local water repair and an aqueduct crossing use tools, saves, equipment and character abilities.
   Choosing a rest route consumes actual travel resources; the established sanctuary remains safe.
3. The causeway combines a wheel chamber, a guardian and a maintenance control. The earlier key,
   diagram, waterworks sketch and rest decisions materially change the final approach. A verified
   pedestrian route is a valid lesser success if opening the cart crossing proves too costly.

Each encounter has terrain, a purpose, bounded statistics, retreat and an explicit lower-level
adjustment. Adapted creatures do not inherit unlisted powers from their full Setting entries.
Traps name detection, bypass, DCs and damage. Failed checks leave useful next actions. Gold, supplies and recovery carry forward between acts. The Lite example excludes consumable
rewards; consumables belong to future SRD Full. The GM admits combat
through the Ruleset; prose descriptions alone do not create engine actors or install procedures.

The concealed spring gives the survey a human consequence, with witnesses and earned reveal
conditions. It does not replace the expedition with interviews. Delivering the map, receiving
payment and deciding what may be published conclude the adventure after the field challenges.
Read each act's linked objective: marker findings and a chosen route close the first; reaching
the causeway with surveyed ground and the spring decision recorded closes the second; a tested
crossing or explicit hazard report delivered to Arel closes the third. Optional valve repairs
affect supplies and relationships without silently becoming another required quest. Withdrawing
early can end the expedition honestly; it does not complete unplayed later acts.
Earlier bargains and an abandoned objective are respected; the GM never forces replacement fights
to reach a count. The six-act flagship retains the central Door mystery and larger revelations.

## Review the adapted result

Read your new Plan's JSON without your earlier conversation. Locate the starting situation,
the core answer, meaningful choices and their outcomes. Check each cross-act dependency against
what earlier play can actually establish. Verify that referenced files exist and that the selected
Ruleset owns every claimed deterministic operation. Use the format validator for structure and
source bindings; use your judgement for whether the content forms the promised adventure.
