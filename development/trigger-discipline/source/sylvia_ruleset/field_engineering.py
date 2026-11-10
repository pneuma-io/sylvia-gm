"""The authored FIELD WORK reference task, using existing campaign entities only."""

ENTRY = "specialty.field_engineer.entry.field_work"


def price():
    """The note fixes this task at 3/2 AP; neither TECH nor OPERATOR discounts it again."""
    return [
        {
            "set": {
                "field": "_field_work_ap",
                "value": (
                    "3 - occurrences(one(actor.class_id == 'TECH' and "
                    f"'{ENTRY}' in actor.specialties), true)"
                ),
            }
        }
    ]


def checks():
    """Share current physical prerequisites between direct controls and owning execution."""
    conditions = [
        ("actor.active == true and actor.downed == false", "This actor cannot work on equipment."),
        ("actor.class_id == 'TECH'", "This bounded reference action requires TECH."),
        (
            "device.id == _device_id and '.' not in _device_id "
            "and '{' not in _device_id and '}' not in _device_id",
            "Select an existing supported device.",
        ),
        (
            "device.kind == 'FIELD_SENSOR_RELAY_NODE' and device.active == true",
            "Select an active field sensor / relay node.",
        ),
        (
            "device.already_deployed == true",
            "Deploy the actual device first; this action is not deployment.",
        ),
        (
            "device.field_work != null and device.field_work.compatible == true",
            "This device does not support the reference physical reorientation.",
        ),
        ("device.map_id == map_id", "The device is not on the current map."),
        (
            "_actor_id in device.field_work.physical_access",
            "This actor lacks physical access to the mounting.",
        ),
        (
            "actor.position_cell[0] == device.field_work.physical_access[_actor_id].actor_cell[0] "
            "and actor.position_cell[1] == "
            "device.field_work.physical_access[_actor_id].actor_cell[1] "
            "and device.position_cell[0] == "
            "device.field_work.physical_access[_actor_id].device_cell[0] "
            "and device.position_cell[1] == "
            "device.field_work.physical_access[_actor_id].device_cell[1]",
            "Physical access was established for different positions; reassess the work site.",
        ),
        (
            "_actor_id in device.field_work.qualified_actors",
            "This actor lacks the authored technical capability for this task.",
        ),
        (
            "device.physical_configuration.orientation == expected_orientation",
            "The device configuration has changed; inspect it again.",
        ),
        (
            "expected_orientation == 'NORTH' and orientation == 'EAST'",
            "Only the authored NORTH to EAST reference reorientation is supported.",
        ),
        (
            "actor.engineering_tool != null "
            "and actor.engineering_tool.id == device.field_work.tool_id",
            "This actor does not carry the required physical tool.",
        ),
        (
            "actor.engineering_tool.kind == 'ENGINEERING_TOOL' "
            "and actor.engineering_tool.active == true",
            "The required engineering tool is unavailable.",
        ),
        (
            "actor.engineering_tool.carrier_id == _actor_id "
            "and actor.engineering_tool.location == 'READY'",
            "This actor must have the actual appropriate tool ready; retrieval is separate.",
        ),
        (
            "_device_id in actor.engineering_tool.compatible_devices",
            "This tool is not compatible with the selected device.",
        ),
        (
            "device.mounting_hardware != null "
            "and device.mounting_hardware.id == device.field_work.material_id",
            "The required mounting material does not exist on this device.",
        ),
        (
            "device.mounting_hardware.kind == 'MOUNTING_HARDWARE' "
            "and device.mounting_hardware.active == true",
            "The required mounting hardware is unavailable.",
        ),
        (
            "device.mounting_hardware.installed_on == _device_id",
            "The appropriate mounting hardware must actually be installed on this device.",
        ),
        ("actor.ap >= _field_work_ap", "This actor cannot afford the reorientation."),
    ]
    return [{"condition": condition, "reason": reason} for condition, reason in conditions]


