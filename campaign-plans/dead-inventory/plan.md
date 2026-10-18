# Dead Inventory

> GM preparation: contains adventure spoilers.

## Premise

A recovered suit of Warden armour wakes during an Athenaeum stocktake. Get the porters out before the storeroom becomes wreckage.

Tone: Contained chaos and salvage

One close-quarters encounter with rescue and damage-control choices.

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

Establish the storeroom doors, trapped workers and moving armour. Give the player a chance to act before resolving its first attack.

## Starting facts and state

```json
{
  "entities": [],
  "facts": [
    {
      "fact": "The party stands at an Athenaeum storeroom entrance. An empty suit of armour has begun walking between crates; two porters shelter behind separate racks.",
      "id": "opening-1",
      "known_by": [
        "player"
      ]
    },
    {
      "fact": "A brass inventory plate hangs crookedly from the armour. Its movement blocks the main aisle. A service door is reachable along the left wall.",
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
  "fact": "The crooked brass fitting activates a retrieval routine keyed to the matching tags. Removing it safely ends the movement.",
  "id": "inventory-fitting",
  "known_by": [
    "old-inventory-record"
  ],
  "reveal": {
    "condition": "Observe the suit following tags and inspect its clamp, or read the fitting diagram in Iven’s inventory folio.",
    "from_act": "act-1"
  }
}
```

## Close the inventory

Extract the porters and end or contain the armour’s activity.

Character entry and scope. Recommended level 1–3; one player with zero to 2 optional companions. This is guidance, not an enforced level limit. The chosen character can accept the immediate invitation, negotiate terms or decline. Establish any needed local context openly without inventing prior player actions.

Iven Pell, storekeeper: responsible for everyone present, angry at his own haste. He can name the service exit and explain that the plate was transferred from a crate. He offers 15 gp for clearing the danger and will not demand that a person risk death for an artifact.

Porters Fara and Doss: Fara can crawl under the racks; Doss has a pinned boot and needs help or time to unlace it. They react to instructions and opportunities without becoming extra attack turns.

Layout. A thirty-foot aisle has two heavy racks on each side, each with room to squeeze behind. The suit stands midway down it. The left-wall service door opens into an empty stone corridor with a sound iron gate; it can contain the armour if lured through and closed from outside. Fragile crate stacks obstruct the right wall.

Behaviour and evidence. The armour faces the jangling inventory tags, not the loudest voice. When a tag is dropped, it turns toward the sound. Iven explains that the armour was recovered beside matching brass plates. A careful look shows the crooked plate’s inner clamp. Removing it may stop the suit; touching an active opponent is risky, and a player should hear that risk before trying.

Rescue. A direct path to Fara can clear her in one good opening. Doss can be freed by lifting the rack with leverage, sacrificing his boot or taking longer to unlace it. Breaking crates makes space but destroys specimens. Record actual losses rather than charging a vague failure penalty.

Containment. Toss a string of tags into the service corridor, lead the suit after them and close the gate. It stops trying to leave when the tags are secured out of reach. If the plate is safely removed, it becomes dormant. Destruction is also a valid way to protect the workers. The plate is a local control fitting, not a revelation about the Doors.

### Objectives

objective-1

### Approaches and branches

Rescue first, then leave the armour behind a closed gate.

Disable the fitting after observing the behaviour, accepting the risk of approaching it.

Fight directly and use the racks for cover while making room for the porters.

### Consequences

Saving both porters earns the agreed fee even if stock is lost. Iven gives an honest incident report and accepts his share of responsibility.

An intact suit can be studied later; this adventure ends when the hazard is contained. A destroyed suit leaves useful salvage rather than a replacement boss.

All linked objective conditions are required, including the named closing event. Carry optional promises forward; they neither replace nor extend this ending.

### Prepared encounters

One weakened Warden Armour assembly: solo level-one baseline AC 14, HP 12, speed 20 ft, initiative +0, slam +3 to hit for 1d4+1 bludgeoning; one attack. It has no flight, reaction attacks, regeneration or unlisted immunities. State that this damaged salvage assembly differs from the full Setting creature. It follows tags, attacks an obstruction and stops to reacquire a sound instead of targeting a downed porter. Use declared enemy and adjudicated attack tools. A larger party may face a sturdier single assembly chosen before combat, with the same containment solution.

### Encounter adjudication

Read the selected party before committing opposition. The written encounter is a suggested baseline, not automatic level scaling. Resolve checks, attacks, damage, resources and combat turns through the admitted D&D SRD Ruleset; narrate only committed results. Preserve negotiation, surrender, retreat and established positions.

