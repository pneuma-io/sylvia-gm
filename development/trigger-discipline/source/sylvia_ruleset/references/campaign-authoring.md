# Trigger Discipline campaign authoring

Use this reference when creating a Trigger Discipline Campaign Setting or Campaign Plan with a tactical map and selectable party. It describes the Sylvia package interface; the atomic TD notes remain the rules authority. Read the selected library records and their owned files through Sylvia's Builder inspection tools before writing. Use the Setting's authored people and equipment values.

For Character Files and starting-character choices, read `references/character-authoring.md` before assigning mechanical values.

## Package ownership

- The host is ruleset-neutral. It stores packages, substitutes selected participant IDs, copies initial state, and runs declared tools and surfaces. It does not infer a TD map, party position, turn roster, weapon, or network.
- `pneuma-io.trigger-discipline` is the TD Ruleset. Its format-4 package record has no Setting dependency.
- The new Campaign Setting owns its world content and a saved copy of the TD Ruleset. The new Campaign Plan owns a saved copy of that Setting and its Ruleset copy. Keep these bindings closed and exact.
- Create authored Setting files for the location, characters, and equipment the campaign needs. Do not import `conformance/scenario.json` or any conformance fixture as campaign state.

## Authoring sequence

1. Inspect available Ruleset and Setting sources. Read the TD identity, state declarations, character schema, runtime files, and the owned Setting documents relevant to the chosen party and location.
2. Create or edit the Setting so it contains the actual location, characters, equipment, and world facts. Use stable IDs and values found in the runtime or owned Setting documents. Do not turn test-reference values into TD defaults or invent TD rules, equipment statistics, or canon.
3. Create `campaign-plan-draft/plan.json` and `plan.md` under Sylvia's Builder workflow. Keep the Plan's Ruleset, Setting, and closure bindings exact.
4. Put the playable starting world in `initial_state.mechanics`. Narrative `entities` and `facts` do not create tactical state.
5. Put one `initial_state.party_placements` slot per selectable party position, ordered Player Character first and Companions in selection order. Author enough slots for the maximum permitted party.
6. Validate and publish through the normal Campaign Plan tools. Correct schema and reference failures before calling the Plan ready.

## Initial mechanics

Use namespace `pneuma-io.trigger-discipline`. The two roots needed for an ordinary mapped firefight are:

```json
{"namespace":"pneuma-io.trigger-discipline","key":"state","value":{
  "decision_owner":"player","active_side":"allies","focused_actor":null,
  "eligible_actor_ids":[],"turn_roster":["authored-hostile-id"],
  "spatial_scene":{"format":1,"map":{"id":"authored-map-id","revision":1,
    "bounds":{"min":[0,0,0],"max":[20,20,3]},
    "grid":{"cell_size":1,"unit":"m","origin":[0,0]},
    "layers":[{"id":"floor","min_elevation":0,"max_elevation":3}]},
    "geometry":[],"entities":[],"zones":[],"terrain":[]},
  "selection":{},"preview":{},"prepared":{"overwatch":{"active":false,"owner":null,"zone_id":null,"shots":0},"suppression":{"active":false,"owner":null,"zone_id":null,"capacity":0}},
  "ground_objects":{},"ground_object_sequence":0,"network_motion":{},
  "objectives":[],"last_move":{},"last_attack":{},"last_trigger":{}}}
```

Add a second mechanics entry with key `entities`; its value maps each authored NPC ID to that NPC's TD game-state object. Add other declared state fields only when the chosen tools or surfaces require them.

Use one-metre cells for the current same-level map. Read editable cover-height presets from `definitions.authoring_defaults.cover_height_presets` in `runtime/ruleset.yaml`. Geometry owns its real dimensions, material data, movement, visibility, active state, and destroyed state.

For an ordinary one-cell crate, use a single box from `[14,6,0]` to `[15,7,1.2]`,
`occupies_cells: true`, and player `height_label: Half`. One geometry identity owns its
material, cover HP and destroyed state. A 1.2 m box centered across grid intersections can
reserve four cells even if it looks like one small prop. Confirm intentional overlap or align
both the origin and size to the intended footprint. Existing maps keep their authored bounds.
`check_campaign_plan_draft` reports advisory multi-cell warnings for offset blocking props;
deliberately larger props and physical barriers remain valid.

