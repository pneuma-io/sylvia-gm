# The Honest Thief

> GM preparation: contains adventure spoilers.

## Premise

Recover stolen wages from a guarded Thornwall counting-house. Scout the rooms, beat its locks and traps, and get the money and a witness through a dangerous exit.

Tone: A compact fantasy heist with stealth, traps and an armed escape route.

A focused Severed Lands D&D adventure combining dangerous exploration, meaningful character abilities and prepared combat with choices about the people involved.

## Character entry

```json
{
  "custom_companions": {
    "maximum": 2,
    "minimum": 0
  },
  "player_character": {
    "mode": "compatible"
  },
  "required_companions": []
}
```

## Boundaries

Apply the player’s selected content boundaries; offer a less intense presentation where needed.

## Starting location

```json
{
  "id": "thornwall",
  "path": "locations/thornwall.md"
}
```

## Opening direction

Open beside a cooling Thornwall forge with unpaid workers Bren and Lio, their pay chits and a rough counting-house sketch. They ask for help; no infiltration or agreement has yet occurred.

## Starting facts and state

```json
{
  "entities": [],
  "facts": [
    {
      "fact": "The counting-house hosts a dinner tonight. Its office still holds the payroll and account books.",
      "id": "opening-1",
      "known_by": [
        "player"
      ]
    },
    {
      "fact": "Bren and Lio seek 60 gp in withheld wages and offer a separate 10 gp fee; neither consents to harming servants.",
      "id": "opening-2",
      "known_by": [
        "player"
      ]
    }
  ],
  "mechanics": [
    {
      "key": "state",
      "namespace": "sylvia.srd-lite.bg3",
      "value": {
        "advancement": {
          "allocation": "The Player Character and Companions start and remain at level 2. Story success, defeat, combat and optional jobs never award advancement. Grant only the adventure's actually earned money, possessions and narrative rewards. Any lower-level encounter-variant notes are authoring guidance for a separately revised Plan, not permission to override this declaration in play.",
          "maximum_level": 2,
          "method": "fixed",
          "recipients": "whole_party",
          "starting_level": 2,
          "timing": "This edition stays at level 2 throughout its authored adventure and optional continuation. It awards no XP or level increases; changing that policy requires an explicitly revised Plan."
        }
      }
    }
  ]
}
```

## Private truths and reveal conditions

```json
{
  "fact": "Fenwick knowingly deducted tools that remained in his own stock; Sella preserved a true copy of the ledger.",
  "id": "false-deductions",
  "known_by": [
    "fenwick",
    "sella"
  ],
  "reveal": {
    "condition": "Compare pay chits, accounts and stock, or obtain Sella’s supported testimony.",
    "from_act": "act-1"
  }
}
```

## The payroll job

Plan and execute a counting-house recovery, resolving the wages, evidence and witness’s safety.

Truth and cast. Fenwick falsified tool deductions to cover a speculative loss. Accountant Sella copied the real ledger and wants her papers and safe departure. Steward Noll protects the staff and is willing to clear civilians from trouble. Two hired guards protect the building; a single brutal debt collector arrives at the rear service gate when the dinner concludes. None automatically attacks a surrendered or unconscious person. The workers need money and evidence, not a massacre.

Reconnaissance and routes. A front reception hall connects to B dining room and C upper landing. D kitchen opens to the alley and to C by the rear stair. C reaches E the office. E’s narrow balcony looks down into D’s service yard, whose gate leads to the street. The front guard stays at A; the landing guard walks C–E. Dexterity (Stealth) DC 12 during reconnaissance supplies patrol timing. Failure makes a guard ask questions; a credible explanation or Charisma (Deception/Persuasion) DC 12 avoids an escort back to the street. A worker’s invitation, disguised delivery, climb or Sella’s cooperation is enough to attempt entry; no mandatory social prelude is required.

Climb, locks and leverage. A prepared rope on the balcony allows an ordinary climb; a wet unassisted climb needs Strength (Athletics) DC 12, with a failed attempt causing a ten-foot fall for 1d6 bludgeoning unless secured. The side door lock is thieves’ tools DC 12; the office lock DC 13, or either uses a key stolen through Dexterity (Sleight of Hand) DC 13 against an unaware holder. Failure at a lock costs time and noise, advancing the known patrol toward that door. Actual spell effects can change sight, sound, access or movement only within their rules; neither invisibility nor a charming manner automatically bypasses every obstacle.

Trapped strongbox. Scratches under the latch and a fine nozzle are visible on close examination. Intelligence (Investigation) DC 12 identifies a spring needle; thieves’ tools DC 13 disables it. A failed disable attempt triggers Dexterity save DC 12; failure takes 1d4 piercing plus 1d4 poison, success takes neither, and the trap is then spent. Sella knows the spare key under the pen tray and the needle’s safety catch. Credible agreement to get her papers out secures both without a roll. The box holds the workers’ 60 gp separately from 25 gp in client deposits. The latter is not bonus loot without a deliberate decision to steal it.

