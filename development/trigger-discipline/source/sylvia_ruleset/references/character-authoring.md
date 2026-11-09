# Trigger Discipline character authoring

Use this page to find the packaged Trigger Discipline notes needed for a Character File. The files under `references/canon/` are build-time copies of indexed source files. Canonical notes remain the rules authority; Hubs files only navigate them.

## Reading path

Start with `references/canon/Character/LEVEL.md`, `references/canon/Character/SPECIALTY.md`, `references/canon/Character/CLASS-SKILL INDEPENDENCE.md`, and `references/canon/Character/CHECK.md`.

Read `references/canon/Hubs/Classes.md`, then open the selected class file under `references/canon/Classes/`. Open the specialty files named by that class under `references/canon/Specialties/`.

For each proposed build, check class ownership, the level's choice budget, ENTRY requirements,
tier prerequisites and KEYSTONE eligibility against those notes. Record the player's selected
nodes and the prerequisites they satisfy. A node appearing in the schema's allowed list means
it is packaged; it does not establish eligibility for every class or level. Resolve an ineligible
choice with the player before completing the Character File.

For aptitudes and skills, use `references/canon/Hubs/APTITUDES.md` and `references/canon/Hubs/SKILLS.md`, then read the selected entries under `references/canon/Character/Aptitudes/` and `references/canon/Character/Skills/`.

For personal resources, read `references/canon/Combat/HP.md`, `references/canon/Combat/RES.md`, and `references/canon/Combat/AP.md`. For carried equipment, read `references/canon/Equipment/INVENTORY.md`, `references/canon/Equipment/READY.md`, `references/canon/Equipment/QUICK ACCESS.md`, and `references/canon/Equipment/STOWED.md`.

## Unresolved starting values

The indexed notes do not define starting aptitude or skill allocations, starting HP, RES, or AP, or a starting equipment allowance and loadout. Do not fill those gaps from conformance fixtures, benchmark values, or Testing notes.

Use an explicit build owned by the selected Campaign Setting when it supplies these choices. Otherwise ask the user for the missing choices before treating the character as complete.

## Starting-state format

In a Character Builder chat, retrieve `get_content_definition` with `name: character_creation`.
It exposes the exact participant fields, complete starting-state schema and authored input shape
from the selected Setting's contained Ruleset. `check_participant_draft` checks the current draft
without saving it. The Ruleset copies identity, class, level, skills, aptitudes and specialties
from `participant` and calculates DOWNED from HP. Do not repeat those fields in authored
`entity_state`; Sylvia supplies and verifies them.

`references/specialty-eligibility.json` is generated from the same packaged source identities.
It identifies each node's class, specialty and tier for the review above. Source tooling also
offers `sylvia_ruleset.character_build.build_issues` for the settled class/tier/choice-budget
checks. The same catalog now generates the participant and completed-state schema constraints:
class ownership, level choice budget, unique choices, ENTRY and preceding-tier requirements.
Unresolved nodes are rejected. Normal draft checks and Character File save/reopen enforce these
settled constraints; they do not approve stat/progression allocations or prove gameplay automation.

Supply explicit `max_res` and `max_hp` when the selected character profile establishes them.
No maximum is calculated from the current injured/spent value. The GM-only
`recover_between_encounters` operation restores an explicitly adjudicated amount of RES up to
that maximum, after the GM establishes genuine safety, time and support. Without a maximum,
or in the unsafe/insufficient-time case, it changes no character resource. It never restores
HP/AP, reloads weapons or removes injuries; those require their actual rules and decisions.

The panels display these authored maxima beside current resources. Optional `weapon.label`
supplies the firearm's display name. A detachable magazine may carry its explicit `capacity`
alongside `id` and `rounds`, including spare and retained magazines; it travels with that physical
magazine through reloads and transfers. Tube feeds use the existing `weapon.tube_capacity`.
Omit unknown maxima, capacities and labels rather than deriving them from remaining resources.
These presentation fields do not grant resources, equipment or a starting profile.

