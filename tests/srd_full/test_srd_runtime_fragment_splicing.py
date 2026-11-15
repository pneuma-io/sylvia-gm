"""Prove bounded package fragment scans and preserved source presentation."""

import re
from copy import deepcopy

import pytest
from scripts import srd_actor_spell_modifiers as actor
from scripts import srd_timed_senses as senses


def _tool(name):
    return {
        "description": "Actual fragment " + name,
        "parameters": [
            {
                "name": "spell",
                "type": "string",
                "enum_values": ["acid_splash", "fire_bolt"],
                "required": True,
            },
            {"name": "caster_state", "type": "string", "resolve": "entity", "required": True},
        ],
        "effects": [{"set": {"field": "state.fragment_count", "value": 1}}],
        "metadata": {"result_card": {"title": "Owned fragment", "summary": []}},
    }


def _document():
    untouched = "".join(
        f"  untouched_{index}:\n    description: 'source byte {index}'\n    effects: []\n"
        for index in range(1500)
    )
    changed = {"owned_" + str(index): _tool(str(index)) for index in range(12)}
    owned = "".join(f"  {name}:\n    description: prior\n" for name in changed)
    return (
        "# exact prefix\ntools:\n" + untouched + owned + "procedures: {}\n# exact suffix\n",
        changed,
        untouched,
    )


@pytest.mark.parametrize("emit", [actor._emit, senses._replace_tools], ids=["actor", "senses"])
def test_multi_tool_replacement_scans_the_whole_document_once(emit, monkeypatch):
    text, changed, untouched = _document()
    scans = []
    for name in ("search", "sub", "finditer"):
        original = getattr(re, name)

        def observe(*args, _original=original, _name=name, **kwargs):
            if (
                len(args) > 1
                and isinstance(args[1], str)
                and len(args[1]) >= len(text) // 2
                or _name == "sub"
                and len(args) > 2
                and isinstance(args[2], str)
                and len(args[2]) >= len(text) // 2
            ):
                scans.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(re, name, observe)
    result = emit(text, deepcopy(changed))
    assert untouched in result
    assert result.startswith("# exact prefix\ntools:\n") and result.endswith(
        "procedures: {}\n# exact suffix\n"
    )
    assert len(scans) <= 1, scans
    assert all(result.count("  " + name + ":\n") == 1 for name in changed)


@pytest.mark.parametrize(
    "emit,broad", [(actor._emit, True), (senses._replace_tools, False)], ids=["actor", "senses"]
)
def test_header_policy_anchors_selector_and_new_tool_order(emit, broad):
    text = (
        "# retained prefix\ntools:\n"
        "  odd_suffix: # source-owned suffix\n    description: original suffix\n"
        "  d20_test: &prior_d20\n    metadata:\n"
        "      result_card: &d20_result_card\n        title: Original\n"
        "  cast_spell: &srd_cast_spell\n    description: original caster\n"
        "  untouched: &untouched_anchor\n    metadata:\n      result_card: *d20_result_card\n"
        "procedures: {}\ndefinition_row_schemas: {}\n# retained suffix\n"
    )
    changed = {
        name: _tool(name)
        for name in ("odd_suffix", "d20_test", "cast_spell", "new_first", "new_second")
    }
    result = emit(text, deepcopy(changed))
    assert result.count("  odd_suffix:") == (1 if broad else 2)
    assert ("description: original suffix" in result) is (not broad)
    assert "      result_card: &d20_result_card\n" in result
    assert "  cast_spell: &srd_cast_spell\n" in result
    assert (
        "# BEGIN GENERATED SPELL SELECTOR" in result and "# END GENERATED SPELL SELECTOR" in result
    )
    assert (
        "  untouched: &untouched_anchor\n    metadata:\n      result_card: *d20_result_card\n"
        in result
    )
    assert (
        result.index("  new_first:")
        < result.index("  new_second:")
        < result.index("procedures: {}")
    )
    assert result.endswith("procedures: {}\ndefinition_row_schemas: {}\n# retained suffix\n")


def test_senses_d20_anchor_observes_earlier_alias_removal_and_addition():
    text = (
        "tools:\n  alias_owner:\n    metadata:\n      result_card: *d20_result_card\n"
        "  d20_test:\n    description: Original\nprocedures: {}\n"
    )
    removed = senses._replace_tools(text, {"alias_owner": _tool("clean"), "d20_test": _tool("d20")})
    assert "&d20_result_card" not in removed
    holder = _tool("alias")
    holder["description"] = "*d20_result_card"
    added = senses._replace_tools(
        text.replace("*d20_result_card", "ordinary"),
        {"alias_owner": holder, "d20_test": _tool("d20")},
    )
    assert "      result_card: &d20_result_card\n" in added


@pytest.mark.parametrize("emit", [actor._emit, senses._replace_tools], ids=["actor", "senses"])
def test_absent_procedure_marker_and_last_unbounded_tool_stay_unchanged(emit):
    text = (
        "# owned source without the insertion boundary\ntools:\n"
        "  owned:\n    description: retained final tool\n"
    )
    assert emit(text, {"owned": _tool("owned"), "new": _tool("new")}) == text


@pytest.mark.parametrize("emit", [actor._emit, senses._replace_tools], ids=["actor", "senses"])
def test_no_changes_does_not_scan_the_document(emit, monkeypatch):
    def unexpected_scan(*args, **kwargs):
        pytest.fail("an empty replacement call must retain the original no-op")

    monkeypatch.setattr(re, "finditer", unexpected_scan)
    text, _, _ = _document()
    assert emit(text, {}) is text
