"""Parse and enforce Setting-owned Character File information."""

from __future__ import annotations

from collections.abc import Mapping

from sylvia_gm.application.schema_admission import validate_json_schema, validate_parameters
from sylvia_gm.domain.authority import SafePayload, owned_payload
from sylvia_gm.domain.content_validation import ContentShapeError, validate_content_shape
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected, RulesetDiagnostic
from sylvia_gm.domain.package_library import canonical_json_bytes, parse_content_json_bytes

CHARACTER_REQUIREMENTS_PATH = "character-requirements.json"
_BYTE_LIMIT = 256 * 1024


def _reject(code: str, message: str, *, path: str, guidance: str) -> CommandRejected:
    return CommandRejected(
        "Setting character requirements are invalid",
        code=code,
        diagnostics=(
            RulesetDiagnostic(
                code=code,
                phase="setting_character_requirements",
                file=CHARACTER_REQUIREMENTS_PATH,
                path=path,
                message=message,
                guidance=guidance,
            ),
        ),
    )


def _require_explicit_object_extensions(schema: Mapping[str, object], *, path: str) -> None:
    """Make every structured object state whether and how undeclared fields are admitted."""

    if schema.get("type") == "object" and "additionalProperties" not in schema:
        raise _reject(
            "setting_character_requirements_schema_invalid",
            "An object schema does not declare its extension policy.",
            path=path,
            guidance="Set additionalProperties to false, true, or an admitted schema.",
        )
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, child in properties.items():
            if isinstance(name, str) and isinstance(child, Mapping):
                _require_explicit_object_extensions(child, path=f"{path}.properties.{name}")
    items = schema.get("items")
    if isinstance(items, Mapping):
        _require_explicit_object_extensions(items, path=f"{path}.items")
    additional = schema.get("additionalProperties")
    if isinstance(additional, Mapping):
        _require_explicit_object_extensions(additional, path=f"{path}.additionalProperties")


def parse_setting_character_requirements(source: str) -> SafePayload:
    """Parse one bounded, closed requirements envelope and return its admitted root schema."""

    if not isinstance(source, str):
        raise _reject(
            "setting_character_requirements_json_invalid",
            "The requirements source must be text.",
            path="$",
            guidance="Supply a UTF-8 JSON object.",
        )
    try:
        encoded = source.encode("utf-8")
    except UnicodeEncodeError as error:
        raise _reject(
            "setting_character_requirements_json_invalid",
            "Use valid UTF-8 text.",
            path="$",
            guidance="Correct the requirements file text encoding.",
        ) from error
    if not encoded or len(encoded) > _BYTE_LIMIT:
        raise _reject(
            "setting_character_requirements_size_invalid",
            "The requirements file is empty or oversized.",
            path="$",
            guidance="Keep the complete file within 256 KiB.",
        )
    try:
        raw = parse_content_json_bytes(
            encoded,
            label="Setting character requirements",
            maximum_depth=64,
            maximum_nodes=250_000,
            maximum_object_members=10_000,
        )
    except (UnicodeEncodeError, ValueError) as error:
        raise _reject(
            "setting_character_requirements_json_invalid",
            "The requirements file is not strict JSON.",
            path="$",
            guidance="Use finite JSON values, unique object fields, and bounded nesting.",
        ) from error
    try:
        validate_content_shape(
            "setting_character_requirements", raw, file=CHARACTER_REQUIREMENTS_PATH
        )
    except ContentShapeError as error:
        raise CommandRejected(
            "Setting character requirements fields are invalid",
            code="setting_character_requirements_envelope_invalid",
            diagnostics=error.diagnostics,
        ) from error
    schema = raw["schema"]
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise _reject(
            "setting_character_requirements_schema_invalid",
            "The requirements schema must have an object root.",
            path="schema",
            guidance="Declare an admitted schema with type object.",
        )
    try:
        validate_json_schema(schema, label="Setting character requirements schema")
        _require_explicit_object_extensions(schema, path="schema")
    except CommandRejected as error:
        if error.diagnostics:
            raise
        raise _reject(
            "setting_character_requirements_schema_invalid",
            "The requirements schema is unsupported.",
            path="schema",
            guidance="Use only Sylvia GM's admitted JSON-schema subset.",
        ) from error
    return owned_payload(schema)


def validate_setting_character_data(
    data: object, *, schema: SafePayload | None
) -> dict[str, object]:
    """Return an owned setting_data object after exact, value-free schema validation."""

    if not isinstance(data, Mapping) or any(not isinstance(key, str) for key in data):
        raise _reject(
            "setting_character_data_invalid",
            "setting_data must be an object.",
            path="setting_data",
            guidance="Supply the Setting-owned character fields as a JSON object.",
        )
    try:
        captured = owned_payload(data)
        if len(canonical_json_bytes(captured)) > _BYTE_LIMIT:
            raise ValueError
    except (RecursionError, TypeError, ValueError) as error:
        raise _reject(
            "setting_character_data_size_invalid",
            "setting_data is not bounded finite JSON.",
            path="setting_data",
            guidance="Keep setting_data within 256 KiB.",
        ) from error
    if schema is None:
        if data:
            raise _reject(
                "setting_character_data_undeclared",
                "This Setting declares no character information.",
                path="setting_data",
                guidance="Use an empty setting_data object.",
            )
    else:
        try:
            validate_parameters(captured, schema)
        except MechanicsRejected as error:
            diagnostics = tuple(
                RulesetDiagnostic(
                    code="setting_character_data_schema_invalid",
                    phase="setting_character_requirements",
                    path="setting_data"
                    + ("." + issue.path.removeprefix("$.") if issue.path != "$" else ""),
                    message="This field does not match the Setting's character requirements.",
                    expected=issue.expected_type,
                    actual=issue.actual_type,
                    guidance="Supply the required field with its declared JSON type.",
                )
                for issue in error.validation_issues[:16]
            )
            raise CommandRejected(
                "Setting character data does not match its requirements",
                code="setting_character_data_schema_invalid",
                diagnostics=diagnostics,
            ) from error
        except CommandRejected as error:
            raise _reject(
                "setting_character_requirements_schema_invalid",
                "The Setting requirements schema is invalid.",
                path="setting_data",
                guidance="Correct the Setting's character requirements definition.",
            ) from error
    return dict(captured)


__all__ = [
    "CHARACTER_REQUIREMENTS_PATH",
    "parse_setting_character_requirements",
    "validate_setting_character_data",
]
