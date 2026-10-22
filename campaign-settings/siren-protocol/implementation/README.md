# Siren Protocol cast and equipment templates

**Status:** incomplete starting state; authored templates  
**Setting canon:** Siren Protocol 0.8.0  
**Ruleset:** Trigger Discipline  
**Profile:** `pneuma-io.haremrun.playable-cast.v0-1`

This directory preserves the authored cast allocations and equipment selections. Complete starting
resources and numerical progression await Wan's decision; these are not playable Character Files.
See [SOURCE.md](../SOURCE.md) for the selected package and current source provenance.

It is intentionally **below setting canon**. Biographies, visual identities, factions, locations, and setting truths remain canonical; the exact Level 5 builds, equipment selections, `side` value, and balance numbers here are authored defaults that a Campaign Plan may replace.

## Files

- [`manifest.json`](manifest.json) — version, provenance, compatibility, and implementation boundaries.
- [`characters.json`](characters.json) — eleven incomplete Trigger Discipline character templates and Siren Protocol loadout references.
- [`equipment.json`](equipment.json) — named Siren Protocol equipment models and v0.1 mechanical values.

Human-readable companions:

- [`../docs/PLAYABLE_IMPLEMENTATION.md`](../docs/PLAYABLE_IMPLEMENTATION.md)
- [`../characters/PLAYABLE_BUILDS.md`](../characters/PLAYABLE_BUILDS.md)
- [`../setting/technology/`](../setting/technology/)

The historical adapter's standalone validator is not shipped in this Setting. Use the contained
Ruleset's declarations and Sylvia's normal draft/package admission. Structure checks do not settle
the missing starting values or establish gameplay support.

## Default build scale

The eleven profiles use Level 5 as a useful authored midpoint:

- primary SKILL 5;
- secondary SKILL 3;
- two familiar SKILLS 2;
- one APTITUDE 1;
- five SPECIALTY choices forming Entry + two Tier-1 + two Tier-2 nodes.

These are retained authored selections, not a general player-creation budget or advancement schedule.

## What is executable today

The contained Ruleset's actual tools and `support/gameplay-resolution.json` own the distinction
between engine execution, hybrid resolution, GM adjudication and unsupported behavior. Read
[the implementation guide](../docs/PLAYABLE_IMPLEMENTATION.md) and the Ruleset's
`references/character-authoring.md` before completing a template. A specialty ID being present
does not establish eligibility or an automated effect.

## Deliberate limits

- `side: allies` is import convenience, not allegiance canon.
- Blaze keeps her fiction identity as the VEHICLES/mobility character; her v0.1 runtime Class is GHOST/EVADER for **on-foot** mobility/escape. GHOST does not improve her vehicle movement.
- Siren keeps her social/HUMINT identity; her v0.1 runtime Class is INFLUENCER/HANDLER.
- Hex retains her authored five nodes. The contained TD package has a FOOTHOLD hook; its availability does not automatically replace one of those choices.
- Nova's default autonomous drones are unarmed. No weapon module or free drone attack routine is invented.
- Vehicles have no bespoke tactical vehicle-combat subsystem in v0.1.
- Projected barriers exist in Siren Protocol fiction and use Trigger Discipline BARRIER/COVER logic, but this profile does not invent unqualified universal barrier numbers.
- BURST/FULL AUTO use the contained TD implementation when actual weapon and ammunition prerequisites hold; the Setting adds no second formula.
- Handling, cycling and loading use the contained Ruleset's actual supported operations. Explicit adjudicated costs remain rulings, not new starting defaults.

## Change policy

Rebalancing this directory does not retcon Siren Protocol fiction. If Trigger Discipline changes its schema, specialty nodes, or accepted balance values, update the implementation profile and provenance rather than rewriting character biography to match old mechanics.
