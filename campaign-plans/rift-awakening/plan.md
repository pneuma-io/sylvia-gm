# Rift Awakening

> GM preparation: contains adventure spoilers.

## Premise

Wake beside strangers beneath an unfamiliar sky. Find shelter in Thornwall, defend the people who take you in, and follow a growing crisis across the Severed Lands to the city of Nexus. Explore six substantial chapters of dangerous places, local stories and purposeful combat, building a life before deciding whether to stay or seek a way home.

Tone: Wonder, companionship and hard-won belonging; dangerous combat and an expanding existential mystery, with ordinary humor and warmth between crises.

A flagship isekai campaign from levels 1–12. Each act is a substantial regional or city chapter with a main conflict, several connected adventures, optional side stories and time for relationships. The player can revisit places, pursue fresh local quests and use creative approaches. The main story changes the world without treating side content or combat grinding as a leveling requirement.

## Character entry

```json
{
  "custom_companions": {
    "maximum": 5,
    "minimum": 1
  },
  "player_character": {
    "mode": "compatible"
  },
  "required_companions": []
}
```

## Boundaries

Apply the player's selected content boundaries. Starting Companions are adults with their own choices; friendship and romance are optional and never compulsory rewards.

The concealed drug premise is fantasy cosmology, not medical advice or a claim about real LSD effects. Provide no dose, procurement or use instructions. The fiction does not make this world or its residents imaginary.

Depict threatened families and loss with the agreed restraint. Permanent local death has real consequences; do not present suicide or casual self-destruction as a desirable shortcut to the ending.

## Starting location

```json
{
  "id": "verdant-margin",
  "path": "locations/verdant-margin.md"
}
```

## Opening direction

Start with the PC waking in the clearing beside the selected Companions. Offer immediate sensory detail, the sign to Thornwall and space to speak or look around. Stop for the player's choices before taking the road. Do not open with an attack, a narrated trip to town, an NPC exposition lecture or the hidden crossing explanation. Once the party travels toward Thornwall, run the Rift Wolf introduction, then the inn, practical orientation and first rest before the town's main quest.

## Starting facts and state

```json
{
  "entities": [],
  "facts": [
    {
      "fact": "The PC and selected Companions have just awakened together in a woodland clearing on the Verdant Margin's road to Thornwall. None remembers how they arrived. Their identities, relationships and starting gear remain those actually selected in setup; no unchosen shared history is implied.",
      "id": "ra-opening",
      "known_by": [
        "player"
      ]
    },
    {
      "fact": "A weathered sign at the clearing's edge points toward Thornwall. The road is visible. The party has not yet followed it, fought the wolves, reached the gate or met the innkeeper.",
      "id": "ra-sign",
      "known_by": [
        "player"
      ]
    },
    {
      "fact": "A small pack of agitated Rift Wolves hunts the road toward Thornwall. They are local animals affected by the same disturbances that are straining the mill.",
      "id": "ra-wolves",
      "known_by": []
    },
    {
      "fact": "Hadran Vale ordered three mill conductors removed and sold through Fen Marr. The mill is failing; Nell and Seli are trying to protect its workers. V and Edda have not met this party.",
      "id": "ra-mill-private",
      "known_by": [
        "hadran-vale",
        "fen-marr"
      ]
    }
  ],
  "mechanics": [
    {
      "key": "state",
      "namespace": "sylvia.srd-lite.bg3",
      "value": {
        "advancement": {
          "allocation": "The Player Character and active Companions share each earned target level; it is not split by kills, damage, attendance at a single conversation or who dealt the last blow. Use stable milestone IDs and never award a repeated event twice. Replacement/joining party members should be authored at the current party level through supported character setup, not given invented past awards. No combat XP or side-quest XP. Gold, appropriate equipment and local relationships reward optional play. The level cap remains 12 during optional continuation unless this Plan is explicitly revised.",
          "maximum_level": 12,
          "method": "milestone",
          "milestones": [
            {
              "alternative_outcomes": "Combat, an earned escape or another resolved encounter all qualify once refuge is reached; choosing a road or starting a fight does not.",
              "id": "ra-01-road-refuge",
              "target_level": 2,
              "trigger": "The introductory road encounter has ended and the surviving party actually reaches Thornwall refuge."
            },
            {
              "alternative_outcomes": "An honestly failed rescue qualifies with known fates and recovered evidence; missing both the workers and the evidence does not.",
              "id": "ra-02-underroot-evidence",
              "target_level": 3,
              "trigger": "The Underroot expedition ends with its workers accounted for and the three-conductor theft established through the prepared evidence."
            },
            {
              "alternative_outcomes": "The completed evacuation and reduced refugee meal qualify; an intention to repair or evacuate does not.",
              "id": "ra-03-thornwall-meal",
              "target_level": 4,
              "trigger": "The mill is protected or the authored evacuation completed, the accusation receives its disposition, and Vesper's closing community meal occurs."
            },
            {
              "alternative_outcomes": "A failed rescue with documented losses and contained wreckage qualifies; abandoning an active test without that result does not.",
              "id": "ra-04-glasshouse-rescue",
              "target_level": 5,
              "trigger": "The Glasshouse test ends, volunteers' fates are known, its bypass is isolated, and survivors or the loss report reach Waystation with the sponsorship/pulse evidence."
            },
            {
              "alternative_outcomes": "A damaged Door and displaced refuge qualify once the named aftermath is performed; a speech or proposed committee alone does not.",
              "id": "ra-05-waystation-custody",
              "target_level": 6,
              "trigger": "The Door emergency ends with protection or completed evacuation, and the assembly actually transfers research custody and releases the Cairnfields records."
            },
            {
              "alternative_outcomes": "The prepared rubbing-copy route qualifies; reaching the Spire without usable interpreted evidence does not.",
              "id": "ra-06-wind-atlas",
              "target_level": 7,
              "trigger": "The Wind Archive expedition ends with the original atlas or authenticated rubbings obtained and its network/Closed Vigil/Hollows-Nexus links established."
            },
            {
              "alternative_outcomes": "Completed evacuation after failed repair qualifies; a promised treaty or unperformed evacuation does not.",
              "id": "ra-07-basin-assembly",
              "target_level": 8,
              "trigger": "The basin is restored or its refuge evacuated, and the assembly records the actual water/route outcome and verified Hollows lead."
            },
            {
              "alternative_outcomes": "Recorded rescue losses qualify; an uncorroborated accusation or an unexplored returned street does not.",
              "id": "ra-08-hearth-row-record",
              "target_level": 9,
              "trigger": "The Hearth Row rescue ends, residents' fates are established, and Kell receives the report and corroborated evidence of V's experiment."
            },
            {
              "alternative_outcomes": "The prepared Nexus archive route qualifies; withheld public disclosure can qualify if the chosen limited recipients actually receive it and the disposition is recorded.",
              "id": "ra-09-hollows-disclosure",
              "target_level": 10,
              "trigger": "The crossing and return truths and operating manual are established through the observatory or authenticated copies, and the camp meeting performs the chosen disclosure and records custody/accountability."
            },
            {
              "alternative_outcomes": "An actual evacuation with losses qualifies; defeating guards without resolving the civic crisis does not.",
              "id": "ra-10-nexus-relief",
              "target_level": 11,
              "trigger": "The civic crisis ends by isolation/protection or completed evacuation, the illegal bypass is established, and its operators are confronted or their death/escape recorded."
            },
            {
              "alternative_outcomes": "A limited coalition and a containment-only fallback qualify; promising future access or unspecified research does not.",
              "id": "ra-11-root-gate-commitment",
              "target_level": 12,
              "trigger": "The Root Gate approach is secured, its controls are checked against atlas/manual, and the coalition or surviving representatives commits an executable operation with assigned roles."
            }
          ],
          "recipients": "whole_party",
          "starting_level": 1,
          "timing": "Award each listed milestone once after its actual trigger. At the next safe pause, use the Ruleset's earned-advancement and player-confirmed level-up operations; resolve class, subclass, feat and spell choices with the player. Do not interrupt active combat or give an automatic rest. Act 6 begins at level 12 after the Root Gate milestone is applied."
        }
      }
    }
  ]
}
```

## Private truths and reveal conditions

```json
{
  "id": "ra-crossing",
  "reveal": {
    "condition": "Read the paired arrival records at the Hollows observatory, or obtain their authenticated Athenaeum copies with Cipher or a Warden Studies translator. Earlier coherent evidence or V's voluntary account can reveal it sooner.",
    "from_act": "act-1"
  },
  "setting_fact_id": "ra-crossing"
}
```

```json
{
  "id": "ra-return",
  "reveal": {
    "condition": "Compare the observatory's lifetime registers, departure records and translated instructions. V knows natural return and historic early return, but does not initially know how to operate a safe Door.",
    "from_act": "act-1"
  },
  "setting_fact_id": "ra-return"
}
```

```json
{
  "id": "ra-native-lives",
  "reveal": {
    "condition": "Local families establish where children belong from Act 1. The observatory's birth and departure registers establish the reason when investigated.",
    "from_act": "act-1"
  },
  "setting_fact_id": "ra-native-lives"
}
```

```json
{
  "id": "ra-rift",
  "reveal": {
    "condition": "The Wind Archive service atlas establishes the network and Closed Vigil; the observatory explains its original purpose. Isolated earlier diagrams reveal only the portion their text actually supports.",
    "from_act": "act-1"
  },
  "setting_fact_id": "ra-rift"
}
```

```json
{
  "id": "ra-awakening",
  "reveal": {
    "condition": "Compare mill pulse marks with the Glasshouse log and Wind Archive timings. The connected horizon makes the physical process visible even to people who lack its technical explanation.",
    "from_act": "act-1"
  },
  "setting_fact_id": "ra-awakening"
}
```

