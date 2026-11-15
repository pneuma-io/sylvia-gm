"""Printed native Wight gear uses actual shared NPC don/doff time and AC projection."""

import hashlib
import json

import pytest
from scripts.srd_full.srd_native_wight_equipment import extend_runtime
from scripts.srd_full.srd_parameter_declarations import compact_runtime, restore_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_native_wight_rise import DEPENDENCIES, _controller, rise_owner
from tests.srd_full.test_srd_spell_summoning import SRD


@pytest.fixture(scope="module")
def gear_runtime():
    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    raw = (DEPENDENCIES / "wight-public-parent-runtime.yaml").read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest()
        == "00a7de081d9880701c9bd84f339b10b53ff7eb99a945fc5e39f19b5e325099d5"
    )
    text = extend_runtime(restore_runtime(raw.decode()))
    assert extend_runtime(text) == text
    expanded = _load_runtime(text)
    text = compact_runtime(text)
    assert _load_runtime(restore_runtime(text)) == expanded
    assert not validate_ruleset(text)
    validate_content_shape("package_runtime", _load_runtime(text), file="native-wight-equipment")
    return expanded, text.encode()


@pytest.fixture
def gear_owner(monkeypatch, gear_runtime):
    return rise_owner.__wrapped__(monkeypatch, gear_runtime)


def test_printed_wight_real_npc_armor_doff_reopen_don_and_time(gear_owner):
    state, data = _controller()
    entities = f"{SRD}.entities"
    actor = state[entities]["native_wight"]
    assert actor["armor_class"] == 14 and actor["speed"] == 30
    assert actor["equipment_profile"] == {
        "strength": 15,
        "dexterity": 14,
        "unarmored_ac": 12,
        "speed": 30,
        "climb_speed": 0,
        "swim_speed": 0,
        "armor_training": ["light"],
    }
    assert actor["armor_untrained"] is False
    provenance = actor["created_source_armor"]
    tick = state[f"{SRD}.state"]["lasting_tick"]
    _run("change_srd_equipment", state, actor_state="native_wight", slot="armor", item=[])
    actor = state[entities]["native_wight"]
    assert actor["worn_armor"] is None and actor["armor_class"] == 12
    assert actor["speed"] == 30 and actor["armor_untrained"] is False
    assert actor["created_source_armor"] == provenance
    assert "Studded Leather Armor" in actor["inventory"]
    assert state[f"{SRD}.state"]["lasting_tick"] == tick + 20

    state = json.loads(json.dumps(state))
    _run(
        "change_srd_equipment",
        state,
        actor_state="native_wight",
        slot="armor",
        item=[
            {
                "inventory_item": "Studded Leather Armor",
                "armor_type": "studded_leather_armor",
                "magic_bonus": 0,
            }
        ],
    )
    actor = state[entities]["native_wight"]
    assert actor["worn_armor"]["inventory_item"] == "Studded Leather Armor"
    assert actor["armor_class"] == 14 and actor["speed"] == 30
    assert actor["armor_untrained"] is False and actor["armor_stealth_disadvantage"] is False
    assert actor["created_source_armor"] == provenance
    assert state[f"{SRD}.state"]["lasting_tick"] == tick + 40
