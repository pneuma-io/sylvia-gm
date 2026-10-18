"""Verify the explicitly rebound Tavin fixture against the exact admitted Lite contract."""
import hashlib
import io
import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest

from sylvia_gm.application.character_rules import verify_character_rules
from sylvia_gm.application.initial_character_state import verify_initial_character_state
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.infrastructure.package_archive import admit_package_archive


@lru_cache
def _declaration():
    with admit_package_archive(io.BytesIO(Path(os.environ['SRD_LITE_CANDIDATE']).read_bytes())) as admitted:
        primary = next(m for m in admitted.manifests if m.role.value == 'primary')
        assert primary.package_id == 'sylvia.srd-lite.bg3'
        return next(d for d in primary.participant_artifacts if d.kind == 'player_character')


def _tavin():
    raw = (Path(__file__).parent / 'fixtures/lite-tavin-bell.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '1ab33014ca0b3ef428e9a9a714b9085b906009ff7c4a47e75197b3f35e0988a5'
    value = json.loads(raw)
    assert value['schema'] == {'id': _declaration().schema_id, 'content_sha256': _declaration().schema_sha256}
    return value


def _verify(tavin):
    declaration = _declaration()
    return verify_initial_character_state(
        tavin['participant'], tavin['entity_state'],
        entity_state_schema=declaration.entity_state_schema,
        initial_state_calculations=declaration.initial_state_calculations,
        initial_state_validation=declaration.initial_state_validation,
        setting_data=tavin['setting_data'],
    )


def test_unchanged_tavin_equipment_and_state_use_actual_lite_declaration():
    tavin = _tavin()
    rules = verify_character_rules(tavin['participant'], tavin['entity_state'],
        _declaration().initial_state_validation, tavin['setting_data'])
    assert rules['_equipment_ac'] == 17 and rules['_equipment_speed'] == 30
    assert rules['_equipment_speed_penalty'] == 0
    assert rules['_equipment_untrained_armor'] is False
    assert rules['_equipment_stealth_disadvantage'] is True
    assert rules['_equipment_climb_speed'] == rules['_equipment_swim_speed'] == 0
    assert rules['_focus_maximum'] == rules['_wholeness_maximum'] == 0
    assert canonical_json_bytes(_verify(tavin)) == canonical_json_bytes(tavin['entity_state'])


@pytest.mark.parametrize('change', ['saved_ac', 'defense_removed'])
def test_lite_still_rejects_inconsistent_tavin_equipment_state(change):
    tavin = deepcopy(_tavin())
    if change == 'saved_ac':
        tavin['entity_state']['armor_class'] = 16
    else:
        tavin['participant']['fighting_styles'] = []
    with pytest.raises(CommandRejected) as error:
        _verify(tavin)
    expected = ('character_initial_state_calculated_value_mismatch' if change == 'saved_ac'
                else 'character_initial_state_rule_rejected')
    assert error.value.code == expected
