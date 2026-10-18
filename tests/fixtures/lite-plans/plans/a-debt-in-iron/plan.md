# A Debt in Iron

> GM preparation: contains adventure spoilers.

## Premise

As Lodge trainee Tavin Bell, retrieve a pledged Warden relic from a dangerous salvage vault, then defend the crew carrying it out while deciding how to face your mentor’s debt.

Tone: A personal relic expedition with construct combat and a contested extraction.

A focused Severed Lands D&D adventure combining dangerous exploration, meaningful character abilities and prepared combat with choices about the people involved.

## Character entry

```json
{
  "custom_companions": {
    "maximum": 2,
    "minimum": 0
  },
  "player_character": {
    "character": {
      "id": "tavin-bell",
      "path": "characters/tavin-bell.json"
    },
    "mode": "fixed"
  },
  "required_companions": []
}
```

## Boundaries

Apply the player’s selected content boundaries; offer a less intense presentation where needed.

## Starting location

```json
{
  "id": "waystation",
  "path": "locations/waystation.md"
}
```

## Opening direction

Introduce Tavin Bell from the accepted Character File beside the Lodge’s missing-relic notice. Quarterkeeper Ysra has a salvage camp location and the signed pledge. Establish Tavin’s past agreement without deciding their feelings or next action.

## Starting facts and state

```json
{
  "entities": [],
  "facts": [
    {
      "fact": "Tavin Bell signed a pledge involving a borrowed Warden survey plate to cover mentor Corren’s debt. The plate has not returned to the Lodge.",
      "id": "opening-1",
      "known_by": [
        "player"
      ]
    },
    {
      "fact": "Salvage captain Eda Venn holds the pledge but lost access to the plate when a Warden vault sealed around her survey crew. Ysra requests its recovery and an honest account.",
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
          "allocation": "The Player Character and Companions start and remain at level 1. Story success, defeat, combat and optional jobs never award advancement. Grant only the adventure's actually earned money, possessions and narrative rewards. Any lower-level encounter-variant notes are authoring guidance for a separately revised Plan, not permission to override this declaration in play.",
          "maximum_level": 1,
          "method": "fixed",
          "recipients": "whole_party",
          "starting_level": 1,
          "timing": "This edition stays at level 1 throughout its authored adventure and optional continuation. It awards no XP or level increases; changing that policy requires an explicitly revised Plan."
        }
      }
    }
  ]
}
```

## Private truths and reveal conditions

```json
{
  "fact": "Corren knew the pledge was overdue and separately promised the same plate to Rusk.",
  "id": "mentor-knew",
  "known_by": [
    "corren",
    "eda",
    "rusk"
  ],
  "reveal": {
    "condition": "Compare Eda’s dated letter and Rusk’s receipt, or obtain Corren’s supported admission.",
    "from_act": "act-1"
  }
}
```

## The sealed survey vault

Reach the pledged relic and free the surviving survey crew from its old defences.

People and truth. Corren knew repayment was overdue before telling Tavin it was settled. Eda legally acquired the pledge in good faith and spent money on a survey, but the plate is Lodge property Corren could not sell. Ysra wants the relic and an accurate record; her loyalty is not bought with a successful duel. At Eda’s camp, apprentice Myr is trapped behind the vault’s maintenance gate and foreman Kellin has escaped with a diagram. Eda offers to reduce her claim if Tavin rescues Myr and retrieves the crew’s instruments alongside the plate.

Compact vault. A camp approach enters B survey antechamber. From B, a guarded direct stair descends to D plate dais; a narrow maintenance passage reaches C counterweight room, then D from behind its pillar. E instrument store is barred from D and holds Myr. There is no world-altering Door here: the device projects local survey alignments and the plate supplies calibration marks. The scratched camp diagram identifies C, so a failed check never conceals the alternate route.

