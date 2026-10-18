"""Canonical package bytes remain exact, finite and detached from caller mutations."""

import pytest

from sylvia_gm.domain import package_library
from sylvia_gm.domain.authority import owned_json_value


@pytest.mark.parametrize("frozen", [False, True])
def test_canonical_encoding_preserves_exact_nested_bytes(frozen):
    value = {"z": [None, True, False, -2, 1.25, {"b": "é", "a": "\n"}], "a": {}}
    if frozen:
        value = owned_json_value(value)
    assert package_library.canonical_json_bytes(value) == (
        '{"a":{},"z":[null,true,false,-2,1.25,{"a":"\\n","b":"é"}]}'.encode()
    )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), b"bytes"])
def test_canonical_encoding_rejects_nested_non_json_values(invalid):
    with pytest.raises(TypeError, match="JSON-compatible"):
        package_library.canonical_json_bytes({"items": [invalid]})


def test_canonical_encoding_captures_containers_before_serialization(monkeypatch):
    value = {"items": [{"value": 1}]}
    original = package_library.json.dumps

    def mutate_then_serialize(captured, **options):
        value["items"][0]["value"] = 2
        value["items"].append({"value": 3})
        return original(captured, **options)

    monkeypatch.setattr(package_library.json, "dumps", mutate_then_serialize)
    assert package_library.canonical_json_bytes(value) == b'{"items":[{"value":1}]}'
