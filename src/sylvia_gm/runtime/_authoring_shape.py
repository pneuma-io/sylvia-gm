"""Closed authoring shapes for runtime effects; semantic execution remains elsewhere."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sylvia_gm.runtime._authoring_declarations import EFFECT_BODY_FIELDS, SCALAR_EFFECT_BODIES

_ENVELOPE_COMMON = frozenset({"condition", "store_as"})
_ENVELOPE_BY_HANDLER = {
    "call": frozenset({"params", "export_context"}),
    "trigger": frozenset({"context"}),
}


def runtime_authoring_shape_errors(raw: object) -> list[tuple[str, str]]:
    """Return field paths for ignored executable keys without evaluating mechanics."""

    errors: list[tuple[str, str]] = []

    def effects(value: object, path: str) -> None:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            return
        for index, effect in enumerate(value):
            item_path = f"{path}[{index}]"
            if not isinstance(effect, Mapping):
                continue
            condition = effect.get("condition")
            handlers = [name for name in effect if name in EFFECT_BODY_FIELDS]
            flat = (
                isinstance(condition, str)
                and ("on_true" in effect or "on_false" in effect)
                and not [name for name in handlers if name != "condition"]
            )
            if flat:
                allowed = {"condition", "on_true", "on_false"}
                for name in sorted(set(effect) - allowed):
                    errors.append((f"{item_path}.{name}", "undeclared effect-envelope field"))
                effects(effect.get("on_true", []), f"{item_path}.on_true")
                effects(effect.get("on_false", []), f"{item_path}.on_false")
                continue
            if handlers == ["condition"]:
                for name in sorted(set(effect) - {"condition"}):
                    errors.append((f"{item_path}.{name}", "undeclared effect-envelope field"))
                body = effect["condition"]
                if isinstance(body, Mapping):
                    allowed = set(EFFECT_BODY_FIELDS["condition"])
                    for name in sorted(set(body) - allowed):
                        errors.append(
                            (f"{item_path}.condition.{name}", "undeclared condition-body field")
                        )
                    effects(body.get("on_true", []), f"{item_path}.condition.on_true")
                    effects(body.get("on_false", []), f"{item_path}.condition.on_false")
                elif not isinstance(body, str):
                    errors.append(
                        (f"{item_path}.condition", "condition body must be text or an object")
                    )
                continue
            non_condition = [name for name in handlers if name != "condition"]
            if len(non_condition) != 1:
                continue  # Existing semantic validator reports missing/multiple handlers.
            handler = non_condition[0]
            allowed_envelope = (
                {handler} | _ENVELOPE_COMMON | set(_ENVELOPE_BY_HANDLER.get(handler, ()))
            )
            for name in sorted(set(effect) - allowed_envelope):
                errors.append((f"{item_path}.{name}", "undeclared effect-envelope field"))
            body = effect[handler]
            if isinstance(body, Mapping):
                allowed_body = set(EFFECT_BODY_FIELDS[handler])
                for name in sorted(set(body) - allowed_body):
                    errors.append((f"{item_path}.{handler}.{name}", "undeclared effect-body field"))
                for branch in ("on_true", "on_false", "do", "consequence"):
                    if branch in body:
                        effects(body[branch], f"{item_path}.{handler}.{branch}")
            elif handler not in SCALAR_EFFECT_BODIES or not isinstance(body, str):
                errors.append((f"{item_path}.{handler}", "effect body must be an object"))

    if not isinstance(raw, Mapping):
        return errors
    tools = raw.get("tools", {})
    if isinstance(tools, Mapping):
        for name, tool in tools.items():
            if isinstance(name, str) and isinstance(tool, Mapping):
                effects(tool.get("effects", []), f"$.tools.{name}.effects")
    procedures = raw.get("procedures", {})
    if isinstance(procedures, Mapping):
        for name, procedure in procedures.items():
            if isinstance(name, str) and isinstance(procedure, Mapping):
                effects(procedure.get("steps", []), f"$.procedures.{name}.steps")
    return errors[:16]


__all__ = ["runtime_authoring_shape_errors"]
