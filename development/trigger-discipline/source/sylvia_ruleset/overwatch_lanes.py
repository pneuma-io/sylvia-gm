"""Shooter-bound geometry on the existing Overwatch accounting and reaction procedure."""

from copy import deepcopy

WATCH = "prepared.overwatchers[_actor_id]"
ROOT = "state.prepared.overwatchers.{_actor_id}"


def query():
    return {
        "spatial_query": {
            "scene": "state.spatial_scene",
            "operation": "grid_lane",
            "query": {"map_id": "{map_id}", "owner_id": "{_actor_id}", "lane": "{lane}"},
        },
        "store_as": "_lane_geometry",
    }


def install_specs(specs, param):
    tool = deepcopy(specs["prepare_overwatch"])
    tool["description"] = (
        "Prepare a shooter-origin grid lane or triangle using explicitly chosen geometry. "
        "No class width grant or accuracy penalty is implied. "
        "Replacing your own lane pays setup again."
    )
    tool["parameters"] = [
        p for p in tool["parameters"] if p["name"] not in ("zone_id", "prepared_zones")
    ]
    lane_parameter = param("lane", "array", items="object", min_items=1, max_items=1)
    lane_parameter["items"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["pattern", "direction", "extent", "half_width", "origin", "facing"],
        "properties": {
            "pattern": {"type": "string", "enum": ["lane", "triangle"]},
            "direction": {"type": "string", "enum": ["east", "south", "west", "north"]},
            "extent": {"type": "integer", "minimum": 1, "maximum": 2048},
            "half_width": {"type": "integer", "minimum": 0, "maximum": 2048},
            "origin": {"type": "array", "items": {"type": "number"}},
            "facing": {"type": "array", "items": {"type": "number"}},
        },
    }
    tool["parameters"].extend(
        [lane_parameter, param("prepared", "object", from_state="state.prepared")]
    )
    pending = tool["effects"][:]
    while pending:
        effect = pending.pop(0)
        loop = effect.get("for_each", {})
        if loop.get("collection") == "prepared_zones":
            effect.clear()
            effect["set"] = {"field": "_prepared_zone_exists", "value": True}
        if effect.get("set", {}).get("field") == "state.prepared.overwatch.stationary_origin":
            effect["set"]["value"] = "actor.position_cell"
        lane_fields = {
            "lane_origin": "_selected_lane.origin",
            "lane_facing": "_selected_lane.facing",
            "lane_shape": "_lane_geometry.zone.shape",
            "lane_stance": "actor.stance",
        }
        for field, value in lane_fields.items():
            if effect.get("set", {}).get("field") == "state.prepared.overwatch." + field:
                effect["set"]["value"] = value
        if effect.get("set", {}).get("field") == "state.prepared.overwatch.zone_id":
            effect["set"]["value"] = "_lane_geometry.zone.id"
        for branch in ("on_true", "on_false"):
            pending.extend(effect.get(branch, []))
        pending.extend(loop.get("do", []))
        if effect.get("set", {}).get("field") == "state.prepared.overwatch.owner":
            # The actual scene mutation runs inside the original post-exposure commit guard.
            effect["set"]["value"] = "_actor_id"

    def commit(effects):
        for index, effect in enumerate(effects):
            if effect.get("set", {}).get("field") == "state.prepared.overwatch.owner":
                effects[index:index] = [
                    {
                        "spatial_mutation": {
                            "scene": "state.spatial_scene",
                            "operation": "grid_lane",
                            "map_id": "{map_id}",
                            "expected_revision": "{state.spatial_scene.map.revision}",
                            "owner_id": "{_actor_id}",
                            "lane": "{lane}",
                        },
                        "store_as": "_lane_commit",
                    },
                ]
                return True
            for key in ("on_true", "on_false"):
                if commit(effect.get(key, [])):
                    return True
        return False

    if not commit(tool["effects"]):
        raise ValueError("Overwatch setup commit was not found")

    # The ordinary single-slot procedure supplies accounting and incoming exposure;
    # only its own preparation writes are redirected to this shooter's entry.
    def owned(value):
        if isinstance(value, str):
            return value.replace("state.prepared.overwatch.", ROOT + ".")
        if isinstance(value, list):
            return [owned(item) for item in value]
        if isinstance(value, dict):
            return {key: owned(item) for key, item in value.items()}
        return value

    tool["effects"] = owned(tool["effects"])

    # This guard operates on the legacy slot, after the owned writes were redirected.
    def add_legacy_break(effects):
        for index, effect in enumerate(effects):
            if effect.get("set", {}).get("field") == ROOT + ".owner":
                effects[index:index] = [
                    {
                        "condition": "prepared.overwatch.owner == _actor_id",
                        "on_true": [
                            {"set": {"field": "state.prepared.overwatch.active", "value": False}},
                            {"set": {"field": "state.prepared.overwatch.shots", "value": 0}},
                        ],
                        "on_false": [],
                    }
                ]
                return True
            for branch in ("on_true", "on_false"):
                if add_legacy_break(effect.get(branch, [])):
                    return True
        return False

    if not add_legacy_break(tool["effects"]):
        raise ValueError("Owned Overwatch setup commit was not found")
    tool["effects"][:0] = [
        {
            "require": {
                "condition": "'.' not in _actor_id",
                "error": "Shooter identity is unsupported by the current TD state-field paths.",
            }
        },
        {"set": {"field": "_selected_lane", "value": "lane[0]"}},
        query(),
    ]
    specs["prepare_overwatch_lane"] = tool
    for name in ("prepare_overwatch", "prepare_sentinel_overwatch", "prepare_sentinel_sightline"):
        if not any(p["name"] == "prepared" for p in specs[name]["parameters"]):
            specs[name]["parameters"].append(
                param("prepared", "object", from_state="state.prepared")
            )

        def break_owned(effects):
            for index, effect in enumerate(effects):
                if effect.get("set", {}).get("field") == "state.prepared.overwatch.owner":
                    effects[index:index] = break_lane()
                    return True
                for branch in ("on_true", "on_false"):
                    if break_owned(effect.get(branch, [])):
                        return True
            return False

        if not break_owned(specs[name]["effects"]):
            raise ValueError(f"{name}: preparation commit was not found")
    specs["cancel_overwatch_lane"] = {
        "capability": tool["capability"],
        "description": "Cancel your armed lane without an AP refund.",
        "parameters": [
            param("actor", "string", resolve="entity"),
            param("prepared", "object", from_state="state.prepared"),
        ],
        "costs_action": False,
        "effects": [
            {
                "require": {
                    "condition": 'prepared.overwatchers != null and _actor_id in'
                    ' prepared.overwatchers and '
                    + WATCH
                    + ".owner == _actor_id",
                    "error": "Only the watcher can cancel this prepared reaction.",
                }
            },
            {"set": {"field": ROOT + ".active", "value": False}},
            {"set": {"field": ROOT + ".shots", "value": 0}},
        ],
    }


