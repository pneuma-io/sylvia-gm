"""Regeneration cannot silently discard independently authored runtime operations."""

import pytest
import yaml
from scripts.build_srd_5_2_1_advancement import replace_generated


def test_advancement_regeneration_preserves_authored_rest_and_rejects_wrong_placement():
    generated = {"award": {"description": "new award"}}
    old = "  # FC-09 tools begin\n  award: {description: old}\n"
    end = "  # FC-09 tools end\n"
    authored = "  begin_rest: {description: tracked rest}\n"
    source = "tools:\n" + old + end + authored + "definitions: {}\n"
    result = replace_generated(source, "tools", generated)
    assert yaml.safe_load(result)["tools"] == generated | {
        "begin_rest": {"description": "tracked rest"}
    }
    assert replace_generated(result, "tools", generated) == result
    misplaced = "tools:\n" + old + authored + end + "definitions: {}\n"
    with pytest.raises(ValueError, match="Refusing to remove authored tools.*begin_rest"):
        replace_generated(misplaced, "tools", generated)