Antechamber defence. A damaged Warden Armor guards the direct stair. Its floor pattern includes a maintenance stripe; Intelligence (Investigation or Arcana) DC 12 interprets the worn symbol and permits a slow safe passage along that stripe. Failure leaves the symbol unclear, not the armour already attacking. Kellin’s diagram or spending time tracing the stripe from C supplies another way. Crossing the guarded stair without resolving its command starts the prepared fight. Spells act according to actual target restrictions; an enchantment valid against a humanoid does not necessarily affect a construct.

Counterweight hazard. A visibly frayed chain supports C’s narrow walkway. A secured rope or a braced ladder bypasses the risk. Rushing unsupported requires Dexterity save DC 11; failure causes 1d4 bludgeoning on a shallow fall and leaves the character below the walkway with a ladder route back. Tools DC 11 locks the counterweight before crossing; a failed attempt costs a pin or time if that was stated, and warns that the chain is moving. Do not create a second surprise damage trap behind the same check.

Plate and prisoner. The plate rests on a pressure cradle. Myr can call out its paired hand-release from E. Releasing Myr uses the key at C, thieves’ tools DC 12 or lifting a bent bar with Strength DC 12; failure alerts the guardian if still active but does not collapse the vault. Two people holding the releases, or an equivalent supported tool arrangement, remove the plate safely. Removing it blindly closes D’s gate; its manual catch remains accessible with tools DC 12 from either side. The store contains the crew’s 20 gp of instruments and a healing potion they offer to the rescuer. Record whether it is used or kept.

### Objectives

objective-1

### Approaches and branches

Decode the command, take the maintenance passage or defeat the guardian.

Free Myr before touching the plate, or accept the extra access problem caused by acting without their warning.

### Consequences

Carry the plate, instruments, Myr’s condition and any potion use into the camp scene.

All linked objective conditions are required, including the named closing event. Carry optional promises forward; they neither replace nor extend this ending.

### Prepared encounters

Fight 1, a badly damaged vault guardian for Tavin’s level-1 baseline. Warden Armor adaptation (construct): AC 13, HP 10, speed 20 ft, initiative +0. One melee attack, +3 to hit, reach 5 ft, one target, 1d4+1 bludgeoning. STR +1, DEX +1, CON +1, INT +0, WIS +0, CHA +0 for other checks/saves; no added proficiency.  No full-profile immunities or multiattack. It prioritises defending the direct stair, never pursues beyond B, and can be shut down with the identified maintenance command from C. For a stronger party restore HP to 18 before combat rather than adding an unexplained new guardian.

### Encounter adjudication

Run declared ability checks, saving throws, initiative, attacks, damage, conditions, spells and rests through the admitted D&D Ruleset. Before an uncertain roll name its purpose, DC and consequence. Give plainly visible facts and essential leads without a roll; success can improve position, speed or discretion. A supported spell or sound equipment use can solve an obstacle outright when its actual text allows. Track the real HP, spell slots, ammunition, carried gear and recovery resources; never restore them between scenes by narration. These Plans do not install extra engine procedures.

### Setting sources

content/monsters/warden-armor.md

factions/warden-lodge.md

factions/hollows-crews.md

locations/waystation.md

locations/the-hollows.md

## The iron road

Get the recovered people and relic past a rival claim, then settle Tavin’s responsibility.

A earned pause. Eda’s camp offers a secure short rest after the vault. Tavin’s actual Second Wind uses, Hit Dice and HP come from the accepted character and Ruleset, with no narrative refill. Corren’s dated letter is in Eda’s unlocked contract folio; she shows it on request. It proves he knew the debt was overdue. The player can speak with him by messenger or take the evidence back, but the expedition still has a physical return route.

The rival factor. Corren also promised the plate to collector Rusk to delay another demand. Rusk blocks a narrow stone causeway with one armed retainer. His own receipt explains why he expects delivery; it does not grant ownership over the Lodge’s object. The retainer begins at the barricade while Rusk argues from behind it. A credible witnessed agreement to hear his claim in Waystation can persuade him to withdraw; Persuasion DC 12 wins immediate passage if offered concrete security. Failure means he demands the plate and prepares to seize it, not that every further approach is impossible.

