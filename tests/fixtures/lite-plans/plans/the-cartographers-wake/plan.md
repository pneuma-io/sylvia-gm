# The Cartographer’s Wake

> GM preparation: contains adventure spoilers.

## Premise

A stolen chart at a Nexus wake points to a sealed survey vault. Track the thieves, brave the old works and recover the mapmaker’s last expedition record.

Tone: A brisk investigation leading into a dangerous Warden ruin.

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
  "id": "nexus",
  "path": "locations/nexus.md"
}
```

## Opening direction

Open at Seren Dall’s wake in a Nexus bookshop. Ula has discovered an empty map roller and a broken cellar latch. Explain the theft and invitation to investigate without deciding the character accepted it.

## Starting facts and state

```json
{
  "entities": [],
  "facts": [
    {
      "fact": "A wake for cartographer Seren Dall is underway in Ula’s Nexus bookshop. His final chart has vanished from its display roller.",
      "id": "opening-1",
      "known_by": [
        "player"
      ]
    },
    {
      "fact": "A porter saw two intruders leave through the cellar toward an abandoned survey annex. Ula offers 25 gp for the chart and the field case it locates; the party has not yet entered the cellar.",
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
  "fact": "Hadrik hired the collectors to steal the original; Pera made a protective tracing earlier but is not the thief.",
  "id": "stolen-route",
  "known_by": [
    "hadrik",
    "collectors",
    "pera"
  ],
  "reveal": {
    "condition": "Compare Ula’s receipt, the tracing and a collector’s written instructions, or obtain supported testimony.",
    "from_act": "act-1"
  }
}
```

## The last route

Trace the stolen chart into the survey annex and resolve custody of the recovered field record.

Cast and truth. Ula wants Seren’s bequest honoured. Former assistant Pera copied the chart to protect a refuge named on it, but did not steal the original. A local buyer, Hadrik, hired two opportunists to take the original before the bequest could restrict its sale. Davren the scholar wants credit; refuge representative Nalin wants consultation. Hadrik’s crew is using the chart to reach Seren’s field case in a small abandoned Warden survey annex beneath the bookshop quarter. Neither map nor case contains the Rift Awakening’s Door secrets.

Investigation with useful outcomes. The broken cellar latch, muddy blue chalk and porter’s description all identify the annex route without a roll. An Intelligence (Investigation) DC 12 check matches a scrap to Pera’s harmless tracing, separating her activity from the theft; on failure Ula’s dated receipt supplies that distinction after a conversation. A Wisdom (Survival) DC 12 trail check locates a dry maintenance crawlway and avoids the guarded landing. Otherwise the clear main stair still reaches the annex. Persuasion DC 12 persuades the frightened porter to sketch the sentry’s position; failure leaves the position unknown, not the route blocked.

Site and connections. A: the cellar stair descends to B, a 20-foot-square landing, with a side crawlway to C. B’s guarded arch reaches C, a disused instrument hall, and a barred door to D, the keeper’s office. C and D both connect to E, the survey vault. All passages are ordinary navigable space; give a clear sketch or descriptions before the player chooses a route. The landing has waist-high crates offering half cover. The crawlway is cramped but carries no automatic damage or exhaustion.

Instrument hall trap. Copper tripwire crosses the clearly scuffed plinth. Searching the plinth reveals the wire; Investigation DC 12 identifies the counterweight and a wedge point. Disable with thieves’ tools and a Dexterity check DC 12, wedge it with a ten-foot pole from safety, or go through D. A failed disable attempt snaps the wire: Dexterity save DC 12, 1d6 bludgeoning on failure, no damage on success, then the trap is spent. The keeper’s door opens with the sentry’s key, tools DC 12, or Strength DC 14; forcing it warns the vault occupant. The office is defensible for a short rest if entrances are secured; a rest gives Hadrik’s surviving hireling time to negotiate or carry the loose chart toward the known exit, never vanish off-map.

Vault decision and loot. The original chart lies on a reading stand and the field case is locked beneath it. A repeated three-notch symbol on the map and stand identifies its release; careful examination suffices, or a tools check DC 12 opens it noisily. Taking the case without disengaging the guardian causes the prepared second encounter. The guardian has a physical shutoff behind the far pillar; reach it and use an action with the office key, or use tools DC 12 while exposed. Failure costs the action. No roll erases the guardian from initiative before the mechanism is actually used. The case contains route notes, Nalin’s consultation letter and 15 gp worth of legitimate survey fittings. Ula’s 25 gp fee is separate. Distribute the chart according to the actual bequest and choices, then close with the wake’s unfinished toast.

### Objectives

objective-1

### Approaches and branches

Use the crawlway, negotiate with the collector, steal back the chart, or fight through the landing.

Decode or shut down the guardian; supported magic and tools retain their actual costs and effects.

### Consequences

Keep evidence of Hadrik’s involvement and promises to Nalin distinct from the mechanical victory.

All linked objective conditions are required, including the named closing event. Carry optional promises forward; they neither replace nor extend this ending.

### Prepared encounters

Fight 1, hired collector on B. Rift-raider outrider (humanoid): AC 12, HP 10, speed 30 ft, initiative +1. One melee attack, +3 to hit, reach 5 ft, one target, 1d6+1 slashing. STR +1, DEX +1, CON +1, INT +0, WIS +0, CHA +0 for other checks/saves; no added proficiency. Can instead shoot a shortbow at +3, range 80/320 ft, for 1d6+1 piercing. This lightly armed scout has no innate spells or psychic damage. A lone second hireling in E flees with the chart if warned, but does not join the first fight for the solo baseline. The collector blocks the vault route, accepts 5 gp or credible exposure of the theft, and yields at 3 HP. Pursuit uses movement and declared checks, never automatic escape. Solo level 1: HP 7 and club damage 1d4+1.

Fight 2, damaged vault guardian. Damaged Warden Armor (construct): AC 14, HP 14, speed 20 ft, initiative +0. One melee attack, +3 to hit, reach 5 ft, one target, 1d6+1 bludgeoning. STR +1, DEX +1, CON +1, INT +0, WIS +0, CHA +0 for other checks/saves; no added proficiency. Its exposed joints and spent enchantments remove the full creature’s immunities and multiattack. It guards the field case and does not pursue beyond C. Its pillar creates a route to the shutoff and cover against the hireling if still present. It is inactive until the case is disturbed without disengagement. Solo level 1: HP 10 and slam 1d4+1. An escaped collector does not become an extra surprise wave.

### Encounter adjudication

Run declared ability checks, saving throws, initiative, attacks, damage, conditions, spells and rests through the admitted D&D Ruleset. Before an uncertain roll name its purpose, DC and consequence. Give plainly visible facts and essential leads without a roll; success can improve position, speed or discretion. A supported spell or sound equipment use can solve an obstacle outright when its actual text allows. Track the real HP, spell slots, ammunition, carried gear and recovery resources; never restore them between scenes by narration. These Plans do not install extra engine procedures.

### Setting sources

content/monsters/rift-raider.md

content/monsters/warden-armor.md

factions/caravaneers-guild.md

locations/nexus.md

## Campaign objectives

```json
{
  "completion_conditions": [
    "The survey-annex recovery has ended with the party outside it and the original chart and field case each recovered into named custody or reported lost or left at a known position. Establish Hadrik's theft separately from Pera's protective copy through the prepared evidence.",
    "The recovered chart and case, if any, are assigned an actual recipient under the bequest or the player's stated contrary choice; record the response to Nalin's consultation claim without requiring everyone to agree.",
    "Closing event: back at the wake Ula receives the recovery or loss account, settles the payment earned and offers Seren's unfinished toast; the player participates or explicitly declines it. Another recovery attempt is optional."
  ],
  "description": "Trace the stolen chart into the survey annex and resolve custody of the recovered field record.",
  "id": "objective-1",
  "title": "The last route"
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
        "The survey-annex recovery has ended with the party outside it and the original chart and field case each recovered into named custody or reported lost or left at a known position. Establish Hadrik's theft separately from Pera's protective copy through the prepared evidence.",
        "The recovered chart and case, if any, are assigned an actual recipient under the bequest or the player's stated contrary choice; record the response to Nalin's consultation claim without requiring everyone to agree.",
        "Closing event: back at the wake Ula receives the recovery or loss account, settles the payment earned and offers Seren's unfinished toast; the player participates or explicitly declines it. Another recovery attempt is optional."
      ],
      "id": "adventure-settled",
      "immediate_aftermath": [
        "Closing event: back at the wake Ula receives the recovery or loss account, settles the payment earned and offers Seren's unfinished toast; the player participates or explicitly declines it. Another recovery attempt is optional."
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

The survey-annex recovery has ended with the party outside it and the original chart and field case each recovered into named custody or reported lost or left at a known position. Establish Hadrik's theft separately from Pera's protective copy through the prepared evidence.

The recovered chart and case, if any, are assigned an actual recipient under the bequest or the player's stated contrary choice; record the response to Nalin's consultation claim without requiring everyone to agree.

Closing event: back at the wake Ula receives the recovery or loss account, settles the payment earned and offers Seren's unfinished toast; the player participates or explicitly declines it. Another recovery attempt is optional.

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
      "id": "rift-raider",
      "path": "content/monsters/rift-raider.md"
    },
    {
      "id": "warden-armor",
      "path": "content/monsters/warden-armor.md"
    }
  ],
  "factions": [
    {
      "id": "caravaneers-guild",
      "path": "factions/caravaneers-guild.md"
    }
  ],
  "items": [],
  "locations": [
    {
      "id": "nexus",
      "path": "locations/nexus.md"
    }
  ],
  "lore": [],
  "npcs": []
}
```