Use `occupies_cells: false` for a thin fence whose physical thickness should constrain
clearance without reserving whole neighboring cells. Author its actual endpoints, height,
material, movement and visibility separately: a see-through fence can still block movement
or stop a projectile. Never stretch its coverage to a whole cell in presentation or rules.

Full, Half and Quarter are editable height labels (2.4, 1.2 and 0.6 m), not protection
percentages. Stance, body and muzzle height, firing angle and the actual intersection decide
exposure. Keep numeric dimensions available for inspection. Set `metadata.player.material_label`
to the material description the player may know (for example, `Material resistance 3` when that
rating is public). Keep it in sync with the authored material. Private material metadata is not
automatically shown; omit the public label when the material is unknown. This label is descriptive
text, not a replacement for the numeric `material_resistance` used by projectile resolution.

## Party placement slot

Each slot has exactly `entity_state` and `append`. Use participant markers as the entire value so Campaign Start can bind the selected character:

```json
{
  "entity_state":{"id":{"$participant":"id"},"name":{"$participant":"name"},
    "side":"allies","active":true,"downed":false,
    "position_cell":[2,2],"center":[2.5,2.5,0.9],"muzzle":[2.91,2.5,1.4]},
  "append":[
    {"namespace":"pneuma-io.trigger-discipline","key":"state.spatial_scene.entities","value":
      {"id":{"$participant":"id"},"map_id":"authored-map-id","position":[2.5,2.5,0],
       "footprint":{"kind":"box","half_extents":[0.4,0.4]},"height":1.8,"facing":[1,0],
       "active":true,"movement":"blocking","visibility":"opaque",
       "metadata":{"player":{"label":{"$participant":"name"},"kind":"unit","stance":"standing","side":"allies"}}}},
    {"namespace":"pneuma-io.trigger-discipline","key":"state.turn_roster","value":{"$participant":"id"}},
    {"namespace":"pneuma-io.trigger-discipline","key":"state.eligible_actor_ids","value":{"$participant":"id"}}
  ]
}
```

Replace the illustrative position and state values for each slot. Preserve the selected character's owned mechanical fields unless the placement needs a complete top-level replacement. `entity_state` updates whole top-level fields; it does not merge nested fields.

`state.turn_roster` contains every authored NPC actor ID plus participant markers appended by the chosen-party slots. Start `state.eligible_actor_ids` with any authored allied actors who may act immediately, then append the chosen party IDs. End-turn helpers rebuild eligibility from the roster: an actor must match the next side, be active, be non-downed, and have `max_ap > 0`; eligible actors receive `ap = max_ap`, and the others receive zero AP.

## Map and actor consistency

Sylvia displays increasing X to the right and increasing Y downward. Movement destinations are
zero-based `[x, y]` cells: one cell down from `[3,2]` is `[3,3]`. State the map's compass orientation
in the authored location and keep named edges, entrances, obstacles, starting placements and
narrative descriptions consistent with it. For a north-up map, north decreases Y and south
increases Y. These are display coordinates; they do not choose the world's compass orientation.

Every acting entity needs one ID shared by the `entities` dictionary key, its game-state `id`, the tactical scene entity `id`, and `turn_roster`. Eligibility lists use those same IDs for actors allowed to act now. Its `position_cell`, `center`, and `muzzle` must agree with the tactical entity's map position. Use the same `map_id` on the map, geometry, scene entities, and zones.

For move, fire, and turn handling, a normal combatant's game state commonly needs `id`, `name`, `side`, `active`, `downed`, `ap`, `max_ap`, `hp`, `res`, `stance`, `position_cell`, `center`, `muzzle`, `movement_m_per_ap`, `skills`, `aptitudes`, `specialties`, `weapon`, and `aim`. The weapon needs the fields required by the selected fire and reload tools, including its authored damage and penetration values, range data, recoil state, magazine/chamber state, mechanism, feed, AP costs, location, controller, and fire modes. Copy actual character and equipment values from admitted runtime or owned Setting documents.

Store complete combatant state in the Character File's `entity_state`. Its separate `participant` object contains only the fields admitted by TD's closed character schema. Placement slots supply position and identity fields when Start copies that character.

The matching tactical entity needs `id`, `map_id`, `position`, `footprint`, `height`, `facing`, `active`, `movement`, `visibility`, and suitable player metadata. Inspect selected runtime queries before authoring geometry or zones.

## Authored actions and remaining fixed-scenario boundary

Fire tools operate on the selected actor, target and current map. They do not write campaign
objectives or require a conformance door/hostile. The authored Plan and GM own mission progression.