For the currently represented `life_threatening` record, use `NONE` when absent and the
canonical `ACTIVE`, `STABILIZED` or `FAILED` states otherwise. The obsolete labels `STABLE`
and `LIFE_THREATENING` do not match treatment. `treat_casualty` requires an active, non-downed
medic, the represented eligible condition, an appropriate capable kit, sufficient AP and a
clear adjacent path on the current map. A downed casualty can still receive treatment.
The GM establishes diagnosis and whether the intervention suits the actual condition; the kit
flag alone does not establish those facts. Read the packaged STABILIZE, RESUSCITATION and FIELD
MEDIC notes. Supply the actual `issue_id`, explicit `ap_cost` and `difficulty`, `check_mode`
(`ROLL` or `CERTAIN`) and `function_recovery` (`PLAUSIBLE` or `NOT_PLAUSIBLE`). These values are
GM rulings from the current rules/Setting, not benchmark defaults. Use `BATTLEFIELD_CARE` for an
actual injury without a represented lethal condition. It does not stabilize some other condition.
An attempted failed check spends its AP without inventing deterioration. STABILIZE/RESUSCITATION
success changes the represented condition to `STABILIZED`, without healing HP or erasing an injury.

The casualty's `battlefield_treatments` records each issue's first successful treatment across
all medics. Reuse one stable injury/condition instance ID (1–128 characters); do not change an ID
or describe the same issue again to gain another first treatment. When the actor has COMBAT
MEDICINE and immediate function recovery is medically plausible, that first success restores the
canonical 1 RES, capped by the casualty's explicitly authored `max_res`. A missing maximum rejects
that recovery attempt without spending resources. First success still counts when no bonus applies
or RES is already full; changing medics, taking damage later or reopening does not reset it.
The history holds at most 256 issues and rejects a new one when full without erasing old receipts.
The action does not automate diagnosis, supply consumption, other treatment effects or specialty
discount calculations. Explicit treatment costs can reflect a justified GM ruling; no unsettled
discount or starting-resource value is selected by this format.

`entity_state.weapon.controlled_by` represents the restraint described in
`references/canon/CQC/CONTROL.md`. Omit it for an unrestricted starting weapon. It does
not record ordinary ownership; putting the operative's own ID there prevents weapon use.

`handle_weapon` is a GM-adjudicated action for the actor's existing carried firearm. Supply
its actual ID, a READY/QUICK_ACCESS/STOWED destination, and the AP cost justified by the
storage location and situation. No default cost is supplied. Handling preserves the firearm,
its ammunition and recoil, breaks maintained aim/prepared fire, and changes the same weapon
read by firing and Loadout. It cannot retrieve a disarmed weapon from the ground or acquire,
transfer or swap equipment. Those operations are not automated by this action; do not report
a generic inventory transfer as having changed the active TD firearm.

GUNNER's SUSTAINED FIRE DISCIPLINE and OPERATOR's WEAPON MANIPULATION now have
owner-approved provisional first-playable rules. Read
`references/rifleman-entry-provisional.md` for their exact values, chain continuity,
physical-operation boundaries and explicit-final-cost handling. Other specialty
numbers and starting profiles remain unresolved. These implementations do not
establish final balance or native acceptance. Do not author a starting
`gunner_chain_weapon_id`; it records actual firing history during play.

Optional `entity_state.reserve_weapons` contains at most 16 other carried firearms in
QUICK_ACCESS or STOWED locations, each with the same complete weapon-state shape. Give each
physical firearm its own stable ID; do not copy a weapon into both current and reserve slots.
This field supplies no starting equipment allowance. `switch_weapon` moves one reserve object
to `weapon` and stores the previous one, preserving ammunition, chamber, cycling state and
recoil. Its required explicit total AP cost accounts for the actual handling/storage situation.
An absent reserve, repeated or restrained weapon rejects the switch without spending AP.
The current `weapon` may be `null` when no firearm occupies that slot. Omit `current_weapon_id`
when switching from that empty slot; the reserve moves into it without inventing an old weapon.
Reserve entries must still be complete weapon objects, never null placeholders.

`transfer_reserve_weapon` hands an existing reserve object to an adjacent active recipient
through the same clear short physical path used for close interactions. Any current firearms
must be stored first; an empty current slot is allowed. It spends the giver's explicit handoff cost, removes the original object
and adds it to the recipient's reserve list in the same operation. The receiver can then use
`switch_weapon`; a handoff does not reload or ready it. An optional `owner_id` records known
ownership independently from the carrying character and survives both operations. Omit it
when ownership is unknown. These tools transfer possession, not legal ownership, and do not
create purchases, ammunition or new profiles.