Causeway choices. Waist-high fallen stones give half cover. An old cart track descends around the barricade but crosses a loose slope: a fixed rope or crew help makes a careful descent safe; a hurried unsupported descent uses Athletics DC 11, with failure inflicting 1d4 bludgeoning and costing movement while the carrier regains the path. Stealth DC 12 can move the plate via that track while the party talks; failure reveals the route and allows Rusk to react on his turn. Myr can show this path if rescued. Carrying a heavy instrument case matters to free hands; a dropped case remains recoverable instead of disappearing.

After force or agreement. The survivors yield, flee or testify according to play. Rusk’s receipt is useful evidence even if he leaves; the retainer can corroborate it. No winner’s sword magically settles two ownership claims. At the Lodge, Ysra accepts recovery, an honest loss report or a supervised shared-use proposal. Eda’s documented 20 gp expense is reduced by returning the instruments and rescuing Myr. Corren can repay the remainder by work and surrender his role as Tavin’s mentor. Tavin may defend him, expose him, change mentors or leave; do not dictate forgiveness.

Concrete ending. The Lodge pays 15 gp for the safe recovery, restores ordinary borrowed supplies and records the crew’s help. Eda offers one future safe camp contact if fairly treated. The survey plate remains an institutional tool, not a free magic weapon. Resolve any legal claim through the established local agreement, name who keeps the plate and let Tavin choose the next personal step. This ends the relic adventure without recruiting Tavin into the flagship’s central mystery.

### Objectives

objective-2

### Approaches and branches

Negotiate passage, use Myr’s alternate path, distract the factor or defeat the retainer.

The plate can be returned, shared under agreement, lost or knowingly surrendered; settle the consequence honestly.

### Consequences

Myr’s survival, recovered instruments and Corren’s evidence change the available settlement and the return route.

All linked objective conditions are required, including the named closing event. Carry optional promises forward; they neither replace nor extend this ending.

### Prepared encounters

Fight 2, causeway retainer if Rusk orders seizure. Rusk’s retainer (humanoid): AC 12, HP 9, speed 30 ft, initiative +1. One melee attack, +3 to hit, reach 5 ft, one target, 1d4+1 slashing. STR +1, DEX +1, CON +1, INT +0, WIS +0, CHA +0 for other checks/saves; no added proficiency.  Rusk uses the first rounds to seize or bargain over the object, not provide a second attack against the lone level-1 protagonist. If personally attacked, Rusk has AC 11, HP 6 and an improvised strike +2 for 1 bludgeoning. Either yields when the retainer drops or a credible escape is offered. Additional party members may justify one additional retainer declared before initiative.

### Encounter adjudication

Run declared ability checks, saving throws, initiative, attacks, damage, conditions, spells and rests through the admitted D&D Ruleset. Before an uncertain roll name its purpose, DC and consequence. Give plainly visible facts and essential leads without a roll; success can improve position, speed or discretion. A supported spell or sound equipment use can solve an obstacle outright when its actual text allows. Track the real HP, spell slots, ammunition, carried gear and recovery resources; never restore them between scenes by narration. These Plans do not install extra engine procedures.

### Setting sources

content/monsters/warden-armor.md

factions/warden-lodge.md

factions/hollows-crews.md

locations/waystation.md

locations/the-hollows.md

## Campaign objectives

```json
{
  "completion_conditions": [
    "The vault attempt has ended outside its defences with Myr, the survey plate and the crew instruments each recovered or explicitly recorded as lost or left at a known position. A failed recovery must include the actual escape, not only a decision to withdraw.",
    "Closing event: at Eda's camp, Eda and available Kellin hear the extraction or failed-recovery report and respond to Myr's condition, the plate and the instruments; name the actual carriers or unresolved loss before the return journey."
  ],
  "description": "Reach the pledged relic and free the surviving survey crew from its old defences.",
  "id": "objective-1",
  "title": "The sealed survey vault"
}
```