Accounts and alarm. Comparing the chits with tools still in stock proves the deductions false. Sella’s copy preserves that proof if the box is inaccessible. Loud forced entry, an observed attack or an exposed intruder raises the alarm. The nearer guard responds first; the other remains with the dinner guests for the solo baseline. Barricading C, using the balcony or distracting the patrol can change the confrontation. A short rest is possible only after leaving to a safe public location; state that the payroll will be transferred at the dinner’s close if the party leaves it behind. Rest is not forbidden, but the target’s ordinary schedule continues.

The service gate. The collector wants the payroll transferred to his patron. A forged release checked with Deception DC 13, Fenwick’s genuine written repayment order, or exposing the fraud to witnesses can avert his attack. On failure he demands the box and blocks the gate; combat or an alternate exit remains possible. Getting Sella and the wages out matters more than pursuing a fleeing foe. Settle the workers’ actual payment, the evidence and the 10 gp fee. Confiscated weapons may be kept only as established possessions; no automatic stolen-property sale happens in the background.

### Objectives

objective-1

### Approaches and branches

Use social access, stealth, tools or supported magic; confrontation is a viable but costly route.

Evidence can obtain an agreed repayment, but the guarded extraction and physical access remain concrete situations to resolve.

### Consequences

Record alarms, used resources, who saw the character and which property actually left.

All linked objective conditions are required, including the named closing event. Carry optional promises forward; they neither replace nor extend this ending.

### Prepared encounters

Fight 1, a hired guard intercepts on C if the infiltration is detected. Guard: AC 13, HP 10, speed 30 ft, initiative +1. One melee attack, +3 to hit, reach 5 ft, one target, 1d6+1 piercing. STR +1, DEX +1, CON +1, INT +0, WIS +0, CHA +0 for other checks/saves; no added proficiency.  The guard has a whistle, takes an action to use it, and accepts surrender. The dining-room guard does not pile in on the solo baseline. Solo level 1: HP 7, attack damage 1d4+1.

Fight 2, debt collector at the service gate. Collector: AC 12, HP 14, speed 30 ft, initiative +1. One melee attack, +3 to hit, reach 5 ft, one target, 1d6+1 bludgeoning. STR +1, DEX +1, CON +1, INT +0, WIS +0, CHA +0 for other checks/saves; no added proficiency.  The gate, rain barrels and balcony create alternative routes and half cover. The collector yields at 4 HP and bargains for a way out; no second form or hidden reinforcements. Solo level 1: HP 9 and damage 1d4+1. A clean operation can bypass both fights through actual successful preparation.

### Encounter adjudication

Run declared ability checks, saving throws, initiative, attacks, damage, conditions, spells and rests through the admitted D&D Ruleset. Before an uncertain roll name its purpose, DC and consequence. Give plainly visible facts and essential leads without a roll; success can improve position, speed or discretion. A supported spell or sound equipment use can solve an obstacle outright when its actual text allows. Track the real HP, spell slots, ammunition, carried gear and recovery resources; never restore them between scenes by narration. These Plans do not install extra engine procedures.

### Setting sources

factions/warrens-court.md

locations/thornwall.md

## Campaign objectives

```json
{
  "completion_conditions": [
    "The counting-house operation has ended with the party outside and the 60 gp wages recovered, actually paid by Fenwick under the negotiated substitute, or reported unrecovered after a failed attempt. A promise of later repayment is an outstanding debt, not recovered wages.",
    "Sella has reached her chosen safe departure point or her observed loss or last known position is reported; record whether her papers and the deduction evidence were recovered and who holds them. Client deposits remain separate from the workers' wages.",
    "Closing event: the workers or Noll on their behalf receive the actual recovered money or failed-recovery report and respond; settle the 10 gp fee as agreed and identify the owner of any unpaid debt. There is no compulsory hunt for a fleeing collector."
  ],
  "description": "Plan and execute a counting-house recovery, resolving the wages, evidence and witness’s safety.",
  "id": "objective-1",
  "title": "The payroll job"
}
```

## Intended ending or ongoing final act

```json
{
  "mode": "finite",
  "outcomes": [
    {
      "after_story": null,
      "conditions": [
        "The counting-house operation has ended with the party outside and the 60 gp wages recovered, actually paid by Fenwick under the negotiated substitute, or reported unrecovered after a failed attempt. A promise of later repayment is an outstanding debt, not recovered wages.",
        "Sella has reached her chosen safe departure point or her observed loss or last known position is reported; record whether her papers and the deduction evidence were recovered and who holds them. Client deposits remain separate from the workers' wages.",
        "Closing event: the workers or Noll on their behalf receive the actual recovered money or failed-recovery report and respond; settle the 10 gp fee as agreed and identify the owner of any unpaid debt. There is no compulsory hunt for a fleeing collector."
      ],
      "id": "adventure-settled",
      "immediate_aftermath": [
        "Closing event: the workers or Noll on their behalf receive the actual recovered money or failed-recovery report and respond; settle the 10 gp fee as agreed and identify the owner of any unpaid debt. There is no compulsory hunt for a fleeing collector."
      ],
      "open_world": true,
      "outcome": "This adventure has reached its authored closing event with the actual successes or losses acknowledged. Its earned result and continuing consequences remain true."
    }
  ]
}
```

