"""Trigger anchor removal retires saved sources without rejecting legitimate scene mutations."""

import json
from functools import lru_cache

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_scene_relations import _tool_scene
from scripts.srd_full.srd_spell_triggers import RECORD_EXPORTS
from scripts.srd_full.srd_timed_actor_defenses import _call, _parameter, _require, _set, _when
from scripts.srd_full.srd_timed_senses import _loop

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_spell_triggers as owner
from tests.srd_full.test_srd_action_budget import SRD, _run


@pytest.fixture(autouse=True)
def _actual_repaired_owner(monkeypatch):
    owner._runtime.cache_clear()
    owner._mechanic.cache_clear()
    runtime = yaml.load(
        (owner.ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes(),
        Loader=yaml.CSafeLoader,
    )
    assert "apply_srd_trigger_spell" in runtime["tools"]
    assert set(owner.PROFILES) <= runtime["definitions"]["spells"].keys()
    probe_name = "probe_srd_saved_trigger"
    runtime["tools"][probe_name] = _tool_scene(
        "Pass the actual saved source row into the canonical private trigger helper.",
        [_parameter("trigger_id", "integer")],
        [
            _set("_probe_found", False),
            _set("_probe_rows", "state.turn_effects"),
            _loop(
                "_probe_rows",
                "_probe_row",
                [
                    _when(
                        "_probe_row.kind == 'spell_trigger' and _probe_row.instance == trigger_id",
                        [
                            _call(
                                "check_srd_spell_trigger_record",
                                {"record": "{_probe_row}"},
                                RECORD_EXPORTS,
                            ),
                            _set("_probe_found", True),
                        ],
                    )
                ],
            ),
            _require("_probe_found == true", "The probe needs the actual saved trigger."),
        ],
    )
    encoded = yaml.safe_dump(runtime, sort_keys=False).encode()

    @lru_cache
    def mechanic(name):
        if name != probe_name:
            return owner._mechanic(name)
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="a" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(test_srd_action_budget, "_mechanic", mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards, "_spell_definitions", lambda: runtime["definitions"]["spells"]
    )
    yield
    owner._runtime.cache_clear()
    owner._mechanic.cache_clear()


@pytest.mark.parametrize("spell", ["magic_mouth", "glyph_of_warding"])
def test_unheld_saved_source_with_absent_holder_metadata_queries_without_literal_paths(spell):
    state, _, _, identity = owner._cast(spell)
    metadata = owner._entry(state, "orb")["metadata"]
    del metadata["carried_by"]
    del metadata["worn_by"]
    state = json.loads(json.dumps(state))
    checked = _run("probe_srd_saved_trigger", state, trigger_id=identity).result["context"]
    assert checked["_trigger_active"] is True
    assert checked["_trigger_origin"] == [5, 0, 0]
    assert checked["_trigger_plane"] == "material"
    _run("refresh_srd_spell_triggers", state)
    assert owner._source(state)["instance"] == identity


@pytest.mark.parametrize("removal", ["remove", "inactive"])
def test_missing_actual_anchor_retires_instead_of_rejecting_scene_removal(removal):
    state, _, _, _ = owner._cast("glyph_of_warding")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if removal == "remove":
        scene["entities"] = [e for e in scene["entities"] if e["id"] != "orb"]
    else:
        owner._entry(state, "orb")["active"] = False
    _run("refresh_srd_spell_triggers", state)
    assert not state[f"{SRD}.state"]["has_spell_triggers"]
    assert not any(e.get("kind") == "spell_trigger" for e in state[f"{SRD}.state"]["turn_effects"])


@pytest.mark.parametrize("field", ["carried_by", "worn_by"])
def test_missing_saved_holder_retires_its_source_without_rejecting_the_removal(field):
    state, _, _, identity = owner._cast("glyph_of_warding")
    orb = owner._entry(state, "orb")
    orb["metadata"].pop("carried_by", None)
    orb["metadata"].pop("worn_by", None)
    orb["metadata"][field] = "guard"
    state[f"{SRD}.state"]["spatial_scene"]["entities"] = [
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] != "guard"
    ]
    checked = _run("probe_srd_saved_trigger", state, trigger_id=identity).result["context"]
    assert not checked["_trigger_active"]
    assert checked["_trigger_retirement"] == "holder no longer exists in the admitted scene"
    _run("refresh_srd_spell_triggers", state)
    assert not state[f"{SRD}.state"]["has_spell_triggers"]
