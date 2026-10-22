# Core Cast — Playable Builds v0.1

**Status:** authored implementation defaults, not immutable character canon  
**Ruleset:** Trigger Discipline  
**Default profile level:** 5

The individual character files define who the eleven women are. This document defines one editable mechanical template; complete starting resources are still pending. Campaign Plans may change level, side, SKILLS, APTITUDES, SPECIALTY path, loadout, or starting resources without retconning character biography or visual identity.

## Build scale

The first profile uses one primary SKILL 5, one secondary SKILL 3, two familiar SKILLS 2, one APTITUDE 1, and five SPECIALTY choices (Entry + two Tier-1 + two Tier-2). This uses the Trigger Discipline Level-5 benchmark as a tested ruler; the benchmark itself explicitly is not final player-creation/progression canon.

| Character | Runtime build | Skills | Aptitude | SPECIALTY choices | Default identity/loadout |
|---|---|---|---|---|---|
| **Vesper** | RIFLEMAN / OPERATOR | MARKSMANSHIP 5; TACTICS 3; RECON 2; CQC 2 | PRECISION 1 | WEAPON MANIPULATION; RETENTION RELOAD; TRANSITION DRILL; SHOULDER SWITCH; SELECTIVE LOAD | R55, P15, FlexPlate, Fog-8, TacLink, MORROW optics |
| **Riot** | BREACHER / BREAKER | CQC 5; GRENADES 3; ATHLETICS 2; MARKSMANSHIP 2 | FORCE 1 | TACTICAL BREACHING; READ THE BARRIER; FOCUSED BREACH; BREACH AND FLOW; CONTROLLED BREACH | B12, P15, FlexPlate, F7s, Flash-4s, Breach Kit, TacLink |
| **Solace** | CORPSMAN / FIELD MEDIC | MEDICINE 5; INVESTIGATION 3; SOCIAL READ 2; ATHLETICS 2 | REASON 1 | COMBAT MEDICINE; RAPID STABILIZATION; KIT AT HAND; PATCH AND MOVE; GET THEM UP | P15, FlexPlate, CALYX Field Kit, SPHN Trauma Kit, medical telemetry |
| **Blaze** | GHOST / EVADER | VEHICLES 5; STEALTH 3; RECON 2; MARKSMANSHIP 2 | PRECISION 1 | BREAK CONTACT; VANISHING MOVE; CUT THE CORNER; EXTENDED REPOSITION; HARD TO REACQUIRE | K30, P15, Civicweave, Fog-8, DriveLink, Cinder R |
| **Sable** | SHARPSHOOTER / SENTINEL | MARKSMANSHIP 5; RECON 3; INVESTIGATION 2; STEALTH 2 | ACUITY 1 | PREPARED SIGHTLINE; BROAD SECTOR; STABLE NEST; CROSSING SHOT; SECOND TRIGGER | M7, P15, FlexPlate, Fog-8, MORROW optics |
| **Velvet** | INFLUENCER / ICON | INFLUENCE 5; SOCIAL READ 3; INVESTIGATION 2; RECON 2 | PRESENCE 1 | RECOGNITION; READ THE AUDIENCE; PUBLIC INTRODUCTION; MAKE AN ENTRANCE; CROWD MOMENTUM | P15, Civicweave, CivicLink |
| **Pixie** | GHOST / INFILTRATOR | STEALTH 5; RECON 3; ATHLETICS 2; CQC 2 | ACUITY 1 | APPROACH PLAN; TIMED CROSSING; QUIET HANDS; CLEAN ENTRY; DISTRACTION WINDOW | K30, P15, Civicweave, Fog-8, Infiltration Kit, CivicLink |
| **Hex** | HACKER / NETWORKER | NETWORKING 5; LOCAL HACKING 3; INVESTIGATION 2; ENGINEERING 2 | REASON 1 | MAP THE LINKS; PIVOT; QUIET ENUMERATION; SEGMENT MAP; TOKEN RELAY | K30, P15, Civicweave, Intrusion Rig, CivicLink |
| **Kora** | BULWARK / GUARDIAN | TACTICS 5; CQC 3; ATHLETICS 2; MARKSMANSHIP 2 | NERVE 1 | INTERPOSE; BODY COVER; PULL BEHIND; DEFENSIVE EXCHANGE; PROTECT THE OPERATOR | R55, P15, Bastion armour, Wall-4 shield, Flash-4s, Fog-8, TacLink |
| **Siren** | INFLUENCER / HANDLER | SOCIAL READ 5; INFLUENCE 3; INVESTIGATION 2; RECON 2 | PRESENCE 1 | WORK A CONTACT; VET THE SOURCE; DISCREET CHANNEL; INTRODUCTION; CUTOUT | P15, Civicweave, CivicLink |
| **Nova** | TECH / AUTODRONES | ENGINEERING 5; DRONE OPERATIONS 3; TACTICS 2; NETWORKING 2 | REASON 1 | TACTICAL AUTONOMY; CONDITIONAL ORDER; AUTONOMOUS REPOSITION; SECOND ELEMENT; SECTOR NETWORK | K30, P15, FlexPlate, 2 Oriel drones, Mesh Relay, CommandMesh |

## Intentional exceptions and boundaries

**Blaze:** her setting/fantasy anchor remains mobility / `VEHICLES`. GHOST/EVADER is the v0.1 **on-foot runtime Class expression** because Trigger Discipline has no driver Class. GHOST abilities do not improve a vehicle's movement. Vehicle operation comes from VEHICLES 5, the actual vehicle, and the situation.

**Siren:** her setting anchor remains social / HUMINT alternate. INFLUENCER/HANDLER is the closest current runtime expression of contacts, informants, favors, introductions, and private information networks.

**Hex:** the existing five-node build is preserved. Current TD includes a FOOTHOLD hook; its availability does not automatically replace one of Hex's selected nodes.

**Nova:** the two Oriel autonomous drones are unarmed. No default weapon module, free attack routine, or extra character turn is implied.

## Equipment and rules boundaries

- `side: allies` in machine artifacts is an import convenience only.
- Cyberware provides real sensors/interfaces/control routes, not flat CHECK bonuses or extra AP.
- The M7 retains its real manual cycle requirement; the B12 retains tube/chamber state.
- BURST/FULL AUTO physical modes exist where listed, but BURST/FULL AUTO resolve through the contained TD package when its actual weapon and ammunition prerequisites hold.
- Tactical vehicle combat is not added in v0.1.
- Projected barriers exist through BARRIER/COVER logic, but their Siren Protocol numeric profile remains deliberately unstandardized.
- HP/base RES/AP and numerical progression await Wan's explicit Setting decision. Retrieve the current character-creation contract and identify missing inputs; neither benchmarks nor these templates supply the missing numbers.

Machine-readable artifacts and exact equipment IDs live in [`../implementation/characters.json`](../implementation/characters.json) and [`../implementation/equipment.json`](../implementation/equipment.json).
