---
type: test-fixture
domain: balance-testing
status: tuning
player_canon: false
version: v0.1
tags: [type/test-fixture, domain/balance-testing, status/tuning]
---
# REFERENCE EQUIPMENT VALUES

**At a glance:** Initial numeric values for the locked [[REFERENCE EQUIPMENT]] set. These are deliberately rough **balance-test inputs**, not final player-facing equipment canon. Codex may tune only the numeric parameters admitted by [[BALANCE PARAMETERS]] under [[BALANCE CONTRACT]]; it may not change the locked equipment schema or underlying mechanics to improve a metric.

> [!note]- Full rules
> ## Test-only interpretation conventions
> These conventions exist so the starting values can actually be simulated. They are part of the balance fixture layer, not a final promise about player-facing notation.
>
> - **DAMAGE** is integer damage delivered by one connected projectile/effect before RES/HP routing.
> - **target PENETRATION** is an integer number of DAMAGE points that bypass RES and apply to HP, capped by that hit's DAMAGE. Remaining DAMAGE attacks RES normally. HIT QUALITY and explicit abilities may alter effective target PENETRATION through their own rules.
> - **MATERIAL PENETRATION** is an integer rating used against a material's **MATERIAL RESISTANCE** rating during PROJECTILE / blast routing. For this v0.1 fixture only:
>   - MATERIAL PENETRATION lower than MATERIAL RESISTANCE -> **STOP**.
>   - equal ratings -> **REDUCE**: the projectile/effect continues, but remaining DAMAGE and target PENETRATION are halved, rounded down; MATERIAL PENETRATION becomes 0 for the next layer.
>   - MATERIAL PENETRATION higher than MATERIAL RESISTANCE -> **PENETRATE**: continue through the layer; reduce remaining MATERIAL PENETRATION by that layer's MATERIAL RESISTANCE.
> - **HANDLING** is a 0-4 awkwardness rating used only when geometry genuinely constrains bringing the weapon to bear: 0 COMPACT, 1 HANDY, 2 STANDARD, 3 LONG, 4 CUMBERSOME. In v0.1 constrained/contact-fire tests, add HANDLING to ATTACK DIFFICULTY. Ordinary open firing does not pay this modifier. [[BALANCE PARAMETERS]] may tune only the declared fixture coefficient/ranges without changing what HANDLING represents.
> - **RECOIL** is a 1-4 weapon recoil rating used as a tuning input for the persistent RECOIL state. The first parameterized same-action / persistent-state conversion now lives in [[BALANCE PARAMETERS]]; higher weapon RECOIL remains harder to control. Bracing/support can explicitly reduce the effective rating where listed.
> - **BURDEN** is a 0-4 carried-equipment rating used as a tuning input for movement/handling tests: 0 negligible, 1 light, 2 noticeable, 3 heavy, 4 very heavy. The first test-only DISTANCE:AP conversion now lives in [[BALANCE PARAMETERS]].
> - RANGE PROFILE distances below are meters. The listed modifier is added to ATTACK DIFFICULTY for that distance band. Physical visibility, COVER, AIM, RECOIL, HANDLING, and TARGET KNOWLEDGE still apply separately.
> - Reload/cycle values are AP costs for the balance fixture. They do not create a new ready/unready firearm state; normal MAGAZINE, CHAMBER, CYCLING, and RELOAD state still applies.
>
> ## Firearms — v0.1 starting values
> | Fixture | DAMAGE | target PEN | MATERIAL PEN | RANGE PROFILE | RECOIL | CYCLIC RATE | CAPACITY / FEED | FIRE MODES | MECHANISM | RELOAD / CYCLE | HANDLING |
> |---|---:|---:|---:|---|---:|---|---|---|---|---|---:|
> | **PISTOL** | 4 | 1 | 1 | 0-15m +0; 16-30m +2; 31-50m +5; beyond +8 | 1 | trigger-limited semi-auto | 15-round detachable magazine + chamber | SEMI AUTO | short-recoil self-loader | 2 AP magazine change | 0 |
> | **SMG** | 4 | 1 | 1 | 0-30m +0; 31-60m +2; 61-100m +5; beyond +8 | 1 | ~800 rpm | 30-round detachable magazine + chamber | SEMI AUTO / FULL AUTO | compact self-loader | 2 AP magazine change | 1 |
> | **SERVICE RIFLE** | 5 | 2 | 3 | 0-120m +0; 121-250m +2; 251-400m +5; beyond +8 | 2 | ~750 rpm | 30-round detachable magazine + chamber | SEMI AUTO / 3-round BURST / FULL AUTO | gas-operated self-loader | 2 AP magazine change | 2 |
> | **LMG** | 5 | 2 | 3 | 0-150m +0; 151-350m +2; 351-600m +5; beyond +8 | 2; **1 when properly braced/supported** | ~650 rpm | 100-round belt box + chamber | FULL AUTO | belt-fed gas-operated | 4 AP belt/box change | 4 |
> | **SHOTGUN** | 7 / 5 / 3 | 1 | 1 | 0-10m: D7 +0; 11-20m: D5 +2; 21-35m: D3 +5; beyond +8 | 4 | manual-cycle limited | 8-shell tube + chamber | single shot | pump action | 1 AP per shell; 1 AP cycle | 2 |
> | **PRECISION RIFLE** | 6 | 3 | 4 | 0-250m +0; 251-500m +2; 501-800m +5; beyond +8 | 3 | trigger-limited semi-auto | 10-round detachable magazine + chamber | SEMI AUTO | precision gas-operated self-loader | 2 AP magazine change | 3 |
> | **MANUAL-ACTION PRECISION RIFLE** | 7 | 4 | 5 | 0-300m +0; 301-650m +2; 651-1000m +5; beyond +8 | 4 | manual-cycle limited | 5-round detachable magazine + chamber | single shot | bolt action | 2 AP magazine change; 1 AP cycle | 4 |
>
> Design intent for this first pass:
> - PISTOL wins on constrained-space HANDLING, not raw lethality.
> - SMG buys volume/capacity while retaining good HANDLING, but does not gain rifle penetration.
> - SERVICE RIFLE is the general benchmark firearm.
> - LMG does not receive magical per-round DAMAGE; its advantage is belt capacity, sustainable automatic fire, and support-assisted RECOIL.
> - SHOTGUN is violently strong at close range but loses concentration quickly and has slow shell-by-shell feeding/cycling.
> - PRECISION RIFLE gains range and penetration at the cost of HANDLING/RECOIL.
> - MANUAL-ACTION PRECISION RIFLE pushes that trade farther and pays a real cycle cost between shots.
>
> ## Protection — v0.1 starting values
> | Fixture | Mechanical values | Physical limits |
> |---|---|---|
> | **STANDARD BODY ARMOR** | +4 RES; BURDEN 1 | Personal protection only; does not create COVER or block external geometry. |
> | **HEAVY BODY ARMOR** | +7 RES; BURDEN 3 | More protective but materially heavier/bulkier; exact movement and STEALTH consequences are balance-contract parameters derived from BURDEN rather than a generic quality penalty. |
> | **BALLISTIC SHIELD** | COVER HP 16; MATERIAL RESISTANCE 4; BURDEN 4; HANDLING 4 | Directional physical COVER, not RES. Occupies hands, protects only its real facing/height, can be flanked, destroyed, or penetrated. |
>
> ## Explosives / effect devices — v0.1 starting values
> Common handling assumption for these three fixtures: 1 AP to arm/ready from QUICK ACCESS and 1 AP to throw/place. STOWED access costs remain governed by inventory state.
>
> ### FRAGMENTATION GRENADE
> - Fuse: **4 seconds of tactical time**.
> - Fragmentation bands from final detonation point:
>   - 0-4m: DAMAGE 7, target PEN 3, MATERIAL PEN 2.
>   - >4-8m: DAMAGE 5, target PEN 2, MATERIAL PEN 1.
>   - >8-14m: DAMAGE 3, target PEN 1, MATERIAL PEN 0.
>   - >14-20m: DAMAGE 1, target PEN 0, MATERIAL PEN 0.
> - COVER, STANCE, corners, and intervening material resolve through BLAST GEOMETRY before target effects.
>
> ### FLASH / CONCUSSION GRENADE
> - Fuse: **4 seconds of tactical time**.
> - No baseline direct DAMAGE in v0.1; the reference device tests temporary sensory disruption rather than fragmentation.
> - If final geometry leaves a target physically exposed to the effect after any valid EXPLOSIVE SAVE movement/stance change:
>   - 0-4m: +4 DIFFICULTY to CHECKS/attacks that materially require clear sight or hearing until the end of that target's next TEAM TURN.
>   - >4-8m: +3 DIFFICULTY for the same duration.
>   - >8-12m: +2 DIFFICULTY for the same duration.
> - Solid blocking geometry can prevent the effect; this is not a universal status applied through walls.
>
> ### SMOKE GRENADE
> - Fuse: **2 seconds of tactical time**.
> - Smoke radius: 2m at first emission; 6m after 2 seconds; maximum 8m after 4 seconds.
> - Dense phase: **18 seconds** after reaching full spread; then a **6-second** dissipation phase.
> - In v0.1 visual tests, tracing through >=4m of dense smoke blocks visual LINE OF SIGHT. Shorter traces through dense smoke add +3 DIFFICULTY to sight-dependent targeting/observation. Other sensors use their actual capabilities rather than inheriting this visual rule.
>
> ## Medical — v0.1 starting values
> Medical fixtures provide physical tools/supplies; they do not grant a generic MEDICINE bonus and do not create treatment results unsupported by the actual injury.
>
> ### MEDKIT
> - BURDEN 1.
> - 6 supply units.
> - 1 AP to open/put into use from QUICK ACCESS; 2 AP from ordinary STOWED access before the treatment action itself.
> - Supports ordinary battlefield interventions such as hemorrhage control, basic airway management, dressing, splinting, injectors, and STABILIZE when the casualty's actual condition can be addressed by those tools.
> - A substantial treatment normally consumes 1 supply unit; trivial examination/diagnosis does not.
> - Does **not** contain the equipment assumed for baseline RESUSCITATION after vital failure.
>
> ### TRAUMA / RESUSCITATION KIT
> - BURDEN 3.
> - 12 supply units.
> - 2 AP setup before advanced use unless already deployed.
> - Includes advanced airway equipment, hemorrhage-control tools, fluids/blood-equivalent supplies, monitoring/defibrillation capability, and serious wound-care equipment appropriate to the reference tech level.
> - Supports STABILIZE, serious TRAUMA treatment, and RESUSCITATION where the cause is physically reversible and the required intervention is represented by the kit.
> - Major trauma or resuscitation consumes 2 supply units; ordinary supported treatment consumes 1.
> - No automatic check bonus: specialist value must still come from CORPSMAN/class/specialty mechanics rather than owning the better box.
>
> ## Technical — v0.1 starting values
> ### FPV DRONE
> - DURABILITY 4.
> - MANUAL CONTROL movement: **12m per operator AP** spent controlling movement; difficult maneuvers may still require DRONE OPERATIONS.
> - Operator control cost: 1 operator AP per AP-equivalent manual drone action.
> - Battery: 300 seconds of active operation.
> - Signal reference: 300m through ordinary urban clutter; 800m in open terrain before environmental modifiers. Solid structure/interference can shorten this drastically.
> - Sensors: ordinary visual camera; in clear open conditions use CONFIRMED-quality observation to ~60m and APPROXIMATE-quality observation to ~150m when target size/visibility support it.
> - Payload: up to ~1kg / one grenade-sized or sensor-sized payload without changing the movement profile.
> - No default weapon. The FPV ONE-WAY FRAGMENTATION PAYLOAD below is a separate fitted module.
>
> ### FPV ONE-WAY FRAGMENTATION PAYLOAD
> - One grenade-sized payload fitted within the FPV DRONE's declared payload capacity.
> - The final attack run costs 1 operator AP after movement. Resolve its uncertain final placement with `1d20 + DRONE OPERATIONS + PRECISION + modifiers` against the real approach/placement DIFFICULTY.
> - On success, the payload detonates at the declared final point. On failure, use the existing GRENADE PLACEMENT failure margin, SCATTER DISTANCE, randomized SCATTER DIRECTION, and constraining geometry to establish the actual final point.
> - Detonation uses the reference FRAGMENTATION GRENADE's fragmentation distance bands, DAMAGE, target PENETRATION, MATERIAL PENETRATION, and frozen BLAST GEOMETRY snapshot. EXPLOSIVE SAVE exists only when the actual attack run provides a real warning window and physically possible response.
> - Detonation consumes the payload and destroys the carrying FPV DRONE. It is not a reusable attack or a free attack alongside operator actions.
>
> ### AUTONOMOUS DRONE
> - DURABILITY 8.
> - Autonomous movement: **8m per AP-equivalent execution**.
> - Issuing or materially changing a STANDING ORDER costs 1 operator AP.
> - While a valid STANDING ORDER persists, the reference drone can execute up to **2 AP-equivalent simple movement/observation actions per TEAM TURN** without further operator AP. This is deliberately much less than another full character turn.
> - Battery: 900 seconds of active operation.
> - Signal reference: 500m through ordinary urban clutter; 1500m in open terrain before environmental modifiers.
> - Sensors: ordinary visual + low-light/thermal package; in clear conditions use CONFIRMED-quality observation to ~80m and APPROXIMATE-quality observation to ~250m where the sensor can genuinely resolve the target.
> - Payload: up to ~3kg of sensor, relay, tool, or other explicit equipment.
> - No default weapon or free attack routine. The AUTONOMOUS LIGHT WEAPON MODULE below is separate fitted equipment.
>
> ### AUTONOMOUS LIGHT WEAPON MODULE
> - Compact self-loading firearm module: DAMAGE 4, target PENETRATION 1, MATERIAL PENETRATION 1, RECOIL 2, SEMI AUTO only.
> - RANGE PROFILE: 0-15m +0, >15-30m +2 DIFFICULTY, >30-50m +5, >50m +8.
> - Capacity 10. An adjacent capable character reloads the external module in 3 AP; the drone cannot reload itself.
> - One deliberate shot consumes 2 of the reference drone's AP-equivalent executions. The drone uses its own +4 autonomous targeting bonus for the ATTACK ROLL and carries persistent RECOIL normally. Skilled MANUAL CONTROL instead uses the operator's DRONE OPERATIONS + PRECISION and operator AP.
> - Autonomous fire requires a valid STANDING ORDER naming the target, zone, or sensor-bound trigger. The drone must currently confirm the target with its own sensors, have a legal line of fire, remain within the order's authorization/collateral limits, and revalidate those facts before every shot.
> - Issuing or materially changing the order still costs 1 operator AP. Once valid, an autonomous shot does not also charge operator AP; ammunition, drone execution budget, RECOIL, geometry, COVER, MATERIAL PENETRATION, target PENETRATION, and consequences remain real.
>
> ### ENGINEERING DEPLOYABLE — FIELD SENSOR / RELAY NODE
> - BURDEN 2.
> - DURABILITY 6.
> - Setup: 3 AP; pack-up: 2 AP if recovered intact. TECH SYSTEMS HANDLING reduces setup to 2 AP; FIELD ENGINEER RAPID DEPLOYMENT reduces it to 1 AP while preserving the minimum action cost.
> - Battery: 1200 seconds of active operation.
> - Sensor coverage: 25m local motion/thermal detection radius where geometry permits; direct visual confirmation to ~40m when its camera has a valid sightline.
> - Signal relay: extends a connected friendly drone/device signal path by up to 150m when the node has a valid link to both sides of the relay.
> - It is a physical, hackable NODE with an actual position. Walls, destruction, power loss, jamming, or hostile access can defeat it.
> - It has no weapon and creates no automatic enemy detection through opaque geometry.
>
> ### POWER BUDGET REFERENCE PAIR
> - The v0.1 counterfactual uses two FIELD SENSOR / RELAY NODE devices connected by an intact, power-capable cable or shared power bus. Their equal reference load makes operating seconds a valid one-for-one exchange for this pair only.
> - A FIELD ENGINEER spends 2 AP to reroute one 300-second block of available operation from the donor to the recipient.
> - At the seed values, donor/recipient effective runtime changes from 1200s/1200s to 900s/1500s. The recipient has not overcharged its battery; it is using the connected donor's allocated power.
> - The recipient loses unused borrowed runtime if the physical power connection breaks. The donor may be reduced to 0s and shut down. A transfer cannot exceed the donor's remaining runtime.
> - The exchange changes endurance only. It grants no sensor-range, relay-range, durability, output, or other performance increase.
> - Rerouting AP and block size are admitted balance parameters. Physical connection, electrical compatibility, one-for-one conservation for this equal-load pair, and the absence of an output bonus are locked fixture behavior.
>
> ## What remains outside this v0.1 equipment note
> The first balance-suite formulas/seeds/ranges for multi-round RECOIL/CONNECTION OPPORTUNITIES, generic HP/RES/AP/movement, common material fixtures, and EXPLOSIVE SAVE warning windows are now defined in [[BALANCE PARAMETERS]] and exercised by [[BALANCE SCENARIOS]]. They remain test-only rather than final player canon.
>
> Still deliberately unresolved outside the initial serious balance suite:
> - final player-facing notation for RECOIL, HANDLING, BURDEN, MATERIAL RESISTANCE, and range bands;
> - final sensory-impairment presentation wording for FLASH / CONCUSSION;
> - exact targeting/attack economy for future **armed** drone modules, which remains REVIEW ONLY until an actual weapon/module fixture exists;
> - Campaign Setting-specific equipment quality, named models, ammunition catalogs, and technology ceilings.
>
> The values above remain intentionally tunable under [[BALANCE CONTRACT]]. A successful optimizer result is only a balance candidate until explicitly accepted and promoted.

## Connections
[[REFERENCE EQUIPMENT]] · [[BENCHMARK FIXTURES]] · [[BALANCE CONTRACT]] · [[BALANCE PARAMETERS]] · [[BALANCE SCENARIOS]] · [[WEAPON PROFILE]] · [[RANGE PROFILE]] · [[RECOIL]] · [[PENETRATION]] · [[MATERIAL PENETRATION]] · [[BODY ARMOR]] · [[BALLISTIC SHIELD]] · [[GRENADE]] · [[BLAST GEOMETRY]] · [[MEDICINE]] · [[DRONE]] · [[FPV DRONE]] · [[AUTONOMOUS DRONE]] · [[FIELD ENGINEER]]