```json
{
  "id": "ra-v-guilt",
  "reveal": {
    "condition": "Recover the signed Hearth Row work order or hear survivor Iona's witnessed account supported by the instrument's dated record. V may confess earlier. Killing or losing V never removes the other evidence routes.",
    "from_act": "act-1"
  },
  "setting_fact_id": "ra-v-guilt"
}
```

```json
{
  "id": "ra-edda",
  "reveal": {
    "condition": "Glasshouse supply receipts name Edda. She admits her daughter's existence when speaking privately about why waiting is unbearable.",
    "from_act": "act-1"
  },
  "setting_fact_id": "ra-edda"
}
```

```json
{
  "id": "ra-final-procedure",
  "reveal": {
    "condition": "Combine the atlas and observatory instructions with the civic regulator labels under Nexus. Scholars or recovered operating diagrams can interpret them; no unique surviving NPC or side quest is required.",
    "from_act": "act-1"
  },
  "setting_fact_id": "ra-final-procedure"
}
```

## Act 1 — A Place at the Table

Levels 1–4. Wake together, find shelter in Thornwall, and become involved in a food crisis that powerful people would prefer to blame on newcomers.

Arrival. The PC and selected starting Companions wake in a clearing on the Verdant Margin's Thornwall road. They retain their selected identities and abilities but cannot remember arriving. Their starting equipment is whatever setup actually accepted. A weathered sign points to Thornwall; birds stop singing during a faint violet tremor. Let them talk, inspect the clearing and choose a direction before danger interrupts. Do not skip directly to a quest giver, invent a prior friendship or reveal the drug connection. Each Companion was transported independently unless their selected histories say otherwise.

The road and the inn. Rift Wolves intercept the party once it takes the road toward town, before the gate is reached. Their violet eyes and agitation are the first visible Rift effect, not evidence that the party caused it. Resolve the fight or earned escape, then allow entry to Thornwall. Old Vesper offers the Fortress Inn's spare beds and a simple meal for the first night without imposing a debt grind. She explains the town, Riftborn, Rift-touched wildlife, the distant Returners and where to find ordinary help. She has beliefs, not the secret cosmology. Let the party settle, sleep and spend time together before the morning's central problem.

A town with room to live. The market, Forge Quarter, Warrens, fortress walls and inn are available throughout the act. Vesper is blunt and hospitable without becoming the party's servant. Stern, a visiting Returner patrol veteran, notices the wolves' unusual behavior but first helps the town. Seli, a Riftborn mill hand, knows the works and has been accused of sabotage. Nell Arven, the miller's SL-born apprentice, wants her trapped coworkers found. Hadran Vale, a grain factor working for House Voss, secretly bought cheap grain and sold regulator parts to a broker; he now profits from shortages. He fears exposure, not the apocalypse, and can surrender his records to save himself. Do not equate his actions with a guilty verdict on all of House Voss.

The morning problem. Nell comes to the inn because Underroot Mill has stopped, roots are forcing their way into its works and two workers have not returned. Flour shortages already reach the market. She offers a 40 gp party fee from the mill's emergency fund for the rescue/investigation, including an honest failed rescue. The mill lies just outside Thornwall, so the town remains the hub. The rescue proceeds through occupied works, overgrown storerooms and the old regulator beneath them; determine ordinary connections from the party's approach rather than a compulsory room sequence. Root-touched animals defend nests around the workers, and hired guards are removing records from the lower works. Their employer paid them to conceal a sale, not to murder a town; a receipt or threatened arrest can turn them.

What was stolen. Three copper conductors were removed at Hadran's instruction. One lies where a worker dropped it inside the mill. Fen Marr, a Warrens broker, holds a second. The third went to Waystation in Edda's supply consignment. The regulator's empty sockets and dispatch record establish all three destinations. If the record is destroyed, Nell retains a labeled sketch and Seli kept a dispatch copy in her inn room; either worker identifies how to reach the other source. Stern or a Forge Quarter smith can interpret the clear regulator markings without a successful lore roll. Two conductors suffice to isolate the faulty mill circuit; recovering the third is not a gate forcing an early Act 2 trip.

The people behind the shortage. Fen trades the second conductor for 20 gp, the recovery of his stolen account book from Hadran's grain yard, or an enforceable promise of safe testimony. He may also surrender it to credible authority or lose it to theft. His book and the grain-yard tally independently link Hadran to both sale and hoarding. The yard's paid guards defend stores until defeated, persuaded to stand down or shown that they are being made scapegoats. Hadran offers compensation and supplies for silence; accepting it can solve the immediate food problem while leaving his power intact. The party may expose, bargain or steal, but the townspeople still need the mill secured or an evacuation.

Closing the crisis. When the party begins the mill isolation operation, a shared violet pulse drives larger root-touched beasts into the works. The fight protects workers carrying out the repair; stopping the beasts alone does not turn the regulator off. Recovered instructions let Nell, Seli, a smith or the party operate it. Sound physical work and supported magic can help; no special class, feat or sacrifice is required. If the protection fails, the prepared fallback is evacuation to Thornwall's fortress yard, moving mill workers and the occupied downstream homes before the final surge. Vesper and Stern provide helpers once warned. A failed attempt may destroy the mill and food stores; record actual losses and the relieved or angry people who survive.

A place at the table. Present the evidence to the council or to the market if officials refuse. Record Seli's accusation as withdrawn, upheld or still publicly contested, and what happens to Hadran and the hoarded grain. At Vesper's meal the workers acknowledge what the party actually achieved. The town pays an additional 60 gp for successful isolation, or 30 gp for the completed evacuation. A smith offers each party member one suitable ordinary weapon or tool from available stock, not a magic item. Stern's invitation identifies the Waystation consignment and missing volunteers; declining it leaves free local play available without falsely completing later acts. Spend time with the adopted home before leaving.

### Objectives

thornwall-refuge

underroot

thornwall-settled

### Approaches and branches

Work with the council, with Warrens residents, or independently; each has access to the evidence and repair routes.

A successful repair preserves livelihoods; evacuation saves people but leaves a refugee and food problem. Neither outcome requires forgiving Hadran.

A personal side quest may delay the investigation without an invented hourly countdown. State visible escalation before the player knowingly leaves an active crisis.

### Consequences

Record Seli's standing, Hadran's disposition, Fen's bargain, mill survival and named casualties. Town help in later acts follows these facts.

The mill pulse and Waystation dispatch are clues, not the full answer. Nobody at the inn can establish why the PC crossed.

### Prepared encounters

1A — Road to Thornwall, level 1: Rift Wolves (SRD Wolf). They attack between clearing and town. Start with one against PC plus one Companion; increase only after checking the actual party. Hungry animals break off when injured and driven away. Objective: survive and reach refuge, not kill every wolf.

1B — Underroot rescue, level 2: root-touched vermin and wolves (SRD Giant Rat/Wolf). They have trapped workers in the occupied mill. Objective: get the people out and reach the regulator. Food, distraction or careful handling can avoid some animals.

1C — Grain yard or lower mill, levels 2–3: Hadran's paid guards (Bandit, with a Scout foreman for a larger party). They remove records or defend seized grain. Objective: obtain the conductor/evidence and settle the confrontation; guards can surrender or testify.

1D — Mill isolation, level 3: a root-touched boar and lesser beasts (SRD Boar/Black Bear/Wolf as appropriate to party). The pulse brings them into the working area while people isolate the regulator or evacuate. Objective: keep the actual operation or escape possible. No waves appear after the party has won.

### Encounter adjudication

Use the opposition guide in this Plan's contained Setting for the named retained SRD profiles and party-size guidance. Place the combat when its stated event occurs; establish the immediate surroundings naturally. Resolve combat, checks, spells and recovery through the Ruleset. Negotiation, stealth, retreat and supported abilities can change an encounter's outcome; do not replace a bypassed fight with another mandatory fight.

### Setting sources

locations/verdant-margin.md

locations/thornwall.md

npcs/innkeep-vesper.md

npcs/patrol-veteran-stern.md

content/monsters/rift-awakening-opposition.md

## Act 2 — The People Who Wait

Levels 4–6. Join a refuge divided over the price of going home; rescue volunteers and decide who can be trusted with the Door beneath it.

On the way. Stern offers company and provisions for the journey, not compulsory patrol membership. A relief caravan is being robbed near the pass by armed scavengers selling goods to an experimental camp. Its cargo includes volunteer letters, ordinary food and the third mill conductor. Fighting off, outwitting or bargaining with the robbers reveals a Glasshouse delivery receipt. If bypassed entirely, the driver later reaches Waystation with the same receipt. The journey creates a change of setting; do not require invented mileage, water-cask crafting or repeated work shifts to advance it.

A refuge before a faction. Waystation offers Marla's communal kitchen, the signal tower, researchers, patrols and the dormant Door chamber. Let the party share meals, choose lodgings and discover why different people stay. Edda Rill is a practical organizer who knows everyone's needs and arranges work for newcomers. Oren Pell, an SL-born repairer, shares a home with a Riftborn partner; their children are his reason to fear reckless experiments. V is physically present for this chapter, courteous and useful but guarded. Stern wants volunteers brought back, Marla wants nobody punished for wanting home, and the quartermaster wants a verifiable chain of custody for dangerous parts.

The Glasshouse. Three volunteers have missed their promised return from a research camp in an abandoned pass-side conservatory. Edda admits funding research but calls the missing people a communications failure. The camp has a live bypass assembled with the Thornwall conductor. Glass walls and instruments are ordinary surroundings, not required combat tricks. Returning echo-creatures harry trapped volunteers while the operator Dain refuses to shut down the test because he believes its next pulse will prove passage possible. Reach the living quarters and controls through exploration, combat or negotiation. Dain will yield to direct evidence that the pulses injure volunteers, or flee when his guards abandon him. His notes remain even if he dies.

