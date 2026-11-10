# Shooter-origin Overwatch geometry

`prepare_overwatch_lane` uses the existing setup cost, shot allowance, weapon checks,
incoming preparation exposure and ordinary reaction attack. It does not assign class
grants, width penalties, ranges or new numerical balance values.

Supply `actor` and `lane`, a one-element array containing a closed declaration:

```json
{"pattern":"triangle","direction":"east","extent":6,"half_width":2,
 "origin":[2.5,2.5,0],"facing":[1,0]}
```

Those dimensions are an example fixture, not a canonical grant. Patterns are `lane`
and `triangle`; directions are east, south, west and north (positive y is south).
Width is `2 * half_width + 1` cells. Extent counts forward cells. Step zero contains
only the shooter's cell. At forward step `f` from 1 through extent, a triangle covers
integer lateral offsets satisfying `abs(side) * extent <= half_width * f`.
The included cell volumes have closed boundaries, as existing box zones do. A path
touching an included face or corner can therefore qualify; excluded cell centres do
not become covered merely because they lie in the bounding rectangle.

The shared engine clips complete cells to the map and uses the map layer containing
the shooter's feet for vertical extent. The 2,048 dimension and 4,096-cell limits are
execution limits, not weapon ranges. Unsupported diagonal directions and malformed,
empty-forward or stale declarations refuse. TD currently refuses shooter IDs containing
a dot because the inherited runtime uses dots as state-field path separators; this
is an execution boundary, not a class or geometry restriction. Current TD movement remains same-level;
this change does not add stairs, floors or continuous SRD positioning.

The owner derives and validates the zone from the current scene entity. The submitted
origin and facing must match it. Map selection also carries the current map revision.
Preview uses the same rasterizer and returns its cells without mutating state or RNG.
Committed shape, owner, pose and stance are retained. Reactions check that binding,
then use current LOS, cover, materials, ammunition and the existing trigger procedure.
Movement and an actual stance change break the watch at the mutation; returning
to the former cell or stance does not restore it. A fresh paid setup can
replace the same watcher's declaration. Exact command retries reuse owner receipts.

`cancel_overwatch_lane` cancels the acting watcher's preparation without a refund.
New lane watches are stored by shooter in optional `state.prepared.overwatchers`.
The original `state.prepared.overwatch` slot retains authored-zone and SENTINEL API
compatibility. Replacing a shooter's declaration through either API ends their other
watch; it never duplicates that shooter's allowance. Other shooters remain armed.

The shared path/exposure queries find each next physical trigger window. Remaining
responses are queried again after every window. Same-side responses use the trusted
owner's order choice; opposing sides use the existing non-active-side priority. Each
response checks the updated pose, zone, LOS, firearm and target immediately before
its ordinary attack. One movement route spends its existing AP price once, pauses on
incapacitation, and considers each watch at most once for that movement event. No
new reaction pool, numerical modifier or class power is introduced.


Existing boxes and cylinders are unchanged. `prepare_overwatch` remains the explicit
legacy authored-zone API for saved/authored consumers; ordinary map controls use the
new lane API. Existing package pins and saves are not rewritten. A full TD/Siren build
must be rebuilt and checked before it can adopt these source changes. The generated
Overwatch verification archive is a scoped runtime fixture, not that full distribution.

The decoupled balance suite still uses its historical angle-sector implementation.
Its consumer update, rebuild/rebinding and parity execution are deferred until the
CodexWork migration is complete. Old balance results do not qualify this geometry.