def specification(actor, param):
    """Perform one declared physical configuration change inside the normal atomic action."""
    return {
        "capability": "trigger-discipline.equipment",
        "description": (
            "Physically reorient an existing deployed FIELD SENSOR / RELAY NODE NORTH to EAST. "
            "Baseline TECH pays 3 AP; FIELD WORK Entry pays 2 AP. Requires the authored "
            "technical capability, actual physical access, carried READY compatible tool "
            "and existing installed mounting hardware. "
            "Changes only physical_configuration.orientation. "
            "Does not deploy, create, repair, consume or transfer equipment, change sensor/power/"
            "network/software capabilities, or discount another TECH benefit. No final GM cost "
            "parameter: other reconfiguration tasks remain GM-adjudicated "
            "outside this reference action."
        ),
        "parameters": [
            actor,
            param("device", "string", resolve="entity"),
            param("expected_orientation", "enum", enum=("NORTH", "EAST", "SOUTH", "WEST")),
            param("orientation", "enum", enum=("NORTH", "EAST", "SOUTH", "WEST")),
            param("map_id", "string", from_state="state.spatial_scene.map.id"),
        ],
        "effects": [
            *price(),
            *(
                {"require": {"condition": check["condition"], "error": check["reason"]}}
                for check in checks()
            ),
            {
                "set": {
                    "field": "{_device_id}.physical_configuration.orientation",
                    "value": "orientation",
                }
            },
            {"decrement": {"field": "{_actor_id}.ap", "amount": "_field_work_ap"}},
            {"set": {"field": "_field_work_status", "value": "'REORIENTED'"}},
            {"set": {"field": "_field_work_spent", "value": "_field_work_ap"}},
            {
                "set": {
                    "field": "state.last_field_work",
                    "value": {
                        "status": "'REORIENTED'",
                        "actor": "_actor_id",
                        "device": "_device_id",
                        "before": "expected_orientation",
                        "after": "orientation",
                        "ap_spent": "_field_work_ap",
                    },
                }
            },
        ],
        "costs_action": True,
    }


def equipment_schemas():
    """Describe existing carried tools and installed mounts, like nested firearm objects."""
    from .network_state_schema import _array, _enum, _object

    text = {"type": "string", "minLength": 1, "maxLength": 128}
    active = {"type": "boolean"}
    tool = _object(
        {
            "id": text,
            "kind": _enum("ENGINEERING_TOOL"),
            "active": active,
            "carrier_id": text,
            "location": _enum("READY", "QUICK_ACCESS", "STOWED", "GROUND"),
            "compatible_devices": _array(text, 256),
        },
        additional=True,
    )
    mount = _object(
        {
            "id": text,
            "kind": _enum("MOUNTING_HARDWARE"),
            "active": active,
            "installed_on": text,
        },
        additional=True,
    )
    return tool, mount


def state_schemas(existing):
    """Type optional task records while preserving unrelated entity and character fields."""
    from .network_state_schema import _array, _enum, _object

    text = {"type": "string", "minLength": 1, "maxLength": 128}
    active = {"type": "boolean"}
    tool, mount = equipment_schemas()
    cell = _array({"type": "integer"}, 2, 2)
    bindings = _object(
        {
            "compatible": active,
            "qualified_actors": _array(text, 256),
            "physical_access": {
                "type": "object",
                "additionalProperties": _object(
                    {
                        "actor_cell": cell,
                        "device_cell": cell,
                    }
                ),
            },
            "tool_id": text,
            "material_id": text,
        }
    )
    node = _object(
        {
            "id": text,
            "active": active,
            "already_deployed": active,
            "map_id": text,
            "position_cell": _array({"type": "integer"}, 2, 2),
            "physical_configuration": _object(
                {
                    "orientation": _enum("NORTH", "EAST", "SOUTH", "WEST"),
                },
                additional=True,
            ),
            "field_work": bindings,
            "mounting_hardware": mount,
        },
        (),
        additional=True,
    )
    entities = next(item for item in existing if item["key"] == "entities")
    shape = entities["schema"]["additionalProperties"]
    shape["properties"]["engineering_tool"] = tool
    shape.setdefault("allOf", []).append(
        {
            "type": "object",
            "if": _object({"kind": _enum("FIELD_SENSOR_RELAY_NODE")}, additional=True),
            "then": node,
        }
    )
    return existing