Evidence that travels. The supply receipt names Edda; the pulse log matches the Thornwall mill failure, including its first harmless-looking shiver. A duplicate log left with Waystation's quartermaster before departure gives the same relationship if the camp burns. Shutting the labeled bypass down releases its trapped space; if the attempt fails, exhaustion of its charge ends the test and leaves wreckage to isolate. Do not manufacture a successful rescue after a loss. Return the survivors or their documented fates to the refuge. The Returners pay 100 gp from the rescue fund for completing this work; medical supplies belong to the injured until legitimately released.

A returner's argument. Privately, Edda speaks of her daughter and the fear that accepting this world means abandoning her. She believes V knows a route and keeps postponing it. Show her the dated records and introduce a willing injured volunteer: she can support a supervised shutdown without renouncing her desire to leave. If she refuses, Dain or her surviving collaborators attempt a stronger bypass beneath Waystation. If she cooperates and Dain is contained, the already-installed unauthorized relay still destabilizes the Door; helping repair it is a positive consequence of winning Edda over, not a replacement villain appearing to nullify diplomacy.

The Door emergency. Warn the party through matching pulses and the operators' announced test before it becomes active. The cavern guardians awaken and the unauthorized circuit strains the settlement. The party can isolate the bypass using Glasshouse instructions and stop hostile operators or constructs. V can translate labels, but the quartermaster's complete duplicate log gives the same shutdown sequence if V refuses. If protection fails, Stern and Marla direct evacuation to the upper settlement while the party covers it; the Door may darken, but the refuge's survivors remain real. No local test opens the safe way home yet.

The assembly. After the emergency, survivors argue in the main hall. The party presents its result and helps assign actual research keys, logs and materials to V under oversight, Edda under oversight, an elected mixed committee, or another named group able to do the work. V must answer the immediate concealment but need not confess Hearth Row without evidence. Edda accepts conditions, resigns, escapes, is detained or remains opposed according to play. The signal tower's matching Cairnfields pulse and Warden service bearings point to Old Crane at the Spire. The refuge supplies the expedition with ordinary provisions and 100 gp; surplus depends on losses, with a minimum 50 gp relief payment if evacuation consumed the fund.

### Objectives

glasshouse

waystation-settled

### Approaches and branches

The rescue evidence supports cooperation without making the party swear loyalty to the Returners.

A persuaded Edda helps prevent another reckless operation; the mechanical damage already done still needs repair.

Early travel to Nexus is possible. Researchers there can identify the same Cairnfields bearings and support the unfinished Waystation problem; travel never silently marks it complete.

### Consequences

Carry Edda's response, volunteer fates, research custody, Oren's family and the Door's condition into later debates.

The paired pulse logs establish human acceleration of a network problem. They do not prove that all arrivals or all Returners are dangerous.

### Prepared encounters

2A — Relief caravan approach, level 4: pass robbers (Bandits/Scouts with a Bandit Captain if party size warrants). They want goods, not extermination. Protect people and decide what happens to the cargo.

2B — Glasshouse rescue, level 4: Rift echoes (Specters) and Dain's guards (Bandits/Scout). Avoid surrounding a small party with life-draining foes. End the active test and recover its trapped people; guards can turn on Dain.

2C — Door emergency, level 5: malfunctioning guardians (Animated Armor) and any surviving violent bypass operators (Bandit Captain/Scouts). Edda fights only if her choices in play actually lead there. Protect the shutdown or completed evacuation.

### Encounter adjudication

Use the opposition guide in this Plan's contained Setting for the named retained SRD profiles and party-size guidance. Place the combat when its stated event occurs; establish the immediate surroundings naturally. Resolve combat, checks, spells and recovery through the Ruleset. Negotiation, stealth, retreat and supported abilities can change an encounter's outcome; do not replace a bypassed fight with another mandatory fight.

### Setting sources

locations/waystation.md

npcs/v.md

npcs/marla-cook.md

npcs/patrol-veteran-stern.md

factions/returners.md

content/monsters/rift-awakening-opposition.md

## Act 3 — Roads Above the End

Levels 6–8. Explore the Cairnfields, recover the Wardens' atlas, and settle a route and water dispute among people living in the shadow of the wound.

The Spire and its neighbors. Old Crane answers the Waystation signal from the Spire. He is an established local guide, not a new immortal oracle. He offers bearings to the Wind Archive, a Warden service chamber within the Spire complex, and warns that the basin regulator has diverted water from an inhabited route. The expedition camps where circumstances permit; the Door Graveyard remains the Setting's refuge from Riftbeasts. Nearby scavengers, traveling families and disaffected raiders use it as a temporary meeting place, not a newly invented capital. Time among them supplies ordinary meals, trades, rivalries and reasons to care about the route.

The dispute. Surveyor Tessa Dorn leads stranded carters and wants the basin reopened. Raider captain Rusk controls a working intake and charges desperate travelers for water. A broken regulator, not Rusk alone, caused the shortage. He has prisoners and stolen goods but also dependents, and his own hardliners oppose compromise. Crane will vouch for a witnessed water-sharing arrangement, not declare Rusk innocent. Tessa offers 150 gp of pooled caravan funds for an honest survey and operation; payment includes a documented failed attempt with completed evacuation. No one can buy the party's opinion for this fee.

The Wind Archive expedition. Travel into the Spire ruins crosses predator territories and raider-held approaches. Choose accessible paths in response to scouting and actual abilities. The Archive's guardians recognize maintenance writs from the Waystation service record; partial or damaged credentials may require dealing with a live custodian or fighting constructs protecting the chamber. Inside, service diagrams show seven primary Anchor stations and their ancillary regulators. The Door Graveyard's wrecks were moved here after collapse; do not add every arch as a new working network node. The atlas marks the Hollows observatory and the surviving Nexus Root Gate regulator. It records Closed Vigil and containment strain, but the return instructions are in the observatory manual.

Losing the original need not lose the campaign. Crane made rubbings during an earlier visit and keeps them in a sealed cache at his Spire shelter. If the chamber is inaccessible or the atlas is destroyed, clear the shelter's occupying beasts or recover the cache from Rusk's supply carriers, then compare its numbered rubbings with a visible service marker. This produces an authenticated copy, not a miraculous replacement appearing in someone's pocket. Crane, a rescued Warden Studies surveyor, or the diagrams' bilingual key can interpret it. The party can learn the truth without Crane surviving.

The basin. Investigating the intake reveals the broken relief circuit and an old maintenance isolation setting reproduced in the atlas. Rusk can permit repairs for access guarantees, release prisoners under pressure, or force a fight when the party contests his control. His hardliners may attack openly once he announces a concession; defeating them changes who remains in power. Predators drawn to the leaking water make the final isolation dangerous. If repairs fail, escort the affected families to the Door Graveyard using stored water from the intake or the carters' reserve. These supplies are prepared alternatives, not a demand for unplanned side quests.

The assembly at the Graveyard. Show an actual working watercourse before claiming restoration. Otherwise count who escaped, what was lost and how water is being shared in temporary refuge. The assembly records terms made with Rusk or his successors and whether prisoners and stolen possessions were returned. No treaty with an absent or hostile faction is magically binding. Crane or the recovered atlas gives the verified route to Kell's Iron and the observatory. As the party leaves, distant tears briefly brighten in unison: the first landscape-scale sign that isolated failures are becoming one event. As a main-expedition award, the salvagers release one recovered +1 Weapon suitable for a party member; use the retained SRD item and supported equipment operations, or record the item without pretending unimplemented effects are automatic. This is a single party item, not one per side quest.

### Objectives

wind-archive

cairnfields-settled

### Approaches and branches

Restore the water with Rusk still in charge under witnessed limits, depose him, or rescue the affected people without resolving the territorial war.

Trade, stealth, maintenance credentials and supported movement magic can change the expedition. The usable atlas and actual basin outcome remain the required results.

### Consequences

Record the route's condition, agreements actually witnessed, prisoners released, caravan losses and the atlas or authenticated copy carried onward.

A surviving Rusk can later offer escorts or become a resentful rival. Do not erase a negotiated settlement just to restore an enemy.

### Prepared encounters

3A — Expedition approaches, level 6: territorial Riftbeasts (Dire Wolf packs or a displaced Owlbear) and Rusk's scouts (Scout/Bandit Captain). Use distinct encounters on routes actually explored, with room to learn or bypass their territories.

3B — Wind Archive, level 6: Warden sentries (Animated Armor, with one Shield Guardian for a sufficiently large party). The guardian's controlling amulet belongs to the service custodian in the Archive; claiming or disabling it follows the actual stat block, not invented universal construct control.

3C — Basin settlement, level 7: Rusk's hardliners (Berserker/Bandit Captain) if diplomacy has not contained them, and a territorial basin predator (Owlbear or Giant Crocodile). The creature wants its leaking feeding ground; it can be driven off. Protect repairs or the refugee departure rather than fight everything simultaneously.

### Encounter adjudication

Use the opposition guide in this Plan's contained Setting for the named retained SRD profiles and party-size guidance. Place the combat when its stated event occurs; establish the immediate surroundings naturally. Resolve combat, checks, spells and recovery through the Ruleset. Negotiation, stealth, retreat and supported abilities can change an encounter's outcome; do not replace a bypassed fight with another mandatory fight.

### Setting sources

locations/cairnfields.md

npcs/old-crane.md

factions/caravaneers-guild.md

content/monsters/rift-awakening-opposition.md

## Act 4 — The Lives Between

Levels 8–10. Make a home among Hollows scavengers, rescue the people erased from V's story, and learn what returning would actually mean.

