# FIELD WORK reference action

`reorient_field_node` implements only FIELD ENGINEER's fully specified reference:
an already deployed FIELD SENSOR / RELAY NODE changes declared physical orientation
from NORTH to EAST. Baseline TECH pays 3 AP, or 2 AP with
`specialty.field_engineer.entry.field_work`. These are the note's prices, not a
Lab discount or new balance seed. TECH SYSTEMS HANDLING and OPERATOR do not apply
another discount. The tool accepts no replacement price or arbitrary configuration
patch; existing GM-final-total weapon handling tools remain unchanged.

Use existing campaign `entities` for the actor and actual node. As with carried
firearms, the actual tool is nested in its carrier (`engineering_tool`); existing
mounting hardware is nested in the device (`mounting_hardware`). This
action creates no objects and provides no inventory or ownership editor. Author:

- Device: `kind: FIELD_SENSOR_RELAY_NODE`, its actual `id`, `active`,
  `already_deployed`, `map_id`, `position_cell`, and
  `physical_configuration.orientation`. Other configuration and capabilities stay
  untouched. The action does not interpret the compass label as a new map FOV,
  sensor range, world-axis convention or network operation.
- Device `field_work`: `compatible`, `qualified_actors` (the actors with actual
  technical capability for this task), `physical_access`, `tool_id` and
  `material_id`. Physical access maps each authorized actor ID to the actual
  `actor_cell` and `device_cell` for which access has been established. Changed
  positions invalidate that assessment. Missing access rejects even when network
  privilege exists. The worker must be an active, non-downed TECH on the current
  map. This bounded action invents no ENGINEERING threshold, skill grant, reach
  distance or extra CHECK; the GM authors those actual prerequisite facts.
- Tool: actual carried object with `kind: ENGINEERING_TOOL`, `id`, `active`, `carrier_id`,
  `location: READY`, and `compatible_devices` including this device ID. STOWED and
  QUICK_ACCESS tools require separate retrieval; no retrieval cost is invented.
- Material: actual installed object with `kind: MOUNTING_HARDWARE`, `id`, `active`, and
  `installed_on` equal to this device ID. This reference uses the existing mount;
  it neither consumes nor manufactures replacement parts. Tasks requiring new
  components, material consumption or repair amounts remain outside this action.

These records express existing fictional equipment, competence and access. Merely
selecting the Entry does not supply them. Optional typed equipment records survive
ordinary campaign save, export and recovery. Existing character schemas and
unrelated equipment stay valid. Runtime guards recheck existence, readiness,
compatibility, deployment, local access, competence, orientation and affordability.

The map's **Reorient field node east** action binds a controlled actor and an
existing visible device through the ordinary device picker. Preview and queue
use the same checks and price as the owning action and do not spend resources.
Chat/GM invokes the same owning tool. Expected orientation and campaign revision
prevent stale application. The existing durable action receipt prevents duplicate
spend on replay; a fresh action against the already reoriented node rejects.

Ordinary deployment, FIELD REPAIR, RAPID DEPLOYMENT, FIELD MODIFICATION, OVERDRIVE
and FIELD WORKSHOP gain no implementation or price from this reference action.
Native/UI input, physical devices, full specialty coverage and final balance
remain separate qualifications.