def metadata(ordinary):
    result = deepcopy(ordinary)
    controls = result["player_controls"]
    controls["queries"] = [query()]
    controls["map_preview"] = {"grid_lane": "_lane_geometry"}
    controls["checks"].append(
        {
            "condition": "'.' not in _actor_id",
            "reason": "This shooter identity is unsupported by TD state-field paths.",
        }
    )
    controls["preview"]["summary"].extend(
        [
            {
                "label": "Pattern",
                "value": {"path": "context._lane_geometry.zone.metadata.lane.pattern"},
            },
            {
                "label": "Direction",
                "value": {"path": "context._lane_geometry.zone.metadata.lane.direction"},
            },
            {
                "label": "Forward cells",
                "value": {"path": "context._lane_geometry.zone.metadata.lane.extent"},
            },
            {
                "label": "Half width in cells",
                "value": {"path": "context._lane_geometry.zone.metadata.lane.half_width"},
            },
            {
                "label": "Geometry",
                "value": "Authored choice; class grants and width penalties are not specified",
            },
        ]
    )
    return result


def validity(
    prepared="prepared_fire.overwatch",
    shooter="overwatcher",
    shooter_id="_overwatcher_id",
    *,
    invalidate="true",
    root="state.prepared.overwatch",
):
    """A lane never tracks a changed body pose or an edited/reassigned zone."""
    return [
        {"set": {"field": "_lane_body_valid", "value": f"{prepared}.lane_origin == null"}},
        {"set": {"field": "_lane_zone_valid", "value": f"{prepared}.lane_origin == null"}},
        {
            "for_each": {
                "collection": "watch_bodies",
                "as": "_lane_body",
                "do": [
                    {
                        "set": {"field": "_lane_body_valid", "value": True},
                        "condition": (
                            f"_lane_body.id == {shooter_id} and _lane_body.active == true and "
                            f"{shooter}.active == true and {shooter}.downed == false and "
                            f"occurrences(one(_lane_body.position), {prepared}.lane_origin) == 1 "
                            "and "
                            f"occurrences(one(_lane_body.facing), {prepared}.lane_facing) == 1 and "
                            f"{shooter}.stance == {prepared}.lane_stance"
                        ),
                    },
                ],
            },
            "condition": f"{prepared}.lane_origin != null",
        },
        {
            "for_each": {
                "collection": "watch_zones",
                "as": "_lane_zone",
                "do": [
                    {
                        "set": {"field": "_lane_zone_valid", "value": True},
                        "condition": (
                            f"_lane_zone.id == {prepared}.zone_id and "
                            f"_lane_zone.metadata.owner == {shooter_id} and "
                            f"occurrences(one(_lane_zone.shape), {prepared}.lane_shape) == 1"
                        ),
                    },
                ],
            },
            "condition": f"{prepared}.lane_origin != null",
        },
        {
            "set": {"field": "_watch_stationary", "value": False},
            "condition": "_lane_body_valid != true or _lane_zone_valid != true",
        },
        {
            "condition": (
                f"{prepared}.lane_origin != null and _watch_stationary != true and {invalidate}"
            ),
            "on_true": [
                {"set": {"field": root + ".active", "value": False}},
                {"set": {"field": root + ".shots", "value": 0}},
            ],
            "on_false": [],
        },
    ]


def break_lane(actor_id="_actor_id", prepared="prepared"):
    """Record loss of a bound posture at the mutation, before it can be restored."""
    return [
        {
            "condition": (f"{prepared}.overwatch.owner == {actor_id} and "
                          f"{prepared}.overwatch.lane_origin != null"),
            "on_true": [
                {"set": {"field": "state.prepared.overwatch.active", "value": False}},
                {"set": {"field": "state.prepared.overwatch.shots", "value": 0}},
            ],
            "on_false": [],
        },
        {
            "condition": (f"{prepared}.overwatchers != null and "
                          f"{actor_id} in {prepared}.overwatchers and "
                          f"{prepared}.overwatchers[{actor_id}].lane_origin != null"),
            "on_true": [
                {
                    "set": {
                        "field": "state.prepared.overwatchers.{" + actor_id + "}.active",
                        "value": False,
                    }
                },
                {
                    "set": {
                        "field": "state.prepared.overwatchers.{" + actor_id + "}.shots",
                        "value": 0,
                    }
                },
            ],
            "on_false": [],
        },
    ]
