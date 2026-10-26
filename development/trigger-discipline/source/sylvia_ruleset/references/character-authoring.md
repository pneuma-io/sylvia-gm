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

Run the draft check after assembling the required inputs and correct every reported field error.
The check verifies the declared structure, calculations and settled build constraints. The canonical
review above remains necessary for gameplay support and unquantified effects. Preserve this Character
Builder's saved Setting context when resuming it, even if other campaigns have been played since.

One declaration describes one operative. A squad contains several independently validated
Character Files selected by the Campaign Plan. Initial map placement belongs to that Plan's scene.
Supply the other required starting-state choices from the selected build or the user. This format
does not establish a starting stat budget, equipment allowance or automatic class progression.
