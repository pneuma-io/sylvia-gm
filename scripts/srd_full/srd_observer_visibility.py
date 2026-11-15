"""Observer-specific sight consumes the admitted scene and active Mind Spike sources."""

if __package__:
    from .srd_timed_actor_defenses import _call, _entity, _set, _tool
    from .srd_timed_senses import _optional
else:
    from srd_timed_actor_defenses import _call, _entity, _set, _tool
    from srd_timed_senses import _optional


def observer_visibility_tool():
    """Keep actual sight, knowledge of location, and Hidden separate for each observer."""
    return _tool(
        "Read actual sight and Hidden for one observer, including its active Mind Spike source.",
        [
            _entity("observer_state"),
            _entity("subject_state"),
            _optional("lasting_state", resolve="entity", default="state"),
        ],
        [
            _call(
                "check_srd_mind_spike_tracking",
                {
                    "caster_state": "{_observer_state_id}",
                    "target_state": "{_subject_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ["_mind_spike_tracking"],
            ),
            _call(
                "resolve_srd_scene_relation_node",
                {
                    "actor_state": "{_observer_state_id}",
                    "target_state": "{_subject_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                [
                    "_scene_can_see",
                    "_scene_distance",
                    "_scene_target_position",
                    "_scene_same_plane",
                ],
            ),
            _set("_observer_can_see_subject", "_scene_can_see == true"),
            _set(
                "_observer_knows_location", "_mind_spike_tracking == true or _scene_can_see == true"
            ),
            _set(
                "_observer_subject_hidden",
                "subject_state.hidden == true and _mind_spike_tracking != true",
            ),
        ],
    )
