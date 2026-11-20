"""SRD Lite call parameters must reach the called tool resolved, never as literal text.

A nested call, trigger or deferred call resolves each parameter only as an exact {path}
reference or a {path} text template (runtime/_template_resolver.py). Anything else inside braces,
such as an expression, and any template inside a list or object parameter, reaches the called
tool as literal text. Issue 191 found one; SRD Lite 0.3.27 repaired it and the two others this
check found, and the check keeps the package free of them.
"""

import re
from functools import lru_cache
from pathlib import Path

import yaml

from sylvia_gm.runtime._condition_evaluator import evaluate_expression
from sylvia_gm.runtime._template_resolver import resolve_template
from sylvia_gm.runtime._types import ExecutionContext

ROOT = Path(__file__).resolve().parents[1]
PATH = r"\{[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*\}"


@lru_cache
def _runtime():
    raw = (ROOT / "rulesets/srd-lite/runtime/ruleset.yaml").read_bytes()
    return yaml.load(raw, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _literal_text(value):
    """Whether resolve_params would hand this parameter value on with braces unresolved."""
    if isinstance(value, str):
        return not re.fullmatch(PATH, value) and bool(re.search(r"[{}]", re.sub(PATH, "", value)))
    return any("{" in text or "}" in text for text in _strings(value))


def _parameter_maps(node):
    """Yield (kind, called name, parameters) for every call, trigger and deferred call."""
    if isinstance(node, dict):
        call = node.get("call")
        if isinstance(call, str):
            yield "call", call, node.get("params", {})
        elif isinstance(call, dict):
            yield "call", call.get("name"), call.get("params", {})
        trigger = node.get("trigger")
        if isinstance(trigger, str):
            yield "trigger", trigger, node.get("context", {})
        elif isinstance(trigger, dict):
            yield "trigger", trigger.get("name"), trigger.get("context", {})
        if isinstance(node.get("defer"), dict):
            yield "defer", node["defer"].get("call"), node["defer"].get("params", {})
        for value in node.values():
            yield from _parameter_maps(value)
    elif isinstance(node, list):
        for item in node:
            yield from _parameter_maps(item)


def test_no_call_parameter_reaches_its_tool_as_literal_brace_text():
    runtime = _runtime()
    checked = 0
    literal = []
    for section in ("tools", "procedures"):
        for owner, row in (runtime.get(section) or {}).items():
            for kind, called, parameters in _parameter_maps(row):
                for key, value in parameters.items():
                    checked += 1
                    if _literal_text(value):
                        literal.append(f"{owner} -> {kind} {called}: {key} = {value!r}")
    assert checked > 10_000
    assert literal == []


def test_the_check_recognises_the_shapes_it_guards_against():
    assert _literal_text("{_birth.known_spell_effect == true}")
    assert _literal_text(["{dwelling_id}:room:west"])
    assert _literal_text({"id": "{dwelling_id}"})
    assert not _literal_text("{_birth_known_spell_effect}")
    assert not _literal_text("{dwelling_id}:room")
    assert not _literal_text("Plain text and 'quoted' text")
    assert not _literal_text(["west", 5])


def _effects(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _effects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _effects(item)


def test_dwelling_identity_guard_sees_the_wall_identities_the_dwelling_creates():
    """Magnificent Mansion and Rope Trick refuse a clash with any wall they will create."""
    for name in ("validate_srd_magnificent_mansion", "validate_srd_rope_trick_layout"):
        tool = _runtime()["tools"][name]
        (bind,) = [e for e in _effects(tool) if e.get("call") == "bind_srd_dwelling_scene_ids"]
        assert bind["params"]["face_ids"] == "{_xd_dwelling_face_ids}"
        (built,) = [
            e["set"]["value"] for e in _effects(tool)
            if isinstance(e.get("set"), dict) and e["set"].get("field") == "_xd_dwelling_face_ids"
        ]
        context = ExecutionContext(params={"dwelling_id": "home-1"})
        guarded = [evaluate_expression(expression, context) for expression in built]
        walls = ExecutionContext(params={"_xd_plane": "home-1:room"})
        created = [
            resolve_template(e["params"]["identity"], walls) for e in _effects(tool)
            if e.get("call") == "bind_srd_mansion_reserved_identity"
        ]
        assert guarded == created == [
            f"home-1:room:{face}" for face in ("west", "east", "south", "north", "floor", "ceiling")
        ]
