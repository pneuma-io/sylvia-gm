"""Trim unused common casting inputs from the named final private divination producers."""

from copy import deepcopy

if __package__:
    from .srd_retained_spell_parameters import _unused_parameters
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_retained_spell_parameters import _unused_parameters
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools

OWNERS = frozenset(
    {
        "cast_srd_identify",
        "cast_srd_telepathic_bond",
        "cast_srd_mind_spike",
        "cast_srd_detect_thoughts",
        "cast_srd_nature_divination",
        "cast_srd_animal_messenger",
        "cast_srd_spell_message",
        "cast_srd_scrying",
    }
)


def _unused(tool):
    """Retain implicit state bindings needed by spatial ownership as well as explicit roots."""
    state_parameters = {
        parameter["name"]
        for parameter in tool["parameters"]
        if parameter.get("resolve") == "entity" and parameter.get("default") == "state"
    }
    return _unused_parameters(tool) - state_parameters


def _trim_calls(value, removed):
    """Trim only the matching literal named-owner calls after the final body scan."""
    if isinstance(value, list):
        for item in value:
            _trim_calls(item, removed)
    elif isinstance(value, dict):
        call = value.get("call")
        if isinstance(call, str) and call in removed:
            value["params"] = {
                key: item
                for key, item in value.get("params", {}).items()
                if key not in removed[call]
            }
        elif isinstance(call, dict) and call.get("call", call.get("name")) in removed:
            name = call.get("call", call.get("name"))
            call["params"] = {
                key: item
                for key, item in call.get("params", {}).items()
                if key not in removed[name]
            }
        for item in value.values():
            _trim_calls(item, removed)


def bind_runtime(text):
    """Re-evaluate the final source producers after consumers; retain every actual reference."""
    tools = _load_runtime(text)["tools"]
    changed, removed = {}, {}
    for name in sorted(OWNERS):
        original = tools[name]
        if original.get("metadata", {}).get("category") != "internal":
            raise SystemExit("Divination trimming requires its named private producer owners.")
        unused = _unused(original)
        if unused:
            owner = deepcopy(original)
            owner["parameters"] = [
                parameter for parameter in owner["parameters"] if parameter["name"] not in unused
            ]
            removed[name], changed[name] = unused, owner
    if not removed:
        return text
    for name, original in tools.items():
        caller = deepcopy(changed.get(name, original))
        _trim_calls(caller, removed)
        if caller != original:
            changed[name] = caller
    # The raw fragment replacer matches LF tool headers; admitted Windows bytes retain CRLF.
    for name in changed:
        text = text.replace(f"  {name}:\r\n", f"  {name}:\n", 1)
    return _replace_tools(text, changed)
