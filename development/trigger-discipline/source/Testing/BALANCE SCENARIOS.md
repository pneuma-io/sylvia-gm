---
type: test-fixture-suite
domain: balance-testing
status: locked
player_canon: false
tags: [type/test-fixture-suite, domain/balance-testing, status/locked]
---
# BALANCE SCENARIOS

**At a glance:** Required scenario families, benchmark maps, tactical policies, measurements, and directional assertions for the automated balance suite. These fixtures test the canonical game; they are not a Campaign Setting, encounter generator, or alternate rules source.

> [!note]- Full suite
> ## 1. Scenario record contract
> Every implemented scenario has a stable ID and serializable record containing:
> - scenario version;
> - exact map/geometry fixture;
> - side A and side B fixture IDs, level, class/specialty build, equipment, current HP/RES/AP, ammunition, posture, position, and knowledge state;
> - any NODE/LINK, medical condition, social fact, objective, timer, or environmental state required by that scenario;
> - TEAM TURN / initiative state;
> - deterministic or seeded policy ID for each controlled actor;
> - parameter-set hash;
> - random seed;
> - terminal conditions;
> - raw event log sufficient for deterministic replay.
>
> A scenario may not insert a hidden modifier to make an expected result happen. If an intended relationship does not emerge from the canonical rules plus [[BALANCE PARAMETERS]], that is a balance/design finding.
>
> ## 2. Test-space coordinate convention
> - Balance maps use a **1m test grid** so distances from [[REFERENCE EQUIPMENT VALUES]] can be represented directly.
> - This is a simulator fixture, not a player-facing square-scale lock.
> - Positions are integer `(x,y)` meters unless a projectile/geometry test requires a sub-meter intersection internally.
> - Cover objects have explicit occupied cells/segments, height class, MATERIAL RESISTANCE, COVER HP, and facing when directional.
> - Actor posture is STANDING unless the scenario says CROUCHED or PRONE.
>
> ## 3. Core map fixtures
>
> ### `MAP_OPEN_100`
> - 100m x 30m open rectangle.
> - No COVER or concealment.
> - Standard duel spawns: `(10,15)` and `(90,15)`.
> - Alternate range spawns place the target at exact test distances of 5m, 10m, 20m, 35m, 50m, 80m, and 100m.
> - Use: raw weapon/range, movement, armor, HIT QUALITY, and mirror checks.
>
> ### `MAP_TIGHT_20`
> - 20m x 20m enclosed room.
> - Four 2m x 2m solid pillars centered at `(6,6)`, `(14,6)`, `(6,14)`, `(14,14)`.
> - Standard spawns `(4,10)` and `(16,10)`.
> - Use: PISTOL/SMG/SHOTGUN HANDLING, contact-range firearm use, CQC, short flanks, grenades.
>
> ### `MAP_STREET_60`
> - 60m x 20m street.
> - Two chest-high 6m-long barriers centered on the lane at x=20 and x=40, each spanning y=7..12 with 2m gaps to either side of the central protected position.
> - Barrier test material defaults to light masonry (`MATERIAL RESISTANCE 3`, `COVER HP 12`) unless the case declares another material fixture.
> - Standard spawns `(5,10)` and `(55,10)`.
> - Use: partial COVER, peeking, movement under fire, SUPPRESSION, OVERWATCH, destructible COVER, grenades.
>
> ### `MAP_BREACH_30`
> - 30m x 20m rectangle divided by a full-height wall along x=15.
> - One 2m doorway at y=9..10; default state CLOSED/blocked by a destructible door (`MR 2`, `COVER HP 8`).
> - At 0 HP the reference door becomes passable across both doorway cells and no longer blocks LOS. It leaves minor debris: crossing costs +1 AP until an ordinary 2 AP clear action or BREAKER CLEAR PASSAGE removes it.
> - Attacker spawn zone x=3..7; defender spawn zone x=22..27.
> - Interior defender side contains two chest-high cover objects at `(21..23,5..7)` and `(21..23,13..15)`.
> - Use: BREACHER, BREAKER/SHOCK/ENFORCER audit scenarios, grenades, sudden close gunplay, CQC.
>
> ### `MAP_COMPOUND_80`
> - 80m x 60m objective map.
> - Side A spawn rectangle x=2..10, y=20..40; Side B x=70..78, y=20..40.
> - Central objective zone is the 10m x 10m square x=35..44, y=25..34.
> - Chest-high cover lines: x=25 at y=10..20 and 40..50; x=55 at y=10..20 and 40..50.
> - Two full-height 6m x 6m structures centered around `(30,30)` and `(50,30)` create blocked sightlines and flank routes.
> - Use: mixed-class team objectives, flanking, holding, support systems, medical extraction, GHOST/BULWARK tradeoffs.
>
> ### `MAP_LONG_1000`
> - 1000m x 60m open/low-cover lane.
> - Firing positions at `(50,30)` and target positions selectable at 100m, 250m, 400m, 600m, 800m, and 950m from shooter.
> - Optional low barrier at shooter position allows supported/braced tests.
> - Use: SERVICE RIFLE vs PRECISION RIFLE vs MANUAL-ACTION PRECISION RIFLE, AIM, SHARPSHOOTER, range breakpoints.
>
> ### `MAP_NETWORK_SITE_40`
> - 40m x 30m physical site with three networked rooms and one external objective.
> - The persistent authored graph is `NET40-A <-> NET40-B <-> NET40-C`. Stable IDs, physical bindings, LINKS, access barriers, privileges, monitoring, and functions below are encounter data and are hashed into replay provenance; only discovery/control state is created during play.
> - NODE `NET40-A` at `(8,15)` is a locally accessible workstation in segment `OFFICE`. Initial hostile GET IN is standard DIFFICULTY; on success it reveals the direct LINK and immediate neighbor `NET40-B` to NETWORKER. Standard MONITORING covers the local session.
> - NODE `NET40-B` at `(20,15)` is the `OFFICE`/`SECURITY` GATEWAY, connected to A and C. Standard hostile PIVOT grants standard-user privilege and reveals the direct C link. Standard MONITORING covers the gateway.
> - NODE `NET40-C` at `(32,15)` is the security controller in segment `SECURITY`. Reaching it follows the B-C LINK; the door/relay objective requires a hard ESCALATE to elevated privilege. Its REMOTE FUNCTION controls a physical door/relay actually represented on the map. Hard MONITORING covers hostile escalation.
> - Every node begins UNDETECTED, uncompromised, and without a FOOTHOLD. Authorized traversal through already controlled nodes is automatic; hostile access uses [[INTRUSION]].
> - A physical FIELD SENSOR / RELAY NODE may be deployed to extend a drone/device link but cannot create software privilege.
> - Use: HACKER vs competent nonspecialist, LOCAL HACKER/NETWORKER, TECH/HACKER boundary, drone/relay support.
>
> ## 4. Tactical policy fixtures
> The simulator must not hide balance behind one omniscient tactical AI. Each relevant scenario runs at least the applicable policies below.
>
> **`POLICY_DIRECT`** — pursue the shortest legal path to the stated objective and use the highest expected immediate-value legal action under currently known information.
>
> **`POLICY_COVER_FIRST`** — prioritize maintaining real COVER/LOS advantage, deliberate fire, and safe reload/recovery over rapid objective progress.
>
> **`POLICY_PRESSURE`** — prioritize movement, closing distance, SUPPRESSION, grenades, and forcing exposure where the actor's build/equipment supports it.
>
> **`POLICY_FIRE_DISCIPLINE`** — prefer deliberate SEMI/BURST fire; use FULL AUTO only when its expected connection/resource value beats deliberate fire under current RANGE/RECOIL/exposure.
>
> **`POLICY_VOLUME`** — prefer BURST/FULL AUTO and SUPPRESSION while ammunition and geometry support it.
>
> **`POLICY_FLANK`** — prioritize routes that change COVER angle / TARGET KNOWLEDGE rather than shortest path.
>
> **`POLICY_OBJECTIVE`** — maximize objective completion probability, including withdrawal/medical/technical actions rather than chasing eliminations.
>
> **`POLICY_SUPPORT`** — protect, stabilize, deploy, hack, influence, observe, or maintain the relevant support objective instead of defaulting to personal damage output.
>
> Policies may use only information the fixture actor actually has under [[TARGET KNOWLEDGE]]. They must not read hidden enemy state to choose actions.
>
> ## 5. Core probability/control scenarios
>
> ### `CHECK-CURVE-*`
> Enumerate d20 outcomes for every benchmark Level and control fixture at routine/standard/hard/severe/extreme test DIFFICULTIES.
>
> Required comparisons:
> - specialist vs GENERALIST CONTROL;
> - TRAINING CONTROL vs TALENT CONTROL;
> - each Level 1/5/10/15/20 snapshot.
>
> Metrics: success %, NATURAL 1/20 handling, marginal value of +1 SKILL and +1 APTITUDE.
>
> Directional assertions:
> - changing DIFFICULTY must never change the relative arithmetic of the same two bonuses;
> - if TALENT CONTROL materially dominates TRAINING CONTROL on professional tasks, report the conflict; do not change the CHECK equation or fixture ladder inside balance optimization.
>
> ## 6. Firearm / protection scenarios
>
> ### `FIRE-SINGLE-RANGE-*`
> One shooter, stationary exposed target, one deliberate shot at each weapon's key RANGE breakpoints plus exact 5/10/20/35/50/100m common anchors.
>
> Metrics: hit %, HIT QUALITY distribution, expected DAMAGE to RES, expected HP bypass, expected total HP loss, AP per expected HP/RES damage.
>
> Run target states:
> - no armor / baseline RES;
> - STANDARD BODY ARMOR;
> - HEAVY BODY ARMOR.
>
> Directional assertions:
> - PISTOL's niche is HANDLING, not superior open-range lethality;
> - SMG gains volume/capacity without inheriting rifle penetration;
> - SERVICE RIFLE is the general firearm benchmark;
> - PRECISION / MANUAL-ACTION PRECISION RIFLES improve long-range/penetration performance while paying HANDLING/RECOIL/cycle tradeoffs;
> - SHOTGUN must be materially strongest in its intended close band and materially worse after its concentration falls off.
>
> ### `FIRE-HANDLING-TIGHT-*`
> Use `MAP_TIGHT_20`, pillars, contact/near-contact positions, and forced angular movement.
>
> Metrics: legal firing opportunities, attack DIFFICULTY from HANDLING, AP to first effective shot, expected close-range damage.
>
> Assertion: compact weapons should produce a measurable constrained-space handling advantage without receiving an invisible damage bonus.
>
> ### `FIRE-AUTO-STRING-*`
> Compare SEMI, 3-round BURST, and FULL AUTO strings over 1-4 consecutive firing actions with no recovery, partial recovery, and full recovery.
>
> FULL AUTO test round counts: 3, 5, 10, 15, 20, subject to actual ammunition/cyclic capability.
>
> Metrics: connections/round, expected damage/AP, expected damage/round, RECOIL state, follow-up hit quality, ammunition efficiency.
>
> Assertions:
> - no universally solved FULL AUTO round count across all ranges/exposures/weapon types;
> - BURST remains more controlled/predictable than equivalent manual FULL AUTO packet where canon intends that distinction;
> - RIFLEMAN's FIRE CONTROL improves sustained useful fire rather than raw first-shot DAMAGE.
>
> ### `PROTECTION-ARMOR-*`
> Same attacker/loadout against no armor, STANDARD BODY ARMOR, and HEAVY BODY ARMOR at identical geometry.
>
> Metrics: shots/AP to DOWNED, HP bypass per hit, RES depletion, movement cost from BURDEN.
>
> Assertions:
> - HEAVY BODY ARMOR always provides greater personal RES contribution than STANDARD;
> - HEAVY's burden must create measurable movement/handling opportunity cost in scenarios that require relocation;
> - armor never behaves as COVER.
>
> ## 7. COVER / material / projectile scenarios
>
> ### `PROJECTILE-MATERIAL-*`
> Exact one-projectile routing against every MATERIAL fixture and firearm.
>
> Metrics/assertions: STOP/REDUCE/PENETRATE result, residual DAMAGE, residual target PEN, residual MATERIAL PEN; no target resolution occurs if the projectile never reaches the target.
>
> ### `COVER-PARTIAL-*`
> Use `MAP_STREET_60` with STANDING/CROUCHED/PRONE targets and full/half/quarter exposure fixtures.
>
> Metrics: target hits, COVER INTERCEPTS, wide misses, cover damage, target damage.
>
> Assertions:
> - failing ACTUAL but meeting OPEN routes into the blocking source;
> - failing OPEN does not magically damage cover;
> - destroying cover during a sequential multi-round string updates later projectile geometry.
>
> ### `COVER-DESTRUCTION-*`
> Sustained SERVICE RIFLE/LMG/precision fire into each cover-material fixture.
>
> Metrics: rounds/AP/time to breach/destroy, ammunition cost, resulting LOS changes.
>
> No universal target time is imposed; use this family to detect cover that is effectively irrelevant or effectively indestructible compared with its declared fixture class.
>
> ## 8. SUPPRESSION / OVERWATCH / trigger-order scenarios
>
> ### `SUPPRESS-CROSSING-*`
> `MAP_STREET_60`; defender starts fully protected behind first barrier and must cross to the second barrier/objective.
>
> Compare: no suppression, SUPPRESSION with each suitable firearm, ordinary deliberate fire policy.
>
> Metrics: crossing survival, HP/RES loss, AP delay, ammunition spent, exposure attacks consumed, cover impacts, objective arrival time.
>
> Assertions:
> - remaining fully protected creates no magical damage;
> - exposing into active fire creates real risk;
> - automatic weapons should usually be more ammunition/time-efficient suppressors without making other firearms incapable of SUPPRESSION.
>
> ### `OW-CROSSING-*`
> Same map, one declared OW ZONE crossing.
>
> Metrics: trigger rate, attack resolution position, watcher AP/ammunition, target continuation.
>
> ### `TRIGGER-SIMULTANEOUS-*`
> Minimum cases:
> 1. two same-side OVERWATCH attacks trigger on one movement step;
> 2. SUPPRESSION exposure and same-side OVERWATCH trigger together;
> 3. GUARDIAN/INTERPOSE-style explicit-before protection and enemy attack trigger together;
> 4. first trigger resolution changes LOS/COVER or incapacitates a later trigger owner;
> 5. nested trigger window creates a new valid event.
>
> Assertion: repeated runs with identical state/choices resolve identically; state updates/revalidation happen between effects exactly through [[TRIGGER ORDERING]].
>
> ## 9. CQC scenarios
>
> ### `CQC-MANEUVER-*`
> Exact probability and AP-efficiency probes for SHOVE, TRIP, THROW, DISARM, BODY CONTROL, WEAPON CONTROL, and ESCAPE across benchmark CQC SKILL/APTITUDE combinations.
>
> Metrics: success %, AP/result, forced distance, control duration under scripted escape attempts.
>
> Assertions:
> - CQC expertise must matter more over the fixture scale than raw APTITUDE alone;
> - one declared maneuver never grants unrelated free effects;
> - a competent nonspecialist can attempt baseline maneuvers; ENFORCER must improve them rather than unlock physics.
>
> ### `CQC-CONTACT-FIRE-*`
> Compare PISTOL/SMG/SERVICE RIFLE/LMG/precision weapon contact firing with and without WEAPON CONTROL.
>
> Assertion: HANDLING produces real constrained-space differences while adjacency alone does not disable firearms.
>
> ## 10. Explosive/effect-device scenarios
>
> ### `GRENADE-PLACEMENT-*`
> Placement at unobstructed and obstructed points with success/failure/scatter states.
>
> ### `GRENADE-WARNING-*`
> Fragmentation grenade with remaining fuse values 4s, 3s, 2s, 1s, 0.5s at distances 2/4/8/12m from reachable COVER.
>
> Metrics: save eligibility, response success, achieved distance/posture/cover, resulting damage.
>
> Assertions:
> - EXPLOSIVE SAVE changes physical state before detonation; it never directly halves damage;
> - cooked/no-warning cases can remove the save when the response window is genuinely gone.
>
> ### `BLAST-COVER-*`
> Fragmentation/flash components against full cover, partial cover, corners, and materials.
>
> Assertion: one detonation uses its pre-detonation geometry snapshot even when that detonation destroys the cover.
>
> ### `SMOKE-LOS-*`
> Trace visual LOS through 0-8m dense-smoke path lengths over the smoke expansion/persistence timeline.
>
> Metrics: LOS blocked/penalized duration, objective crossing value, sensor-specific results.
>
> ## 11. Medical scenarios
>
> Use explicit reference casualties rather than a universal death timer.
>
> **`CASUALTY_BLEED`** — DOWNED, ACTIVE life-threatening hemorrhage; deterioration trigger every 6 tactical seconds of uncontrolled bleeding; STABILIZE requirement is hemorrhage-control-capable equipment/method; if FAILED, reference reversible window 18 seconds with TRAUMA/RESUSCITATION KIT.
>
> **`CASUALTY_AIRWAY`** — DOWNED, ACTIVE airway failure; deterioration trigger after 12 seconds without airway control; STABILIZE requires airway-capable intervention; FAILED reversible window 24 seconds with appropriate equipment.
>
> These are scenario reference conditions only, not universal injury canon.
>
> ### `MED-STABILIZE-*`
> Ordinary competent MEDICINE fixture vs CORPSMAN at Level 1/5/10/15/20, under safe and active-combat AP pressure.
>
> Metrics: probability/time to STABILIZED, resources consumed, casualty survival, lost combat AP.
>
> ### `MED-RESUSCITATE-*`
> Same comparison after controlled FAILED transitions at several points inside the reversible window.
>
> Assertions:
> - non-CORPSMAN treatment remains possible when prerequisites exist;
> - CORPSMAN gains meaningful speed/reliability/outcome advantage;
> - owning a better kit does not create a generic MEDICINE bonus or bypass an impossible condition.
>
> ## 12. Stealth / TARGET KNOWLEDGE scenarios
>
> ### `STEALTH-INFO-*`
> Script observations/movement that move targets among UNAWARE, SUSPICIOUS, APPROXIMATE, LAST KNOWN, and CONFIRMED according to the canonical evidence state model.
>
> Metrics: time/observations to obtain CONFIRMED, time for stale information to degrade, successful ambush condition rate, search AP.
>
> Assertions:
> - information does not improve beyond source precision merely by team sharing;
> - breaking the observation supporting CONFIRMED produces LAST KNOWN as canon specifies rather than requiring a magic stealth reset;
> - GHOST/INFILTRATOR/EVADER benefits must alter real observation, movement, risk, or information-state outcomes rather than grant invisibility.
>
> ## 13. TECH / drone / HACKER scenarios
>
> ### `DRONE-RECON-*`
> `MAP_COMPOUND_80`; compare ordinary user, TECH, FPV-focused, and AUTODRONE-focused fixtures on scouting a hidden objective without exposing the operator.
>
> Metrics: AP to deploy/task, map area observed, TARGET KNOWLEDGE quality, signal failures, drone loss, operator exposure.
>
> ### `DRONE-ARMED-*`
> `MAP_STREET_60`; exercise the separate FPV ONE-WAY FRAGMENTATION PAYLOAD and AUTONOMOUS LIGHT WEAPON MODULE against exposed, partial-cover, and geometry-blocked targets.
>
> Metrics: placement/attack success, actual detonation point, frozen blast revision, target/COVER outcomes, ammunition, RECOIL, drone execution budget, operator AP, and payload/drone loss.
>
> Assertions: drones remain unarmed without a fitted module; the FPV strike consumes its drone and uses DRONE OPERATIONS + PRECISION plus GRENADE/BLAST geometry; autonomous fire requires a valid sensor-bound STANDING ORDER, performs worse than skilled MANUAL CONTROL, and uses ordinary firearm/projectile rules without free operator attacks.
>
> ### `TECH-DEPLOY-*`
> Deploy/recover FIELD SENSOR / RELAY NODE under objective time pressure and degraded signal geometry.
>
> Assertion: TECH improves intended physical-system operation/deployment; it does not gain unauthorized network privilege.
>
> ### `HACK-LOCAL-*`
> `MAP_NETWORK_SITE_40`, valid LOCAL ACCESS to NODE A, objective on NODE A.
>
> Compare competent LOCAL HACKING nonspecialist, HACKER baseline, LOCAL HACKER candidate.
>
> ### `HACK-NETWORK-*`
> Same map; objective on NODE C requires DISCOVER/PIVOT/AUTHENTICATION/ESCALATE-style topology progress from a legitimate initial route.
>
> Compare nonspecialist, HACKER baseline, NETWORKER candidate.
>
> Metrics: AP/checks to objective, DETECTION exposure, privilege achieved, successful objective %, foothold persistence where applicable.
>
> Assertions:
> - HACKER never succeeds without a valid route merely because its numbers are high;
> - LOCAL HACKER should outperform NETWORKER on deep single-node work when builds are otherwise equivalent;
> - NETWORKER should outperform LOCAL HACKER on multi-node topology objectives;
> - TECH should outperform HACKER at intended physical deployment/operation when skill investment is controlled, while HACKER should outperform TECH at subversion once access exists.
>
> ## 14. Social / INFLUENCER scenarios
>
> Social simulations test bounded decision states, not whether a model can roleplay persuasion.
>
> ### `SOCIAL-NO-LEVERAGE-*`
> A requested outcome is plausible but costly to the target; no relevant SOCIAL FACT has been discovered.
>
> ### `SOCIAL-LEVERAGE-*`
> The same target/outcome with a WEAK, STRONG, or DECISIVE test leverage fixture whose factual relevance is explicitly given.
>
> ### `SOCIAL-IMPOSSIBLE-*`
> Requested outcome is psychologically/physically impossible under the fixture (for example, surrender when the target has no reason or ability to comply). No numeric result may convert it into an automatic success.
>
> Compare competent INFLUENCE nonspecialist, INFLUENCER baseline, and DIPLOMAT/HANDLER/ICON candidates in their designated fixture domains.
>
> Metrics: checks/AP/setup to a plausible agreement/objective, probability of success, leverage steps created/used, contact/reputation resource use.
>
> Assertions:
> - LEVERAGE must materially improve relevant plausible attempts;
> - INFLUENCER must create a measurable leverage-efficiency/strength advantage without owning general social permission;
> - no result becomes mind control;
> - HANDLER/ICON benefits must be exercised through actual contact/reputation facts rather than generic combat bonuses.
>
> ## 15. Level-1 class-baseline isolation scenarios
> For every class, compare the named HaremRun fixture against an otherwise identical competent control with the class baseline magnitude set to zero. Keep SKILL/APTITUDE/equipment equal unless the tested role requires a declared different distribution.
>
> - `CLASS-BULWARK-HOLD`: hold the central `MAP_COMPOUND_80` objective under incoming fire; measure survival/hold time and traversal cost.
> - `CLASS-RIFLEMAN-SUSTAIN`: repeated firing strings with partial recovery; measure controlled-fire uptime and ammunition/AP efficiency.
> - `CLASS-BREACHER-CLOSE`: cross from 20m to the close envelope against a known defender; measure AP/exposure to arrive.
> - `CLASS-GHOST-REPOSITION`: change flank angle around central structures; measure AP, exposure windows, and objective arrival.
> - `CLASS-SHARPSHOOTER-SOLUTION`: long-range AIM/firing sequence; measure AP to reliable hit-quality bands.
> - `CLASS-TECH-OPERATE`: deploy/task/recover physical technical equipment; measure AP/time.
> - `CLASS-HACKER-INTRUDE`: valid-route intrusion; measure AP/time/checks without changing access prerequisites.
> - `CLASS-CORPSMAN-TREAT`: stabilize/treat the same casualty; measure AP/reliability/outcome.
> - `CLASS-INFLUENCER-ANGLE`: convert the same relevant SOCIAL FACT into usable leverage; measure setup and effect strength.
>
> Required assertion: every Level-1 class baseline must clear [[BALANCE CONTRACT]]'s meaningful-delta gate in its intended scenario while preserving its stated tradeoff/boundary.
>
> ## 16. Specialty audit / balance-candidate scenarios
> After the human HTML audit freezes the 26 current specialty trees, every specialty receives at least:
> - one **PRIMARY** scenario where its identity should be strongly useful;
> - one **SECONDARY** scenario where it should contribute but not dominate;
> - one **OFF-ROLE** scenario intended to detect generic dominance;
> - one same-class **COUNTERFACTUAL** A/B run with the tested node/path removed but all SKILL/APTITUDE/equipment investment held constant where possible.
>
> Current directions requiring coverage:
> - BULWARK: SHIELD BEARER, ANCHOR, GUARDIAN;
> - RIFLEMAN: GUNNER, OPERATOR, ARSENAL;
> - BREACHER: BREAKER, SHOCK, ENFORCER;
> - GHOST: INFILTRATOR, FLANKER, EVADER;
> - SHARPSHOOTER: SNIPER, SENTINEL, SNAPSHOT;
> - TECH: FPV DRONES, AUTODRONES, FIELD ENGINEER;
> - HACKER: LOCAL HACKER, NETWORKER;
> - CORPSMAN: FIELD MEDIC, TRAUMA, RESUSCITATOR;
> - INFLUENCER: DIPLOMAT, HANDLER, ICON.
>
> Run designated build snapshots at Levels 1/5/10/15/20 where the path has enough choices to exist. Level 10 is the main straight-path Keystone comparison point under [[BENCHMARK FIXTURES]].
>
> A numeric optimizer may tune admitted magnitudes; it may not change the audited node's trigger, topology, mechanical delta, limitation, or prerequisites.
>
> ## 17. Mixed-team objective scenarios
> These detect balance failures that duels cannot.
>
> ### `TEAM-CONTROL-4V4`
> `MAP_COMPOUND_80`, 4v4, central objective must be held continuously for 12 tactical seconds. Run diverse legal compositions and mirrored sides.
>
> ### `TEAM-EXTRACT-4V4`
> One side begins with a DOWNED reference casualty 20m from extraction. Objective is to move/stabilize/extract; opposing side delays/stops them. Elimination is not automatically the objective.
> The deterministic reference placement uses the casualty at `(35,25)` and extraction at `(15,25)` on `MAP_COMPOUND_80`; the remaining rescuers begin adjacent to the casualty.
>
> ### `TEAM-BREACH-4V4`
> `MAP_BREACH_30`; attackers must cross the dividing wall/door and control the defender-side objective for 8 tactical seconds.
> The deterministic defender-side objective is centered at `(24,10)`. The door's authored 0-HP/debris state from the map fixture applies.
>
> ### `TEAM-RECON-HACK-4V4`
> `MAP_NETWORK_SITE_40`; attackers must obtain reliable TARGET KNOWLEDGE of an objective and complete a network/physical interaction; defenders can contest physically or through actual system state.
> The deterministic physical observation objective is at `(36,15)`. The network interaction follows the authored `NET40-A -> NET40-B -> NET40-C` route and requires elevated privilege before operating the controller function.
>
> Metrics for all team scenarios:
> - objective success rate;
> - terminal time;
> - actor DOWNED/death/remaining HP/RES;
> - AP by action family;
> - movement and exposure;
> - shots/rounds/ammunition;
> - COVER damage;
> - SUPPRESSION/OVERWATCH value;
> - treatment/deployment/hacking/social objective contributions;
> - contribution by class/specialty without assigning all value to final damage.
>
> No team composition is required to have exactly 50% win rate against every other composition. Flag compositions/strategies that dominate broadly across unrelated maps/objectives rather than forcing symmetry between intentionally different roles.
>
> ## 18. Required report dimensions
> Every aggregate report must be filterable by:
> - scenario ID/family and map;
> - level;
> - fixture/class/specialty/build;
> - equipment/loadout;
> - policy;
> - side / first TEAM TURN;
> - range band;
> - COVER/exposure/material;
> - armor/BURDEN;
> - parameter-set hash;
> - seed/replay ID.
>
> Primary numeric outputs include where applicable:
> - success/objective/win rate;
> - checks and AP to objective;
> - hit/HIT QUALITY/critical distributions;
> - connection opportunities and actual connected projectiles;
> - RES and HP damage separately;
> - time/AP/rounds to DOWNED;
> - ammunition efficiency;
> - RECOIL state over time;
> - movement and exposure distance/time;
> - cover intercepts/damage/destruction;
> - SUPPRESSION exposure capacity and attacks;
> - medical stabilization/resuscitation survival;
> - TARGET KNOWLEDGE states/time;
> - hacking privilege/detection/objective state;
> - social leverage/setup/outcome state;
> - specialty mechanical-delta effect sizes.

## Connections
[[BALANCE CONTRACT]] · [[BALANCE PARAMETERS]] · [[BENCHMARK FIXTURES]] · [[REFERENCE EQUIPMENT]] · [[REFERENCE EQUIPMENT VALUES]] · [[SPECIALTY]] · [[TARGET KNOWLEDGE]] · [[TRIGGER ORDERING]] · [[SUPPRESSION]] · [[OVERWATCH]] · [[COVER]] · [[PROJECTILE]] · [[CQC ACTION]] · [[GRENADE]] · [[STABILIZE]] · [[RESUSCITATION]]
