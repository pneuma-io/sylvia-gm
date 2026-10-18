"""Retained radial wards reuse actual Cover after the shared collector gains Cone qualification."""

from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts import srd_spell_triggers as repair
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm.application import bundled_packages
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_spell_triggers as owner
from tests.srd_full.test_srd_action_budget import SRD

ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def _runtime():
    payload = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
    assert "collect_srd_trigger_area_cover_targets" in runtime["tools"]
    assert set(repair.PROFILES) <= runtime["definitions"]["spells"].keys()
    return runtime, payload


@lru_cache
def _mechanic(name):
    runtime, payload = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=payload,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _actual_owner(monkeypatch):
    monkeypatch.setattr(owner, "PROFILES", repair.PROFILES)
    monkeypatch.setattr(owner, "CHOICES", repair.CHOICES)
    monkeypatch.setattr(owner, "_schema", repair._schema)
    monkeypatch.setattr(owner, "_runtime", _runtime)
    monkeypatch.setattr(owner, "_mechanic", _mechanic)
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards,
        "_spell_definitions",
        lambda: _runtime()[0]["definitions"]["spells"],
    )
    monkeypatch.setattr(
        bundled_packages, "_bundled_srd_root", lambda: ROOT / "rulesets/dnd-srd-5.2.1"
    )
    owner._test_archive.cache_clear()
    yield
    owner._test_archive.cache_clear()


@pytest.mark.parametrize("slot,damage_type", [(3, "fire"), (5, "cold")])
def test_original_glyph_damage_variations_keep_actual_dc_roll_cover_and_upcast(slot, damage_type):
    owner.test_explosive_glyph_actual_saved_dc_shared_roll_upcast_half_and_single_release(
        slot, damage_type
    )


def test_original_public_paid_glyph_archive_release_reopen_retry(tmp_path, monkeypatch):
    owner.test_actual_public_paid_glyph_release_saved_cards_reopen_and_retry(tmp_path, monkeypatch)
