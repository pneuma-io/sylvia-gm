# The Bell Below

> GM preparation: contains adventure spoilers.

## Premise

A scavengers’ warning bell has vanished into the tunnels beneath the Hollows. Descend through a flooded Warden workhouse, fight its hungry inhabitants and haul the bell back to daylight.

Tone: A small treasure-and-rescue dungeon with light, water and a difficult haul.

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
  "id": "the-hollows",
  "path": "locations/the-hollows.md"
}
```

## Opening direction

Start beside a collapsed workhouse stair in the Hollows. Crew lookout Essin points to the missing warning bell’s snapped hanging rope and offers a paid recovery. Establish the accessible stair and dark water below without sending the character down.

## Starting facts and state

```json
{
  "entities": [],
  "facts": [
    {
      "fact": "A bronze warning bell fell into an old Warden workhouse when a salvage platform collapsed. Crews need it to signal the district’s phasing warnings.",
      "id": "opening-1",
      "known_by": [
        "player"
      ]
    },
    {
      "fact": "Essin offers 25 gp for recovery and lends rope, a crowbar and a shuttered lamp. The bell is a useful local tool, not an Anchor Door component.",
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
  "fact": "Tunnel Wretches moved the intact bell to their cooking alcove; Del survives on the workshop ledge.",
  "id": "bell-in-alcove",
  "known_by": [
    "wretches",
    "del"
  ],
  "reveal": {
    "condition": "Follow the drag marks, hear Del calling from the workshop, or learn the route from a wretch.",
    "from_act": "act-1"
  }
}
```

## Bring up the bell

Explore the workhouse, recover its warning bell and get the load through a contested exit.

Local truth. Tunnel Wretches dragged the fallen bell from the drainage room to their feeding alcove because the metal carries cooking warmth. They know its sound warns the surface crews but want fresh provisions for its return. A territorial immature Rift Darkmantle roosts above the flooded workshop. Essin’s partner Del is trapped, awake and exhausted, on a dry ledge beyond that workshop. Del can show the hauling route and explicitly does not become a free combat companion.

Five keyed areas. A rubble stair leads to B drainage room, ankle-deep water and the end of the bell’s drag marks. B connects to C workshop by a low arch, and E hoist shaft by a barred service door. C connects to D feeding alcove and the dry rescue ledge. D returns to E through a short crawlway. E’s hoist leads to the surface. The bell is sixty pounds with sound handles: use actual carrying limits, a willing helper or the hoist rather than assigning an arbitrary speed penalty. A lamp, darkvision or a supported light spell matters to what can be seen; darkness is not removed just because the GM describes the room.

Flooded workshop. A visible cable runs beneath the surface. Wisdom (Perception) DC 12 detects the darkmantle’s breathing ripple before entry; failure permits normal surprise adjudication, not free damage. Probing with a pole reveals a six-foot sump. Crossing its narrow dry rim requires Dexterity (Acrobatics) DC 12 unless a line is rigged. Failure plunges the character into the sump; it takes an action and Strength (Athletics) DC 10 to climb the slippery wall, or a companion’s rope can make the climb secure. There is no instant drowning: use actual movement and breath rules, identify the reachable ledge and keep held gear accounted for.

Hoist trap and bypass. The service door’s old safety catch is visibly bent. Tools DC 12 or a crowbar with Strength DC 13 opens it; forcing it releases a loose drum, Dexterity save DC 12 for 1d6 bludgeoning on failure or no damage on success. Blocking the drum before opening avoids the save. Del knows a crawlway around the door. A successful Investigation DC 12 identifies the sound hoist pawl; a failed hurried test jams it, costing a meaningful repair action with tools DC 10 or a replacement pin from B. No repair roll consumes a whole hour unless that cost was stated first.

Wretches and reward. The alcove has two inhabitants and clear retreat routes. They trade the bell and safe passage for two carried rations and a promise to leave their cooking hearth intact; the rations must actually exist and be spent. Threats can provoke the second fight, while a supported distraction can let the bell be moved without their consent. Searching abandoned shelves in E finds 10 gp of loose workshop coins. The entry shelter is secure for a short rest after B is cleared; doing so permits the wretches to drag the bell to D, a known location, not make it irretrievable. Finish with Del’s rescue or known fate, the bell returned or abandoned and Essin’s payment.

### Objectives

objective-1

### Approaches and branches

Trade supplies, use the connected crawlway or fight for access; the bell can be won without exterminating the tunnels.

Rescue Del first to gain hauling knowledge, or retrieve the bell and return for the ledge if the route remains available.

### Consequences

Carry real light, ropes, rations, injuries and the bell’s weight through the haul.

All linked objective conditions are required, including the named closing event. Carry optional promises forward; they neither replace nor extend this ending.

### Prepared encounters

Fight 1, workshop predator. Immature Rift Darkmantle (monstrosity): AC 11, HP 13, speed 10 ft, initiative +1. One melee attack, +3 to hit, reach 5 ft, one target, 1d6+1 bludgeoning. STR +1, DEX +1, CON +1, INT +0, WIS +0, CHA +0 for other checks/saves; no added proficiency.  It can fly 20 ft when the actor’s admitted movement supports it, otherwise it clings to reachable beams and drops by ordinary movement. Its weakening Veil nodules shed visible gloom but supply no unimplemented Darkness Aura, suffocation or automatic grapple. It flees through a ceiling crack at 4 HP. Solo level 1: HP 8 and 1d4+1 damage.

Fight 2, two wretches guard D if talks or stealth fail. Tunnel Wretch (humanoid): AC 12, HP 5, speed 30 ft, initiative +1. One melee attack, +4 to hit, reach 5 ft, one target, 1d4+2 piercing. STR +1, DEX +1, CON +1, INT +0, WIS +0, CHA +0 for other checks/saves; no added proficiency. Can instead use a sling at +4, range 30/120 ft, for 1d4+2 bludgeoning. This isolated specimen has no Pack Tactics; use ordinary light rules, with sunlight disadvantage only when the supported rule is explicitly applied. One attacks while the other uses its action to drag the bell or bar the crawlway; they do not both focus a lone level-1 character. Losing one or receiving food makes the other flee or bargain. Use both as combatants only for a healthy baseline character or a larger party, with that decision fixed before initiative.

### Encounter adjudication

Run declared ability checks, saving throws, initiative, attacks, damage, conditions, spells and rests through the admitted D&D Ruleset. Before an uncertain roll name its purpose, DC and consequence. Give plainly visible facts and essential leads without a roll; success can improve position, speed or discretion. A supported spell or sound equipment use can solve an obstacle outright when its actual text allows. Track the real HP, spell slots, ammunition, carried gear and recovery resources; never restore them between scenes by narration. These Plans do not install extra engine procedures.

### Setting sources

content/monsters/tunnel-wretch.md

content/monsters/rift-darkmantle.md

factions/hollows-crews.md

locations/the-hollows.md

## Campaign objectives

```json
{
  "completion_conditions": [
    "The party has left the workhouse with the warning bell, or withdrawn to the surface after abandoning recovery with the bell's last known position or observed loss recorded.",
    "Del is back on the surface or has a recorded observed fate or last known location after the ended rescue. Wretches need not be killed if trade, diversion or another method worked.",
    "Closing event: Essin receives the bell or failed-recovery report and Del's outcome, responds with the actual warning-system consequence, and settles the recovery payment. An ongoing haul or a promise to return does not count."
  ],
  "description": "Explore the workhouse, recover its warning bell and get the load through a contested exit.",
  "id": "objective-1",
  "title": "Bring up the bell"
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
        "The party has left the workhouse with the warning bell, or withdrawn to the surface after abandoning recovery with the bell's last known position or observed loss recorded.",
        "Del is back on the surface or has a recorded observed fate or last known location after the ended rescue. Wretches need not be killed if trade, diversion or another method worked.",
        "Closing event: Essin receives the bell or failed-recovery report and Del's outcome, responds with the actual warning-system consequence, and settles the recovery payment. An ongoing haul or a promise to return does not count."
      ],
      "id": "adventure-settled",
      "immediate_aftermath": [
        "Closing event: Essin receives the bell or failed-recovery report and Del's outcome, responds with the actual warning-system consequence, and settles the recovery payment. An ongoing haul or a promise to return does not count."
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

The party has left the workhouse with the warning bell, or withdrawn to the surface after abandoning recovery with the bell's last known position or observed loss recorded.

Del is back on the surface or has a recorded observed fate or last known location after the ended rescue. Wretches need not be killed if trade, diversion or another method worked.

Closing event: Essin receives the bell or failed-recovery report and Del's outcome, responds with the actual warning-system consequence, and settles the recovery payment. An ongoing haul or a promise to return does not count.

## Failure and withdrawal

Use the explicit failed-attempt outcome and closing report in the current objective. An intention to withdraw, an unrelated side task or delegation alone does not satisfy it. An early departure leaves unplayed later objectives unfinished.

Resolve defeat, unconsciousness, death and recovery under the selected Ruleset and agreed boundaries; no scripted rescue cancels committed results.

## Optional continuation

Further adventures require the player’s interest after this story has a complete ending. Surviving contacts and obligations can carry forward.

## Side opportunities

## Mechanics requiring GM adjudication

Site phasing, enemy motives, alarms and engineering objectives are explicitly described GM adjudications. Checks, saves, damage, spells, rest and admitted combat use the D&D Ruleset. No extra automatic hazard, pursuit, faction, economy or crafting subsystem is installed.

Listed creature profiles are weaker or specialised Setting adaptations with declared statistics. They do not assert support for unencoded powers from the original monster entries.

Gold and salvage become awarded possessions only when recorded through the applicable inventory/state path. SRD Lite has a reduced spell list (117 supported spells) and no consumables; consumables belong to future SRD Full. A narrative reward never installs a magic-item feature, restores a slot or applies healing.

## Referenced Setting files

```json
{
  "creatures": [
    {
      "id": "tunnel-wretch",
      "path": "content/monsters/tunnel-wretch.md"
    },
    {
      "id": "rift-darkmantle",
      "path": "content/monsters/rift-darkmantle.md"
    }
  ],
  "factions": [
    {
      "id": "hollows-crews",
      "path": "factions/hollows-crews.md"
    }
  ],
  "items": [],
  "locations": [
    {
      "id": "the-hollows",
      "path": "locations/the-hollows.md"
    }
  ],
  "lore": [],
  "npcs": []
}
```
