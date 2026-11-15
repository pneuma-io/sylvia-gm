"""The printed underwater example consumes genuine saved respiration facts once."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _call, _emit, _when
    from .srd_control_behaviors import _param, _require, _set, _walk
    from .srd_timed_actor_defenses import _load_runtime
else:
    from srd_actor_spell_modifiers import _call, _emit, _when
    from srd_control_behaviors import _param, _require, _set, _walk
    from srd_timed_actor_defenses import _load_runtime


CHOICE = "contingent_trigger"
HP = "hp_damage_threshold"
WATER = "engulfed_in_water"
OBSERVER = "observe_srd_contingency_water"
DRAIN = "drain_srd_contingency_releases"
PREFIX = "lasting_state.contingency_sources.{_contingency_source_id}"
HP_CONDITION = (
    "_contingency_current == true and hp_before > target_state.hp and "
    "target_state.hp <= _contingency_record.hp_threshold"
)


def _choice():
    return _param(CHOICE, "enum", enum_values=[HP, WATER], required=False, default=HP)


def _add_choice(tool):
    existing = [parameter for parameter in tool["parameters"] if parameter["name"] == CHOICE]
    if existing and existing != [_choice()]:
        raise ValueError("Retain the exact supported Contingency trigger choice.")
    if not existing:
        tool["parameters"].append(_choice())


def _water_observer(hp_observer):
    """Reuse the actual consume-and-queue body, replacing only its genuine event witnesses."""
    water = deepcopy(hp_observer)
    water["description"] = "Consume the first actual saved underwater circumstance and queue once."
    water["parameters"] = [p for p in water["parameters"] if p["name"] != "hp_before"]
    events = [node for node in water["effects"] if node.get("condition") == HP_CONDITION]
    if len(events) != 1:
        raise ValueError("Underwater Contingency needs the real completed HP queue owner.")
    event = events[0]
    event["condition"] = (
        f"_contingency_current == true and _contingency_record.trigger == '{WATER}' and "
        "target_state.respiration_medium == 'water'"
    )
    receipts = {
        PREFIX + ".trigger_hp_before": _set(PREFIX + ".trigger_medium", "'water'"),
        PREFIX + ".trigger_hp_after": _set(PREFIX + ".trigger_tick", "lasting_state.lasting_tick"),
    }
    found = set()
    for index, node in enumerate(event["on_true"]):
        field = node.get("set", {}).get("field")
        if field in receipts:
            event["on_true"][index] = receipts[field]
            found.add(field)
    if found != set(receipts) or any("hp_before" in str(node) for node in water["effects"]):
        raise ValueError("Underwater Contingency must retain only actual medium/clock witnesses.")
    return water


def extend_runtime(text):
    """Run after ordinary Contingency emission and before Extended Casting captures choices."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    hp = deepcopy(tools["observe_srd_contingency_damage"])
    for node in hp["effects"]:
        if node.get("condition") == HP_CONDITION + f" and _contingency_record.trigger == '{HP}'":
            node["condition"] = HP_CONDITION
    water = _water_observer(hp)
    events = [node for node in hp["effects"] if node.get("condition") == HP_CONDITION]
    events[0]["condition"] += f" and _contingency_record.trigger == '{HP}'"
    changed["observe_srd_contingency_damage"] = hp
    changed[OBSERVER] = water

    parent = deepcopy(tools["prepare_srd_contingency_cast"])
    _add_choice(parent)
    saves = [
        index
        for index, node in enumerate(parent["effects"])
        if node.get("set", {}).get("field") == "caster_state.contingency_prepared"
    ]
    if len(saves) != 1:
        raise ValueError("Underwater Contingency needs the actual validated child preparation.")
    trigger = [
        _set("_contingent_trigger_patch", {"trigger": CHOICE}),
        _set("_contingent_prepared", "merge(_contingent_prepared, _contingent_trigger_patch)"),
    ]
    if trigger[0] not in parent["effects"]:
        parent["effects"][saves[0] : saves[0]] = trigger
    changed["prepare_srd_contingency_cast"] = parent

    install = deepcopy(tools["install_srd_contingency_source"])
    records = [
        node["set"]["value"]
        for node in install["effects"]
        if node.get("set", {}).get("field") == PREFIX
    ]
    if len(records) != 1 or records[0].get("trigger") not in {
        "'hp_damage_threshold'",
        "_contingent_setup.trigger",
    }:
        raise ValueError("Underwater Contingency needs the exact installed parent record.")
    records[0]["trigger"] = "_contingent_setup.trigger"
    installation_check = _when(
        f"_contingent_setup.trigger == '{WATER}'",
        [
            _call(OBSERVER, {"target_state": "{_caster_state_id}"}),
            _set(
                "_spell_detail_rows",
                [
                    {"label": "'Trigger'", "value": "'Engulfed in water'"},
                ],
            ),
            _when(
                "lasting_state.contingency_sources[_contingency_source_id].status == 'queued'",
                [
                    _set(
                        "_spell_effect_summary",
                        "'Trigger fulfilled at setup; both spell slots paid.'",
                    ),
                    _set(
                        "_spell_summary_rows",
                        [
                            {
                                "label": "'Contingency'",
                                "value": "'Trigger fulfilled at setup; both spell slots paid.'",
                            }
                        ],
                    ),
                ],
            ),
        ],
    )
    if installation_check not in install["effects"]:
        install["effects"].append(installation_check)
    changed["install_srd_contingency_source"] = install
    for name in ("cast_spell", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        owner = deepcopy(tools[name])
        calls = [
            node
            for node in _walk(owner["effects"])
            if node.get("call") == "prepare_srd_contingency_cast"
        ]
        if calls:
            _add_choice(owner)
            for call in calls:
                call["params"][CHOICE] = "{" + CHOICE + "}"
            changed[name] = owner
    return _emit(text, changed)


def bind_extended_choices(text):
    """Retain and validate the sixth choice in the real begin/completion owners before clones."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    owners = {
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        "cast_spell",
        "cast_spell_outside_combat",
    } & tools.keys()
    for name in sorted(owners):
        if name not in tools:
            continue
        owner = deepcopy(tools[name])
        _add_choice(owner)
        for node in _walk(owner["effects"]):
            if node.get("call") == "prepare_srd_contingency_cast":
                node["params"][CHOICE] = "{" + CHOICE + "}"
            if node.get("call") in owners:
                node["params"][CHOICE] = "{" + CHOICE + "}"
            write = node.get("set", {})
            if write.get("field") == "caster_state.extended_casting.contingency_choices":
                write["value"][CHOICE] = CHOICE

        def retain(sequence):
            for node in list(sequence):
                for branch in ("on_true", "on_false"):
                    retain(node.get(branch, []))
                retain(node.get("for_each", {}).get("do", []))
            anchors = [
                index
                for index, node in enumerate(sequence)
                if node.get("require", {}).get("error")
                == "Start and continue this longer casting before resolving its spell."
            ]
            check = _when(
                "spell == 'contingency'",
                [
                    _require(
                        "caster_state.extended_casting.contingency_choices.contingent_trigger "
                        "== contingent_trigger",
                        "Keep the original Contingency trigger.",
                    )
                ],
            )
            if anchors and check not in sequence:
                if len(anchors) != 1:
                    raise ValueError(
                        "Contingency completion needs one actual retained choice guard."
                    )
                sequence.insert(anchors[0] + 1, check)

        retain(owner["effects"])
        changed[name] = owner
    return _emit(text, changed)


def bind_respiration(text):
    """Observe the real medium write, then drain at the outer setter, outside child installation."""
    tools = _load_runtime(text)["tools"]
    name = (
        "set_srd_respiration_node" if "set_srd_respiration_node" in tools else "set_srd_respiration"
    )
    owner = deepcopy(tools[name])
    matches = [
        node
        for node in _walk(owner["effects"])
        if node.get("set", {}).get("field") == "target_state.respiration_medium"
    ]
    if matches != [{"set": {"field": "target_state.respiration_medium", "value": "medium"}}]:
        raise ValueError("Underwater Contingency requires the genuine respiration medium setter.")
    observation = _when(
        "target_state.has_contingency == true and "
        "_contingency_water_before != true and medium == 'water'",
        [_call(OBSERVER, {"target_state": "{_target_state_id}"})],
    )
    before = _set("_contingency_water_before", "target_state.respiration_medium == 'water'")

    def bind(sequence):
        for node in list(sequence):
            for branch in ("on_true", "on_false"):
                bind(node.get(branch, []))
            bind(node.get("for_each", {}).get("do", []))
        for index, node in enumerate(list(sequence)):
            if node in matches and observation not in sequence:
                sequence[index : index + 1] = [deepcopy(before), node, deepcopy(observation)]

    bind(owner["effects"])
    changed = {name: owner}
    outer = deepcopy(changed.get("set_srd_respiration", tools["set_srd_respiration"]))
    calls = {node["call"] for node in _walk(outer["effects"]) if "call" in node}
    if DRAIN not in calls:
        outer["effects"].append(
            _when(
                "state.contingency_release_queue != null and "
                "len(state.contingency_release_queue) > 0",
                [_call(DRAIN, {})],
            )
        )
    refresh = _call(
        "refresh_srd_respiration",
        {"target_state": "{_target_state_id}"},
        ("_respiration_can_breathe",),
    )

    def refresh_after_release(sequence):
        for node in list(sequence):
            for branch in ("on_true", "on_false"):
                refresh_after_release(node.get(branch, []))
            refresh_after_release(node.get("for_each", {}).get("do", []))
        for index, node in enumerate(list(sequence)):
            if node.get("call") == DRAIN and refresh not in sequence:
                sequence.insert(index + 1, deepcopy(refresh))

    refresh_after_release(outer["effects"])
    changed["set_srd_respiration"] = outer
    graph = {
        key: {node["call"] for node in _walk(value["effects"]) if "call" in node}
        for key, value in (tools | changed).items()
    }
    reached, pending = set(), [DRAIN]
    while pending:
        current = pending.pop()
        if current not in reached:
            reached.add(current)
            pending.extend(graph.get(current, set()) - reached)
    if "set_srd_respiration" in reached:
        raise ValueError(
            "Underwater Contingency cannot drain recursively through child installation."
        )
    return _emit(text, changed)