Kell's Iron. Use Kell's established northern Hollows camp as the hub. Work crews share meals, argue over salvage claims and maintain a memorial of people who did not come back. Kell offers practical shelter and introductions in exchange for respecting crew claims, not mandatory employment. She left V's movement after its secrecy became intolerable; she suspects a past experiment but lacks proof. The party can explore returned buildings, choose salvage jobs, spend time with Companions and meet families who have lived their entire lives here before the main expedition advances.

Hearth Row returns. A displaced residential street reappears within reach of the camp. Its inhabitants experienced a broken, shortened interval while years passed outside. Iona, a local-born schoolkeeper, recognizes V's signature from the old work crews. Younger residents born during the displacement have no elsewhere to go. The row's last stable houses hold survivors, hostile Rift echoes and the bypass instrument responsible for their disappearance. A rescue has people to find and bring out, not merely a ruin to clear. Iona will accompany rescuers or send her testimony with another survivor; she refuses to be used as a weapon against every Riftborn.

The missing chapter. The signed work order in the instrument cabinet names V as authorizing researcher. Its engraved activation date corroborates Iona's account if the paper has been destroyed. A worker's service copy in the observatory confirms the same date if the instrument itself is lost. These are prepared corroborating sources, reached through the street or the next expedition. V may come with the party, arrive when asked, or be confronted by a carried report. They admit the mistake if shown the evidence; confession does not decide forgiveness. Kell wants responsibility, survivors want practical restitution, and some Returners fear losing their only competent leader. Keep those interests separate.

The observatory. The atlas leads to a Warden observation station in the Hollows, occupied by researchers associated with Doctor Cipher. Cipher wants the functioning records and publication credit. She can accept witnessed access and copying, sell what she has, or resist confiscation; her hired guards defend her work unless persuaded that she plans to expend them. The station's automatic quarantine constructs also block access. Reaching the records may require fighting one or both groups, but Cipher's death never destroys the manual by fiat. Read surviving labels and journals, compare translations, and let the party discover the implication before an NPC explains it.

The reveal. Paired records compare outside moments to whole local lifetimes. Translated subject reports consistently identify LSD as the precipitating experience, including remembered blotter or liquid in an ordinary room; no dose, procurement or real-world travel instructions are supplied. The lifetime registers distinguish returning arrivals from locally born children. Records of controlled early departures establish that leaving early is possible and that clear memories are lost. A service manual explains containment and the individual-return interlock. The revelation reframes this world as a real second life rather than invalidating it as a hallucination. V remembers the origin but only now has the missing safe procedure.

An independent archive route. If the original records are destroyed, inaccessible or sold away, Cipher's dispatch ledger names the duplicate set already deposited with the Athenaeum School of Warden Studies in Nexus. Kell has the receipt and can introduce its courier. The school permits supervised study in exchange for a copy of the field atlas and the Hearth Row record; theft or coercion remains possible. Its translator authenticates the paired records from identical station seals. This route is available on the main story, with an early Nexus visit if necessary; return to the camp meeting or conduct it by an actually delivered report with the relevant attendees. Do not require an unrelated scholar's side quest.

The meeting. At Kell's Iron, survivors and invited Returners hear whatever the party chooses to disclose. Full publication respects their ability to decide; limited disclosure can reduce panic but repeats V's dangerous habit. Actually give the selected people their records, demonstration or account, and record what remains concealed. Decide whether V stays as a supervised adviser, is detained, resigns, flees or is absent/dead; compensation promises are recorded obligations, not extra act gates. Kell pays 200 gp from the recovered commission fund for the expedition and report. The atlas and manual identify Nexus as the place the linked wound can still be contained.

### Objectives

hearth-row

hollows-settled

### Approaches and branches

Confront V privately before the survivors, ask them to confess publicly, expose them without consent, or continue without their help.

Work with Cipher under limits, take her records by force, or use the prepared archive copy. The truth is evidence, not a reward for flattering one NPC.

Earlier discoveries stand. If the party already knows the origin, this act supplies verification, human consequences and the workable procedure rather than pretending surprise.

### Consequences

Carry who knows the truth, actual Hearth Row losses, V's authority, Cipher's terms and custody of the copied manual into Nexus.

An SL-born child cannot be promised passage to a parent's outside world. A Riftborn parent's choice therefore has real family consequences that persuasion cannot erase.

### Prepared encounters

4A — Hearth Row rescue, level 8: Rift echoes (Specter/Wraith, sized cautiously for small parties) hunting among returned buildings. Fight to reach survivors and the instrument; do not use the echoes to script a resident's death or overwrite ordinary resurrection rules.

4B — Observatory approach and archives, level 9: Cipher's hired defenders (Gladiator/Scout) if talks fail, plus Warden quarantine constructs (Animated Armor/Shield Guardian). Keep the defenders' allegiances distinct and offer access credentials as an alternative to destruction.

4C — Leaving an active displacement, levels 8–9: one displaced predator (Owlbear or a retained elemental profile whose actual damage fits the presentation). Use this only if the rescue or observatory operation exposed its territory; it protects the evacuation scene, not a compulsory extra fight after an earned quiet exit.

### Encounter adjudication

Use the opposition guide in this Plan's contained Setting for the named retained SRD profiles and party-size guidance. Place the combat when its stated event occurs; establish the immediate surroundings naturally. Resolve combat, checks, spells and recovery through the Ruleset. Negotiation, stealth, retreat and supported abilities can change an encounter's outcome; do not replace a bypassed fight with another mandatory fight.

### Setting sources

locations/the-hollows.md

npcs/kell.md

npcs/v.md

npcs/doctor-cipher.md

factions/hollows-crews.md

content/monsters/rift-awakening-opposition.md

## Act 5 — A City Worth Leaving

Levels 10–12. Live in Nexus long enough to value it, stop a civic disaster, and bring a coalition to the regulator beneath the city.

Arrival in Nexus. Palla offers the Anchor's Rest as a base, with visiting scholars, neighbors and food from many remembered homes. Visit the Crossroads, Riftborn Quarter, Athenaeum and Undercroft entrances at the player's pace. Invite Companions to take an interest in a person, job or cause of their own; do not write an unchosen romance or tell the PC they now love the city. People from earlier acts can arrive with refugees, trade or research if the party's actions gave them reason. Let ordinary side stories and quiet days establish what a decision to stay might contain.

The city's disagreement. Physician Asha Ren protects the Athenaeum hospital and treats the injured regardless of faction. SL-born council delegate Miren Holt wants containment, informed consent and a guarantee that no experiment expends her constituents. Returner delegates want access to early return rather than another indefinite shutdown. Edda can represent them if she retained trust; otherwise a named survivor or local organizer speaks without inheriting her culpability. V can provide expertise without automatically chairing the meeting. Palla wants facts clear enough that ordinary people can make choices. None needs to be convinced that wanting home is shameful.

The civic surge. A section of the Riftborn Quarter begins phasing while hospital equipment and nearby Anchor markers pulse together. Give visible warning and a chance to help before casualties, not a surprise city-wide death toll during a tavern conversation. The cause is a new illegal mass-passage bypass connected from a Returner workshop into the Undercroft civic regulator. Edda authorized it only if she knowingly persisted after the earlier evidence; otherwise Dain's surviving cell or the already-established engineer Halvek continued it against her wishes. Introduce Halvek in the workshop's public commissioning records before the confrontation, not as an endless replacement villain.

Rescue and investigation. The hospital and occupied blocks need an evacuation route while the party finds and isolates the connection. Displaced beasts and awakened constructs make that dangerous. Asha can manage patients if protected, and district crews can do ordinary evacuation work; the PC need not personally carry everyone. Live wiring and stamped installation orders establish the cause. The workshop ledger holds copies if machinery is destroyed. Confront the responsible operators, expose what they knew and end the bypass. A failed isolation can still lead to a completed evacuation, with damaged wards and homes honestly recorded. The city pays 300 gp for the resolved operation or evacuation, and makes ordinary supplies available for the final expedition.

The Awakening. After the civic crisis the party can compare the accumulated timings: Thornwall roots, Glasshouse, the Cairnfields and now Nexus. Distant tears connect into a violet horizon. A common pulse is audible across districts, and shadows briefly fall toward the same absent light. The wound is beginning to act as one. It is neither a voice nor a new deity. Use an announced change in physical conditions to create urgency; do not silently advance a hidden clock for every side conversation. The coalition can evacuate vulnerable areas and plan the final work, but indefinite delay after this warning carries visibly worsening consequences.

The Root Gate approach. Athenaeum service stairs and an established Undercroft smuggling route both lead toward the regulator. Asha can secure official access; a local crew can sell or give the alternate directions in recognition of the civic rescue. The approach is occupied by Warden constructs and any radical holdouts actually left in play. Clear, bypass or negotiate through them and compare the atlas/manual with the controls. This produces a concrete operation: preserve containment and restore elective individual return, or preserve containment with elective return shut. A forced mass bypass is physically available but its recorded and visible consequences are explained before it is chosen.

Commitment. Miren convenes the coalition at the Athenaeum, or district representatives meet at Palla's if formal leaders refuse. Assign who operates controls, who handles evacuation and who maintains the protected approach. The party can choose capable volunteers, themselves or actual allies; no unique class, ancestry, optional favor or perfect faction approval is necessary. Record opposition and promises without pretending unanimity. Securing the approach and committing an executable operation grants the final milestone; give the party the Ruleset's actual level-up choices before Act 6. The city lends each participant one suitable ordinary weapon, armor suit or tool needed for the operation from its actual armory, with return or loss accounted for after the finale; heavy armor still requires the character's normal training and rules.

### Objectives

nexus-crisis

root-gate

### Approaches and branches

