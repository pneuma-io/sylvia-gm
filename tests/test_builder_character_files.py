"""File-backed character commands retain validation, bounds and observed-byte identity."""

import json
from copy import deepcopy

import pytest

from sylvia_gm.agent._builder_session import (
    _participant_artifact_tool,
    _participant_draft_check_tool,
    _participant_entity_state,
)
from sylvia_gm.agent._participant_file_tool import participant_file_tool
from sylvia_gm.builder._workspace_files import content_version


def write_draft(root, data):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    (root / "draft.json").write_bytes(raw)
    return {"path": "draft.json", "expected_version": content_version(raw)}


def tool(root, callback):
    return participant_file_tool(
        _participant_artifact_tool(role="player_character", publisher=callback),
        root, _participant_entity_state,
    )


@pytest.mark.parametrize("checking", [True, False])
def test_file_matches_inline_and_captures_bytes_before_callback(tmp_path, checking):
    calls = []

    def validate(**fields):
        calls.append(deepcopy(fields))
        (tmp_path / "draft.json").write_text('{"changed":true}')
        return {"published": not checking, "state_changed": not checking}

    inline = _participant_artifact_tool(
        role="companion", publisher=validate, checking=checking, schemas={"test": {}},
    )
    fields = {"summary": "Chosen details", "authored_profile": "A fictional friend.",
              "participant": {"name": "New name"}, "entity_state": {"n": 3, "items": []},
              "setting_data": {"origin": "Fictional"}, "schema_id": "test"}
    params = write_draft(tmp_path, fields)
    file_tool = participant_file_tool(inline, tmp_path, _participant_entity_state)
    result = file_tool.handler(params, None)
    assert result["success"] is True and result["draft_version"] == params["expected_version"]
    assert result["draft_path"] == "draft.json"
    assert calls[0]["role"] == "companion" and calls[0]["entity_state"] == fields["entity_state"]
    inline_fields = {**fields, "entity_state": json.dumps(fields["entity_state"]),
                     "setting_data": json.dumps(fields["setting_data"])}
    expected = inline.handler(inline_fields, None)
    assert calls[0] == calls[1]
    assert {k: v for k, v in result.items() if not k.startswith("draft_")} == expected
    assert file_tool.handler(params, None)["code"] == "stale_file"
    assert len(calls) == 2


@pytest.mark.parametrize("raw", [
    b'{"x":1,"x":2}', b'{"participant":{"x":1,"x":2}}', b'{"x":NaN}',
    b'{"x":Infinity}', b'[]', b'{} trailing', b'\xff',
    b'{"x":' + b'[' * 34 + b'0' + b']' * 34 + b'}',
    b'{"x":"' + b'a' * (256 * 1024) + b'"}',
], ids=["duplicate-root", "duplicate-nested", "nan", "infinity", "array", "trailing",
        "encoding", "depth", "oversized"])
def test_invalid_file_never_invokes_publication(tmp_path, raw):
    calls = []
    params = write_draft(tmp_path, raw)
    result = tool(tmp_path, lambda **fields: calls.append(fields)).handler(params, None)
    assert not result["success"] and result["state_changed"] is False
    assert result["code"] == "participant_file_invalid" and calls == []


@pytest.mark.parametrize("change", [
    {"expected_version": "0" * 64}, {"path": "../other.json"}, {"path": "missing.json"},
    {"expected_version": "missing"}, {"extra": True}, {"path": 5},
])
def test_stale_denied_or_missing_file_never_invokes_publication(tmp_path, change):
    calls = []
    params = {**write_draft(tmp_path, {}), **change}
    result = tool(tmp_path, lambda **fields: calls.append(fields)).handler(params, None)
    assert not result["success"] and result["state_changed"] is False and calls == []


@pytest.mark.parametrize("extra", [{"role": "companion"}, {"schema_id": "other"},
                                  {"source_profile": {}}, {"unexpected": True}])
def test_file_cannot_override_inline_authority_or_parameters(tmp_path, extra):
    calls = []
    check = _participant_draft_check_tool("player_character", ())
    fields = {"summary": "Name", "authored_profile": "Profile", "participant": {},
              "entity_state": {}, **extra}
    if "source_profile" in extra:
        # A typed owner rejection is handled by the original tool, not trusted by the file wrapper.
        from sylvia_gm.domain.errors import CommandRejected

        def reject(source):
            calls.append(source)
            raise CommandRejected("Source changed", code="stale_character_profile")

        check = _participant_draft_check_tool("player_character", (), resolve_source_profile=reject)
    result = participant_file_tool(check, tmp_path, _participant_entity_state).handler(
        write_draft(tmp_path, fields), None,
    )
    assert not result["success"] and result.get("state_changed", False) is False
    assert len(calls) == (1 if "source_profile" in extra else 0)
