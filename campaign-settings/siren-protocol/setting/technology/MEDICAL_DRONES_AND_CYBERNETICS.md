# Medical, Drones, and Cybernetics

**Status:** Siren Protocol v0.1 authored implementation layer  
**Rules authority:** Trigger Discipline

Siren Protocol's 2225 technology is dense and capable, but every device still has to do something physically or informationally meaningful. A futuristic appearance is not permission for unexplained bonuses.

## Medical equipment

### CALYX Field Medical Kit

Siren Protocol's common portable MEDKIT profile:

- BURDEN 1;
- 6 supply units;
- 1 AP to open/use from QUICK ACCESS;
- 2 AP from ordinary STOWED access before treatment;
- substantial treatment normally consumes 1 supply;
- supports ordinary battlefield interventions such as hemorrhage control, airway management, dressings, splinting, injectors, and STABILIZE when the actual condition is addressable;
- does not by itself contain the equipment assumed for baseline RESUSCITATION after vital failure.

The kit creates capability, not a generic MEDICINE bonus.

### SPHN Trauma / Resuscitation Kit

The larger advanced reference package:

- BURDEN 3;
- 12 supply units;
- 2 AP setup before advanced use unless already deployed;
- major trauma/resuscitation normally consumes 2 supplies;
- ordinary supported treatment consumes 1;
- includes advanced airway, hemorrhage-control, fluids/blood-equivalent, monitoring/defibrillation, and serious wound-care capability.

It supports serious TRAUMA treatment and RESUSCITATION only when the cause is physically reversible and the necessary intervention is actually represented.

SPHN and CALYX both field products and protocols in this space. MORROW remains a major medical corporation without owning all medicine in the setting.

## FPV drones

### ASTER Kite FPV Drone

The Kite is the v0.1 manual FPV reference:

- DURABILITY 4;
- 12m movement per operator AP spent manually controlling movement;
- 1 operator AP per AP-equivalent manual drone action;
- 300-second active battery;
- reference signal: 300m through ordinary urban clutter / 800m open terrain before environment;
- ordinary visual camera;
- clear-condition observation reference: roughly 60m CONFIRMED, 150m APPROXIMATE when target size/visibility support it;
- roughly 1kg payload;
- **no default weapon**.

A payload still needs its own rules object. Attaching a physical item does not make the drone an extra character turn.

## Autonomous drones

### EMBERLINE Oriel Autonomous Drone

The Oriel is the v0.1 autonomous reference used by Nova:

- DURABILITY 8;
- 8m autonomous movement per AP-equivalent execution;
- issuing/materially changing a STANDING ORDER costs 1 operator AP;
- while a valid order persists, it can execute up to 2 AP-equivalent simple movement/observation actions per TEAM TURN without further operator AP;
- 900-second active battery;
- reference signal: 500m urban / 1500m open terrain before environment;
- visual + low-light/thermal sensors;
- clear-condition observation reference: roughly 80m CONFIRMED, 250m APPROXIMATE when resolvable;
- roughly 3kg payload;
- **no default weapon or attack routine**.

AUTODRONES can make standing orders more useful and coordinate several machines, but the drones remain limited systems. Their autonomy does not give Nova extra player-character turns.

### Armed drone boundary

The first Siren Protocol implementation deliberately ships no armed Oriel/Kite module. Current Trigger Discipline conformance explicitly records reference-drone armed actions as unsupported because no default weapon/module exists.

A future armed-drone module must define a real weapon/payload and then use valid DRONE, attack, PREP, OVERWATCH, ammunition, geometry, and action-economy rules. Do not solve the gap by saying 'the drone shoots.'

## Field sensor and relay

### EMBERLINE Mesh Relay / Field Sensor Node

- BURDEN 2;
- DURABILITY 6;
- setup 3 AP normally;
- TECH SYSTEMS HANDLING reference setup: 2 AP;
- 1200-second battery;
- 25m local motion/thermal detection radius where geometry permits;
- direct visual confirmation reference to ~40m with valid LOS;
- extends a connected friendly signal path by up to 150m when both links are real;
- a physical, hackable NODE;
- no weapon.

Walls, power loss, destruction, jamming, lost links, or hostile access can defeat it.

## Specialist physical kits

### PALISADE Breach Kit

A collection of physically appropriate entry tools and consumables. It does not grant a blanket breaching bonus merely because it is expensive or branded. The barrier material, method, BREAKER specialty, and relevant ENGINEERING/other rules determine the result.

### Infiltration Tool Kit

Lock/access tools, compact cameras, line, cutters, and similar physical aids. It creates plausible ways to manipulate or observe real objects; it does not add an unexplained STEALTH bonus.

### Portable Intrusion Rig

A field set of cables, radios, local interfaces, compute, protocol tools, and diagnostic hardware. It can provide a plausible route to a NODE, LINK, interface, or exposed service. It cannot invent a credential, privilege, wireless route, remote function, or hardware capability that does not exist.

## Cybernetics design rule

Cybernetically augmented people remain biological humans. Cyberware may create new physical sensing, communication, identity, control, or prosthetic capability, but **no v0.1 cybernetic below provides a generic flat CHECK bonus or extra AP.**

Where a cybernetic mechanical advantage is needed later, it must name a real Trigger Discipline quantity/state/action or be written as an explicit Siren Protocol extension.

### LATTICE CivicLink

Identity, payment, credential, communication, and trusted-service interface.

It can authenticate or transact when the underlying system accepts the user's authority. It does not automatically hack systems, persuade people, bypass gates, or create credentials.

### PALISADE TacLink

Weapon/armour HUD, squad-data, and tactical information interface.

It can display information supplied by real sensors and systems. It does not AIM for the user, erase RECOIL, ignore COVER, or create more AP.

### MORROW Multispectrum Optics

Low-light, thermal, rangefinding, and enhanced visual sensing.

The augmentation changes what the character can physically perceive when the sensor can resolve it. It does not automatically negate RANGE difficulty, AIM requirements, COVER, smoke effects outside sensor capability, or uncertainty.

### MORROW Medical Telemetry Suite

Vitals and compatible-implant telemetry for the user and connected authorized equipment.

It can provide real observable medical data. It cannot automatically detect a hidden condition the sensors cannot measure or make treatment succeed.

### VECTORLINE DriveLink

Direct control interface for compatible vehicles.

It provides a real vehicle-control route. It does not grant a generic VEHICLES bonus, additional actions, or perfect control of an incompatible or compromised vehicle.

### EMBERLINE CommandMesh

Drone-command and sensor-fusion interface used to manage standing orders, feeds, and compatible unmanned systems.

It does not grant free drone actions, invent sensor coverage, create signal through absent links, or turn an unarmed drone into a weapon.

### IRONLACE Reinforced Prosthetic Limb

A high-end durable replacement limb compatible with Siren Protocol's advanced medicine and cybernetics.

The v0.1 profile deliberately gives it **no automatic FORCE, CQC damage, movement, carrying, or AP increase**. A replacement limb can be excellent without every prosthetic becoming a super-strength upgrade. If a future military or industrial augmentation genuinely exceeds human performance, author the exact mechanical delta explicitly.

## Hacking boundary

Intended operation and repair belong to TECH/appropriate SKILLS. Subverting a system's security belongs to HACKER.

Having a direct implant connection to a device does not mean the user owns its privilege. Likewise, hacking a cybernetic does not give the attacker abilities the hardware never possessed.