Restore both safety and choice, favor a continued elective shutdown, or pursue a disclosed dangerous bypass. The GM describes feasibility rather than assigning moral victory in advance.

The party may refuse formal office and still do the technical work. A coalition can be small but real; a speech to absent allies does not create help.

### Consequences

Record evacuated and saved districts, the culpable operators, public knowledge, participants in the final operation and everyone who declined.

Thornwall supplies, Waystation volunteers, Cairnfields escorts and Hollows crews may help if earned. None is a hidden mandatory side-quest key.

### Prepared encounters

5A — Hospital and streets, level 10: displaced predators or Rift manifestations (retained Air/Fire/Earth Elemental profiles, using their real damage types) and rogue Animated Armor. Protect evacuating people and access to the workshop; success preserves those neighborhoods in the aftermath.

5B — Bypass workshop, level 10: Halvek's or the culpable Edda's defenders (Gladiator/Bandit Captain/Scout), with a Shield Guardian if the party warrants it. Do not add the guardian if its presence overwhelms a small party. Operators surrender when their promises are disproved or defense collapses.

5C — Root Gate approach, level 11: Warden custodians (Shield Guardian with its actual control amulet held by a named custodian, or a Stone Golem for a larger party). Recovered maintenance authority, careful infiltration or a fight can secure access. This approach remains cleared afterward.

### Encounter adjudication

Use the opposition guide in this Plan's contained Setting for the named retained SRD profiles and party-size guidance. Place the combat when its stated event occurs; establish the immediate surroundings naturally. Resolve combat, checks, spells and recovery through the Ruleset. Negotiation, stealth, retreat and supported abilities can change an encounter's outcome; do not replace a bypassed fight with another mandatory fight.

### Setting sources

locations/nexus.md

npcs/nexus-innkeep.md

factions/the-stayers.md

factions/returners.md

content/monsters/rift-awakening-opposition.md

## Act 6 — The Open Door

Level 12. Confront the machinery enforcing a centuries-old emergency, contain the awakened Rift, and decide what to do with a real second life.

Before the descent. At the Anchor's Rest or the defended Athenaeum staging area, give the party one genuine chance to speak with each other and chosen friends. Volunteers reaffirm their consent and the coalition checks the actual operation. These are conversations, not loyalty checks required to unlock survival. The Root Gate approach stays in the state established in Act 5; do not repopulate it to consume fresh resources. Let the Ruleset determine rests and available resources. No additional travel or crafting subsystem is introduced.

The Vigilant Engine. The final regulator houses the last active custodian of Closed Vigil. Its role is to prevent a repeat of the mass-passage catastrophe, and its obsolete orders treat restoration of elective return as sabotage. It demands maintenance authorization, identifies the forbidden bypass and offers indefinite shutdown as its only approved solution. It is a construct with a recorded directive, not secretly V or a god. Present its instruction clearly enough to debate, deceive with evidence, lawfully supersede or oppose. The observatory manual and atlas together contain the legitimate procedure for ending Closed Vigil; the Engine can be persuaded through demonstrated compliance with that procedure, not a generic ungrounded charm roll.

The confrontation. Default to a serious fight against the Engine if the party insists on operating beyond its orders without satisfying the authenticated override. Its body uses the Stone Golem profile, with support only as appropriate to the actual party. Defeating its body allows access; no secret invulnerability phase or optional item is required. An earned override disarms it and leaves a potentially valuable custodian. If the party loses, retreat through the secured approach and evacuate the threatened districts if still possible. An intact Engine continues its failing shutdown; do not narrate a safe restoration the party did not perform.

Performing the operation. The actual work has three named tasks: disconnect the mass-passage bypass, set the regulator to distribute containment across the surviving anchors, then choose the individual-return interlock's open or closed setting. The atlas identifies circuits, the manual identifies controls, and the Root Gate labels confirm them. Skilled work, admitted magic, allied operators or direct manual operation can complete these tasks; no single roll decides the fate of the world. Uncertain checks resolve particular attempts and consequences explained beforehand. If interruption damages the controls, the manual provides a containment-only fallback using the accessible emergency isolator. That fallback can save the region but leave elective return unavailable pending later repairs.

What each result means. Regulated return plus containment quiets the linked tears without destroying ordinary crossings. The Door admits willing Riftborn individually after a witnessed consent and identity check; locals cannot be transferred because there is no outside body to receive them. Containment-only also saves the region but leaves early-return advocates with a real grievance and an unresolved future project. Forcing the mass bypass replicates the known failure: neighboring anchors overload, districts phase and casualties follow actual exposure and evacuation. Stop it in time and a damaged containment fallback may still hold. If the region is lost, native lives end locally and permanently dead Riftborn return; do not call that everybody getting a happy trip home.

Stay or leave. Explain the established memory loss before the PC chooses. A safe Door remains available under its actual agreed custody; staying today need not mean an invented irreversible vow never to leave. Each Companion decides independently, using the relationship and priorities developed in play. The PC may stay while a Companion leaves, leave while others stay, or arrange another feasible future. The party cannot take SL-born children into a nonexistent outside life or force an unwilling person through. A PC returning now experiences a brief ordinary outside awakening, with no clear encyclopedic memories, transferable powers or convenient wealth. The Severed Lands and the people left there continue.

The immediate ending. At the surviving Nexus gathering, give the actual result to Asha, Miren, Palla and surviving delegates or their named successors. Record who holds the regulator, what oversight and access were agreed, V's confession/accountability or unresolved absence, Edda's return/stay/detention/death, and the neighborhoods and people lost. Resolve the prepared party farewells or welcome before offering continued local adventure to a staying PC or Finish the story. If the PC has already departed, use a brief external closing glimpse of those left behind rather than claiming the PC knows it. Optional after-story follows only if the player finishes: it grows from actual relationships and choices, not a preset marriage, children or mandatory death scene.

### Objectives

final-operation

### Approaches and branches

A legitimate override can avoid the Engine battle without losing the climax: performing the chosen operation and personal decisions remain substantive play.

Successful containment can coexist with an imperfect coalition, V never being forgiven or a Companion choosing to leave.

If the party chooses an unsafe operation or fails, resolve the actual losses and escape. An early withdrawal is an honest failed campaign ending, not completion of unplayed successful milestones.

### Consequences

Maintain the world after any personal departure. People, property, children, promises, injuries and political decisions are not erased when the outside trip ends.

No new mandatory act begins after the Nexus aftermath. Optional local play uses the surviving world and the same level-12 cap unless the user explicitly commissions a revised Plan.

### Prepared encounters

6A — Root Gate regulator, level 12: Vigilant Engine (SRD Stone Golem). For PC plus one Companion consider the declared damaged-custodian variant in the opposition guide; for a robust larger party add only warranted Animated Armor or a separate earlier custodian encounter. The Engine attacks those forcing the controls, obeys an authenticated override and stops when defeated. Objective: gain usable control or withdraw with an honest failure result.

6B — Interrupted final operation: only holdouts or manifestations established by the actual chosen operation may interfere (Gladiator/elemental profiles already used). A cooperative Edda is not forced into a betrayal, and a contained wound does not spawn a final surprise boss. Combat supports the performed operation, not an arbitrary kill total.

### Encounter adjudication

Use the opposition guide in this Plan's contained Setting for the named retained SRD profiles and party-size guidance. Place the combat when its stated event occurs; establish the immediate surroundings naturally. Resolve combat, checks, spells and recovery through the Ruleset. Negotiation, stealth, retreat and supported abilities can change an encounter's outcome; do not replace a bypassed fight with another mandatory fight.

### Setting sources

locations/nexus.md

npcs/v.md

npcs/nexus-innkeep.md

DM-SECRETS.md

content/monsters/rift-awakening-opposition.md

## Campaign objectives

```json
{
  "completion_conditions": [
    "The road encounter has ended by combat, an earned escape or another actual resolution, and the surviving party reaches Thornwall.",
    "Old Vesper, or the Fortress Inn staff if she is unavailable, has welcomed the party, offered basic local information and a first night's food and lodging; the party actually takes its first rest or knowingly makes another local arrangement."
  ],
  "description": "Reach Thornwall together and meet the people offering a foothold.",
  "id": "thornwall-refuge",
  "title": "A first safe night"
}
```

```json
{
  "completion_conditions": [
    "The Underroot Mill expedition has ended and Nell, Seli and the other workers are accounted for as rescued, escaped, still trapped with their location known, or dead by established events.",
    "The stripped regulator and its dispatch record establish that three conductors were removed: one remains at the mill, one was sold to Fen Marr, and one went to Waystation. Direct inspection or Nell's retained sketch plus Seli's dispatch copy supplies the evidence."
  ],
  "description": "Find the missing mill workers and establish the removed regulator conductors.",
  "id": "underroot",
  "title": "What happened to the mill"
}
```

```json
{
  "completion_conditions": [
    "The mill regulator is actually isolated using two conductors and the threatened workers and homes protected, OR the party's attempted protection has ended and evacuation of the threatened mill and downstream homes is actually completed with losses recorded.",
    "Hadran Vale's sale and grain-hoarding evidence has reached the council or been publicly presented through the market; Seli's accusation receives an explicit upheld, withdrawn or disputed disposition based on what was shown.",
    "Closing event: Old Vesper's community meal occurs, even if reduced to refugee provisions; the surviving workers and town representatives acknowledge the actual outcome, and Stern delivers the Waystation lead or leaves her written invitation."
  ],
  "description": "Resolve the failing mill and the attempt to blame Thornwall's newcomers.",
  "id": "thornwall-settled",
  "title": "A place at the table"
}
```

