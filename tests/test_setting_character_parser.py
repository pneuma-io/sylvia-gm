import hashlib
import json

import pytest

from sylvia_gm.application.reusable_participants import parse_setting_character
from sylvia_gm.domain.authority import PackageLockEntry, PackageRole
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.package_library import ParticipantArtifactDeclaration, canonical_json_bytes


def authority():
    schema = {
        "$id": "example.signal.character.v1",
        "type": "object",
        "properties": {"name": {"type": "string"}, "callsign": {"type": "string"}},
        "required": ["name", "callsign"],
        "additionalProperties": False,
    }
    package = PackageLockEntry(
        package_id="example.signal-rules", version="2.0.0", sha256="1" * 64,
        role=PackageRole.PRIMARY, load_order=0, dependencies=(), capabilities=(),
    )
    declaration = ParticipantArtifactDeclaration(
        kind="character", schema_id=schema["$id"],
        schema_sha256=hashlib.sha256(canonical_json_bytes(schema)).hexdigest(), schema=schema,
    )
    setting = PackageBinding("example.signal-setting", "3.0.0", "2" * 64)
    return package, declaration, setting


def envelope(declaration, **changes):
    value = {
        "format_id": "sylvia-gm-character-file",
        "format_version": 1,
        "id": "operator",
        "schema": {"id": declaration.schema_id, "content_sha256": declaration.schema_sha256},
        "summary": "The operator assigned to the north relay.",
        "authored_profile": "# Vela\n\nTrained at the north relay.",
        "participant": {"name": "Vela", "callsign": "Northstar"},
        "entity_state": {"stress": 2, "equipment": ["field radio"]},
    }
    value.update(changes)
    return value


def parse(value, *, character_id="operator", role="player_character"):
    package, declaration, setting = authority()
    return parse_setting_character(
        json.dumps(value), character_id=character_id, role=role, package=package,
        declaration=declaration, campaign_setting=setting,
    )


def test_parses_exact_non_dnd_profile_and_mechanics():
    package, declaration, setting = authority()
    value = envelope(declaration)
    parsed = parse_setting_character(
        json.dumps(value), character_id="operator", role="player_character",
        package=package, declaration=declaration, campaign_setting=setting,
    )
    assert parsed.artifact_id == "operator"
    assert parsed.authored_profile == value["authored_profile"]
    assert parsed.participant == value["participant"]
    assert parsed.entity_state == value["entity_state"]
    assert parsed.content_sha256 == hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    assert parsed.package_sha256 == package.sha256
    assert parsed.campaign_setting_sha256 == setting.content_sha256


@pytest.mark.parametrize(
    "change, error",
    [
        ({"id": "someone-else"}, "envelope"),
        ({"schema": {"id": "wrong", "content_sha256": "0" * 64}}, "schema"),
    ],
)
def test_rejects_wrong_exact_identity(change, error):
    _, declaration, _ = authority()
    with pytest.raises(ValueError, match=error):
        parse(envelope(declaration, **change))


@pytest.mark.parametrize(
    "change",
    [
        {"authored_profile": ""},
        {"participant": {"name": "Vela"}},
        {"participant": "Vela is an operator."},
        {"entity_state": "derive this from the profile"},
    ],
)
def test_rejects_sparse_or_prose_only_character_files(change):
    _, declaration, _ = authority()
    with pytest.raises(ValueError):
        parse(envelope(declaration, **change))


@pytest.mark.parametrize("change", [{"format_version": True}, {"guess_stats": True}])
def test_requires_a_closed_versioned_character_file(change):
    _, declaration, _ = authority()
    with pytest.raises(ValueError, match="envelope"):
        parse(envelope(declaration, **change))
