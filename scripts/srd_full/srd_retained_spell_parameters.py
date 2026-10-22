"""Trim unused public-cast declarations from the final private saved-hit consequence leaf."""

import re
from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _emit
    from .srd_timed_actor_defenses import _load_runtime
else:
    from srd_actor_spell_modifiers import _emit
    from srd_timed_actor_defenses import _load_runtime

OWNER = "complete_srd_spell_attack_consequences"


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _unused_parameters(tool):
    """Retain every mentioned root/ID alias and every implicit state-registration parameter."""
    body = list(_strings({key: value for key, value in tool.items() if key != "parameters"}))
    removed = set()
    for parameter in tool["parameters"]:
        name = parameter["name"]
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_.])(?:{re.escape(name)}|_{re.escape(name)}_id)"
            r"(?![A-Za-z0-9_])"
        )
        if not parameter.get("from_state") and not any(pattern.search(text) for text in body):
            removed.add(name)
    return removed


def _trim_calls(value, removed):
    """Keep the final private signature and every literal caller's forwarded fields consistent."""
    if isinstance(value, list):
        for item in value:
            _trim_calls(item, removed)
    elif isinstance(value, dict):
        call = value.get("call")
        if call == OWNER:
            params = value.get("params", {})
            value["params"] = {name: item for name, item in params.items() if name not in removed}
        elif isinstance(call, dict) and call.get("call", call.get("name")) == OWNER:
            params = call.get("params", {})
            call["params"] = {name: item for name, item in params.items() if name not in removed}
        for item in value.values():
            _trim_calls(item, removed)


def bind_runtime(text):
    """Run after reaction continuations and their final source consumers have been emitted."""
    tools = _load_runtime(text)["tools"]
    original = tools[OWNER]
    if original.get("metadata", {}).get("category") != "internal":
        raise SystemExit("Retained parameter trimming requires the private consequence owner.")
    removed = _unused_parameters(original)
    if not removed:
        return text
    owner = deepcopy(original)
    owner["parameters"] = [
        parameter for parameter in owner["parameters"] if parameter["name"] not in removed
    ]
    changed = {OWNER: owner}
    for name, original_caller in tools.items():
        if name == OWNER:
            continue
        caller = deepcopy(original_caller)
        _trim_calls(caller, removed)
        if caller != original_caller:
            changed[name] = caller
    return _emit(text, changed)