```json
{
  "completion_conditions": [
    "The trapped volunteers' fates are established, and the live Glasshouse bypass is shut down or its exhausted wreckage isolated after a failed rescue.",
    "The supply receipt and pulse log, or the volunteers' duplicate log kept at Waystation, establish Edda's sponsorship and the connection to Thornwall; the surviving volunteers or an honest loss report reach Waystation."
  ],
  "description": "End the Glasshouse test and account for its people.",
  "id": "glasshouse",
  "title": "The missing volunteers"
}
```

```json
{
  "completion_conditions": [
    "The unauthorized Door test has actually ended: the bypass is isolated with the refuge protected, OR its failed operation ends and the endangered residents are evacuated to the upper settlement with losses established.",
    "Closing event: Waystation's assembly assigns research custody to an accountable named group, records Edda's and V's actual roles, and releases the signal record and service bearings for the Cairnfields expedition. Giving a speech without transferring access and records does not close the act."
  ],
  "description": "End Waystation's Door emergency and decide who controls the research.",
  "id": "waystation-settled",
  "title": "The right to hope"
}
```

```json
{
  "completion_conditions": [
    "The Wind Archive expedition has ended and a readable atlas or an authenticated copy from Old Crane's archived rubbings has been obtained and interpreted.",
    "The party has established that the Doors form a containment network, that Closed Vigil stopped elective passage, and that the Hollows observatory and Nexus regulator contain the missing operating knowledge."
  ],
  "description": "Recover usable evidence of the network from the Spire archive.",
  "id": "wind-archive",
  "title": "The old instructions"
}
```

```json
{
  "completion_conditions": [
    "The damaged basin regulator has been isolated and water restored to the inhabited route, OR the failed operation has ended and the affected refuge is actually evacuated to the Door Graveyard with water supplies and recorded losses.",
    "Closing event: the refuge assembly receives the field result, records water-access and escort terms with whichever parties agreed, and the expedition leaves with its verified Hollows lead. No forced peace with hostile raiders is required."
  ],
  "description": "Settle the refuge's water and route crisis while keeping its inhabitants in the decision.",
  "id": "cairnfields-settled",
  "title": "A road people can use"
}
```

```json
{
  "completion_conditions": [
    "The Hearth Row rescue has ended and the residents encountered are safe, missing with their last known locations recorded, or dead by established events.",
    "V's authorization of the failed bypass is established from the signed work order OR Iona's direct account corroborated by its dated instrument record. Kell receives the survivors or loss report and the evidence."
  ],
  "description": "Reach the returned street, account for its residents and establish what V did.",
  "id": "hearth-row",
  "title": "The street that was lost"
}
```

```json
{
  "completion_conditions": [
    "The observatory expedition has ended and its authenticated departure/lifetime records and operating manual are recovered or copied through the prepared Athenaeum duplicate route.",
    "The party establishes the LSD origin, natural return, memory loss, the different fate of local-born people, and the feasible controlled early-return procedure.",
    "Closing event: a meeting at Kell's Iron records what will be disclosed, to whom, V's accountability or absence, and custody of the operating evidence; the chosen disclosures actually occur to the named attendees or recipients. A promise to tell them later is insufficient."
  ],
  "description": "Establish the crossing truth and give it a public disposition.",
  "id": "hollows-settled",
  "title": "A life is still a life"
}
```

```json
{
  "completion_conditions": [
    "The hospital and occupied streets are protected by isolating the civic bypass, OR the attempted protection ends and the threatened people are actually evacuated with losses established.",
    "The illegal connection from a Returner workshop to the Undercroft regulator is established from the live wiring and its stamped orders, or their copies in the workshop ledger. The surviving responsible operators are confronted or their deaths/escape recorded."
  ],
  "description": "End the civic surge and confront the bypass causing it.",
  "id": "nexus-crisis",
  "title": "A city that breathes"
}
```

```json
{
  "completion_conditions": [
    "The Root Gate approach is actually secured by defeating, bypassing or negotiating past its occupants; the atlas and operating manual have been compared with its labeled controls.",
    "Closing event: the Nexus coalition, or the party and surviving district representatives if formal authority refuses, assigns evacuation and operating roles and commits to a specific feasible final operation or containment fallback. Anyone refusing remains outside the agreement; consensus is not compulsory."
  ],
  "description": "Secure the final approach and commit an executable plan.",
  "id": "root-gate",
  "title": "The people at the Door"
}
```

```json
{
  "completion_conditions": [
    "The Vigilant Engine confrontation has ended by defeat, authenticated override or a failed attempt followed by actual retreat; its status and the final controls' accessibility are recorded.",
    "The selected operation actually completes as regulated return plus containment, containment with elective return shut, or an attempted operation fails and the actual rupture/evacuation result is established. Announcing a choice does not perform it.",
    "Each surviving party member makes their own stay or feasible departure choice and any chosen immediate return actually occurs. When safe departure is unavailable, record staying, waiting or an attempted unsafe departure honestly.",
    "Closing event: the Nexus survivors receive the actual result; the prepared immediate aftermath resolves research custody, V and Edda's disposition, losses and the party's farewells or welcome. The PC's subsequent lifetime is optional after-story, not an act objective."
  ],
  "description": "Resolve the awakened Rift and the party's choice about home.",
  "id": "final-operation",
  "title": "The open Door"
}
```

## Intended ending or ongoing final act

```json
{
  "mode": "finite",
  "outcomes": [
    {
      "after_story": "Develop the PC's chosen local future from actual friendships, work, loves and unfinished hopes. If their eventual natural return is narrated, memories fade outside while the local life remains consequential. Do not prescribe a spouse, child or date of death.",
      "conditions": [
        "The final-operation objective's applicable confrontation, performed operation and immediate aftermath conditions are satisfied; this is not merely an announced plan.",
        "Containment and regulated early return are working; the PC chooses to remain locally for now."
      ],
      "id": "stay-with-a-door",
      "immediate_aftermath": [
        "Nexus survivors are told whether containment holds and whether safe early return is actually available; named losses, damaged neighborhoods and evacuations are acknowledged.",
        "Custody of the Root Gate and actual consent/access rules are recorded. V's accountability or absence and Edda's final disposition are explicitly resolved to the extent the party can actually establish.",
        "Surviving Companions make independent feasible choices; farewells, departures or welcome take place. Previously accepted promises remain recorded obligations, not unannounced extra gates to ending."
      ],
      "open_world": true,
      "outcome": "The region survives with a genuine choice of early return, and the PC keeps living in it."
    },
    {
      "after_story": "An ordinary outside life carries a faint sense of having learned to live differently. Do not grant transferable powers, precise secret knowledge or a guaranteed second crossing.",
      "conditions": [
        "The final-operation objective's applicable confrontation, performed operation and immediate aftermath conditions are satisfied; this is not merely an announced plan.",
        "Containment and regulated early return are working; the consenting PC actually departs through the Door."
      ],
      "id": "leave-through-the-door",
      "immediate_aftermath": [
        "Nexus survivors are told whether containment holds and whether safe early return is actually available; named losses, damaged neighborhoods and evacuations are acknowledged.",
        "Custody of the Root Gate and actual consent/access rules are recorded. V's accountability or absence and Edda's final disposition are explicitly resolved to the extent the party can actually establish.",
        "Surviving Companions make independent feasible choices; farewells, departures or welcome take place. Previously accepted promises remain recorded obligations, not unannounced extra gates to ending.",
        "Present the brief outside awakening with fleeting emotional impressions and almost no clear local memories, then a short external glimpse of the local result if needed to close remaining aftermath."
      ],
      "open_world": false,
      "outcome": "The PC wakes in the outside world; the Severed Lands continues without them."
    },
    {
      "after_story": "Develop the life the PC actually chooses in the saved world. Any later Door research remains uncertain unless established through continued play; natural return still follows the end of a Riftborn's local life.",
      "conditions": [
        "The final-operation objective's applicable confrontation, performed operation and immediate aftermath conditions are satisfied; this is not merely an announced plan.",
        "Containment holds but elective return remains shut or damaged; the PC survives locally."
      ],
      "id": "containment-without-passage",
      "immediate_aftermath": [
        "Nexus survivors are told whether containment holds and whether safe early return is actually available; named losses, damaged neighborhoods and evacuations are acknowledged.",
        "Custody of the Root Gate and actual consent/access rules are recorded. V's accountability or absence and Edda's final disposition are explicitly resolved to the extent the party can actually establish.",
        "Surviving Companions make independent feasible choices; farewells, departures or welcome take place. Previously accepted promises remain recorded obligations, not unannounced extra gates to ending."
      ],
      "open_world": true,
      "outcome": "People have time to live and repair what remains, while early departure is still unavailable."
    },
    {
      "after_story": "Follow the surviving community and the PC's chosen response to loss. Rebuilding need not erase culpability or manufacture a complete recovery.",
      "conditions": [
        "The final-operation objective's applicable confrontation, performed operation and immediate aftermath conditions are satisfied; this is not merely an announced plan.",
        "The operation fails or the mass bypass causes a rupture; the PC reaches an actually surviving local refuge and regional losses are established."
      ],
      "id": "rupture-survivor",
      "immediate_aftermath": [
        "Nexus survivors are told whether containment holds and whether safe early return is actually available; named losses, damaged neighborhoods and evacuations are acknowledged.",
        "Custody of the Root Gate and actual consent/access rules are recorded. V's accountability or absence and Edda's final disposition are explicitly resolved to the extent the party can actually establish.",
        "Surviving Companions make independent feasible choices; farewells, departures or welcome take place. Previously accepted promises remain recorded obligations, not unannounced extra gates to ending.",
        "Identify the actual surviving refuge, missing people and relief effort. Do not promise that destroyed native lives can be recovered through return."
      ],
      "open_world": true,
      "outcome": "The survivors face the cost of the failed operation in a permanently changed Severed Lands."
    },
    {
      "after_story": "Let the faint emotional residue of a real second life accompany the PC's outside future. The people and losses left behind were real.",
      "conditions": [
        "The PC's local death becomes permanent under the Ruleset, ending this local life; OR an actually attempted uncontrolled departure returns the PC while the failed operation's consequences are established."
      ],
      "id": "life-ended",
      "immediate_aftermath": [
        "Record the actual point of departure/death, who survived locally and what remained unresolved. Do not grant unearned milestones or call unplayed acts completed.",
        "Give a brief outside awakening; use an external closing glimpse only for local outcomes actually established, without inventing the party's later victory."
      ],
      "open_world": false,
      "outcome": "The PC returns outside with almost no clear memory; unfinished local work and its consequences remain unfinished.",
      "requires_all_acts": false
    }
  ]
}
```