TD network tools now take authored node/function IDs. `network_connection` explicitly connects
or disconnects an actor at a stationary local interface; connection alone grants no privilege.
`intrude_network` resolves GET_IN,
DISCOVER, ESCALATE or FOOTHOLD; `pivot_network` takes `route_step: [source, target, link]`.
`operate_remote_function` applies one authored geometry patch through the same map engine used
by movement and fire. It does not complete campaign objectives.

Network implementation is still partial. Do not treat this checkpoint as finished network
simulation or final security tuning. The package currently supports routes of at most eight
nodes. Single-session ROUTE PERSISTENCE and explicit remote-service connections are supported
as described below; whole-chain REROUTE, moving local interfaces and the remaining specialty
hooks need implementation and acceptance.

The installed support declarations distinguish these boundaries: MAP THE LINKS automatically
reveals identifiable adjacent topology after successful entry (including a pivot), not after
every unrelated network action. PIVOT and FOOTHOLD use the real route/access/persistence tools,
but their specialty discounts remain GM-adjudicated with explicit values. ROUTE PERSISTENCE
checks the actual specialty and surviving route; its reduced cost must also be authored.
Historical counterfactual coverage is separate from these gameplay support declarations.

The private state records are separate from the public `network` projection:

- `network_intrusions[node].actions[action]` supplies `ap`, `check_required`, `difficulty`,
  `privilege` and `reveal_links`. `visible_links` supplies the node's identifiable neighbors for
  MAP THE LINKS. Use actual authored values; the conformance fixture supplies no campaign defaults.
- `network_topology.nodes` and `.links` hold `online` and `generation`; each link has its own
  `barrier` using the same action fields. A permitted/authorized link uses `check_required: false`.
- `network_interfaces[node]` declares the supported `kind: local_stationary`, `active`,
  `generation`, exact `map_id`/`position_cell` and authored `ap`. A visible node alone grants no
  entry. `network_connection` requires the actor at that cell and the node/interface online.
- For a genuinely remotely reachable service, use `kind: remote_service` with `active`,
  `generation`, explicit `ap` and `connections: {actor_id: {active, generation}}`. This records
  the actual actor-to-service connection, not a list of actors who merely know the node.
  Its generation must be positive and advance whenever that connection ends or is replaced.
  Remove/deactivate it when connectivity is lost. The package does not infer Internet access,
  radio range, mobile local access, credentials or privilege from the service label. Moving on
  the tactical map does not itself end a remote connection that remains physically valid.
- `network_entries[actor].nodes[node]` is written by connection tools and records its generation,
  interface/node generations and the actor's movement generation. Ordinary movement, reaction-fire
  movement and forced displacement advance `network_motion[actor]`, including a walk away and back.
  Initialize `network_motion: {}` for network actions. Ordinary physical movement also creates its
  actor counter as needed; scenes without networks do not need a pre-existing motion map.
  Reconnect a local interface after moving; reconnect either kind after disconnecting or
  replacing its interface/node, then establish fresh access.
  Disconnect remains possible when the device goes offline and affects only that actor's entry.
  Remote entries additionally save `connection_generation` and reject access when it differs
  from the actual service connection. Reconnection does not restore expired sessions.
- `network_sessions[actor].nodes[node]` is written by successful access tools. It holds privilege,
  entry generation, ordered node/link IDs, their generations, and actor-owned session generations.
  Replacing a parent session expires its old dependent grants; copied state cannot revive them.
  Its friendly `route_label` supplies a read-only actor-specific panel preview after the owner
  validates an available action; it does not expose private topology or grant permission. A selected
  function shows its public label, actual AP cost and permission to use that function without an
  intrusion check. This does not grant or claim control of other functions on the device.
- `preserve_network_route` uses the usual `route_step` and requires the actual ROUTE PERSISTENCE
  specialty plus explicit `network_intrusions[node].reentry.ROUTE_PERSISTENCE.ap`. It can preserve
  one existing session after exactly one intermediate link loss using a known, valid alternative
  under the same entry. Existing node/session generations must survive; it cannot resurrect ended
  access. It preserves privilege and makes no intrusion roll. Whole-chain REROUTE is not implemented.
- `network_functions[node].functions[function]` supplies the required privilege, exact AP cost,
  `kind: geometry`, `map_id`, `geometry_id`, and an `active`/`movement`/`visibility` patch. Its public
  counterpart supplies only the label, visibility, enabled state and display state.
