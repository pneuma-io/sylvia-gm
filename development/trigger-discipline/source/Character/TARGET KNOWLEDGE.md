---
type: information-state
domain: stealth
status: locked
tags: [type/information, domain/stealth, status/locked]
---
# TARGET KNOWLEDGE

**At a glance:** STEALTH manages what an observer actually knows about a character. Knowledge is stateful information, not an invisibility toggle or generic alert meter.

> [!note]- Full rules
> Each observer, or a team that has successfully shared the information, tracks the strongest TARGET KNOWLEDGE currently supported by evidence.
>
> **UNAWARE**
> - No meaningful reason to believe the character is present.
> - No known position or useful target information.
>
> **SUSPICIOUS**
> - Evidence suggests that something may be wrong or someone may be present, but the observer cannot place the character spatially.
> - Examples include unexplained noise, disturbed equipment, an open door, ambiguous movement, or a sensor anomaly.
> - SUSPICIOUS records information; it does not force an NPC to investigate or behave in a prescribed way.
>
> **APPROXIMATE**
> - The observer has narrowed the character to a real bounded area, route, direction, or region but does not know the exact current GRID position.
> - The bounded area must come from actual evidence such as localized sound, motion detection, a witnessed route, or equivalent information.
>
> **LAST KNOWN**
> - The observer previously knew the character's exact position but no longer has evidence that the character remains there.
> - The old exact position remains recorded as stale information and may still guide searching, movement, attacks, or SUPPRESSION, but it is not treated as the character's current position.
>
> **CONFIRMED**
> - The observer has evidence supporting the character's current exact position, usually direct visual observation or another sufficiently precise live sensor.
> - CONFIRMED lasts only while the observation that supports it remains valid.
>
> These states are not a mandatory ladder. Evidence can move directly between states when the fiction supports it. A localized noise may move UNAWARE directly to APPROXIMATE; direct sight may move UNAWARE directly to CONFIRMED.
>
> **Losing exact contact**
> - When every observer with CONFIRMED knowledge loses the physical observation supporting it, CONFIRMED immediately becomes LAST KNOWN. No STEALTH CHECK is required merely to make blocked sight stop providing exact information.
> - STEALTH may then be required when hidden movement, noise, concealment, searching, or another uncertain action could reveal the new route or position.
>
> **Information becoming stale**
> - LAST KNOWN may degrade to APPROXIMATE when unseen movement means the exact old position is no longer credible but the possible route or area remains constrained.
> - LAST KNOWN or APPROXIMATE may degrade to SUSPICIOUS when enough unseen movement, branching geometry, concealment, time, or misdirection means no useful bounded position remains.
> - SUSPICIOUS returns to UNAWARE only when the fiction gives the observer a credible reason to stop treating the character's presence as relevant. There is no automatic round-based amnesia.
> - Staleness is determined by plausible movement and information, not by an arbitrary universal timer. Exact benchmark distances/timing may be balance parameters for simulation.
>
> **Reacquisition and search**
> - Directly observing the character immediately creates CONFIRMED knowledge when the observation is sufficiently precise.
> - Search, RECON, sensors, or investigation can improve knowledge only as far as the available evidence supports; a successful search does not summon a hidden target.
> - Prefer one active CHECK rather than opposed-roll dice tennis. STEALTH is rolled when the hidden character performs an uncertain action that could affect detection; otherwise the searching side may make the active check where appropriate.
>
> **Evidence from attacks and actions**
> - Attacking does not automatically create CONFIRMED knowledge. The action produces the information its physical signature can support.
> - A visible shooter in a clear firing line may become CONFIRMED; gunfire from behind opaque geometry may provide APPROXIMATE information; a poorly localized signature may create only SUSPICIOUS information.
> - Sensors use the same framework. A motion sensor may only provide APPROXIMATE information while a camera maintaining an exact live track can provide CONFIRMED.
>
> **TEAM KNOWLEDGE**
> - Knowledge begins with the observer or sensor that obtained it. Successfully communicated information can become TEAM KNOWLEDGE.
> - Shared information cannot be more precise than the source information and becomes stale when the underlying observation becomes stale unless another observer maintains better information.
>
> Exact STEALTH difficulties, search AP, sensor precision, and benchmark movement/timing before information becomes stale are balance/reference parameters.

## Connections
[[STEALTH]] · [[TEAM KNOWLEDGE]] · [[AMBUSH]] · [[GHOST]] · [[EVADER]] · [[INFILTRATOR]] · [[RECON]]