## GM guidance

Party setup: this Plan begins with a level-1 Player Character and one to five level-1 Companions, all newly transported Riftborn. Use compatible selected identities without rewriting an established local-born character into an arrival; author appropriate characters during setup. Read this Plan and its contained Setting truth before running the opening. Public beliefs can be mistaken; reveal facts only through the stated evidence or an earlier earned route. All main-story answers, alternative evidence sources and closing events are authored here. Act labels are private preparation. Record the current required problem and the exact unresolved condition so an optional errand cannot become a substitute main plot. Apply the advancement declaration in initial_state.mechanics with the Ruleset's actual tools; no narrated instant level changes, invented XP or free recovery.

Allow revisits, detours, emergent side quests, independent faction choices and early discoveries. Ask what the player wants to do in the scene, rather than choosing their actions or emotions. Keep Companions present and individually motivated; quiet scenes and conflict both develop relationships. Returning to an earlier hub carries its actual people, losses and promises. Do not require real-time travel accounting or low-paid work loops as gates between authored situations.

Prepare who opposes the party, when the encounter arises, what each side wants and what resolves it. Use the admitted SRD profiles identified by the opposition guide and the actual party's capabilities. Describe ordinary surroundings to support choices; do not force a sequence of bespoke ledges, switches or repeated terrain gimmicks. Recovery, character creation, equipment effects and spell rules belong to the Ruleset. The main plot supplies its necessary research, transport opportunities and ordinary support; side jobs provide modest additional money, equipment and human connections.

Each act has exact required objectives and a named closing event. All conditions of each linked objective must hold, with alternatives only where written. Methods, alliances and routes remain open; refusal or a new side job does not complete a main objective. A deliberately abandoned campaign can end honestly at its actual state without claiming unplayed acts or milestones. In the finale, provide a feasible safe-return route through main-story evidence, separate the world's operation from each person's choice, and resolve immediate aftermath before optional continued play or after-story.

## Completion and aftermath

All six acts reach their required authored endings through their stated success or compromise alternatives, and the final operation and immediate aftermath actually occur.

The applicable finite ending's conditions hold. A premature death, abandonment or failed final attempt can end the story honestly without being mislabeled completion of all six acts.

## Failure and withdrawal

Use each act's prepared failed-rescue, evacuation or alternate-evidence route when it occurs; do not silently repair losses or convert refusal alone into success.

A permanent PC death ends this local life under the life-ended outcome. A consensual decision to stop earlier records the actual unfinished world and gives only an aftermath grounded in events already established.

Failure at the Root Gate can produce containment-only survival, actual evacuation, regional rupture or permanent death according to what the party performed. No optional ally or side-quest object is a secret requirement for a viable ending.

## Optional continuation

After the required immediate ending, a locally surviving PC can choose Continue adventuring in the actual surviving world or Finish the story. The after-story is only narrated on Finish.

A returned PC cannot continue local free-roam as if departure had not happened. A new protagonist or new campaign is a separate player choice, not an automatic extra act.

## Side opportunities

Side-story policy. Offer several leads naturally in each hub, not a checklist dumped at arrival. These prepared stories are optional and can be joined by player-requested local adventures. Resolve each chosen story's people and payment before treating it as finished. No side story awards levels or XP, supplies an indispensable main-plot key, or requires a compulsory romance. Listed money is the total party reward, paid once by the named patron. Unclaimed possessions remain with their owners. At higher levels most local foes are genuinely easier; do not automatically scale every thief into a boss. Equivalent ordinary tools, modest coin and favors can reward emergent quests. SRD Lite has a reduced spell list (117 supported spells) and no consumables; consumables belong to future SRD Full. Do not invent numerical powers for a colorful keepsake.

A1 — The orchard at the wall. Grower Bren's winter stores are being eaten by Rift-touched boars. Investigate the broken boundary and drive off or fight the animals (Boar); repairing the boundary prevents their return. Bren pays 15 gp and gives the party a basket for Vesper's kitchen. Killing them without closing the gap leaves an honest unfinished protection job; peaceful redirection earns the same reward. The altered roots hint at the mill's strain but are not required evidence.

A1 — A room in the Warrens. Seli's neighbor Ruan is evicted on a forged lease while his tools are held by hired enforcers (Bandits). Recover the original from the landlord's clerk, bargain or confront the enforcers, then return the tools and establish where Ruan can live. He offers an ordinary tool kit and later repairs at fair prices. Exposure can make a council clerk an ally; leaving without a housing disposition is not a completed rescue.

A1 — The apprentice's bell. Forge apprentice Toma lost a commission bell to cellar scavengers. Giant Rats and a frightened thief make an appropriate early fight. Find the bell, decide whether to report the thief, and deliver it. Toma pays 10 gp or fits one ordinary shield. The thief stole food first and should have something to say.

A1 — The broken patrol. Stern asks the party to look for two overdue volunteers along a familiar woodland spur. Wolves have driven them into a damaged shelter; one blames the other for abandoning the watch. Resolve the animals, return the volunteers or their known fates, and decide whether to carry their accusation. Stern pays 20 gp and admits that fear can make good people unreliable. This is an optional small rescue, not the Glasshouse main-story lead.

A1 — Supper for a stranger. Vesper asks for help preparing a welcome meal for an elderly arrival who recognizes nobody. Follow a remembered song through market musicians and invite the people he actually wants present. No fight is expected. A real shared supper, not curing his memory, resolves it; Vesper gives 5 gp and keeps a place at her table. The PC and Companions may share something of themselves without recovering the crossing mystery.

A1 — The prize nobody won. A fairground sparring exhibition has been rigged by a sponsor threatening its young contestants. Investigate wagers and face willing adult opponents using agreed nonlethal Ruleset combat. Expose the rigging or secure release from the threat; distribute the 20 gp prize as actually negotiated. Losing the bout does not make the intimidation invisible. No child combatants are required.

A1 — A letter without a reader. A dead cart driver's partner asks someone to retrieve a satchel from a wolf-haunted roadside wreck. Defeat or divert the animals, recover the letters and choose whether to admit reading a private one. Delivery earns 10 gp and the family's trust if respected. Its contents concern an ordinary estrangement, not a second secret chosen-one prophecy.

A1 — Two weddings, one roof. A native family objects to a Riftborn marrying their adult child because arrivals might leave. Hear the couple and negotiate a venue at the inn; no roll can dictate love or consent. Completing their chosen celebration or respectfully returning deposits ends the story. A 10 gp honorarium or a carved keepsake is offered. The family question foreshadows departure without revealing the cosmic answer.

A2 — The winter stores. The quartermaster finds food missing. Hungry camp residents diverted it to unregistered families, while a fence resells part of it. Track both uses, confront the fence's Bandits and settle how the families are fed and losses recorded. The quartermaster pays 30 gp for a real resolution; punishing the families alone leaves the theft's profiteer active.

A2 — The watch that stopped answering. A signal crew at a pass outpost is trapped by a territorial Owlbear. Rescue or establish their fates and restore the signal. Stern pays 40 gp and supplies one ordinary spyglass on loan, returned or accounted for afterward. Their sketches corroborate widespread pulses but are never necessary for the main atlas route.

A2 — Names for tomorrow. Marla keeps a book of residents who want their local lives remembered. Interview willing people, help a bereaved friend write an entry and settle an argument over whether leaving is betrayal. No combat expected. Marla rewards the party with a shared feast and introductions. The book remains here whatever anyone later remembers outside.

A2 — A shield left behind. Oren wants a missing repair partner's shield recovered from an old patrol shelter occupied by Bandits. The partner deserted, alive, and does not want to return to Oren's household. Resolve the confrontation, respect the person's choice and bring Oren the shield or truthful message. Payment is 35 gp; do not turn a promised reconciliation into compelled affection.

A2 — The false farewell. A swindler sells fake Door tickets and hires a Scout to intimidate complainants. Follow receipts to the storeroom, recover the victims' money by negotiation or combat, and publicly establish that no passage was purchased. Victims offer a 25 gp pooled reward if they can afford it. This fraud never disproves every honest Returner's hope.

A2 — A night off. Invite a Companion to a cookout, a friendly spar or a visit to the signal tower, following their interests. Rival volunteers may challenge the party to consensual practice combat; use the Ruleset and stop when agreed. The reward is a developed relationship and 10 gp in modest stakes only if explicitly wagered. No automatic loyalty score or stat bonus is granted.

A3 — The stolen names. Raider scavengers have removed memorial markers for useful metal. Track them, confront Scouts and a Bandit Captain if necessary, then return markers or obtain the bereaved families' chosen replacement. Tessa pays 60 gp; a family may forgive the scavengers or refuse. Their response survives the transaction.

A3 — The cart that became a home. Carters' wagon is stranded in an Owlbear feeding ground and now houses an injured adult and two children. Create an escape, defeat or draw off the beast, and deliver the family to its chosen camp. Their guild pays 50 gp and repairs ordinary gear. Children need protection, not graphic peril or a compulsory death to prove stakes.