- `network_credentials[id]` supplies `active`, `generation`, `held_by` (actor to material generation)
  and `grants` (node to privilege). The GM authors legitimate acquisition; knowing an ID alone is
  not possession. Revocation/rotation makes saved material invalid without erasing the acquired data.
- GET_IN and PIVOT accept `method: INTRUDE | CREDENTIAL | FOOTHOLD` (default INTRUDE). CREDENTIAL
  also requires `credential_id`. Each non-intrusion method needs an explicit
  `network_intrusions[node].reentry[method].ap`; optional visibility uses the same monitoring path.
  Authentication and surviving footholds make no intrusion roll. All ordinary connection/route,
  ownership, online-state and resource prerequisites still apply. No cost is inferred from a specialty.
- Establishing FOOTHOLD requires an explicit action-rule `persistence: {method, generation}` and
  may retain at most the actor's current privilege. The resulting `network_footholds[actor].nodes[node]`
  contains `active`, `method`, `method_generation` and privilege. Re-entry checks this actual method;
  a method change, remediation generation or removal invalidates it. Ordinary node restart alone
  does not erase an implant. Authors must advance its persistence generation when the method's
  stated remediation occurs, including a rebuild that removes it. A real route is still necessary.
- `network_monitors` is an array (at most 64). Each record supplies `id`, `active`, `defender`,
  covered `nodes`/`links`, observed `actions`, `notice: CERTAIN | UNCERTAIN`, `evidence` (1 suspicious,
  2 located, 3 confirmed), and nullable `response`. An uncertain observation additionally supplies
  `skill`, `aptitude`, `difficulty` and explicit LOW/STANDARD/HIGH `visibility_modifiers`. The action
  rule can supply `visibility`; canonical STANDARD applies when absent. No numerical monitoring
  modifiers or difficulties are campaign defaults. Coverage is independent of intrusion success;
  omit harmless authorized operations from a monitor's actions unless actual evidence warrants it.
- `network_defenders[id]` supplies `active`, `operator`, private `awareness` and `responses`.
  Awareness is separate per actor/defender and retains stronger evidence. Each response declares
  `mode: AUTOMATIC | ACTOR`, `kind: END_SESSION | ISOLATE_LINK | SHUTDOWN_NODE | REVOKE_CREDENTIAL |
  REMOVE_FOOTHOLD`, an actual `target`
  node/link and `min_awareness`. ACTOR responses also require explicit `ap`. Only a monitor's
  authored response trigger runs automatically; `respond_network_defense` is GM-controlled and
  checks the actual operator's turn, condition, resources and evidence. Credential revocation and
  foothold removal block future authentication; END_SESSION independently ends a current session.
  Other defensive responses
  remain GM-adjudicated until their state transitions are supported. Private defender facts are
  never included in the public graph or inferred as a player-visible alert meter.

Use stable IDs without dots or template braces. Do not put private rules or defender records
inside node/function presentation records, including hidden ones. Plan admission checks every
authored presentation record before discovery, rejects dangling links/groups and verifies physical
bindings against declared tactical surfaces and their present authored entities. It does not require
a runtime-created map to exist in initial state when no authored binding needs it.

TD declares `state_schemas` with the `starting_state_validation.v1` host requirement. Ordinary
Plan checks validate private network field types, required collections, explicit nonnegative
costs, positive generations, interface-specific fields and bounded session/monitor lists.
References connect private nodes/links/functions to the authored graph, preserve each function's
node scope, and require an authored geometry effect to identify the current map and geometry.
Hidden presentation records receive the same structural checks. Worlds without networks need no
network defaults; ordinary movement counters can exist independently.

This is authoring validation, not an access grant or a simulation of defender decisions. Session
route continuity, live generation equality, actor ownership, conditional response targets and
monitor/response semantics still use the runtime guards and author review. A Plan must not rely
on the validator to invent a connection, access privilege, balanced cost or repair. Errors return
field paths and corrective messages without echoing private values. Synthetic source tests use a
separate dock console/floodgate scene; installed play and live monitoring remain unverified.


Prepared-fire actions bind the selected public zone from the current map. The runtime rejects
a missing zone before spending resources or replacing existing preparation. Drone controls bind
the selected actor, device and target; the runtime checks actual control and device type. Selecting
a device grants no permission. Author their matching game-state and spatial identities normally.
