"""Record a GM narrative occurrence against the actual armed Contingency once."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _call, _emit, _when
    from .srd_contingency_spell import SOURCE_SHA256
    from .srd_contingency_water_trigger import CHOICE, HP, PREFIX, WATER
    from .srd_control_behaviors import _entity, _lasting, _param, _require, _set, _tool, _walk
    from .srd_timed_actor_defenses import _load_runtime
else:
    from srd_actor_spell_modifiers import _call, _emit, _when
    from srd_contingency_spell import SOURCE_SHA256
    from srd_contingency_water_trigger import CHOICE, HP, PREFIX, WATER
    from srd_control_behaviors import _entity, _lasting, _param, _require, _set, _tool, _walk
    from srd_timed_actor_defenses import _load_runtime


NARRATIVE = "narrative_world_fact"
TAG = "contingent_fact_tag"
RECORDER = "record_srd_contingency_world_fact"
OBSERVER = "observe_srd_contingency_world_fact"
DRAIN = "drain_srd_contingency_releases"


def _choice():
    return _param(CHOICE, "enum", enum_values=[HP, WATER, NARRATIVE], required=False, default=HP)


def _tag():
    return _param(
        TAG,
        required=False,
        default="",
        description=(
            "A selected narrative occurrence tag; mechanical HP/save/hit/condition outcomes "
            "use native events."
        ),
    )


def _add_choices(tool):
    prior = _choice() | {"enum_values": [HP, WATER]}
    existing = [p for p in tool["parameters"] if p["name"] == CHOICE]
    if existing not in ([prior], [_choice()]):
        raise ValueError("Preserve the actual saved Contingency trigger selector.")
    next(p for p in tool["parameters"] if p["name"] == CHOICE).update(_choice())
    tags = [p for p in tool["parameters"] if p["name"] == TAG]
    if tags and tags != [_tag()]:
        raise ValueError("Preserve the actual narrative occurrence tag choice.")
    if not tags:
        tool["parameters"].append(_tag())


def normalize_trigger_choice(text):
    """Restore only our selector extension before the ordinary Water emitter repeats."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name, tool in tools.items():
        if _choice() in tool["parameters"]:
            current = deepcopy(tool)
            next(p for p in current["parameters"] if p["name"] == CHOICE)["enum_values"] = [
                HP,
                WATER,
            ]
            changed[name] = current
    return _emit(text, changed) if changed else text


def _observer(water):
    """Reuse lifetime/on-person qualification and the existing consume-and-queue body."""
    result = deepcopy(water)
    result["description"] = (
        "Observe a recorded narrative occurrence; never derive a mechanical verdict."
    )
    result["parameters"].append(_param("fact_tag"))
    condition = (
        f"_contingency_current == true and _contingency_record.trigger == '{WATER}' and "
        "target_state.respiration_medium == 'water'"
    )
    events = [node for node in result["effects"] if node.get("condition") == condition]
    if len(events) != 1:
        raise ValueError("Use the genuine Contingency occurrence queue owner.")
    event = events[0]
    event["condition"] = (
        f"_contingency_current == true and _contingency_record.trigger == '{NARRATIVE}' and "
        "_contingency_record.fact_tag == fact_tag and "
        "_contingency_record.cast_id == _contingency_source_id and "
        "_contingency_record.source_sha256 == " + repr(SOURCE_SHA256)
    )
    fields = {PREFIX + ".trigger_medium", PREFIX + ".trigger_tick"}
    actual = {node.get("set", {}).get("field") for node in event["on_true"]} & fields
    if actual != fields:
        raise ValueError("Narrative receipts replace only the genuine medium/tick event witnesses.")
    event["on_true"] = [
        node for node in event["on_true"] if node.get("set", {}).get("field") not in fields
    ]
    event["on_true"] += [
        _set("_contingency_fact_tick", 0),
        _when(
            "lasting_state.lasting_tick != null",
            [
                _set("_contingency_fact_tick", "lasting_state.lasting_tick"),
            ],
        ),
        _set(
            PREFIX + ".trigger_occurrence",
            {
                "tag": "fact_tag",
                "caster": "_target_state_id",
                "source_id": "_contingency_source_id",
                "tick": "_contingency_fact_tick",
            },
        ),
    ]
    return result