A3 — The novice at the Spire. A young adult pilgrim imitates Crane's feats and is trapped above a route patrolled by giant eagles defending their nest. Climb, use appropriate magic, or fight only if needed; return the novice and settle whether they still want training. Crane gives a serviceable climber's kit and 20 gp. He does not teach an unencoded teleport ability as a reward.

A3 — Water for enemies. Two wounded raiders have been denied the communal supply. Escort a healer and protect them from a vengeful Berserker while negotiating treatment terms. A completed treatment/evacuation or honestly recorded refusal resolves the dispute. The healer pays 40 gp; later raider testimony depends on how they were treated, not on an automatic faction meter.

A3 — The silent market. An abandoned trading camp holds goods claimed by two missing merchants' families and is occupied by predators (Dire Wolves). Recover the ledgers and accessible goods, then settle the competing claims with evidence. Keep 75 gp commission from the agreed estate; taking everything instead creates a theft consequence and no honest commission.

A3 — Weather in a bottle. A surveyor wants observations of the violet pulse from three accessible marked stations. Scouts or a displaced elemental may contest a station; let tools and field judgment determine safe approaches. Deliver actual observations, including an honestly abandoned dangerous station. Pay 60 gp for the full set or 20 gp per completed station. The measurements enrich the Awakening's presentation but are not required for the network diagnosis.

A4 — The schoolroom window. Iona asks for pupils' names and drawings from a returned annex guarded by a hostile Specter. Retrieve what survives and hold the remembrance the living families choose. Kell's fund pays 80 gp. The drawings are evidence of individual local childhoods, not a way to reverse their deaths.

A4 — A fair share of iron. A rival crew seizes Kell's salvagers' tools under a disputed contract. Find the signed version at the storehouse and confront its Gladiator foreman if talks fail. Return tools and record the actual settlement. Kell pays 100 gp; a compromise can preserve useful colleagues without making the theft acceptable.

A4 — The house that remembers rain. A couple's former home reappears, occupied by an elemental drawn to its broken water system. Recover their requested belongings and isolate the leak or escort them away after failure. They pay 70 gp and offer a modest heirloom with no mechanical bonus. Let them choose whether returning to the house is comforting or painful.

A4 — Cipher's missing assistant. An adult assistant fled with notes showing that volunteers were not informed of risks. Cipher's hired Scouts pursue them. Protect or betray the assistant, recover the notes and actually deliver them to the chosen recipient. A public research fund pays 100 gp for verified disclosure; Cipher instead offers 150 gp for suppression, with the assistant's trust lost. This evidence concerns a separate experiment, not a gate to the main manual.

A4 — Dinner for the living. A crew wants to mark the return of Hearth Row survivors without calling their losses a miracle. Gather invited people, settle a seating feud and help the survivors decide what is said. No combat expected. Kell provides a meal and 25 gp from the celebration fund. A Companion may speak, refuse or simply be present.

A4 — The false salvage claim. A dealer declares a Riftborn's adult SL-born child to be property attached to a recovered estate. The claim is fraudulent. Retrieve the actual register and confront the dealer's Bandit Captain and guards if necessary; establish the person's freedom and chosen destination. Their guild pays 100 gp. The person is never legitimate loot, and local birth is not a hidden return ticket.

A5 — Palla's lost cabinet. A stolen box from the Anchor's Rest contains guests' personal effects and an embarrassing academic correspondence. Trace it through the Crossroads, face the fence's guards (Bandit Captain/Scouts), then return or account for each requested item. Palla pays 100 gp and gives the party a private dinner. The notes are not indispensable final-operation instructions.

A5 — The last performance. A Riftborn musician preparing to leave wants one final concert, while a violent creditor intends to seize the proceeds. Protect rehearsal and performance from the creditor's Gladiator-backed gang, or settle a legitimate part of the debt without conceding extortion. The musician pays 100 gp from actual takings. They still decide whether to depart; the party cannot buy their ending.

A5 — The hospital supply line. Asha needs medicine from a warehouse sealed by an awakened Shield Guardian and its actual control amulet held inside by a trapped custodian. Obtain access, rescue the custodian or defeat the guardian, then deliver the supplies. The hospital pays 150 gp. Restoring delivery improves later evacuation outcomes but does not unlock the only feasible finale.

A5 — A charter for tomorrow. Miren asks for testimony from native and Riftborn businesses on the proposed Door charter. A faction hires street enforcers to suppress a witness; protect the witness using negotiation or combat against Scouts/Bandit Captain. Deliver consenting testimony and hold the hearing. Pay 100 gp; the adopted provisions reflect the hearing rather than a universal persuasion roll.

A5 — The counterfeit relic. A respected curator bought a fake while the real object was stolen from a small neighborhood shrine. Track the sale to smugglers and a trapped Undercroft vault (Animated Armor). Recover the object and decide who receives it, then disclose or conceal the curator's error. The lawful owner offers 150 gp; selling it creates different consequences. It is culturally valuable, not a mandatory combat upgrade.

A5 — The family at the threshold. A Riftborn parent wants to leave; their adult SL-born child wants the family workshop fairly transferred first. Help settle accounts and threats from a predatory creditor (Bandit Captain if violent), then witness the actual chosen arrangement. The child offers 80 gp for useful assistance. Neither owes the party gratitude for deciding their lives, and no scheme can transfer the child outside.

A5 — A place of your own. A Companion or the PC may pursue a modest tenancy, apprenticeship, garden or community project in Nexus. Help clear a dangerous abandoned room if appropriate (local beasts, without level-scaling them), negotiate with its actual owner and record the agreement. Reward is the agreed use of a place and relationships, not free ownership or income. This gives staying a concrete possibility without presuming it is chosen.

A6 — One more rescue. Before the final descent, a named contact is trapped in a district actually threatened by the Awakening. Use one established local foe or manifestation, not a new revelation. Rescue or establish their fate and report it. The coalition offers 150 gp if its funds remain. Refusal has that person's actual consequence, not a hidden penalty to the final controls.

A6 — Things to leave behind. People preparing to return ask for wills, letters and ordinary possessions to be delivered locally. Complete only the errands the party accepts; recipients decide how to react. A pooled 50 gp honorarium may be offered. The keepsakes continue to exist even though their authors will barely remember them.

A6 — The last argument. A radical remnant threatens a consenting departure queue only if the final political result left such a remnant active. Negotiate or fight its surviving Bandit Captain/Gladiator leadership and secure or disperse the queue honestly. The custodian council pays 150 gp. Do not create this attack after a fully resolved peaceful agreement simply to negate it.

A6 — A table still set. After the required immediate ending, a staying PC can revisit Vesper, Marla, Kell or Palla with willing Companions. Their reception reflects actual old promises and losses. No combat or payment is required. This can launch optional local play or become the player's chosen closing scene; it is never another required act.

## Mechanics requiring GM adjudication

Rift synchronization, localized displacement, maintenance procedures, Door consent checks and outside return are fictional GM adjudications, not newly installed engine tools. Use Ruleset tools for every represented combat, check, condition, resource, inventory and advancement change.

No custom travel, recovery, faction-meter, companion-loyalty or economy subsystem is introduced. Narrative cooperation never supplies an undeclared stat bonus or a free rest.

The cosmology limits where travel magic can lead: no identified spell destination is the originating outside body. Explain this established setting fact before a player spends a resource expecting ordinary planar magic to replace the return procedure. Do not silently disable legitimate local spell uses.

## Referenced Setting files

```json
{
  "creatures": [
    {
      "id": "rift-awakening-opposition",
      "path": "content/monsters/rift-awakening-opposition.md"
    }
  ],
  "factions": [
    {
      "id": "returners",
      "path": "factions/returners.md"
    },
    {
      "id": "the-radicals",
      "path": "factions/the-radicals.md"
    },
    {
      "id": "the-stayers",
      "path": "factions/the-stayers.md"
    },
    {
      "id": "hollows-crews",
      "path": "factions/hollows-crews.md"
    },
    {
      "id": "caravaneers-guild",
      "path": "factions/caravaneers-guild.md"
    }
  ],
  "items": [],
  "locations": [
    {
      "id": "verdant-margin",
      "path": "locations/verdant-margin.md"
    },
    {
      "id": "thornwall",
      "path": "locations/thornwall.md"
    },
    {
      "id": "waystation",
      "path": "locations/waystation.md"
    },
    {
      "id": "cairnfields",
      "path": "locations/cairnfields.md"
    },
    {
      "id": "hollows",
      "path": "locations/the-hollows.md"
    },
    {
      "id": "nexus",
      "path": "locations/nexus.md"
    }
  ],
  "lore": [
    {
      "id": "rift-awakening-truth",
      "path": "DM-SECRETS.md"
    },
    {
      "id": "world-overview",
      "path": "lore/world-overview.md"
    },
    {
      "id": "history-timeline",
      "path": "lore/history-timeline.md"
    }
  ],
  "npcs": [
    {
      "id": "innkeep-vesper",
      "path": "npcs/innkeep-vesper.md"
    },
    {
      "id": "patrol-veteran-stern",
      "path": "npcs/patrol-veteran-stern.md"
    },
    {
      "id": "marla-cook",
      "path": "npcs/marla-cook.md"
    },
    {
      "id": "v",
      "path": "npcs/v.md"
    },
    {
      "id": "old-crane",
      "path": "npcs/old-crane.md"
    },
    {
      "id": "kell",
      "path": "npcs/kell.md"
    },
    {
      "id": "doctor-cipher",
      "path": "npcs/doctor-cipher.md"
    },
    {
      "id": "nexus-innkeep",
      "path": "npcs/nexus-innkeep.md"
    }
  ]
}
```
