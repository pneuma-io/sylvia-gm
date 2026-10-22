---
type: test-fixture
domain: balance-testing
status: locked
player_canon: false
tags: [type/test-fixture, domain/balance-testing, status/locked]
---
# BENCHMARK FIXTURES

**At a glance:** Standardized Level 1 / 5 / 10 / 15 / 20 test characters for implementation and balance work. These fixtures provide a consistent ruler for simulations; they are **not** final player-character creation or advancement canon.

> [!note]- Full rules
> - The benchmark suite exists so class, specialty, equipment, map, and scenario comparisons do not accidentally change underlying character competence at the same time.
> - Fixture numbers may be used and tuned as testing assumptions without silently becoming the final campaign progression schedule.
> - HaremRun character identities are presentation/archetype labels for the fixtures. HaremRun mechanics, powers, equipment, or setting assumptions are not imported into TRIGGER DISCIPLINE.
> - Equivalent benchmark characters receive equivalent total mechanical investment. Their SKILL investment is distributed according to the role being tested rather than forcing every character to have identical skills.
> - Do not infer a mechanical bonus from a character's HaremRun history, appearance, personality, reputation, or narrative competence. Every mechanical value must be stated by the fixture.
>
> ## Fixture numeric scale
> **SKILL: 0–8**
> - 0 — untrained
> - 2 — familiar
> - 4 — professionally competent
> - 6 — expert
> - 8 — exceptional / master-level
>
> **APTITUDE: 0–3**
> - 0 — ordinary or weak for the tested task
> - 1 — capable
> - 2 — notably strong
> - 3 — exceptional human
>
> This scale is deliberately narrower for APTITUDE than SKILL so trained competence remains the larger contributor to a CHECK.
>
> ## Specialist benchmark progression
> | Level | Primary SKILL | Secondary SKILL | Strong APTITUDE | SPECIALTY choices |
> |---:|---:|---:|---:|---:|
> | 1 | 4 | 2 | 1 | 1 |
> | 5 | 5 | 3 | 1 | 5 |
> | 10 | 6 | 4 | 2 | 10 |
> | 15 | 7 | 5 | 2 | 15 |
> | 20 | 8 | 6 | 3 | 20 |
>
> - Level 10 is a major comparison point because a straight SPECIALTY Entry → Keystone path costs ten SPECIALTY choices.
> - The table describes benchmark test snapshots, not a promise that final player SKILL/APTITUDE advancement will occur at those exact levels.
>
> ## Control fixtures
> - **GENERALIST CONTROL:** primary SKILL is one point lower than the specialist benchmark at the same level, with broader secondary competence. Use to test whether narrow specialization is producing appropriate scenario value rather than simply inflating every check.
> - **TRAINING CONTROL:** use high relevant SKILL with APTITUDE 0. At Level 1, use SKILL 5; at Levels 5/10/15/20, use the specialist benchmark's primary SKILL. This Level-1 fixture keeps professional training from beginning below the fixed TALENT CONTROL under the additive CHECK while leaving the specialist progression ladder unchanged. Use the control to verify that strong training remains effective even with mediocre natural talent.
> - **TALENT CONTROL:** use APTITUDE 3 with relevant SKILL 2. Use to detect math where raw talent improperly dominates professional training.
>
> ## Named HaremRun benchmark cast
> - **Kora — BULWARK**
> - **Vesper — RIFLEMAN**
> - **Riot — BREACHER**
> - **Pixie — GHOST**
> - **Sable — SHARPSHOOTER**
> - **Nova — TECH**
> - **Hex — HACKER**
> - **Solace — CORPSMAN**
> - **Velvet — INFLUENCER**
> - **Blaze — mobility / VEHICLES control fixture**
> - **Siren — alternate social / HUMINT control fixture**
>
> The extra Blaze and Siren fixtures are intentional. One named character should not define the full acceptable shape of a class or system role.
>
> ## Fixture identifiers
> Use stable machine-readable identifiers such as:
> - `HR_VESPER_L01_RIFLEMAN`
> - `HR_VESPER_L05_RIFLEMAN`
> - `HR_VESPER_L10_RIFLEMAN`
> - `HR_VESPER_L15_RIFLEMAN`
> - `HR_VESPER_L20_RIFLEMAN`
>
> The same pattern applies to the rest of the named suite. Specialty/build suffixes may be appended once a specific progression path is being tested.
>
> ## Explicit persistent-INJURY counterfactual
> **`INJURY_REPAIRABLE_FOREARM_TRAUMA_V1`**
> - This is one authored test INJURY, not a universal wound table or a rule that every injury uses the same states.
> - The forearm/hand structures are severely damaged but remain anatomically present and medically salvageable. Appropriate definitive care, serious-trauma equipment, supplies, access, time, and working conditions are assumed.
> - Ordinary successful definitive care leaves `SEVERE_HAND_IMPAIRMENT`: +4 DIFFICULTY to actions that materially require the affected hand or two functional hands.
> - Successful definitive care with TRAUMA `SALVAGE` instead leaves `MODERATE_HAND_IMPAIRMENT`: +2 DIFFICULTY to those same actions.
> - SALVAGE changes the eventual persistent state after recovery. It restores no immediate HP or combat function, applies at most once to this INJURY, and cannot reduce destroyed or irretrievably severed anatomy.
> - The ordinary persistent penalty and SALVAGE reduction are admitted balance parameters; the injury-specific eligibility, one-step ceiling, non-stacking rule, and physical limits are locked.
> - For FIELD MEDIC `COMBAT MEDICINE`, this same injury is medically plausible for immediate battlefield function. Its first successful eligible battlefield treatment restores 1 RES once. That treatment restores no HP and leaves the injury, anatomy, persistent state, and later trauma-care requirements in place.
>
> ## FIELD WORK reconfiguration counterfactual
> **`FIELD_SENSOR_RELAY_RECONFIGURATION_V1`**
> - A compatible FIELD SENSOR / RELAY NODE is already deployed and operating. This is not a deployment action.
> - The TECH has the required tools, physical access, and technical capability.
> - The declared task is a physical reorientation from `NORTH` to `EAST`. No sensor power, range, network privilege, software function, or other capability changes.
> - A baseline TECH spends 3 AP. FIELD ENGINEER `FIELD WORK` spends 2 AP.
> - Missing tools, lost access, incompatible equipment, or an attempted undeclared capability change makes the fixture ineligible.
>
> ## Deliberately not fixed here
> This character-fixture lock does **not** assign final player-facing:
> - HP
> - innate/base RES by level
> - AP pool
> - movement distance / DISTANCE:AP values
> - personal equipment loadouts
>
> The first **test-only** HP/RES/AP/movement seed vectors and their permitted search bounds now live in [[BALANCE PARAMETERS]]. That keeps this benchmark ladder stable while still giving the simulator a complete starting character state. Those seeds are not final character-creation/progression canon.
>
> Representative equipment families are defined in [[REFERENCE EQUIPMENT]], and their current v0.1 numeric tuning inputs live in [[REFERENCE EQUIPMENT VALUES]]. Armor/weapon/equipment numbers in that file are **test values**, not final campaign progression or equipment canon. Character anatomy, class baselines, loadouts, armor, burden, and equipment remain separate variables so one benchmark number does not silently absorb several systems at once.

## Connections
[[BALANCE CONTRACT]] · [[BALANCE PARAMETERS]] · [[BALANCE SCENARIOS]] · [[CHECK]] · [[LEVEL]] · [[SPECIALTY]] · [[SKILLS]] · [[APTITUDES]] · [[RES]] · [[HP]] · [[AP]] · [[REFERENCE EQUIPMENT]] · [[REFERENCE EQUIPMENT VALUES]]
