"""Reuse special-route checks on the final compiled Lite tools without rebinding them."""
import os
import zipfile

import pytest

from scripts.srd_timed_actor_defenses import _load_runtime
from sylvia_gm.runtime import RulesetRuntime
from tests.srd_lite_mass_cure_geometry_fixtures import _calls
from tests import srd_lite_steed_mount_fixtures as mount
from tests import srd_lite_steed_fey_step_fixtures as fey
from tests import srd_lite_steed_turns_fixtures as turns
from tests.srd_lite_steed_mount_fixtures import _position


@pytest.fixture(scope='module')
def compiled():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        data = _load_runtime(archive.read('packages/sylvia.srd-lite.bg3/runtime/ruleset.yaml').decode())
    tools, pending = {}, [mount.MOUNT, mount.DISMOUNT, fey.ACTION, turns.BEGIN, turns.PAUSE,
                          'begin_combat_turn', 'move_srd_scene_creature']
    while pending:
        name = pending.pop()
        if name not in tools:
            tools[name] = data['tools'][name]
            pending.extend(_calls(tools[name]['effects']))
    parsed = RulesetRuntime(ruleset_dict={**{k: v for k, v in data.items() if k not in {'tools', 'procedures'}},
                                       'tools': tools})
    return parsed, data, []


def test_paid_mount_uses_actual_initialized_turn_and_preserves_rider_budget(compiled):
    turns.test_actual_mid_turn_mount_preserves_paid_rider_movement_and_initializes_steed_once(compiled)


def test_paid_controlled_steed_carries_rider_without_spending_rider_movement(compiled):
    bridge, act = turns._turn_case(compiled, mounted=False)
    # This isolated fixture declares the third creature before any tested action.
    bridge.write_field('target', 'size_rank', 2)
    turns._success(act('begin_combat_turn', actor='caster'))
    turns._success(act(mount.MOUNT, rider_state='caster', steed_state='steed'))
    turns._phase(act)
    turns._success(act('move_srd_scene_creature', actor_state='steed', destination=[0, 10, 0]))
    assert _position(bridge, 'steed') == [0, 10, 0]
    assert _position(bridge, 'caster') == [0, 10, 6]
    assert bridge.combat_state['movement_spent'] == 10
    turns._phase(act, turns.PAUSE)
    assert bridge.combat_state['movement_spent'] == 15
    assert bridge.combat_state['movement_remaining'] == 15


def test_fey_teleport_keeps_its_wall_crossing_exception(compiled):
    bridge, _, act = fey._fey_case(compiled, combat=True, geometry=[fey._wall()])
    result = fey._step(act, [30, 0, 0])
    assert result.success, result.error
    assert fey._frame(bridge, 'steed')['position'] == [30, 0, 0]


def test_saved_rider_teleport_keeps_both_bodies_and_one_bonus_action(compiled):
    fey.test_native_saved_rider_moves_with_saddle_offset_and_receipt(compiled)