def extend_runtime(text):
    """Run after Water and child preparation, before Extended Casting captures choices."""
    tools = _load_runtime(text)["tools"]
    parent = deepcopy(tools["prepare_srd_contingency_cast"])
    _add_choices(parent)
    saves = [
        i
        for i, node in enumerate(parent["effects"])
        if node.get("set", {}).get("field") == "caster_state.contingency_prepared"
    ]
    if len(saves) != 1:
        raise ValueError("Narrative choice needs the actual validated setup record.")
    additions = [
        _require(
            f"{CHOICE} != '{NARRATIVE}' or (len({TAG}) > 0 and len({TAG}) <= 128)",
            "Select a nonempty narrative occurrence tag of at most 128 characters.",
        ),
        _require(
            f"{CHOICE} == '{NARRATIVE}' or {TAG} == ''",
            "A narrative tag belongs only to the narrative occurrence trigger.",
        ),
        _set("_contingent_fact_patch", {"fact_tag": TAG}),
        _set("_contingent_prepared", "merge(_contingent_prepared, _contingent_fact_patch)"),
    ]
    if additions[0] not in parent["effects"]:
        parent["effects"][saves[0] : saves[0]] = additions
    install = deepcopy(tools["install_srd_contingency_source"])
    records = [
        node["set"]["value"]
        for node in install["effects"]
        if node.get("set", {}).get("field") == PREFIX
    ]
    if len(records) != 1 or records[0].get("trigger") != "_contingent_setup.trigger":
        raise ValueError("Keep the actual Water-qualified parent trigger record.")
    records[0]["fact_tag"] = "_contingent_setup.fact_tag"
    changed = {
        "prepare_srd_contingency_cast": parent,
        "install_srd_contingency_source": install,
        OBSERVER: _observer(tools["observe_srd_contingency_water"]),
    }
    recorder = _tool(
        "Record an actual GM narrative occurrence for this caster's armed Contingency. "
        "Use native tools for HP, save, attack, damage and condition outcomes; "
        "this tag is a world fact.",
        [_entity("caster_state"), _param("fact_tag"), _lasting() | {"model_hidden": True}],
        [
            _require(
                "len(fact_tag) > 0 and len(fact_tag) <= 128",
                "Record a bounded factual occurrence tag.",
            ),
            _call(
                OBSERVER,
                {"target_state": "{_caster_state_id}", "fact_tag": "{fact_tag}"},
                ("_contingency_released", "_contingency_source_id"),
            ),
            _when(
                "lasting_state.contingency_release_queue != null and "
                "len(lasting_state.contingency_release_queue) > 0",
                [_call(DRAIN, {})],
            ),
        ],
        public=True,
    )
    changed[RECORDER] = recorder
    for name in ("cast_spell", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        owner = deepcopy(tools[name])
        calls = [
            n for n in _walk(owner["effects"]) if n.get("call") == "prepare_srd_contingency_cast"
        ]
        if calls:
            _add_choices(owner)
            for call in calls:
                call["params"][TAG] = "{" + TAG + "}"
            changed[name] = owner
    return _emit(text, changed)


def bind_retained_choices(text):
    """Retain the selected factual tag through the real begin and completion owners."""
    tools = _load_runtime(text)["tools"]
    owners = {
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        "cast_spell",
        "cast_spell_outside_combat",
    } & tools.keys()
    changed = {}
    check = _require(
        "caster_state.extended_casting.contingency_choices." + TAG + " == " + TAG,
        "Keep the original Contingency narrative tag.",
    )
    guard = _when("spell == 'contingency'", [check])
    for name in sorted(owners):
        tool = deepcopy(tools[name])
        _add_choices(tool)
        for node in _walk(tool["effects"]):
            if node.get("call") == "prepare_srd_contingency_cast" or node.get("call") in owners:
                node["params"][TAG] = "{" + TAG + "}"
            write = node.get("set", {})
            if write.get("field") == "caster_state.extended_casting.contingency_choices":
                write["value"][TAG] = TAG

        def retain(sequence):
            # Keep Water's exact guard intact so its repeat emitter can recognize it.
            for node in list(sequence):
                if node.get("condition") == "spell == 'contingency'" and any(
                    child.get("require", {}).get("error")
                    == "Keep the original Contingency trigger."
                    for child in node.get("on_true", [])
                ):
                    node["on_true"] = [child for child in node["on_true"] if child != check]
                for branch in ("on_true", "on_false"):
                    retain(node.get(branch, []))
                retain(node.get("for_each", {}).get("do", []))
            anchors = [
                index
                for index, node in enumerate(sequence)
                if node.get("condition") == "spell == 'contingency'"
                and any(
                    child.get("require", {}).get("error")
                    == "Keep the original Contingency trigger."
                    for child in node.get("on_true", [])
                )
            ]
            if anchors and guard not in sequence:
                if len(anchors) != 1:
                    raise ValueError("Retain one actual original-trigger completion guard.")
                sequence.insert(anchors[0] + 1, deepcopy(guard))

        retain(tool["effects"])
        changed[name] = tool
    return _emit(text, changed)
