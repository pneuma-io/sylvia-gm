# Firearms, Protection, and Effects

**Status:** Siren Protocol v0.1 authored implementation layer  
**Rules authority:** Trigger Discipline

Siren Protocol firearms remain recognizable projectile weapons with real ammunition, magazines, chambers, recoil, range, handling, cover interaction, and penetration. 2225 manufacturing improves packaging, materials, optics, reliability, and integration without turning guns into magic damage emitters.

The PALISADE models below are common reference models used by the pre-made cast. They are **not** the only legal or available firearms in Australia.

## Firearm baseline

| Siren Protocol model | TD family | DAMAGE | Target PEN | Material PEN | RECOIL | Capacity/feed | Handling |
|---|---|---:|---:|---:|---:|---|---:|
| P15 Compact Sidearm | PISTOL | 4 | 1 | 1 | 1 | 15-round magazine + chamber | 0 |
| K30 PDW | SMG | 4 | 1 | 1 | 1 | 30-round magazine + chamber | 1 |
| R55 Service Rifle | SERVICE RIFLE | 5 | 2 | 3 | 2 | 30-round magazine + chamber | 2 |
| L65 Support Weapon | LMG | 5 | 2 | 3 | 2 / 1 braced | 100-round belt box + chamber | 4 |
| B12 Entry Shotgun | SHOTGUN | 7 / 5 / 3 by range | 1 | 1 | 4 | 8-shell tube + chamber | 2 |
| M6 Precision Rifle | PRECISION RIFLE | 6 | 3 | 4 | 3 | 10-round magazine + chamber | 3 |
| M7 Manual Precision Rifle | MANUAL-ACTION PRECISION RIFLE | 7 | 4 | 5 | 4 | 5-round magazine + chamber | 4 |

### Range profiles

- **P15:** 0–15m +0; 16–30m +2; 31–50m +5; beyond +8.
- **K30:** 0–30m +0; 31–60m +2; 61–100m +5; beyond +8.
- **R55:** 0–120m +0; 121–250m +2; 251–400m +5; beyond +8.
- **L65:** 0–150m +0; 151–350m +2; 351–600m +5; beyond +8.
- **B12:** 0–10m D7 +0; 11–20m D5 +2; 21–35m D3 +5; beyond +8.
- **M6:** 0–250m +0; 251–500m +2; 501–800m +5; beyond +8.
- **M7:** 0–300m +0; 301–650m +2; 651–1000m +5; beyond +8.

These are v0.1 implementation values selected from Trigger Discipline's reference-equipment tuning layer. They may be rebalanced later without changing what the weapon models are in the fiction.

## Firearm state remains physical

The profile does not simplify away magazine, chamber, cycling, or reload state.

- P15, K30, R55, M6, and M7 use detachable magazines.
- B12 is pump-action and requires real shell loading and cycling.
- M7 requires a real bolt cycle between shots.
- L65 uses a belt box.
- A weapon cannot fire ammunition it does not physically have available.

The current Trigger Discipline package directly supports detachable-magazine handling and manual cycling; general tube-fed shell loading and some ready/stow handling may still require GM judgment.

## Automatic fire boundary

K30, R55, and L65 preserve their physical automatic/burst fire capabilities. Siren Protocol does **not** invent its own connection-count formula while Trigger Discipline's exact BURST/FULL AUTO connection math remains open.

Until upstream resolution, these modes use current Trigger Discipline guidance/GM judgment. Single-fire resolution remains directly supported.

## Ammunition

Ordinary ammunition is the v0.1 default.

Trigger Discipline explicitly allows Campaign Settings to author special ammunition that alters target PENETRATION, MATERIAL PENETRATION, or both. Siren Protocol does not yet add a special-ammunition catalog. When it does, armour-piercing ammunition should change real penetration behavior rather than receiving a vague quality or damage bonus.

## Body armour

### Civicweave Concealable Armour

A civilian/executive cut using the STANDARD BODY ARMOR profile:

- +4 RES;
- BURDEN 1;
- does not create COVER.

### FlexPlate Tactical Armour

A tactical cut using the same v0.1 STANDARD BODY ARMOR profile:

- +4 RES;
- BURDEN 1;
- does not create COVER.

Civicweave and FlexPlate intentionally share the current mechanical reference profile. Their fiction, cut, mounting, concealment, and social context differ without inventing a generic quality bonus.

### Bastion Heavy Armour

Uses the HEAVY BODY ARMOR reference profile:

- +7 RES;
- BURDEN 3.

It is materially heavier and bulkier. Any movement or stealth consequence must follow the actual Trigger Discipline burden implementation rather than an invented Siren Protocol penalty.

## Wall-4 ballistic shield

The PALISADE Wall-4 is physical directional COVER:

- COVER HP 16;
- MATERIAL RESISTANCE 4;
- BURDEN 4;
- HANDLING 4.

It occupies hands, protects only its actual geometry, can be flanked, and can be penetrated or destroyed. It is not bonus RES and does not form a 360-degree bubble.

## Projected barriers

Projected barriers are real Siren Protocol technology. Trigger Discipline already defines BARRIER as external directional protection using the same COVER/angle logic whether the fiction is a metal panel or a future projected field.

The **PALISADE Aegis Projected Screen** is the v0.1 named example.

Its existence is established, but the authored profile deliberately leaves its COVER HP, MATERIAL RESISTANCE, burden/power draw, and deployment values **unstandardized**. Copying the Wall-4 numbers would falsely imply qualification that has not happened.

No default character loadout uses an Aegis Screen yet.

## Fragmentation, concussion, and smoke

### F7 Fragmentation Grenade

Fuse: 4 seconds.

- 0–4m: DAMAGE 7, target PEN 3, material PEN 2.
- >4–8m: DAMAGE 5, target PEN 2, material PEN 1.
- >8–14m: DAMAGE 3, target PEN 1, material PEN 0.
- >14–20m: DAMAGE 1, target PEN 0, material PEN 0.

Actual blast geometry, cover, stance, corners, and intervening material resolve before target effects.

### Flash-4 Concussion Grenade

Fuse: 4 seconds. No baseline direct DAMAGE in v0.1.

If the target remains physically exposed after valid geometry/save handling:

- 0–4m: +4 DIFFICULTY to materially sight/hearing-dependent actions;
- >4–8m: +3;
- >8–12m: +2;
- duration: through the end of that target's next TEAM TURN.

Solid blocking geometry can prevent the effect.

### Fog-8 Smoke Grenade

- Fuse: 2 seconds.
- Initial radius: 2m.
- Radius after 2 seconds: 6m.
- Maximum radius after 4 seconds: 8m.
- Dense phase: 18 seconds.
- Dissipation: 6 seconds.
- A visual trace through >=4m of dense smoke blocks visual LOS in the v0.1 profile.
- Shorter dense traces add +3 DIFFICULTY to sight-dependent targeting/observation.

Thermal, radar, or other sensors use their actual capabilities rather than automatically inheriting the visual smoke rule.

## Manufacturer rule

PALISADE is a major defence/security corporation and therefore provides useful common reference models, but it does not monopolize firearms or armour. Future catalogs should include competitors, civilian manufacturers, niche workshops, imported weapons, local modifications, and Night Market work without changing the underlying Trigger Discipline weapon schema merely to create brand variety.
