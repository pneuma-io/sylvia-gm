"""Issue 279: the release fight's locked casting window, and how a saved campaign leaves it.

On SRD Lite 0.3.28 a combat Dissonant Whispers begun without target_distance committed, and its
completion then needed that distance, so no completion, new cast, turn end or combat end was
possible. These tests rebuild that lock on the published 0.3.28 archive, update the campaign's
rules to the bundled Ruleset, and show that the saved window then completes, or can be
released, and that the turn and the fight can end.
"""

import io
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

import tests.test_srd_lite_foundation as foundation
from sylvia_gm import dice
from sylvia_gm.application.bundled_packages import bundled_srd_archive, bundled_srd_version
from sylvia_gm.domain.authority import ActionStatus, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected
from tests.lite_fixture_archive import archive_closed_case
from tests.test_campaign_rules_update import adopt
from tests.test_srd_lite_foundation import VARIANT_ID, Case

LOCKED_ARCHIVE = Path(__file__).parent / "fixtures/lite-0.3.28.sylvia-gm-package"
POSITIONS = {"hero": [0, 0, 0], "guard": [10, 0, 0], "other": [-30, 0, 0], "extra": [40, 10, 0]}
LOCK_GUARD = "Complete the pending reaction window before advancing its turn or time."
# A saved campaign carries its advancement record; a rules update checks it like any field.
ADVANCEMENT = {"advancement": {
    "method": "fixed", "starting_level": 1, "maximum_level": 1, "recipients": "whole_party",
    "timing": "No advancement awards.", "allocation": "No advancement awards.",
}}


def _release_fight_begin(case):
    """The release fight's exact facts: the target named twice and no distance."""
    keep = {"spell", "caster", "caster_state", "slot_resource", "verbal_component_available",
            "caster_can_see_target", "target", "material_item_ids"}
    return {key: value for key, value in case.parameters.items() if key in keep} | {
        "targets": ["guard"], "caster_can_see_all_targets": True,
    }


def _refused(case, tool, parameters, match):
    before = case.snapshot()
    with pytest.raises((MechanicsRejected, CommandRejected), match=match):
        case.execute(case.command(tool, parameters))
    after = case.snapshot()
    assert after.revision == before.revision and after.state == before.state


def _stack(case):
    return list(case.lasting().get("pending_srd_action_stack") or [])


def _slot(case):
    return case.entities()["hero"]["resources"]["spell_slot_1"]


@pytest.fixture
def make_case(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(279))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    opened = []

    def make(archive=None):
        if archive is not None:
            monkeypatch.setattr(foundation, "ARCHIVE", archive)
        case = Case(tmp_path / f"case-{len(opened)}.db", "dissonant_whispers",
                    casting_source="bard", positions=POSITIONS, state_changes=ADVANCEMENT)
        opened.append(case)
        case.start()
        return case

    yield make
    for case in opened:
        archive_closed_case(case)


def _locked_on_0328(make_case):
    case = make_case(LOCKED_ARCHIVE)
    assert _version(case) == "0.3.28"
    case.act("begin_srd_spell_action", **_release_fight_begin(case))
    (pending,) = _stack(case)
    event = thaw_json_value(case.lasting()["pending_srd_actions"][pending])
    assert event["kind"] == "spell_cast" and event["parameters"].get("target_distance") is None
    # The 0.3.28 lock: nothing the GM can call closes the window.
    _refused(case, "complete_srd_pending_action", {"pending_action_id": pending},
             "Missing required parameter 'target_distance'")
    _refused(case, "begin_srd_spell_action", _release_fight_begin(case) | {"target_distance": 10},
             "Resolve the pending action before another ordinary spell")
    _refused(case, "end_combat_turn", {}, LOCK_GUARD)
    _refused(case, "end_combat_encounter", {"end_reason": "fighting_stopped"}, LOCK_GUARD)
    assert _slot(case) == 3
    return case, pending


def _update_rules(case):
    """Update campaign rules as the app does: at a completed turn, then play on."""
    owner = case.owner
    owner.complete_turn("lite", "turn")
    owner.install_package_archive(io.BytesIO(bundled_srd_archive()), install_id="bundled-current")
    options = owner.campaign_rules_update_options("lite")
    assert options["candidates"], options
    candidate = options["candidates"][0]
    preview = owner.preview_campaign_rules_update("lite", candidate["candidate_id"])
    assert preview["compatible"], preview["conflicts"]
    adopt(owner, preview, "adopt-279")
    assert _version(case) == bundled_srd_version() != "0.3.28"
    owner.open_turn("lite", "after-update")
    command = case.command
    case.command = lambda *args, **kwargs: replace(command(*args, **kwargs), turn_id="after-update")


def _version(case):
    lock = case.owner.current_package_lock("lite").package_lock
    return next(e.version for e in lock.entries if e.package_id == VARIANT_ID)


def _turn_and_fight_end(case):
    case.act("end_combat_turn")
    case.act("end_combat_encounter", end_reason="fighting_stopped")
    combat = case.snapshot().state[VARIANT_ID + ".combat"]
    assert combat["encounter_active"] is False


def test_locked_0328_window_completes_after_rules_update(make_case):
    case, pending = _locked_on_0328(make_case)
    _update_rules(case)
    receipt, _ = case.act("complete_srd_pending_action", pending_action_id=pending)
    assert receipt.status is ActionStatus.COMMITTED
    # The updated Ruleset measures the saved 10 feet and resolves the save; the slot is spent.
    assert not _stack(case) and _slot(case) == 2
    _turn_and_fight_end(case)


def test_locked_0328_window_can_be_released_after_rules_update(make_case):
    case, pending = _locked_on_0328(make_case)
    _update_rules(case)
    receipt, _ = case.act("release_srd_unresolved_spell_cast", pending_action_id=pending)
    assert receipt.status is ActionStatus.COMMITTED
    assert "casting released" in str(thaw_json_value(receipt.result))
    # A released cast keeps its Action used and spends no slot.
    assert not _stack(case) and _slot(case) == 3
    assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is False
    _turn_and_fight_end(case)


def test_release_fight_cast_measures_the_map_and_resolves(make_case):
    case = make_case()
    receipt, _ = case.act("begin_srd_spell_action", **_release_fight_begin(case))
    (pending,) = _stack(case)
    case.act("complete_srd_pending_action", pending_action_id=pending)
    assert not _stack(case) and _slot(case) == 2
    case.act("end_combat_turn")


def test_release_fight_cast_beyond_range_refuses_before_anything_is_spent(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(dice, "_rng", Random(279))
    case = Case(tmp_path / "far.db", "dissonant_whispers", casting_source="bard",
                positions=POSITIONS | {"guard": [200, 0, 0]})
    try:
        case.start()
        _refused(case, "begin_srd_spell_action", _release_fight_begin(case),
                 "beyond range or lacks a clear path")
        assert not _stack(case) and _slot(case) == 3
        assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is True
    finally:
        archive_closed_case(case)


def test_release_refuses_anything_but_an_open_casting_window(make_case):
    case = make_case()
    _refused(case, "release_srd_unresolved_spell_cast", {"pending_action_id": "action-1"},
             "This pending action does not exist")
