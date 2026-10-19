---
type: test-parameter-manifest
domain: balance-testing
status: locked
values_status: tuning
player_canon: false
tags: [type/test-parameter-manifest, domain/balance-testing, status/locked]
---
# BALANCE PARAMETERS

**At a glance:** Machine-oriented manifest of the initial values Codex may vary under [[BALANCE CONTRACT]]. Seeds and bounds below are balance-fixture data only. The allowed parameter surface is locked; the values are deliberately tunable and are not player canon.

> [!note]- Full manifest
> ## Manifest semantics
> Each parameter has one of three authorities:
> - **FIXED FIXTURE** — part of the measuring apparatus. Codex records it but may not optimize it.
> - **TUNABLE** — Codex may vary it only inside the stated domain and constraints.
> - **REVIEW ONLY** — Codex may measure/report it, but changing it requires a human design decision and a canon/manifest update first.
>
> Ranges are inclusive. Integer parameters stay integer. Ordered vectors must remain ordered. A bound is not permission to break a canonical qualitative relationship.
>
> The numeric seeds below deliberately complete the first simulation environment. They are starting coordinates, not a claim that these are good final numbers.
>
> ## 1. Fixed benchmark apparatus
>
> | ID | Authority | Value | Source / constraint |
> |---|---|---|---|
> | `fixture.check.formula` | FIXED FIXTURE | `1d20 + SKILL + relevant APTITUDE + modifiers` | [[CHECK]] |
> | `fixture.skill.range` | FIXED FIXTURE | `0..8` | [[BENCHMARK FIXTURES]] |
> | `fixture.aptitude.range` | FIXED FIXTURE | `0..3` | [[BENCHMARK FIXTURES]] |
> | `fixture.level.snapshots` | FIXED FIXTURE | `1, 5, 10, 15, 20` | [[BENCHMARK FIXTURES]] |
> | `fixture.specialist.progression` | FIXED FIXTURE | exact table in [[BENCHMARK FIXTURES]] | Do not tune the ruler while measuring the game. |
> | `fixture.natural_1` | FIXED FIXTURE | automatic failure where canon says so | [[NATURAL 1]] |
> | `fixture.natural_20.attack` | FIXED FIXTURE | legitimate attack -> CRITICAL HIT | [[NATURAL 20]] / [[HIT QUALITY]] |
> | `fixture.hit_quality.tiers` | FIXED FIXTURE | `HIT, CLEAN HIT, DECISIVE HIT, CRITICAL HIT` | Tier count/order locked by [[HIT QUALITY]]. |
> | `fixture.ap_seconds` | FIXED FIXTURE | `1 AP = 1 tactical second` | Balance-suite conversion only, used to connect AP to fuse/battery/time fixtures. Not player canon. |
> | `fixture.reference_equipment.schema` | FIXED FIXTURE | exact fields/families | [[REFERENCE EQUIPMENT]] |
> | `fixture.cover.damage_formula` | FIXED FIXTURE | `max(0, DAMAGE - MATERIAL RESISTANCE)` | Applies once per resolved projectile/fragment/component impact; MATERIAL PENETRATION remains separate. |
> | `fixture.hacking.network_storage` | FIXED FIXTURE | authored stable NODE/LINK graph | Concrete topology is repository-owned encounter data; discovery/control is runtime state. |
>
> ## 2. Generic CHECK / DIFFICULTY probes
> These tier labels are testing shorthand only; canonical DIFFICULTY remains task-derived.
>
> | ID | Seed | Allowed range | Constraints |
> |---|---:|---:|---|
> | `check.difficulty.routine` | 10 | 8..12 | `< standard` |
> | `check.difficulty.standard` | 14 | 12..16 | `> routine`, `< hard` |
> | `check.difficulty.hard` | 18 | 16..20 | `> standard`, `< severe` |
> | `check.difficulty.severe` | 22 | 20..24 | `> hard`, `< extreme` |
> | `check.difficulty.extreme` | 26 | 24..30 | `> severe` |
>
> Codex may tune these benchmark targets for probability/scenario fixtures. It may not alter the CHECK equation or SKILL/APTITUDE fixture ladder to repair a probability curve.
>
> ## 3. AP, time, movement, HP, and RES
>
> | ID | Seed | Allowed range | Constraints |
> |---|---|---|---|
> | `combat.ap.pool` | 6 | 5..8 | AP refresh procedure remains canonical. |
> | `move.upright_m_per_ap` | 1.0 | 0.1..6.0 | `> crouched > prone`; current base cost 1 AP/m. |
> | `move.crouched_m_per_ap` | 0.5 | 0.05..4.5 | `< upright`, `> prone`; current base cost 2 AP/m. |
> | `move.prone_m_per_ap` | 0.25 | 0.025..2.0 | `< crouched`; current base cost 4 AP/m. |
> | `move.stance_change_ap` | 1 | 1..2 | Meaningful stance changes still cost time. |
> | `move.burden_penalty_m_per_ap_per_point` | 0.5 | 0.25..1.0 | Apply from BURDEN 1-4; the fixture floor is the lesser of 0.5m/AP and the configured PRONE base rate. |
> | `casualty.drag.engage_ap` | 1 | 1..2 | Take hold of a casualty; release is free. |
> | `casualty.drag.one_hand_rate_multiplier` | 0.50 | 0.35..0.65 | Multiplies the rescuer's current movement rate; `< two-hand drag`. |
> | `casualty.drag.two_hand_rate_multiplier` | 0.65 | 0.50..0.80 | Multiplies current movement; `> one-hand drag`, `< carry`. |
> | `casualty.carry.lift_ap` | 2 | 1..3 | Both hands; actual FORCE/terrain constraints remain. |
> | `casualty.carry.rate_multiplier` | 0.75 | 0.60..0.90 | Multiplies current movement; `> two-hand drag`. |
> | `casualty.carry.lower_ap` | 1 | 1..2 | Safe lowering; immediate drop uses actual injury/impact consequences. |
> | `breach.minor_debris.crossing_extra_ap` | 1 | 1..2 | Added once per passage through uncleared minor debris. |
> | `breach.minor_debris.clear_ap` | 2 | 1..3 | Ordinary clearing action; BREAKER CLEAR PASSAGE can waive it. |
> | `hp.vector_l01_l05_l10_l15_l20` | `[10,10,11,11,12]` | each 8..16 | nondecreasing; `L20 <= L1 + 4`; HP remains much flatter than RES. |
> | `res.vector_l01_l05_l10_l15_l20` | `[6,8,10,12,14]` | each 4..24 | nondecreasing; may scale more strongly than HP. |
>
> Test-only movement composition:
> `effective DISTANCE:AP = stance rate - BURDEN penalty + applicable class/specialty movement delta`, minimum 0.5m/AP unless an explicit canonical state prevents movement entirely.
>
> ## 4. Locked Level-1 class-baseline magnitude seeds
> The existence/direction of each class baseline is canonical. Only the magnitude below is tunable.
>
> | ID | Seed | Allowed range | Canonical direction |
> |---|---:|---:|---|
> | `class.bulwark.res_bonus` | +3 RES | +1..+6 | BULWARK has higher baseline RES. |
> | `class.bulwark.move_delta_m_per_ap` | -0.75 | -1.50..-0.50 | BULWARK is less mobile. |
> | `class.ghost.move_delta_m_per_ap` | +1.0 | +0.5..+2.0 | GHOST is more mobile. |
> | `class.breacher.close_move_delta_m_per_ap` | +2.0 | +1.0..+3.0 | Only genuine closing movement toward a known enemy. |
> | `class.breacher.close_envelope_m` | 12 | 6..20 | Qualifying close-engagement distance for CLOSE ASSAULT. |
> | `class.rifleman.extra_recoil_recovery_per_ap` | +1 | +1..+2 | Faster recovery, never zero-cost sustained fire. |
> | `class.sharpshooter.extra_aim_range_offset_per_ap` | +1 DIFFICULTY | +1..+3 | Additional RANGE-derived difficulty offset from AIM only. |
> | `class.tech.operation_ap_discount` | 1 AP | 1..2 | Intended physical-system operation/deployment only; minimum final action cost 1 AP. |
> | `class.hacker.intrusion_ap_discount` | 1 AP | 1..2 | Only after a valid access route exists; minimum final action cost 1 AP. |
> | `class.corpsman.medicine_ap_discount` | 1 AP | 1..2 | Eligible operational MEDICINE only; minimum final action cost 1 AP. |
> | `class.corpsman.medicine_check_bonus` | +2 | +1..+4 | Does not create unsupported treatment capability. |
> | `class.influencer.leverage_setup_ap_discount` | 1 AP | 1..2 | Only after a relevant SOCIAL FACT exists; minimum final setup cost 1 AP. |
> | `class.influencer.leverage_strength_steps` | +1 tier | +1..+2 | Uses test leverage tiers below; does not create mind control or implausible outcomes. |
>
> ## 5. AIM, RECOIL, firing time, and HIT QUALITY
>
> ### AIM
> | ID | Seed | Allowed range | Constraints |
> |---|---:|---:|---|
> | `fire.aim.attack_difficulty_offset_per_ap` | -2 | -1..-3 | Specific maintained firing solution only. |
> | `fire.aim.max_ap_on_one_solution` | 2 | 1..3 | Additional AIM beyond the cap has no fixture benefit unless an explicit ability says otherwise. |
>
> ### Fire-action time
> | ID | Seed | Allowed range | Constraints |
> |---|---:|---:|---|
> | `fire.semi_single.ap` | 2 | 1..3 | One deliberate SEMI AUTO shot. |
> | `fire.burst_3.ap` | 2 | 1..3 | 3-round BURST when the weapon supports it. |
> | `fire.full_auto.rounds_per_ap` | 5 | 3..8 | Flexible FULL AUTO string; `AP = ceil(rounds / value)`, minimum 1. |
> | `fire.one_handed_manipulation_surcharge_ap` | 2 | 1..3 | Added only when a physically possible FIREARM MANIPULATION is performed one-handed; the operation's ordinary cost and physical steps remain. |
>
> ### Persistent RECOIL fixture
> - `current RECOIL` is an integer penalty state.
> - Before an ATTACK ROLL, add current RECOIL to ATTACK DIFFICULTY.
> - A firing action then adds state according to the action formulas below; the newly generated state affects later firing, while same-string control is represented by the firing-mode modifier / connection math.
> - One AP of genuine non-firing recovery time reduces current RECOIL by the recovery rate, to minimum 0. Maintaining SUPPRESSION is not recovery time.
>
> | ID | Seed | Allowed range | Constraints |
> |---|---:|---:|---|
> | `recoil.recovery_per_ap` | 1 | 1..2 | RIFLEMAN adds its class delta. |
> | `recoil.semi_gain_multiplier` | 1.0 | 0.5..1.5 | gain = round(`weapon RECOIL * multiplier`), minimum 1 after firing. |
> | `recoil.burst_extra_state` | +1 | 0..+2 | gain = weapon RECOIL + extra. |
> | `recoil.full_auto_round_step` | 5 rounds | 3..8 | gain = weapon RECOIL + ceil(rounds / step) - 1. |
> | `recoil.burst_attack_difficulty_multiplier` | 1.0 | 0.5..1.5 | current-string modifier = round(`weapon RECOIL * multiplier`). |
> | `recoil.full_auto_attack_difficulty_multiplier` | 1.0 | 0.5..1.5 | base current-string modifier from weapon RECOIL. |
> | `recoil.full_auto_extra_difficulty_per_step` | +1 | 0..+2 | additional modifier for each full-auto round step beyond the first. |
>
> ### HIT QUALITY thresholds and effects
> Margin means final attack total minus ACTUAL DIFFICULTY after a legitimate hit.
>
> | ID | Seed | Allowed range | Constraints |
> |---|---:|---:|---|
> | `hit_quality.clean_margin` | 5 | 3..7 | `> 0`, `< decisive`. |
> | `hit_quality.decisive_margin` | 10 | 7..14 | `> clean`, `< critical`. |
> | `hit_quality.critical_margin` | 15 | 11..21 | `> decisive`; NATURAL 20 still guarantees CRITICAL. |
> | `hit_quality.clean_target_pen` | +1 | +1..+2 | target PEN only. |
> | `hit_quality.decisive_damage` | +1 | +1..+3 | includes CLEAN benefit. |
> | `critical.deep_target_pen` | +2 | +1..+4 | target PEN only. |
> | `critical.devastating_damage` | +2 | +1..+4 | additional DAMAGE after DECISIVE benefits. |
>
> ### Connection opportunities
> **3-round BURST seed table**: `HIT 1 / CLEAN 2 / DECISIVE 3 / CRITICAL 3`, with at most one connected projectile resolving as CRITICAL.
>
> Each table cell is TUNABLE from `0..rounds fired`, but must be nondecreasing by HIT QUALITY and HIT must provide at least one connection on a successful legitimate burst.
>
> **FULL AUTO seed formula**:
> `connections = min(rounds fired, 1 + floor((rounds fired - 1) / connection_round_step) + quality_bonus)`
>
> with `quality_bonus = HIT 0 / CLEAN 1 / DECISIVE 2 / CRITICAL 3`, before target-exposure caps. CRITICAL still marks at most one connected projectile as a CRITICAL HIT.
>
> | ID | Seed | Allowed range |
> |---|---:|---:|
> | `full_auto.connection_round_step` | 5 rounds | 3..8 |
> | `full_auto.quality_bonus.clean` | +1 | 0..+2 |
> | `full_auto.quality_bonus.decisive` | +2 | +1..+4 |
> | `full_auto.quality_bonus.critical` | +3 | +2..+5 |
> | `exposure_cap.full` | no extra cap | FIXED FIXTURE |
> | `exposure_cap.half` | 3 connections | 2..6 |
> | `exposure_cap.quarter` | 1 connection | 1..3 |
> | `cover.actual_difficulty_offset.full` | +0 | FIXED FIXTURE |
> | `cover.actual_difficulty_offset.half` | +2 | +1..+3 |
> | `cover.actual_difficulty_offset.quarter` | +5 | +3..+7; `> half` |
>
> These are test representations of the locked connection-opportunity concept. Codex may tune listed numbers but may not replace the one-roll / finite-connection procedure.
>
> ## 6. OVERWATCH and SUPPRESSION
>
> | ID | Seed | Allowed range | Constraints |
> |---|---:|---:|---|
> | `overwatch.setup_ap` | 2 | 1..3 | Uses PREP/OVERWATCH procedure. |
> | `overwatch.arc_degrees` | 90 | 45..180 | Testing representation of declared arc/area. |
> | `overwatch.shot_allowance` | 1 | 1..3 | Per prepared OVERWATCH unless an explicit ability changes it. |
> | `suppression.setup_ap` | 2 | 1..4 | Real AP expenditure. |
> | `suppression.rounds_per_5m_zone` | 5 | 3..10 | Larger connected area requires proportionally more committed ammunition. |
> | `suppression.exposure_capacity_round_divisor` | 10 | 5..15 | capacity = max(1, floor(committed rounds / divisor)). |
> | `suppression.attack_difficulty_modifier` | +4 | +2..+6 | AIM never applies to SUPPRESSION ATTACK. |
> | `suppression.persistence_team_turns` | 1 opposing TEAM TURN | FIXED FIXTURE | Canonical baseline persistence remains as written. |
>
> SUPPRESSION RECOIL uses the FULL AUTO gain formula with the actual committed rounds. It does not receive a separate magic recoil value.
>
> ## 7. CQC fixture
>
> | ID | Seed | Allowed range | Constraints |
> |---|---:|---:|---|
> | `cqc.defense_base` | 10 | 8..12 | CQC SKILL remains more influential by range than APTITUDE. |
> | `cqc.defense_skill_coefficient` | 1.0 | 1.0..1.5 | Multiplies defender CQC SKILL. |
> | `cqc.defense_aptitude_coefficient` | 1.0 | 0.5..1.0 | Must not exceed SKILL coefficient. |
> | `cqc.action_ap` | 2 | 1..3 | One maneuver -> one CHECK -> one declared result. |
> | `cqc.escape_ap` | 2 | 1..3 | Uses ESCAPE procedure. |
> | `cqc.shove_distance_m` | 2 | 1..4 | Successful SHOVE only. |
> | `cqc.throw_distance_m` | 2 | 1..4 | Successful THROW only. |
> | `cqc.contact_handling_multiplier` | 1.0 | 0.5..1.5 | Multiplies weapon HANDLING when geometry genuinely constrains contact fire. |
>
> Individual maneuver situational modifiers may be scenario inputs. Codex may not add an opposed-roll exchange or automatic counterattack to tune CQC.
>
> ## 8. Explosive response fixture
> [[REFERENCE EQUIPMENT VALUES]] remains the seed source for grenade fuses, bands, DAMAGE/PEN, smoke spread, and device handling.
>
> Test-only EXPLOSIVE SAVE warning bands:
> - `>=3.0s` remaining: base response modifier `0`;
> - `>=2.0s and <3.0s`: `+2 DIFFICULTY`;
> - `>=1.0s and <2.0s`: `+5 DIFFICULTY`;
> - `<1.0s`: no baseline save unless an explicit ability/effect provides a physically valid response.
>
> | ID | Seed | Allowed range |
> |---|---:|---:|
> | `explosive_save.normal_warning_s` | 3.0 | 2.0..4.0 |
> | `explosive_save.hard_warning_s` | 2.0 | 1.5..3.0 |
> | `explosive_save.minimum_warning_s` | 1.0 | 0.5..1.5 |
> | `explosive_save.hard_modifier` | +2 | +1..+4 |
> | `explosive_save.severe_modifier` | +5 | +3..+7 |
> | `grenade.scatter.distance_by_failure_margin_m` | `[1,2,4,6]` | each within `[0..2,1..4,2..6,3..10]`; strictly increasing for failure-margin bands `1..4`, `5..9`, `10..14`, `15+` |
> | `grenade.scatter.direction_count` | 8 | FIXED FIXTURE; equal grid compass/diagonal directions before geometry constrains the landing point |
>
> Maximum response movement is bounded by the actual remaining tactical time using the current movement fixture and geometry; EXPLOSIVE SAVE never grants teleport movement.
>
> ## 9. Medical reference action seeds
> These values exist only so the locked critical-care state machine can be simulated.
>
> | ID | Seed | Allowed range | Constraints |
> |---|---:|---:|---|
> | `medicine.stabilize_ap` | 3 | 2..5 | Requires real intervention/equipment. |
> | `medicine.stabilize_difficulty` | 14 | 12..20 | Scenario condition may add modifiers. |
> | `medicine.resuscitation_ap` | 6 | 4..10 | Requires reversible FAILED condition and appropriate equipment. |
> | `medicine.resuscitation_difficulty` | 18 | 14..24 | Scenario condition may add modifiers. |
> | `medicine.field_treatment_ap` | 3 | 2..5 | Ordinary serious battlefield treatment fixture. |
>
> LIFE-THREATENING CONDITION deterioration triggers and reversible windows remain condition-specific scenario/reference data; Codex must not invent one universal death clock.
>
> ## 10. Technical, drone, hacking, and social test-economy seeds
>
> [[REFERENCE EQUIPMENT VALUES]] supplies drone movement, durability, battery, sensors, signal, payload, deployable setup, and medical/technical equipment seeds.
>
> | ID | Seed | Allowed range | Constraints |
> |---|---:|---:|---|
> | `hacking.standard_intrusion_ap` | 2 | 1..4 | Only after the relevant action has a plausible access route and prerequisites. |
> | `hacking.hard_intrusion_ap` | 3 | 2..5 | Used for deeper privilege/pivot fixture actions. |
> | `hacking.standard_intrusion_difficulty` | 14 | 12..16 | Standard authored access barrier. |
> | `hacking.hard_intrusion_difficulty` | 18 | 16..20 | Hard/deeper authored access barrier; `> standard`. |
> | `hacking.stay_quiet_difficulty` | 14 | 12..18 | Rolled only when hostile action visibility under real MONITORING is uncertain and material. |
> | `hacking.visibility.low_difficulty_offset` | -2 | -4..-1 | LOW HACKING VISIBILITY only. |
> | `hacking.visibility.standard_difficulty_offset` | 0 | FIXED FIXTURE | STANDARD HACKING VISIBILITY baseline. |
> | `hacking.visibility.high_difficulty_offset` | +2 | +1..+4 | HIGH HACKING VISIBILITY only. |
> | `hacking.detection.failure_steps` | 1 | 1..2 | Failed STAY QUIET advances evidence by this many states, capped at CONFIRMED. |
> | `hacking.retry.lock_without_state_change` | false | FIXED FIXTURE | Identical retries remain legal while route/target state remains available. |
> | `hacking.authorized_function_ap` | 1 | FIXED FIXTURE | Operate one already-authorized benchmark REMOTE FUNCTION; traversal remains automatic. |
> | `social.fact_to_leverage_setup_ap` | 2 | 1..4 | Relevant SOCIAL FACT required. |
> | `social.leverage.weak_modifier` | -2 DIFFICULTY | -1..-3 | Testing shorthand, not a player-facing tier name lock. |
> | `social.leverage.strong_modifier` | -4 DIFFICULTY | -3..-6 | Stronger than weak. |
> | `social.leverage.decisive_modifier` | -6 DIFFICULTY | -5..-8 | Stronger than strong; requested outcome still must be plausible. |
>
> Armed-drone attack economy is now admitted only through the two explicit modules in [[REFERENCE EQUIPMENT VALUES]]. Drones remain unarmed by default.
>
> | ID | Seed | Allowed range | Constraints |
> |---|---:|---:|---|
> | `equipment.fpv_fragmentation_payload.attack_ap` | 1 | 1..2 | Operator AP; movement remains separate; final cost minimum 1 AP. |
> | `equipment.autodrone_light_weapon.damage` | 4 | 2..6 | Ordinary firearm DAMAGE. |
> | `equipment.autodrone_light_weapon.target_pen` | 1 | 0..3 | Never greater than DAMAGE. |
> | `equipment.autodrone_light_weapon.material_pen` | 1 | 0..3 | Ordinary projectile MATERIAL PENETRATION. |
> | `equipment.autodrone_light_weapon.range_breakpoints_m` | `[15,30,50]` | 70%-130% per seed | Strictly increasing. |
> | `equipment.autodrone_light_weapon.range_modifiers` | `[0,2,5,8]` | seed +/-2 | Nondecreasing. |
> | `equipment.autodrone_light_weapon.recoil` | 2 | 1..5 | Persistent ordinary firearm RECOIL. |
> | `equipment.autodrone_light_weapon.capacity` | 10 | 5..15 | Limited external magazine; module remains SEMI AUTO only. |
> | `equipment.autodrone_light_weapon.reload_ap` | 3 | 2..5 | Adjacent capable character; the drone cannot self-reload. |
> | `equipment.autodrone_light_weapon.attack_ap_equivalent` | 2 | 1..2 | Consumes drone execution budget, not operator AP; cannot exceed `equipment.autodrone.actions_per_turn`. |
> | `equipment.autodrone_light_weapon.targeting_bonus` | +4 | +2..+5 | Autonomous ATTACK ROLL only; remains below the Level-1 skilled MANUAL CONTROL benchmark. |
>
> ## 11. Reference-equipment numeric family permissions
> Every numeric value currently defined in [[REFERENCE EQUIPMENT VALUES]] is TUNABLE only under the following initial bounds. Schema, physical behavior, fire modes, mechanisms, and capability presence are fixed.
>
> **Firearms**
> - DAMAGE: seed +/-2, integer, minimum 1, maximum 10.
> - target PENETRATION: seed +/-2, integer, minimum 0, never greater than DAMAGE after ordinary caps.
> - MATERIAL PENETRATION: seed +/-2, integer, minimum 0, maximum 8.
> - RANGE breakpoints: each seed breakpoint may move to 70%-130% of seed while remaining strictly ordered; RANGE DIFFICULTY modifiers may move by +/-2 but must remain nondecreasing with distance.
> - RECOIL: integer 1..5.
> - HANDLING: integer 0..4. The relative niche constraints in [[REFERENCE EQUIPMENT VALUES]] must still hold; the optimizer cannot make every weapon equally handy.
> - capacity/feed count: 50%-150% of seed, integer where countable, while preserving the equipment family's feed/mechanism identity.
> - reload/cycle AP: 1..6 and consistent with the physical feed/mechanism; a manual-action weapon must retain a real cycle cost.
> - CYCLIC RATE may move 70%-130% of seed where numeric, but the fire-mode/mechanism identity is fixed.
>
> **Protection**
> - armor RES contribution: seed +/-3, minimum +1.
> - armor BURDEN: integer 0..4, but HEAVY BODY ARMOR must remain more burdensome and more protective than STANDARD BODY ARMOR.
> - BALLISTIC SHIELD COVER HP: 8..30.
> - BALLISTIC SHIELD MATERIAL RESISTANCE: 2..6.
> - BALLISTIC SHIELD BURDEN/HANDLING remain 0..4 and must preserve the deliberate large-shield cost.
>
> **Explosives/effect devices**
> - fuse: 50%-150% of seed unless the warning-band scenario would become physically nonsensical.
> - radius/band endpoints: 70%-130% of seed and strictly ordered.
> - DAMAGE / target PEN / MATERIAL PEN: seed +/-2, nonnegative.
> - FLASH/CONCUSSION DIFFICULTY effect: seed +/-2 and nonincreasing with distance.
> - smoke expansion/persistence/radius: 60%-150% of seed while preserving growth then dissipation.
>
> **Medical / technical equipment**
> - supply counts, setup AP, durability, movement, battery, signal, sensor distance, payload, coverage, and relay ranges: 60%-150% of seed unless a narrower canonical physical limit exists.
> - armed-drone module values use the explicit bounds above; capability presence, default-unarmed status, fire mode, consumption, order/sensor prerequisites, and ordinary projectile/blast routing are locked.
> - No equipment parameter may become a generic CHECK bonus unless the canonical equipment already provides one.
>
> ## 12. Material-resistance scenario fixtures
> These are test materials, not a Campaign Setting catalog.
>
> | Material fixture | MATERIAL RESISTANCE seed | COVER HP seed | Initial tuning range |
> |---|---:|---:|---|
> | light interior / drywall-like layer | 1 | 4 | MR 0..2; HP 2..8 |
> | timber / light furniture | 2 | 8 | MR 1..3; HP 4..14 |
> | light masonry / vehicle-panel class | 3 | 12 | MR 2..4; HP 8..20 |
> | reinforced plate / shield-class barrier | 4 | 16 | MR 3..5; HP 10..26 |
> | reinforced concrete / heavy structure | 5 | 24 | MR 4..6; HP 16..40 |
> | hardened structural barrier | 6 | 32 | MR 5..8; HP 20..50 |
>
> Material order must remain monotonic. These fixtures exercise STOP / REDUCE / PENETRATE routing rather than asserting that all real materials share one universal rating.
>
> ## 13. Specialty magnitude admission
> The all-class structural audit and human grill are complete for this balance candidate. The IDs below are explicitly admitted for optimization; unlisted topology, prerequisites, state transitions, triggers, capability boundaries, and canonical prose remain locked.
>
> For every admitted specialty quantity:
> - each explicit numeric magnitude receives a stable ID of the form `specialty.<specialty>.<node>.<quantity>`;
> - its initial seed is the audited candidate value;
> - ordinary scalar magnitudes default to a 50%-150% search window, rounded appropriately;
> - AP costs/discounts stay integer and final action cost remains at least 1 AP unless the audited node explicitly creates a zero-cost exception;
> - DAMAGE, PENETRATION, RES, RECOIL, movement, ammunition, area, duration, contact/reputation reach, treatment, information, and similar magnitudes must preserve the audited node's direction and limitation;
> - topology, trigger, prerequisites, action type, state created, and conceptual mechanical delta are immutable to Codex.
>
>
> ### Approved repair-node magnitudes
>
> | ID | Seed | Allowed range | Constraint |
> |---|---:|---:|---|
> | `specialty.local_hacker.service_access.difficulty_offset` | -2 | -4..-1 | Genuine direct/local initial GET IN only. |
> | `engineering.field_reconfiguration_ap` | 3 | 2..5 | Physical reorientation of the already deployed reference FIELD SENSOR / RELAY NODE; not deployment and no capability change. |
> | `stealth.break_contact_ap` | 2 | 2..4 | Ordinary post-LOS STEALTH attempt after every exact observer has lost LOS. |
> | `specialty.networker.foothold.ap_discount` | 1 | 1..2 | Compromised remotely reached NODE through a valid NETWORK route; minimum final 1 AP; ordinary monitoring, detection, persistence, and remediation remain. |
> | `specialty.shield_bearer.shield_training.burden_reduction` | 1 | 1..2 | Effective use only; shield's physical BURDEN remains unchanged. |
> | `specialty.shield_bearer.shield_training.handling_reduction` | 1 | 1..2 | Compatible one-handed actions only. |
> | `specialty.fpv_drones.nap_of_the_earth.exposure_band_reduction` | 1 | FIXED FIXTURE | Cannot create full COVER. |
> | `specialty.fpv_drones.snap_peek.ap_discount` | 1 | 1..2 | Combined out/observe/return sequence; minimum 1 AP. |
> | `specialty.guardian.body_cover.exposure_band_reduction` | 1 | FIXED FIXTURE | Requires actual body screening; cannot create full COVER. |
> | `specialty.anchor.firing_edge.aim_steps` | 1 | 1..2 | One maintained declared edge/solution. |
> | `specialty.field_engineer.rapid_deployment.ap_discount` | 1 | FIXED FIXTURE | Stacks after TECH to minimum final 1 AP. |
> | `specialty.field_engineer.power_budget.action_ap` | 2 | 1..3 | Physically connected compatible pair; minimum 1 AP. |
> | `specialty.field_engineer.power_budget.transfer_seconds` | 300s | 120..600s | One-for-one donor/recipient runtime for the equal-load reference pair; cannot exceed donor remainder. |
> | `specialty.gunner.fire_through_the_opening.difficulty_offset` | -2 | -4..-1 | Narrow suppression zone only. |
> | `specialty.gunner.fire_through_the_opening.max_width_m` | 2 | 1..3 | Cannot cover a wider zone simultaneously. |
> | `specialty.gunner.fire_superiority.maintenance_ap_discount` | 1 | 1..2 | Fresh ammunition and continuing RECOIL required; minimum 1 AP. |
> | `specialty.evader.slip_the_fire.movement_multiplier` | 0.50 | 0.35..0.75 | Applies to the movement AP that triggers SUPPRESSION. |
> | `specialty.evader.slip_the_fire.difficulty_offset` | +2 | +1..+4 | SUPPRESSION ATTACK only. |
> | `specialty.sentinel.hold_under_fire.trigger_priority` | 1 | FIXED FIXTURE | Prepared shot precedes same-action suppression. |
> | `specialty.sentinel.second_trigger.additional_triggers` | +1 | +1..+2 | Every retained shot still consumes ammunition and RECOIL. |
> | `specialty.sentinel.deep_sector.extra_depth_m` | +10m | +5..+20m | Continuous existing sightline only; no corner vision. |
> | `specialty.enforcer.heavy_contact.res_damage` | 1 | 1..3 | Successful forceful contact only; ordinary DAMAGE remains separate. |
> | `specialty.shield_bearer.draw_the_burst.extra_capacity_cost` | 1 | 1..2 | Requires a physical shield intercept. |
> | `specialty.field_medic.continue_care.progress_retained` | 1 | FIXED FIXTURE | Lost access/supplies or changed condition may invalidate progress. |
> | `specialty.anchor.hold_under_fire.dormant_team_turns` | 1 | FIXED FIXTURE | Same position/edge; cannot trigger without LOS. |
> | `specialty.anchor.prepared_garrison.ap_discount` | 1 | 1..2 | Minimum 1 AP and original zone remains valid. |
> | `specialty.guardian.protect_the_operator.ap_discount` | 1 | 1..2 | Adjacent ally commits at least 2 AP; minimum 1 AP. |
> | `specialty.guardian.last_guard.move_m` | 1 | FIXED FIXTURE | Adjacent legal safer position; triggers remain active. |
> | `specialty.shield_bearer.corner_guard.prep_ap` | 1 | 1..2 | One declared corner direction. |
> | `specialty.shield_bearer.step_together.stance_ap_discount` | 1 | FIXED FIXTURE | Both actors spend movement AP and remain adjacent. |
> | `specialty.field_medic.combat_medicine.res_restore` | 1 | 1..3 | First treatment of each distinct eligible injury/condition. |
> | `specialty.field_medic.second_wind.temporary_ap` | 1 | FIXED FIXTURE | Non-attack uses; once per casualty per encounter. |
> | `injury.repairable_forearm.ordinary_persistent_difficulty` | +4 | +3..+6 | `INJURY_REPAIRABLE_FOREARM_TRAUMA_V1` only; actions materially requiring the affected hand. |
> | `specialty.trauma.salvage.persistent_difficulty_reduction` | 2 | 1..3 | One injury-authored severity step; must remain below the ordinary persistent penalty and cannot stack. |
> | `specialty.resuscitator.resuscitation.window_hold` | 1 | FIXED FIXTURE | Only after valid care begins before window expiry. |
> | `specialty.diplomat.negotiating_position.counteroffers` | 1 | FIXED FIXTURE | Materially changed plausible offer only. |
> | `specialty.autodrones.reacquire.search_radius_m` | 5m | 3..10m | Search remains bounded by last physically credible information. |
> | `specialty.autodrones.machine_squad.additional_drones` | +2 | +1..+3 | Each drone remains a separate physical unit. |
> | `specialty.autodrones.machine_squad.group_command_ap` | 1 | 1..2 | One compatible group-order action; individual execution remains physical. |
> | `specialty.autodrones.machine_squad.contingency_count` | 1 | 1..2 | Predefined sensor-bound contingency only. |