`drop_weapon` moves the complete current weapon to `state.ground_objects.weapon_N` at its holder's
actual scene position and empties the current slot. Successful DISARM uses that same path. Ground
records include the scene map, position and full weapon state. `pickup_weapon` requires the exact
ground and weapon IDs, the current map, a clear adjacent physical path, and an explicit AP cost.
It moves the existing weapon, including ammunition/recoil/recorded ownership, and clears the ground
slot. An empty current slot can receive it READY; otherwise store the current firearm first and
pick up into QUICK_ACCESS or STOWED reserves. Neither operation supplies a handling-cost default.
Dropping and picking up break maintained aim/prepared fire. Failed pickups preserve equipment/AP;
a stale ground counter rejects a drop rather than overwriting another object. These actions do not
create new equipment or change recorded ownership.

The package explicitly selects these combat-state objects as its inventory. The shared
`campaign_state/inventory` reference names their state and supported update tools; Loadout
reads those same objects. Sylvia's fallback inventory ledger is unavailable for this package.
Use the declared TD action for equipment changes and report unsupported acquisition or ownership
changes as unresolved rather than recording a second ledger or claiming the weapon was changed.

For an existing autonomous drone, `assign_drone_order` records WATCH or
ENGAGE_CONFIRMED_TARGET against an actual target. Supply a stable `order_id` and the explicit
operator `ap_cost`; a changed order needs a new identity. The authored drone must name its actual
operator in `controlled_by` and have a genuinely established `signal_connected` link. This command
does not create or change either fact. It preserves the drone's actions, ammunition and sensor
knowledge. Repeating the same order identity and contents spends nothing; conflicting contents
under that identity are rejected. Identities are durable per drone across replacement and reopen;
the receipt also binds the operator, target, mode and explicit AP cost. Replaying an earlier order
never restores it over a newer one. Each drone retains at most 256 identities, with no eviction:
at the limit a new identity refuses before spending; retained replays/conflicts still resolve.
An identified legacy order lacking a complete receipt is reserved across replacement and cannot
execute again. History already lost by older bytes cannot be reconstructed or claimed as verified.
The existing `autodrone_fire` resolver owns each shot and still
requires actual confirmation of the assigned target. WATCH never authorizes fire. Complex
AUTODRONES triggers, patrols, movement and contingencies retain explicit GM resolution; storing an
order does not claim those effects executed. No default command cost or specialty discount is set.

CONDITIONAL ORDER has one supported additional sensing condition. Supply an existing physical
entity as `condition_entity_id` on an ENGAGE_CONFIRMED_TARGET order. Both the AUTODRONES ENTRY
and CONDITIONAL ORDER must be selected. The drone fires only while that additional entity and
the ordinary target are both in its actual `sensor_confirmed_targets`; otherwise it continues
WATCH without spending shot resources. Preview and execution read the same condition. Assignment
creates no sensor evidence. The receipt binds this optional condition, including across replacement
and recovery; old receipts without it still identify an unconditional order. Other conditions,
responses and autonomous sensor/movement simulation remain explicit GM work.

`discover_network_quietly` applies selected QUIET ENUMERATION to ordinary DISCOVER. It uses the
same actual compromised node, route, privilege, authored AP cost, check and discoverable links.
Only this action's HACKING VISIBILITY drops one ordered band (HIGH to STANDARD, STANDARD to LOW,
LOW stays LOW); relevant monitoring still resolves normally. It does not create access, bypass a
boundary, reveal hidden paths or make reconnaissance undetectable. Other mapping actions and
other NETWORKER nodes retain their separately stated support boundaries.

For an operative who selected SECOND TRIGGER, `prepare_sentinel_overwatch` prepares ordinary
OVERWATCH with an explicitly adjudicated total `shot_allowance` greater than the ordinary value.
It pays the ordinary setup cost; it does not choose a specialty trigger-count default or apply
other SENTINEL nodes. Each actual valid trigger uses the existing resolver, ammunition, RECOIL
and cycling rules. Invalid or spent preparation grants no attack. Ordinary `prepare_overwatch`
keeps its existing allowance.

For PREPARED SIGHTLINE, `prepare_sentinel_sightline` combines one ordinary target-specific
AIM action with ordinary OVERWATCH at the actor's current position. Supply `aim_ap` within
the ordinary action's supported range and an explicitly adjudicated combined `ap_cost` below
the separate costs. The package chooses no discount. The selected ENTRY, current zone, usable
weapon, active/non-downed actor and actual target exposure are required before either preparation
or cost changes. It stores the stationary origin; moving away ends this preparation and its AIM,
even if the character returns. An ordinary replacement watch removes the stationary constraint.
The normal prepared shot applies and consumes the same AIM benefit as ordinary deliberate fire.
This does not automate BROAD SECTOR, STABLE NEST or CROSSING SHOT numerical benefits.
For a build that also selected SECOND TRIGGER, the same combined action accepts an explicit
`shot_allowance` greater than the ordinary value, with no second setup action or charge. Omit
that input (or use the API sentinel 0) to keep ordinary OVERWATCH's existing allowance; 0 is not
a new rule quantity. No retained-trigger count is chosen for the player.