## GM guidance

Run declared ability checks, saving throws, initiative, attacks, damage, conditions, spells and rests through the admitted D&D Ruleset. Before an uncertain roll name its purpose, DC and consequence. Give plainly visible facts and essential leads without a roll; success can improve position, speed or discretion. A supported spell or sound equipment use can solve an obstacle outright when its actual text allows. Track the real HP, spell slots, ammunition, carried gear and recovery resources; never restore them between scenes by narration. These Plans do not install extra engine procedures. The encounter profiles below are complete bounded GM adaptations for the stated baseline, not the full Setting stat blocks or certified CR ratings. Use only their listed attacks and traits. All have one action, no multiattack, legendary actions, resistances, immunities or unlisted spellcasting. Ordinary rules for movement, cover, opportunity attacks, surprise and surrender still apply. For an uncertain opposing ability check or save, use the listed modifiers without inventing proficiency. Before engagement, admit the actors through the current encounter tools and declare any supported additional trait. Never narrate a proprietary creature power as an automatic engine effect. Every encounter has a reason to occur and a way to withdraw, negotiate or gain an advantage. Honour a clever bypass as a mechanical success; preserve the wider hazardous adventure instead of forcing a replacement fight. Do not reduce this prepared adventure to a sequence of conversations.

Make the named main problem and its leads accessible. Read the current act and all linked objective conditions before framing its main quest and reassess when a required result occurs. All listed conditions and the closing event are required; explicit alternatives satisfy only their own condition. Optional scenes can be deferred without adding gates. A completed side job does not replace the main story. Do not fabricate success or mark unplayed later acts complete after an early departure.

The player chooses their actions, loyalties and risks. Refusal, failed checks, retreat, scouting, spell use and enemy surrender can change the route. A check resolves an uncertain action, not another person’s entire will.

Baseline: one level-2 Player Character with no Companions. A lower-level or badly depleted solo character should face one active enemy at a time and the reduced HP/damage option stated for the encounter; prepare that adjustment before initiative. For a small adventuring party, add at most one ordinary foe to a fight before it starts, or use an appropriate fully supported profile after inspecting the actual party. Larger parties need deliberate rebalancing. Character compatibility does not enforce level or establish balance. Allied workers supply information, ropes and agreed off-screen work; they never receive free player-character combat turns.

Use the authored sites, opposition, clues and consequences. Core clues have alternate routes; opening a door or making a routine observation never chooses the character’s next action. Later acts inherit actual resources, wounds, allies and evidence. Keep stakes local to this expedition, relic, crew or route: the Anchor Door mystery and world-scale climax belong to The Rift Awakening.

## Completion and aftermath

The counting-house operation has ended with the party outside and the 60 gp wages recovered, actually paid by Fenwick under the negotiated substitute, or reported unrecovered after a failed attempt. A promise of later repayment is an outstanding debt, not recovered wages.

Sella has reached her chosen safe departure point or her observed loss or last known position is reported; record whether her papers and the deduction evidence were recovered and who holds them. Client deposits remain separate from the workers' wages.

Closing event: the workers or Noll on their behalf receive the actual recovered money or failed-recovery report and respond; settle the 10 gp fee as agreed and identify the owner of any unpaid debt. There is no compulsory hunt for a fleeing collector.

## Failure and withdrawal

Use the explicit failed-attempt outcome and closing report in the current objective. An intention to withdraw, an unrelated side task or delegation alone does not satisfy it. An early departure leaves unplayed later objectives unfinished.

Resolve defeat, unconsciousness, death and recovery under the selected Ruleset and agreed boundaries; no scripted rescue cancels committed results.

## Optional continuation

Further adventures require the player’s interest after this story has a complete ending. Surviving contacts and obligations can carry forward.

## Side opportunities

## Mechanics requiring GM adjudication

Site phasing, enemy motives, alarms and engineering objectives are explicitly described GM adjudications. Checks, saves, damage, spells, rest and admitted combat use the D&D Ruleset. No extra automatic hazard, pursuit, faction, economy or crafting subsystem is installed.

Listed creature profiles are weaker or specialised Setting adaptations with declared statistics. They do not assert support for unencoded powers from the original monster entries.

Gold, salvage and consumables are actual awarded possessions only when recorded through the applicable inventory/state path. A narrative reward never silently installs a magic-item feature, restores a slot or applies healing.

## Referenced Setting files

```json
{
  "creatures": [],
  "factions": [
    {
      "id": "warrens-court",
      "path": "factions/warrens-court.md"
    }
  ],
  "items": [],
  "locations": [
    {
      "id": "thornwall",
      "path": "locations/thornwall.md"
    }
  ],
  "lore": [],
  "npcs": []
}
```
