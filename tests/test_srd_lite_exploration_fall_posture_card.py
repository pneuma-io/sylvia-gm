"""Read native posture without replaying the paid fall; retain its genuine public receipt."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_lite_exploration_fall import RESOLVE_EVENT, SETTLE_TARGET
from tests.test_srd_lite_exploration_steed_loss import _lists
from tests.test_srd_lite_exploration_steed_loss import bound as _bound
from tests.test_srd_lite_exploration_steed_loss import native_owner_source as _native_owner_source

from sylvia_gm import dice
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._types import ExecutionContext

bound = _bound
native_owner_source = _native_owner_source


@pytest.fixture(scope="module")
def feedback_owners(bound):
    """Select only the added read and row from their actual bound owning definitions."""
    tools = bound[0]["tools"]
    settle = tools[SETTLE_TARGET]["effects"][0]["on_true"]
    reads = [
        effect
        for effects in _lists(settle)
        for effect in effects
        if effect.get("set", {}).get("field") == "_scene_fall_prone_after"
    ]
    assert len(reads) == 1
    event_rows = next(
        effect["for_each"]["do"]
        for effect in tools[RESOLVE_EVENT]["effects"]
        if effect.get("for_each", {}).get("collection") == "_resolve_fall_rows"
    )
    assert event_rows[0]["call"] == SETTLE_TARGET
    assert event_rows[0]["export_context"]["_scene_fall_prone_after"] == "_scene_fall_prone_after"
    posture_rows = [
        effect
        for effects in _lists(event_rows)
        for effect in effects
        if effect.get("condition")
        == "_scene_fall_status == 'landed' and _scene_fall_prone_after == true"
    ]
    assert len(posture_rows) == 1
    declaration = tools["complete_srd_pending_action"]["metadata"]["result_card"]
    return reads[0], posture_rows[0], declaration


@pytest.fixture(scope="module")
def genuine_receipt():
    """Reuse only the selected synthetic paid capture; never alter its original bytes."""
    path = Path(os.environ["SRD_LITE_STEED_FALL_RECEIPT"])
    content = path.read_bytes()
    assert len(content) < 65536
    receipt = json.loads(content)
    assert receipt["title"] == "Falling creatures"
    (equation,) = [row["equation"] for row in receipt["details"] if "equation" in row]
    assert equation["die_sides"] == 6 and len(equation["rolls"]) == 8
    assert equation["total"] == equation["roll"] == sum(equation["rolls"])
    return path, content, receipt


@pytest.mark.parametrize(
    ("conditions", "status", "expected_prone"),
    [
        (["prone"], "landed", True),
        ([], "landed", False),
        (None, "landed", False),
        (["prone"], "descending", False),
    ],
)
def test_native_landing_posture_projects_without_recasting_or_rewriting_fiction(
    feedback_owners, genuine_receipt, conditions, status, expected_prone, tmp_path
):
    read, posture_row, declaration = feedback_owners
    path, original_bytes, receipt = genuine_receipt
    actor_name = receipt["summary"][0]["label"]
    # Posture and status are explicit synthetic inputs; dice/damage/location rows are genuine.
    target = {"conditions": deepcopy(conditions)}
    frame = ExecutionContext(
        params={"target_state": target},
        vars={
            "_scene_fall_actor_name": actor_name,
            "_scene_fall_status": status,
            "_scene_fall_summary_rows": deepcopy(receipt["summary"]),
        },
    )
    before_target, before_receipt = deepcopy(target), deepcopy(receipt)
    rng = dice._rng.getstate()
    execute_effects([read, posture_row], frame)
    assert target == before_target and dice._rng.getstate() == rng
    assert receipt == before_receipt and path.read_bytes() == original_bytes
    frame.vars.update(
        _pending_card_title=receipt["title"],
        _pending_card_summary=frame.vars["_scene_fall_summary_rows"],
        _pending_card_details=deepcopy(receipt["details"]),
        _spell_featured_equations=deepcopy(receipt["equations"]),
    )
    card = project_result_card(declaration, parameters={}, public_context=frame.vars)
    assert card is not None
    assert card["summary"][: len(receipt["summary"])] == receipt["summary"]
    assert card["details"] == receipt["details"]
    assert card["equations"] == receipt["equations"]
    added = card["summary"][len(receipt["summary"]) :]
    if expected_prone:
        assert added == [{"label": actor_name, "value": "Prone", "emphasis": False}]
        directory = Path(os.environ.get("SRD_LITE_STEED_POSTURE_CAPTURE_DIR", str(tmp_path)))
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "feedback-only-prone-reprojected-card.json"
        destination.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
        print(f"Feedback-only synthetic posture; genuine saved dice/receipt: {destination}")
    else:
        assert added == [], "No absent Prone or unsupported standing posture may be invented."