Retained triggers recheck the watcher's current condition, weapon and actual exposure. A downed
watcher or a wholly screened crossing spends no ammunition, recoil or trigger allowance.

For COUNTER-PREP, opt in with `counter_prep: true` on ordinary or derived watch setup.
This is the same ordinary operation and setup price, available through its declared tool.
The existing Map action continues to request ordinary watch; it has no configuration toggle.
The watcher must have a legal SHARPSHOOTER build with ENTRY, two TIER 1 choices, two TIER 2
choices and COUNTER-PREP (at least level 6). An old or omitted flag remains ordinary movement
watch. Configuration adds exposed enemy AIM, OVERWATCH and the supported combined/DEEP AIM
preparations as triggers; it does not remove movement triggers or add a shot allowance.
An enemy must actually be inside the saved zone with current exposure. Entirely full-cover
actions, invalid preparations and unavailable or spent watches cause no shot. Each response
uses ordinary ammunition, chamber, RECOIL and AIM rules, with no generic reaction AP. Incoming
watch and SUPPRESSION resolve through the existing same-moment ordering, with prerequisites
rechecked before each effect and before the preparation continues. Combined sightline setup
is one event, not separate AIM and watch events. Duplicate action receipts cannot fire again.
The package still has its existing single watch slot and single suppression slot; this does
not implement multiple simultaneous watcher ownership or periodic unchanged-watch events.

`advance_character` records one GM-confirmed earned milestone, one level and one legal specialty.
Supply the same identity for the actor and its live Character File, the current expected level,
a stable milestone ID and the chosen specialty. The action updates both the runtime actor and
the live participant together, preserving the saved reusable source profile. It uses
the same class/tier prerequisites as Character File admission, rejects duplicate choices and caps
level at 20. Repeating a recorded milestone grants nothing. It preserves skills, aptitudes, HP,
RES, AP, injuries and equipment, including ammunition. Numerical progression is unresolved; this
action supplies no resource gains, healing, skill increases or starting defaults.

For a manual-action tube-fed firearm, `load_one_shell` moves one actual `spare_shells` round
into its existing tube and spends the explicit weapon `shell_load_ap`. Author `tube_capacity`
excluding the chamber. The B12's supplied equipment record specifies capacity 8 and loading/cycle
cost 1 AP each; it does not select a starting quantity of shells. These optional state fields
have no defaults. Each completed shell persists independently, including when AP runs out.
The action preserves LIVE, EMPTY or SPENT chamber state; an empty/spent action then needs the
separate existing `cycle_weapon` operation. Loading breaks the acting character's maintained aim
and prepared fire. It supplies no automatic numerical recoil recovery. Other feed/mechanism
combinations remain explicitly unsupported by this shell action.

For an authored range-dependent firearm, `range_damage` has four entries matching its three
`range_breakpoints_m` plus the beyond band. Omit it for ordinary fixed damage. A null entry is
unresolved, not a fallback to `damage`: the B12 uses `[7, 5, 3, null]`, breakpoints `[10, 20, 35]`
and difficulty modifiers `[0, 2, 5, 8]` from its supplied equipment record. The runtime uses the
ordinary ray's actual target-intersection distance and existing hit-quality, cover and penetration
resolution. No per-pellet rolls are introduced. Unknown-band ordinary fire rejects before rolling
or changing AP, ammunition, recoil or preparation. A prepared shot in that band records its
unsupported status without consuming its trigger allowance or ammunition. The existing bounded
scalar preview cannot perform the ray-intersection search; it warns that range damage is checked
on commit rather than claiming a numeric damage estimate or exact range eligibility. This is a
presentation limitation, not permission for the GM to substitute an unsupplied damage value.

Run the draft check after assembling the required inputs and correct every reported field error.
The check verifies the declared structure, calculations and settled build constraints. The canonical
review above remains necessary for gameplay support and unquantified effects. Preserve this Character
Builder's saved Setting context when resuming it, even if other campaigns have been played since.

One declaration describes one operative. A squad contains several independently validated
Character Files selected by the Campaign Plan. Initial map placement belongs to that Plan's scene.
Supply the other required starting-state choices from the selected build or the user. This format
does not establish a starting stat budget, equipment allowance or automatic class progression.
