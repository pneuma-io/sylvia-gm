# BREAK CONTACT: quiet equipment reference

`attempt_break_contact(actor, activity_id)` performs one admitted stationary
activity: keeping already-held loose metal equipment quiet while enemies search
and listen. It uses the existing active STEALTH check. The ordinary cost is 2 AP.
A legal GHOST with BREAK CONTACT pays 1 AP and applies −2 difficulty to its next
attempt after all represented exact visual observers genuinely lose physical LOS.
The package reads the existing parameter definitions; this adds no balance values.

The activity does not move, reload, ready/stow, change inventory, open a door or
compose another paid action. Successful quiet activity preserves LAST KNOWN and
its old exact position. Failure supplies only the authored localizable noise
region to its actual listener, retaining stronger evidence and provenance.
It never creates team knowledge, an exact position, invisibility, a staleness
timer or an automatic attack reveal.

## Authoring and control boundary

An encounter author supplies optional `state.break_contact` through the existing
`CampaignCreate` / `StateValue` path, after the existing Plan starting-state
validator checks the payload against this exact package. The low-level trusted
`CampaignCreate` API does not itself enforce those declarations; it is not a
player fact-authoring endpoint. The typed records bind actual
actor/observer identities, explicit current sensory origins and pose bases,
per-observer knowledge/provenance, already-held equipment, map/revision/stationary
position, uncertainty, aptitude/base difficulty and the failure listener/region.
Those are encounter facts, not character defaults or player-supplied parameters.
The module's `state_schemas` declaration and `tests/test_td_break_contact.py`
describe the complete reference shape and its synthetic starting facts.

The ordinary map preset **Keep held metal quiet** references activity ID
`quiet-metal`. Other admitted IDs can be called through the owning tool or a
Plan's own action preset. There is no general opportunity creator/editor,
automatic activity selector, sensor/acoustics solver or broader stealth workflow.
Absent, consumed, stale, unsupported or unaffordable activities stay unavailable.

The world-state root uses the existing `private_state.v1` engine visibility.
The model/public state projection omits it; existing map, status and network
panels continue to project their explicit filtered fields. Public receipts
publish only the performed outcome, check total/difficulty and AP spent. Hidden
origins, observers, noise regions and provenance are not spatial-query inputs.

## Supported physical proof and lifetime

For supported direct visual sources, the declared origin must remain within the
observer's actual body and match its current pose basis. One actual active opaque
box plane must separate that origin from the target's entire body enclosure and
intercept all eight enclosure corners strictly within its bounds. A clear ray to
the target center, excluding the observing body itself, can establish continuing
direct sight. Other opaque bodies are conservatively enclosed; unknown volumes,
sensors, stale origins and inconclusive visibility never establish contact loss.
Zero sampled exposure is insufficient. Slits and self-body occlusion cannot grant
the Entry. This deliberately does not resolve every possible geometry/sensor.

Successful scene-changing package actions refresh represented contacts without
rolling or spending. Proven blocked CONFIRMED sources become LAST KNOWN
immediately; proven direct reacquisition becomes CONFIRMED. Unsupported sources
retain their information. The next-use receipt belongs to an actual all-lost
event, survives save/reopen/recovery and is consumed by the performed attempt,
including failure. Repeated requests, turn changes or repeated blocked checks
cannot re-arm it. A later supported reacquisition and loss can form a new event.

The reference admits at most eight actor records, sixteen observers per actor
and 128 provenance entries per observer. A full evidence record refuses another
activity without spending. Execution remains subject to the host's unchanged
atomic effect budget. Existing campaigns need not contain this optional state.
Higher EVADER tiers, general evidence sharing/staleness, maintaining observation
between action boundaries, native/device/provider acceptance and full cast
playability remain outside this bounded reference proof.
