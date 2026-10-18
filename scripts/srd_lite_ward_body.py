"""Use the shared authoritative body query for Aura of Life's current recipients."""

from .srd_lite_visual_rolls import _lists


def bind_ward_recipient_bodies(runtime):
    """Replace only the ward's Box-only distance leaf; keep source and ally qualification."""
    tool = runtime["tools"]["resolve_srd_spell_ward_benefits"]
    calls = [
        (rows, index)
        for rows in _lists(tool["effects"])
        for index, effect in enumerate(rows)
        if effect.get("call") == "resolve_srd_environment_body_segment"
    ]
    assert len(calls) == 1
    rows, index = calls[0]
    call = rows[index]
    assert call["params"] == {
        "moving_subject": "{_target_state_id}",
        "fixed_subject": "{_ward_record.caster_state_id}",
        "start": "{_ward_target_scene.position}",
        "end": "{_ward_target_scene.position}",
        "radius": 30,
    }
    assert call["export_context"] == {"_ward_in_area": "_body_hit"}
    assert set(call) <= {"call", "params", "export_context", "condition"}
    replacement = [
        {
            "spatial_query": {
                "scene": "state.spatial_scene",
                "operation": "body_proximity",
                "query": {
                    "map_id": "{state.spatial_scene.map.id}",
                    "moving_entity_id": "{_ward_target_scene.id}",
                    "fixed_entity_id": "{_ward_caster_scene.id}",
                    "destination": "{_ward_target_scene.position}",
                    "radius": 30,
                },
            },
            "store_as": "_ward_body_proximity",
        },
        {"set": {"field": "_ward_in_area", "value": "_ward_body_proximity.hit"}},
    ]
    if "condition" in call:
        replacement = [{"condition": call["condition"], "on_true": replacement}]
    rows[index : index + 1] = replacement