```json
{
  "completion_conditions": [
    "The returning party has passed Rusk's causeway challenge by force, agreement or the cart track, or a failed return has ended with an actual retreat and loss report reaching Ysra. Establish Corren's overdue debt from Eda's letter and Rusk's second receipt or corroborated claim, not an invented confession.",
    "Ysra receives the recovered plate or honest loss report. Record its current custody, Eda's adjusted claim, Rusk's claim, Myr's outcome and any remaining repayment owner; agreement need not make every claimant happy.",
    "Closing event: Ysra records the Lodge's response and the earned recovery payment, and Tavin states the chosen response to Corren and the mentorship. Forgiveness, another relic hunt or recruitment into the Door mystery is not required."
  ],
  "description": "Get the recovered people and relic past a rival claim, then settle Tavin’s responsibility.",
  "id": "objective-2",
  "title": "The iron road"
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
        "All required act objectives have reached their authored endings. An early departure can end play honestly, but does not count as completion of unplayed acts.",
        "The returning party has passed Rusk's causeway challenge by force, agreement or the cart track, or a failed return has ended with an actual retreat and loss report reaching Ysra. Establish Corren's overdue debt from Eda's letter and Rusk's second receipt or corroborated claim, not an invented confession.",
        "Ysra receives the recovered plate or honest loss report. Record its current custody, Eda's adjusted claim, Rusk's claim, Myr's outcome and any remaining repayment owner; agreement need not make every claimant happy.",
        "Closing event: Ysra records the Lodge's response and the earned recovery payment, and Tavin states the chosen response to Corren and the mentorship. Forgiveness, another relic hunt or recruitment into the Door mystery is not required."
      ],
      "id": "adventure-settled",
      "immediate_aftermath": [
        "Closing event: Ysra records the Lodge's response and the earned recovery payment, and Tavin states the chosen response to Corren and the mentorship. Forgiveness, another relic hunt or recruitment into the Door mystery is not required."
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

Baseline: one level-1 Player Character with no Companions. A lower-level or badly depleted solo character should face one active enemy at a time and the reduced HP/damage option stated for the encounter; prepare that adjustment before initiative. For a small adventuring party, add at most one ordinary foe to a fight before it starts, or use an appropriate fully supported profile after inspecting the actual party. Larger parties need deliberate rebalancing. Character compatibility does not enforce level or establish balance. Allied workers supply information, ropes and agreed off-screen work; they never receive free player-character combat turns. Tavin Bell’s accepted Character File owns their actual equipment and class resources.

Use the authored sites, opposition, clues and consequences. Core clues have alternate routes; opening a door or making a routine observation never chooses the character’s next action. Later acts inherit actual resources, wounds, allies and evidence. Keep stakes local to this expedition, relic, crew or route: the Anchor Door mystery and world-scale climax belong to The Rift Awakening.

## Completion and aftermath

All required act objectives have reached their authored endings. An early departure can end play honestly, but does not count as completion of unplayed acts.

The returning party has passed Rusk's causeway challenge by force, agreement or the cart track, or a failed return has ended with an actual retreat and loss report reaching Ysra. Establish Corren's overdue debt from Eda's letter and Rusk's second receipt or corroborated claim, not an invented confession.

Ysra receives the recovered plate or honest loss report. Record its current custody, Eda's adjusted claim, Rusk's claim, Myr's outcome and any remaining repayment owner; agreement need not make every claimant happy.

Closing event: Ysra records the Lodge's response and the earned recovery payment, and Tavin states the chosen response to Corren and the mentorship. Forgiveness, another relic hunt or recruitment into the Door mystery is not required.

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
  "creatures": [
    {
      "id": "warden-armor",
      "path": "content/monsters/warden-armor.md"
    }
  ],
  "factions": [
    {
      "id": "warden-lodge",
      "path": "factions/warden-lodge.md"
    },
    {
      "id": "hollows-crews",
      "path": "factions/hollows-crews.md"
    }
  ],
  "items": [],
  "locations": [
    {
      "id": "waystation",
      "path": "locations/waystation.md"
    },
    {
      "id": "the-hollows",
      "path": "locations/the-hollows.md"
    }
  ],
  "lore": [],
  "npcs": []
}
```
