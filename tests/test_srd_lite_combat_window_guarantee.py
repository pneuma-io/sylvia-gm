"""Issue 279: a combat cast refuses at begin, or resolves, or can be released; it never locks.

Every implemented SRD Lite spell is begun in coordinate combat with the scene facts a GM is
likely to leave out, with a recipient beyond any range, and with a claimed distance beyond
range. Begin must either refuse with nothing saved, or every window it opens must complete.
A completion refused after begin committed fails the test, except for the listed choice-input
refusals (#291): each of those names its input and must close with
release_srd_unresolved_spell_cast. In every case the caster's turn and the fight must then end.
"""

from random import Random

import pytest

from scripts.srd_lite_catalogue import SELECTED
from sylvia_gm import dice
from sylvia_gm.domain.authority import ActionStatus, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import VARIANT_ID, Case

# Scene facts that saved combat positions can establish. A GM may leave any of them out.
SCENE_FACTS = frozenset({
    "target_distance", "targets_max_distance", "caster_can_see_target", "caster_can_hear_target",
    "caster_can_touch_target", "cover", "caster_can_see_all_targets",
    "all_targets_have_clear_path", "caster_can_touch_all_targets",
    "all_attack_targets_have_clear_path",
})
NEAR = {"hero": [0, 0, 0], "guard": [5, 0, 0], "other": [-5, 0, 0], "extra": [20, 10, 0]}
FAR = NEAR | {"guard": [900, 0, 0]}
SHAPES = {
    # The release fight: the GM named the recipient and left the measurable facts out.
    "facts_omitted": (NEAR, lambda p: _without_facts(p)),
    "facts_omitted_with_targets": (NEAR, lambda p: _without_facts(p) | {"targets": ["guard"]}),
    "recipient_out_of_range": (FAR, lambda p: _without_facts(p)),
    "claimed_distance_out_of_range": (
        NEAR, lambda p: p | {"target_distance": 990, "targets_max_distance": 990},
    ),
}
WINDOW_LIMIT = 8
# Choice inputs (not scene facts) still checked only when the cast resolves (#291). Each refusal
# names the input to correct; the GM reports it and releases the window.
RELEASABLE = {
    "chromatic_orb": "Check input 'selected_damage_type'.",
    "protection_from_energy": "Check input 'selected_damage_type'.",
    "etherealness": "Check input 'targets'.",
    "suggestion": "Check input 'activity_words'.",
    # The fixture's synthetic target has no Character File, equipment or Hit Point lifecycle.
    "mage_armor": "Check input 'target'.",
    "power_word_kill": "Check input 'target'.",
    # Placement whose own validator writes state, so it cannot run before the Action is spent.
    "wall_of_stone": "Check input 'solid_wall_origin'.",
    "wall_of_fire": "Check input 'environment_origin'.",
}


def _without_facts(parameters):
    return {key: value for key, value in parameters.items() if key not in SCENE_FACTS}


def _stack(case):
    return list(case.lasting().get("pending_srd_action_stack") or [])


def _attempt(case, tool, parameters):
    """Run one call; a refusal must leave the campaign exactly as it was."""
    before = case.snapshot()
    try:
        receipt = case.execute(case.command(tool, parameters))
    except (MechanicsRejected, CommandRejected) as error:
        after = case.snapshot()
        assert after.revision == before.revision and after.state == before.state, tool
        return None, error
    return receipt, None


@pytest.fixture
def combat_case(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(279))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    opened = []

    def make(spell, positions):
        case = Case(tmp_path / f"{spell}-{len(opened)}.db", spell, positions=positions)
        opened.append(case)
        # The sweep runs the Ruleset through the owner directly; the MCP surface adds only the
        # tool catalogue, which dominates the run time. The release fight's own MCP sequence
        # is in test_srd_lite_locked_window_recovery.
        case.execute = case.owner.execute_action
        case.start()
        return case

    yield make
    for case in opened:
        archive_closed_case(case)


def _release(case, spell, shape, pending_id, refusal):
    expected = RELEASABLE.get(spell)
    assert expected is not None and expected in str(refusal), (
        f"#279: {spell} ({shape}) committed at begin, then completion was refused: {refusal}"
    )
    # The refusal names its input, so the GM can tell the player what to correct.
    assert refusal.field_paths, (spell, shape, str(refusal))
    receipt, refused = _attempt(case, "release_srd_unresolved_spell_cast",
                                {"pending_action_id": pending_id})
    assert refused is None, (spell, shape, "release refused", str(refused))
    assert receipt.status is ActionStatus.COMMITTED
    assert "casting released" in str(thaw_json_value(receipt.result))


def _turn_and_fight_end(case, spell, shape):
    combat = case.snapshot().state[VARIANT_ID + ".combat"]
    if combat.get("turn_active") and combat.get("active_actor") == "hero":
        _, refused = _attempt(case, "end_combat_turn", {})
        assert refused is None, (spell, shape, "turn cannot end", str(refused))
    _, refused = _attempt(case, "end_combat_encounter", {"end_reason": "fighting_stopped"})
    assert refused is None, (spell, shape, "fight cannot end", str(refused))


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("spell", sorted(SELECTED))
def test_combat_cast_refuses_at_begin_or_resolves(combat_case, spell, shape):
    positions, shape_parameters = SHAPES[shape]
    case = combat_case(spell, positions)
    begin_parameters = shape_parameters(dict(case.parameters))
    receipt, refused = _attempt(case, "begin_srd_spell_action", begin_parameters)
    if refused is not None or receipt.status is not ActionStatus.COMMITTED:
        assert not _stack(case), (spell, shape, str(refused))
        _turn_and_fight_end(case, spell, shape)
        return
    for _ in range(WINDOW_LIMIT):
        stack = _stack(case)
        if not stack:
            break
        pending_id = stack[-1]
        event = thaw_json_value(case.lasting()["pending_srd_actions"][pending_id])
        receipt, refused = _attempt(
            case, "complete_srd_pending_action", {"pending_action_id": pending_id},
        )
        if refused is not None:
            assert event.get("kind") == "spell_cast", (spell, shape, event.get("kind"), refused)
            _release(case, spell, shape, pending_id, refused)
            continue
        assert receipt.status is ActionStatus.COMMITTED, (spell, shape, receipt.result)
    assert not _stack(case), (spell, shape, "windows still open", _stack(case))
    _turn_and_fight_end(case, spell, shape)