### Setting sources

locations/nexus.md

content/monsters/warden-armor.md

## Campaign objectives

```json
{
  "completion_conditions": [
    "Fara and Doss have left the dangerous aisle, or their observed injuries, losses or last known positions are recorded after evacuation. Do not declare an unseen porter safe.",
    "The armour is dormant after plate removal, destroyed, contained behind the service corridor gate, or isolated in the evacuated store by barred exits. An active suit with open access to people remains an unfinished hazard.",
    "Closing event: outside the hazard Iven receives the headcount and surviving-stock account, gives his incident response and settles the payment earned. The player may accept thanks publicly or leave quietly; studying the suit later is optional."
  ],
  "description": "Extract the porters and end or contain the armour’s activity.",
  "id": "objective-1",
  "title": "Close the inventory"
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
        "Fara and Doss have left the dangerous aisle, or their observed injuries, losses or last known positions are recorded after evacuation. Do not declare an unseen porter safe.",
        "The armour is dormant after plate removal, destroyed, contained behind the service corridor gate, or isolated in the evacuated store by barred exits. An active suit with open access to people remains an unfinished hazard.",
        "Closing event: outside the hazard Iven receives the headcount and surviving-stock account, gives his incident response and settles the payment earned. The player may accept thanks publicly or leave quietly; studying the suit later is optional."
      ],
      "id": "adventure-settled",
      "immediate_aftermath": [
        "Closing event: outside the hazard Iven receives the headcount and surviving-stock account, gives his incident response and settles the payment earned. The player may accept thanks publicly or leave quietly; studying the suit later is optional."
      ],
      "open_world": true,
      "outcome": "This adventure has reached its authored closing event with the actual successes or losses acknowledged. Its earned result and continuing consequences remain true."
    }
  ]
}
```

## GM guidance

Use this Plan as private preparation within Severed Lands. Ground play in the accepted party and starting facts. The named new people and incidents belong to this adventure; existing Setting facts and earned consequences remain authoritative. Give the player concrete information for a first decision, follow their chosen approach, and keep private answers concealed until discovered.

Make the named main problem and its leads accessible. Read the current act and all linked objective conditions before framing its main quest and reassess when a required result occurs. All listed conditions and the closing event are required; explicit alternatives satisfy only their own condition. Optional scenes can be deferred without adding gates. A completed side job does not replace the main story. Do not fabricate success or mark unplayed later acts complete after an early departure.

Objectives express opportunities, not mandatory conduct. A refusal, changed allegiance or early discovery can change the route or end the job. Carry forward established consequences instead of restoring a preferred plot.

Read this preparation and its relevant Setting sources. Keep a concise record of named promises, discovered evidence, lost resources and NPC attitudes. The preparation contains the core answer and resolutions; incidental dialogue and connective description remain the GM’s work.

Conclude after the bounded problem and immediate aftermath are resolved. Offer a short epilogue choice; begin a further adventure only if the player asks to continue.

## Completion and aftermath

Fara and Doss have left the dangerous aisle, or their observed injuries, losses or last known positions are recorded after evacuation. Do not declare an unseen porter safe.

The armour is dormant after plate removal, destroyed, contained behind the service corridor gate, or isolated in the evacuated store by barred exits. An active suit with open access to people remains an unfinished hazard.

Closing event: outside the hazard Iven receives the headcount and surviving-stock account, gives his incident response and settles the payment earned. The player may accept thanks publicly or leave quietly; studying the suit later is optional.

## Failure and withdrawal

Use the explicit failed-attempt outcome and closing report in the current objective. An intention to withdraw, an unrelated side task or delegation alone does not satisfy it. An early departure leaves unplayed later objectives unfinished.

Resolve defeat, unconsciousness, death and recovery under the selected Ruleset and agreed boundaries; no scripted rescue cancels committed results.

## Optional continuation

Only after resolving this adventure, ask whether the player wants to pursue a surviving relationship or optional lead. The ending does not depend on accepting.

## Side opportunities

## Mechanics requiring GM adjudication

The Plan does not implement new engine procedures. Its fictional deadlines, NPC attitudes, narrative projects and noncombat outcomes are GM adjudication; use existing Ruleset tools for encoded uncertainty and committed mechanical results.

Tag recognition, clamp removal and containment are authored GM adjudications. The routine is not an engine AI controller.

## Referenced Setting files

```json
{
  "creatures": [
    {
      "id": "warden-armor",
      "path": "content/monsters/warden-armor.md"
    }
  ],
  "factions": [],
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
