"""Batch only named retained-action nullable snapshots without changing public inputs."""

import re
from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools

OWNERS = (
    "begin_srd_spell_action",
    "cast_spell_attack_damage",
    "complete_srd_spell_attack_hit",
    "continue_srd_character_attack_targeting",
    "continue_srd_creature_attack_targeting",
    "begin_srd_character_attack",
    "begin_srd_creature_attack",
    "complete_srd_creature_attack",
    "complete_srd_character_attack",
    "hurl_srd_produce_flame_node",
    "complete_srd_produce_flame_attack",
    "complete_srd_spell_attack_consequences",
    "cast_spell",
    "open_srd_extended_spell_window",
)
ROWS = "_retained_nullable_capture_rows"
ROW = "_retained_nullable_capture_row"


def _pair(first, second):
    left = first.get("set", {})
    children = second.get("on_true", [])
    if left.get("value", "not_null") is not None or len(children) != 1 or second.get("on_false"):
        return None
    right = children[0].get("set", {})
    expression = right.get("value")
    field = left.get("field")
    if (
        not isinstance(field, str)
        or field != right.get("field")
        or not isinstance(expression, str)
        or second.get("condition") != expression + " != null"
    ):
        return None
    if field.startswith("_") and "." not in field:
        return "", field, expression
    if ".pending_srd_actions." in field:
        prefix, _, key = field.rpartition(".")
        return prefix + ".", key, expression
    return None


def _safe(pairs):
    """Reject any later source read that can observe an earlier destination write."""
    written = set()
    for prefix, key, expression in pairs:
        roots = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expression))
        if prefix:
            # Pending captures may not read any pending record or its state aliases.
            if "pending_srd_actions" in expression or "pending_srd_action_stack" in expression:
                return False
        elif written & roots:
            return False
        written.add(key)
    return True


def _batch(pairs):
    prefix = pairs[0][0]
    rows = [
        {"key": repr(key), "missing": expression + " == null", "value": expression}
        for _, key, expression in pairs
    ]
    field = prefix + "{" + ROW + ".key}"
    return [
        {"set": {"field": ROWS, "value": rows}},
        {
            "for_each": {
                "collection": ROWS,
                "as": ROW,
                "do": [
                    {
                        "condition": ROW + ".missing == true",
                        "on_true": [{"set": {"field": field, "value": None}}],
                        "on_false": [{"set": {"field": field, "value": ROW + ".value"}}],
                    },
                ],
            }
        },
    ]


def _compose(effects):
    result, index = [], 0
    while index < len(effects):
        pairs, end = [], index
        while end + 1 < len(effects):
            item = _pair(effects[end], effects[end + 1])
            if item is None or (pairs and item[0] != pairs[0][0]):
                break
            pairs.append(item)
            end += 2
        if len(pairs) >= 4 and _safe(pairs):
            result.extend(_batch(pairs))
            index = end
            continue
        effect = deepcopy(effects[index])
        for branch in ("on_true", "on_false"):
            if branch in effect:
                effect[branch] = _compose(effect[branch])
        if "for_each" in effect:
            effect["for_each"]["do"] = _compose(effect["for_each"]["do"])
        result.append(effect)
        index += 1
    return result


def bind_runtime(text):
    """Run after final retained reaction owners; never batch Jar, bodies or projection."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in OWNERS:
        if name not in tools:
            continue
        original = tools[name]
        effects = _compose(original["effects"])
        if effects != original["effects"]:
            if ROW in str(original) or ROWS in str(original):
                raise SystemExit("Retained capture batching needs its unbatched named owner.")
            tool = deepcopy(original)
            tool["effects"] = effects
            changed[name] = tool
    if not changed:
        return text
    for name in changed:
        text = re.sub(rf"(?m)^(  {re.escape(name)}:[^\n]*)\r\n", r"\1\n", text, count=1)
    return _replace_tools(text, changed)


def _restore(effects):
    result, index = [], 0
    while index < len(effects):
        effect = effects[index]
        if effect.get("set", {}).get("field") == ROWS:
            rows = effect["set"]["value"]
            loop = effects[index + 1]["for_each"]
            field = loop["do"][0]["on_true"][0]["set"]["field"]
            suffix = "{" + ROW + ".key}"
            if not field.endswith(suffix):
                raise SystemExit("Retained capture restoration needs its exact final batch.")
            prefix = field[: -len(suffix)]
            pairs = []
            for row in rows:
                key = row["key"]
                if not re.fullmatch(r"'[A-Za-z_][A-Za-z0-9_]*'", key):
                    raise SystemExit("Retained capture restoration needs literal field names.")
                pairs.append((prefix, key[1:-1], row["value"]))
            if effects[index : index + 2] != _batch(pairs):
                raise SystemExit("Retained capture restoration found a changed final batch.")
            for prefix, key, expression in pairs:
                destination = prefix + key
                result.extend(
                    [
                        {"set": {"field": destination, "value": None}},
                        {
                            "condition": expression + " != null",
                            "on_true": [
                                {"set": {"field": destination, "value": expression}},
                            ],
                        },
                    ]
                )
            index += 2
            continue
        restored = deepcopy(effect)
        for branch in ("on_true", "on_false"):
            if branch in restored:
                restored[branch] = _restore(restored[branch])
        if "for_each" in restored:
            restored["for_each"]["do"] = _restore(restored["for_each"]["do"])
        result.append(restored)
        index += 1
    return result


def restore_runtime(text):
    """Restore only these exact snapshots before the next normal family/reaction regeneration."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in OWNERS:
        if name not in tools or ROWS not in str(tools[name]):
            continue
        tool = deepcopy(tools[name])
        tool["effects"] = _restore(tool["effects"])
        changed[name] = tool
    for name in changed:
        text = re.sub(rf"(?m)^(  {re.escape(name)}:[^\n]*)\r\n", r"\1\n", text, count=1)
    return _replace_tools(text, changed) if changed else text
