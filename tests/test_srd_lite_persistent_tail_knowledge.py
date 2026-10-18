"""Exercise the actual retained-tail unpack without replaying a paid cast."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import srd_lite_persistent_spell_map as visual
from tests.test_srd_lite_pending_area_preview import _execute

W = Path("D:/Codex/2026-10-09/sylvia-main-01a11b3b/integration-01a120e9")
CAPTURE = (
    W
    / "parallel-native-persistent-map-draft-1167/successor-1255/native-run"
    / "persistent-guardian-held-light-1255-guardian-cast-1167-1005.json"
)
UNPACK = {
    "set": {
        "field": "_retained_cast_tail_choices",
        "value": "_retained_cast_tail_choices[0]",
    }
}


@pytest.mark.parametrize("knowledge", [True, False, None], ids=["known", "private", "omitted"])
def test_actual_tail_unpacks_before_reading_effect_knowledge(knowledge):
    selected = json.loads(
        (W / "selected-truth-dispatch-owners-1144.json").read_text(encoding="utf-8")
    )["tools"]
    original = deepcopy(selected["finish_srd_retained_cast_tail"])
    visual._forward_knowledge(selected, set())
    tail = selected["finish_srd_retained_cast_tail"]
    points = [i for i, row in enumerate(tail["effects"]) if row == UNPACK]
    assert len(points) == 1
    index = points[0]
    read = tail["effects"][index + 1]
    assert read == {
        "set": {
            "field": "_visual_cast_known",
            "value": "_retained_cast_tail_choices.known_spell_effect == true",
        }
    }
    # Removing only the new read reconstructs the exact native effect order.
    without_read = deepcopy(tail["effects"])
    del without_read[index + 1]
    assert without_read == original["effects"]

    capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
    context = capture["receipt"]["result"]["context"]
    assert capture["parameters"][visual.KNOWN] is True
    assert context["_retained_cast_tail_choices"][visual.KNOWN] is True
    assert context["_visual_cast_known"] is False
    choices = deepcopy(context["_retained_cast_tail_choices"])
    if knowledge is None:
        choices.pop(visual.KNOWN)
    else:
        choices[visual.KNOWN] = knowledge
    saved = deepcopy(capture["after"]["state"]["sylvia.srd-lite.bg3.state"])
    owner = deepcopy(tail)
    owner["effects"] = deepcopy(tail["effects"][: index + 2])
    result, state = _execute(
        {"actual_tail_unpack": owner},
        saved,
        name="actual_tail_unpack",
        parameters={
            "caster_state": "state",
            "_retained_cast_tail_context": deepcopy(context["_retained_cast_tail_context"]),
            "_retained_cast_tail_choices": [choices],
        },
    )
    assert result.success, result.error
    assert result.context["_visual_cast_known"] is (knowledge is True)
    assert result.context["_retained_cast_tail_choices"] == choices
    assert state == saved
