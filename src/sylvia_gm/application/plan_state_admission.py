"""Check a Plan's package-owned starting state without changing or publishing its values."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import NoReturn

from sylvia_gm.application.plan_spatial_admission import _plan_state
from sylvia_gm.application.schema_admission import validate_parameters
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import MechanicsRejected, RulesetDiagnostic
from sylvia_gm.domain.package_library import PackageManifest
from sylvia_gm.domain.starting_state import StateSchema


def _reject(path: str, message: str) -> NoReturn:
    """Return an authoring location and correction, never an authored private value."""
    raise ContentShapeError((RulesetDiagnostic(
        code="plan_starting_state_invalid", phase="content", file="plan.json", path=path,
        message=message,
        guidance="Correct this field using the Ruleset's state_schemas; no state changed.",
    ),))


def _select(
    value: object, parts: tuple[str, ...], path: str, bindings: Mapping[str, str | int],
    *, source: bool, key: str | int | None = None,
) -> Iterator[tuple[object, str, Mapping[str, str | int]]]:
    """Walk literal fields, collection members and named source-key bindings only."""
    if not parts:
        yield value, path, bindings
        return
    part, rest = parts[0], parts[1:]
    if part == "$key":
        if key is not None:
            yield key, path, bindings
        return
    capture = source and part.startswith("$")
    if part == "*" or capture:
        members = (value.items() if isinstance(value, Mapping) else enumerate(value)
                   if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
                   else ())
        for member_key, member in members:
            next_bindings = {**bindings, part: member_key} if capture else bindings
            child = (f"{path}[{member_key}]" if isinstance(member_key, int)
                     else f"{path}.{member_key}")
            yield from _select(member, rest, child, next_bindings, source=source, key=member_key)
        return
    selected = bindings[part] if part.startswith("$") else part
    if isinstance(value, Mapping) and selected in value:
        yield from _select(value[selected], rest, f"{path}.{selected}", bindings,
                           source=source, key=selected)
    elif (isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
          and isinstance(selected, int) and 0 <= selected < len(value)):
        yield from _select(value[selected], rest, f"{path}[{selected}]", bindings,
                           source=source, key=selected)


def _matches(value: object, selector: tuple[str, ...], path: str,
             bindings: Mapping[str, str | int], *, source: bool
             ) -> list[tuple[object, str, Mapping[str, str | int]]]:
    """Bound expanded collections even when the package schema leaves their size open."""
    matches: list[tuple[object, str, Mapping[str, str | int]]] = []
    for match in _select(value, selector, path, bindings, source=source):
        if len(matches) >= 8192:
            _reject(path, "A starting-state reference selects more than 8192 values.")
        matches.append(match)
    return matches


def validate_plan_state(payload: Mapping[str, object], manifest: PackageManifest) -> None:
    """Apply exact-key schemas and references inside the inherited Ruleset's namespace."""
    validate_starting_declarations(
        payload, manifest.package_id, manifest.version, manifest.state_schemas,
    )


def validate_starting_declarations(
    payload: Mapping[str, object], package_id: str, version: str,
    declarations: Sequence[StateSchema],
) -> None:
    """Share retained declaration checks with Builder's pre-publication validation."""
    state, paths = _plan_state(payload)
    for declaration in declarations:
        identity = f"{package_id}.{declaration.key}"
        if identity not in state:
            if declaration.required:
                _reject("$.initial_state.mechanics", (
                    f"Supply required starting key '{declaration.key}' in namespace "
                    f"'{package_id}' from retained Ruleset {version}. "
                    "Read its state_schemas for the required fields."
                ))
            continue
        value, path = state[identity], paths[identity]
        try:
            validate_parameters(owned_payload({"value": value}), {
                "type": "object", "properties": {"value": declaration.schema},
                "required": ["value"], "additionalProperties": False,
            })
        except MechanicsRejected as error:
            issue = error.validation_issues[0]
            _reject(path + issue.path.removeprefix("$.").removeprefix("value"),
                    f"Starting field violates its Ruleset schema ({issue.kind.replace('_', ' ')}).")
        for reference in declaration.references:
            target = state.get(f"{package_id}.{reference.target_key}")
            cache: dict[tuple[tuple[str, str | int], ...], list[object]] = {}
            for selected, selected_path, bindings in _matches(
                value, reference.source, path, {}, source=True,
            ):
                if selected is None:
                    continue
                scope = tuple((name, binding) for name, binding in bindings.items()
                              if name in reference.target)
                if scope not in cache:
                    cache[scope] = [item for item, _, _ in _matches(
                        target, reference.target, path, bindings, source=False,
                    )]
                if not isinstance(selected, (str, int)) or isinstance(selected, bool) or not any(
                    type(selected) is type(candidate) and selected == candidate
                    for candidate in cache[scope]
                ):
                    _reject(selected_path, "Reference must identify an existing record in its "
                            "Ruleset-declared target collection.")