+>
> ### Audited shared family seeds
>
> These families provide units and bounded seeds; they are not generic scores. Each concrete ID in the following table is frozen to its named node/action, prerequisite, limitation, and measured rules quantity.
>
> | Family | Seed | Allowed range |
> |---|---:|---:|
> | `ap_discount` | 1 | 1..2 |
> | `target_pen_bonus` | 1 | 1..2 |
> | `material_pen_bonus` | 1 | 1..2 |
> | `damage_bonus` | 1 | 1..3 |
> | `hit_quality_steps` | 1 | 1..2 |
> | `recoil_reduction` | 1 | 1..2 |
> | `res_bonus` | 1 | 1..3 |
> | `durability_or_restoration_bonus` | 1 | 1..3 |
> | `movement_m_per_ap_bonus` | 1.0 | 0.5..2.0 |
> | `displacement_m_bonus` | 2 | 1..4 |
> | `area_or_range_m_bonus` | 2 | 1..5 |
> | `count_bonus` | 1 | 1..2 |
> | `information_steps` | 1 | 1..2 |
> | `leverage_steps` | 1 | 1..2 |
> | `signal_steps` | 1 | 1..2 |
> | `treatment_steps` | 1 | 1..2 |
> | `ammunition_discount` | 1 | 1..3 |
> | `opponent_difficulty_offset` | 2 | 1..4 |
> | `difficulty_offset` | -2 | -4..-1 |
> | `movement_cost_ap` | 1 | 1..2 |
> | `repair_steps` | 1 | 1..2 |
> | `prepared_movement_m` | 2 | 1..4 |
> | `action_ap` | 1 | 1..2 |
> | `additional_ap` | 1 | 1..2 |
> | `resource_cost` | 1 | 1..2 |
> | `penalty_reduction` | 2 | 1..4 |
> | `duration_team_turns` | 1 | 1..2 |
> | `movement_penalty_reduction_m_per_ap` | 1.0 | 0.5..2.0 |
> | `failure_margin_window` | 2 | 1..4 |
> | `evidence_steps_reduction` | 1 | 1..2 |
> | `noise_steps_reduction` | 1 | 1..2 |
> | `deterioration_steps` | 1 | 1..2 |
> | `time_steps_reduction` | 1 | 1..2 |
> | `opponent_action_cost_ap` | 1 | 1..2 |
> | `aim_steps` | 1 | 1..2 |
> | `diminishing_returns_step_bonus` | 1 | 1..2 |
> | `developed_aim_steps` | 2 | 1..4 |
> | `collateral_damage_reduction` | 2 | 1..4 |
> | `repair_effectiveness_penalty_steps` | 1 | 1..2 |
> | `risk_steps` | 1 | 1..2 |
> | `sensor_steps` | 1 | 1..2 |
> | `operation_rate_steps` | 1 | 1..2 |
> | `power_output_steps` | 1 | 1..2 |
> | `wear_steps` | 1 | 1..2 |
> | `damage_cost` | 2 | 1..4 |
> | `heat_steps` | 1 | 1..2 |
> | `power_depletion_steps` | 1 | 1..2 |
> | `short_term_survivability_bonus` | 1 | 1..3 |
> | `movement_m_per_ap_penalty` | 1.0 | 0.5..2.0 |
> | `persistence_steps` | 1 | 1..2 |
> | `hp_restoration_bonus` | 1 | 1..3 |
> | `injury_recovery_steps` | 1 | 1..2 |
> | `one_handed_surcharge_reduction_ap` | 1 | 1..2 |
> | `burden_reduction` | 1 | 1..2 |
> | `additional_term_setup_ap` | 1 | 1..2 |
> | `patch_durability` | 1 | 1..2 |
> | `use_count` | 1 | 1..2 |
> | `backup_effectiveness_penalty_steps` | 2 | 1..3 |
> | `solution_steps` | 2 | 1..3 |
> | `visibility_band_shift` | 1 | 1..2 |
>
> ### Explicit per-node specialty magnitude IDs
>
> | ID | Shared family |
> |---|---|
> | `specialty.anchor.braced_position.opponent_difficulty_offset` | `opponent_difficulty_offset` |
> | `specialty.anchor.establish_strongpoint.action_ap` | `action_ap` |
> | `specialty.arsenal.armory_position.count_bonus` | `count_bonus` |
> | `specialty.arsenal.backup_plan.ap_discount` | `ap_discount` |
> | `specialty.arsenal.fast_transition.ap_discount` | `ap_discount` |
> | `specialty.arsenal.handoff.ap_discount` | `ap_discount` |
> | `specialty.arsenal.matched_ammo.ap_discount` | `ap_discount` |
> | `specialty.arsenal.pickup_drill.ap_discount` | `ap_discount` |
> | `specialty.arsenal.pickup_drill.information_steps` | `information_steps` |
> | `specialty.arsenal.rapid_changeover.ap_discount` | `ap_discount` |
> | `specialty.arsenal.role_package.ap_discount` | `ap_discount` |
> | `specialty.arsenal.role_package.burden_reduction` | `burden_reduction` |
> | `specialty.arsenal.second_primary.count_bonus` | `count_bonus` |
> | `specialty.arsenal.sidearm_first.ap_discount` | `ap_discount` |
> | `specialty.arsenal.walking_armory.ap_discount` | `ap_discount` |
> | `specialty.autodrones.autonomous_reposition.ap_discount` | `ap_discount` |
> | `specialty.autodrones.coordinated_coverage.area_or_range_m_bonus` | `area_or_range_m_bonus` |
> | `specialty.autodrones.distributed_fire.difficulty_offset` | `difficulty_offset` |
> | `specialty.autodrones.handoff_order.ap_discount` | `ap_discount` |
> | `specialty.autodrones.local_response.count_bonus` | `count_bonus` |
> | `specialty.autodrones.second_element.count_bonus` | `count_bonus` |
> | `specialty.breaker.alternate_method.ap_discount` | `ap_discount` |
> | `specialty.breaker.breach_and_flow.ap_discount` | `ap_discount` |
> | `specialty.breaker.controlled_breach.area_or_range_m_bonus` | `area_or_range_m_bonus` |
> | `specialty.breaker.controlled_breach.collateral_damage_reduction` | `collateral_damage_reduction` |
> | `specialty.breaker.focused_breach.difficulty_offset` | `difficulty_offset` |
> | `specialty.breaker.focused_breach.material_pen_bonus` | `material_pen_bonus` |
> | `specialty.breaker.firing_port.ap_discount` | `ap_discount` |
> | `specialty.breaker.hard_target.difficulty_offset` | `difficulty_offset` |
> | `specialty.breaker.no_fixed_entry.ap_discount` | `ap_discount` |
> | `specialty.breaker.rapid_second_breach.ap_discount` | `ap_discount` |
> | `specialty.breaker.tactical_breaching.ap_discount` | `ap_discount` |
> | `specialty.breaker.work_under_fire.difficulty_offset` | `difficulty_offset` |
> | `specialty.breaker.wide_breach.resource_cost` | `resource_cost` |
> | `specialty.diplomat.bottom_line.information_steps` | `information_steps` |
> | `specialty.diplomat.counteroffer.ap_discount` | `ap_discount` |
> | `specialty.diplomat.counteroffer.difficulty_offset` | `difficulty_offset` |
> | `specialty.diplomat.face_saving_exit.leverage_steps` | `leverage_steps` |
> | `specialty.diplomat.guaranteed_terms.leverage_steps` | `leverage_steps` |
> | `specialty.diplomat.pivot.ap_discount` | `ap_discount` |
> | `specialty.diplomat.pivot.difficulty_offset` | `difficulty_offset` |
> | `specialty.diplomat.surrender_terms.count_bonus` | `count_bonus` |
> | `specialty.diplomat.surrender_terms.difficulty_offset` | `difficulty_offset` |
> | `specialty.diplomat.terms_on_paper.leverage_steps` | `leverage_steps` |
> | `specialty.diplomat.trade_the_issue.leverage_steps` | `leverage_steps` |
> | `specialty.diplomat.grand_bargain.additional_term_setup_ap` | `additional_term_setup_ap` |
> | `specialty.enforcer.chain_maneuver.ap_discount` | `ap_discount` |
> | `specialty.enforcer.controlled_shot.difficulty_offset` | `difficulty_offset` |
> | `specialty.enforcer.counter_grapple.ap_discount` | `ap_discount` |
> | `specialty.enforcer.disarm_and_take.ap_discount` | `ap_discount` |
> | `specialty.enforcer.follow_the_fall.difficulty_offset` | `difficulty_offset` |
> | `specialty.enforcer.gun_hand_transition.ap_discount` | `ap_discount` |
> | `specialty.enforcer.muzzle_control.difficulty_offset` | `difficulty_offset` |
> | `specialty.enforcer.own_the_clinch.ap_discount` | `ap_discount` |
> | `specialty.enforcer.peel_off.difficulty_offset` | `difficulty_offset` |
> | `specialty.enforcer.retention_counter.ap_discount` | `ap_discount` |
> | `specialty.enforcer.weapon_frame.difficulty_offset` | `difficulty_offset` |
> | `specialty.enforcer.weapon_retention.opponent_difficulty_offset` | `opponent_difficulty_offset` |
> | `specialty.evader.ambush_again.difficulty_offset` | `difficulty_offset` |
> | `specialty.evader.break_contact.ap_discount` | `ap_discount` |
> | `specialty.evader.break_contact.difficulty_offset` | `difficulty_offset` |
> | `specialty.evader.break_pursuit.difficulty_offset` | `difficulty_offset` |
> | `specialty.evader.can_t_pin_me.difficulty_offset` | `difficulty_offset` |
> | `specialty.evader.can_t_pin_me.movement_m_per_ap_bonus` | `movement_m_per_ap_bonus` |
> | `specialty.evader.cut_the_corner.difficulty_offset` | `difficulty_offset` |
> | `specialty.evader.evasive_break.prepared_movement_m` | `prepared_movement_m` |
> | `specialty.evader.extended_reposition.movement_m_per_ap_bonus` | `movement_m_per_ap_bonus` |
> | `specialty.evader.false_trail.opponent_difficulty_offset` | `opponent_difficulty_offset` |
> | `specialty.evader.ghost_walk.movement_m_per_ap_bonus` | `movement_m_per_ap_bonus` |
> | `specialty.evader.hard_to_reacquire.opponent_difficulty_offset` | `opponent_difficulty_offset` |
> | `specialty.evader.nowhere_there.opponent_difficulty_offset` | `opponent_difficulty_offset` |
> | `specialty.evader.rapid_reentry.ap_discount` | `ap_discount` |
> | `specialty.evader.vanishing_move.ap_discount` | `ap_discount` |
> | `specialty.field_engineer.emplace.difficulty_offset` | `difficulty_offset` |
> | `specialty.field_engineer.emplace.durability_or_restoration_bonus` | `durability_or_restoration_bonus` |
> | `specialty.field_engineer.field_repair.durability_or_restoration_bonus` | `durability_or_restoration_bonus` |
> | `specialty.field_engineer.field_sensor.ap_discount` | `ap_discount` |
> | `specialty.field_engineer.field_work.ap_discount` | `ap_discount` |
> | `specialty.field_engineer.field_workshop.ap_discount` | `ap_discount` |
> | `specialty.field_engineer.harden_system.durability_or_restoration_bonus` | `durability_or_restoration_bonus` |
> | `specialty.field_engineer.hot_repair.repair_effectiveness_penalty_steps` | `repair_effectiveness_penalty_steps` |
> | `specialty.field_engineer.hot_repair.risk_steps` | `risk_steps` |
> | `specialty.field_engineer.overdrive.movement_m_per_ap_bonus` | `movement_m_per_ap_bonus` |
> | `specialty.field_engineer.overdrive.sensor_steps` | `sensor_steps` |
> | `specialty.field_engineer.overdrive.operation_rate_steps` | `operation_rate_steps` |
> | `specialty.field_engineer.overdrive.power_output_steps` | `power_output_steps` |
> | `specialty.field_engineer.overdrive.wear_steps` | `wear_steps` |
> | `specialty.field_engineer.overdrive.damage_cost` | `damage_cost` |
> | `specialty.field_engineer.overdrive.heat_steps` | `heat_steps` |
> | `specialty.field_engineer.overdrive.power_depletion_steps` | `power_depletion_steps` |
> | `specialty.field_engineer.patch_job.ap_discount` | `ap_discount` |
> | `specialty.field_engineer.patch_job.patch_durability` | `patch_durability` |
> | `specialty.field_engineer.patch_job.use_count` | `use_count` |
> | `specialty.field_engineer.redundant_system.backup_effectiveness_penalty_steps` | `backup_effectiveness_penalty_steps` |
> | `specialty.field_engineer.technical_obstacle.movement_cost_ap` | `movement_cost_ap` |
> | `specialty.field_medic.battlefield_recovery.res_bonus` | `res_bonus` |
> | `specialty.field_medic.battlefield_recovery.short_term_survivability_bonus` | `short_term_survivability_bonus` |
> | `specialty.field_medic.drag_and_treat.ap_discount` | `ap_discount` |
> | `specialty.field_medic.functional_splint.penalty_reduction` | `penalty_reduction` |
> | `specialty.field_medic.get_them_up.ap_discount` | `ap_discount` |
> | `specialty.field_medic.keep_them_fighting.ap_discount` | `ap_discount` |
> | `specialty.field_medic.kit_at_hand.ap_discount` | `ap_discount` |
> | `specialty.field_medic.multiple_casualties.ap_discount` | `ap_discount` |
> | `specialty.field_medic.pain_control.duration_team_turns` | `duration_team_turns` |
> | `specialty.field_medic.pain_control.penalty_reduction` | `penalty_reduction` |
> | `specialty.field_medic.patch_and_move.movement_penalty_reduction_m_per_ap` | `movement_penalty_reduction_m_per_ap` |
> | `specialty.field_medic.rapid_stabilization.ap_discount` | `ap_discount` |
> | `specialty.flanker.angle_advantage.difficulty_offset` | `difficulty_offset` |
> | `specialty.flanker.chained_flank.ap_discount` | `ap_discount` |
> | `specialty.flanker.crossfire.difficulty_offset` | `difficulty_offset` |
> | `specialty.flanker.double_envelopment.difficulty_offset` | `difficulty_offset` |
> | `specialty.flanker.cut_the_exit.ap_discount` | `ap_discount` |
> | `specialty.flanker.encirclement.count_bonus` | `count_bonus` |
> | `specialty.flanker.exposed_target.damage_bonus` | `damage_bonus` |
> | `specialty.flanker.flanking_move.ap_discount` | `ap_discount` |
> | `specialty.flanker.flanking_overwatch.ap_discount` | `ap_discount` |
> | `specialty.flanker.reorient_punish.ap_discount` | `ap_discount` |
> | `specialty.flanker.shoot_and_scoot.ap_discount` | `ap_discount` |
> | `specialty.flanker.side_pressure.ammunition_discount` | `ammunition_discount` |
> | `specialty.flanker.side_pressure.ap_discount` | `ap_discount` |
> | `specialty.flanker.shadow_the_cover.prepared_movement_m` | `prepared_movement_m` |
> | `specialty.flanker.weak_side.difficulty_offset` | `difficulty_offset` |
> | `specialty.fpv_drones.attack_run.difficulty_offset` | `difficulty_offset` |
> | `specialty.fpv_drones.evasive_piloting.action_ap` | `action_ap` |
> | `specialty.fpv_drones.evasive_piloting.movement_m_per_ap_penalty` | `movement_m_per_ap_penalty` |
> | `specialty.fpv_drones.evasive_piloting.opponent_difficulty_offset` | `opponent_difficulty_offset` |
> | `specialty.fpv_drones.forward_observer.difficulty_offset` | `difficulty_offset` |
> | `specialty.fpv_drones.last_second_correction.action_ap` | `action_ap` |
> | `specialty.fpv_drones.last_second_correction.failure_margin_window` | `failure_margin_window` |
> | `specialty.fpv_drones.manual_pilot.movement_m_per_ap_bonus` | `movement_m_per_ap_bonus` |
> | `specialty.fpv_drones.manual_pilot.difficulty_offset` | `difficulty_offset` |
> | `specialty.fpv_drones.mark_and_pass.ap_discount` | `ap_discount` |
> | `specialty.fpv_drones.mark_and_pass.duration_team_turns` | `duration_team_turns` |
> | `specialty.fpv_drones.masked_approach.opponent_difficulty_offset` | `opponent_difficulty_offset` |
> | `specialty.fpv_drones.precision_delivery.difficulty_offset` | `difficulty_offset` |
> | `specialty.fpv_drones.precision_flight.ap_discount` | `ap_discount` |
> | `specialty.fpv_drones.rapid_launch.ap_discount` | `ap_discount` |
> | `specialty.fpv_drones.signal_discipline.signal_steps` | `signal_steps` |
> | `specialty.fpv_drones.through_the_eye_of_the_needle.ap_discount` | `ap_discount` |
> | `specialty.gunner.dominating_fire.area_or_range_m_bonus` | `area_or_range_m_bonus` |
> | `specialty.gunner.long_string.diminishing_returns_step_bonus` | `diminishing_returns_step_bonus` |
> | `specialty.gunner.recoil_rhythm.recoil_reduction` | `recoil_reduction` |
> | `specialty.gunner.relentless.ap_discount` | `ap_discount` |
> | `specialty.gunner.shift_fire.ammunition_discount` | `ammunition_discount` |
> | `specialty.gunner.shift_fire.ap_discount` | `ap_discount` |
> | `specialty.gunner.suppression_discipline.ammunition_discount` | `ammunition_discount` |
> | `specialty.gunner.suppression_discipline.ap_discount` | `ap_discount` |
> | `specialty.gunner.sustained_fire_discipline.recoil_reduction` | `recoil_reduction` |
> | `specialty.gunner.weapon_rest.recoil_reduction` | `recoil_reduction` |
> | `specialty.guardian.interpose.prepared_movement_m` | `prepared_movement_m` |
> | `specialty.handler.borrowed_access.leverage_steps` | `leverage_steps` |
> | `specialty.handler.discreet_channel.opponent_difficulty_offset` | `opponent_difficulty_offset` |
> | `specialty.handler.favor_ledger.leverage_steps` | `leverage_steps` |
> | `specialty.handler.favor_ledger.persistence_steps` | `persistence_steps` |
> | `specialty.handler.introduction.difficulty_offset` | `difficulty_offset` |
> | `specialty.handler.parallel_ask.ap_discount` | `ap_discount` |
> | `specialty.handler.protect_the_source.ap_discount` | `ap_discount` |
> | `specialty.handler.protect_the_source.count_bonus` | `count_bonus` |
> | `specialty.handler.quick_ask.ap_discount` | `ap_discount` |
> | `specialty.handler.rumor_map.information_steps` | `information_steps` |
> | `specialty.handler.safe_haven.ap_discount` | `ap_discount` |
> | `specialty.handler.vet_the_source.information_steps` | `information_steps` |
> | `specialty.handler.work_a_contact.ap_discount` | `ap_discount` |
> | `specialty.handler.work_a_contact.leverage_steps` | `leverage_steps` |
> | `specialty.icon.crowd_momentum.leverage_steps` | `leverage_steps` |
> | `specialty.icon.endorsement.leverage_steps` | `leverage_steps` |
> | `specialty.icon.make_an_entrance.difficulty_offset` | `difficulty_offset` |
> | `specialty.icon.make_it_public.leverage_steps` | `leverage_steps` |
> | `specialty.icon.message_discipline.difficulty_offset` | `difficulty_offset` |
> | `specialty.icon.name_opens_doors.difficulty_offset` | `difficulty_offset` |
> | `specialty.icon.read_the_audience.information_steps` | `information_steps` |
> | `specialty.icon.reputational_reserve.leverage_steps` | `leverage_steps` |
> | `specialty.infiltrator.adaptive_plan.ap_discount` | `ap_discount` |
> | `specialty.infiltrator.approach_plan.difficulty_offset` | `difficulty_offset` |
> | `specialty.infiltrator.clean_entry.ap_discount` | `ap_discount` |
> | `specialty.infiltrator.close_pass.penalty_reduction` | `penalty_reduction` |
> | `specialty.infiltrator.distraction_window.difficulty_offset` | `difficulty_offset` |
> | `specialty.infiltrator.gear_control.penalty_reduction` | `penalty_reduction` |
> | `specialty.infiltrator.guide_through.difficulty_offset` | `difficulty_offset` |
> | `specialty.infiltrator.leave_it_clean.ap_discount` | `ap_discount` |
> | `specialty.infiltrator.leave_it_clean.evidence_steps_reduction` | `evidence_steps_reduction` |
> | `specialty.infiltrator.quiet_hands.penalty_reduction` | `penalty_reduction` |
> | `specialty.infiltrator.silent_removal.ap_discount` | `ap_discount` |
> | `specialty.infiltrator.silent_removal.noise_steps_reduction` | `noise_steps_reduction` |
> | `specialty.infiltrator.synchronized_patterns.difficulty_offset` | `difficulty_offset` |
> | `specialty.infiltrator.timed_crossing.difficulty_offset` | `difficulty_offset` |
> | `specialty.infiltrator.work_in_the_shadows.penalty_reduction` | `penalty_reduction` |
> | `specialty.local_hacker.local_escalation.difficulty_offset` | `difficulty_offset` |
> | `specialty.local_hacker.local_lockout.opponent_difficulty_offset` | `opponent_difficulty_offset` |
> | `specialty.local_hacker.multi_function_access.ap_discount` | `ap_discount` |
> | `specialty.local_hacker.quiet_console.visibility_band_shift` | `visibility_band_shift` |
> | `specialty.local_hacker.rapid_reconfigure.ap_discount` | `ap_discount` |
> | `specialty.local_hacker.root_on_the_box.ap_discount` | `ap_discount` |
> | `specialty.local_hacker.snapshot.ap_discount` | `ap_discount` |
> | `specialty.networker.cascade_access.ap_discount` | `ap_discount` |
> | `specialty.networker.cascade_access.difficulty_offset` | `difficulty_offset` |
> | `specialty.networker.credential_sweep.action_ap` | `action_ap` |
> | `specialty.networker.foothold.ap_discount` | `ap_discount` |
> | `specialty.networker.gateway_pivot.ap_discount` | `ap_discount` |
> | `specialty.networker.own_the_topology.ap_discount` | `ap_discount` |
> | `specialty.networker.parallel_probe.count_bonus` | `count_bonus` |
> | `specialty.networker.parallel_probe.visibility_band_shift` | `visibility_band_shift` |
> | `specialty.networker.pivot.ap_discount` | `ap_discount` |
> | `specialty.networker.quiet_enumeration.visibility_band_shift` | `visibility_band_shift` |
> | `specialty.networker.reroute.ap_discount` | `ap_discount` |
> | `specialty.networker.route_persistence.ap_discount` | `ap_discount` |
> | `specialty.networker.segment_map.information_steps` | `information_steps` |
> | `specialty.networker.token_relay.ap_discount` | `ap_discount` |
> | `specialty.operator.empty_switch.ap_discount` | `ap_discount` |
> | `specialty.operator.cycle_on_the_return.ap_discount` | `ap_discount` |
> | `specialty.operator.fighting_hands.ap_discount` | `ap_discount` |
> | `specialty.operator.malfunction_drill.ap_discount` | `ap_discount` |
> | `specialty.operator.manual_action_master.ap_discount` | `ap_discount` |
> | `specialty.operator.one_handed_manipulation.one_handed_surcharge_reduction_ap` | `one_handed_surcharge_reduction_ap` |
> | `specialty.operator.quick_cycle.ap_discount` | `ap_discount` |
> | `specialty.operator.retention_reload.ap_discount` | `ap_discount` |
> | `specialty.operator.selective_load.ap_discount` | `ap_discount` |
> | `specialty.operator.shoulder_switch.ap_discount` | `ap_discount` |
> | `specialty.operator.transition_drill.ap_discount` | `ap_discount` |
> | `specialty.operator.weapon_manipulation.ap_discount` | `ap_discount` |
> | `specialty.resuscitator.airway_first.ap_discount` | `ap_discount` |
> | `specialty.resuscitator.airway_first.treatment_steps` | `treatment_steps` |
> | `specialty.resuscitator.continuous_care.ap_discount` | `ap_discount` |
> | `specialty.resuscitator.crash_intervention.ap_discount` | `ap_discount` |
> | `specialty.resuscitator.bridge_to_trauma.ap_discount` | `ap_discount` |
> | `specialty.resuscitator.bridge_to_trauma.difficulty_offset` | `difficulty_offset` |
> | `specialty.resuscitator.massive_bleeding.difficulty_offset` | `difficulty_offset` |
> | `specialty.resuscitator.not_yet.ap_discount` | `ap_discount` |
> | `specialty.resuscitator.not_yet.difficulty_offset` | `difficulty_offset` |
> | `specialty.resuscitator.rapid_assessment.ap_discount` | `ap_discount` |
> | `specialty.resuscitator.refuse_deterioration.deterioration_steps` | `deterioration_steps` |
> | `specialty.resuscitator.restore_consciousness.time_steps_reduction` | `time_steps_reduction` |
> | `specialty.resuscitator.resuscitation_kit.ap_discount` | `ap_discount` |
> | `specialty.resuscitator.return_of_circulation.treatment_steps` | `treatment_steps` |
> | `specialty.sentinel.broad_sector.area_or_range_m_bonus` | `area_or_range_m_bonus` |
> | `specialty.sentinel.continuous_watch.count_bonus` | `count_bonus` |
> | `specialty.sentinel.crossing_shot.difficulty_offset` | `difficulty_offset` |
> | `specialty.sentinel.fast_setup.ap_discount` | `ap_discount` |
> | `specialty.sentinel.linked_sector.ap_discount` | `ap_discount` |
> | `specialty.sentinel.prepared_sightline.ap_discount` | `ap_discount` |
> | `specialty.sentinel.stable_nest.difficulty_offset` | `difficulty_offset` |
> | `specialty.sentinel.tracking_sector.ap_discount` | `ap_discount` |
> | `specialty.shield_bearer.brace_shield.opponent_difficulty_offset` | `opponent_difficulty_offset` |
> | `specialty.shield_bearer.walking_bastion.opponent_difficulty_offset` | `opponent_difficulty_offset` |
> | `specialty.shock.assault_momentum.ap_discount` | `ap_discount` |
> | `specialty.shock.assault_window.count_bonus` | `count_bonus` |
> | `specialty.shock.break_the_rhythm.opponent_action_cost_ap` | `opponent_action_cost_ap` |
> | `specialty.shock.close_suppression.ammunition_discount` | `ammunition_discount` |
> | `specialty.shock.close_suppression.ap_discount` | `ap_discount` |
> | `specialty.shock.combined_pressure.ap_discount` | `ap_discount` |
> | `specialty.shock.dislocate.displacement_m_bonus` | `displacement_m_bonus` |
> | `specialty.shock.exploit_exposure.difficulty_offset` | `difficulty_offset` |
> | `specialty.shock.following_fire.ammunition_discount` | `ammunition_discount` |
> | `specialty.shock.following_fire.ap_discount` | `ap_discount` |
> | `specialty.shock.grenade_follow_up.ap_discount` | `ap_discount` |
> | `specialty.shock.hard_push.hit_quality_steps` | `hit_quality_steps` |
> | `specialty.shock.rolling_assault.count_bonus` | `count_bonus` |
> | `specialty.shock.shove_through.ap_discount` | `ap_discount` |
> | `specialty.snapshot.decisive_window.hit_quality_steps` | `hit_quality_steps` |
> | `specialty.snapshot.hasty_full_solution.aim_steps` | `aim_steps` |
> | `specialty.snapshot.hasty_zero.difficulty_offset` | `difficulty_offset` |
> | `specialty.snapshot.instant_solution.ap_discount` | `ap_discount` |
> | `specialty.snapshot.instant_solution.developed_aim_steps` | `developed_aim_steps` |
> | `specialty.snapshot.next_target.ap_discount` | `ap_discount` |
> | `specialty.snapshot.opening_window.difficulty_offset` | `difficulty_offset` |
> | `specialty.snapshot.quick_called_shot.difficulty_offset` | `difficulty_offset` |
> | `specialty.snapshot.quick_solution.ap_discount` | `ap_discount` |
> | `specialty.snapshot.reacquire.ap_discount` | `ap_discount` |
> | `specialty.snapshot.second_sight_picture.ap_discount` | `ap_discount` |
> | `specialty.snapshot.shoot_and_shift.ap_discount` | `ap_discount` |
> | `specialty.snapshot.snap_brace.ap_discount` | `ap_discount` |
> | `specialty.snapshot.snap_penetration.target_pen_bonus` | `target_pen_bonus` |
> | `specialty.snapshot.target_switch.ap_discount` | `ap_discount` |
> | `specialty.sniper.braced_solution.ap_discount` | `ap_discount` |
> | `specialty.sniper.deep_aim.additional_ap` | `additional_ap` |
> | `specialty.sniper.deep_aim.difficulty_offset` | `difficulty_offset` |
> | `specialty.sniper.exact_release.hit_quality_steps` | `hit_quality_steps` |
> | `specialty.sniper.extreme_range.area_or_range_m_bonus` | `area_or_range_m_bonus` |
> | `specialty.sniper.fine_point.difficulty_offset` | `difficulty_offset` |
> | `specialty.sniper.first_round_hit.hit_quality_steps` | `hit_quality_steps` |
> | `specialty.sniper.follow_through.ap_discount` | `ap_discount` |
> | `specialty.sniper.material_window.material_pen_bonus` | `material_pen_bonus` |
> | `specialty.sniper.patient_reacquire.ap_discount` | `ap_discount` |
> | `specialty.sniper.perfect_solution.action_ap` | `action_ap` |
> | `specialty.sniper.perfect_solution.solution_steps` | `solution_steps` |
> | `specialty.sniper.penetrating_shot.target_pen_bonus` | `target_pen_bonus` |
> | `specialty.sniper.range_dope.difficulty_offset` | `difficulty_offset` |
> | `specialty.sniper.terminal_shot.damage_bonus` | `damage_bonus` |
> | `specialty.sniper.thread_the_gap.difficulty_offset` | `difficulty_offset` |
> | `specialty.trauma.debridement.treatment_steps` | `treatment_steps` |
> | `specialty.trauma.deep_wound_care.durability_or_restoration_bonus` | `durability_or_restoration_bonus` |
> | `specialty.trauma.definitive_care.ap_discount` | `ap_discount` |
> | `specialty.trauma.definitive_care.treatment_steps` | `treatment_steps` |
> | `specialty.trauma.efficient_closure.ap_discount` | `ap_discount` |
> | `specialty.trauma.efficient_closure.durability_or_restoration_bonus` | `durability_or_restoration_bonus` |
> | `specialty.trauma.fracture_management.treatment_steps` | `treatment_steps` |
> | `specialty.trauma.hemorrhage_control.ap_discount` | `ap_discount` |
> | `specialty.trauma.hemorrhage_control.treatment_steps` | `treatment_steps` |
> | `specialty.trauma.limb_preservation.difficulty_offset` | `difficulty_offset` |
> | `specialty.trauma.limb_preservation.treatment_steps` | `treatment_steps` |
> | `specialty.trauma.shock_management.treatment_steps` | `treatment_steps` |
> | `specialty.trauma.trauma_assessment.information_steps` | `information_steps` |
> | `specialty.trauma.trauma_care.hp_restoration_bonus` | `hp_restoration_bonus` |
> | `specialty.trauma.trauma_care.injury_recovery_steps` | `injury_recovery_steps` |
> | `specialty.trauma.recovery_plan.injury_recovery_steps` | `injury_recovery_steps` |
> | `specialty.trauma.recovery_plan.time_steps_reduction` | `time_steps_reduction` |
> | `specialty.trauma.wound_management.difficulty_offset` | `difficulty_offset` |

## Connections
[[BALANCE CONTRACT]] · [[BALANCE SCENARIOS]] · [[BENCHMARK FIXTURES]] · [[REFERENCE EQUIPMENT]] · [[REFERENCE EQUIPMENT VALUES]] · [[CHECK]] · [[DIFFICULTY]] · [[AP]] · [[MOVEMENT]] · [[RES]] · [[HP]] · [[HIT QUALITY]] · [[CRITICAL HIT]] · [[RECOIL]] · [[SUPPRESSION]] · [[OVERWATCH]] · [[CQC DEFENSE]] · [[GRENADE]] · [[EXPLOSIVE SAVE]] · [[STABILIZE]] · [[RESUSCITATION]]
