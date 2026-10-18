"""Share final qualified packet mechanics while retaining each entry's exact public context."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_prismatic_damage_packets import PRESENTATION_FIELDS, _without_presentation_snapshot
    from .srd_timed_actor_defenses import _load_runtime, _RuntimeDumper
    from .srd_timed_senses import _replace_tools
else:
    from srd_prismatic_damage_packets import PRESENTATION_FIELDS, _without_presentation_snapshot
    from srd_timed_actor_defenses import _load_runtime, _RuntimeDumper
    from srd_timed_senses import _replace_tools


RENDER = "render_damage_snapshot"
COHORTS = (
    (
        "execute_srd_qualified_scalar_packet",
        "apply_srd_qualifying_damage",
        "apply_srd_prismatic_damage",
        "apply_srd_dream_wake_damage",
    ),
    (
        "execute_srd_qualified_composite_packet",
        "apply_srd_qualifying_composite_damage",
        "apply_srd_prismatic_composite_damage",
        None,
    ),
)


def _drain(tool):
    """Separate only the exact final direct drain; received observation remains in the core."""
    value = deepcopy(tool)
    effects = value["effects"]
    expected = {
        "condition": "lasting_state.has_dream_spells == true",
        "on_true": [{"call": "drain_srd_dream_wakes", "params": {}}],
    }
    if expected not in effects:
        return value, []
    if effects[-1] != expected or effects.count(expected) != 1:
        raise SystemExit(
            "Packet reuse requires one direct Dream drain at the actual final boundary."
        )
    value["effects"] = effects[:-1]
    return value, [deepcopy(expected)]


def _forward(parameter):
    name = parameter["name"]
    if parameter.get("resolve") in {"entity", "participant"}:
        return "{_" + name + "_id}"
    return "{" + name + "}"


def _delegate(original, core, render, drain):
    value = deepcopy(original)
    value["effects"] = [
        {
            "call": core,
            "params": {
                **{p["name"]: _forward(p) for p in original["parameters"]},
                RENDER: render,
            },
            "export_context": "*",
        },
        *deepcopy(drain),
    ]
    return value


def _canonical(original, fields):
    """Guard only the contiguous, proven unused presentation snapshots; keep mechanics ordered."""
    _without_presentation_snapshot(original, fields)
    value = deepcopy(original)
    indices = [
        index
        for index, effect in enumerate(value["effects"])
        if effect.get("set", {}).get("field") in fields
    ]
    if indices != list(range(indices[0], indices[-1] + 1)):
        raise SystemExit("Packet reuse requires one contiguous presentation snapshot block.")
    start, stop = indices[0], indices[-1] + 1
    value["effects"][start:stop] = [
        {"condition": RENDER + " == true", "on_true": value["effects"][start:stop]}
    ]
    value["parameters"].append(
        {"name": RENDER, "type": "boolean", "required": True, "model_hidden": True}
    )
    # Each original entry still owns its preconditions and final narrative/card exactly once.
    value["pre_conditions"] = []
    value.pop("narrative", None)
    value.get("metadata", {}).pop("result_card", None)
    return value


def bind_runtime(text):
    """Capture after final Jar/Fortitude/Dream/Bond/Prism consumers, before reaction clones."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for core, owner, prism, dream in COHORTS:
        original = tools[owner]
        if (
            core in tools
            and original["effects"]
            == _delegate(original, core, True, _drain(original)[1])["effects"]
        ):
            continue
        if any(effect.get("call") == core for effect in original["effects"]):
            raise SystemExit("Packet reuse cannot discard a later change to a captured delegate.")
        base, drain = _drain(original)
        fields = PRESENTATION_FIELDS - ({"_hp_lost"} if "composite" in owner else set())
        expected_prism = _without_presentation_snapshot(base, fields)
        actual_prism, prism_drain = _drain(tools[prism])
        if actual_prism != expected_prism or prism_drain != drain:
            raise SystemExit("Packet reuse requires the actual final Prismatic owner to match.")
        if dream is not None and tools[dream] != base:
            raise SystemExit(
                "Packet reuse cannot assume a differing Dream/body-source owner is equal."
            )
        changed[core] = _canonical(base, fields)
        changed[owner] = _delegate(original, core, True, drain)
        changed[prism] = _delegate(tools[prism], core, False, drain)
        if dream is not None:
            changed[dream] = _delegate(tools[dream], core, True, [])
    if not changed:
        return text
    existing = {name: tool for name, tool in changed.items() if name in tools}
    for name in existing:
        text = re.sub(rf"(?m)^(  {re.escape(name)}:[^\n]*)\r\n", r"\1\n", text, count=1)
    text = _replace_tools(text, existing)
    new = {name: tool for name, tool in changed.items() if name not in tools}
    if new:
        fragment = yaml.dump(new, Dumper=_RuntimeDumper, sort_keys=False, width=105)
        fragment = "".join(
            "  " + line if line.strip() else line for line in fragment.splitlines(True)
        )
        text, count = re.subn(
            r"(?m)^procedures:", lambda _: fragment + "\nprocedures:", text, count=1
        )
        if count != 1:
            raise SystemExit(
                "Packet reuse needs the actual procedures footer for new implementations."
            )
    return text


def _expanded_core(core, render):
    """Recover only this canonical owner's exact presentation choice and ordered effects."""
    effects = []
    found = 0
    for effect in core["effects"]:
        if effect.get("condition") == RENDER + " == true":
            if effect.get("on_false") or set(effect) != {"condition", "on_true"}:
                raise SystemExit("Packet restoration found a changed presentation boundary.")
            if render:
                effects.extend(deepcopy(effect["on_true"]))
            found += 1
        else:
            effects.append(deepcopy(effect))
    if found != 1:
        raise SystemExit("Packet restoration requires its one canonical presentation boundary.")
    return effects


def restore_runtime(text):
    """Restore named entry bodies before source binders and remove unused owned implementations."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    changed = {}
    for core, owner, prism, dream in COHORTS:
        if core not in tools:
            continue
        for name, render in ((owner, True), (prism, False), (dream, True)):
            if name is None or name not in tools:
                continue
            entry = tools[name]
            first = entry["effects"][0]
            if first.get("call") != core:
                continue
            expected = _delegate(entry, core, render, [])["effects"][0]
            if first != expected:
                raise SystemExit("Packet restoration needs the exact named delegate arguments.")
            value = deepcopy(entry)
            value["effects"] = _expanded_core(tools[core], render) + value["effects"][1:]
            changed[name] = value
    for name in changed:
        text = re.sub(rf"(?m)^(  {re.escape(name)}:[^\n]*)\r\n", r"\1\n", text, count=1)
    if changed:
        text = _replace_tools(text, changed)
    for core, *_ in COHORTS:
        if core not in tools:
            continue
        # A later consumer may retain an explicit call; its private implementation stays available.
        current = _load_runtime(text)
        owned = current["tools"].pop(core)
        if core in str(current):
            continue
        pattern = (
            rf"(?ms)^  {re.escape(core)}:[^\n]*\r?\n.*?(?=^  [A-Za-z_][A-Za-z0-9_]*:|^procedures:)"
        )
        text, count = re.subn(pattern, "", text, count=1)
        if count != 1 or core in _load_runtime(text)["tools"]:
            raise SystemExit("Packet restoration needs its exact generated private tool boundary.")
        del owned
    return text
