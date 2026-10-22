"""Canonical package bytes remain exact, finite and detached from caller mutations."""

import json
import random

import pytest

from sylvia_gm.domain import package_library
from sylvia_gm.domain.authority import owned_json_value, thaw_json_value


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


def test_canonical_encoding_matches_previous_capture_for_nested_values():
    randomizer = random.Random(173)
    atoms = [None, False, True, 0, -2, 10**40, -0.0, 1.25, "\n", "é", "𝄞"]

    def tree(depth):
        if not depth or randomizer.randrange(3) == 0:
            return randomizer.choice(atoms)
        children = [tree(depth - 1) for _ in range(randomizer.randrange(5))]
        return (
            children
            if randomizer.randrange(2)
            else dict(zip(map(str, range(len(children))), children, strict=True))
        )

    for value in [tree(4) for _ in range(100)]:
        for source in (value, owned_json_value(value)):
            previous = json.dumps(
                thaw_json_value(owned_json_value(thaw_json_value(source))),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            assert package_library.canonical_json_bytes(source) == previous


def test_canonical_encoding_preserves_sequence_and_key_normalization():
    value = {2: ("second", {False: -0.0}), "2": ["last key wins"], "a": (1, 2)}
    assert package_library.canonical_json_bytes(value) == b'{"2":["last key wins"],"a":[1,2]}'
