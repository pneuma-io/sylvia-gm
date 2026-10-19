---
type: mechanic
domain: explosives
status: locked
tags: [type/mechanic, domain/explosives, status/locked]
---
# BLAST GEOMETRY

**At a glance:** Explosive effects resolve from the device's actual detonation point through real distance, geometry, material, and posture rather than applying identical damage to every target inside a flat radius.

> [!note]- Full rules
> - Core resolution order is `DETONATION POINT -> DISTANCE -> GEOMETRY -> MATERIAL -> POSTURE -> EFFECT`.
> - At the instant of one detonation, establish the relevant geometry from the battlefield state immediately before that detonation resolves. All simultaneous EFFECT COMPONENTS of that same detonation use that geometry snapshot unless an explicit component has a later separate timing step.
> - Each explosive/effect device defines one or more EFFECT COMPONENTS. Components may include FRAGMENTATION, BLAST / CONCUSSION, FLASH, SMOKE, or another campaign-supported physical effect.
> - Components use a small number of distance bands rather than continuous physics math. The component's current strength is determined by its distance band before armor/RES or other target consequences resolve.
> - Hard geometry can fully block a component when the component requires a direct propagation path and the intervening material is sufficient.
> - Low or partial barriers protect only according to the body they physically screen from the detonation point. STANCE therefore matters through geometry rather than through a universal grenade-defense bonus.
> - PRONE only helps when the specific component and detonation geometry make reduced exposure physically relevant.
> - Penetrable material may allow a component to pass through according to that component's MATERIAL PENETRATION / material interaction.
> - An explosion may damage destructible COVER. COVER that absorbs the detonation protects against that same detonation according to the pre-detonation geometry even if the detonation destroys it. After all simultaneous components of that detonation resolve, apply the resulting damage/destruction and update battlefield geometry for subsequent actions or later separately timed effects.
> - This same-detonation snapshot is an explicit exception to the normal immediate-update rule used by sequential projectiles, where later rounds in the same firing string can use geometry changed by earlier rounds.
> - If a component has enough material penetration or overmatch to pass through the material, resolve that passage normally instead of treating the barrier as absolute protection.
> - Different components of the same grenade may interact differently with the same geometry.

## Balance parameters
- distance-band radii
- component DAMAGE / MATERIAL PENETRATION / effect strength
- posture interaction by component
- material interaction / attenuation
- destructible-cover damage conversion

## Connections
[[GRENADE]] · [[EXPLOSIVE SAVE]] · [[COVER]] · [[DESTRUCTIBLE COVER]] · [[MATERIAL PENETRATION]] · [[PROJECTILE]] · [[PRONE]]
