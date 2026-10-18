"""Malformed copied package records identify a bounded repair location."""

import json

import pytest

from sylvia_gm.builder._package_record_edits import _read_record
from sylvia_gm.domain.errors import CommandRejected


@pytest.mark.parametrize(
    "source",
    [
        '{\n  "actions": ["PRIVATE_VALUE",\n  ]\n}',
        '{\n  "name": "PRIVATE_VALUE\n}',
    ],
)
def test_package_record_syntax_error_identifies_file_and_location(tmp_path, source):
    """A failed read preserves the draft and discloses location, never its values."""
    relative = "campaign-plan-draft/package-records/example.rules.json"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    with pytest.raises(json.JSONDecodeError) as parsed:
        json.loads(source)

    with pytest.raises(CommandRejected) as rejected:
        _read_record(tmp_path, path)

    assert rejected.value.code == "package_record_json_invalid"
    diagnostic = rejected.value.diagnostics[0].as_payload()
    assert diagnostic["file"] == relative
    assert f"line {parsed.value.lineno}, column {parsed.value.colno}" in diagnostic["message"]
    assert diagnostic["guidance"]
    assert "PRIVATE_VALUE" not in json.dumps(diagnostic)
    assert str(tmp_path) not in json.dumps(diagnostic)
    assert path.read_text(encoding="utf-8") == source
